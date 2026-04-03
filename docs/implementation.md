# Implementation Guide

Practical guidance for implementing The CLI Spec in your tool. Examples are provided for common CLI frameworks.

---

## Rust (clap)

### Structured Output (Principle 1)

Auto-detect TTY and switch between human-readable and JSON output:

```rust
use std::io::IsTerminal;

struct OutputConfig {
    json: bool,
}

impl OutputConfig {
    fn new(json_flag: bool) -> Self {
        Self {
            json: json_flag || !std::io::stdout().is_terminal(),
        }
    }
}
```

For structured errors with a `kind` field, define an error enum that maps to kinds:

```rust
use thiserror::Error;

#[derive(Error, Debug)]
pub enum AppError {
    #[error("{0}")]
    Config(String),
    #[error("{0}")]
    Auth(String),
    #[error("{0}")]
    NotFound(String),
    #[error("{0}")]
    Api(String),
}

impl AppError {
    fn kind(&self) -> &str {
        match self {
            Self::Config(_) => "config",
            Self::Auth(_) => "auth",
            Self::NotFound(_) => "not_found",
            Self::Api(_) => "api",
        }
    }

    fn print_json(&self) {
        eprintln!(
            r#"{{"error":{{"kind":"{}","message":"{}"}}}}"#,
            self.kind(),
            self
        );
    }
}
```

### Schema Introspection (Principle 2)

clap exposes the full command tree at runtime via `Command::get_subcommands()`. Walk it to generate a schema:

```rust
use clap::CommandFactory;
use serde_json::{json, Value};

fn generate_schema() -> Value {
    let cmd = Cli::command();
    json!({
        "name": cmd.get_name(),
        "version": env!("CARGO_PKG_VERSION"),
        "commands": walk_commands(&cmd),
        "errors": [
            {"kind": "config", "retryable": false, "description": "Configuration error"},
            {"kind": "auth", "retryable": false, "description": "Authentication failed"},
            {"kind": "not_found", "retryable": false, "description": "Resource not found"},
            {"kind": "api", "retryable": true, "description": "API error"},
        ]
    })
}

fn walk_commands(cmd: &clap::Command) -> Vec<Value> {
    cmd.get_subcommands()
        .filter(|c| !c.is_hide_set())
        .map(|c| {
            let mut entry = json!({
                "name": c.get_name(),
                "description": c.get_about().map(|s| s.to_string()).unwrap_or_default(),
            });
            let args: Vec<Value> = c.get_arguments()
                .filter(|a| !["help", "version"].contains(&a.get_id().as_str()))
                .map(|a| {
                    let mut arg = json!({
                        "name": format!("--{}", a.get_long().unwrap_or(a.get_id().as_str())),
                        "required": a.is_required_set(),
                    });
                    if let Some(vals) = a.get_possible_values().as_slice() {
                        if !vals.is_empty() {
                            arg["enum"] = json!(vals.iter()
                                .map(|v| v.get_name().to_string())
                                .collect::<Vec<_>>());
                        }
                    }
                    arg
                })
                .collect();
            if !args.is_empty() {
                entry["args"] = json!(args);
            }
            let subs = walk_commands(c);
            if !subs.is_empty() {
                entry["subcommands"] = json!(subs);
            }
            entry
        })
        .collect()
}
```

Add the `Schema` variant to your command enum:

```rust
#[derive(clap::Subcommand)]
enum Commands {
    /// Output JSON schema for agent integration
    Schema,
    // ... other commands
}
```

### Shell Completions

Add `clap_complete` to your dependencies and a `Completions` command:

```rust
use clap_complete::Shell;

#[derive(clap::Subcommand)]
enum Commands {
    /// Generate shell completions
    Completions { shell: Shell },
}

// Handler:
Commands::Completions { shell } => {
    clap_complete::generate(
        shell,
        &mut Cli::command(),
        env!("CARGO_PKG_NAME"),
        &mut std::io::stdout(),
    );
}
```

### Non-Interactive (Principle 4)

Use `dialoguer` for interactive prompts, gated on TTY detection:

```rust
use std::io::IsTerminal;

if std::io::stdin().is_terminal() {
    // Interactive: prompt the user
    let password = dialoguer::Password::new()
        .with_prompt("API token")
        .interact()?;
} else {
    // Non-interactive: read from flag or stdin
    let password = args.token
        .ok_or(AppError::Config("--token required in non-interactive mode".into()))?;
}
```

### Bounded Output (Principle 6)

Add `--limit`, `--offset`, and `--fields` to list commands:

```rust
#[derive(clap::Args)]
struct ListArgs {
    /// Maximum number of items to return
    #[arg(long, default_value = "100")]
    limit: usize,
    /// Number of items to skip
    #[arg(long, default_value = "0")]
    offset: usize,
    /// Comma-separated list of fields to include
    #[arg(long)]
    fields: Option<String>,
}
```

---

## Go (cobra)

### Structured Output (Principle 1)

```go
import (
    "encoding/json"
    "os"

    "golang.org/x/term"
)

func isJSON(cmd *cobra.Command) bool {
    jsonFlag, _ := cmd.Flags().GetBool("json")
    return jsonFlag || !term.IsTerminal(int(os.Stdout.Fd()))
}
```

### Schema Introspection (Principle 2)

Walk cobra's command tree to generate a schema:

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
                "name":     "--" + f.Name,
                "type":     f.Value.Type(),
                "required": f.Annotations != nil,
                "default":  f.DefValue,
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

### Non-Interactive (Principle 4)

```go
func isInteractive() bool {
    return term.IsTerminal(int(os.Stdin.Fd()))
}

if isInteractive() {
    fmt.Print("API token: ")
    token, _ = term.ReadPassword(int(os.Stdin.Fd()))
} else {
    // Read from --token flag or stdin
    token = []byte(viper.GetString("token"))
}
```

---

## Python (click)

### Structured Output (Principle 1)

```python
import sys
import json
import click

def is_json(ctx):
    return ctx.params.get("json_output") or not sys.stdout.isatty()

@click.command()
@click.option("--json", "json_output", is_flag=True, help="Output as JSON")
def list_items(json_output):
    items = fetch_items()
    if is_json(click.get_current_context()):
        click.echo(json.dumps(items))
    else:
        for item in items:
            click.echo(f"{item['name']:<20} {item['status']}")
```

### Schema Introspection (Principle 2)

Walk click's command tree:

```python
import json
import click

def generate_schema(cli):
    def walk(cmd, ctx=None):
        info = {
            "name": cmd.name,
            "description": cmd.get_short_help_str(),
        }
        args = []
        for param in cmd.params:
            if param.name in ("help",):
                continue
            arg = {
                "name": f"--{param.name}",
                "required": param.required,
                "type": param.type.name,
            }
            if param.default is not None:
                arg["default"] = param.default
            args.append(arg)
        if args:
            info["args"] = args
        if isinstance(cmd, click.Group):
            subs = []
            for name, sub in cmd.commands.items():
                subs.append(walk(sub))
            if subs:
                info["subcommands"] = subs
        return info

    return {
        "name": cli.name,
        "version": importlib.metadata.version(cli.name),
        "commands": [walk(cmd) for cmd in cli.commands.values()],
    }

@cli.command()
def schema():
    """Output JSON schema for agent integration."""
    click.echo(json.dumps(generate_schema(cli), indent=2))
```

### Non-Interactive (Principle 4)

```python
import sys

if sys.stdin.isatty():
    token = click.prompt("API token", hide_input=True)
else:
    token = ctx.params.get("token")
    if not token:
        raise click.UsageError("--token required in non-interactive mode")
```

---

## Node.js (commander / yargs)

### Structured Output (Principle 1)

```javascript
const isTTY = process.stdout.isTTY;

function output(data, opts) {
  if (opts.json || !isTTY) {
    console.log(JSON.stringify(data));
  } else {
    console.table(data);
  }
}
```

### Schema Introspection (Principle 2)

With commander:

```javascript
function generateSchema(program) {
  return {
    name: program.name(),
    version: program.version(),
    commands: program.commands.map(cmd => ({
      name: cmd.name(),
      description: cmd.description(),
      args: cmd.options.map(opt => ({
        name: `--${opt.long?.replace(/^--/, '') || opt.short}`,
        required: opt.required,
        default: opt.defaultValue,
      })),
    })),
  };
}

program.command("schema")
  .description("Output JSON schema for agent integration")
  .action(() => console.log(JSON.stringify(generateSchema(program), null, 2)));
```

---

## CI/CD Integration

### GitHub Actions (lint + test + coverage)

A standard CI workflow for CLI tools following the spec:

```yaml
name: CI

on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

concurrency:
  group: ${{ github.workflow }}-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    name: Lint
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
        with:
          components: rustfmt, clippy
      - run: make lint

  test:
    name: Test
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - run: make test

  coverage:
    name: Coverage
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: dtolnay/rust-toolchain@stable
      - uses: taiki-e/install-action@cargo-tarpaulin
      - run: cargo tarpaulin --out xml
      - uses: codecov/codecov-action@v5
        with:
          files: cobertura.xml
          token: ${{ secrets.CODECOV_TOKEN }}

  all-checks-passed:
    name: All checks passed
    runs-on: ubuntu-latest
    needs: [lint, test]
    if: always()
    steps:
      - name: Verify all checks passed
        run: |
          if [ "${{ contains(needs.*.result, 'failure') || contains(needs.*.result, 'cancelled') }}" == "true" ]; then
            echo "Some checks failed or were cancelled"
            exit 1
          fi
          echo "All checks passed"
```

### Makefile

Standard targets for spec-compliant CLI tools:

```makefile
.PHONY: build release test lint fmt check clean install

build:
	cargo build

release:
	cargo build --release

test:
	cargo test

lint:
	cargo fmt -- --check
	cargo clippy -- -D warnings

fmt:
	cargo fmt

check: lint test

clean:
	cargo clean

install: release
	cp target/release/mytool ~/.local/bin/mytool
```

---

## Testing the Spec

Verify your tool follows the spec with these checks:

```bash
# Principle 1: Structured output
mytool list --json | jq . > /dev/null       # Valid JSON
mytool list | jq . > /dev/null              # Auto-JSON when piped
mytool bad-command 2>&1 | jq .error.kind    # Structured errors

# Principle 2: Schema introspection
mytool schema | jq .commands                # Has commands
mytool schema | jq .errors                  # Has error kinds

# Principle 3: Stderr/stdout separation
mytool list 2>/dev/null | jq . > /dev/null  # No stderr in stdout

# Principle 4: Non-interactive
echo "" | mytool login --token test 2>&1    # Doesn't hang
mytool delete foo --yes 2>&1                # No prompt

# Principle 5: Idempotent
mytool start foo; mytool start foo; echo $? # Exit 0 both times

# Principle 6: Bounded output
mytool list --limit 1 --json | jq .total    # Has pagination metadata
mytool list --fields name --json | jq '.[0] | keys'  # Field filtering
```
