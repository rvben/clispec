# The CLI Spec

**7 principles for building CLI tools that work for humans, scripts, and AI agents.**

Version 0.1 — April 2026

---

Most CLI tools were designed for human operators: colored tables, prose error messages, interactive prompts. AI agents are now a primary consumer of CLI tools, and they need structured output, predictable behavior, and machine-readable introspection.

The solution is not to choose one audience over the other. Design for both simultaneously.

Agents are not trusted operators. They hallucinate inputs, retry unpredictably, and cannot respond to interactive prompts. A well-designed CLI handles all of this gracefully — and in doing so, becomes better for humans and scripts too.

| # | Principle | In one sentence |
|---|-----------|----------------|
| 1 | [Structured Output](#1-structured-output) | Emit JSON when piped, human-friendly output in a terminal, support `--output` for format selection. |
| 2 | [Schema Introspection](#2-schema-introspection) | Let consumers discover commands, arguments, output fields, and error types at runtime. |
| 3 | [Stderr/Stdout Separation](#3-stderrstdout-separation) | Data goes to stdout, everything else to stderr. |
| 4 | [Non-Interactive by Default](#4-non-interactive-by-default) | Never block on input without a TTY. |
| 5 | [Idempotent Operations](#5-idempotent-operations) | Repeated commands produce the same result without side effects. |
| 6 | [Bounded Output](#6-bounded-output) | Let consumers control the volume and shape of output. |

---

## The 6 Principles

### 1. Structured Output

Every command must support machine-readable output. When stdout is a TTY, display human-friendly tables with colors. When piped, emit JSON by default. Support `--output <format>` (or `-o`) for explicit format selection. Use `isatty()` to auto-detect.

JSON is the default structured format due to universal tooling support. But JSON is not always optimal — Markdown and YAML use significantly fewer tokens for LLM consumption. Supporting multiple formats via `--output` lets consumers choose.

```bash
# DO: Auto-detect and support explicit format selection
$ mytool list
NAME        STATUS    UPTIME
web-01      running   14d 3h
db-01       stopped   —

$ mytool list -o json
[{"name": "web-01", "status": "running", "uptime_seconds": 1220400},
 {"name": "db-01", "status": "stopped", "uptime_seconds": null}]

$ mytool list -o yaml
- name: web-01
  status: running
  uptime_seconds: 1220400

$ mytool list | jq '.[] | select(.status == "running")'
# Works — JSON emitted automatically when piped
```

```bash
# DON'T: Only support human-readable output
$ mytool list
NAME        STATUS    UPTIME
web-01      running   14d 3h
# No way to get structured data
```

On failure, emit a structured error to stderr and exit non-zero. Include a machine-readable `kind` field so consumers can branch on the failure type without parsing the message.

```bash
# DO: Structured errors on stderr
$ mytool connect --profile staging
# stderr: {"error": {"kind": "auth", "message": "Token expired for profile 'staging'"}}
# exit code: 1
```

```bash
# DON'T: Prose-only errors
$ mytool connect --profile staging
Error: something went wrong  # what kind? Consumer has no idea.
```

### 2. Schema Introspection

Provide a `schema` command that outputs a machine-readable description of the tool's capabilities. Agents should never need to parse `--help` text to discover what a tool can do.

A useful schema includes:

- **Commands** with descriptions
- **Arguments** with types, defaults, and whether they are required
- **Output fields** with types — so consumers know the shape of the response without calling the command
- **Error kinds** — the finite set of `kind` values the tool emits, so consumers can write exhaustive handlers
- **Mutation markers** — which commands are read-only and which modify state

```bash
# DO: Comprehensive machine-readable schema
$ mytool schema
{
  "name": "mytool",
  "version": "1.2.0",
  "commands": [
    {
      "name": "list",
      "description": "List all services",
      "mutating": false,
      "args": [
        {"name": "--status", "type": "string", "required": false,
         "enum": ["running", "stopped", "all"], "default": "all"},
        {"name": "--limit", "type": "integer", "required": false, "default": 100}
      ],
      "output_fields": [
        {"name": "name", "type": "string"},
        {"name": "status", "type": "string"},
        {"name": "uptime_seconds", "type": "integer | null"}
      ]
    }
  ],
  "errors": [
    {"kind": "auth", "retryable": false, "description": "Authentication failed"},
    {"kind": "not_found", "retryable": false, "description": "Resource does not exist"},
    {"kind": "timeout", "retryable": true, "description": "Request timed out"},
    {"kind": "rate_limit", "retryable": true, "description": "Too many requests"}
  ]
}
```

```bash
# DON'T: Rely on help text as the only interface
$ mytool list --help
Usage: mytool list [options]
  Lists services. Use --status to filter.
# Consumers must regex-parse this to discover capabilities
```

For context that schema cannot capture — workflows, security boundaries, operational guidance — ship companion files alongside your tool (`CONTEXT.md`, `SKILL.md`, or `AGENTS.md`).

### 3. Stderr/Stdout Separation

Data goes to stdout. Messages, progress indicators, and diagnostics go to stderr. Never mix human-readable messages into the data stream.

This applies in every output mode, not just structured formats. An agent piping your output to `jq` should never get a progress message in the JSON.

```bash
# DO: Clean separation
$ mytool list 2>/dev/null | jq '.[0].name'
"web-01"

# Behind the scenes:
# stdout: [{"name": "web-01", ...}]
# stderr: Fetching services... done.
```

```bash
# DON'T: Mix streams
$ mytool list | jq '.[0].name'
Fetching services...
parse error: Invalid literal at line 1, column 1
```

### 4. Non-Interactive by Default

All commands must work without a TTY. Interactive prompts should only appear when stdin is a terminal. Provide flag alternatives for every interactive input.

An agent cannot type "y" at a confirmation prompt. If your CLI blocks waiting for input that will never come, the agent is stuck.

```bash
# DO: Work in both modes
$ mytool login                          # Interactive: prompts for password
$ mytool login --password-stdin < pw    # Scripted: reads from stdin
$ mytool delete vm-01                   # Interactive: "Are you sure?"
$ mytool delete vm-01 --yes             # Scripted: no prompt
```

```bash
# DON'T: Block on input without a TTY
$ echo '{}' | mytool login
Password: ^C  # Hangs forever
```

For destructive operations, consider supporting `--dry-run` with structured output so consumers can preview changes before committing.

### 5. Idempotent Operations

Commands that can be safely re-run should produce the same result. Starting an already-running service returns success. Creating a resource that already exists with identical configuration is a no-op.

Agents retry. They lose track of state. They run the same command twice because a previous step timed out. Idempotency makes retries safe. If your CLI returns an error on a repeat operation, the agent will try to "fix" a problem that does not exist.

When a resource exists with a different configuration than requested, return an error with a `conflict` kind — do not silently overwrite and do not silently ignore the difference.

```bash
# DO: Idempotent by default
$ mytool start web-01
Started web-01

$ mytool start web-01
web-01 is already running   # exit code 0

# DO: Detect conflicts
$ mytool create db-01 --memory 4GB
# stderr: {"error": {"kind": "conflict", "message": "db-01 exists with memory=8GB"}}
```

```bash
# DON'T: Error on repeat operations
$ mytool start web-01
Error: web-01 is already running   # exit code 1 — agent thinks it failed
```

### 6. Bounded Output

Let consumers control the volume and shape of output. Agents have finite context windows — a command that dumps 10,000 records as a single JSON array is unusable.

Support `--limit` and `--offset` (or cursor-based pagination) for list commands. Support `--fields` to select specific output fields. Document pagination behavior in the schema.

```bash
# DO: Pagination and field selection
$ mytool list --limit 10 --offset 0 -o json
{"items": [...], "total": 1847, "limit": 10, "offset": 0}

$ mytool list --limit 10 --offset 10 -o json
{"items": [...], "total": 1847, "limit": 10, "offset": 10}

$ mytool list --fields name,status --limit 10 -o json
{"items": [{"name": "web-01", "status": "running"}, {"name": "db-01", "status": "stopped"}],
 "total": 1847, "limit": 10, "offset": 0}
```

```bash
# DON'T: Unbounded output
$ mytool list -o json
# Returns 50KB+ JSON array — exceeds agent context limits
# No way to paginate or reduce output size
```

---

## General Guidance

These recommendations apply broadly but are not principles in their own right.

**Validate inputs strictly.** Agents hallucinate plausible-but-wrong inputs. Reject path traversals, control characters, and malformed data. Use allowlists over denylists. Your CLI should never pass unsanitized input to a shell.

**Use consistent command structure.** The noun-verb pattern (`mytool resource action`) makes command discovery a tree search rather than a guessing game. Keep flag names consistent across subcommands.

**Document stability.** If agents depend on your structured output, field removal is a breaking change. Document which parts of your output are stable.

---

## Reference Implementations

These tools are designed around these principles:

[proxctl](https://github.com/rvben/proxctl) ·
[unifi-cli](https://github.com/rvben/unifi-cli) ·
[vership](https://github.com/rvben/vership) ·
[confluence-cli](https://github.com/rvben/confluence-cli)

For a full list, see the [homebrew tap](https://github.com/rvben/homebrew-tap). For registries of agent-friendly CLI tools, see [clime.sh](https://clime.sh) and [CLI-Anything Hub](https://clianything.cc).

---

## Related Work

- [You Need to Rewrite Your CLI for AI Agents](https://justin.poehnelt.com/posts/rewrite-your-cli-for-ai-agents/) — Justin Poehnelt
- [Writing CLI Tools That AI Agents Actually Want to Use](https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no) — DEV Community
- [One CLI, Two Audiences](https://www.checklyhq.com/blog/agentic-cli/) — Checkly
- [Command Line Interface Guidelines](https://clig.dev/) — clig.dev

---

## Contributing

This is a living specification. Contributions welcome.

- Open an [issue](https://github.com/rvben/clispec/issues) to discuss changes
- Submit a pull request for spec improvements
- To list your tool as a reference implementation, open a PR

---

## License

[CC BY 4.0](LICENSE) — Copyright 2026 Ruben Jongejan
