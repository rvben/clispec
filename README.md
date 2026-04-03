# Agent-First CLI Design

**A specification for building CLI tools that work equally well for humans and AI agents.**

Most CLI tools were designed for human eyeballs: colored tables, prose error messages, interactive prompts. AI agents need structured output, predictable behavior, and machine-readable introspection. The solution is not to choose one audience over the other. It is to design for both simultaneously.

This is a living specification. Contributions welcome via [issues](https://github.com/rvben/agent-first-cli/issues) and pull requests.

---

## The 6 Principles

### 1. Structured Output

Every command must support machine-readable output. When stdout is a TTY, display human-friendly tables with colors. When piped or when `--json` is passed, emit structured data. Use `isatty()` to auto-detect.

JSON is the default structured format due to universal tooling support (`jq`, every programming language). However, JSON is not always optimal for LLM consumption — Markdown uses 34-38% fewer tokens, YAML offers better accuracy for nested data. Consider supporting `--format` for alternatives when your consumers are primarily LLMs rather than scripts.

On failure, emit a structured error to stderr with a machine-readable `kind` field and exit non-zero. The `kind` field gives consumers the semantics — the exit code just signals pass/fail.

```bash
# DO: Auto-detect output format
$ mytool list
NAME        STATUS    UPTIME
web-01      running   14d 3h
db-01       stopped   —

$ mytool list --json
[{"name": "web-01", "status": "running", "uptime_seconds": 1220400},
 {"name": "db-01", "status": "stopped", "uptime_seconds": null}]

$ mytool list | jq '.[] | select(.status == "running")'
# Works — JSON emitted automatically when piped
```

```bash
# DO: Structured errors with a kind field
$ mytool connect --profile staging 2>&1
{"ok": false, "error": {"kind": "auth", "message": "Token expired for profile 'staging'"}}

# Agents branch on kind, not exit codes:
#   "auth"      → refresh credentials
#   "not_found" → don't retry
#   "timeout"   → retry with backoff
```

```bash
# DON'T: Force agents to parse tables or prose errors
$ mytool list --format=table  # only option
$ mytool connect --profile staging
Error: something went wrong  # exit 1 — what kind of error?
```

### 2. Schema Introspection

Provide a `schema` command that outputs a machine-readable description of all commands, their arguments, types, and output fields. Agents should never need to parse `--help` text to discover a tool's capabilities.

For context that schema cannot capture — workflows, gotchas, security boundaries — ship companion files alongside your tool. `CONTEXT.md`, `SKILL.md`, or `AGENTS.md` give agents the operational knowledge they need without polluting `--help` output.

```bash
# DO: Machine-readable schema
$ mytool schema
{
  "name": "mytool",
  "version": "1.2.0",
  "commands": [
    {
      "name": "list",
      "description": "List all services",
      "args": [
        {"name": "--status", "type": "string", "enum": ["running", "stopped", "all"]}
      ]
    }
  ]
}
```

```bash
# DON'T: Rely on help text as the only interface
$ mytool list --help
Usage: mytool list [options]
  Lists services. Use --status to filter.
# Agents must regex-parse this to discover capabilities
```

### 3. Stderr/Stdout Separation

Data goes to stdout. Messages, progress indicators, and diagnostics go to stderr. Never mix human-readable messages into the data stream.

This applies in every output mode, not just `--json`. Even human-readable table data should go to stdout, and "No results found" messages to stderr. An agent piping your output to `jq` should never get a progress message in the JSON.

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

This is a hard requirement, not a convenience. An agent cannot type "y" at a confirmation prompt. If your CLI hangs waiting for input that will never come, the agent's workflow is dead.

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
Password: ^C  # Hangs waiting for input that will never come
```

### 5. Idempotent Operations

Commands that can be safely re-run should be. Starting an already-running service returns success, not an error. Creating a resource that already exists with identical configuration is a no-op.

Agents retry. They lose track of state. They run the same command twice because a previous step timed out. Idempotency makes all of this safe. If your CLI returns an error on a repeat operation, the agent will try to "fix" a problem that does not exist.

```bash
# DO: Idempotent by default
$ mytool start web-01
Started web-01

$ mytool start web-01
web-01 is already running   # exit code 0, not an error
```

```bash
# DON'T: Error on repeat operations
$ mytool start web-01
Error: web-01 is already running   # exit code 1 — agent thinks it failed
```

### 6. Predictable Mutations

Destructive commands must support `--dry-run` with structured output showing the proposed changes. The output should be a machine-readable diff, not prose.

Agents are not trusted operators. They hallucinate plausible-but-wrong inputs — path traversals, embedded query parameters, double-encoded strings, control characters. Your CLI is the last line of defense. Validate all inputs strictly. Reject what does not conform rather than guessing what was intended.

```bash
# DO: Structured dry-run preview
$ mytool deploy web-api --env production --dry-run
{
  "action": "deploy",
  "changes": [
    {"field": "image", "from": "v1.2.0", "to": "v1.3.0"},
    {"field": "replicas", "from": 2, "to": 2}
  ],
  "destructive": false
}

# DO: Reject hallucinated inputs
$ mytool get "vm-01; rm -rf /"
Error: invalid resource name — contains disallowed characters
```

```bash
# DON'T: Prose-only dry run
$ mytool deploy web-api --env production --dry-run
Would deploy web-api to production with the new image.
# Agent can't parse what actually changes

# DON'T: Trust agent input
$ mytool get "../../etc/passwd"
# Should reject, not attempt to resolve
```

---

## Reference Implementations

The following tools implement all 6 principles:

[proxctl](https://github.com/rvben/proxctl) ·
[unifi-cli](https://github.com/rvben/unifi-cli) ·
[vership](https://github.com/rvben/vership) ·
[jira-cli](https://github.com/rvben/jira-cli) ·
[confluence-cli](https://github.com/rvben/confluence-cli) ·
[homeassistant-cli](https://github.com/rvben/homeassistant-cli) ·
[zoom-cli](https://github.com/rvben/zoom-cli) ·
[n8nc](https://github.com/rvben/n8nc) ·
[qnap-cli](https://github.com/rvben/qnap-cli) ·
[yuki-cli](https://github.com/rvben/yuki-cli) ·
[verg](https://github.com/rvben/verg)

For registries of agent-friendly CLI tools, see [clime.sh](https://clime.sh) and [CLI-Anything Hub](https://clianything.cc).

---

## Related Work

- [You Need to Rewrite Your CLI for AI Agents](https://justin.poehnelt.com/posts/rewrite-your-cli-for-ai-agents/) — Justin Poehnelt
- [Writing CLI Tools That AI Agents Actually Want to Use](https://dev.to/uenyioha/writing-cli-tools-that-ai-agents-actually-want-to-use-39no) — DEV Community
- [One CLI, Two Audiences](https://www.checklyhq.com/blog/agentic-cli/) — Checkly
- [Command Line Interface Guidelines](https://clig.dev/) — clig.dev

---

## Contributing

- Open an [issue](https://github.com/rvben/agent-first-cli/issues) to discuss changes to the spec
- Submit a pull request for spec improvements
- To list your tool as a reference implementation, open a PR adding it to the list above

---

## License

[CC BY 4.0](LICENSE) — Copyright 2026 Ruben Jongejan
