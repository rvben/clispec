# Go (cobra)

Guidance for implementing The CLI Spec in Go using [cobra](https://github.com/spf13/cobra).

---

## Structured Output (Principle 1)

```go
import (
    "encoding/json"
    "os"
    "golang.org/x/term"
)

func isJSON(cmd *cobra.Command) bool {
    outputFlag, _ := cmd.Flags().GetString("output")
    return outputFlag == "json" || !term.IsTerminal(int(os.Stdout.Fd()))
}
```

Register the flag on the root command:

```go
rootCmd.PersistentFlags().StringP("output", "o", "text", "Output format: json, text")
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

---

## Schema Introspection (Principle 2)

Walk cobra's command tree to auto-generate a schema:

```go
func generateSchema(root *cobra.Command) map[string]interface{} {
    return map[string]interface{}{
        "name":     root.Name(),
        "version":  version,
        "commands": walkCommands(root),
    }
}

func walkCommands(cmd *cobra.Command) []map[string]interface{} {
    var commands []map[string]interface{}
    for _, c := range cmd.Commands() {
        if c.Hidden {
            continue
        }
        entry := map[string]interface{}{
            "name":        c.Name(),
            "description": c.Short,
        }
        var args []map[string]interface{}
        c.Flags().VisitAll(func(f *pflag.Flag) {
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
        if subs := walkCommands(c); len(subs) > 0 {
            entry["subcommands"] = subs
        }
        commands = append(commands, entry)
    }
    return commands
}
```

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

---

## Non-Interactive (Principle 4)

```go
import "golang.org/x/term"

if term.IsTerminal(int(os.Stdin.Fd())) {
    fmt.Print("API token: ")
    token, _ = term.ReadPassword(int(os.Stdin.Fd()))
    fmt.Println()
} else {
    token = []byte(viper.GetString("token"))
    if len(token) == 0 {
        return fmt.Errorf("--token required in non-interactive mode")
    }
}
```

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
