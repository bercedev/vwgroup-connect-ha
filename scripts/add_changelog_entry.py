#!/usr/bin/env python3
# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Insert ONE entry into the Unreleased block, touching nothing else.

This exists because of how two entries were nearly lost in one day. Not one of
the scripts that wrote them was wrong about the content; they were wrong about
the SCOPE of their write. Each rebuilt the whole ``## [Unreleased]`` block from
its own idea of what belonged there, so when two branches had both done that,
git saw one large overlapping change on each side rather than two small
additions in different places. Resolving the conflict then meant choosing one
side's whole block, and the other side's entries went with it.

So this script never rewrites the block. It inserts the new entry at the end of
its section — creating the section only if it is missing, in Keep a Changelog's
order — and leaves every other byte alone. Two branches adding to two different
sections then produce two hunks that do not touch, and git merges them without
asking. Two branches adding to the SAME section still conflict, but over two
lines whose union is obviously correct, instead of over forty lines where one
side has to lose.

It also refuses to be the thing it is replacing: before writing, it compares
the result against the input and aborts if a single original line was removed
or altered. Insert-only is the entire point, so it is asserted rather than
intended. scripts/check_changelog_structure.py is the net under all of this;
this script is meant to make that net never fire.

Usage:
    python scripts/add_changelog_entry.py --section fixed --text-file entry.md
    python scripts/add_changelog_entry.py --section added --text "- **T.** b (#1)"
    ... | python scripts/add_changelog_entry.py --section security --text -
"""
from __future__ import annotations

import argparse
import difflib
import re
import sys
from pathlib import Path

_UNRELEASED = "## [Unreleased]"
#: Anchored to column 0 for the same reason as in the checker: a sentence that
#: merely names the heading is not the heading. Unanchored, prose above the real
#: block becomes the block's start, and the entry is then written ABOVE the
#: heading instead of inside it.
_UNRELEASED_RE = re.compile(r"^## +\[Unreleased\]", re.M)
_RELEASE = re.compile(r"^## +\[.+?\]", re.M)
_SECTION = re.compile(r"^### +(.+?)\s*$", re.M)

#: Keep a Changelog's order, in this file's bilingual spelling. The spelling is
#: taken from the headings the recent releases actually use, not invented: the
#: file keeps older English-only headings as written, and a new section must not
#: silently introduce a seventh way of saying "Fixed".
SECTIONS: dict[str, str] = {
    "added": "Hinzugefügt / Added",
    "changed": "Geändert / Changed",
    "deprecated": "Veraltet / Deprecated",
    "removed": "Entfernt / Removed",
    "fixed": "Behoben / Fixed",
    "security": "Sicherheit / Security",
}
#: An existing heading in either spelling maps back to its slot, so an entry
#: lands in the section that is already there instead of opening a second one
#: next to it — which is exactly the duplicate the checker refuses.
ALIASES: dict[str, str] = {}
for _slot, _heading in SECTIONS.items():
    german, english = (p.strip() for p in _heading.split("/"))
    for _spelling in (
        _heading,                    # "Behoben / Fixed"
        f"{english} / {german}",     # "Fixed / Behoben" — the EN-primary order
        german,                      # "Behoben"
        english,                     # "Fixed"
    ):
        ALIASES[_spelling.casefold()] = _slot
    ALIASES[_slot] = _slot
# Both orders are deliberate, not defensive. This file's own header switched the
# convention to English-primary at v2.4.1, and its history carries 176 English-
# only headings against 5 bilingual ones, so the heading an existing section
# wears is genuinely not predictable. A spelling that does not alias to its slot
# would make this script open a SECOND heading for a section already present —
# the exact duplicate the checker refuses, produced by the tool meant to prevent
# it.

_ORDER = list(SECTIONS)


def _p(text: object) -> str:
    """ASCII-safe for stdout.

    The headings carry umlauts, and a cp1252 console takes the whole run down
    on printing one — which on a writer means an entry half written and a
    traceback where the message should be.
    """
    return str(text).encode("ascii", "replace").decode("ascii")


def block_bounds(text: str) -> tuple[int, int]:
    m = _UNRELEASED_RE.search(text)
    if m is None:  # main() refuses before this; belt and braces for callers
        raise ValueError(f'no "{_UNRELEASED}" heading at the start of a line')
    # Search from the END of the heading: _RELEASE also matches
    # "## [Unreleased]", so starting any earlier finds this heading again and
    # makes the block empty.
    nxt = _RELEASE.search(text, m.end())
    return m.start(), (nxt.start() if nxt else len(text))


def insert(text: str, slot: str, entry: str) -> str:
    """Return text with entry inserted into slot. Inserts only."""
    b0, b1 = block_bounds(text)
    block = text[b0:b1]

    present: list[tuple[int, str]] = []  # (offset in block, slot or raw heading)
    for m in _SECTION.finditer(block):
        present.append((m.start(), ALIASES.get(m.group(1).casefold(), m.group(1))))

    entry = entry.strip("\n")

    for i, (_pos, name) in enumerate(present):
        if name != slot:
            continue
        # End of this section: the next heading in the block, or the block's end.
        stop = present[i + 1][0] if i + 1 < len(present) else len(block)
        cut = len(block[:stop].rstrip())  # last non-space byte of the section
        return text[: b0 + cut] + "\n\n" + entry + text[b0 + cut :]

    # Section missing: open it before the first section that sorts after it.
    want = _ORDER.index(slot)
    for pos, name in present:
        if name in _ORDER and _ORDER.index(name) > want:
            head = f"### {SECTIONS[slot]}\n\n{entry}\n\n"
            return text[: b0 + pos] + head + text[b0 + pos :]

    cut = len(block.rstrip())
    rest = text[b0 + cut :]
    # No trailing "\n" on the tail: ``rest`` already carries the block's
    # preserved trailing whitespace, so adding one leaves a double blank line
    # before the next release heading. The other two branches rely on that same
    # preserved tail, which is why neither produces the artefact. The
    # ``or "\n"`` matters: when Unreleased is the last block and the file has no
    # final newline, ``rest`` is empty and today's code normalises it to one.
    tail = f"\n\n### {SECTIONS[slot]}\n\n{entry}"
    return text[: b0 + cut] + tail + (rest or "\n")


def assert_insert_only(before: str, after: str) -> None:
    """Abort unless every original line survives, in order, byte for byte."""
    lost = [
        line
        for line in difflib.ndiff(before.splitlines(), after.splitlines())
        if line.startswith("- ")
    ]
    if lost:
        raise SystemExit(
            _p(
                "Refusing to write: this would have removed or altered "
                f"{len(lost)} existing line(s), the first being "
                f"{lost[0][2:][:80]!r}. Inserting is the only thing this script "
                "is allowed to do — that a rewrite loses entries is why it exists."
            )
        )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--file", default="CHANGELOG.md")
    ap.add_argument("--section", required=True, help="one of: " + ", ".join(_ORDER))
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--text", help='the entry, opening "- **". "-" reads stdin.')
    src.add_argument("--text-file", help="a file holding the entry")
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="print the diff and write nothing",
    )
    args = ap.parse_args(argv)

    slot = ALIASES.get(args.section.casefold())
    if slot is None:
        print(_p(f"unknown section {args.section!r}; use one of: {', '.join(_ORDER)}"))
        return 2

    if args.text_file:
        entry = Path(args.text_file).read_text(encoding="utf-8")
    elif args.text == "-":
        entry = sys.stdin.read()
    else:
        entry = args.text

    # The --text-file and stdin paths get universal newlines for free; --text
    # does not, so a CR arriving through a shell variable (PowerShell's
    # Get-Content -Raw is the obvious producer here) was written into the file
    # verbatim. Against a CRLF working copy that yields literal "\r\r\n" bytes,
    # which no gate in this project can see: the checker reads with universal
    # newlines, so it cannot observe a CR at all, and git normalises the diff.
    # One silent, permanent corruption. This makes --text behave like the two
    # paths that were already right rather than adding a rule; the second
    # replace is load-bearing for the lone trailing CR that strip("\n") leaves.
    entry = entry.replace("\r\n", "\n").replace("\r", "\n")

    # Tightened from ``startswith("- **")``, which accepted an UNCLOSED bold
    # marker — and an entry with no closing "**" is exactly what the checker
    # cannot see, so the gate's own message ("invisible to the check that would
    # notice it going missing") was describing something it let through. re.S is
    # required: a real title may wrap over several lines.
    if not re.match(r"- \*\*.+?\*\*", entry.strip(), re.S):
        print(
            _p(
                'Refusing: an entry opens with "- **" and a bold title that is '
                "closed again. Every entry in this file does, and the structure "
                "checker identifies entries by it — one written another way is "
                "invisible to the check that would notice it going missing."
            )
        )
        return 2

    # An entry is written into the block verbatim, so a heading inside it is a
    # heading in the file. Three distinct outcomes, and only the first is loud:
    # a "### " matching the section being added to duplicates that heading and
    # the checker refuses the result; a "### " in any OTHER spelling opens a
    # bogus parallel section that both scripts pass, because the checker
    # deliberately does not police section vocabulary; and a "## [" splits the
    # Unreleased block in two, so everything after it falls outside the block
    # and check 2 silently inspects less than it reports.
    #
    # This reuses the two patterns already compiled above rather than inventing
    # a rule: measured against every entry body in the file, ``^#{1,6}`` would
    # reject four legitimate entries that use "#### " sub-headings, while these
    # two reject none of 985.
    if _SECTION.search(entry) or _RELEASE.search(entry):
        print(
            _p(
                'Refusing: the entry text carries a "### " section heading or a '
                '"## [...]" release heading. The entry goes into the block '
                "verbatim, so a heading inside it opens a second section beside "
                "the one being added to, or splits the Unreleased block in two. "
                'Add one entry per run; a multi-part entry uses "#### ".'
            )
        )
        return 2

    path = Path(args.file)
    # Read the bytes first, to learn the file's own line ending. On a Windows
    # working copy with core.autocrlf=true this file is CRLF while the blob is
    # LF, so writing LF unconditionally would flip every one of its several
    # thousand line endings. Git hides that again on commit, which is exactly
    # the problem: every other reader — a plain diff, a review tool, this
    # project's own --baseline-file comparison — would see a wholly rewritten
    # file, and a wholly rewritten file is where one lost entry hides.
    raw = path.read_bytes()
    crlf = raw.count(b"\r\n")
    newline = "\r\n" if crlf > (raw.count(b"\n") - crlf) else "\n"
    before = raw.decode("utf-8").replace("\r\n", "\n")
    if not _UNRELEASED_RE.search(before):
        print(_p(f'Refusing: {path} has no "{_UNRELEASED}" block to add to.'))
        return 2

    after = insert(before, slot, entry)
    assert_insert_only(before, after)

    if args.dry_run:
        for line in difflib.unified_diff(
            before.splitlines(),
            after.splitlines(),
            fromfile=str(path),
            tofile="(would write)",
            lineterm="",
            n=2,
        ):
            print(_p(line))
        return 0

    path.write_text(after, encoding="utf-8", newline=newline)
    added = len(after.splitlines()) - len(before.splitlines())
    print(_p(f'{path}: {added} line(s) inserted into "{SECTIONS[slot]}".'))
    return 0


if __name__ == "__main__":
    sys.exit(main())
