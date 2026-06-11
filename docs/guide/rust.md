# Rust (clap)

The [reference implementations](https://github.com/rvben/homebrew-tap) are all Rust CLIs using [clap](https://docs.rs/clap) 4 with derive macros. Every example below links to real, tested source code.

---

## Structured Output (Principle 1)

Auto-detect TTY to switch between human tables and JSON. Wrap the `--output` flag and `isatty()` check in an `OutputConfig` struct that every command receives.

Default the flag to `auto` so TTY detection only applies when the user did not choose a format. An explicit `-o text` must produce text even when piped:

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

- `print_data()` — writes to stdout (data stream)
- `print_message()` — writes to stderr (human messages), suppressed by `--quiet`
- `print_result()` — outputs JSON or human message depending on mode

For structured errors with a `kind` field, define an error enum with exit codes:

**Working example:** [proxctl/src/api/error.rs](https://github.com/rvben/proxctl/blob/main/src/api/error.rs)

---

## Schema Introspection (Principle 2)

clap exposes the full command tree at runtime via `Command::get_subcommands()` and `Command::get_arguments()`. Walk it to auto-generate a JSON schema — no manual maintenance required.

**Working example:** [confluence-cli/src/schema.rs](https://github.com/rvben/confluence-cli/blob/main/src/schema.rs) (132 lines including tests)

This implementation:

- Walks the clap command tree recursively with `walk_commands()`
- Extracts argument names, types, defaults, possible values, and required status
- Separates positional args from flags
- Flattens nested subcommands into `"space list"`, `"page get"` style paths
- Includes tests that verify schema structure and completeness

Global flags (`--output`, `--quiet`, `--profile`) belong in the schema's top-level `global_args` array, not repeated per command and not omitted. These are the flags an agent needs on every invocation; a schema that drops them hides the most-used part of the interface. Collect them from the root command's arguments while walking subcommand args separately.

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

---

## Non-Interactive (Principle 4)

Gate interactive prompts on TTY detection. Use [dialoguer](https://docs.rs/dialoguer) for interactive input, with flag fallbacks for scripted use.

**Working example:** [proxctl/src/main.rs](https://github.com/rvben/proxctl/blob/main/src/main.rs) — search for `config_init` to see the interactive setup flow with password input, credential validation, and non-interactive alternatives.

---

## Bounded Output (Principle 6)

Add `--limit`, `--offset`, and `--fields` as standard list command arguments:

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
