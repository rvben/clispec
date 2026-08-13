#!/usr/bin/env python3
"""Run the normative fixture suite for The CLI Spec v0.3.

Three fixture directories, three contracts:

  valid/    Must produce no errors and no warnings. A conformant document is
            not merely accepted, it is clean.
  invalid/  Must produce at least one error, from the layer the fixture names
            in `x-test.detect`, and *only* at the location it names in
            `x-test.at`. A fixture that fails somewhere else is failing for the
            wrong reason and is a broken fixture, not a passing test.
  lint/     Must produce no errors and at least one warning whose rule matches
            `x-test.expects`. These pin the findings that are deliberately
            advisory rather than disqualifying.

The suite also runs its own controls, because a harness that reports uniform
results is more likely to be broken than to be right: it proves the validator
accepts a known-good document and rejects a known-bad mutation of it before
trusting any per-fixture verdict.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import clispec_convert as cc  # noqa: E402
import clispec_validate as cv  # noqa: E402

FIXTURES = REPO_ROOT / "tests" / "fixtures"

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""


class Suite:
    def __init__(self):
        self.passed = 0
        self.failures: list[str] = []

    def check(self, name: str, ok: bool, detail: str = ""):
        if ok:
            self.passed += 1
            print(f"  {GREEN}ok{RESET}   {name}")
        else:
            self.failures.append(f"{name}: {detail}")
            print(f"  {RED}FAIL{RESET} {name}")
            if detail:
                for line in detail.splitlines():
                    print(f"         {DIM}{line}{RESET}")


def controls(suite: Suite, schema: dict):
    """Prove the validator can both accept and reject before trusting it."""
    print("controls")

    # Every version the site serves, not only the one under test. A frozen
    # schema is a URL other people's tooling fetches, so it has to keep loading
    # long after anyone here has a reason to open it.
    for path in sorted((REPO_ROOT / "docs" / "schema").glob("v*.json")):
        try:
            cv.jsonschema.Draft202012Validator.check_schema(json.loads(path.read_text()))
            suite.check(f"{path.name} is a valid draft 2020-12 schema", True)
        except (cv.jsonschema.SchemaError, json.JSONDecodeError) as exc:
            suite.check(f"{path.name} is a valid draft 2020-12 schema", False, str(exc))
            if path.name == cv.DEFAULT_SCHEMA.name:
                return

    baseline = json.loads((FIXTURES / "valid" / "minimal.json").read_text())
    findings = cv.validate(baseline, schema)
    suite.check(
        "positive control: the minimal document validates clean",
        not findings,
        "\n".join(str(f) for f in findings),
    )

    mutated = json.loads(json.dumps(baseline))
    del mutated["clispec"]
    suite.check(
        "negative control: dropping a required key is rejected",
        any(f.severity == "error" for f in cv.validate(mutated, schema)),
        "the validator accepted a document it must reject; the suite below proves nothing",
    )

    # JSON can be valid without being an object. The checker must report that
    # structural error instead of passing the value into object-shaped lints.
    try:
        findings = cv.validate([], schema)
    except Exception as exc:  # pragma: no cover - the assertion reports it
        suite.check(
            "negative control: a non-object document is reported, not crashed",
            False,
            f"validator raised {type(exc).__name__}: {exc}",
        )
    else:
        suite.check(
            "negative control: a non-object document is reported, not crashed",
            any(f.severity == "error" for f in findings),
            "the validator accepted [] or returned no useful error",
        )

    mutated = json.loads(json.dumps(baseline))
    mutated["commands"][0]["cardinality"] = "unbounded"
    suite.check(
        "negative control: an unbounded command without pagination is rejected",
        any(f.severity == "error" for f in cv.validate(mutated, schema)),
        "the narrowed Principle 6 rule is not being enforced",
    )


# The set of required keys, stated here rather than read out of the schema.
#
# A sweep over `schema["required"]` would test whatever the schema happens to
# say and would therefore survive the removal of any entry: delete a key from
# the schema and the sweep simply stops checking it. For a version that is
# meant to freeze, the suite has to hold the contract independently. Adding an
# entry here is as breaking as removing one, so the sets are compared exactly.
REQUIRED_KEYS = {
    "": ["clispec", "name", "version", "commands", "errors"],
    "commands[]": ["name", "description", "effects"],
    "errors[]": ["kind", "exit_code"],
}

_REQUIRED_AT = {
    "": (lambda s: s, lambda d: d),
    "commands[]": (lambda s: s["$defs"]["command"], lambda d: d["commands"][0]),
    "errors[]": (lambda s: s["$defs"]["error"], lambda d: d["errors"][0]),
}


def run_required_keys(suite: Suite, schema: dict):
    """Every required key, declared here, then deleted one at a time."""
    print("\nrequired keys  (the declared set, each deleted individually)")
    baseline = json.loads((FIXTURES / "valid" / "minimal.json").read_text())

    for scope, expected in REQUIRED_KEYS.items():
        subschema, locate = _REQUIRED_AT[scope]
        declared = sorted(subschema(schema).get("required", []))
        label = f"{scope or 'document'}: required set is exactly {expected}"
        suite.check(label, declared == sorted(expected), f"schema says {declared}")

        for name in expected:
            mutated = json.loads(json.dumps(baseline))
            target = locate(mutated)
            if name not in target:
                suite.check(
                    f"{scope}{'.' if scope else ''}{name}",
                    False,
                    "minimal.json does not carry this key, so the check is vacuous",
                )
                continue
            del target[name]
            errors = [f for f in cv.validate(mutated, schema) if f.severity == "error"]
            suite.check(
                f"{scope}{'.' if scope else ''}{name}",
                bool(errors),
                "accepted a document missing a required key",
            )


def _meta(doc, path: Path) -> dict:
    meta = doc.get("x-test")
    if not isinstance(meta, dict):
        raise SystemExit(f"{path}: fixture has no x-test block describing what it pins")
    return meta


def run_valid(suite: Suite, schema: dict):
    print("\nvalid/  (conformant and clean)")
    for path in sorted((FIXTURES / "valid").glob("*.json")):
        doc = json.loads(path.read_text())
        _meta(doc, path)
        findings = cv.validate(doc, schema)
        suite.check(
            path.name,
            not findings,
            "\n".join(str(f) for f in findings),
        )


def run_invalid(suite: Suite, schema: dict):
    print("\ninvalid/  (rejected, for the stated reason, in the stated place)")
    messages: list[tuple[str, str]] = []
    for path in sorted((FIXTURES / "invalid").glob("*.json")):
        doc = json.loads(path.read_text())
        meta = _meta(doc, path)
        detect = meta.get("detect")
        violates = meta.get("violates")
        at = meta.get("at")
        if detect not in ("schema", "rules") or not violates or at is None:
            suite.check(path.name, False, "x-test needs detect, violates and at")
            continue

        findings = cv.validate(doc, schema)
        errors = [f for f in findings if f.severity == "error"]
        messages += [(path.name, f.message) for f in errors]

        if not errors:
            suite.check(path.name, False, "accepted a document that must be rejected")
            continue

        want_rule = "json-schema" if detect == "schema" else violates
        wrong_layer = [f for f in errors if (f.rule == "json-schema") != (detect == "schema")]
        if wrong_layer:
            suite.check(
                path.name,
                False,
                "failed in the wrong layer; expected "
                f"{detect}, also got:\n" + "\n".join(str(f) for f in wrong_layer),
            )
            continue

        if not any(f.rule == want_rule for f in errors):
            suite.check(
                path.name,
                False,
                f"no error from rule {want_rule!r}; got:\n"
                + "\n".join(str(f) for f in errors),
            )
            continue

        stray = [f for f in errors if not (f.path == at or f.path.startswith(at + "/"))]
        suite.check(
            path.name,
            not stray,
            f"expected every error at or under {at!r}, also got:\n"
            + "\n".join(str(f) for f in stray),
        )

    # A key forbidden for this output kind is written `{"not": {}}`, whose
    # generated message ("should not be valid under {}") names neither the key
    # nor the rule. The checker replaces it with the schema's own `$comment`,
    # and these two checks bracket that: nothing may reach a tool author in the
    # raw form, and at least one fixture must exercise the replacement, or the
    # first check is passing on an empty set.
    jargon = [f"{name}: {m}" for name, m in messages if "should not be valid under" in m]
    suite.check(
        "a forbidden key is reported in words, not JSON Schema jargon",
        not jargon,
        "\n".join(jargon),
    )
    suite.check(
        "and some fixture actually declares a forbidden key",
        any("is not a key this command may declare" in m for _, m in messages),
        "nothing exercised the replacement, so the check above proves nothing",
    )


def _resolve(doc, pointer: str):
    """A JSON pointer, or the KeyError/IndexError of walking off the document."""
    node = doc
    for token in pointer.lstrip("/").split("/"):
        node = node[int(token)] if isinstance(node, list) else node[token]
    return node


def run_convert(suite: Suite, schema: dict):
    """The v0.2 converter: what it rewrites, what it refuses to guess, and what
    it leaves alone.

    The refusals are the part worth pinning. A converter that quietly invented
    a cardinality would produce documents that validate and lie, and nothing
    downstream could tell an invented value from a declared one. Silent
    deletions are the same failure pointed the other way: neither version
    restricts what else a document may carry, so a key the converter has no
    opinion about has to come out the far side unchanged.
    """
    print("\nconvert/  (v0.2 in, v0.3 draft plus an honest review list)")
    for path in sorted((FIXTURES / "convert").glob("*.json")):
        doc = json.loads(path.read_text())
        meta = _meta(doc, path)
        out, review = cc.convert(doc, meta.get("assume_cardinality"))
        got = {r.path for r in review}

        # Exact, not "contains": an empty expectation has to mean the converter
        # reviewed nothing, or the clean fixture would pass without proving it.
        want = set(meta.get("expects_review", []))
        suite.check(
            f"{path.name}: review list",
            got == want,
            f"missing {sorted(want - got)}, unexpected {sorted(got - want)}",
        )

        errors = [f for f in cv.validate(out, schema) if f.severity == "error"]
        if meta.get("expects_clean"):
            suite.check(
                f"{path.name}: draft validates",
                not errors,
                "\n".join(str(f) for f in errors),
            )
        else:
            suite.check(
                f"{path.name}: draft is incomplete, as declared",
                bool(errors),
                "the draft validates, so the review items were not load-bearing",
            )

        flat = [c.get("name") for c in out.get("commands", [])]
        expected = meta.get("expects_commands")
        suite.check(
            f"{path.name}: flattens to the expected paths",
            all("subcommands" not in c for c in out.get("commands", []))
            and (expected is None or flat == expected),
            f"got {flat}",
        )

        for pointer, want_value in meta.get("expects_preserved", {}).items():
            try:
                got_value = _resolve(out, pointer)
            except (KeyError, IndexError, ValueError):
                got_value = "(absent)"
            suite.check(
                f"{path.name}: carries {pointer} through",
                got_value == want_value,
                f"expected {want_value!r}, got {got_value!r}",
            )


def _subschema(schema: dict, ref: str) -> dict:
    """A standalone schema for one `$defs` entry, with `$defs` still resolvable."""
    return {
        "$schema": schema["$schema"],
        "$defs": schema["$defs"],
        "$ref": f"#/$defs/{ref}",
    }


def _json_values(block: str) -> list:
    """Every JSON value in one fenced block.

    A block may hold several objects in sequence (two commands shown side by
    side) or the members of an object without its braces (`"outcomes": [...]`).
    Both are normal in prose and neither is a parse failure worth reporting.
    """
    text = block.strip()
    if text.startswith('"'):
        return [json.loads("{" + text + "}")]
    decoder = json.JSONDecoder()
    values, i = [], 0
    while i < len(text):
        value, end = decoder.raw_decode(text, i)
        values.append(value)
        i = end
        while i < len(text) and text[i] in " \n\r\t,":
            i += 1
    return values


# A block that illustrates one key rather than a whole shape is marked in the
# markdown source, immediately above its fence. Marking is explicit and rare on
# purpose: an unmarked block is checked, so nothing gets skipped by accident.
FRAGMENT_MARKER = "<!-- clispec-test: fragment -->"


def _pages() -> list[Path]:
    """Every page the site publishes as current.

    `docs/spec/` is excluded: those pages are frozen archives of older versions,
    and validating their examples against v0.3 would assert the opposite of what
    freezing means.
    """
    docs = REPO_ROOT / "docs"
    return sorted(
        p for p in docs.rglob("*.md") if "spec" not in p.relative_to(docs).parts
    )


def run_docs(suite: Suite, schema: dict):
    """Every JSON example on the site, validated against the schema it describes.

    Prose and schema drift apart silently: an example keeps looking right long
    after the rule it illustrates has changed, and it is the example people copy.
    Blocks are classified by shape, and the counts are asserted at the end so a
    classifier that quietly stops recognising commands cannot pass.
    """
    print("\ndocs/  (the site's own examples)")
    found = 0
    seen = {"document": 0, "command": 0, "field": 0, "outcome": 0, "envelope": 0}

    for page in _pages():
        rel = page.relative_to(REPO_ROOT / "docs")
        text = page.read_text()
        n = 0
        for match in re.finditer(r"```json\n(.*?)```", text, re.S):
            n += 1
            if text[: match.start()].rstrip().endswith(FRAGMENT_MARKER):
                continue
            found += 1
            label = f"{rel}[{n}]"
            try:
                values = _json_values(match.group(1))
            except json.JSONDecodeError as exc:
                suite.check(f"{label} parses", False, f"{exc.msg} at position {exc.pos}")
                continue

            for value in values:
                if not isinstance(value, dict):
                    continue
                if "clispec" in value:
                    seen["document"] += 1
                    findings = cv.validate(value, schema)
                    suite.check(
                        f"{label}: complete document validates",
                        not findings,
                        "\n".join(str(f) for f in findings),
                    )
                elif "error" in value and isinstance(value["error"], dict):
                    # The error envelope is a runtime payload, not a schema document.
                    seen["envelope"] += 1
                elif "outcomes" in value:
                    for outcome in value["outcomes"]:
                        seen["outcome"] += 1
                        errs = cv.schema_findings(outcome, _subschema(schema, "outcome"))
                        suite.check(
                            f"{label}: outcome {outcome.get('name')!r}",
                            not errs,
                            "\n".join(str(e) for e in errs),
                        )
                elif "effects" in value:
                    seen["command"] += 1
                    errs = cv.schema_findings(value, _subschema(schema, "command"))
                    suite.check(
                        f"{label}: command {value.get('name')!r}",
                        not errs,
                        "\n".join(str(e) for e in errs),
                    )
                elif "name" in value and "type" in value:
                    seen["field"] += 1
                    errs = cv.schema_findings(value, _subschema(schema, "field"))
                    suite.check(
                        f"{label}: field {value.get('name')!r}",
                        not errs,
                        "\n".join(str(e) for e in errs),
                    )

    suite.check("the site contains JSON examples to check", bool(found), "found none")

    # Minimums, not exact counts: the prose should be free to gain examples, but
    # a classifier that silently recognises nothing must not read as success.
    for shape, least in (("document", 2), ("command", 3), ("field", 3), ("outcome", 1)):
        suite.check(
            f"the spec still illustrates {shape} shapes ({seen[shape]} found)",
            seen[shape] >= least,
            f"expected at least {least}",
        )


def run_lint(suite: Suite, schema: dict):
    print("\nlint/  (conformant, but worth saying out loud)")
    for path in sorted((FIXTURES / "lint").glob("*.json")):
        doc = json.loads(path.read_text())
        meta = _meta(doc, path)
        expects = meta.get("expects")
        findings = cv.validate(doc, schema)
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]

        if errors:
            suite.check(
                path.name,
                False,
                "a lint fixture must be conformant; got errors:\n"
                + "\n".join(str(f) for f in errors),
            )
            continue
        suite.check(
            path.name,
            any(f.rule == expects for f in warnings),
            f"expected a {expects!r} warning; got: "
            + (", ".join(f.rule for f in warnings) or "none"),
        )


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    schema = cv.load_schema(Path(argv[1])) if argv[:1] == ["--schema"] else cv.load_schema()
    suite = Suite()
    controls(suite, schema)
    run_required_keys(suite, schema)
    run_valid(suite, schema)
    run_invalid(suite, schema)
    run_lint(suite, schema)
    run_convert(suite, schema)
    run_docs(suite, schema)

    total = suite.passed + len(suite.failures)
    print()
    if suite.failures:
        print(f"{RED}{len(suite.failures)} of {total} checks failed{RESET}")
        for f in suite.failures:
            print(f"  - {f.splitlines()[0]}")
        return 1
    print(f"{GREEN}{total} checks passed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
