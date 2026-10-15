# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The release documentation has to agree with the machines that read it.

Four things in ``RELEASE_PROCESS.md`` and its neighbours had drifted far
enough to cost a release, and prose alone could not hold them:

1. **The changelog heading.** The document prescribed
   ``## [vX.Y.Z] — date — "…"``. Three readers parse that line and not one
   of them accepts a ``v`` prefix: ``ci.yml`` turns red, ``release.yml``
   silently publishes a bare "see the CHANGELOG" link instead of the notes,
   and the version drops out of ``release.yml``'s version/date list. The
   em dash before the date is wrong too — the date is captured by
   ``\\s*-\\s*``, so an em dash loses it from the *next* release's
   "Recent releases" pointer. The tests below replay all three readers'
   real logic over whatever heading the document currently prescribes.

2. **The pre-release checklist** claimed to "match the CI gates exactly"
   while covering four checks out of two of ``ci.yml``'s four jobs.

3. **Dangling doc links.** ``docs/CHANGELOG_TECHNICAL.md`` and
   ``docs/HACS-CHECKLIST.md`` were deleted as stale in ``9e055cec``
   (2.8.0rc1, 2026-05-31) and nine references across six files kept
   pointing at them, two of those in ``CHANGELOG.md``'s opening
   blockquotes.

4. **The coverage gate**, which lived in four places with three different
   values. ``ci.yml``'s ``--cov-fail-under`` is now the single source and
   the prose points at it instead of restating it.

Plus the release-time placeholder in ``docs/COMPANION.md`` that no step
resolved.

These tests read the documents, so they fail the moment either side moves.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).resolve().parent.parent
_RELEASE_PROCESS = _REPO_ROOT / "RELEASE_PROCESS.md"
_CHANGELOG = _REPO_ROOT / "CHANGELOG.md"
_CI_YML = _REPO_ROOT / ".github" / "workflows" / "ci.yml"
_PR_TEMPLATE = _REPO_ROOT / ".github" / "PULL_REQUEST_TEMPLATE.md"
_QUALITY_SCALE = _REPO_ROOT / "custom_components" / "vag_connect" / "quality_scale.yaml"
_COMPANION = _REPO_ROOT / "docs" / "COMPANION.md"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ──────────────────────────────────────────────────────────────────────
# The three machine readers, transcribed from the workflows verbatim.
# ──────────────────────────────────────────────────────────────────────


def reader_ci_changelog_gate(changelog: str, version: str) -> bool:
    """``.github/workflows/ci.yml`` — step *CHANGELOG.md enthält aktuelle Version*.

    ``if f'[{version}]' not in changelog: sys.exit(1)``
    """
    return f"[{version}]" in changelog


def reader_release_notes_body(changelog: str, version: str) -> str:
    """``.github/workflows/release.yml`` — step *Release-Notes generieren*.

    Empty means the published notes fall back to a bare CHANGELOG link.
    """
    pattern = rf"## \[{re.escape(version)}\][^\n]*\n(.*?)(?=\n## \[|\Z)"
    match = re.search(pattern, changelog, re.DOTALL)
    return match.group(1).strip() if match else ""


def reader_release_version_list(changelog: str) -> list[tuple[str, str]]:
    """``.github/workflows/release.yml`` — the version/date list in the same step."""
    return re.findall(
        r"## \[(\d+\.\d+\.\d+)\](?:\s*-\s*(\d{4}-\d{2}-\d{2}))?", changelog
    )


_SAMPLE_BODY = "\n\n### Fixed\n- a thing (#1)\n"


def _changelog_around(heading: str) -> str:
    """One realistic changelog whose newest section carries ``heading``."""
    return (
        "# Changelog\n\n## [Unreleased]\n\n"
        + heading
        + _SAMPLE_BODY
        + "\n## [1.2.3] - 2026-01-02 — Older\n\n### Fixed\n- older (#2)\n"
    )


# ──────────────────────────────────────────────────────────────────────
# 0. The replay above has to be able to fail, or it proves nothing.
# ──────────────────────────────────────────────────────────────────────


class TestReadersActuallyDiscriminate:
    """Positive control: the transcribed readers reject the broken forms."""

    def test_v_prefixed_heading_is_rejected(self) -> None:
        changelog = _changelog_around('## [v9.9.9] — 2026-12-31 — "Name"')

        assert not reader_ci_changelog_gate(changelog, "9.9.9"), (
            "ci.yml's gate looks for the bare '[9.9.9]'; if a 'v'-prefixed "
            "heading satisfied it, this whole replay would be meaningless."
        )
        assert reader_release_notes_body(changelog, "9.9.9") == ""
        assert "9.9.9" not in [v for v, _ in reader_release_version_list(changelog)]

    def test_bare_heading_with_hyphen_date_is_accepted(self) -> None:
        changelog = _changelog_around('## [9.9.9] - 2026-12-31 — "Name"')

        assert reader_ci_changelog_gate(changelog, "9.9.9")
        assert reader_release_notes_body(changelog, "9.9.9") != ""
        assert ("9.9.9", "2026-12-31") in reader_release_version_list(changelog)

    def test_em_dash_before_the_date_loses_the_date(self) -> None:
        """Why the document must say ASCII hyphen, not em dash."""
        changelog = _changelog_around('## [9.9.9] — 2026-12-31 — "Name"')

        assert ("9.9.9", "") in reader_release_version_list(changelog), (
            "release.yml captures the date via r'\\s*-\\s*'. An em dash is not "
            "matched, so the date is empty and the next release's "
            "'Recent releases' pointer lists this version without one."
        )


# ──────────────────────────────────────────────────────────────────────
# 1. RELEASE_PROCESS.md must prescribe a heading the readers accept.
# ──────────────────────────────────────────────────────────────────────


_HEADING_LINE = re.compile(r"^## \[(?P<inner>[^\]]+)\](?P<rest>.*)$", re.MULTILINE)


def _prescribed_headings(text: str) -> list[tuple[str, str]]:
    """Every ``## [...]`` heading the document shows, minus ``[Unreleased]``."""
    out: list[tuple[str, str]] = []
    for m in _HEADING_LINE.finditer(text):
        inner = m.group("inner").strip()
        if inner.lower() == "unreleased":
            continue
        out.append((inner, m.group("rest")))
    return out


def _concretise(inner: str, rest: str) -> tuple[str, str]:
    """Turn a template heading into a real one: version placeholder -> digits."""
    version = inner
    for placeholder in ("X.Y.Z", "x.y.z"):
        version = version.replace(placeholder, "9.9.9")
    rest = rest.replace("YYYY-MM-DD", "2026-12-31").replace("2026-MM-DD", "2026-12-31")
    return version, rest


class TestPrescribedChangelogHeading:
    """The document's own heading examples, run through the real parsers."""

    def test_at_least_one_heading_is_prescribed(self) -> None:
        """Guard: if the examples ever vanish, the tests below must not pass empty."""
        assert _prescribed_headings(_read(_RELEASE_PROCESS)), (
            "RELEASE_PROCESS.md no longer shows a '## [...]' changelog heading. "
            "The format tests below would silently pass over nothing."
        )

    def test_no_prescribed_heading_uses_a_v_prefix(self) -> None:
        offenders = [
            f"## [{inner}]{rest}"
            for inner, rest in _prescribed_headings(_read(_RELEASE_PROCESS))
            if re.match(r"^v\d|^vX", inner, re.IGNORECASE)
        ]
        assert not offenders, (
            "RELEASE_PROCESS.md prescribes a 'v'-prefixed CHANGELOG heading: "
            + " | ".join(offenders)
            + " — ci.yml's CHANGELOG gate goes red on it and release.yml "
            "publishes a bare CHANGELOG link instead of the release notes. "
            "The 'v' belongs on the git tag only."
        )

    def test_every_prescribed_heading_passes_all_three_readers(self) -> None:
        failures: list[str] = []
        for inner, rest in _prescribed_headings(_read(_RELEASE_PROCESS)):
            version, concrete_rest = _concretise(inner, rest)
            if not re.fullmatch(r"\d+\.\d+\.\d+", version):
                failures.append(f"## [{inner}] -> version {version!r} is not a semver")
                continue
            heading = f"## [{version}]{concrete_rest}"
            changelog = _changelog_around(heading)

            if not reader_ci_changelog_gate(changelog, version):
                failures.append(f"{heading!r}: ci.yml CHANGELOG gate would go RED")
            if not reader_release_notes_body(changelog, version):
                failures.append(
                    f"{heading!r}: release.yml would publish a bare CHANGELOG "
                    "link instead of the notes"
                )
            if version not in [v for v, _ in reader_release_version_list(changelog)]:
                failures.append(
                    f"{heading!r}: release.yml's version list would not see it"
                )
        assert not failures, "RELEASE_PROCESS.md prescribes unparseable headings:\n" + "\n".join(
            failures
        )

    def test_release_process_warns_about_the_v_prefix(self) -> None:
        text = _read(_RELEASE_PROCESS)
        assert "machine-read" in text.lower(), (
            "RELEASE_PROCESS.md should say outright that the heading is parsed "
            "by CI and by release.yml, so the next editor does not 'tidy' the "
            "'v' back in."
        )
        assert "[v4.13.0]" in text or "`v`" in text


class TestShippedChangelogHeadings:
    """The live CHANGELOG must stay parseable too."""

    def test_newest_released_heading_passes_all_three_readers(self) -> None:
        changelog = _read(_CHANGELOG)
        entries = reader_release_version_list(changelog)
        assert entries, "No '## [X.Y.Z]' heading parsed out of CHANGELOG.md at all."

        version, date = entries[0]
        assert reader_ci_changelog_gate(changelog, version)
        assert reader_release_notes_body(changelog, version), (
            f"release.yml could not extract the section for {version}."
        )
        assert date, (
            f"CHANGELOG.md's newest entry ({version}) has no date that "
            "release.yml can capture — it needs an ASCII hyphen before an ISO "
            "date, not an em dash."
        )

    def test_no_released_heading_carries_a_v_prefix(self) -> None:
        offenders = re.findall(r"^## \[v[^\]]+\].*$", _read(_CHANGELOG), re.MULTILINE)
        assert not offenders, "CHANGELOG.md headings with a 'v' prefix: " + " | ".join(
            offenders
        )


# ──────────────────────────────────────────────────────────────────────
# 2. The pre-release checklist must not overclaim.
# ──────────────────────────────────────────────────────────────────────


class TestPreReleaseChecklistClaim:
    """It covers four checks from two of ci.yml's four jobs — say so."""

    def test_does_not_claim_to_match_ci_exactly(self) -> None:
        text = _read(_RELEASE_PROCESS)
        assert "match the CI gates exactly" not in text, (
            "The pre-release checklist does not match the CI gates exactly: it "
            "omits the manifest.json field check, the JSON parse of every file "
            "under custom_components/, the CHANGELOG-contains-version check, "
            "hassfest and HACS Validation, and it pins neither the ruff nor the "
            "Python version CI uses."
        )

    def test_names_the_ci_gates_it_does_not_run(self) -> None:
        text = _read(_RELEASE_PROCESS)
        missing = [
            name
            for name in ("hassfest", "HACS Validation", "manifest.json", "ruff")
            if name not in text
        ]
        assert not missing, (
            "The checklist section should name the CI gates it does not "
            "reproduce, so a green local run is not mistaken for green CI. "
            f"Not mentioned anywhere in the document: {missing}"
        )

    def test_ci_still_has_the_four_jobs_the_prose_describes(self) -> None:
        """If a job is added or removed, the prose above needs rewriting."""
        text = _read(_CI_YML)
        _, _, jobs_block = text.partition("\njobs:\n")
        assert jobs_block, "ci.yml has no top-level 'jobs:' block."
        jobs = re.findall(r"^  ([a-z][a-z0-9-]*):$", jobs_block, re.MULTILINE)
        assert jobs == ["lint-and-validate", "tests", "hassfest", "hacs"], (
            "ci.yml's job list changed to "
            f"{jobs} — RELEASE_PROCESS.md's pre-release checklist section "
            "describes four jobs by name and has to be updated with it."
        )


# ──────────────────────────────────────────────────────────────────────
# 3. No tracked document may point at a docs/ file that does not exist.
# ──────────────────────────────────────────────────────────────────────


_DOCS_REF = re.compile(r"docs/[A-Za-z0-9_.\-]+\.md")


def _text_files() -> list[Path]:
    paths: list[Path] = []
    for pattern in ("*.md", ".github/**/*.md", ".github/**/*.yml", "docs/**/*.md"):
        paths.extend(_REPO_ROOT.glob(pattern))
    return sorted({p for p in paths if p.is_file()})


class TestNoDanglingDocsReferences:
    """``9e055cec`` deleted six stale docs and left the links behind."""

    def test_doc_inventory_is_non_empty(self) -> None:
        """Guard against the glob silently matching nothing."""
        files = _text_files()
        assert len(files) > 10, f"Only found {len(files)} text files to scan."
        assert any(p.name == "RELEASE_PROCESS.md" for p in files)
        assert any(p.name == "CHANGELOG.md" for p in files)

    def test_every_referenced_docs_file_exists(self) -> None:
        dangling: list[str] = []
        for path in _text_files():
            text = path.read_text(encoding="utf-8", errors="replace")
            for target in sorted(set(_DOCS_REF.findall(text))):
                if not (_REPO_ROOT / target).is_file():
                    dangling.append(f"{path.relative_to(_REPO_ROOT).as_posix()} -> {target}")
        assert not dangling, (
            "Documents point at files that are not in the tree. Either remove "
            "the reference or point it somewhere real — do not add a stub to "
            "satisfy the link:\n" + "\n".join(dangling)
        )

    @pytest.mark.parametrize("gone", ["CHANGELOG_TECHNICAL.md", "HACS-CHECKLIST.md"])
    def test_deleted_docs_are_not_referenced_anywhere(self, gone: str) -> None:
        """Belt and braces: these two were deleted as stale in 2.8.0rc1."""
        assert not (_REPO_ROOT / "docs" / gone).exists(), (
            f"docs/{gone} is back in the tree. If it has been genuinely "
            "restored with content, delete this test; if it is a stub added to "
            "satisfy a link, remove the stub instead."
        )
        offenders = [
            path.relative_to(_REPO_ROOT).as_posix()
            for path in _text_files()
            if gone in path.read_text(encoding="utf-8", errors="replace")
        ]
        assert not offenders, f"docs/{gone} no longer exists but is still cited in: {offenders}"


# ──────────────────────────────────────────────────────────────────────
# 4. ci.yml owns the coverage gate; prose must not restate the number.
# ──────────────────────────────────────────────────────────────────────


_GATE = re.compile(r"--cov-fail-under=(\d+)")
# Any 1-2 digit number left on a line that talks about the coverage gate,
# once the flag itself and shell redirections like `2>&1` are taken out.
_NUM_ON_GATE_LINE = re.compile(r"(?<![\d.])(\d{1,2})(?![\d.])")
_REDIRECT = re.compile(r"\d*>&?\d*")
_GATE_WORDS = ("coverage", "cov-fail-under", "threshold")


def _flag_values(text: str) -> list[int]:
    """The gate as the tool reads it: every ``--cov-fail-under=N``."""
    return [int(v) for v in _GATE.findall(text)]


def _prose_values(text: str) -> list[int]:
    """The gate as a human restated it: loose numbers on coverage lines."""
    found: list[int] = []
    for line in text.splitlines():
        if not any(word in line.lower() for word in _GATE_WORDS):
            continue
        stripped = _REDIRECT.sub(" ", _GATE.sub(" ", line))
        found.extend(int(n) for n in _NUM_ON_GATE_LINE.findall(stripped))
    return found


def _live_gate() -> int:
    values = {int(v) for v in _GATE.findall(_read(_CI_YML))}
    assert len(values) == 1, f"ci.yml states more than one coverage gate: {values}"
    return values.pop()


class TestCoverageGateSingleSource:
    """One value in ci.yml; everything else points at it.

    ``CHANGELOG.md`` is excluded on purpose: its entries record what the
    gate *was* when each release shipped, which must not be rewritten.
    """

    def test_ci_yml_states_the_gate_exactly_once(self) -> None:
        text = _read(_CI_YML)
        gate = _live_gate()
        assert _flag_values(text) == [gate]
        assert _prose_values(text) == [], (
            "ci.yml mentions the coverage number outside the --cov-fail-under "
            f"flag: {_prose_values(text)}. A second copy in a nearby comment "
            "is exactly what went stale — the comment said 65 for months "
            "after the gate moved to 75."
        )

    @pytest.mark.parametrize(
        "rel",
        [
            "custom_components/vag_connect/quality_scale.yaml",
            ".github/PULL_REQUEST_TEMPLATE.md",
        ],
    )
    def test_these_files_point_at_ci_yml_instead_of_restating_it(self, rel: str) -> None:
        text = _read(_REPO_ROOT / rel)
        numbers = _flag_values(text) + _prose_values(text)
        assert not numbers, (
            f"{rel} restates the coverage gate as {numbers}. Both copies here "
            "had gone stale with different wrong values. Reference "
            "`--cov-fail-under` in .github/workflows/ci.yml instead."
        )
        assert "ci.yml" in text, (
            f"{rel} mentions coverage but does not say where the gate lives; "
            "point at .github/workflows/ci.yml."
        )

    def test_release_process_only_quotes_the_live_gate(self) -> None:
        gate = _live_gate()
        text = _read(_RELEASE_PROCESS)
        numbers = _flag_values(text) + _prose_values(text)
        assert numbers, (
            "RELEASE_PROCESS.md's pre-release checklist no longer runs a "
            "coverage command — this test would pass over nothing."
        )
        wrong = sorted({n for n in numbers if n != gate})
        assert not wrong, (
            f"RELEASE_PROCESS.md quotes coverage figures {wrong} while ci.yml's "
            f"gate is {gate}. A checklist that passes locally and fails in CI "
            "is worse than no checklist."
        )


# ──────────────────────────────────────────────────────────────────────
# 5. Release-time placeholders need a step that resolves them.
# ──────────────────────────────────────────────────────────────────────


_APPLIES_TO = re.compile(r"^\|\s*\*\*Applies to\*\*\s*\|(?P<value>[^|]*)\|", re.MULTILINE)
_STEP = re.compile(r"^### (\d+)\. ", re.MULTILINE)


class TestCompanionAppliesToPlaceholder:
    """``docs/COMPANION.md``'s *Applies to* row ships verbatim otherwise."""

    def test_the_row_is_still_there_to_reason_about(self) -> None:
        match = _APPLIES_TO.search(_read(_COMPANION))
        assert match, (
            "docs/COMPANION.md no longer has an '**Applies to**' row. If the "
            "header table was restructured, update this test and the "
            "RELEASE_PROCESS.md step that resolves it."
        )

    def test_unresolved_placeholder_has_a_release_step(self) -> None:
        value = _APPLIES_TO.search(_read(_COMPANION)).group("value").strip()
        if re.fullmatch(r"[\s\d.v]+", value):
            pytest.skip(f"Applies to is already a concrete version: {value!r}")

        process = _read(_RELEASE_PROCESS)
        assert "docs/COMPANION.md" in process, (
            f"docs/COMPANION.md says 'Applies to: {value}', which no step "
            "resolves, so the phrase ships to readers as written. "
            "RELEASE_PROCESS.md needs a step that replaces it with the version "
            "being released."
        )
        assert "Applies to" in process, (
            "RELEASE_PROCESS.md mentions docs/COMPANION.md but not the "
            "'Applies to' row it has to resolve."
        )

    def test_steps_are_numbered_without_gaps(self) -> None:
        numbers = [int(n) for n in _STEP.findall(_read(_RELEASE_PROCESS))]
        assert numbers == list(range(1, len(numbers) + 1)), (
            f"RELEASE_PROCESS.md step numbering is {numbers} — inserting a step "
            "means renumbering the ones after it."
        )
