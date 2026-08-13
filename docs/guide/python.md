# Python (click)

Guidance for implementing The CLI Spec in Python using [click](https://click.palletsprojects.com/).

---

## Structured Output (Principle 1)

Default the flag to `auto` so TTY detection only applies when the user did not choose a format. An explicit `mytool -o text list` must produce text even when piped (defaulting to `"text"` makes the explicit choice indistinguishable from the default, so it gets overridden).

Click group options belong before the subcommand, which matches clispec's portable placement for entries in `global_args`:

```python
import sys
import json
import click

@click.group()
@click.option("--output", "-o", default="auto",
              type=click.Choice(["auto", "text", "json"]),
              help="Output format; auto detects TTY")
@click.pass_context
def cli(ctx, output):
    ctx.ensure_object(dict)
    if output == "auto":
        output = "text" if sys.stdout.isatty() else "json"
    ctx.obj["output"] = output

@cli.command("list")
@click.pass_context
def list_items(ctx):
    items = fetch_items()
    if ctx.obj["output"] == "json":
        click.echo(json.dumps({"items": items}))
    else:
        for item in items:
            click.echo(f"{item['name']:<20} {item['status']}")
```

### Errors

Keep the kind-to-exit-code mapping in one table and generate the schema's `errors` array from it, so the declared contract and the process status cannot drift apart:

```python
ERRORS = {
    "usage": (2, False),
    "auth": (3, False),
    "not_found": (4, False),
    "conflict": (5, False),
    "confirmation_required": (6, False),
    "configuration": (7, False),
    "rate_limit": (8, True),
    "internal": (1, False),
}

class CliSpecError(Exception):
    def __init__(self, kind, message, *, hint=None):
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.hint = hint
        self.exit_code = ERRORS[kind][0]
```

Errors exit non-zero and are reported in the format the caller selected. In text mode a human gets a human message; the exit code is what stays machine-readable in both modes. Run Click with `standalone_mode=False` so its prose usage errors pass through the same adapter as application failures:

```python
import sys

def emit_error(exc: CliSpecError, output: str):
    if output == "text":
        print(f"Error: {exc.message}", file=sys.stderr)
        if exc.hint:
            print(exc.hint, file=sys.stderr)
    else:
        error = {"kind": exc.kind, "message": exc.message}
        if exc.hint:
            error["hint"] = exc.hint
        print(json.dumps({"error": error}), file=sys.stderr)
    sys.exit(exc.exit_code)

def main():
    # Resolve the format before invoking the group: a parsing failure has to be
    # reported in the format the caller asked for, and it is the first error
    # most consumers hit.
    output = resolve_format(sys.argv[1:])
    try:
        cli(standalone_mode=False)
    except CliSpecError as exc:
        emit_error(exc, output)
    except click.ClickException as exc:
        emit_error(CliSpecError("usage", exc.format_message()), output)
    except Exception:
        emit_error(CliSpecError("internal", "Unexpected internal error"), output)

if __name__ == "__main__":
    main()
```

Mapping `click.ClickException` to `usage` means an unknown flag exits 2 with a declared kind, rather than Click's own exit code and a prose-only message.

---

## Declaring what each command is

click knows the command tree and the parameters. It does not know whether re-running a command is safe or whether a list can grow without limit. `effects`, `cardinality` and `output_kind` are claims about behavior, so keep them beside the commands and look them up while walking. Keying by the full path makes the table read like the command list itself:

```python
DECLARED = {
    "services list":  {"effects": "read_only", "cardinality": "unbounded"},
    "services start": {"effects": "idempotent", "cardinality": "single"},
    "deploys create": {"effects": "non_idempotent", "cardinality": "single"},
    "logs tail":      {"effects": "read_only", "output_kind": "stream"},
    "completions":    {"effects": "read_only", "output_kind": "opaque",
                       "media_type": "text/x-shellscript"},
}
```

A decorator is the alternative, and it keeps the claim next to the function:

```python
def declares(**claims):
    def wrap(cmd):
        cmd.clispec = claims
        return cmd
    return wrap

@cli.command("list")
@declares(effects="read_only", cardinality="unbounded")
def list_items():
    ...
```

Either way, make an undeclared command a test failure rather than a schema that quietly omits a required key:

```python
def test_every_command_is_declared():
    for path in command_paths(cli):
        assert path in DECLARED, f"command {path!r} has no entry in DECLARED"
```

---

## Schema Introspection (Principle 2)

Walk click's command tree to generate the schema. v0.3 wants a **flat** list where `name` is the complete space-separated path, and where every entry is invocable: a group that only routes to children is not an entry.

Options render as `--flags`; `click.Argument` params are positional and keep their bare name. Flags registered on the group go into the top-level `global_args` array so agents discover `--output` and friends:

```python
import json
import importlib.metadata
import click

def param_to_arg(param):
    is_flag = isinstance(param, click.Option)
    # param.opts keeps the declared spellings ("--config-file", "-c");
    # param.name is normalized to config_file, which is not a valid flag.
    arg = {
        "name": max(param.opts, key=len) if is_flag else param.name,
        "required": param.required,
        "type": param.type.name,
    }
    if param.default is not None:
        arg["default"] = param.default
    if isinstance(param.type, click.Choice):
        arg["enum"] = list(param.type.choices)
    if short := next((o for o in param.opts if len(o) == 2 and o[0] == "-"), None):
        arg["short"] = short   # written with its dash, e.g. "-o"
    return arg

def generate_schema(group):
    def walk(cmd, prefix):
        path = f"{prefix} {cmd.name}".strip()
        commands = []
        # A group routes to its children and cannot be run, so it is not an
        # entry. Only a click.Group with no callback of its own is skipped.
        if not isinstance(cmd, click.Group):
            info = {
                "name": path,
                "description": cmd.get_short_help_str(),
                **DECLARED[path],
            }
            if args := [param_to_arg(p) for p in cmd.params]:
                info["args"] = args
            commands.append(info)
        else:
            for _, child in sorted(cmd.commands.items()):
                commands.extend(walk(child, path))
        return commands

    return {
        "clispec": "0.3",
        "name": group.name,
        # The distribution name is not always the command name; pass it explicitly.
        "version": importlib.metadata.version("mytool-dist"),
        "output": {"tty": "text", "piped": "json"},
        "global_args": [param_to_arg(p) for p in group.params],
        "commands": [c for _, child in sorted(group.commands.items())
                     for c in walk(child, "")],
        "errors": [
            {"kind": kind, "exit_code": code, "retryable": retryable}
            for kind, (code, retryable) in ERRORS.items()
        ],
    }

@cli.command()
def schema():
    """Output JSON schema for agent integration."""
    click.echo(json.dumps(generate_schema(cli), indent=2))
```

`get_short_help_str()` is the description, and v0.3 requires it. A command whose docstring is empty produces a document that does not validate, which is the right outcome: the description is the first thing an agent reads when choosing between commands.

Output field types are a closed set in v0.3 (`string`, `integer`, `number`, `boolean`, `object`, `array`), with `nullable` as a separate boolean and array element types in `items`. If your results are pydantic models, derive `output_fields` from the model rather than hand-writing type strings: `int | None` is `{"type": "integer", "nullable": true}` and `list[str]` is `{"type": "array", "items": {"type": "string"}}`.

Validate the generated document in `pytest` so a schema that stops conforming fails your own suite. See [Verifying Compliance](verifying.md).

---

## Non-Interactive (Principle 4)

Secrets never travel via argv (see General Guidance in the spec): prompt on a TTY, read stdin or an environment variable otherwise.

```python
import os
import sys

@cli.command()
@click.option("--token-stdin", is_flag=True, help="Read API token from stdin")
def init(token_stdin):
    if token_stdin:
        token = sys.stdin.readline().strip()
    elif os.environ.get("MYTOOL_TOKEN"):
        token = os.environ["MYTOOL_TOKEN"]
    elif sys.stdin.isatty():
        token = click.prompt("API token", hide_input=True)
    else:
        raise CliSpecError(
            "configuration",
            "API token required in non-interactive mode",
            hint="Set MYTOOL_TOKEN or pass --token-stdin.",
        )

    # validate and save
```

A command that would ask for confirmation refuses without a TTY instead of proceeding, and names the bypass flag in the hint. `click.confirm(abort=True)` is not enough on its own: without a TTY it raises where a consumer expected a declared kind.

```python
@cli.command()
@click.argument("name")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt")
def delete(name, yes):
    if not yes:
        if not sys.stdin.isatty():
            raise CliSpecError(
                "confirmation_required",
                f"Deleting {name} requires confirmation",
                hint="Re-run with --yes to confirm.",
            )
        click.confirm(f"Delete {name}?", abort=True)
    # ...
```

Declare that flag as the command's `confirmation_bypass_arg`. It already appears in `args`, because click reports it as a parameter.

---

## Shell Completions

click has built-in completion support. Users activate it via environment variables:

```bash
# bash
eval "$(_MYTOOL_COMPLETE=bash_source mytool)"

# zsh
eval "$(_MYTOOL_COMPLETE=zsh_source mytool)"

# fish
_MYTOOL_COMPLETE=fish_source mytool | source
```

To provide a `completions` command that outputs the script directly:

```python
import os

@cli.command()
@click.argument("shell", type=click.Choice(["bash", "zsh", "fish"]))
@click.pass_context
def completions(ctx, shell):
    """Generate shell completions."""
    prog_name = ctx.find_root().info_name  # e.g. "mytool", not cli.name
    env_var = f"_{prog_name.upper()}_COMPLETE"
    os.environ[env_var] = f"{shell}_source"
    cli.main(args=[], prog_name=prog_name, standalone_mode=False)
```

The output is a script, not a document, so declare the command `"output_kind": "opaque"` with `"media_type": "text/x-shellscript"`. That exempts its stdout from the structured-output and bounded-output rules and nothing else: `-o json` still cannot reformat a shell script, and a failure still writes the error envelope to stderr and exits with the code its kind declares.

---

## Recommended Packages

| Package | Purpose |
|---------|---------|
| [click](https://pypi.org/project/click/) | CLI framework |
| [rich](https://pypi.org/project/rich/) | Colored terminal output and tables |
| [httpx](https://pypi.org/project/httpx/) | HTTP client (async-capable) |
| [pydantic](https://pypi.org/project/pydantic/) | Data validation and serialization |
| [keyring](https://pypi.org/project/keyring/) | OS credential storage |
