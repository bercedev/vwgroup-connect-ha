# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The changelog gate must gate, and must not fire on legitimate work.

``scripts/check_changelog_structure.py`` runs on every pull request to main, so
it is a required check, and that cuts both ways: a case it misses lets an entry
disappear, and a case it invents blocks work that is fine. The second is the
worse failure, because a check that fires on routine work gets a ``--skip``
added to it and then protects nothing for ever.

Both scripts shipped with ad-hoc verification and no tests in this suite. An
adversarial review of them produced twenty-six candidate defects, of which
these are the ones that reproduced independently. Each test below is written to
FAIL against the code as it was before its fix, which is the only way to know
the test is testing anything:

* an entry whose bold marker is never closed used to swallow the next entry,
  whose key then vanished — so check 3 reported a present entry as lost
* the writer's own gate accepted that unclosed marker, while its message
  claimed to exist in order to prevent exactly it
* the Unreleased heading was found by bare substring, so a sentence NAMING the
  heading became the block start and check 2 inspected a fragment
* a second Unreleased heading hid everything under it, which is what a merge
  resolved by keeping both sides leaves behind
* two entries sharing a title shared one dict slot, so deleting one of them
  passed — and this file already contains two such pairs
* a heading inside the entry text was written into the file verbatim, which
  either duplicates a section, opens a bogus parallel one, or splits the block
* a CR reaching ``--text`` was written through, and no gate in the project can
  see a lone CR

The regression half matters as much: every test that asserts a refusal is
paired with the real CHANGELOG.md still passing, because that file is nine
months and 985 entries of the shape these rules have to tolerate.
"""
from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_CHECKER = _ROOT / "scripts" / "check_changelog_structure.py"
_WRITER = _ROOT / "scripts" / "add_changelog_entry.py"
_REAL = _ROOT / "CHANGELOG.md"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


chk = _load(_CHECKER, "_cl_checker")
wri = _load(_WRITER, "_cl_writer")


def _run(script: Path, *args: str) -> tuple[int, str]:
    """Run a script in a subprocess, as CI does, and return (code, output).

    ``stdin=DEVNULL`` is not decoration. With stdin inherited, Windows can
    raise ``OSError: [WinError 6] the handle is invalid`` from
    ``DuplicateHandle`` when the parent's stdin is not a real handle, which it
    is not under every runner — and the failure lands on the subprocess call
    rather than on the assertion, so it reads like a broken checker. These
    scripts also accept ``--text -`` from stdin, so an inherited one is a
    second hazard: a test must never consume the runner's input.
    """
    res = subprocess.run(
        [sys.executable, "-I", str(script), *args],
        capture_output=True, encoding="utf-8", errors="replace", cwd=_ROOT,
        stdin=subprocess.DEVNULL,
    )
    return res.returncode, res.stdout + res.stderr


MIN = """# Changelog

## [Unreleased]

### Behoben / Fixed

- **First.** body ([#101](u)).

- **Second.** body ([#102](u)).

## [1.0.0] - 2026-01-01

### Fixed

- **Old.** body ([#1](u)).
"""


# ── the entry regex must not read one entry out of its neighbour's bytes ─────

class TestAnUnclosedBoldMarkerCannotSwallowTheNextEntry:
    """With an untempered ``.+?`` and re.S, "- **" with no closing "**"
    matches to the next entry's OPENING marker. finditer does not overlap, so
    that entry is never matched and its key disappears from a file that still
    contains it."""

    def test_the_following_entrys_key_survives(self):
        broken = MIN.replace(
            "- **First.** body ([#101](u)).",
            "- ** no closing marker here\n\n- **First.** body ([#101](u)).",
        )
        keys = chk.entry_keys(broken)
        assert "#101" in keys, (
            "the entry after a malformed one lost its key; check 3 would "
            "report it as deleted while it is still in the file"
        )
        assert "#102" in keys

    def test_the_lost_entry_check_does_not_fire_on_it(self, tmp_path: Path):
        base = tmp_path / "base.md"
        live = tmp_path / "live.md"
        base.write_text(MIN, encoding="utf-8", newline="\n")
        live.write_text(
            MIN.replace(
                "### Behoben / Fixed\n",
                "### Behoben / Fixed\n\n- ** unclosed\n",
            ),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live), "--baseline-file", str(base))
        assert code == 0, f"false loss reported:\n{out}"

    def test_a_genuine_deletion_is_still_caught(self, tmp_path: Path):
        """The counter-check. Without it the test above passes by disabling the
        check rather than by fixing it."""
        base = tmp_path / "base.md"
        live = tmp_path / "live.md"
        base.write_text(MIN, encoding="utf-8", newline="\n")
        live.write_text(
            MIN.replace("- **Second.** body ([#102](u)).\n\n", ""),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live), "--baseline-file", str(base))
        assert code == 1
        assert "#102" in out

    def test_a_wrapped_title_still_matches(self):
        """re.S is required, so the temper must not cost multi-line titles."""
        wrapped = MIN.replace(
            "- **First.** body ([#101](u)).",
            "- **A title that wraps\n  over two lines.** body ([#101](u)).",
        )
        assert "#101" in chk.entry_keys(wrapped)

    def test_the_real_file_is_unaffected(self):
        """985 entry lines, 819 keys. A temper that changed any of them would
        be a behaviour change on nine months of history."""
        text = _REAL.read_text(encoding="utf-8")
        assert len(chk._ENTRY.findall(text)) == sum(
            1 for ln in text.splitlines() if ln.startswith("- **")
        )


# ── the writer's gate must reject what the checker cannot see ────────────────

class TestTheWriterRejectsAnUnclosedBoldMarker:
    @pytest.mark.parametrize("bad", ["- ** no closing", "- **", "- **Title.*"])
    def test_it_refuses(self, tmp_path: Path, bad: str):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "fixed",
                         "--text", bad)
        assert code == 2, f"accepted {bad!r}:\n{out}"
        assert live.read_text(encoding="utf-8") == MIN, "it wrote anyway"

    def test_a_well_formed_entry_still_passes(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        code, _ = _run(_WRITER, "--file", str(live), "--section", "fixed",
                       "--text", "- **Fine.** body ([#999](u)).")
        assert code == 0
        assert "#999" in live.read_text(encoding="utf-8")


# ── a heading inside the entry text ──────────────────────────────────────────

class TestTheWriterRejectsHeadingsInsideAnEntry:
    """Three outcomes, only the first loud: a matching "### " duplicates the
    section, any other spelling opens a bogus parallel one that both scripts
    pass, and a "## [" splits the block so check 2 inspects less than it
    reports."""

    @pytest.mark.parametrize("bad", [
        "- **A.** x\n\n### Behoben / Fixed\n\n- **B.** y",
        "- **A.** x\n\n### Fixed\n\n- **B.** y",
        "- **A.** x\n\n## [9.9.9] - 2026-01-01\n\n- **B.** y",
    ])
    def test_it_refuses(self, tmp_path: Path, bad: str):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "fixed",
                         "--text", bad)
        assert code == 2, f"accepted a heading inside the entry:\n{out}"
        assert live.read_text(encoding="utf-8") == MIN

    def test_a_sub_heading_is_allowed(self, tmp_path: Path):
        """Four real entries use "#### " sub-headings. A rule written as
        ``^#{1,6}`` would have rejected them, which is why the two existing
        patterns are reused instead of a new one."""
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "fixed",
                         "--text", "- **A.** x\n\n#### Detail\n\nmore ([#998](u)).")
        assert code == 0, out
        assert "#### Detail" in live.read_text(encoding="utf-8")

    def test_no_real_entry_would_be_rejected(self):
        """The false-positive cost, measured rather than asserted: the rule is
        applied to every entry body in the shipped file.

        An entry's body ends at the next entry OR the next heading of either
        level. Slicing only to the next entry would run the body across the
        following section heading and make this test fail on the file as it is
        — which is a wrong instrument, not a defect: nothing inserts an entry
        that reaches past a heading, because that is what is being refused.
        """
        text = _REAL.read_text(encoding="utf-8")
        starts = [m.start() for m in re.finditer(r"^- \*\*", text, re.M)]
        bodies = []
        for i, s in enumerate(starts):
            hard = len(text) if i + 1 >= len(starts) else starts[i + 1]
            nxt = re.compile(r"^(?:### |## )", re.M).search(text, s + 3)
            bodies.append(text[s:min(hard, nxt.start() if nxt else hard)])
        # Self-consistent rather than a hardcoded count: one body per entry
        # line. A literal number here fails the first time anyone adds an
        # entry, which is what happened — and it would have read as "the rule
        # rejects a real entry" rather than "the test counted yesterday's file".
        assert len(bodies) == sum(
            1 for ln in text.splitlines() if ln.startswith("- **")
        ), f"instrument drifted: {len(bodies)} bodies"
        rejected = [
            b for b in bodies
            if wri._SECTION.search(b) or wri._RELEASE.search(b)
        ]
        assert rejected == [], f"{len(rejected)} real entries would be refused"


# ── the Unreleased heading is a heading, not a substring ─────────────────────

class TestTheHeadingIsAnchoredToColumnZero:
    PROSE = MIN.replace(
        "# Changelog\n",
        '# Changelog\n\nEntries go under the "## [Unreleased]" heading below.\n',
    )

    def test_prose_naming_the_heading_is_not_the_block_start(self):
        block = chk.unreleased_block(self.PROSE)
        assert block.startswith("## [Unreleased]")
        assert "### Behoben / Fixed" in block, (
            "the block was cut short at the real heading, so check 2 inspected "
            "a fragment and could not see a duplicate in it"
        )

    def test_a_duplicate_is_still_found_below_such_prose(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            self.PROSE.replace(
                "- **Second.** body ([#102](u)).",
                "### Behoben / Fixed\n\n- **Second.** body ([#102](u)).",
            ),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 1, f"the duplicate heading was missed:\n{out}"
        assert "Behoben / Fixed" in out

    def test_the_writer_inserts_inside_the_block(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(self.PROSE, encoding="utf-8", newline="\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "fixed",
                         "--text", "- **New.** body ([#997](u)).")
        assert code == 0, out
        got = live.read_text(encoding="utf-8")
        assert got.index("## [Unreleased]") < got.index("#997"), (
            "the entry was written above the heading"
        )

    def test_two_unreleased_headings_are_reported(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            MIN.replace("## [1.0.0]", "## [Unreleased]\n\n### Behoben / Fixed\n\n- **X.** y ([#103](u)).\n\n## [1.0.0]"),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 1
        assert "headings" in out


# ── two entries, one title ───────────────────────────────────────────────────

class TestCollidingTitlesOccupyTwoSlots:
    """Some titles are deliberately reused release to release, and the shipped
    file already contains two colliding pairs. One dict slot for both means a
    deletion leaves the key in place and check 3 passes it."""

    SAME = MIN.replace("- **Second.** body ([#102](u)).", "- **First.** other body.")
    SAME = SAME.replace("- **First.** body ([#101](u)).", "- **First.** one body.")

    def test_both_are_keyed(self):
        keys = chk.entry_keys(self.SAME)
        titled = [k for k in keys if k.startswith("title:First.")]
        assert len(titled) == 2, f"two entries collapsed into {titled}"

    @pytest.mark.parametrize("drop", ["- **First.** one body.", "- **First.** other body."])
    def test_deleting_either_is_caught(self, tmp_path: Path, drop: str):
        base = tmp_path / "base.md"
        live = tmp_path / "live.md"
        base.write_text(self.SAME, encoding="utf-8", newline="\n")
        live.write_text(self.SAME.replace(drop + "\n", ""), encoding="utf-8", newline="\n")
        code, out = _run(_CHECKER, "--file", str(live), "--baseline-file", str(base))
        assert code == 1, f"a deletion passed:\n{out}"

    def test_reordering_them_is_not_a_loss(self, tmp_path: Path):
        """The counter-check: numbering must not turn a legitimate reorder into
        a reported deletion."""
        base = tmp_path / "base.md"
        live = tmp_path / "live.md"
        base.write_text(self.SAME, encoding="utf-8", newline="\n")
        live.write_text(
            self.SAME.replace(
                "- **First.** one body.\n\n- **First.** other body.",
                "- **First.** other body.\n\n- **First.** one body.",
            ),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live), "--baseline-file", str(base))
        assert code == 0, out

    def test_the_real_file_has_the_two_known_pairs(self):
        keys = chk.entry_keys(_REAL.read_text(encoding="utf-8"))
        numbered = sorted(k for k in keys if re.search(r" \[\d+\]$", k))
        assert len(numbered) == 2, numbered


# ── line endings and whitespace ──────────────────────────────────────────────

class TestACarriageReturnNeverReachesTheFile:
    """No gate in this project can see a lone CR: the checker reads with
    universal newlines and git normalises the diff. So the writer is the only
    place it can be stopped."""

    def test_a_cr_in_text_is_normalised(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\r\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "fixed",
                         "--text", "- **A.** one\r\n\r\n  two ([#996](u)).")
        assert code == 0, out
        raw = live.read_bytes()
        assert b"\r\r\n" not in raw
        assert raw.count(b"\r") == raw.count(b"\r\n"), "a lone CR survived"

    def test_crlf_is_preserved(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\r\n")
        _run(_WRITER, "--file", str(live), "--section", "fixed",
             "--text", "- **A.** b ([#995](u)).")
        raw = live.read_bytes()
        assert raw.count(b"\n") == raw.count(b"\r\n"), "CRLF file turned into LF"

    def test_lf_is_preserved(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        _run(_WRITER, "--file", str(live), "--section", "fixed",
             "--text", "- **A.** b ([#994](u)).")
        assert b"\r" not in live.read_bytes()

    def test_a_new_section_leaves_no_double_blank_line(self, tmp_path: Path):
        """The create-section branch added its own trailing newline on top of
        the block's preserved one.

        ``--section security`` is required to reach that branch: it sorts after
        the section already present, so the heading is appended at the END of
        the block. A section that sorts BEFORE an existing one takes the
        insert-before branch, which never had the defect — so the same test
        written with ``added`` passes with the bug restored and proves nothing.
        A mutation run is what caught that.
        """
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "security",
                         "--text", "- **A.** b ([#993](u)).")
        assert code == 0, out
        got = live.read_text(encoding="utf-8")
        assert "### Sicherheit / Security" in got
        assert "\n\n\n" not in got, "left a double blank line"

    def test_an_earlier_section_is_inserted_before_the_existing_one(self, tmp_path: Path):
        """The other branch, so the test above cannot be read as covering both."""
        live = tmp_path / "CHANGELOG.md"
        live.write_text(MIN, encoding="utf-8", newline="\n")
        code, out = _run(_WRITER, "--file", str(live), "--section", "added",
                         "--text", "- **A.** b ([#989](u)).")
        assert code == 0, out
        got = live.read_text(encoding="utf-8")
        assert got.index("### Hinzugefügt / Added") < got.index("### Behoben / Fixed")
        assert "\n\n\n" not in got

    def test_a_file_without_a_final_newline_gains_exactly_one(self, tmp_path: Path):
        """``--section security`` on purpose: it sorts AFTER fixed, so the new
        section is appended at the end of the block, which is the branch whose
        trailing newline changed. ``added`` would be inserted BEFORE the
        existing section and never touch the end of the file."""
        live = tmp_path / "CHANGELOG.md"
        live.write_bytes(
            "# Changelog\n\n## [Unreleased]\n\n### Behoben / Fixed\n\n- **A.** b ([#1](u))."
            .encode()
        )
        code, out = _run(_WRITER, "--file", str(live), "--section", "security",
                         "--text", "- **C.** d ([#992](u)).")
        assert code == 0, out
        raw = live.read_bytes()
        assert raw.endswith(b".\n"), raw[-40:]
        assert not raw.endswith(b".\n\n")


# ── the insert-only guarantee, and the original three checks ─────────────────

class TestTwoSpellingsOfOneSectionAreOneSection:
    """This file's history carries 176 English-only headings and 5 bilingual
    ones, and its own header switched the convention to English-primary at
    v2.4.1. So a merge between a branch writing "### Fixed" and one writing
    "### Behoben / Fixed" leaves two headings for one section — and a check
    comparing the literal strings counts two unique names and passes."""

    @pytest.mark.parametrize("other", [
        "### Fixed", "### Behoben", "### Fixed / Behoben", "###\tBehoben / Fixed",
        "  ### Behoben / Fixed",
    ])
    def test_a_second_spelling_is_a_duplicate(self, tmp_path: Path, other: str):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            MIN.replace("- **Second.**", f"{other}\n\n- **Second.**"),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 1, f"{other!r} passed as a separate section:\n{out}"

    def test_an_unknown_heading_is_compared_literally(self, tmp_path: Path):
        """The script does not police section vocabulary, so it must not guess
        that two unknown names mean one thing. "Docs" and "Thanks" are real
        headings in this file."""
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            MIN.replace("- **Second.**", "### Thanks\n\n- **Second.**"),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 0, out

    def test_a_sub_heading_is_not_a_section(self):
        """Four real entries use "#### " inside the entry body. Counting those
        as sections would conflate two conventions the file keeps apart."""
        assert chk._SECTION.search("#### Read switches") is None

    def test_both_orders_resolve_to_one_slot(self):
        assert chk.heading_slot("Behoben / Fixed") == chk.heading_slot("Fixed / Behoben")
        assert chk.heading_slot("Fixed") == chk.heading_slot("Behoben")
        assert chk.heading_slot("Docs") != chk.heading_slot("Thanks")

    def test_the_writer_knows_both_orders_too(self):
        """Otherwise the writer opens a second heading beside a section that is
        already there — the duplicate the checker refuses, produced by the tool
        meant to prevent it."""
        for spelling in ("Behoben / Fixed", "Fixed / Behoben", "Fixed", "Behoben"):
            assert wri.ALIASES[spelling.casefold()] == "fixed"

    def test_the_writer_adds_to_an_english_only_section(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            MIN.replace("### Behoben / Fixed", "### Fixed / Behoben"),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_WRITER, "--file", str(live), "--section", "fixed",
                         "--text", "- **New.** b ([#990](u)).")
        assert code == 0, out
        got = live.read_text(encoding="utf-8")
        assert got.count("### Fixed / Behoben") == 1
        assert "### Behoben / Fixed" not in got, "it opened a second heading"


class TestTheCiJobGatesOnlyWhatItCan:
    """Encodes the decision rather than relying on someone remembering it.

    The lost-entry comparison was measured against this file's own history,
    each commit against its real parent: it would have refused five of the last
    80 revisions, and none of the five had lost anything — every one was an
    entry whose bold title had been rewritten, one of them by inserting an
    issue number into the title, which RELEASE_PROCESS.md asks for. Zero real
    losses caught in that window; the duplicate-heading check caught three with
    no false alarm. A required check that refuses legitimate work and has never
    caught damage gets a ``--skip`` added to it and then protects nothing, so it
    must not be wired into CI — and the next person to reach for ``--base``
    there should be stopped by a failing test rather than by a comment.
    """

    WF = _ROOT / ".github" / "workflows" / "changelog_check.yml"

    def test_the_workflow_runs_the_checker(self):
        assert "check_changelog_structure.py" in self.WF.read_text(encoding="utf-8")

    def test_it_does_not_pass_a_baseline(self):
        text = self.WF.read_text(encoding="utf-8")
        invocations = [
            ln for ln in text.splitlines()
            if "check_changelog_structure.py" in ln and not ln.lstrip().startswith("#")
        ]
        assert invocations, "the checker is not invoked at all"
        for ln in invocations:
            assert "--base" not in ln, (
                "the lost-entry comparison is back in CI; read the script's "
                "docstring before changing this"
            )

    def test_the_structure_step_is_guarded(self):
        """A step with no guard runs after the docs-only ``exit 0``, which ends
        that step and not the job — so a documentation-only pull request would
        be refused for damage already on main. Three of the last 80 revisions
        of CHANGELOG.md on main were in exactly that state."""
        import yaml  # noqa: PLC0415

        job = yaml.safe_load(self.WF.read_text(encoding="utf-8"))["jobs"]["check"]
        step = next(
            s for s in job["steps"]
            if "check_changelog_structure.py" in (s.get("run") or "")
        )
        assert step.get("if"), "the structure step has no `if:` guard"
        producer = next(s for s in job["steps"] if s.get("id") == "scope")
        assert "GITHUB_OUTPUT" in producer["run"]
        assert producer["run"].index("GITHUB_OUTPUT") < producer["run"].index("exit 0"), (
            "the guard's input is published after the early exit, so it would "
            "be empty on exactly the pull requests it has to judge"
        )


class TestTheOriginalChecksStillHold:
    def test_the_real_file_passes(self):
        code, out = _run(_CHECKER)
        assert code == 0, out

    def test_a_conflict_marker_is_refused(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text("<<<<<<< HEAD\n" + MIN, encoding="utf-8", newline="\n")
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 1
        assert "conflict marker" in out

    def test_a_duplicate_section_is_refused(self, tmp_path: Path):
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            MIN.replace(
                "- **Second.**",
                "### Behoben / Fixed\n\n- **Second.**",
            ),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 1
        assert "2 times" in out

    def test_a_release_cut_is_not_a_loss(self, tmp_path: Path):
        """Cutting a release moves every Unreleased entry into a version block.
        A rule phrased as "nothing leaves Unreleased" would fail every release
        pull request of a required check."""
        base = tmp_path / "base.md"
        live = tmp_path / "live.md"
        base.write_text(MIN, encoding="utf-8", newline="\n")
        live.write_text(
            MIN.replace("## [Unreleased]", "## [Unreleased]\n\n## [1.1.0] - 2026-02-02"),
            encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live), "--baseline-file", str(base))
        assert code == 0, out

    def test_a_missing_unreleased_heading_says_it_is_refused(self, tmp_path: Path):
        """The message used to say a release in progress "is fine" while the
        function put it in ``problems`` and returned 1. A check whose text
        contradicts its own exit code teaches the reader to ignore the text."""
        live = tmp_path / "CHANGELOG.md"
        live.write_text(
            MIN.replace("## [Unreleased]\n", ""), encoding="utf-8", newline="\n",
        )
        code, out = _run(_CHECKER, "--file", str(live))
        assert code == 1
        assert "is fine" not in out, "it refuses the file while calling it fine"
        assert "no " in out.lower() and "Unreleased" in out

    def test_an_unreadable_base_ref_refuses_rather_than_skipping(self):
        code, out = _run(_CHECKER, "--base", "no-such-ref-exists-here")
        assert code == 2, out
        assert "Refusing" in out

    def test_the_writer_cannot_delete_a_line(self):
        """The insert-only assertion is the writer's whole contract, so it is
        asserted rather than intended. Mutating insert() to drop a line must
        abort the write."""
        good = wri.insert
        try:
            def sabotage(text: str, slot: str, entry: str) -> str:
                out = good(text, slot, entry)
                lines = out.splitlines(keepends=True)
                del lines[4]
                return "".join(lines)
            wri.insert = sabotage
            with pytest.raises(SystemExit) as excinfo:
                wri.main(["--file", str(_REAL), "--section", "fixed",
                          "--text", "- **X.** y ([#991](u))."])
            assert "Refusing to write" in str(excinfo.value)
        finally:
            wri.insert = good
