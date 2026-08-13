#!/usr/bin/env python3
"""Hold the published spec versions to their published bytes.

A frozen version is a promise that a document validating today still validates
in five years. That promise is only as good as the mechanism behind it, and a
sentence in a policy document is not a mechanism: v0.2 was amended five times
after publication, each time in good faith, each time silently changing what
"clispec 0.2" meant for every tool that had already claimed it.

So the checksum file is the enforcement point. `frozen` means the bytes are
final and any change is an error, in verify mode and in update mode alike.
`candidate` means the version is still being written and may move freely.
Promotion is a one-word edit to CHECKSUMS.txt, made deliberately, reviewed like
any other change.

Both artifacts of a version are covered: the schema a tool validates against and
the prose that says what the schema means. Freezing only the schema would leave
the half of the specification that JSON Schema cannot express free to drift.

    python tools/freeze.py            verify (what CI runs)
    python tools/freeze.py --update   re-record, refusing to move a frozen hash
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = REPO_ROOT / "docs"
CHECKSUMS = DOCS / "CHECKSUMS.txt"

# Everything a published version consists of. Paths are recorded relative to
# `docs/`, which is the site root, so a line in the published checksum file
# names the URL a reader can fetch to check it.
PUBLISHED = ("schema/v*.json", "spec/v*.md")

STATUSES = ("frozen", "candidate")

HEADER = """\
# Checksums for every published part of The CLI Spec: the schemas tools
# validate against, and the prose that says what those schemas mean.
#
# Paths are relative to https://clispec.dev/, so any line here can be checked
# against the live site.
#
# frozen     The bytes are final. That URL will serve exactly this document for
#            as long as the site exists, so a tool that validated against it
#            stays valid. Changing a frozen version is an error, not a
#            decision: publish the next version.
# candidate  Still being written. The hash moves; nothing should depend on it
#            yet beyond experimentation.
#
# Verified by `make checksums`, which CI runs on every push. `make
# checksums-update` re-records candidates and refuses to move a frozen hash; if
# a frozen artifact genuinely has to change, editing the hash here by hand is
# the deliberate act that records the decision.
#
# status     sha256                                                            path
"""


class Entry:
    __slots__ = ("status", "digest", "name")

    def __init__(self, status: str, digest: str, name: str):
        self.status = status
        self.digest = digest
        self.name = name

    def line(self) -> str:
        return f"{self.status:<10} {self.digest}  {self.name}"


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_checksums() -> list[Entry]:
    """Parse CHECKSUMS.txt, or return an empty list if it does not exist yet."""
    if not CHECKSUMS.exists():
        return []
    entries = []
    for lineno, raw in enumerate(CHECKSUMS.read_text().splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) != 3:
            raise SystemExit(
                f"{CHECKSUMS}:{lineno}: expected 'status sha256 path', got {raw!r}"
            )
        status, digest, name = parts
        if status not in STATUSES:
            raise SystemExit(
                f"{CHECKSUMS}:{lineno}: unknown status {status!r}; use one of {STATUSES}"
            )
        entries.append(Entry(status, digest, name))
    return entries


def published() -> dict[str, Path]:
    """Every published artifact, keyed by its path relative to the site root."""
    found = {}
    for pattern in PUBLISHED:
        for path in sorted(DOCS.glob(pattern)):
            found[str(path.relative_to(DOCS))] = path
    return dict(sorted(found.items()))


def verify() -> int:
    entries = {e.name: e for e in read_checksums()}
    on_disk = published()
    problems: list[str] = []

    if not entries:
        problems.append(
            f"{CHECKSUMS.relative_to(REPO_ROOT)} is missing or empty; "
            "run `make checksums-update` to record what is published"
        )

    for name, entry in sorted(entries.items()):
        path = on_disk.pop(name, None)
        if path is None:
            problems.append(f"{name}: recorded here but no longer on disk")
            continue
        actual = sha256(path)
        if actual == entry.digest:
            print(f"  ok   {entry.status:<10} {name}")
            continue
        if entry.status == "frozen":
            problems.append(
                f"{name}: FROZEN artifact has changed\n"
                f"      recorded {entry.digest}\n"
                f"      actual   {actual}\n"
                "      A published version does not get amended. Revert this file "
                "and put the change in the next version."
            )
        else:
            problems.append(
                f"{name}: candidate has changed since it was recorded; "
                "run `make checksums-update`"
            )

    for name in sorted(on_disk):
        problems.append(
            f"{name}: published but unrecorded; run `make checksums-update`"
        )

    if problems:
        print()
        for p in problems:
            print(f"  ERROR {p}")
        print(f"\n{len(problems)} problem(s)")
        return 1
    print(f"\n{len(entries)} artifact(s) match their recorded checksums")
    return 0


def update() -> int:
    entries = {e.name: e for e in read_checksums()}
    out: list[Entry] = []
    refused: list[str] = []

    for name, path in published().items():
        actual = sha256(path)
        previous = entries.pop(name, None)
        if previous is None:
            out.append(Entry("candidate", actual, name))
            print(f"  add        candidate  {name}")
            continue
        if previous.digest == actual:
            out.append(previous)
            print(f"  unchanged  {previous.status:<10} {name}")
            continue
        if previous.status == "frozen":
            # The one thing this tool will not do. Updating here would turn the
            # freeze into a formality that any `make` invocation can undo.
            refused.append(
                f"{name}: refusing to re-record a frozen artifact.\n"
                f"      recorded {previous.digest}\n"
                f"      actual   {actual}\n"
                "      Restore the published bytes (`git checkout` the file at "
                "its release tag) or start a new version."
            )
            out.append(previous)
            continue
        out.append(Entry("candidate", actual, name))
        print(f"  update     candidate  {name}")

    for name, entry in sorted(entries.items()):
        refused.append(f"{name}: recorded as {entry.status} but no longer on disk")

    if refused:
        print()
        for r in refused:
            print(f"  ERROR {r}")
        print(f"\n{len(refused)} problem(s); CHECKSUMS.txt not written")
        return 1

    CHECKSUMS.write_text(HEADER + "\n".join(e.line() for e in out) + "\n")
    print(f"\nwrote {CHECKSUMS.relative_to(REPO_ROOT)} ({len(out)} artifacts)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--update",
        action="store_true",
        help="re-record checksums; a frozen schema whose bytes moved is an error",
    )
    args = parser.parse_args(argv)
    return update() if args.update else verify()


if __name__ == "__main__":
    sys.exit(main())
