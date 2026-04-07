# Verifying Compliance

Test your tool against The CLI Spec with these checks.

---

## Quick Check

```bash
# Principle 1: Structured output
mytool list -o json | jq . > /dev/null         # Valid JSON
mytool list | jq . > /dev/null                 # Auto-JSON when piped
mytool bad-command 2>&1 | jq .error.kind       # Structured errors

# Principle 2: Schema
mytool schema | jq .commands                   # Has commands
mytool schema | jq .errors                     # Has error kinds

# Principle 3: Stderr/stdout separation
mytool list 2>/dev/null | jq . > /dev/null     # Clean stdout

# Principle 4: Non-interactive
echo "" | mytool login --token x 2>&1          # No hang
mytool delete foo --yes 2>&1                   # No prompt

# Principle 5: Idempotent
mytool start foo; mytool start foo; echo $?    # Exit 0 both times

# Principle 6: Bounded output
mytool list --limit 1 --json | jq .total       # Pagination metadata
mytool list --fields name --json               # Field filtering
```

---

## Automated Linting

[cli-agent-lint](https://github.com/Camil-H/cli-agent-lint) scores your CLI on agent-readiness across structured output, schema discovery, input validation, and more.
