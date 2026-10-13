# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The VIN guard has to actually catch things, and the repo has to stay clean.

CONTRIBUTING.md has said since 2026-04-30, under "What goes in the repo /
fixtures / commits", that full VINs do not belong here, "even from the tester's
own car", and that the section binds maintainers as well as contributors. It was
still broken eleven times. A 2026-05 sanitization pass cleaned three occurrences
and missed the rest; one of those was reintroduced into a new test file seven
weeks later; and two outside contributors propagated one of them in good faith,
because nothing in the tooling told them. A written rule nothing enforces decays.

Two things are tested here, and the first matters more than it looks:

1. The guard can fail. A check that silently accepts everything passes its own
   regression test forever while protecting nothing, which is the shape of
   several bugs this project has already paid for. So there are positive cases
   with VINs that MUST be rejected.

2. The tree is clean. This is the regression test proper.

This file is in the guard's own skip list, because it necessarily contains
example VINs that are meant to be rejected.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from check_no_real_vins import looks_synthetic, scan  # noqa: E402

_ROOT = Path(__file__).resolve().parents[1]


class TestTheGuardCanFail:
    """Positive cases. If these ever stop being rejected the guard is blind."""

    @pytest.mark.parametrize("vin", [
        "WVWZZZAUZFW805377",   # the Golf this repo called "Dein Golf 7 GTE VIN"
        "WAUZZZF29MN024037",   # a tester's Q5, named with its subscription state
        "TMBJR8NX2RY179915",
        "WVWZZZ3CZ9W025570",
        "WVGZZZ5NZPM465017",   # WVG — the prefix the first inventory sweep missed
    ])
    def test_a_production_looking_vin_is_refused(self, vin):
        assert looks_synthetic(vin) is False, f"{vin} wrongly accepted as synthetic"

    def test_a_refused_vin_is_actually_reported(self, tmp_path):
        """End to end, not just the predicate: the scanner must surface it."""
        f = tmp_path / "fixture.py"
        f.write_text('VIN = "WVWZZZAUZFW805377"\n', encoding="utf-8")
        findings = scan([f])
        assert len(findings) == 1
        assert findings[0][2] == "WVWZZZAUZFW805377"


class TestTheGuardAcceptsPlaceholders:
    """Negative cases, each one a literal this repo actually relies on."""

    @pytest.mark.parametrize("vin", [
        "WVWZZZAUZFW000000",   # zeroed serial, the convention used for the fix
        "WVWZZZTESTVIN0001",   # word marker
        "WAUZZZTESTVHN0001",
        "TMBXXXXXXXXXXXXXX",   # filler run
        "WVWZZZ1KZAW000503",   # padded counter
        "WVWZZZ7AZRE000999",
        "WAUZZZ1234567890A",   # sequential run with a trailing letter
        "WVWZZZ1234567890X",
        "WVWZZZE1ZMP111111",   # repeated-digit serial
        "WVWZZZSYNTHET0001",
    ])
    def test_a_visibly_fake_vin_passes(self, vin):
        assert looks_synthetic(vin) is True, f"{vin} wrongly refused"

    def test_an_ordinary_identifier_is_not_mistaken_for_a_vin(self):
        """Guards against the check firing on hex blobs, etags and base64."""
        findings = scan([Path(__file__)])
        bad = [f for f in findings if "0x8DEEC19E6798565" in f[2]]
        assert bad == []


class TestTheTreeIsClean:
    """The regression test. This is the one that will fail on a future PR."""

    def test_no_tracked_file_holds_a_non_synthetic_vin(self):
        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True,
            check=True, cwd=_ROOT,
        ).stdout.splitlines()
        findings = scan([_ROOT / p for p in tracked if p])
        # Report masked: this assertion message can end up in a public CI log.
        masked = [f"{p}:{n} {v[:6]}...{v[-4:]}" for p, n, v in findings]
        assert masked == [], "non-synthetic VINs in tree: " + "; ".join(masked)

    def test_the_scan_actually_read_the_tree(self):
        """Counter-check for the test above: a scan over nothing also returns
        nothing, and would look just as green."""
        tracked = subprocess.run(
            ["git", "ls-files"], capture_output=True, text=True,
            check=True, cwd=_ROOT,
        ).stdout.splitlines()
        assert len(tracked) > 100, "git ls-files returned almost nothing"
