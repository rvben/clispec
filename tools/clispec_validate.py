#!/usr/bin/env python3
"""Validate a clispec schema document against The CLI Spec v0.3.

Two layers, deliberately separated:

  errors   Rules that make a document non-conformant. JSON Schema catches the
           structural ones; `RULES` below catches the ones JSON Schema cannot
           express at all (referential integrity, uniqueness across two arrays,
           conditionally required error kinds).

  lints    Advisory findings. A typo like `mutatng` is caught here rather than
           by closing the schema, because the published schema stays open so
           tools can attach their own metadata without breaking conformance.

Usage:
    clispec_validate.py FILE [FILE...]
    clispec_validate.py --strict FILE      # lints are errors
    clispec_validate.py --json FILE        # machine-readable findings
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

try:
    import jsonschema
except ImportError:  # pragma: no cover - surfaced to the user, not handled
    sys.exit(
        "jsonschema is not installed. Run via `make` (which uses uv), or:\n"
        "    uv run --with jsonschema python tools/clispec_validate.py ..."
    )

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCHEMA = REPO_ROOT / "docs" / "schema" / "v0.3.json"

def standard_kinds(schema: dict) -> dict:
    """The reserved error kinds, read from the schema rather than restated.

    The schema is the single home for this list. A copy here would be a second
    source of truth that drifts silently, and the drift would be invisible: the
    near-miss lint would keep passing while measuring the wrong set.
    """
    return schema.get("x-standard-error-kinds", {}).get("kinds", {})


class Finding:
    __slots__ = ("severity", "path", "rule", "message")

    def __init__(self, severity: str, path: str, rule: str, message: str):
        self.severity = severity
        self.path = path
        self.rule = rule
        self.message = message

    def __str__(self) -> str:
        where = self.path or "(document)"
        return f"{self.severity:7} {where}: {self.message}  [{self.rule}]"

    def as_dict(self) -> dict:
        return {
            "severity": self.severity,
            "path": self.path,
            "rule": self.rule,
            "message": self.message,
        }


def load_schema(path: Path | None = None) -> dict:
    return json.loads((path or DEFAULT_SCHEMA).read_text())


# --------------------------------------------------------------------------
# Layer 1: JSON Schema
# --------------------------------------------------------------------------


def _deref(root: dict, node):
    """Follow a local `$ref` so a walk can continue past a `$defs` boundary."""
    for _ in range(8):
        if not (isinstance(node, dict) and isinstance(node.get("$ref"), str)):
            return node
        ref = node["$ref"]
        if not ref.startswith("#/"):
            return node
        target = root
        for step in ref[2:].split("/"):
            target = target[int(step)] if isinstance(target, list) else target.get(step)
            if target is None:
                return node
        node = target
    return node


def _schema_at(root: dict, schema_path) -> dict | None:
    """The subschema at a validator's schema path, following local `$ref`s."""
    node = root
    for token in schema_path:
        node = _deref(root, node)
        if isinstance(node, list):
            node = node[int(token)]
        elif isinstance(node, dict):
            node = node.get(token)
        else:
            return None
    node = _deref(root, node)
    return node if isinstance(node, dict) else None


def _why_forbidden(schema: dict, schema_path) -> str | None:
    """The schema's own explanation for a `not`, most specific first.

    The forbidding property's `description` says why this key is wrong here;
    the enclosing conditional's `$comment` says what the rule is. Reading them
    back out keeps the checker's wording and the rule in one place, rather than
    in a table here that drifts from the schema with nothing to notice.
    """
    path = list(schema_path)
    site = _schema_at(schema, path[:-1])
    if site and isinstance(site.get("description"), str):
        return site["description"]
    found = None
    for i in range(1, len(path)):
        node = _schema_at(schema, path[:i])
        if node and isinstance(node.get("$comment"), str):
            found = node["$comment"]
    return found


def _not_message(err, schema: dict) -> str:
    """Say what a `not` rejected, in words.

    `{"not": {}}` is how the schema forbids a key while still reporting that
    key's own path, and `{"not": {"const": ...}}` forbids one value of it.
    Both generate a message ("should not be valid under {}") that names neither
    the key nor the rule, at the moment a tool author most needs both.
    """
    if err.validator != "not":
        return err.message
    key = str(err.absolute_path[-1]) if err.absolute_path else "this key"
    why = _why_forbidden(schema, err.absolute_schema_path)
    if err.validator_value == {}:
        head = f"{key!r} is not a key this command may declare"
    else:
        head = f"{err.instance!r} is not an allowed value for {key!r} here"
    return f"{head}. {why}" if why else head


def schema_findings(doc, schema: dict) -> list[Finding]:
    validator = jsonschema.Draft202012Validator(schema)
    out = []
    for err in sorted(
        validator.iter_errors(doc), key=lambda e: [str(p) for p in e.absolute_path]
    ):
        parts = [str(p) for p in err.absolute_path]
        path = "/" + "/".join(parts) if parts else ""
        out.append(Finding("error", path, "json-schema", _not_message(err, schema)))
    return out


# --------------------------------------------------------------------------
# Layer 2: rules JSON Schema cannot express
# --------------------------------------------------------------------------


def _commands(doc) -> list:
    c = doc.get("commands")
    return c if isinstance(c, list) else []


def _named(doc, key: str, field: str) -> list:
    entries = doc.get(key)
    if not isinstance(entries, list):
        return []
    return [e.get(field) for e in entries if isinstance(e, dict)]


def _exit_code_owners(doc):
    """Every declared exit code, with the class and identifier that claims it."""
    owners: dict[int, list[tuple[str, str, int]]] = {}
    for cls, entries, code_field, label in (
        ("errors", doc.get("errors"), "exit_code", "kind"),
        ("outcomes", doc.get("outcomes"), "code", "name"),
    ):
        if not isinstance(entries, list):
            continue
        for i, e in enumerate(entries):
            if not isinstance(e, dict):
                continue
            code = e.get(code_field)
            if isinstance(code, int):
                owners.setdefault(code, []).append((cls, str(e.get(label)), i))
    return owners


def rule_ambiguous_exit_code(doc) -> list[Finding]:
    """One exit code must not mean both "it worked" and "it failed".

    Deliberately narrower than "every exit code is unique". Two error kinds
    sharing a code is a normal grouping choice: the code is the cheap first
    branch and the structured `kind` on stderr is the precise one, so a tool
    that answers 2 for every input problem is not ambiguous, just coarse. Five
    of the seven published v0.2 tools do exactly that, and failing them would
    be a bogus penalty of the sort v0.3 exists to remove.

    An error sharing a code with an *outcome* is a different thing entirely.
    The consumer cannot answer the only question the exit code exists to
    answer, and no amount of parsing helps if the two paths write different
    streams. Two outcomes sharing a code are equally unresolvable.
    """
    out = []
    for code, claims in sorted(_exit_code_owners(doc).items()):
        if len(claims) < 2:
            continue
        classes = {c for c, _, _ in claims}
        if classes == {"errors"}:
            continue  # coarse, not ambiguous; see lint_shared_error_exit_code
        cls, name, i = claims[-1]
        others = ", ".join(f"{c}[{j}] {n!r}" for c, n, j in claims[:-1])
        detail = (
            "an error and a successful outcome cannot share an exit code; "
            "a consumer cannot tell whether the command succeeded"
            if len(classes) > 1
            else "two outcomes cannot share an exit code; nothing distinguishes them"
        )
        out.append(
            Finding("error", f"/{cls}/{i}", "ambiguous-exit-code", f"exit code {code} is also used by {others}: {detail}")
        )
    return out


def rule_unique_names(doc) -> list[Finding]:
    """Two entries with the same identifier make the later one unaddressable."""
    out = []
    for key, field, rule in (
        ("errors", "kind", "unique-error-kinds"),
        ("outcomes", "name", "unique-outcome-names"),
        ("commands", "name", "unique-command-names"),
    ):
        seen = set()
        for i, name in enumerate(_named(doc, key, field)):
            if name is None:
                continue
            if name in seen:
                out.append(
                    Finding(
                        "error",
                        f"/{key}/{i}",
                        rule,
                        f"{field} {name!r} is declared more than once",
                    )
                )
            seen.add(name)
    return out


def rule_error_refs(doc) -> list[Finding]:
    """A per-command error list is only useful if it resolves to a real kind."""
    declared_kinds = {k for k in _named(doc, "errors", "kind") if isinstance(k, str)}
    declared_outcomes = {n for n in _named(doc, "outcomes", "name") if isinstance(n, str)}
    out = []
    for i, cmd in enumerate(_commands(doc)):
        if not isinstance(cmd, dict):
            continue
        for key, declared, rule in (
            ("errors", declared_kinds, "error-ref-resolves"),
            ("outcomes", declared_outcomes, "outcome-ref-resolves"),
        ):
            refs = cmd.get(key)
            if not isinstance(refs, list):
                continue
            for j, ref in enumerate(refs):
                if isinstance(ref, str) and ref not in declared:
                    out.append(
                        Finding(
                            "error",
                            f"/commands/{i}/{key}/{j}",
                            rule,
                            f"{ref!r} is not declared in the top-level {key}",
                        )
                    )
    return out


def rule_conditional_kinds(doc) -> list[Finding]:
    """A declared behavior implies the error kind that behavior produces."""
    declared = {k for k in _named(doc, "errors", "kind") if isinstance(k, str)}
    out = []
    needs: dict[str, list[str]] = {}
    for cmd in _commands(doc):
        if not isinstance(cmd, dict):
            continue
        name = cmd.get("name", "?")
        if cmd.get("confirmation_bypass_arg"):
            needs.setdefault("confirmation_required", []).append(name)
        if cmd.get("requires_tty") is True:
            needs.setdefault("tty_required", []).append(name)
    for kind, users in needs.items():
        if kind not in declared:
            out.append(
                Finding(
                    "error",
                    "/errors",
                    "conditional-error-kind",
                    f"{', '.join(repr(u) for u in users)} implies the {kind!r} error kind, "
                    "which is not declared",
                )
            )
    return out


def rule_arg_refs(doc) -> list[Finding]:
    """A field naming an argument must name one the command actually accepts."""
    global_args = {
        a.get("name")
        for a in doc.get("global_args", []) or []
        if isinstance(a, dict)
    }
    out = []
    for i, cmd in enumerate(_commands(doc)):
        if not isinstance(cmd, dict):
            continue
        local = {a.get("name") for a in cmd.get("args", []) or [] if isinstance(a, dict)}
        known = local | global_args
        pointers = [
            ("fields_arg", cmd.get("fields_arg"), f"/commands/{i}/fields_arg"),
            (
                "confirmation_bypass_arg",
                cmd.get("confirmation_bypass_arg"),
                f"/commands/{i}/confirmation_bypass_arg",
            ),
            (
                "idempotency_key_arg",
                cmd.get("idempotency_key_arg"),
                f"/commands/{i}/idempotency_key_arg",
            ),
        ]
        pag = cmd.get("pagination")
        if isinstance(pag, dict):
            pointers += [
                ("pagination.limit_arg", pag.get("limit_arg"), f"/commands/{i}/pagination/limit_arg"),
                ("pagination.offset_arg", pag.get("offset_arg"), f"/commands/{i}/pagination/offset_arg"),
                ("pagination.cursor_arg", pag.get("cursor_arg"), f"/commands/{i}/pagination/cursor_arg"),
            ]
        for label, value, path in pointers:
            if isinstance(value, str) and value not in known:
                out.append(
                    Finding(
                        "error",
                        path,
                        "arg-ref-resolves",
                        f"{label} names {value!r}, which is not in this command's "
                        "args or in global_args",
                    )
                )
    return out


def rule_stdout_schema_valid(doc) -> list[Finding]:
    """An unparseable stdout_schema is worse than none: it looks authoritative."""
    out = []
    for i, cmd in enumerate(_commands(doc)):
        if not isinstance(cmd, dict):
            continue
        sub = cmd.get("stdout_schema")
        if sub is None:
            continue
        try:
            jsonschema.Draft202012Validator.check_schema(sub)
        except jsonschema.SchemaError as exc:
            out.append(
                Finding(
                    "error",
                    f"/commands/{i}/stdout_schema",
                    "stdout-schema-valid",
                    f"not a valid draft 2020-12 schema: {exc.message}",
                )
            )
    return out


RULES = [
    rule_ambiguous_exit_code,
    rule_unique_names,
    rule_error_refs,
    rule_conditional_kinds,
    rule_arg_refs,
    rule_stdout_schema_valid,
]


# --------------------------------------------------------------------------
# Layer 3: lints
# --------------------------------------------------------------------------

WIDE_RECORD = 8  # fields beyond which selecting a subset starts to matter


def _levenshtein1(a: str, b: str) -> bool:
    """True when one edit turns a into b. Cheap enough to skip a full matrix."""
    if a == b:
        return False
    la, lb = len(a), len(b)
    if abs(la - lb) > 1:
        return False
    if la == lb:
        return sum(x != y for x, y in zip(a, b)) == 1
    if la > lb:
        a, b, la, lb = b, a, lb, la
    i = 0
    while i < la and a[i] == b[i]:
        i += 1
    return a[i:] == b[i + 1 :]


def _known_keys(schema: dict, defs_name: str | None = None) -> set[str]:
    node = schema if defs_name is None else schema["$defs"][defs_name]
    return set(node.get("properties", {}))


def lint_near_miss_keys(doc, schema: dict) -> list[Finding]:
    """Catch `mutatng` without closing the schema to genuine extensions."""
    out = []

    def check(obj, known: set[str], path: str):
        if not isinstance(obj, dict):
            return
        for key in obj:
            if key in known or key.startswith("x-") or key == "extensions":
                continue
            near = [k for k in known if _levenshtein1(key, k)]
            if near:
                out.append(
                    Finding(
                        "warning",
                        f"{path}/{key}",
                        "near-miss-key",
                        f"unknown key {key!r} is one edit from {near[0]!r}; "
                        "a typo here is silently ignored",
                    )
                )

    check(doc, _known_keys(schema), "")
    for i, cmd in enumerate(_commands(doc)):
        check(cmd, _known_keys(schema, "command"), f"/commands/{i}")
        for j, arg in enumerate(cmd.get("args", []) or [] if isinstance(cmd, dict) else []):
            check(arg, _known_keys(schema, "arg"), f"/commands/{i}/args/{j}")
        for j, f in enumerate(
            cmd.get("output_fields", []) or [] if isinstance(cmd, dict) else []
        ):
            check(f, _known_keys(schema, "field"), f"/commands/{i}/output_fields/{j}")
    for i, e in enumerate(doc.get("errors", []) or []):
        check(e, _known_keys(schema, "error"), f"/errors/{i}")
    for i, o in enumerate(doc.get("outcomes", []) or []):
        check(o, _known_keys(schema, "outcome"), f"/outcomes/{i}")
    return out


def lint_wide_record_needs_fields(doc, schema: dict) -> list[Finding]:
    """A wide record burns context the consumer cannot reclaim."""
    del schema
    out = []
    for i, cmd in enumerate(_commands(doc)):
        if not isinstance(cmd, dict) or cmd.get("fields_arg"):
            continue
        if cmd.get("cardinality") == "unbounded":
            continue  # already a hard schema error
        fields = cmd.get("output_fields")
        if isinstance(fields, list) and len(fields) > WIDE_RECORD:
            out.append(
                Finding(
                    "warning",
                    f"/commands/{i}",
                    "wide-record-needs-fields",
                    f"{cmd.get('name')!r} returns {len(fields)} fields and declares no "
                    "fields_arg; consumers cannot narrow the record",
                )
            )
    return out


def lint_mutating_without_conflict(doc, schema: dict) -> list[Finding]:
    """Advisory, not a rule: not every mutating command has a conflict state."""
    del schema
    declared = {k for k in _named(doc, "errors", "kind") if isinstance(k, str)}
    if "conflict" in declared:
        return []
    mutators = [
        c.get("name")
        for c in _commands(doc)
        if isinstance(c, dict) and c.get("effects") in ("idempotent", "non_idempotent")
    ]
    if not mutators:
        return []
    return [
        Finding(
            "warning",
            "/errors",
            "mutating-without-conflict",
            f"{len(mutators)} mutating command(s) and no 'conflict' error kind; "
            "confirm none can meet an incompatible existing state",
        )
    ]


def lint_unknown_standard_kind(doc, schema: dict) -> list[Finding]:
    """A reserved kind used with a different meaning breaks generic handlers."""
    reserved = standard_kinds(schema)
    out = []
    for i, e in enumerate(doc.get("errors", []) or []):
        if not isinstance(e, dict):
            continue
        kind = e.get("kind")
        if not isinstance(kind, str) or kind in reserved:
            continue
        near = [k for k in reserved if _levenshtein1(kind, k)]
        if near:
            out.append(
                Finding(
                    "warning",
                    f"/errors/{i}/kind",
                    "near-miss-standard-kind",
                    f"{kind!r} is one edit from the reserved kind {near[0]!r}",
                )
            )
    return out


def lint_shared_error_exit_code(doc, schema: dict) -> list[Finding]:
    """Coarse exit codes are legal, but the consumer should know they are coarse."""
    del schema
    out = []
    for code, claims in sorted(_exit_code_owners(doc).items()):
        if len(claims) < 2 or {c for c, _, _ in claims} != {"errors"}:
            continue
        names = [n for _, n, _ in claims]
        out.append(
            Finding(
                "warning",
                f"/errors/{claims[-1][2]}",
                "shared-error-exit-code",
                f"exit code {code} covers {len(names)} kinds ({', '.join(names)}); "
                "consumers must read the structured kind to tell them apart",
            )
        )
    return out


def lint_non_idempotent_without_key(doc, schema: dict) -> list[Finding]:
    """A retry-unsafe command with no key gives an agent nothing to work with."""
    del schema
    out = []
    for i, cmd in enumerate(_commands(doc)):
        if not isinstance(cmd, dict):
            continue
        if cmd.get("effects") == "non_idempotent" and not cmd.get("idempotency_key_arg"):
            out.append(
                Finding(
                    "warning",
                    f"/commands/{i}",
                    "non-idempotent-without-key",
                    f"{cmd.get('name')!r} cannot be safely retried and offers no "
                    "idempotency key; a consumer that times out has no safe move",
                )
            )
    return out


LINTS = [
    lint_near_miss_keys,
    lint_wide_record_needs_fields,
    lint_mutating_without_conflict,
    lint_unknown_standard_kind,
    lint_shared_error_exit_code,
    lint_non_idempotent_without_key,
]


# --------------------------------------------------------------------------


def validate(doc, schema: dict | None = None) -> list[Finding]:
    """All findings for a document, errors first."""
    schema = schema or load_schema()
    findings = schema_findings(doc, schema)
    # Rules and lints assume a structurally valid object. Once JSON Schema has
    # rejected the document, running later layers adds noise and can turn a
    # useful validation error into a Python exception on values such as `[]`.
    if findings:
        return findings
    for rule in RULES:
        findings.extend(rule(doc))
    for lint in LINTS:
        findings.extend(lint(doc, schema))
    return findings


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--schema", type=Path, default=None)
    ap.add_argument("--strict", action="store_true", help="treat lints as errors")
    ap.add_argument("--json", action="store_true", dest="as_json")
    args = ap.parse_args(argv)

    schema = load_schema(args.schema)
    failed = False
    report = {}
    for path in args.files:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as exc:
            findings = [Finding("error", "", "parse", f"invalid JSON: {exc}")]
        else:
            findings = validate(doc, schema)
        errors = [f for f in findings if f.severity == "error"]
        warnings = [f for f in findings if f.severity == "warning"]
        if errors or (args.strict and warnings):
            failed = True
        if args.as_json:
            report[str(path)] = {
                "ok": not errors and not (args.strict and warnings),
                "findings": [f.as_dict() for f in findings],
            }
        else:
            status = "FAIL" if errors else ("WARN" if warnings else "ok")
            print(f"{status:4} {path}")
            for f in findings:
                print(f"       {f}")

    if args.as_json:
        print(json.dumps(report, indent=2))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
