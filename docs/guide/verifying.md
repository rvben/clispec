# Verifying Compliance

Two halves, and both are needed.

**The document** says what your tool claims. Checking it is cheap, exact, and
catches the things a runtime probe cannot see: a `fields_arg` naming a flag that
does not exist, an exit code claimed by both an error and an outcome, an
`unbounded` command with no way to page it.

**The tool** has to actually behave that way. No amount of schema validation
proves that `--yes` is honored or that stdout is clean.

---

## Validating the document

Against the published schema:

```bash
mytool schema > /tmp/schema.json
curl -fsSL https://clispec.dev/schema/v0.3.json > /tmp/clispec-v0.3.json
uvx check-jsonschema --schemafile /tmp/clispec-v0.3.json /tmp/schema.json
```

That covers everything JSON Schema can express, which is most but not all of the
contract. Referential integrity (does `fields_arg` name a real argument?), exit
code ownership, and the conditionally required error kinds need the checker in
the [spec repository](https://github.com/rvben/clispec):

```bash
git clone https://github.com/rvben/clispec && cd clispec
make check FILE=/tmp/schema.json            # errors, plus advisory warnings
make check FILE=/tmp/schema.json STRICT=1   # warnings become failures
```

It reports three layers separately: JSON Schema errors, conformance rules that
JSON Schema cannot express, and lints. Lints are advisory by design. A command
with 14 output fields and no `--fields` flag is worth mentioning and is not a
violation, and a checker that cannot tell the difference gets ignored.

A `data` command must declare `output_fields`, `stdout_schema`, or both, because
otherwise a consumer has to run it to learn what it returns. If your command
genuinely cannot say in advance, because it projects or passes through whatever
it was given, declare `"stdout_schema": {}`. That accepts any document while
still making the difference between an answer and silence explicit.

Coming from v0.2? `make convert FILE=old.json` does the mechanical part and lists
what it refuses to guess. See [Migrating from v0.2](../index.md#migrating-from-v02).

---

## Probing the tool

These assume a tool with the commands used in the spec's examples. Adapt the
names; the shape of each check is the part that transfers.

```bash
# Principle 1: Structured output
mytool -o json services list | jq -e . > /dev/null   # Explicit JSON is valid
mytool -o text services list | grep -qv '^{'          # Explicit format wins

# Unflagged piped output must match the declared default. A missing declaration
# means JSON; tools retaining an established text default must declare "text".
piped=$(mytool schema | jq -er '.output.piped // "json"')
if [ "$piped" = "json" ]; then
    mytool services list | jq -e . > /dev/null
elif [ "$piped" = "text" ]; then
    ! mytool services list | LC_ALL=C grep -q "$(printf '\033')"
fi

# The structured error envelope is the last line of stderr, in structured mode
mytool -o json bad-command 2>&1 >/dev/null | tail -n1 \
  | jq -e '.error.kind | type == "string"'

# The exit code matches what the schema declares for that kind
kind=$(mytool -o json bad-command 2>&1 >/dev/null | tail -n1 | jq -r '.error.kind')
want=$(mytool schema | jq -r --arg k "$kind" '.errors[] | select(.kind==$k) | .exit_code')
mytool -o json bad-command >/dev/null 2>&1; [ "$?" = "$want" ]

# Principle 2: Schema
mytool schema | jq -e '.clispec == "0.3"'
mytool schema | jq -e '.commands | type == "array" and length > 0'
mytool schema | jq -e '.commands | all(.[]; has("effects") and has("description"))'
mytool schema | jq -e '.commands | all(.[]; has("subcommands") | not)'   # Flat layout
mytool schema | jq -e '.errors | all(.[]; (.kind|type=="string") and (.exit_code|type=="number"))'
mytool schema | jq -e '.global_args | any(.name == "--output" or .name == "--format")'
tmp_home=$(mktemp -d)
HOME="$tmp_home" mytool schema > /dev/null    # Works without config or auth
rmdir "$tmp_home"                             # Schema wrote no configuration
mytool --help | grep -q schema                 # Discoverable from --help
mytool schema services list | jq -e '.commands | type == "array"'
                                               # Subtree filter (SHOULD, for large CLIs)

# Principle 3: Stderr/stdout separation
mytool -o json services list 2>/dev/null | jq -e . > /dev/null

# An opaque command emits its artifact and nothing else, and the format flag
# cannot reformat an artifact: -o json changes no byte of a successful run
mytool completions bash | head -c 2 | grep -q '#!'
mytool -o json completions bash | head -c 2 | grep -q '#!'

# Exempt from JSON on stdout is not exempt from JSON on stderr
mytool -o json artifacts download nope 2>&1 >/dev/null | tail -n1 \
  | jq -e '.error.kind | type == "string"'

# Principle 4: Non-interactive
MYTOOL_TOKEN=x timeout 5 mytool login </dev/null       # No hang without a TTY
mytool -o json services delete foo </dev/null 2>&1 >/dev/null | tail -n1 \
  | jq -e '.error.kind == "confirmation_required"'     # Refuses, does not proceed
timeout 5 mytool console foo </dev/null 2>&1 >/dev/null | tail -n1 \
  | jq -e '.error.kind == "tty_required"'              # requires_tty fails, never hangs

# Principle 5: Safe retries
mytool services start foo; mytool services start foo; echo $?   # Exit 0 both times
mytool -o json services start foo | jq -e '.changed == false'   # No-op reports it

# Principle 6: Bounded output, only where cardinality says so
mytool -o json services list --limit 1 \
  | jq -e 'has("total") or has("next_cursor") or has("truncated")'
mytool -o json services list --fields name \
  | jq -e '.items | all(.[]; keys == ["name"])'
```

### Do not probe what the tool did not claim

The point of `cardinality` and `output_kind` is that a check applies only where
it belongs. Drive the probes from the document:

```bash
# Only unbounded commands owe you pagination. Use the limit argument and cursor
# field each command declares; neither spelling is required to be conventional.
mytool schema | jq -r '.commands[] | select(.cardinality == "unbounded")
                       | [.name, .pagination.limit_arg,
                          (.pagination.cursor_field // "")]
                       | @tsv' \
  | while IFS=$(printf '\t') read -r cmd limit_arg cursor_field; do
      read -r -a cmd_parts <<< "$cmd"
      mytool -o json "${cmd_parts[@]}" "$limit_arg" 1 \
        | jq -e --arg cursor "$cursor_field" \
            'has("total") or has("truncated") or ($cursor != "" and has($cursor))'
    done

# A declared cursor must round-trip. The checker can only prove the argument
# exists; that the tool accepts the token it just handed out is a runtime fact.
mytool schema | jq -r '.commands[] | select(.pagination.style == "cursor")
                       | [.name, .pagination.cursor_field, .pagination.cursor_arg,
                          .pagination.limit_arg]
                       | @tsv' \
  | while IFS=$(printf '\t') read -r cmd field cursor_arg limit_arg; do
      read -r -a cmd_parts <<< "$cmd"
      token=$(mytool -o json "${cmd_parts[@]}" "$limit_arg" 1 \
                | jq -r --arg f "$field" '.[$f] // empty')
      [ -z "$token" ] || \
        mytool -o json "${cmd_parts[@]}" "$limit_arg" 1 "$cursor_arg" "$token" \
          | jq -e 'has("items")'
    done
```

A `--limit` flag on a command that returns one record is not compliance, it is a
flag that does nothing. v0.2 asked for those, and v0.3 exists partly to stop
asking.

---

## In CI

Both halves belong in your pipeline: see [CI/CD](cicd.md).

Third-party option: [cli-agent-lint](https://github.com/Camil-H/cli-agent-lint)
scores a CLI on agent-readiness across structured output, schema discovery, input
validation, and more.
