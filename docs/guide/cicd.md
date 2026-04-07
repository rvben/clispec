# CI/CD

Standard CI/CD patterns for CLI tools following The CLI Spec.

---

## GitHub Actions

A standard CI workflow with lint, test, and coverage jobs:

**Working example:** [proxctl/.github/workflows/ci.yml](https://github.com/rvben/proxctl/blob/main/.github/workflows/ci.yml)

Key patterns:

- **Separate jobs** for lint (fmt + clippy) and test
- **Coverage** as an informational job (not a gate)
- **`all-checks-passed`** gate job that fails if any dependency failed or was cancelled
- **Concurrency control** to cancel in-progress runs on new pushes
- **`make` targets** in CI — the pipeline runs the same commands you run locally

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
| `check` | `lint` + `test` |
| `clean` | Remove build artifacts |
| `install` | Install binary locally |

---

## Pre-commit Hooks

Use [prek](https://github.com/rvben/prek) for pre-commit hook management:

**Working example:** [proxctl/prek.toml](https://github.com/rvben/proxctl/blob/main/prek.toml)

Standard hooks:

- **Pre-commit:** trailing whitespace, end-of-file fixer, cargo fmt, cargo clippy
- **Pre-push:** cargo test
