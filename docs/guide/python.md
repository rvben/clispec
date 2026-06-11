# Python (click)

Guidance for implementing The CLI Spec in Python using [click](https://click.palletsprojects.com/).

---

## Structured Output (Principle 1)

Default the flag to `auto` so TTY detection only applies when the user did not choose a format. An explicit `-o text` must produce text even when piped (defaulting to `"text"` makes the explicit choice indistinguishable from the default, so it gets overridden):

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

@cli.command()
@click.pass_context
def list_items(ctx):
    items = fetch_items()
    if ctx.obj["output"] == "json":
        click.echo(json.dumps({"items": items}))
    else:
        for item in items:
            click.echo(f"{item['name']:<20} {item['status']}")
```

Errors exit non-zero, with the envelope as the last line of stderr:

```python
import sys

def emit_error(kind: str, message: str, hint: str | None = None, exit_code: int = 1):
    error = {"kind": kind, "message": message}
    if hint:
        error["hint"] = hint
    print(json.dumps({"error": error}), file=sys.stderr)
    sys.exit(exit_code)
```

---

## Schema Introspection (Principle 2)

Walk click's command tree to auto-generate a schema:

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
    return arg

def generate_schema(group):
    def walk(cmd):
        info = {
            "name": cmd.name,
            "description": cmd.get_short_help_str(),
        }
        if args := [param_to_arg(p) for p in cmd.params]:
            info["args"] = args
        if isinstance(cmd, click.Group):
            if subs := [walk(c) for name, c in sorted(cmd.commands.items())]:
                info["subcommands"] = subs
        return info

    return {
        "clispec": "0.2",
        "name": group.name,
        # The distribution name is not always the command name; pass it explicitly.
        "version": importlib.metadata.version("mytool-dist"),
        "global_args": [param_to_arg(p) for p in group.params],
        "commands": [walk(c) for name, c in sorted(group.commands.items())],
    }

@cli.command()
def schema():
    """Output JSON schema for agent integration."""
    click.echo(json.dumps(generate_schema(cli), indent=2))
```

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
        raise click.UsageError(
            "set MYTOOL_TOKEN or pass --token-stdin in non-interactive mode")

    # validate and save
```

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

---

## Recommended Packages

| Package | Purpose |
|---------|---------|
| [click](https://pypi.org/project/click/) | CLI framework |
| [rich](https://pypi.org/project/rich/) | Colored terminal output and tables |
| [httpx](https://pypi.org/project/httpx/) | HTTP client (async-capable) |
| [pydantic](https://pypi.org/project/pydantic/) | Data validation and serialization |
| [keyring](https://pypi.org/project/keyring/) | OS credential storage |
