# Versions

A tool that declares `"clispec": "0.2"` is making a claim about a document that
someone else will check, possibly years later. That claim is only meaningful if
the thing being claimed holds still.

This page says which versions exist, which of them will never change again, and
what has to be true before a version stops moving.

## The published versions

| Version | Published | Status | Schema | Prose |
|---|---|---|---|---|
| 0.3 | - | **Candidate** | [`v0.3.json`](schema/v0.3.json) | [The Spec](index.md) |
| 0.2 | 2026-06-11 | **Frozen** 2026-08-13 | [`v0.2.json`](schema/v0.2.json) | [v0.2](spec/v0.2.md) |
| 0.1 | 2026-05-14 | **Frozen**, superseded by 0.2 | [`v0.1.json`](schema/v0.1.json) | in git history only |

The v0.1 prose was never archived to its own page: it was replaced in place when
v0.2 was published, and survives only in the repository history. That is the
mistake this page exists to stop repeating.

Every one of those files is listed with its SHA-256 in
[`CHECKSUMS.txt`](CHECKSUMS.txt), which is published at the site root so anyone
can verify what they fetched:

```bash
curl -sO https://clispec.dev/schema/v0.2.json
curl -s https://clispec.dev/CHECKSUMS.txt | grep v0.2.json
# frozen  f9a1f713...  schema/v0.2.json
shasum -a 256 v0.2.json
```

## What the two statuses mean

**Candidate.** The version is still being written. The schema and the prose can
change, including in ways that make a previously accepted document invalid.
Build against a candidate to experiment and to give feedback; do not put
`"clispec": "0.3"` in a released tool and expect it to keep validating.

**Frozen.** The bytes are final. The URL will serve exactly the document
recorded in `CHECKSUMS.txt` for as long as the site exists. A document that
validated against a frozen schema on the day it froze validates against it
forever, because it is the same schema.

Freezing is enforced, not merely declared: `make checksums` fails CI if a frozen
file's hash moves, and the tool that records checksums refuses to re-record a
frozen entry. Undoing a freeze requires editing the recorded hash by hand, which
is a visible, reviewable act rather than a side effect of some other change.

## Errata

An erratum is a correction to what a frozen version *says*, published here
rather than applied to the frozen page. It may fix a typo, resolve a
contradiction, or state what was already intended. It may not change what a
document has to contain in order to validate. If a correction would change that,
it is not an erratum, it is the next version.

None have been issued.

## Why this page exists

v0.2 was published on 11 June 2026 and then amended five times over the
following two months, each amendment made in good faith and each one only
widening what was allowed:

1. `--format` accepted as an alternative spelling where `--output` is already
   bound by an existing contract.
2. The optional `outcomes` array, for non-zero exits that report a data state.
3. The optional per-command `example` invocation.
4. The piped-output default relaxed, so a tool with an established
   human-readable default may keep it provided it declares that default.
5. The optional `command_layout` discriminator, making the flat and nested
   command encodings unambiguous.

Widening amendments break nothing, which is exactly what made them easy to keep
making. But the effect on a consumer is real: "validates against clispec 0.2"
meant five different things over those two months, and nothing in the document
said which one. A tool could pass a check in July that it would have failed in
June, with no version number anywhere to explain it.

Hence the split into candidate and frozen. A version stays a candidate for as
long as it needs to; once frozen it is done, and the next round of changes gets
its own number and its own URL.

## What v0.3 needs before it freezes

The candidate stops moving when all of these hold:

- **Every normative rule is pinned by a fixture**, and the mutation harness
  (`make mutate`) shows that removing any one rule turns the suite red. A rule
  no test can detect the absence of is not enforced, whatever the prose says.
- **At least three published tools emit conformant v0.3 documents**, converted
  from real v0.2 documents rather than written against the schema by hand.
  Conversion is what surfaces the rules that are impossible to satisfy.
- **No open proposal would change validation.** Editorial improvements can
  continue after the freeze as errata; anything that changes what validates
  cannot.
- **Thirty days with no change to what validates.** A quiet month is weak
  evidence, but it is evidence, and the alternative is freezing on the day the
  last idea happened to arrive.

Progress against these is tracked in the repository, not here, so that this page
stays a statement of policy rather than a status board.

## Declaring a version in a tool

The `clispec` field in a schema document names the version the document was
written against:

<!-- clispec-test: fragment -->
```json
{"clispec": "0.3", "name": "mytool", "version": "1.2.0"}
```

Declare the version you actually validate against, and validate against the
published bytes. In CI that means a vendored copy whose SHA-256 you check
against `CHECKSUMS.txt`, not a live fetch: the copy is then provably the
published document, and your build does not depend on someone else's site being
up. Against a candidate it is the only workable option, because the published
bytes still move. What none of this survives is a local copy nobody has ever
checked, which is a snapshot of a promise rather than the promise.
