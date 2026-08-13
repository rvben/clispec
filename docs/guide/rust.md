# Rust (clap)

The [reference implementations](https://github.com/rvben/homebrew-tap) are all Rust CLIs using [clap](https://docs.rs/clap) 4 with derive macros. Every example below links to real, tested source code.

---

## Structured Output (Principle 1)

Auto-detect TTY to switch between human tables and JSON. Wrap the `--output` flag and `isatty()` check in an `OutputConfig` struct that every command receives.

Default the flag to `auto` so TTY detection only applies when the user did not choose a format. An explicit `mytool -o text list` must produce text even when piped:

```rust
fn resolve_format(output_flag: &str) -> Format {
    match output_flag {
        "auto" => {
            if std::io::stdout().is_terminal() {
                Format::Text
            } else {
                Format::Json
            }
        }
        "json" => Format::Json,
        "yaml" => Format::Yaml,
        _ => Format::Text,
    }
}
```

Defaulting the flag to `"text"` is a subtle bug: an explicit `-o text` becomes indistinguishable from the default and gets overridden to JSON when piped.

**Working example:** [proxctl/src/output.rs](https://github.com/rvben/proxctl/blob/main/src/output.rs)

Key points:

- `print_data()` - writes to stdout (data stream)
- `print_message()` - writes to stderr (human messages), suppressed by `--quiet`
- `print_result()` - outputs JSON or human message depending on mode

### Errors carry their own exit code

Make the error kind an enum and hang the exit code off it. That single function is what the schema's `errors` array is generated from, so the declared mapping and the process status cannot drift apart:

```rust
#[derive(Clone, Copy)]
pub enum Kind {
    Usage,
    Auth,
    NotFound,
    Conflict,
    ConfirmationRequired,
    RateLimit,
    Internal,
}

impl Kind {
    pub fn as_str(self) -> &'static str {
        match self {
            Kind::Usage => "usage",
            Kind::Auth => "auth",
            Kind::NotFound => "not_found",
            Kind::Conflict => "conflict",
            Kind::ConfirmationRequired => "confirmation_required",
            Kind::RateLimit => "rate_limit",
            Kind::Internal => "internal",
        }
    }

    pub fn exit_code(self) -> i32 {
        match self {
            Kind::Internal => 1,
            Kind::Usage => 2,
            Kind::Auth => 3,
            Kind::NotFound => 4,
            Kind::Conflict => 5,
            Kind::ConfirmationRequired => 6,
            Kind::RateLimit => 8,
        }
    }

    pub fn retryable(self) -> bool {
        matches!(self, Kind::RateLimit)
    }

    pub const ALL: &'static [Kind] = &[
        Kind::Usage, Kind::Auth, Kind::NotFound, Kind::Conflict,
        Kind::ConfirmationRequired, Kind::RateLimit, Kind::Internal,
    ];
}
```

Report the failure in the format the caller asked for. A person who gets a line of JSON in their terminal has been handed the machine's copy by mistake; the exit code is the part that stays machine-readable in both modes:

```rust
pub struct CliError {
    pub kind: Kind,
    pub message: String,
    pub hint: Option<String>,
}

fn report(err: &CliError, format: Format) -> i32 {
    match format {
        Format::Text => {
            eprintln!("Error: {}", err.message);
            if let Some(hint) = &err.hint {
                eprintln!("{hint}");
            }
        }
        _ => {
            let mut body = serde_json::json!({
                "kind": err.kind.as_str(),
                "message": err.message,
            });
            if let Some(hint) = &err.hint {
                body["hint"] = serde_json::json!(hint);
            }
            eprintln!("{}", serde_json::json!({ "error": body }));
        }
    }
    err.kind.exit_code()
}

fn main() -> std::process::ExitCode {
    // Parse errors happen before the parsed --output value exists, so recover the
    // requested format from raw argv. Otherwise a usage error is reported in the
    // wrong format, which is the one error every consumer hits first.
    let format = resolve_format(&format_from_argv());
    match run(format) {
        Ok(()) => std::process::ExitCode::SUCCESS,
        Err(err) => std::process::ExitCode::from(report(&err, format) as u8),
    }
}
```

Map clap's own failures into the same type with `Kind::Usage` so a bad flag exits 2 and reports like everything else. `Command::error` and `clap::Error::kind()` give you the text and the category.

**Working example:** [proxctl/src/api/error.rs](https://github.com/rvben/proxctl/blob/main/src/api/error.rs)

---

## Declaring what each command is

clap knows your command tree, your flags, and your help text. It does not know
whether `deploys create` can be retried or whether `services list` can return ten
thousand rows. `effects`, `cardinality` and `output_kind` are claims about
behavior, so they have to be written down once and kept next to the code they
describe.

Key the declarations by the full command path, which is the same string the
schema emits:

```rust
pub struct Declared {
    pub effects: &'static str,
    pub cardinality: Option<&'static str>,
    pub output_kind: Option<&'static str>,
    pub media_type: Option<&'static str>,
}

pub static DECLARED: &[(&str, Declared)] = &[
    ("services list", Declared {
        effects: "read_only", cardinality: Some("unbounded"),
        output_kind: None, media_type: None,
    }),
    ("services start", Declared {
        effects: "idempotent", cardinality: Some("single"),
        output_kind: None, media_type: None,
    }),
    ("deploys create", Declared {
        effects: "non_idempotent", cardinality: Some("single"),
        output_kind: None, media_type: None,
    }),
    ("logs tail", Declared {
        effects: "read_only", cardinality: None,
        output_kind: Some("stream"), media_type: None,
    }),
    ("completions", Declared {
        effects: "read_only", cardinality: None,
        output_kind: Some("opaque"), media_type: Some("text/x-shellscript"),
    }),
];
```

Then make an undeclared command a test failure rather than a schema that quietly
omits a required key:

```rust
#[test]
fn every_command_declares_its_effects() {
    for path in walk_paths(&Cli::command(), "") {
        assert!(
            DECLARED.iter().any(|(p, _)| *p == path),
            "command `{path}` has no entry in DECLARED"
        );
    }
}
```

This is the check worth having. A new subcommand is the moment the declarations
go stale, and it is the moment nobody is thinking about the schema.

---

## Schema Introspection (Principle 2)

clap exposes the full command tree at runtime via `Command::get_subcommands()` and `Command::get_arguments()`. Walk it to generate the schema, and merge in the declarations above.

**Working example:** [confluence-cli/src/schema.rs](https://github.com/rvben/confluence-cli/blob/main/src/schema.rs)

v0.3 wants a **flat** list where `name` is the complete space-separated path, and
where every entry is something a consumer can actually run. A group that only
routes to children is not an entry:

```rust
fn walk(cmd: &clap::Command, prefix: &str, out: &mut Vec<serde_json::Value>) {
    for sub in cmd.get_subcommands() {
        if sub.is_hide_set() {
            continue;
        }
        let path = if prefix.is_empty() {
            sub.get_name().to_string()
        } else {
            format!("{prefix} {}", sub.get_name())
        };
        // A group exists to route to its children, so it is not invocable and
        // does not become an entry. If a group is also runnable on its own,
        // emit it as well.
        if sub.has_subcommands() {
            walk(sub, &path, out);
        } else {
            out.push(entry(sub, &path));
        }
    }
}
```

Global flags (`--output`, `--quiet`, `--profile`) belong in the schema's top-level `global_args` array, not repeated per command and not omitted. These are the flags an agent needs on every invocation; a schema that drops them hides the most-used part of the interface. Collect them from the root command's arguments while walking subcommand args separately.

Output field types are a closed set in v0.3 (`string`, `integer`, `number`, `boolean`, `object`, `array`), with `nullable` as a separate boolean and array element types in `items`. Derive them from the Rust types you already serialize rather than writing type strings by hand: `Option<i64>` is `{"type": "integer", "nullable": true}` and `Vec<String>` is `{"type": "array", "items": {"type": "string"}}`.

Generate the `errors` array from the enum so it cannot drift:

```rust
fn errors() -> Vec<serde_json::Value> {
    Kind::ALL
        .iter()
        .map(|k| serde_json::json!({
            "kind": k.as_str(),
            "exit_code": k.exit_code(),
            "retryable": k.retryable(),
        }))
        .collect()
}
```

The result:

```json
{
  "clispec": "0.3",
  "name": "mytool",
  "version": "2.3.0",
  "output": {"tty": "text", "piped": "json"},
  "global_args": [
    {"name": "--output", "short": "-o", "type": "string",
     "enum": ["auto", "text", "json", "yaml"], "default": "auto"}
  ],
  "commands": [
    {"name": "services list", "description": "List services.",
     "effects": "read_only", "cardinality": "unbounded",
     "pagination": {"style": "cursor", "cursor_field": "next_cursor",
                    "cursor_arg": "--cursor", "limit_arg": "--limit"},
     "fields_arg": "--fields",
     "args": [{"name": "--limit", "type": "integer", "default": 100},
              {"name": "--fields", "type": "string[]"},
              {"name": "--cursor", "type": "string"}],
     "output_fields": [
       {"name": "name", "type": "string"},
       {"name": "uptime_seconds", "type": "integer", "nullable": true}
     ]}
  ],
  "errors": [
    {"kind": "usage", "exit_code": 2, "retryable": false},
    {"kind": "internal", "exit_code": 1, "retryable": false}
  ]
}
```

To add the command to your CLI:

```rust
#[derive(clap::Args)]
struct GlobalArgs {
    /// Output format: auto (TTY-detect), text, json, yaml
    #[arg(long, short = 'o', default_value = "auto")]
    output: String,
}

#[derive(clap::Subcommand)]
enum Commands {
    /// Output JSON schema for agent integration
    Schema,
    // ...
}

// Handler:
Commands::Schema => schema::print_schema(),
```

Validate the generated document in the same test run that generates it, so a
schema that stops conforming fails `cargo test` rather than somebody else's
pipeline. See [Verifying Compliance](verifying.md).

---

## Shell Completions

Use `clap_complete` to generate completions for any shell. One dependency, three lines of code.

**Working example:** Every reference implementation includes this. See any tool's `main.rs` for the pattern:

```rust
Commands::Completions { shell } => {
    clap_complete::generate(
        shell,
        &mut Cli::command(),
        env!("CARGO_PKG_NAME"),
        &mut std::io::stdout(),
    );
}
```

A completion script is not a JSON document, so declare the command
`"output_kind": "opaque"` with `"media_type": "text/x-shellscript"`. That exempts
it from the structured-output and bounded-output rules, and it does not exempt it
from anything else: stdout carries the script and nothing else, and a failure
still exits with a declared kind.

---

## Non-Interactive (Principle 4)

Gate interactive prompts on TTY detection. Use [dialoguer](https://docs.rs/dialoguer) for interactive input, with flag fallbacks for scripted use.

A command that would prompt for confirmation must refuse without a TTY rather
than proceeding, and it declares the bypass flag it names in the hint:

```rust
if !std::io::stdin().is_terminal() && !args.yes {
    return Err(CliError {
        kind: Kind::ConfirmationRequired,
        message: format!("Deleting {name} requires confirmation"),
        hint: Some("Re-run with --yes to confirm.".into()),
    });
}
```

The schema entry for that command carries `"confirmation_bypass_arg": "--yes"`,
and `--yes` must be declared in its `args`. A command that has never prompted
needs no bypass flag: adding a refusal to it breaks every script that already
runs it unattended.

**Working example:** [proxctl/src/main.rs](https://github.com/rvben/proxctl/blob/main/src/main.rs) - search for `config_init` to see the interactive setup flow with password input, credential validation, and non-interactive alternatives.

---

## Bounded Output (Principle 6)

Pagination flags belong on `unbounded` commands, which are the ones whose
collection can grow without limit. A `--limit` on a command that returns one
record is a flag that does nothing.

```rust
/// Shared by list commands whose result set is unbounded. Commands returning a
/// single record, or a collection the caller sized, do not flatten this in.
#[derive(clap::Args)]
struct PageArgs {
    /// Maximum number of items to return
    #[arg(long, default_value = "100")]
    limit: usize,
    /// Continuation token from a previous response
    #[arg(long)]
    cursor: Option<String>,
    /// Comma-separated list of fields to include
    #[arg(long, value_delimiter = ',')]
    fields: Option<Vec<String>>,
}
```

The envelope has to say the result is partial, or a consumer will report the
first page as the whole set:

```rust
#[derive(serde::Serialize)]
struct Page<T> {
    items: Vec<T>,
    total: usize,
    #[serde(skip_serializing_if = "Option::is_none")]
    next_cursor: Option<String>,
}
```

Every argument named in `pagination` or `fields_arg` must exist in that command's
`args` or in `global_args`. Deriving the names from the same constants the clap
attributes use keeps a rename from producing a schema that references a flag the
binary no longer has.

---

## Recommended Crates

| Crate | Purpose |
|-------|---------|
| [clap](https://crates.io/crates/clap) | CLI argument parsing with derive macros |
| [clap_complete](https://crates.io/crates/clap_complete) | Shell completion generation |
| [serde](https://crates.io/crates/serde) + [serde_json](https://crates.io/crates/serde_json) | JSON serialization |
| [owo-colors](https://crates.io/crates/owo-colors) | Terminal colors |
| [thiserror](https://crates.io/crates/thiserror) | Error type definitions |
| [dialoguer](https://crates.io/crates/dialoguer) | Interactive prompts |
| [reqwest](https://crates.io/crates/reqwest) | HTTP client (with `rustls-tls`) |
| [tokio](https://crates.io/crates/tokio) | Async runtime |
