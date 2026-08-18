# The CLI Spec

**6 principles for building CLI tools that work for humans, scripts, and AI agents.**

Read the spec at [clispec.dev](https://clispec.dev). Score any binary on your `$PATH` with [`clispec`](https://github.com/rvben/clispec-cli).

## Versions

| Version | Status | Schema |
|---|---|---|
| 0.3 | Candidate | [`clispec.dev/schema/v0.3.json`](https://clispec.dev/schema/v0.3.json) |
| 0.2 | Frozen | [`clispec.dev/schema/v0.2.json`](https://clispec.dev/schema/v0.2.json) |

A frozen version never changes again, and every published file is listed with
its SHA-256 in [`CHECKSUMS.txt`](https://clispec.dev/CHECKSUMS.txt) so anyone can
verify what they fetched. See [Versions](https://clispec.dev/versions/) for what
the two statuses mean and what v0.3 needs before it freezes.

## Checking your tool

```bash
git clone https://github.com/rvben/clispec && cd clispec
mytool schema > schema.json
make check FILE=schema.json            # errors, plus advisory warnings
make check FILE=schema.json STRICT=1   # warnings become failures
make convert FILE=old-v0.2.json        # v0.2 to v0.3, with an honest review list
```

`make check` runs the layers JSON Schema alone cannot: referential integrity,
exit-code ownership, and the conditionally required error kinds.

## Working on the spec

```bash
make test        # the normative fixture suite
make mutate      # proves the suite can fail, by breaking each rule in turn
make checksums   # verifies no frozen artifact has changed
make build       # build the site
```

CI runs these targets and nothing else, so a green pipeline is reproducible
locally. Everything runs through `uv run --with` at pinned versions, so there is
no install step to keep in sync.

## Reusable CI

The workflows in [`.github/workflows`](.github/workflows) provide two centralized
checks for CLI repositories:

- `conformance.yml` builds a Rust or uv-managed CLI and requires a clispec v0.3
  score of 24/24 (Excellent).
- `published-artifacts.yml` confirms that the latest GitHub release agrees with
  crates.io, PyPI, and Homebrew and that published files have valid metadata.

Pin them to a full commit SHA and name the release beside it, so a mutable ref
can never change CI code silently:

```yaml
uses: rvben/clispec/.github/workflows/conformance.yml@<40-char-sha> # v0.3.1
```

Both halves earn their place. The SHA is what runs. The comment is what lets an
update tool prove which release the old SHA stood for and propose the next one;
without it a bare SHA is unresolvable and stays frozen until someone transcribes
a new one by hand. Repository tags track the spec line they belong to and are
not spec publications in their own right, which
[Versions](https://clispec.dev/versions/) explains.

## Contributing

- Open an [issue](https://github.com/rvben/clispec/issues) to discuss changes
- Submit a pull request for spec improvements
- To list your tool as a reference implementation, open a PR

A change to a normative rule touches four places: the schema, a fixture in
`tests/fixtures/`, the mutation in `tests/mutate.py` that proves the fixture
bites, and the prose in `docs/`.

## License

[CC BY 4.0](LICENSE) - Copyright 2026 Ruben Jongejan
