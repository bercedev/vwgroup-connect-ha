# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The CHANGELOG union merge driver, and the one thing it does not do.

Merging seven pull requests on 2026-10-20 took **thirteen** CHANGELOG.md
conflict resolutions, every one of them identical: both sides had appended one
entry at the same anchor, and "keep both" was always right. There was not a
single code conflict in the batch — all the friction came from one file and
from `strict` status checks putting each remaining branch behind main again.

``.gitattributes`` now sets ``CHANGELOG.md merge=union``, which resolves that
shape with no markers and no human. It is only safe because of a property the
repository already has: ``add_changelog_entry.py`` is insert-only, so the two
sides of such a merge are always *additions in the same place* rather than
competing edits. If entries ever start being rewritten in place, the driver
has to go — and the test below says so where someone will see it.

What the driver does **not** do, measured rather than assumed: it does not
introduce the release-time mis-placement hazard it is sometimes blamed for. A
release pull request renames ``[Unreleased]`` to a version; a feature branch
appends to ``[Unreleased]``; those two do not overlap textually, so git merges
them cleanly and the entry lands under the released version — **with or
without** the driver, identically, because there is no conflict for a driver
to resolve. That hazard is older than this file and is documented in
RELEASE_PROCESS.md instead of being pinned here, because no test can see it
without a baseline.
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import tempfile

import pytest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_ATTRS = _ROOT / ".gitattributes"


def _git(*args: str, cwd: pathlib.Path) -> subprocess.CompletedProcess[str]:
    # stdin=DEVNULL is required, not tidiness: under pytest's capture on
    # Windows, a subprocess with no explicit stdin fails to duplicate the
    # handle and raises "OSError: [WinError 6] The handle is invalid" before
    # git ever runs. encoding is explicit because this machine's default is
    # cp1252 and the changelog is UTF-8.
    return subprocess.run(
        ["git", *args], cwd=cwd, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, encoding="utf-8", errors="replace",
    )


# ── the attribute is set, and git agrees that it applies ───────────────────


def test_the_attributes_file_exists() -> None:
    assert _ATTRS.is_file(), (
        ".gitattributes is gone; CHANGELOG merges go back to being manual"
    )


def test_changelog_is_declared_union() -> None:
    text = _ATTRS.read_text(encoding="utf-8")
    lines = [
        ln.strip() for ln in text.splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]
    assert "CHANGELOG.md merge=union" in lines, (
        f"the union declaration is not in .gitattributes; non-comment lines "
        f"are {lines}"
    )


def test_git_resolves_the_attribute_for_the_real_file() -> None:
    """Declaring it is not the same as git applying it — a stray later rule or
    a typo in the path would leave the attribute unset while the file still
    reads as though it were handled."""
    out = _git("check-attr", "merge", "--", "CHANGELOG.md", cwd=_ROOT)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip().endswith(": union"), (
        f"git reports {out.stdout.strip()!r} for CHANGELOG.md, not union"
    )


def test_the_driver_is_not_applied_to_anything_else() -> None:
    """Union is correct for an append-only log and wrong for source code: it
    would silently concatenate both sides of a real conflict. Nothing but the
    changelog may carry it."""
    text = _ATTRS.read_text(encoding="utf-8")
    union_paths = [
        ln.split()[0] for ln in text.splitlines()
        if "merge=union" in ln and not ln.lstrip().startswith("#")
    ]
    assert union_paths == ["CHANGELOG.md"], (
        f"union is declared for {union_paths}; it is only safe for an "
        "append-only file"
    )


# ── the property that makes it safe ────────────────────────────────────────


def test_the_changelog_writer_is_still_insert_only() -> None:
    """The driver's whole justification. ``add_changelog_entry.py`` refuses to
    write if an existing line would change, which is what makes "keep both"
    always the right resolution. If that ever stops being true, union starts
    merging competing edits instead of additions."""
    script = (_ROOT / "scripts" / "add_changelog_entry.py").read_text(
        encoding="utf-8"
    )
    assert "insert" in script.lower()
    # the refusal is the load-bearing part, not the wording
    assert "refus" in script.lower() or "abort" in script.lower(), (
        "add_changelog_entry.py no longer documents refusing to alter an "
        "existing line; re-check whether union is still the right driver"
    )


# ── and it actually resolves the shape it was added for ────────────────────

_BASE = """# Changelog

## [Unreleased]

### Behoben / Fixed

- **An entry that was already here.** First line.
  Second line.

## [5.1.0] - 2026-10-20

### Behoben / Fixed

- **Something released.** Body.
"""


def _append(after: str) -> str:
    return _BASE.replace(
        "- **An entry that was already here.** First line.\n  Second line.\n",
        "- **An entry that was already here.** First line.\n  Second line.\n"
        f"\n{after}\n",
    )


@pytest.mark.parametrize("union", [False, True], ids=["without", "with"])
def test_two_branches_appending_at_the_same_anchor(union: bool) -> None:
    """The exact shape that cost thirteen resolutions.

    Both directions are asserted: without the driver the merge must CONFLICT
    (otherwise this test is not reproducing the problem at all, and the one
    below would prove nothing), and with it the merge must be clean, keep both
    entries, and leave the new one under ``[Unreleased]``.
    """
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="cl-union-test-"))
    try:
        assert _git("init", "-q", "-b", "main", cwd=tmp).returncode == 0
        _git("config", "user.email", "t@example.invalid", cwd=tmp)
        _git("config", "user.name", "T", cwd=tmp)
        (tmp / "CHANGELOG.md").write_text(_BASE, encoding="utf-8")
        if union:
            (tmp / ".gitattributes").write_text(
                "CHANGELOG.md merge=union\n", encoding="utf-8")
            _git("add", ".gitattributes", cwd=tmp)
        _git("add", "CHANGELOG.md", cwd=tmp)
        _git("commit", "-qm", "base", cwd=tmp)

        _git("checkout", "-q", "-b", "side", cwd=tmp)
        (tmp / "CHANGELOG.md").write_text(
            _append("- **Side entry.** From the other branch.\n  Its body."),
            encoding="utf-8")
        _git("commit", "-qam", "side", cwd=tmp)

        _git("checkout", "-q", "main", cwd=tmp)
        (tmp / "CHANGELOG.md").write_text(
            _append("- **Main entry.** From this branch.\n  Its body."),
            encoding="utf-8")
        _git("commit", "-qam", "main", cwd=tmp)

        merged = _git("merge", "--no-edit", "side", cwd=tmp)
        text = (tmp / "CHANGELOG.md").read_text(encoding="utf-8")

        if not union:
            assert merged.returncode != 0, (
                "the two appends merged cleanly without the driver, so this "
                "fixture is not reproducing the collision it claims to"
            )
            assert "<<<<<<<" in text
            return

        assert merged.returncode == 0, (
            f"union did not resolve it: {merged.stdout}{merged.stderr}"
        )
        assert "<<<<<<<" not in text
        assert "Side entry" in text and "Main entry" in text, "an entry was lost"
        assert text.count("## [Unreleased]") == 1
        # both new entries belong above the released section
        assert text.index("Side entry") < text.index("## [5.1.0]")
        assert text.index("Main entry") < text.index("## [5.1.0]")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
