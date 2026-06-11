# Verifying Compliance

Test your tool against The CLI Spec with these checks.

---

## Quick Check

```bash
# Principle 1: Structured output
mytool list -o json | jq . > /dev/null         # Valid JSON
mytool list | jq . > /dev/null                 # Auto-JSON when piped
mytool list -o text | grep -qv '^{'            # Explicit format wins over TTY detection
mytool bad-command 2>&1 >/dev/null | tail -n1 | jq .error.kind
                                               # Error envelope is the last line of stderr

# Principle 2: Schema
mytool schema | jq .commands                   # Has commands
mytool schema | jq .errors                     # Has error kinds (with exit codes)
mytool schema | jq .global_args                # Has global flags
HOME=$(mktemp -d) mytool schema > /dev/null    # Works without config or auth
mytool --help | grep -q schema                 # Discoverable from --help
mytool schema list | jq .commands              # Subtree filter (SHOULD, for large CLIs)

# Principle 3: Stderr/stdout separation
mytool list 2>/dev/null | jq . > /dev/null     # Clean stdout

# Principle 4: Non-interactive
MYTOOL_TOKEN=x timeout 5 mytool login </dev/null   # No hang without a TTY
mytool delete foo --yes 2>&1                   # No prompt
mytool delete foo </dev/null 2>&1 >/dev/null | tail -n1 | jq -e '.error.kind == "confirmation_required"'
                                               # Refuses without a TTY instead of proceeding

# Principle 5: Idempotent
mytool start foo; mytool start foo; echo $?    # Exit 0 both times
mytool start foo -o json | jq .changed         # No-op repeats report changed: false

# Principle 6: Bounded output
mytool list --limit 1 -o json | jq .total      # Truncation metadata in-band
mytool list --fields name -o json              # Field filtering
```

Validate the schema document against the published JSON Schema:

```bash
mytool schema > /tmp/schema.json
curl -fsSL https://clispec.dev/schema/v0.2.json > /tmp/clispec-v0.2.json
uvx check-jsonschema --schemafile /tmp/clispec-v0.2.json /tmp/schema.json
```

---

## Automated Linting

[cli-agent-lint](https://github.com/Camil-H/cli-agent-lint) scores your CLI on agent-readiness across structured output, schema discovery, input validation, and more.
