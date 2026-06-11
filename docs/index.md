# The CLI Spec

**6 principles for building CLI tools that work for humans, scripts, and AI agents.**

Version 0.2 - June 2026

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
| 5 | [Idempotent Operations](#5-idempotent-operations) | Re-running a command converges to the same state and exits zero. |
| 6 | [Bounded Output](#6-bounded-output) | Let consumers control the volume and shape of output. |

---

## The Principles

### 1. Structured Output

Every command must support machine-readable output. When stdout is a TTY, display human-friendly tables with colors. When piped, emit JSON by default. Support `--output <format>` (or `-o`) for explicit format selection. Use `isatty()` to auto-detect.

The format flag is three-valued: its default (`auto`) selects the format by TTY detection, and an explicit value always wins. `mytool list -o text | grep web` must produce text, not JSON. Do not make the human format the flag's default value - that makes "explicitly chose text" indistinguishable from "did not choose", and the explicit choice gets silently overridden when piped.

`--output`/`-o` is the canonical spelling. If a tool's `--output` or `-o` is already bound by an existing contract (the destination-path convention of curl and most compilers), `--format` is an acceptable alternative; repurposing the existing flag would break the contract the spec tells you to protect. Whichever flag the tool uses must be declared in the schema's `global_args` so consumers discover it instead of guessing.

JSON is the default structured format due to universal tooling support. But JSON is not always optimal — Markdown and YAML use significantly fewer tokens for LLM consumption. Supporting multiple formats via `--output` lets consumers choose.

```bash
# DO: Auto-detect and support explicit format selection
$ mytool list
NAME        STATUS    UPTIME
web-01      running   14d 3h
db-01       stopped   —

$ mytool list -o json
{"items": [{"name": "web-01", "status": "running", "uptime_seconds": 1220400},
           {"name": "db-01", "status": "stopped", "uptime_seconds": null}]}

$ mytool list -o yaml
items:
  - name: web-01
    status: running
    uptime_seconds: 1220400

$ mytool list | jq '.items[] | select(.status == "running")'
# Works — JSON emitted automatically when piped
```

```bash
# DON'T: Only support human-readable output
$ mytool list
NAME        STATUS    UPTIME
web-01      running   14d 3h
# No way to get structured data
```

List commands wrap their results in an `{"items": [...]}` envelope rather than emitting a bare array. The envelope is what gives pagination and truncation metadata a place to live (see Principle 6), and it means the output shape never changes when metadata is added.

On failure, emit a structured error and exit non-zero. The error envelope is:

```json
{"error": {"kind": "auth", "message": "Token expired for profile 'staging'",
           "hint": "Run 'mytool login --profile staging' to refresh."}}
```

- **`kind`** (required) - stable identifier from the finite set declared in the schema. Consumers branch on it without parsing the message.
- **`message`** (required) - human-readable description.
- **`hint`** (optional) - actionable remediation. `retryable` in the schema tells a consumer whether to retry; `hint` tells it what to do instead.
- **`details`** (optional) - object with structured, kind-specific context.

Write the envelope as a single line of JSON, as the **last line of stderr**. Principle 3 allows progress messages on stderr, so the fixed position is what keeps the error mechanically extractable.

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

Not every non-zero exit is an error. Diff-like commands exit non-zero to report a data state: `diff` and `grep` exit 1 for "there is a difference" or "no match", a dependency checker exits 1 for "updates exist". These are **outcomes**, not failures. An outcome exit writes no error envelope; stdout carries the result. Declare them in the schema's `outcomes` array so consumers can branch on the exit code without misclassifying a data state as a failure:

```json
"outcomes": [
  {"code": 1, "name": "changes_pending",
   "description": "Differences found; the report is on stdout. Not an error."}
]
```

Outcome codes must not overlap with the exit codes declared in `errors`.

### 2. Schema Introspection

Provide a `schema` command that outputs a machine-readable description of the tool's capabilities. Agents should never need to parse `--help` text to discover what a tool can do.

A useful schema includes:

- **Commands** with descriptions
- **Arguments** with types, defaults, and whether they are required
- **Global arguments**, flags accepted by every command (`--output`, `--quiet`, `--profile`), listed once at the top level. These are the flags a consumer needs on every invocation; a schema that omits them hides the most-used part of the interface.
- **Output fields** with types — so consumers know the shape of the response without calling the command
- **Error kinds** with **exit codes**, the finite set of `kind` values the tool emits and the exit code each maps to, so consumers can branch on the exit code alone, before parsing anything
- **Outcomes**, documented non-zero exits that signal a data state rather than a failure (see Principle 1), kept separate from errors
- **Mutation markers** — which commands are read-only and which modify state

```bash
# DO: Comprehensive machine-readable schema
$ mytool schema
{
  "clispec": "0.2",
  "name": "mytool",
  "version": "1.2.0",
  "global_args": [
    {"name": "--output", "type": "string", "enum": ["auto", "text", "json", "yaml"],
     "default": "auto", "description": "Output format; auto detects TTY"},
    {"name": "--quiet", "type": "boolean", "default": false}
  ],
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
    {"kind": "auth", "exit_code": 3, "retryable": false, "description": "Authentication failed"},
    {"kind": "not_found", "exit_code": 4, "retryable": false, "description": "Resource does not exist"},
    {"kind": "timeout", "exit_code": 5, "retryable": true, "description": "Request timed out"},
    {"kind": "rate_limit", "exit_code": 6, "retryable": true, "description": "Too many requests"}
  ]
}
```

Mutation markers are a contract, not documentation. Consumers will grant trust based on `mutating: false` (for example, a permission system auto-approving read-only commands). A command marked non-mutating must truly not modify state, and an **unmarked command means unknown, not read-only**: consumers must not assume safety from absence.

Entries in `commands` are invocable commands, identified by their full path as a single string (`"list"`, `"sites use"`, `"files download"`). A group node that only routes to subcommands (`sites` by itself) is not a command: either omit it, or include it for navigation without trust semantics. `mutating` is meaningful only on something that can be executed; consumers should not derive trust from it on a group entry, and tools should not set it there to influence tooling behavior.

The `schema` command must work **before anything else does**: no authentication, no configuration file, no network. An agent reaches for the schema precisely when it knows nothing about the tool — often before setup has happened or after it has failed. A schema command that requires credentials is unavailable in exactly the situation it exists for.

The root `--help` output must mention the `schema` command. `--help` is the universal first probe; it is how a consumer discovers that a machine-readable contract exists at all.

The schema command is not exempt from Bounded Output (Principle 6). For tools with large command trees, a full dump is thousands of tokens of mostly irrelevant detail. `schema` SHOULD accept a command path that narrows the document to a subtree: `mytool schema apps deploy` emits the same top-level shape (`name`, `version`, `global_args`, `errors`) with `commands` filtered to the named subtree. Top-level metadata stays included because a consumer needs it regardless of which command it is about to run.

```bash
# DON'T: Rely on help text as the only interface
$ mytool list --help
Usage: mytool list [options]
  Lists services. Use --status to filter.
# Consumers must regex-parse this to discover capabilities
```

A clispec-compliant schema document MUST validate against [`https://clispec.dev/schema/v0.2.json`](/schema/v0.2.json). The schema is intentionally minimal — additional properties are permitted at every level, so tools can attach their own metadata without breaking conformance.

v0.2 is additive over v0.1 (`global_args` at the top level, `exit_code` on error entries, and clarified `mutating` semantics). Any v0.1 document that does not already use those property names with conflicting types validates against v0.2 unchanged. [`v0.1.json`](/schema/v0.1.json) remains published.

Two widening amendments were folded into v0.2 shortly after publication: the `--format` spelling allowance for tools whose `--output` is already taken, and the optional `outcomes` array. Both only relax or add; every document and tool that conformed to v0.2 at publication still conforms.

For context that schema cannot capture — workflows, security boundaries, operational guidance — ship companion files alongside your tool (`CONTEXT.md`, `SKILL.md`, or `AGENTS.md`).

#### Interop with CLI description formats

The clispec schema is the agent-facing contract: output fields, error kinds, mutation markers. It is not a replacement for richer description formats designed for documentation and shell-completion generation:

- [OpenCLI](https://opencli.org/) — OpenAPI-style description of an entire CLI surface (JSON/YAML).
- [usage](https://usage.jdx.dev/) — KDL format used by mise to generate man pages, completions, and Fig specs from a single source.
- [Fig autocomplete spec](https://fig.io/docs/getting-started/first-completion-spec) — TypeScript-based completion definitions.
- [carapace-spec](https://github.com/carapace-sh/carapace-spec) — multi-shell completion spec with bridges from clap, cobra, click, and others.

Tools are encouraged to emit one of these in addition to the clispec schema. clispec sits one level up: it mandates that the agent-facing contract exists, while leaving man pages, completions, and IDE integration to formats purpose-built for them.

### 3. Stderr/Stdout Separation

Data goes to stdout. Messages, progress indicators, and diagnostics go to stderr. Never mix human-readable messages into the data stream.

This applies in every output mode, not just structured formats. An agent piping your output to `jq` should never get a progress message in the JSON.

```bash
# DO: Clean separation
$ mytool list 2>/dev/null | jq '.items[0].name'
"web-01"

# Behind the scenes:
# stdout: {"items": [{"name": "web-01", ...}]}
# stderr: Fetching services... done.
```

```bash
# DON'T: Mix streams
$ mytool list | jq '.items[0].name'
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

Not blocking is half the rule; the other half is which way to fall. A command that would ask for confirmation on a TTY must, without a TTY, **refuse and exit non-zero** with a structured error naming the bypass flag. It must never proceed silently: an agent that hallucinates and retries should hit a wall, not a trigger. Use the `confirmation_required` error kind, with the `hint` naming the flag.

```bash
# DO: Fail safe without a TTY
$ mytool delete vm-01 < /dev/null
# stderr: {"error": {"kind": "confirmation_required",
#          "message": "Deleting vm-01 requires confirmation",
#          "hint": "Re-run with --yes to confirm."}}
# exit code: 2
```

```bash
# DON'T: Treat a missing TTY as consent
$ mytool delete vm-01 < /dev/null
Deleted vm-01   # the confirmation prompt was the only safeguard, and it vanished
```

The refusal rule applies only to commands that would actually prompt. A command that has never prompted needs no bypass flag, and adding a non-TTY refusal to it is a breaking change for every script that already runs it unattended, not a compliance step. A tool with no interactive prompts satisfies this principle as-is. Reserve new confirmation gates for genuinely destructive or irreversible operations, and introduce them as deliberate design decisions, never as part of a mechanical compliance pass.

For destructive operations, consider supporting `--dry-run` with structured output so consumers can preview changes before committing.

### 5. Idempotent Operations

Commands that can be safely re-run should produce the same result. Starting an already-running service returns success. Creating a resource that already exists with identical configuration is a no-op.

Agents retry. They lose track of state. They run the same command twice because a previous step timed out. Idempotency makes retries safe. If your CLI returns an error on a repeat operation, the agent will try to "fix" a problem that does not exist.

When a resource exists with a different configuration than requested, return an error with a `conflict` kind — do not silently overwrite and do not silently ignore the difference.

Structured output of mutating commands SHOULD include a `changed` boolean (the Terraform and Ansible convention): `true` when the command did work, `false` when the requested state was already in place. Exit 0 says the state is right; `changed` says whether this invocation did anything, and a consumer needs that distinction without parsing prose, for example to decide whether dependent services must be restarted or whether a retry actually re-ran something.

```bash
# DO: Idempotent by default
$ mytool start web-01
Started web-01

$ mytool start web-01
web-01 is already running   # exit code 0

$ mytool start web-01 -o json
{"name": "web-01", "status": "running", "changed": false}   # exit code 0

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

Let consumers control the volume and shape of output. Agents have finite context windows; a command that dumps 10,000 records as a single JSON blob drowns the signal and burns the budget.

Support `--limit` and `--offset` (or cursor-based pagination) for list commands. Support `--fields` to select specific output fields. Declare the pagination flags in the schema's `args` so consumers discover them.

When output is bounded, **say so in-band**. The envelope must carry enough metadata (`total`, a cursor, or an explicit `truncated: true`) for the consumer to know it received a partial result. Silent truncation is the worst failure mode: an agent that gets 100 silently-truncated rows will confidently report that 100 is all there is.

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
# Returns a 50KB+ JSON array - wastes the consumer's context budget
# No way to paginate or reduce output size, no signal that output is partial
```

---

## General Guidance

These recommendations apply broadly but are not principles in their own right.

**Validate inputs strictly.** Agents hallucinate plausible-but-wrong inputs. Reject path traversals, control characters, and malformed data. Use allowlists over denylists. Your CLI should never pass unsanitized input to a shell.

**Use consistent command structure.** The noun-verb pattern (`mytool resource action`) makes command discovery a tree search rather than a guessing game. Keep flag names consistent across subcommands.

**Document stability.** If agents depend on your structured output, field removal is a breaking change. Document which parts of your output are stable.

**Never accept secrets via argv.** A `--token abc123` argument is visible in `ps`, lands in shell history, and ends up verbatim in agent transcripts, which may be logged, summarized, or shared. Accept secrets via stdin (`--password-stdin`), environment variables, or the OS keychain.

**Offer NDJSON for large or streaming output.** One JSON object per line streams incrementally, composes with `--limit`, and degrades gracefully: `head -n 20` of NDJSON is 20 valid records, while a truncated JSON array is unparseable. Expose it as `-o ndjson`.

**Emit no ANSI escapes when stdout is not a TTY, and respect `NO_COLOR`.** Auto-JSON covers the piped case, but text mode requested explicitly via `-o text` must also be free of color codes when piped.

**Make long-running operations observable.** A command that is silent for minutes gets killed and retried (another reason Principle 5 matters). Report progress on stderr, support `--timeout` where applicable, and for genuinely long jobs prefer an async pattern: a start command that returns a job ID, and a status command to poll.

**Be safe under concurrency.** Agents parallelize aggressively; assume two invocations of your tool run at the same time. Use atomic writes and lock files for shared state such as config and caches.

---

## Adopting the Spec in an Existing CLI

Most tools that adopt this spec are not greenfield. They have users, scripts, and an existing flag surface, and that surface is itself a contract. Compliance work must not break it.

- **Keep legacy format flags working.** If the tool had `--json`, keep it as a hidden alias for `--output json`. If `--format` was the selector, keep it as an alias of `--output`. Removing a flag that scripts already pass is a breaking change dressed up as cleanup.
- **Never narrow accepted values.** If the format flag accepted `table` or `plain`, keep accepting them (mapping to the text format) even when the documented set becomes `auto`, `text`, `json`.
- **Watch for flag collisions.** Some CLIs already use `--output` or `-o` for a destination path (the curl and compiler convention). That existing meaning must keep working exactly as before; resolving the collision without breaking either contract takes deliberate design, not a mechanical rename.
- **Add, don't move.** New flags, error kinds, and envelope fields are additive. Renaming or removing anything existing is a versioning decision separate from spec adoption.
- **Existing exit codes and prompt behavior are contracts too.** Do not add confirmation gates to commands that never prompted (see Principle 4), and do not change documented exit semantics; declare them in the schema instead.

---

## Conformance

A CLI is clispec v0.2 compliant when it satisfies all of the following:

- [ ] Emits structured output to stdout when stdout is not a TTY, and supports an explicit format-selection flag declared in `global_args`: canonically `--output`/`-o`, or `--format` where `--output` is already bound by an existing contract. An explicit format always wins over TTY detection. *(Principle 1)*
- [ ] On failure, exits non-zero and writes the structured error envelope as the last line of stderr. Exit codes declared in `outcomes` are data states, not failures, and write no envelope. *(Principle 1)*
- [ ] Exposes a `schema` subcommand whose output validates against [`clispec.dev/schema/v0.2.json`](/schema/v0.2.json). *(Principle 2)*
- [ ] `schema` succeeds with no authentication, no configuration file, and no network access. *(Principle 2)*
- [ ] Root `--help` output mentions the `schema` subcommand. *(Principle 2)*
- [ ] Writes data to stdout and diagnostics to stderr in every output mode. *(Principle 3)*
- [ ] Runs to completion without a TTY and provides a flag alternative for every interactive prompt. *(Principle 4)*
- [ ] Commands that would prompt for confirmation refuse without a TTY, emitting a `confirmation_required` error that names the bypass flag; they never proceed silently. *(Principle 4)*
- [ ] Re-running a command whose outcome is already in place exits zero; incompatible repeats emit a `conflict` error kind. *(Principle 5)*
- [ ] List commands support `--limit` and `--offset` (or cursor pagination) and `--fields` for field selection, and bounded output carries truncation metadata in-band. *(Principle 6)*

The [`clispec`](https://github.com/rvben/clispec-cli) tool scores any binary on your `$PATH` against this checklist:

```bash
cargo install clispec
clispec score mytool
```

---

## Reference Implementations

These tools are designed around these principles:

[proxctl](https://github.com/rvben/proxctl) ·
[unifi-cli](https://github.com/rvben/unifi-cli) ·
[vership](https://github.com/rvben/vership) ·
[confluence-cli](https://github.com/rvben/confluence-cli) ·
[tidemark](https://github.com/rvben/tidemark) ·
[upd](https://github.com/rvben/upd)

tidemark is a fully offline tool (every runtime check passes under the scorer); upd demonstrates the `outcomes` array with its diff-like exit-1-on-updates contract.

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
