# CI/CD

Standard CI/CD patterns for CLI tools following The CLI Spec.

---

## Keeping the contract in CI

A schema is generated code. It drifts the moment someone adds a subcommand, and
nothing in a normal test suite notices. Both halves of
[Verifying Compliance](verifying.md) belong in the pipeline: validate the
document, then probe the binary.

Make it a make target so it runs the same way on a laptop:

```makefile
SCHEMA_VERSION := v0.3
SCHEMA := vendor/clispec-$(SCHEMA_VERSION).json

schema-check: build            ## Validate the generated schema document
	./target/debug/mytool schema > target/schema.json
	uvx check-jsonschema --schemafile $(SCHEMA) target/schema.json

conformance: build             ## Probe the built binary against the runtime rules
	PATH="$(PWD)/target/debug:$$PATH" ./scripts/conformance.sh
```

**Vendor the schema rather than fetching it.** `curl`ing
`https://clispec.dev/schema/v0.3.json` on every run makes your build depend on
someone else's site, and while v0.3 is a candidate its bytes can change under
you. Commit the file, and record where it came from:

```bash
curl -fsSL https://clispec.dev/schema/v0.3.json -o vendor/clispec-v0.3.json
shasum -a 256 vendor/clispec-v0.3.json
# compare against the line for schema/v0.3.json in https://clispec.dev/CHECKSUMS.txt
```

Upgrading the vendored copy then becomes a commit you can read, review, and
revert, instead of a green build turning red on a morning when you changed
nothing.

The runtime half is a shell script of the probes from
[Verifying Compliance](verifying.md), driven by the tool's own schema so it only
checks what the tool claimed:

```bash
#!/usr/bin/env bash
set -euo pipefail

mytool schema | jq -e '.clispec == "0.3"' > /dev/null
mytool schema | jq -e '.commands | all(.[]; has("effects") and has("description"))' > /dev/null
mytool --help | grep -q schema

# The schema command works before anything else does
tmp_home=$(mktemp -d)
HOME="$tmp_home" mytool schema > /dev/null
rmdir "$tmp_home"

# The error kind and the exit code agree with what the schema declared.
# Capture the status with `|| status=$?`: piping a failing command into `jq`
# under `pipefail` aborts the script before the assertion runs, and the probe
# then reports nothing at all.
status=0
mytool -o json bad-command >/dev/null 2>/tmp/err || status=$?
kind=$(tail -n1 /tmp/err | jq -r '.error.kind')
want=$(mytool schema | jq -r --arg k "$kind" '.errors[] | select(.kind==$k) | .exit_code')
[ "$status" = "$want" ] || {
  echo "bad-command exited $status; schema declares $want for kind $kind" >&2
  exit 1
}
```

Run both in a job of their own so a failure names the thing that broke:

```yaml
  clispec:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v5
      - run: make schema-check
      - run: make conformance
```

Add the job to whatever gate requires all checks to pass, or it will fail
quietly and merge anyway.

---

## GitHub Actions

A standard CI workflow with lint, test, and coverage jobs:

**Working example:** [proxctl/.github/workflows/ci.yml](https://github.com/rvben/proxctl/blob/main/.github/workflows/ci.yml)

Key patterns:

- **Separate jobs** for lint (fmt + clippy) and test
- **Coverage** as an informational job (not a gate)
- **`all-checks-passed`** gate job that fails if any dependency failed or was cancelled
- **Concurrency control** to cancel in-progress runs on new pushes
- **`make` targets** in CI - the pipeline runs the same commands you run locally

---

## Makefile

Standard targets for spec-compliant CLI tools:

**Working example:** [proxctl/Makefile](https://github.com/rvben/proxctl/blob/main/Makefile)

Canonical targets:

| Target | Purpose |
|--------|---------|
| `build` | Debug build |
| `release` | Release build |
| `test` | Run tests |
| `lint` | Format check + clippy (or equivalent linter) |
| `fmt` | Auto-format |
| `schema-check` | Validate the generated schema document |
| `conformance` | Probe the built binary against the runtime rules |
| `check` | `lint` + `test` + `schema-check` + `conformance` |
| `clean` | Remove build artifacts |
| `install` | Install binary locally |

---

## Pre-commit Hooks

Use [prek](https://github.com/rvben/prek) for pre-commit hook management:

**Working example:** [proxctl/prek.toml](https://github.com/rvben/proxctl/blob/main/prek.toml)

Standard hooks:

- **Pre-commit:** trailing whitespace, end-of-file fixer, cargo fmt, cargo clippy
- **Pre-push:** cargo test

`schema-check` is a good pre-push hook and a bad pre-commit hook: it needs a
build, and a hook that takes a minute gets bypassed.
