#!/usr/bin/env python3
# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Refuse a CHANGELOG that lost an entry, duplicated a heading, or kept a marker.

The existing CI job checks that CHANGELOG.md was *touched*. That is a different
question from whether the result is intact, and twice in one day the difference
cost an entry:

* A contributor's branch opened its own ``### Behoben / Fixed`` while main
  already had one. Merging produced the heading twice, and nothing failed.
* Two branches each rewrote the whole ``## [Unreleased]`` block, so git's hunks
  overlapped and resolving the conflict by taking one side dropped main's entire
  ``### Hinzugefügt / Added`` section — one entry gone, measured as zero
  occurrences of its issue number in the result. The file *was* modified, so the
  existing check passed it.

Both are mechanical to catch, which is why this exists: the comparison used to
be done by hand after a merge, and a check done by hand is the one that is
missing the next time.

THREE CHECKS, ONE PER REAL INCIDENT

1. No conflict markers, anywhere in the file.
2. No duplicate ``###`` heading inside ``## [Unreleased]``.
3. Against a baseline: no entry that exists in the baseline has vanished from
   the file.

Deliberately NOT checked: section order and section vocabulary. The first draft
of this script enforced Keep a Changelog's order and its six section names, and
produced 55 kB of complaints about nine months of released history — this
project has used 31 distinct section headings, among them "Docs", "Thanks" and
"What is new vs v2.7.4", and the file states that past entries are preserved as
written. Imposing a vocabulary the project does not follow is inventing a rule,
not mechanising one, and a check that fires on history gets switched off.

Check 2 is scoped to ``## [Unreleased]`` for the same reason: that is where
edits land and where the duplicate came from. Released blocks are history.

Check 3 follows entries across the WHOLE file rather than within one block, and
that is deliberate. Cutting a release moves every Unreleased entry into a new
version block; a rule phrased as "nothing leaves Unreleased" would fail every
release pull request, and a check that fails a routine operation gets a
``--skip`` added to it and then protects nothing for ever. An entry that moved
is still present. Only one that vanishes from the file is an error.

Entries are identified by the issue numbers they cite, because that survives the
rewording and re-wrapping an entry normally gets. An entry citing no issue is
identified by its bold title, with whitespace collapsed.

Usage:
    python scripts/check_changelog_structure.py
    python scripts/check_changelog_structure.py --base origin/main
    python scripts/check_changelog_structure.py --file CHANGELOG.md --base HEAD~1
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

_MARKERS = ("<<<<<<<", "=======", ">>>>>>>")
_RELEASE = re.compile(r"^## +\[.+?\]", re.M)
_SECTION = re.compile(r"^### +(.+?)\s*$", re.M)
#: An entry opens with "- **" and its bold title may wrap over several lines.
_ENTRY = re.compile(r"^- \*\*(.+?)\*\*", re.M | re.S)
_ISSUE = re.compile(r"#(\d{3,6})")
_UNRELEASED = "## [Unreleased]"


def _p(text: object) -> str:
    """ASCII-safe for stdout.

    Windows consoles encode stdout as cp1252, and this script prints changelog
    text back at the reader — section headings carry umlauts and the file's own
    sponsor line carries an emoji. Printing either raised UnicodeEncodeError and
    took the run down, which on a checker is the worst failure mode: it would
    have passed in CI on Linux and been unusable on the machine where the
    changelog is actually edited.
    """
    return str(text).encode("ascii", "replace").decode("ascii")


def unreleased_block(text: str) -> str:
    """The ``## [Unreleased]`` block, or "" when the file has none."""
    try:
        start = text.index(_UNRELEASED)
    except ValueError:
        return ""
    nxt = _RELEASE.search(text, start + len(_UNRELEASED))
    return text[start:nxt.start()] if nxt else text[start:]


def entry_keys(text: str) -> dict[str, str]:
    """Identity -> the title it came from, for every entry in the text.

    An entry is keyed by each issue it cites, so one entry covering two issues
    is tracked under both: losing it should name both.
    """
    keys: dict[str, str] = {}
    for m in _ENTRY.finditer(text):
        tail = text[m.start():]
        nxt = re.search(r"^(?:- \*\*|### |## )", tail[3:], re.M)
        body = tail[: nxt.start() + 3] if nxt else tail
        title = re.sub(r"\s+", " ", m.group(1)).strip()
        issues = sorted(set(_ISSUE.findall(body)))
        if issues:
            for num in issues:
                keys[f"#{num}"] = title
        else:
            keys[f"title:{title[:90]}"] = title
    return keys


def _git_show(ref: str, path: str) -> str | None:
    try:
        r = subprocess.run(
            ["git", "show", f"{ref}:{path}"],
            capture_output=True, encoding="utf-8", errors="replace", check=True,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None
    return r.stdout


def check(text: str, baseline: str | None) -> list[str]:
    problems: list[str] = []

    for num, line in enumerate(text.splitlines(), 1):
        for marker in _MARKERS:
            if line.startswith(marker):
                problems.append(
                    f"line {num}: an unresolved conflict marker ({marker}) is "
                    "still in the file"
                )

    block = unreleased_block(text)
    if not block:
        problems.append(
            f'no "{_UNRELEASED}" block - a release in progress is fine, but '
            "check this is intended"
        )
    else:
        found = [m.group(1) for m in _SECTION.finditer(block)]
        for name, count in Counter(found).items():
            if count > 1:
                problems.append(
                    f'Unreleased has the section "{name}" {count} times. Two of '
                    "the same heading in one release is what a merge leaves "
                    "behind; fold the entries into one section."
                )

    if baseline is not None:
        was, now = entry_keys(baseline), entry_keys(text)
        for key in sorted(set(was) - set(now)):
            problems.append(
                f"the entry for {key} is in the baseline and gone from this "
                f"file ({was[key][:70]!r}). An entry is the record that the "
                "work happened; it may move between release blocks, but it may "
                "not disappear."
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="CHANGELOG.md")
    ap.add_argument(
        "--base", default=None,
        help="a git ref to compare entries against, e.g. origin/main",
    )
    ap.add_argument(
        "--baseline-file", default=None,
        help="a file to compare entries against, instead of a git ref. For "
             "testing this checker, and for comparing two files directly.",
    )
    args = ap.parse_args(argv)

    path = Path(args.file)
    if not path.exists():
        print(_p(f"not found: {path}"))
        return 2
    text = path.read_text(encoding="utf-8")

    baseline = None
    if args.baseline_file:
        bp = Path(args.baseline_file)
        if not bp.exists():
            print(_p(f"baseline file not found: {bp}"))
            return 2
        baseline = bp.read_text(encoding="utf-8")
    elif args.base:
        baseline = _git_show(args.base, args.file)
        if baseline is None:
            # NOT a warning-and-continue. A mistyped ref, a shallow clone or a
            # path outside the repository would otherwise switch off the one
            # check that catches a lost entry, and switch it off silently - in
            # CI, where nobody is reading the log of a job that passed.
            print(_p(
                f"Refusing: --base {args.base} was asked for, but {args.file} "
                f"could not be read at that ref. The lost-entry check cannot "
                "run, and skipping it quietly is how it would stop protecting "
                "anything. Fix the ref, or drop --base to run the structure "
                "checks alone."
            ))
            return 2

    problems = check(text, baseline)
    if not problems:
        sections = len(_SECTION.findall(unreleased_block(text)))
        entries = len(entry_keys(text))
        extra = "" if baseline else " (no baseline: lost-entry check skipped)"
        print(_p(
            f"CHANGELOG.md intact: {entries} identified entries, "
            f"{sections} unique section(s) in Unreleased{extra}."
        ))
        return 0

    print("Refusing: CHANGELOG.md is not intact.\n")
    for problem in problems:
        print(_p(f"  - {problem}"))
    print(f"\n{len(problems)} problem(s).")
    return 1


if __name__ == "__main__":
    sys.exit(main())
