#!/usr/bin/env python3
"""Convert a clispec v0.2 document to a v0.3 draft.

What this tool will and will not do is the whole design. It rewrites every
mapping that is mechanical, and it refuses to invent the ones that are not.
v0.3 asks three questions v0.2 did not always record an answer to:

  cardinality   Is the result set bounded? Guessing `bounded` hides an
                unbounded dump from a consumer that trusted the declaration;
                guessing `unbounded` demands pagination the tool may not have.
  idempotency   `mutating: true` covers both "safe to repeat" and "charges the
                card twice". Only the author knows which.
  output shape  An absent `output_fields` did not distinguish an undocumented
                shape from one that genuinely follows caller input. Only the
                latter may honestly become `stdout_schema: {}`.

For those the converter emits a review item and leaves the key out, so the
draft fails validation until a human answers. That is deliberate: a document
that validates because a tool guessed is worse than one that does not
validate, because nothing downstream can tell the guess from a declaration.

The same principle applies in the other direction: keys this converter does
not recognise are carried through untouched rather than dropped. v0.2 permitted
extra metadata and v0.3 still does, so discarding it would be a silent edit to
someone's published contract. Only the keys listed below are consumed, and each
is consumed because v0.3 answers it another way.

Usage:
    clispec_convert.py FILE [-o OUT]
    clispec_convert.py FILE --assume-cardinality bounded   # apply in bulk
    clispec_convert.py FILE --review-json                  # machine-readable

Exit status is 0 only when the draft needs no human review and validates.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))

import clispec_validate as cv  # noqa: E402

FIELD_TYPES = {"string", "integer", "number", "boolean", "object", "array"}

# Suffixed spellings of a reserved kind, seen across published v0.2 tools.
# Suggested, never applied: an error kind is part of a tool's public contract
# and renaming one silently would break every consumer already branching on it.
KIND_SUGGESTIONS = {
    "auth_error": "auth",
    "io_error": "io",
    "parse_error": "parse",
    "network_error": "unavailable",
    "http": "unavailable",
    "api_error": "unavailable",
    "backend": "unavailable",
    "general_error": "internal",
    "system": "internal",
    "config_error": "usage",
    "client_error": "usage",
    "retry_later": "rate_limit",
}

BYPASS_ARGS = ("--yes", "--force", "--confirm", "--no-confirm", "--assume-yes")

# Keys the converter reads, rewrites, retires or relocates. Everything else is
# metadata v0.2 permitted and v0.3 still permits, so it is copied through
# untouched. Dropping it would be a silent edit to someone's published
# contract, and the tools most likely to carry extensions are the ones that
# have been maintained long enough to have consumers depending on them.
DOC_KEYS = {
    "clispec",
    "name",
    "version",
    "description",
    "global_args",
    "output",
    "extensions",
    "commands",
    "errors",
    "outcomes",
    "command_layout",  # retired in v0.3; the flat list is the only layout
    "notes",  # relocated into extensions
    "conventions",
    "homepage",
}

COMMAND_KEYS = {
    "name",
    "description",
    "mutating",
    "subcommands",  # retired in v0.3; flattened into the command path
    "args",
    "stability",
    "example",
    "outcomes",
    "errors",
    "extensions",
    "output_fields",
    "confirmation_required",  # answered by confirmation_bypass_arg
    "note",  # relocated into extensions
    "notes",
}

FIELD_KEYS = {"name", "type", "description", "enum", "fields"}

OUTCOME_KEYS = {"code", "name", "exit_code", "kind", "description"}


def carry_over(src: dict, dst: dict, consumed: set[str]) -> None:
    """Copy the keys this converter has no opinion about."""
    for key, value in src.items():
        if key not in consumed and key not in dst:
            dst[key] = value


class Review:
    """Something the converter refuses to decide on the author's behalf."""

    __slots__ = ("path", "issue", "action")

    def __init__(self, path: str, issue: str, action: str):
        self.path = path
        self.issue = issue
        self.action = action

    def __str__(self) -> str:
        return f"{self.path or '(document)'}: {self.issue}\n    -> {self.action}"

    def as_dict(self) -> dict:
        return {"path": self.path, "issue": self.issue, "action": self.action}


def parse_type(raw, path: str, review: list[Review]) -> dict:
    """Turn a v0.2 type string into a v0.3 type node.

    v0.2 let the type be any string and two conventions grew in the wild: a
    `[]` suffix for arrays and a ` | null` union for optionality. Both are
    recoverable. A bare `array` is not, because the element type was never
    written down anywhere.
    """
    if not isinstance(raw, str):
        review.append(Review(path, f"type is {raw!r}, not a string", "write a v0.3 type node by hand"))
        return {}

    parts = [p.strip() for p in raw.split("|")]
    nullable = "null" in parts
    rest = [p for p in parts if p != "null"]

    if len(rest) != 1:
        review.append(
            Review(
                path,
                f"type {raw!r} is a union of {len(rest)} non-null types, which v0.3 cannot express",
                "pick the single type the field really carries, or split the field",
            )
        )
        return {}

    base = rest[0]
    node: dict = {}

    if base.endswith("[]"):
        inner = base[:-2].strip()
        node["type"] = "array"
        if inner in FIELD_TYPES:
            node["items"] = {"type": inner}
        else:
            review.append(
                Review(
                    path,
                    f"array element type {inner!r} is not one of {sorted(FIELD_TYPES)}",
                    "declare the element type v0.3 recognises",
                )
            )
    elif base in FIELD_TYPES:
        node["type"] = base
        if base == "array":
            review.append(
                Review(
                    path,
                    "type is a bare 'array'; v0.2 never recorded the element type",
                    "add \"items\": {\"type\": ...} describing one element",
                )
            )
    else:
        review.append(
            Review(
                path,
                f"type {base!r} is not one of {sorted(FIELD_TYPES)}",
                "map it to a v0.3 type; output field types are a closed set",
            )
        )
        return {}

    if nullable:
        node["nullable"] = True
    return node


def convert_field(field, path: str, review: list[Review]) -> dict:
    if not isinstance(field, dict):
        review.append(Review(path, "output field is not an object", "rewrite it by hand"))
        return {}
    out = {"name": field.get("name")}
    out.update(parse_type(field.get("type"), f"{path}/type", review))
    if isinstance(field.get("description"), str):
        out["description"] = field["description"]
    if isinstance(field.get("enum"), list):
        out["enum"] = field["enum"]
    nested = field.get("fields")
    if isinstance(nested, list):
        out["fields"] = [
            convert_field(f, f"{path}/fields/{i}", review) for i, f in enumerate(nested)
        ]
    carry_over(field, out, FIELD_KEYS)
    return out


def flatten(commands, prefix: tuple[str, ...], review: list[Review]) -> list[tuple[tuple, dict]]:
    """Depth-first walk producing (path, command) with subcommands removed."""
    out = []
    for cmd in commands or []:
        if not isinstance(cmd, dict):
            continue
        name = cmd.get("name")
        if not isinstance(name, str):
            review.append(Review("", f"a command under {'/'.join(prefix) or '(root)'} has no name", "give it one"))
            continue
        here = prefix + tuple(name.split())
        children = cmd.get("subcommands")
        body = {k: v for k, v in cmd.items() if k != "subcommands"}
        if not (isinstance(children, list) and children):
            out.append((here, body))
            continue

        # A group that also does work on its own keeps an entry of its own, but
        # only on evidence that invoking it produces something: output fields,
        # or an example of invoking it. `mutating` is not evidence, because
        # v0.2 tools put it on every node including pure groups. `args` is not
        # evidence either: a flag declared on a group is how clap, cobra and
        # click all spell "every child accepts this", so it says nothing about
        # what invoking the group itself does. Treating either as a signal
        # publishes `sites` as a runnable command when it only prints help, and
        # an agent would call it.
        if any(k in body for k in ("output_fields", "example")):
            out.append((here, body))
        elif "args" in body:
            review.append(
                Review(
                    "",
                    f"{' '.join(here)!r} groups subcommands and declares args but no "
                    "output fields or example of its own, so it was dropped: a "
                    "group-level flag is usually one its children inherit",
                    "add it back as a command if invoking it directly does something; "
                    "if those args are flags the children share, repeat them on each "
                    "child or move them to global_args",
                )
            )
        else:
            review.append(
                Review(
                    "",
                    f"{' '.join(here)!r} groups subcommands and was dropped, "
                    "having declared no output fields or example of its own",
                    "if it does something when invoked directly, add it back as a command",
                )
            )
        out.extend(flatten(children, here, review))
    return out


def normalize_args(args, path: str, review: list[Review]):
    """Rewrite `short` to the dashed spelling the document uses everywhere else.

    Published v0.2 documents write the bare letter ("o"), while `name` carries
    its dashes ("--output"). One spelling per document beats two, and adding
    the dash loses nothing, so this is applied rather than reviewed.
    """
    if not isinstance(args, list):
        return args
    out = []
    for a in args:
        if not isinstance(a, dict):
            out.append(a)
            continue
        a = dict(a)
        short = a.get("short")
        if isinstance(short, str) and re.fullmatch(r"[A-Za-z0-9]", short):
            a["short"] = f"-{short}"
        out.append(a)
    return out


def convert_command(path_parts, cmd: dict, idx: int, assume, globals_: set, review: list[Review]) -> dict:
    at = f"/commands/{idx}"
    name = " ".join(path_parts)
    out: dict = {"name": name, "description": cmd.get("description", "")}

    if not out["description"]:
        review.append(Review(at, "command has no description", "write one; v0.3 requires it"))

    mutating = cmd.get("mutating")
    if mutating is False:
        out["effects"] = "read_only"
        out["mutating"] = False
    elif mutating is True:
        out["effects"] = "non_idempotent"
        out["mutating"] = True
        review.append(
            Review(
                f"{at}/effects",
                f"{name!r} was mutating: true, converted to the conservative 'non_idempotent'",
                "change to 'idempotent' if running it twice is the same as running it once",
            )
        )
    else:
        review.append(
            Review(
                f"{at}/effects",
                f"{name!r} never declared `mutating`, which in v0.2 meant unknown",
                "declare read_only, idempotent or non_idempotent; v0.3 requires it",
            )
        )

    for key in ("args", "stability", "example", "outcomes", "errors", "extensions"):
        if key in cmd:
            out[key] = cmd[key]
    if "args" in out:
        out["args"] = normalize_args(out["args"], at, review)

    fields = cmd.get("output_fields")
    if isinstance(fields, list):
        out["output_fields"] = [
            convert_field(f, f"{at}/output_fields/{i}", review) for i, f in enumerate(fields)
        ]
    else:
        review.append(
            Review(
                f"{at}/stdout_schema",
                f"{name!r} never described its output in v0.2",
                "declare output_fields or a stdout_schema; use {} only if the shape "
                "genuinely follows the caller's input",
            )
        )

    if assume:
        out["cardinality"] = assume
    else:
        review.append(
            Review(
                f"{at}/cardinality",
                f"{name!r} needs a cardinality and v0.2 recorded none",
                "declare single, bounded or unbounded (or set output_kind to stream/opaque)",
            )
        )

    if cmd.get("confirmation_required") is True:
        # The bypass flag is usually global rather than per-command, which is
        # where every published v0.2 tool that has one puts it.
        arg_names = {a.get("name") for a in cmd.get("args", []) or [] if isinstance(a, dict)}
        arg_names |= globals_
        found = next((a for a in BYPASS_ARGS if a in arg_names), None)
        if found:
            out["confirmation_bypass_arg"] = found
        else:
            review.append(
                Review(
                    f"{at}/confirmation_bypass_arg",
                    f"{name!r} declared confirmation_required but has no recognisable bypass argument",
                    "name the argument that skips the prompt, and declare the confirmation_required error kind",
                )
            )

    for key in ("note", "notes"):
        if key in cmd:
            out.setdefault("extensions", {})[key] = cmd[key]

    carry_over(cmd, out, COMMAND_KEYS)
    return out


def convert_outcome(o, path: str, review: list[Review]) -> dict:
    """v0.2 outcomes appeared in two shapes; v0.3 has one."""
    if not isinstance(o, dict):
        return {}
    if "code" in o and "name" in o:
        out = {"code": o["code"], "name": o["name"]}
    elif "exit_code" in o and "kind" in o:
        out = {"code": o["exit_code"], "name": o["kind"]}
        review.append(
            Review(
                path,
                "outcome used the error shape (exit_code/kind); mapped to code/name",
                "confirm this really is a success outcome and not an error entry",
            )
        )
    else:
        review.append(Review(path, "outcome has neither code/name nor exit_code/kind", "rewrite it"))
        return {}
    if isinstance(o.get("description"), str):
        out["description"] = o["description"]
    carry_over(o, out, OUTCOME_KEYS)
    return out


def convert(doc: dict, assume: str | None) -> tuple[dict, list[Review]]:
    review: list[Review] = []

    found = doc.get("clispec")
    if found != "0.2":
        raise SystemExit(
            f"this converter reads clispec 0.2 documents; this one declares {found!r}.\n"
            "Convert it to 0.2 first, or write the v0.3 document by hand."
        )

    out: dict = {"clispec": "0.3"}
    for key in ("name", "version", "description"):
        if key in doc:
            out[key] = doc[key]

    for key in ("global_args", "output", "extensions"):
        if key in doc:
            out[key] = doc[key]
    if "global_args" in out:
        out["global_args"] = normalize_args(out["global_args"], "/global_args", review)

    globals_ = {
        a.get("name") for a in out.get("global_args", []) or [] if isinstance(a, dict)
    }

    flat = flatten(doc.get("commands", []), (), review)
    out["commands"] = [
        convert_command(parts, cmd, i, assume, globals_, review)
        for i, (parts, cmd) in enumerate(flat)
    ]

    errors = doc.get("errors")
    if isinstance(errors, list):
        out["errors"] = errors
        for i, e in enumerate(errors):
            if not isinstance(e, dict):
                continue
            kind = e.get("kind")
            if kind in KIND_SUGGESTIONS:
                review.append(
                    Review(
                        f"/errors/{i}/kind",
                        f"{kind!r} is a spelling of the reserved kind {KIND_SUGGESTIONS[kind]!r}",
                        f"rename to {KIND_SUGGESTIONS[kind]!r} so generic handlers match, "
                        "or keep it if the meaning genuinely differs (this is not applied automatically: "
                        "the kind is part of your public contract)",
                    )
                )
            if "exit_code" not in e:
                review.append(
                    Review(f"/errors/{i}", f"error kind {kind!r} declares no exit_code", "add one")
                )
    else:
        review.append(Review("", "document declares no errors array", "v0.3 requires one"))

    outcomes = doc.get("outcomes")
    if isinstance(outcomes, list):
        converted = [convert_outcome(o, f"/outcomes/{i}", review) for i, o in enumerate(outcomes)]
        out["outcomes"] = [o for o in converted if o]

    for key in ("notes", "conventions", "homepage"):
        if key in doc:
            out.setdefault("extensions", {})[key] = doc[key]

    carry_over(doc, out, DOC_KEYS)
    return out, review


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("file", type=Path)
    ap.add_argument("-o", "--output", type=Path, help="write the draft here instead of stdout")
    ap.add_argument(
        "--assume-cardinality",
        choices=["single", "bounded"],
        help="apply this cardinality to every command instead of asking. "
        "'unbounded' is not offered: it requires pagination the converter cannot invent.",
    )
    ap.add_argument("--review-json", action="store_true", help="emit review items as JSON")
    args = ap.parse_args()

    doc = json.loads(args.file.read_text())
    out, review = convert(doc, args.assume_cardinality)

    schema = cv.load_schema()
    findings = cv.validate(out, schema)
    errors = [f for f in findings if f.severity == "error"]

    text = json.dumps(out, indent=2) + "\n"
    if args.output:
        args.output.write_text(text)
    else:
        print(text, end="")

    stream = sys.stderr
    if args.review_json:
        json.dump(
            {
                "review": [r.as_dict() for r in review],
                "findings": [f.as_dict() for f in findings],
            },
            stream,
            indent=2,
        )
        stream.write("\n")
    else:
        if review:
            print(f"\n{len(review)} item(s) need a human decision:", file=stream)
            for r in review:
                print(f"  {r}", file=stream)
        if errors:
            print(f"\n{len(errors)} validation error(s) remain in the draft:", file=stream)
            for f in errors:
                print(f"  {f}", file=stream)
        if not review and not errors:
            print("converted cleanly; the draft validates against v0.3", file=stream)

    return 1 if (review or errors) else 0


if __name__ == "__main__":
    sys.exit(main())
