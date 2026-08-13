# Go (cobra)

Guidance for implementing The CLI Spec in Go using [cobra](https://github.com/spf13/cobra).

---

## Structured Output (Principle 1)

Default the flag to `auto` so TTY detection only applies when the user did not choose a format. An explicit `mytool -o text list` must produce text even when piped:

```go
import (
    "encoding/json"
    "os"
    "golang.org/x/term"
)

func resolveFormat(cmd *cobra.Command) string {
    format, _ := cmd.Flags().GetString("output")
    if format != "auto" {
        return format // explicit choice always wins
    }
    if term.IsTerminal(int(os.Stdout.Fd())) {
        return "text"
    }
    return "json"
}
```

Register the flag on the root command:

```go
rootCmd.PersistentFlags().StringP("output", "o", "auto", "Output format: auto, text, json")
```

Messages go to stderr:

```go
func printMessage(msg string) {
    fmt.Fprintln(os.Stderr, msg)
}

func printData(data interface{}, output string) {
    if output == "json" {
        enc := json.NewEncoder(os.Stdout)
        enc.SetIndent("", "  ")
        enc.Encode(data)
    } else {
        // human-readable table output
    }
}
```

### Errors

Centralize errors so Cobra never prints a prose-only failure. `SilenceErrors` and `SilenceUsage` prevent duplicate framework output; the adapter decides how to render the failure and what to exit with.

Keep the kind-to-exit-code mapping in one table and generate the schema's `errors` array from it, so the declared contract and the process status cannot drift apart:

```go
type ErrorSpec struct {
    Kind      string
    ExitCode  int
    Retryable bool
}

var errorSpecs = []ErrorSpec{
    {"usage", 2, false},
    {"auth", 3, false},
    {"not_found", 4, false},
    {"conflict", 5, false},
    {"confirmation_required", 6, false},
    {"configuration", 7, false},
    {"rate_limit", 8, true},
    {"internal", 1, false},
}

type CLIError struct {
    Kind    string
    Message string
    Hint    string
}

func (e *CLIError) Error() string { return e.Message }

func (e *CLIError) exitCode() int {
    for _, s := range errorSpecs {
        if s.Kind == e.Kind {
            return s.ExitCode
        }
    }
    return 1
}
```

Report in the format the caller selected. In text mode a human gets a human message; the exit code is what stays machine-readable in both modes:

```go
func emitError(err error, format string) int {
    var cliErr *CLIError
    if !errors.As(err, &cliErr) {
        cliErr = &CLIError{Kind: "internal", Message: "Unexpected internal error"}
    }

    if format == "text" {
        fmt.Fprintf(os.Stderr, "Error: %s\n", cliErr.Message)
        if cliErr.Hint != "" {
            fmt.Fprintln(os.Stderr, cliErr.Hint)
        }
        return cliErr.exitCode()
    }

    body := map[string]string{"kind": cliErr.Kind, "message": cliErr.Message}
    if cliErr.Hint != "" {
        body["hint"] = cliErr.Hint
    }
    _ = json.NewEncoder(os.Stderr).Encode(map[string]interface{}{"error": body})
    return cliErr.exitCode()
}

func execute() int {
    rootCmd.SilenceErrors = true
    rootCmd.SilenceUsage = true
    // Resolve the format before Execute: a flag-parsing failure has to be
    // reported in the format the caller asked for, and it is the first error
    // most consumers will hit.
    format := resolveFormatFromArgs(os.Args[1:])
    if err := rootCmd.Execute(); err != nil {
        return emitError(err, format)
    }
    return 0
}

func main() { os.Exit(execute()) }
```

Cobra's own flag errors arrive as plain `error` values. Wrap them as `usage` so an unknown flag exits 2 with a declared kind instead of falling through to `internal`.

---

## Declaring what each command is

cobra knows the tree and the flags. It does not know whether re-running a command is safe or whether a list can grow without limit. `effects`, `cardinality` and `output_kind` are claims about behavior, so keep them in a table beside the commands and look them up while walking:

```go
type declared struct {
    Effects     string
    Cardinality string // "" when the command is not a data command
    OutputKind  string // "" means the default, "data"
    MediaType   string
}

var declarations = map[string]declared{
    "services list":  {Effects: "read_only", Cardinality: "unbounded"},
    "services start": {Effects: "idempotent", Cardinality: "single"},
    "deploys create": {Effects: "non_idempotent", Cardinality: "single"},
    "logs tail":      {Effects: "read_only", OutputKind: "stream"},
    "completions":    {Effects: "read_only", OutputKind: "opaque", MediaType: "text/x-shellscript"},
}
```

Then fail the build rather than shipping a schema with a command nobody described:

```go
func TestEveryCommandIsDeclared(t *testing.T) {
    for _, path := range commandPaths(rootCmd, "") {
        if _, ok := declarations[path]; !ok {
            t.Errorf("command %q has no entry in declarations", path)
        }
    }
}
```

---

## Schema Introspection (Principle 2)

Walk cobra's command tree to generate the schema. v0.3 wants a **flat** list where `name` is the complete space-separated path, and where every entry is invocable: a group that only routes to children is not an entry.

Global flags registered on the root command go into the top-level `global_args` array, so agents discover `--output` and friends without them being repeated (or worse, omitted) per command:

```go
func generateSchema(root *cobra.Command) map[string]interface{} {
    var globalArgs []map[string]interface{}
    root.PersistentFlags().VisitAll(func(f *pflag.Flag) {
        if f.Name == "help" {
            return
        }
        globalArgs = append(globalArgs, map[string]interface{}{
            "name":    "--" + f.Name,
            "type":    f.Value.Type(),
            "default": f.DefValue,
        })
    })

    var errs []map[string]interface{}
    for _, s := range errorSpecs {
        errs = append(errs, map[string]interface{}{
            "kind": s.Kind, "exit_code": s.ExitCode, "retryable": s.Retryable,
        })
    }

    return map[string]interface{}{
        "clispec":     "0.3",
        "name":        root.Name(),
        "version":     version,
        "output":      map[string]string{"tty": "text", "piped": "json"},
        "global_args": globalArgs,
        "commands":    walkCommands(root, ""),
        "errors":      errs,
    }
}

func walkCommands(cmd *cobra.Command, prefix string) []map[string]interface{} {
    var commands []map[string]interface{}
    for _, c := range cmd.Commands() {
        if c.Hidden || c.Name() == "help" {
            continue
        }
        path := c.Name()
        if prefix != "" {
            path = prefix + " " + c.Name()
        }
        // Groups route to children and cannot be run, so they are not entries.
        // A command that has children and is also runnable gets both.
        if c.HasSubCommands() && !c.Runnable() {
            commands = append(commands, walkCommands(c, path)...)
            continue
        }

        d := declarations[path]
        entry := map[string]interface{}{
            "name":        path,
            "description": c.Short,
            "effects":     d.Effects,
        }
        if d.Cardinality != "" {
            entry["cardinality"] = d.Cardinality
        }
        if d.OutputKind != "" {
            entry["output_kind"] = d.OutputKind
        }
        if d.MediaType != "" {
            entry["media_type"] = d.MediaType
        }

        var args []map[string]interface{}
        c.NonInheritedFlags().VisitAll(func(f *pflag.Flag) {
            if f.Name == "help" {
                return
            }
            args = append(args, map[string]interface{}{
                "name":    "--" + f.Name,
                "type":    f.Value.Type(),
                "default": f.DefValue,
            })
        })
        if len(args) > 0 {
            entry["args"] = args
        }

        commands = append(commands, entry)
        if c.HasSubCommands() {
            commands = append(commands, walkCommands(c, path)...)
        }
    }
    return commands
}
```

`Short` is the description, and v0.3 requires it. A cobra command with an empty `Short` produces a document that does not validate, which is the right outcome: the description is the first thing an agent reads when choosing between commands.

Add the schema command:

```go
var schemaCmd = &cobra.Command{
    Use:   "schema",
    Short: "Output JSON schema for agent integration",
    Run: func(cmd *cobra.Command, args []string) {
        schema := generateSchema(rootCmd)
        enc := json.NewEncoder(os.Stdout)
        enc.SetIndent("", "  ")
        enc.Encode(schema)
    },
}
```

Validate the generated document in `go test` so a schema that stops conforming fails your own build. See [Verifying Compliance](verifying.md).

---

## Non-Interactive (Principle 4)

Secrets never travel via argv (see General Guidance in the spec): prompt on a TTY, read an environment variable otherwise. The prompt goes to stderr so it never lands in the data stream.

```go
import (
    "golang.org/x/term"
)

if term.IsTerminal(int(os.Stdin.Fd())) {
    fmt.Fprint(os.Stderr, "API token: ")
    token, _ = term.ReadPassword(int(os.Stdin.Fd()))
    fmt.Fprintln(os.Stderr)
} else {
    token = []byte(os.Getenv("MYTOOL_TOKEN"))
    if len(token) == 0 {
        return &CLIError{
            Kind:    "configuration",
            Message: "API token required in non-interactive mode",
            Hint:    "Set MYTOOL_TOKEN before invoking this command.",
        }
    }
}
```

A command that would ask for confirmation refuses without a TTY instead of proceeding, and names the bypass flag in the hint:

```go
if !term.IsTerminal(int(os.Stdin.Fd())) && !yes {
    return &CLIError{
        Kind:    "confirmation_required",
        Message: fmt.Sprintf("Deleting %s requires confirmation", name),
        Hint:    "Re-run with --yes to confirm.",
    }
}
```

Declare that flag as the command's `confirmation_bypass_arg`, and make sure `--yes` appears in its `args`.

---

## Shell Completions

cobra has built-in completion generation:

```go
var completionsCmd = &cobra.Command{
    Use:   "completions [bash|zsh|fish|powershell]",
    Short: "Generate shell completions",
    Args:  cobra.ExactArgs(1),
    Run: func(cmd *cobra.Command, args []string) {
        switch args[0] {
        case "bash":
            rootCmd.GenBashCompletion(os.Stdout)
        case "zsh":
            rootCmd.GenZshCompletion(os.Stdout)
        case "fish":
            rootCmd.GenFishCompletion(os.Stdout, true)
        case "powershell":
            rootCmd.GenPowerShellCompletionWithDesc(os.Stdout)
        }
    },
}
```

The output is a script, not a document, so declare the command `"output_kind": "opaque"` with `"media_type": "text/x-shellscript"`. That exempts its stdout from the structured-output and bounded-output rules and nothing else: `-o json` still cannot reformat a shell script, and a failure still writes the error envelope to stderr and exits with the code its kind declares.
