# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1826 — the VIN guard could not see a Porsche VIN.

``_VIN`` opened with ``[A-HJ-NPR-Z]{3}``: three letters. But position 3 of a
world-manufacturer identifier may be a DIGIT (ISO 3779), and four of the eleven
prefixes the guard is configured for are exactly that shape — ``WP0`` (Porsche),
``WF0``, ``WV1`` and ``WV2``. Measured through the guard's own ``scan()`` before
the fix: a production-shaped, non-synthetic literal under those four returned
**zero** findings each, while the other seven returned one each.

So the gate let through precisely the VINs it was configured to catch, for a
third of its own prefix list, while the branch's changelog told readers that CI
"refuses a VIN-shaped literal in the repository that carries no visible mark of
being made up".

Widening is safe because the narrowing happens elsewhere: ``scan()`` filters
every match through ``vin.startswith(_WMI)`` immediately after ``finditer``, so
a 17-character token that is not one of the eleven prefixes is discarded whether
the regex matched it or not. Only the first two characters are letters in every
prefix this project cares about.

This test drives the real ``_WMI`` tuple rather than a copy of it, so a prefix
added later is covered the moment it is added — which is the only way this hole
stays closed.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_GUARD = Path(__file__).resolve().parent.parent / "scripts" / "check_no_real_vins.py"
_spec = importlib.util.spec_from_file_location("_vin_guard", _GUARD)
assert _spec is not None and _spec.loader is not None
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)


def _production_shaped(prefix: str) -> str:
    """A literal under *prefix* that carries no mark of being invented.

    Assembled from fragments so this file holds no whole seventeen-character
    literal of its own, and deliberately free of every marker, repeated serial,
    sequential run and padded counter ``looks_synthetic`` recognises.
    """
    return (prefix + "ZZZ" + "AUZFW" + "472913")[:17]


def _obviously_invented(prefix: str) -> str:
    """The same prefix with a zeroed serial — the project's own placeholder shape."""
    return (prefix + "ZZZ" + "AUZFW" + "000000")[:17]


class TestEveryConfiguredPrefixIsReachable:
    """The regex must be able to see every prefix the guard filters for."""

    @pytest.mark.parametrize("prefix", guard._WMI)
    def test_a_production_shaped_literal_is_reported(
        self, prefix: str, tmp_path: Path,
    ) -> None:
        target = tmp_path / f"holds_{prefix}.py"
        target.write_text(f'x = "{_production_shaped(prefix)}"\n', encoding="utf-8")
        findings = guard.scan([target])
        assert len(findings) == 1, (
            f"{prefix} is configured in _WMI but the pattern cannot reach it — "
            "the guard is blind to a third of its own list when position 3 is a digit"
        )

    @pytest.mark.parametrize("prefix", guard._WMI)
    def test_an_invented_literal_is_not_reported(
        self, prefix: str, tmp_path: Path,
    ) -> None:
        """The counter-check. Without it the test above would also pass on a
        pattern that reports everything, which proves nothing about reach."""
        target = tmp_path / f"holds_{prefix}.py"
        target.write_text(f'x = "{_obviously_invented(prefix)}"\n', encoding="utf-8")
        assert guard.scan([target]) == []

    def test_the_prefix_list_is_not_empty(self) -> None:
        """Both parametrisations read ``guard._WMI``. An empty tuple would
        silently collect zero cases and the file would pass having tested
        nothing at all."""
        assert len(guard._WMI) >= 11
        assert all(len(p) == 3 for p in guard._WMI)


class TestTheWideningDidNotLoosenTheReport:
    def test_an_unrelated_seventeen_character_token_is_still_ignored(
        self, tmp_path: Path,
    ) -> None:
        """The regex now accepts a digit in position 3, which matches far more
        tokens. The _WMI filter in scan() is what keeps the REPORT narrow, and
        this is the assertion that says so."""
        target = tmp_path / "unrelated.py"
        # A plausible non-VAG identifier: two letters, then digits and letters.
        target.write_text('token = "ZZ9QRSTUVWXY12345"\n', encoding="utf-8")
        assert guard.scan([target]) == []

    def test_a_shorter_or_longer_run_is_not_a_vin(self, tmp_path: Path) -> None:
        target = tmp_path / "lengths.py"
        short = _production_shaped("WVW")[:16]
        long = _production_shaped("WVW") + "7"
        target.write_text(f'a = "{short}"\nb = "{long}"\n', encoding="utf-8")
        assert guard.scan([target]) == []

    def test_the_skip_list_still_applies(self, tmp_path: Path) -> None:
        """scan() skips _SKIP_FILES by posix path, so a widened pattern must not
        have changed which files are read."""
        assert guard._SKIP_FILES, "the guard has no exemptions to check"
        target = tmp_path / "plain.py"
        target.write_text(f'x = "{_production_shaped("WVW")}"\n', encoding="utf-8")
        assert len(guard.scan([target])) == 1
