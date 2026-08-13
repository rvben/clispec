#!/usr/bin/env python3
"""Mutation-test the fixture suite.

A green suite is not evidence until it can go red. This breaks one rule at a
time and requires the suite to notice. A mutation that survives means no
fixture pins that rule, so the rule could be dropped from a future edit of the
schema without anything objecting, which is precisely what a frozen version
must not allow.

Three families of mutation:

  schema      delete a conditional, a required entry, a pattern or an enum
  rules       remove one checker rule (the layer JSON Schema cannot express)
  lints       remove one advisory lint

Plus the inverse of the required-key check: adding a required key is as
breaking as removing one, and the suite must reject both.

Run it after any change to the schema or the checker. Exit code is the number
of surviving mutations, capped at 1.
"""

from __future__ import annotations

import copy
import io
import json
import sys
from contextlib import redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "tools"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

import clispec_validate as cv  # noqa: E402
import run as runner  # noqa: E402

GREEN, RED, DIM, RESET = "\033[32m", "\033[31m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = DIM = RESET = ""


def suite_fails(schema: dict | None = None) -> bool:
    """Run the whole fixture suite silently; True if it rejects this world."""
    saved = cv.load_schema
    if schema is not None:
        cv.load_schema = lambda path=None: copy.deepcopy(schema)
    try:
        with redirect_stdout(io.StringIO()):
            return runner.main([]) != 0
    finally:
        cv.load_schema = saved


def schema_mutations(base: dict):
    """Every rule in the schema, deleted one at a time."""
    for defname in ("command", "type_node", "field"):
        for i, block in enumerate(base["$defs"][defname].get("allOf", [])):
            label = block.get("$comment") or json.dumps(block.get("if", {}))[:60]
            m = copy.deepcopy(base)
            del m["$defs"][defname]["allOf"][i]
            yield f"{defname}/allOf[{i}] {label}", m

    # Conditionals nested inside a property, which the sweep above walks past:
    # `pagination` carries its own per-style requirements, and those are the
    # rules that decide whether a declared paging capability can be driven.
    def _pagination(schema: dict) -> dict:
        return schema["$defs"]["command"]["properties"]["pagination"]

    for i, block in enumerate(_pagination(base).get("allOf", [])):
        label = block.get("$comment") or json.dumps(block.get("if", {}))[:60]
        m = copy.deepcopy(base)
        del _pagination(m)["allOf"][i]
        yield f"command.pagination/allOf[{i}] {label}", m

    scopes = [("", lambda s: s)]
    scopes += [(f"$defs/{n}", (lambda n: lambda s: s["$defs"][n])(n)) for n in ("command", "error")]
    for scope, locate in scopes:
        for name in locate(base).get("required", []):
            m = copy.deepcopy(base)
            target = locate(m)
            target["required"] = [r for r in target["required"] if r != name]
            yield f"required {scope}/{name}", m

    m = copy.deepcopy(base)
    del m["$defs"]["field"]["properties"]["type"]["enum"]
    yield "field.type enum removed", m

    m = copy.deepcopy(base)
    del m["$defs"]["command"]["properties"]["name"]["pattern"]
    yield "command.name pattern removed", m

    m = copy.deepcopy(base)
    del m["$defs"]["command"]["properties"]["subcommands"]
    yield "retired key subcommands accepted again", m

    m = copy.deepcopy(base)
    del m["properties"]["command_layout"]
    yield "retired key command_layout accepted again", m

    m = copy.deepcopy(base)
    m["required"] = m["required"] + ["homepage"]
    yield "a required key added", m

    # The checker reads the reserved kinds out of the schema, so an empty list
    # would leave the near-miss lint measuring nothing while still passing.
    m = copy.deepcopy(base)
    m["x-standard-error-kinds"]["kinds"] = {}
    yield "reserved error kinds emptied", m


def main() -> int:
    base = cv.load_schema()

    print("control")
    if suite_fails():
        print(f"  {RED}FAIL{RESET} the unmutated suite is already red; fix that first")
        return 1
    print(f"  {GREEN}ok{RESET}   the unmutated suite passes")

    survivors: list[str] = []

    def report(label: str, killed: bool):
        if killed:
            print(f"  {GREEN}killed  {RESET} {DIM}{label}{RESET}")
        else:
            survivors.append(label)
            print(f"  {RED}SURVIVED{RESET} {label}")

    print("\nschema")
    for label, mutant in schema_mutations(base):
        report(label, suite_fails(mutant))

    for layer in ("RULES", "LINTS"):
        print(f"\n{layer.lower()}")
        original = list(getattr(cv, layer))
        for i, fn in enumerate(original):
            setattr(cv, layer, original[:i] + original[i + 1 :])
            try:
                killed = suite_fails()
            finally:
                setattr(cv, layer, original)
            report(fn.__name__, killed)

    total = len(survivors)
    print()
    if survivors:
        print(f"{RED}{total} mutation(s) survived{RESET}; no fixture pins:")
        for s in survivors:
            print(f"  - {s}")
        return 1
    print(f"{GREEN}every mutation killed{RESET}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
