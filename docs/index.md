# The CLI Spec

**6 principles for building CLI tools that work for humans, scripts, and AI agents.**

Version 0.3 - candidate, August 2026

> **This version is a candidate.** The schema and this text can still change,
> including in ways that make a previously valid document invalid. Build against
> it to try it and to send feedback; do not ship `"clispec": "0.3"` and expect it
> to keep validating. [Versions](versions.md) explains what freezing means and
> what v0.3 needs before it freezes. [v0.2](spec/v0.2.md) is frozen and remains
> valid.

---

Most CLI tools were designed for human operators: colored tables, prose error
messages, interactive prompts. AI agents are now a primary consumer of CLI tools,
and they need structured output, predictable behavior, and machine-readable
introspection.

The solution is not to choose one audience over the other. Design for both
simultaneously.

Agents are not trusted operators. They hallucinate inputs, retry unpredictably,
and cannot respond to interactive prompts. A well-designed CLI handles all of
this gracefully, and in doing so becomes better for humans and scripts too.

| # | Principle | In one sentence |
|---|-----------|----------------|
| 1 | [Structured Output](#1-structured-output) | Always offer explicit JSON, for structured stdout and for every failure; prefer JSON when piped while preserving established text contracts. |
| 2 | [Schema Introspection](#2-schema-introspection) | Let consumers discover commands, arguments, output shapes, and error types at runtime. |
| 3 | [Stderr/Stdout Separation](#3-stderrstdout-separation) | Data goes to stdout, everything else to stderr. |
| 4 | [Non-Interactive by Default](#4-non-interactive-by-default) | Never block on input without a TTY. |
| 5 | [Safe Retries](#5-safe-retries) | Say what re-running does, and give the consumer a safe move. |
| 6 | [Bounded Output](#6-bounded-output) | Let consumers control the volume and shape of output. |

---

## Describe the command, then apply the rules

This is what changed in v0.3.

v0.2 applied one shape to every command. Every command was supposed to emit a
JSON document, be idempotent, and support `--limit`, `--offset` and `--fields`.
Real tools are not like that. A command that writes a tarball to stdout has no
fields to select. A command that streams log lines has no total to report. A
command that creates a deployment cannot be idempotent without lying about what
it does. Under v0.2 those commands failed the checklist for being honest, and
the fix was to bolt on a flag that did nothing.

v0.3 asks each command to describe itself along three axes, and then applies only
the guarantees that make sense for what it is. The declarations are required
precisely because they buy exemptions: a consumer that reads
`"cardinality": "bounded"` and skips pagination has been told something, and the
tool is accountable for it.

### Output kind: what stdout actually is

`output_kind` is one of:

- **`data`** (the default) - a single structured document. The full
  structured-output rules apply.
- **`stream`** - an unbounded sequence of records emitted incrementally, one per
  line, as they become available. Declared with `stream_format`, which defaults
  to `ndjson`.
- **`opaque`** - bytes with a declared `media_type`: a completion script, an
  archive, a downloaded file, another program's output passed through. There is
  no field structure and no envelope.

```json
{"name": "logs tail", "description": "Stream log records until interrupted.",
 "effects": "read_only", "output_kind": "stream", "stream_format": "ndjson"}

{"name": "artifacts download", "description": "Write an artifact's bytes to stdout.",
 "effects": "read_only", "output_kind": "opaque",
 "media_type": "application/octet-stream"}
```

Each kind carries its own keys and only its own: `cardinality` and `pagination`
belong to `data`, `stream_format` to `stream`, `media_type` to `opaque`. A key on
the wrong kind is an error rather than an ignored extra, because each one is a
promise about a shape that kind does not have. An artifact has no records to
page. A stream has no total, so there is no size for `cardinality` to describe,
and a cursor into a sequence with no end describes a loop that never terminates:
a stream is bounded by the consumer closing the pipe, not by the producer
handing out pages.

### Effects: what re-running does

`effects` is required on every command and is one of:

- **`read_only`** - does not modify any state a consumer cares about.
- **`idempotent`** - may modify state, but repeating converges to the same state
  and is safe to retry.
- **`non_idempotent`** - repeating may cause an additional effect. A consumer
  must not retry blindly.

### Cardinality: how many records come back

`cardinality` is required on `data` commands and is one of:

- **`single`** - one record, or one document that is not a collection.
- **`bounded`** - a collection whose size is fixed by the caller's own input or
  by a small closed domain.
- **`unbounded`** - a collection that can grow without limit. Pagination and
  field selection become mandatory (Principle 6).

### What a declaration never buys you

Narrowing is not exemption. Whatever a command declares, it still:

- writes data to stdout and everything else to stderr (Principle 3),
- reports failure with a declared error kind and the exit code that kind maps to
  (Principle 1),
- runs to completion without a TTY, or refuses safely (Principle 4).

An `opaque` command is excused from JSON on stdout, not from JSON on stderr. A
`stream` is excused from reporting a total, not from separating its streams.

---

## The Principles

### 1. Structured Output

Every command must be machine-consumable. A tool MUST provide structured output
(JSON) through an explicit format flag and MUST honor it non-interactively; text
output MUST carry no ANSI or color codes when stdout is not a TTY. On a TTY,
display human-friendly tables with colors.

What that flag selects depends on what the command is, which is what
`output_kind` declares:

- On a **`data`** command it selects the format of the document on stdout. This
  is the case the rest of this principle is written for, and it is the default.
- On a **`stream`** it selects the framing of the records. `ndjson` is JSON's
  spelling here: one object per line is what makes a structured stream readable
  before it ends.
- On an **`opaque`** command it selects nothing on success. Stdout is the
  artifact, its structure is its `media_type`, and re-encoding a tarball as JSON
  would destroy the thing the consumer asked for. `-o json` is accepted and
  changes no byte of a successful invocation.

The flag governs **failure output on all three**. That is the part no kind is
exempt from: an opaque command that cannot find its artifact writes the error
envelope to stderr exactly like any other command, and exits with the code its
error kind declares. Structured stdout is scoped by output kind; structured
failure is universal.

Tools SHOULD auto-detect with `isatty()` and emit structured output when piped.
That default makes the first un-flagged invocation machine-readable for a naive
consumer, and it is the expected behavior for new tools. A tool with an
established human-readable default MAY instead keep emitting text when piped
rather than break the scripts and editors that parse it (see Adopting the Spec),
but it MUST then declare that default in the schema's top-level `output` field so
a consumer reads the contract instead of inferring it by testing:
`"output": {"tty": "text", "piped": "text"}`. Declaring `"piped": "json"` marks a
tool as safe to pipe blindly.

The format flag is three-valued: its default (`auto`) selects the format by TTY
detection, and an explicit value always wins. `mytool -o text list | grep web`
must produce text, not JSON. Do not make the human format the flag's default
value: that makes "explicitly chose text" indistinguishable from "did not
choose", and the explicit choice gets silently overridden when piped.

`--output`/`-o` is the canonical spelling. If a tool's `--output` or `-o` is
already bound by an existing contract (the destination-path convention of curl
and most compilers), `--format` is an acceptable alternative; repurposing the
existing flag would break the contract the spec tells you to protect. Whichever
flag the tool uses must be declared in the schema's `global_args` so consumers
discover it instead of guessing. Consumers place global arguments before the
command path (`mytool -o json list`), which works consistently across common CLI
frameworks; tools MAY additionally accept them after the command path.

JSON is the default structured format due to universal tooling support. But JSON
is not always optimal: Markdown and YAML use significantly fewer tokens for LLM
consumption. Supporting multiple formats via `--output` lets consumers choose.

```bash
# DO: Auto-detect and support explicit format selection
$ mytool list
NAME        STATUS    UPTIME
web-01      running   14d 3h
db-01       stopped   -

$ mytool -o json list
{"items": [{"name": "web-01", "status": "running", "uptime_seconds": 1220400},
           {"name": "db-01", "status": "stopped", "uptime_seconds": null}]}

$ mytool list | jq '.items[] | select(.status == "running")'
# Works, because JSON is emitted automatically when piped
```

Commands returning a collection wrap their results in an `{"items": [...]}`
envelope rather than emitting a bare array. The envelope is what gives pagination
and truncation metadata a place to live (see Principle 6), and it means the
output shape never changes when metadata is added. A `single` command returns its
document directly; it has nothing to wrap.

A `stream` command emits one record per line with no envelope, flushed as each
record becomes available. A stream that buffers until the end is a `data`
command with extra steps, and it defeats the reason to consume it incrementally.

An `opaque` command writes exactly the bytes of its artifact, nothing else. No
envelope, no trailing newline that is not part of the artifact, no progress
noise (Principle 3 already required that, and it matters most here, where the
consumer may be piping into `tar`).

#### Errors

On failure, exit non-zero with the exit code declared for that error kind, and
report the failure in the format the consumer selected.

**The exit code is the part that is always machine-readable.** It costs nothing
to check, needs no parsing, and is available even when stderr was discarded.
Every error kind declares one in the schema, so a consumer can branch before
reading a byte of output.

**The envelope is emitted whenever the selected output format is structured.**
That is: an explicit `-o json`, or the tool's declared piped default resolving to
a structured format. Write it as a single line of JSON, as the **last line of
stderr**. Principle 3 allows progress messages on stderr, so the fixed position
is what keeps the error mechanically extractable.

```json
{"error": {"kind": "auth", "message": "Token expired for profile 'staging'",
           "hint": "Run 'mytool login --profile staging' to refresh."}}
```

- **`kind`** (required) - stable identifier from the finite set declared in the
  schema. Consumers branch on it without parsing the message.
- **`message`** (required) - human-readable description.
- **`hint`** (optional) - actionable remediation. `retryable` in the schema tells
  a consumer whether to retry; `hint` tells it what to do instead.
- **`details`** (optional) - object with structured, kind-specific context.

Additional keys are permitted. `exit_code` and `retryable` are common: they
duplicate what the schema and the process status already say, which is useful
when the envelope is captured away from either.

**In text mode, write a human error message.** A person who runs a command in
their terminal and gets a line of JSON has been handed the machine's copy by
mistake. v0.2 required the envelope unconditionally, and the tools that
implemented it faithfully print JSON at a human on a TTY. That was a mistake in
v0.2. The contract a machine actually needs is the exit code plus the declared
kind-to-code mapping, and both survive text mode intact; a consumer that wants
the envelope asks for it with the format flag it was already going to pass.

```bash
# DO: format the error the way the caller asked for output
$ mytool connect --profile staging
Error: token expired for profile 'staging'.
Run 'mytool login --profile staging' to refresh.
# exit code: 3

$ mytool -o json connect --profile staging
# stderr: {"error": {"kind": "auth", "message": "Token expired for profile 'staging'"}}
# exit code: 3
```

```bash
# DON'T: prose-only errors with no declared code
$ mytool connect --profile staging
Error: something went wrong    # what kind? exit 1? the consumer has nothing
```

Commands MAY narrow the set with a per-command `errors` array naming the kinds
that command can actually emit. It is a subset of the top-level declarations, and
it is how a consumer writes an exhaustive handler without preparing for every
failure the whole tool can have.

#### Outcomes

Not every non-zero exit is an error. Diff-like commands exit non-zero to report a
data state: `diff` and `grep` exit 1 for "there is a difference" or "no match", a
dependency checker exits 1 for "updates exist". These are **outcomes**, not
failures. An outcome exit writes no error envelope; stdout carries the result.
Declare them in the schema's `outcomes` array so consumers can branch on the exit
code without misclassifying a data state as a failure:

```json
"outcomes": [
  {"code": 1, "name": "drift_found",
   "description": "Drift was detected; the report is on stdout. Not an error."}
]
```

No exit code may be claimed by both an error kind and an outcome, and no two
outcomes may share one: that would make the code unreadable, which is the one
thing it exists for. Two error kinds MAY share a code. It is coarse, and the
structured `kind` still separates them.

### 2. Schema Introspection

Provide a `schema` command that outputs a machine-readable description of the
tool's capabilities. Agents should never need to parse `--help` text to discover
what a tool can do.

A conformant schema document MUST validate against
[`https://clispec.dev/schema/v0.3.json`](schema/v0.3.json). Unknown properties are
permitted at every level, so tools can attach their own metadata without breaking
conformance; `extensions` and an `x-` prefix are the recommended namespaces.

```json
{
  "clispec": "0.3",
  "name": "mytool",
  "version": "2.3.0",
  "output": {"tty": "text", "piped": "json"},
  "global_args": [
    {"name": "--output", "short": "-o", "type": "string",
     "enum": ["auto", "text", "json", "yaml"], "default": "auto",
     "description": "Output format; auto detects TTY"}
  ],
  "commands": [
    {
      "name": "services list",
      "description": "List services in the active environment.",
      "effects": "read_only",
      "cardinality": "unbounded",
      "pagination": {"style": "cursor", "cursor_field": "next_cursor",
                     "cursor_arg": "--cursor", "limit_arg": "--limit"},
      "fields_arg": "--fields",
      "args": [
        {"name": "--limit", "type": "integer", "default": 100},
        {"name": "--fields", "type": "string[]"},
        {"name": "--cursor", "type": "string"}
      ],
      "output_fields": [
        {"name": "name", "type": "string"},
        {"name": "status", "type": "string", "enum": ["running", "stopped"]},
        {"name": "uptime_seconds", "type": "integer", "nullable": true},
        {"name": "tags", "type": "array", "items": {"type": "string"}}
      ],
      "errors": ["auth", "rate_limit"]
    }
  ],
  "errors": [
    {"kind": "auth", "exit_code": 3, "retryable": false,
     "description": "Authentication failed"},
    {"kind": "rate_limit", "exit_code": 8, "retryable": true,
     "description": "Too many requests"}
  ]
}
```

#### Commands are a flat list

`commands` is flat. `name` carries the complete space-separated path
(`"list"`, `"services list"`, `"files download"`). There is no nesting, no
`subcommands`, and no `command_layout` discriminator: both keys are retired in
v0.3 and their presence is an error rather than an ignored extension, because a
consumer reading only the flat list would silently miss every nested command.

Two things follow, and both are the point:

- **Every entry is invocable.** v0.2's nested layout had group entries that
  existed only to route to children, so a consumer could not tell "a command that
  does something" from "a word you type on the way to one". In v0.3 a group is
  simply not in the list.
- **A command path is a value, not a traversal.** Matching a command is a string
  comparison.

#### Output shape

`output_fields` describes one record: for a collection, one element of `items`,
not the envelope. Types come from a closed set (`string`, `integer`, `number`,
`boolean`, `object`, `array`) with `nullable` as a separate boolean, and arrays
declare their element type in `items`. v0.2 allowed free-form type strings, which
produced `"integer | null"`, `"integer|null"`, `"?integer"` and `"object[]"`
across four tools for the same two ideas, none of which a consumer could parse
reliably.

```json
{"name": "uptime_seconds", "type": "integer", "nullable": true}
{"name": "tags", "type": "array", "items": {"type": "string"}}
{"name": "owner", "type": "object", "nullable": true,
 "fields": [{"name": "team", "type": "string"}]}
```

When the real shape is nested or conditional enough that this cannot express it,
add `stdout_schema`: a JSON Schema (draft 2020-12) for the complete stdout
document, envelope included. Pair it with `output_fields` when a compact summary
remains accurate, because this document gets read into an agent's context and a
full JSON Schema per command is not cheap. It may stand alone when no fixed field
summary can be true.

Every `data` command MUST describe its output with one of them. A command that
describes neither leaves a consumer to learn the shape by running it, which is
the discovery-by-invocation this principle exists to remove. A few commands
honestly cannot answer in advance: a projector emits whatever the caller's own
paths selected, a passthrough emits what it was handed. Those say so out loud
with the empty schema, which accepts any document:

```json
{"name": "project", "description": "Project a JSON document down to the paths given.",
 "effects": "read_only", "cardinality": "single", "stdout_schema": {}}
```

`{}` is a declaration; silence is not. The schema rejects silence while still
allowing the honest answer for these commands: "the shape depends on your
input."

Argument types stay free-form (`"path"`, `"duration"`, `"url"`), because an
argument's type is a hint for constructing a call while an output field's type
drives parsing. A repeatable flag declares an array type (`"string[]"`); there is
no separate `variadic` or `repeatable` key.

#### The schema command's own contract

The `schema` command must work **before anything else does**: no authentication,
no configuration file, no network. An agent reaches for the schema precisely when
it knows nothing about the tool, often before setup has happened or after it has
failed. A schema command that requires credentials is unavailable in exactly the
situation it exists for.

The root `--help` output must mention the `schema` command. `--help` is the
universal first probe; it is how a consumer discovers that a machine-readable
contract exists at all.

The schema command is not exempt from Bounded Output (Principle 6). For tools
with many commands, a full dump is thousands of tokens of mostly irrelevant
detail. `schema` SHOULD accept a command path that narrows the document to a
subtree: `mytool schema services list` emits the same top-level shape (`name`,
`version`, `global_args`, `errors`) with `commands` filtered to matching paths.
Top-level metadata stays included because a consumer needs it regardless of which
command it is about to run.

For context that a schema cannot capture (workflows, security boundaries,
operational guidance) ship companion files alongside your tool (`CONTEXT.md`,
`SKILL.md`, or `AGENTS.md`).

#### Interop with CLI description formats

The clispec schema is the agent-facing contract: output shape, error kinds, retry
semantics. It is not a replacement for richer description formats designed for
documentation and shell-completion generation:

- [OpenCLI](https://opencli.org/) - OpenAPI-style description of an entire CLI surface (JSON/YAML).
- [usage](https://usage.jdx.dev/) - KDL format used by mise to generate man pages, completions, and Fig specs from a single source.
- [Fig autocomplete spec](https://fig.io/docs/getting-started/first-completion-spec) - TypeScript-based completion definitions.
- [carapace-spec](https://github.com/carapace-sh/carapace-spec) - multi-shell completion spec with bridges from clap, cobra, click, and others.

Tools are encouraged to emit one of these in addition to the clispec schema.
clispec sits one level up: it mandates that the agent-facing contract exists,
while leaving man pages, completions, and IDE integration to formats purpose-built
for them.

### 3. Stderr/Stdout Separation

Data goes to stdout. Messages, progress indicators, and diagnostics go to stderr.
Never mix human-readable messages into the data stream.

This applies in every output mode and to every output kind. An agent piping your
output to `jq` should never get a progress message in the JSON, and an agent
piping your artifact to `tar` should never get one in the archive.

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

All commands must work without a TTY. Interactive prompts should only appear when
stdin is a terminal. Provide flag alternatives for every interactive input.

An agent cannot type "y" at a confirmation prompt. If your CLI blocks waiting for
input that will never come, the agent is stuck.

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

Not blocking is half the rule; the other half is which way to fall. A command
that would ask for confirmation on a TTY must, without a TTY, **refuse and exit
non-zero** with a structured error naming the bypass flag. It must never proceed
silently: an agent that hallucinates and retries should hit a wall, not a trigger.
Use the `confirmation_required` error kind, with the `hint` naming the flag.

Declare the flag in the schema as `confirmation_bypass_arg`. Its presence says
this command prompts on a TTY and refuses without one; its absence says the
command never prompts. That is a contract in both directions, which is why the
absent case is not "unknown".

```bash
# DO: Fail safe without a TTY
$ mytool delete vm-01 < /dev/null
# stderr: {"error": {"kind": "confirmation_required",
#          "message": "Deleting vm-01 requires confirmation",
#          "hint": "Re-run with --yes to confirm."}}
# exit code: 6
```

```bash
# DON'T: Treat a missing TTY as consent
$ mytool delete vm-01 < /dev/null
Deleted vm-01   # the confirmation prompt was the only safeguard, and it vanished
```

A few commands are inherently interactive: a shell, an editor, a pager, a console
session. They cannot be made to work without a terminal, and pretending otherwise
helps nobody. Declare `"requires_tty": true` and fail the same safe way: refuse,
emit the `tty_required` error kind, exit non-zero. What the principle forbids is
hanging, not needing a terminal.

The refusal rule applies only to commands that would actually prompt. A command
that has never prompted needs no bypass flag, and adding a non-TTY refusal to it
is a breaking change for every script that already runs it unattended, not a
compliance step. A tool with no interactive prompts satisfies this principle
as-is. Reserve new confirmation gates for genuinely destructive or irreversible
operations, and introduce them as deliberate design decisions, never as part of a
mechanical compliance pass.

For destructive operations, consider supporting `--dry-run` with structured
output so consumers can preview changes before committing.

### 5. Safe Retries

Agents retry. They lose track of state. They run the same command twice because a
previous step timed out. What a consumer needs is not a promise that every
command is idempotent, which no real tool can make, but an accurate statement of
what re-running does and a safe move when the answer is "do not".

Every command declares `effects`. There is no default, because a consumer
granting trust (auto-approving a command, retrying it automatically) needs an
explicit claim rather than an absent one.

**`read_only`** commands do not modify state a consumer cares about. This is the
claim that buys the most trust: permission systems auto-approve on it, agents
cache and parallelize on it. Read-only means read-only. A command that writes a
cache entry as a side effect of answering is still read-only; one that rotates a
credential to answer is not.

**`idempotent`** commands may modify state, and repeating them converges to the
same state. Starting an already-running service exits 0. Creating a resource that
already exists with identical configuration is a no-op. If your CLI returns an
error on a repeat operation, the agent will try to fix a problem that does not
exist.

Structured output of a mutating command SHOULD include a `changed` boolean (the
Terraform and Ansible convention): `true` when the command did work, `false` when
the requested state was already in place. Exit 0 says the state is right;
`changed` says whether this invocation did anything, and a consumer needs that
distinction without parsing prose, for example to decide whether dependent
services must be restarted.

When a resource exists with a different configuration than requested, return an
error with the `conflict` kind. Do not silently overwrite, and do not silently
ignore the difference.

```bash
# DO: Idempotent by default
$ mytool start web-01
Started web-01

$ mytool start web-01
web-01 is already running   # exit code 0

$ mytool -o json start web-01
{"name": "web-01", "status": "running", "changed": false}   # exit code 0

# DO: Detect conflicts
$ mytool -o json create db-01 --memory 4GB
# stderr: {"error": {"kind": "conflict", "message": "db-01 exists with memory=8GB"}}
```

**`non_idempotent`** commands cause an additional effect each time. Sending a
notification, creating a deployment, appending a ledger entry. Declaring this is
conformant. Claiming idempotency you do not have is not, and it is worse than
declaring the truth, because the consumer retries on the strength of the claim.

For these, give the consumer a safe move: an argument that accepts a
caller-supplied key, declared as `idempotency_key_arg`, so a repeat with the same
key returns the original result instead of doing the work again. Without one, a
consumer whose call times out has no way to find out whether the effect happened,
and both choices available to it are wrong.

```json
{"name": "deploys create", "description": "Start a new deployment.",
 "effects": "non_idempotent", "idempotency_key_arg": "--request-id",
 "cardinality": "single",
 "output_fields": [{"name": "id", "type": "string"},
                   {"name": "status", "type": "string"}],
 "args": [{"name": "--request-id", "type": "string"}]}
```

The v0.2 `mutating` boolean is deprecated. It conflated "changes something" with
"unsafe to repeat", which are different questions with different consumers: a
permission system asks the first, a retry loop asks the second. It is still
accepted for continuity, and when both are present they must agree.

### 6. Bounded Output

Agents have finite context windows. A command that dumps 10,000 records as a
single JSON blob drowns the signal and burns the budget. But a command that
returns one record, or the three environments you configured, does not need
pagination, and demanding it produces a `--limit` flag that exists only to pass
a checklist.

`cardinality` decides what is required:

- **`single`** - nothing to bound. One record, or one document.
- **`bounded`** - the size follows from the caller's own input or a small closed
  domain: three environments, the ports you asked about, the files you named. No
  pagination required. Add `fields_arg` if the record is wide.
- **`unbounded`** - the collection can grow without limit. `pagination` and
  `fields_arg` are **required**, and the pagination style must be `cursor` or
  `offset`. `"style": "none"` on an unbounded command is a contradiction, not a
  declaration.

```json
{"name": "services list", "description": "List services in the active environment.",
 "effects": "read_only", "cardinality": "unbounded",
 "pagination": {"style": "cursor", "cursor_field": "next_cursor",
                "cursor_arg": "--cursor", "limit_arg": "--limit"},
 "fields_arg": "--fields",
 "output_fields": [{"name": "name", "type": "string"},
                   {"name": "status", "type": "string"}],
 "args": [{"name": "--limit", "type": "integer"},
          {"name": "--cursor", "type": "string"},
          {"name": "--fields", "type": "string[]"}]}
```

`pagination` declares the arguments the consumer drives it with, not only the
style. A `cursor` style names `cursor_field` (where the token appears in the
envelope), `cursor_arg` (the argument that carries it back) and `limit_arg`. An
`offset` style names `offset_arg` and `limit_arg`. All of them are required,
because paging a consumer cannot actually perform is not paging: a token with no
argument to pass it to is a page nothing can reach, and a page size the caller
cannot set is a page size the caller has to accept. Declaring the style alone
describes a capability that exists only in the schema.

Every argument named in `pagination`, `fields_arg`, `confirmation_bypass_arg` or
`idempotency_key_arg` must actually be declared in that command's `args` or in
`global_args`. A name that resolves to nothing is a flag that does not exist, and
it is the single easiest way to produce a schema that looks compliant and is not.

When output is bounded, **say so in-band**. The envelope must carry enough
metadata (`total`, a cursor, or an explicit `truncated: true`) for the consumer
to know it received a partial result. Silent truncation is the worst failure
mode: an agent that gets 100 silently-truncated rows will confidently report that
100 is all there is.

```bash
# DO: Pagination and field selection
$ mytool -o json services list --limit 10
{"items": [...], "total": 1847, "next_cursor": "eyJvIjoxMH0"}

$ mytool -o json services list --fields name,status --limit 10
{"items": [{"name": "web-01", "status": "running"}], "total": 1847,
 "next_cursor": "eyJvIjoxMH0"}
```

```bash
# DON'T: Unbounded output
$ mytool -o json services list
# Returns a 50KB+ JSON array, with no way to page or reduce it,
# and no signal that the output is partial
```

**Streams** are bounded by the consumer, not the producer: it reads until it has
enough and closes the pipe. That works only if records are flushed as they are
produced, which is what `output_kind: "stream"` promises. Time and count bounds
(`--since`, `--tail`) are the natural controls here, not offsets.

**Opaque** commands emit an artifact whose size is what it is. There is nothing
to page, and nothing to select. This is an exemption from Principle 6, and it is
the reason `output_kind` has to be declared rather than guessed.

---

## General Guidance

These recommendations apply broadly but are not principles in their own right.

**Validate inputs strictly.** Agents hallucinate plausible-but-wrong inputs.
Reject path traversals, control characters, and malformed data. Use allowlists
over denylists. Your CLI should never pass unsanitized input to a shell.

**Use consistent command structure.** The noun-verb pattern
(`mytool resource action`) makes command discovery a tree search rather than a
guessing game. Keep flag names consistent across subcommands.

**Document stability.** If agents depend on your structured output, field removal
is a breaking change. Declare per-command `stability` where it varies, and
document which parts of your output are stable.

**Never accept secrets via argv.** A `--token abc123` argument is visible in
`ps`, lands in shell history, and ends up verbatim in agent transcripts, which
may be logged, summarized, or shared. Accept secrets via stdin
(`--password-stdin`), environment variables, or the OS keychain.

**Offer NDJSON for large or streaming output.** One JSON object per line streams
incrementally, composes with `--limit`, and degrades gracefully: `head -n 20` of
NDJSON is 20 valid records, while a truncated JSON array is unparseable. Expose
it as `-o ndjson`.

**Emit no ANSI escapes when stdout is not a TTY, and respect `NO_COLOR`.** This
holds for a structured piped default and for text output alike: text requested
explicitly via `-o text`, or a declared legacy text default, must also be free of
color codes when piped.

**Make long-running operations observable.** A command that is silent for minutes
gets killed and retried (another reason Principle 5 matters). Report progress on
stderr, support `--timeout` where applicable, and for genuinely long jobs prefer
an async pattern: a start command that returns a job ID, and a status command to
poll.

**Be safe under concurrency.** Agents parallelize aggressively; assume two
invocations of your tool run at the same time. Use atomic writes and lock files
for shared state such as config and caches.

---

## Adopting the Spec in an Existing CLI

Most tools that adopt this spec are not greenfield. They have users, scripts, and
an existing flag surface, and that surface is itself a contract. Compliance work
must not break it.

- **Keep legacy format flags working.** If the tool had `--json`, keep it as a
  hidden alias for `--output json`. If `--format` was the selector, keep it as an
  alias of `--output`. Removing a flag that scripts already pass is a breaking
  change dressed up as cleanup.
- **An established piped-output default is a contract too, so declare it.** If
  piped output has always been human-readable text, as with most linters,
  formatters, and version-control tools, you MAY keep that default rather than
  flip it to JSON and break every script and editor that parses it. Declare it in
  the schema's top-level `output` field (`"piped": "text"`) so machine consumers
  discover the behavior instead of inferring it.
- **Never narrow accepted values.** If the format flag accepted `table` or
  `plain`, keep accepting them (mapping to the text format) even when the
  documented set becomes `auto`, `text`, `json`.
- **Watch for flag collisions.** Some CLIs already use `--output` or `-o` for a
  destination path (the curl and compiler convention). That existing meaning must
  keep working exactly as before; resolving the collision without breaking either
  contract takes deliberate design, not a mechanical rename.
- **Add, don't move.** New flags, error kinds, and envelope fields are additive.
  Renaming or removing anything existing is a versioning decision separate from
  spec adoption.
- **Existing exit codes and prompt behavior are contracts too.** Do not add
  confirmation gates to commands that never prompted (see Principle 4), and do
  not change documented exit semantics; declare them in the schema instead.
- **Declare, don't retrofit.** Where v0.3 asks a question v0.2 never asked
  (`effects`, `cardinality`, `output_kind`), the answer is a description of the
  command you already have. If describing it honestly makes the command fail a
  capability check, that is information, not a defect to paper over with a flag
  that does nothing.

---

## Migrating from v0.2

A v0.2 document does not validate as v0.3. The changes, and what each costs:

| Change | What to do |
|---|---|
| `clispec` is now required and must be `"0.3"` | Set it. |
| `commands` is flat; `subcommands` and `command_layout` are retired | Join each path with spaces into `name`. Drop entries that only routed to children. |
| `effects` is required on every command | `mutating: false` becomes `read_only`. `mutating: true` becomes `idempotent` or `non_idempotent`, which only you can decide. |
| `cardinality` is required on `data` commands | Say whether the command returns one record, a caller-bounded collection, or an unbounded one. |
| `exit_code` is required on every error kind | Declare the code each kind exits with. |
| Field types are a closed set with a separate `nullable` | `"integer \| null"` becomes `{"type": "integer", "nullable": true}`; `"string[]"` becomes `{"type": "array", "items": {"type": "string"}}`. |
| `--limit`/`--offset`/`--fields` are required only on `unbounded` commands | Most commands need nothing. Unbounded ones need real pagination, declared down to the arguments that drive it. |

The repository ships a converter that does the mechanical part:

```bash
make convert FILE=schema-v0.2.json OUT=schema-v0.3.json
```

It flattens command paths, rewrites field types, adds dashes to short flags,
converts error-shaped outcomes, and maps `mutating: false` to `read_only`. Keys
it does not recognise are copied through untouched: v0.2 permitted extra metadata
and v0.3 still does, so dropping one would be the converter quietly editing your
published contract.

It does **not** guess `cardinality`, decide whether a mutating command is
idempotent, or turn an undocumented output into `stdout_schema: {}`. All three are
claims a consumer will act on, and a tool that invents them produces a document
that validates and lies. The converter lists every such decision as a review item
and exits non-zero while any remain.

A group entry is dropped rather than published as a command, unless it declared
`output_fields` or an `example` of its own. Those are evidence that invoking the
group does something; `args` is not, because a flag declared on a group is how
clap, cobra and click all spell "every child accepts this". Each dropped group is
a review item naming what to do if it was in fact invocable, since the flat list
means a consumer will call anything left in it.

---

## Conformance

Conformance has two layers. **Core** applies to every tool. **Capability** rules
apply only to commands that declare themselves subject to them, which is why the
declarations are mandatory.

### Core

- [ ] Provides structured output (JSON) through an explicit format flag declared
      in `global_args` (canonically `--output`/`-o`, or `--format` where
      `--output` is already bound by an existing contract), accepts that global
      flag before the command path, and honors it non-interactively; an explicit
      format always wins over TTY detection, and text output carries no ANSI when
      stdout is not a TTY. Structured output SHOULD be the default when piped; a
      tool that keeps a human-readable default MAY do so if it declares that
      default in the top-level `output` field. The flag selects the format of a
      `data` command's document, a `stream`'s framing, and every command's error
      envelope; an `opaque` command's stdout is its artifact either way.
      *(Principle 1)*
- [ ] On failure, exits with the code declared for the error kind, and writes the
      structured error envelope as the last line of stderr whenever the selected
      output format is structured. *(Principle 1)*
- [ ] Every error kind declares an `exit_code`. No code is claimed by both an
      error and an outcome, and no two outcomes share one. *(Principle 1)*
- [ ] Exposes a `schema` subcommand whose output validates against
      [`clispec.dev/schema/v0.3.json`](schema/v0.3.json). *(Principle 2)*
- [ ] `commands` is a flat list of invocable commands; `name` is the full
      space-separated path. *(Principle 2)*
- [ ] Every command declares `description` and `effects`. *(Principles 2, 5)*
- [ ] Every argument referenced by `pagination`, `fields_arg`,
      `confirmation_bypass_arg` or `idempotency_key_arg` is declared in that
      command's `args` or in `global_args`. *(Principles 4, 5, 6)*
- [ ] `schema` succeeds with no authentication, no configuration file, and no
      network access. *(Principle 2)*
- [ ] Root `--help` output mentions the `schema` subcommand. *(Principle 2)*
- [ ] Writes data to stdout and diagnostics to stderr in every output mode and
      for every output kind. *(Principle 3)*
- [ ] Runs to completion without a TTY, or refuses with a declared error kind and
      a non-zero exit. Never hangs. *(Principle 4)*

### Capability

- [ ] Every `data` command declares `cardinality`, and describes its output with
      `output_fields`, `stdout_schema`, or both. `{}` is the explicit way to say
      the shape follows the caller's input. *(Principles 2, 6)*
- [ ] Every `unbounded` command declares `pagination` with a `cursor` or `offset`
      style **and the arguments that drive it** (`cursor_field` + `cursor_arg` +
      `limit_arg`, or `offset_arg` + `limit_arg`), plus a `fields_arg`, and
      carries truncation metadata in-band. *(Principle 6)*
- [ ] Every command declares only the keys its `output_kind` defines:
      `cardinality` and `pagination` on `data`, `stream_format` on `stream`,
      `media_type` on `opaque`. *(Principle 1)*
- [ ] Every `opaque` command declares `media_type` and emits nothing but the
      artifact, while still reporting failure with the envelope and the declared
      exit code. *(Principles 1, 3)*
- [ ] Every `stream` command emits one record per line, flushed as produced.
      *(Principle 1)*
- [ ] Every `idempotent` command exits zero when the requested state is already
      in place, and emits `conflict` when it is in place differently.
      *(Principle 5)*
- [ ] Every `non_idempotent` command declares an `idempotency_key_arg`, or
      documents why a retry cannot be made safe. *(Principle 5)*
- [ ] Every command that prompts on a TTY declares `confirmation_bypass_arg`,
      refuses without a TTY, and emits `confirmation_required`. *(Principle 4)*
- [ ] Every `requires_tty` command emits `tty_required` rather than hanging.
      *(Principle 4)*

### Checking it

The repository is the normative reference. Validate a schema document, including
the rules JSON Schema cannot express:

```bash
make check FILE=path/to/schema.json          # errors and advisory warnings
make check FILE=path/to/schema.json STRICT=1 # warnings become failures
```

The [`clispec`](https://github.com/rvben/clispec-cli) tool scores a binary on your
`$PATH` against the runtime half of the checklist:

```bash
cargo install clispec
clispec score mytool
```

### The badge

Tools that follow the spec can wear the clispec badge, linking back to
clispec.dev:

[![clispec compliant](https://img.shields.io/badge/clispec-compliant-3b82f6)](https://clispec.dev)

```markdown
[![clispec compliant](https://img.shields.io/badge/clispec-compliant-3b82f6)](https://clispec.dev)
```

Compliance is a claim, so make it a true one: run the checks above and confirm
they pass before adding the badge.

---

## Reference Implementations

These tools are designed around these principles:

[proxctl](https://github.com/rvben/proxctl) ·
[unifi-cli](https://github.com/rvben/unifi-cli) ·
[vership](https://github.com/rvben/vership) ·
[confluence-cli](https://github.com/rvben/confluence-cli) ·
[tidemark](https://github.com/rvben/tidemark) ·
[upd](https://github.com/rvben/upd) ·
[dotpick](https://github.com/rvben/dotpick) ·
[whatport](https://github.com/rvben/whatport) ·
[downstat](https://github.com/rvben/downstat) ·
[clihatch](https://github.com/rvben/clihatch)

tidemark is a fully offline tool (every runtime check passes under the scorer);
upd demonstrates the `outcomes` array with its diff-like exit-1-on-updates
contract; dotpick, whatport, and downstat are stateless, agent-facing tools
(projection, port inspection, multi-registry download stats) with structured
output and clispec schemas; clihatch scaffolds new tools that are clispec-compliant
out of the box.

For a full list, see the [homebrew tap](https://github.com/rvben/homebrew-tap).
For registries of agent-friendly CLI tools, see [clime.sh](https://clime.sh) and
[CLI-Anything Hub](https://clianything.cc).

---

## Related Work

- [You Need to Rewrite Your CLI for AI Agents](https://justin.poehnelt.com/posts/rewrite-your-cli-for-ai-agents/) - Justin Poehnelt
- [Writing CLI Tools That AI Agents Actually Want to Use](https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no) - DEV Community
- [One CLI, Two Audiences](https://www.checklyhq.com/blog/agentic-cli/) - Checkly
- [Command Line Interface Guidelines](https://clig.dev/) - clig.dev

---

## Contributing

This is a living specification. Contributions welcome.

- Open an [issue](https://github.com/rvben/clispec/issues) to discuss changes
- Submit a pull request for spec improvements
- To list your tool as a reference implementation, open a PR

---

## License

[CC BY 4.0](https://github.com/rvben/clispec/blob/main/LICENSE) - Copyright 2026 Ruben Jongejan
