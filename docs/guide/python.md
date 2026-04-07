# Python (click)

Guidance for implementing The CLI Spec in Python using [click](https://click.palletsprojects.com/).

---

## Structured Output (Principle 1)

```python
import sys
import json
import click

def is_json(ctx):
    return ctx.obj.get("output") == "json" or not sys.stdout.isatty()

@click.group()
@click.option("--output", "-o", default="text",
              type=click.Choice(["text", "json"]),
              help="Output format")
@click.pass_context
def cli(ctx, output):
    ctx.ensure_object(dict)
    ctx.obj["output"] = output if output != "text" or sys.stdout.isatty() else "json"

@cli.command()
@click.pass_context
def list_items(ctx):
    items = fetch_items()
    if ctx.obj["output"] == "json":
        click.echo(json.dumps(items))
    else:
        for item in items:
            click.echo(f"{item['name']:<20} {item['status']}")
```

Errors go to stderr with a `kind` field:

```python
import sys

def emit_error(kind: str, message: str):
    error = {"error": {"kind": kind, "message": message}}
    print(json.dumps(error), file=sys.stderr)
    sys.exit(1)
```

---

## Schema Introspection (Principle 2)

Walk click's command tree to auto-generate a schema:

```python
import json
import importlib.metadata
import click

def generate_schema(group):
    def walk(cmd):
        info = {
            "name": cmd.name,
            "description": cmd.get_short_help_str(),
        }
        args = []
        for param in cmd.params:
            if param.name in ("help",):
                continue
            arg = {
                "name": f"--{param.name}",
                "required": param.required,
                "type": param.type.name,
            }
            if param.default is not None:
                arg["default"] = param.default
            args.append(arg)
        if args:
            info["args"] = args
        if isinstance(cmd, click.Group):
            subs = [walk(c) for name, c in sorted(cmd.commands.items())]
            if subs:
                info["subcommands"] = subs
        return info

    return {
        "name": group.name,
        "version": importlib.metadata.version(group.name),
        "commands": [walk(c) for name, c in sorted(group.commands.items())],
    }

@cli.command()
def schema():
    """Output JSON schema for agent integration."""
    click.echo(json.dumps(generate_schema(cli), indent=2))
```

---

## Non-Interactive (Principle 4)

```python
import sys

@cli.command()
@click.option("--token", help="API token")
def init(token):
    if sys.stdin.isatty() and not token:
        token = click.prompt("API token", hide_input=True)
    elif not token:
        raise click.UsageError("--token required in non-interactive mode")

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
