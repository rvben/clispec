# Implementation Guide

Practical guidance for implementing The CLI Spec in your CLI tool.

Choose your language:

- [**Rust** (clap)](rust.md) - with links to real, tested source code
- [**Go** (cobra)](go.md)
- [**Python** (click)](python.md)

Or jump to:

- [Verifying Compliance](verifying.md) - test your tool against the spec
- [CI/CD](cicd.md) - GitHub Actions and Makefile patterns

---

## The part your framework cannot generate

Most of the schema falls out of the CLI framework you already use: command
paths, flags, help text, defaults. Three things do not, and all three are
required in v0.3 because they are the claims a consumer acts on:

- **`effects`** - what re-running the command does.
- **`cardinality`** - how many records a data command can return.
- **`output_kind`** - whether stdout is a document, a stream, or an artifact.

Every language guide handles these the same way: keep the declarations in one
table keyed by the full command path, look them up while walking the command
tree, and add a test that fails when a command has no entry. Adding a subcommand
is exactly the moment the declarations go stale, and exactly the moment nobody is
thinking about the schema.

Coming from v0.2? The repository ships a converter for the mechanical part, and
it lists what it refuses to guess: see
[Migrating from v0.2](../index.md#migrating-from-v02).
