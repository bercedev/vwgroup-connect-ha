# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Plugged in, drawing nothing, reported as charging.

@plainmad (#968) runs a Golf GTE Mk8 on app 4.6.4 with his energy provider
smart-controlling the session. He attached the screen for the case nobody had
captured: plugged in, charger supplying **no power**. His labels are

    Charging status. Battery charge level: 86 per cent.
      Charging station shows current status
    Battery 86 % • Check charging station
    Start charging. Check charging status

and the shipped rule answered *charging* for all three. The third is a button
offering to **start**.

The rule ended in a bare-verb search — "Charging" (or "Lädt" / "Wird geladen")
anywhere in the label meant yes — in front of which sat a list of explicit
not-charging phrasings, added in v4.4.0 when app 4.3.2 began narrating
"… Charging stopped". **That list cannot be completed.** 4.3.2 needed one
entry, 4.6.4 needs another, and "Start charging" was never covered.

So the rule stops searching the blob and reads the state where the state is.
Every dump in ``tests/fixtures/companion_battery`` has one grammar:

    "Charging status. Battery charge level: N per cent. <STATE>"
    "Ladestatus. Batterieladung: N Prozent. <STATE>"

plus a compact tile, ``"40% • Charging"``, whose state follows the separator.
The extracted clause decides, and the bare verb counts **only** as a complete
clause — which is the whole distinction: the ID.3 on 4.3.2 writes the state as
the bare word on a car that really is charging, while "Charging station shows
current status" is not an answer at all. Position is what separates them, and
the old rule never looked at position.

An unrecognised wording returns None rather than False. A wrong True is a
confident lie about the car and a wrong False is one too; None leaves the
previous reading alone and shows up as an unparsed label, which is what should
happen the next time the app rephrases.
"""
from __future__ import annotations

import json
import pathlib
import re

import pytest

from custom_components.vag_connect.companion import presets as P
from custom_components.vag_connect.companion.presets import coerce

_FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "companion_battery"
_HIS_FIXTURE = _FIXTURES / "gte_plugged_no_power_464.xml"

# His three labels, verbatim from the attachment (NBSP-BULLET-NBSP separator).
_HIS_STATUS = (
    "Charging status. Battery charge level: 86 per cent. "
    "Charging station shows current status"
)
_HIS_TILE = "Battery 86 % • Check charging station"
_HIS_BUTTON = "Start charging. Check charging status"


def _chg(raw: str) -> object:
    return coerce("bool_charging", raw)


# ── his report ─────────────────────────────────────────────────────────────


def test_plugged_in_with_no_power_is_not_charging() -> None:
    """The headline. This returned True before."""
    assert _chg(_HIS_STATUS) is False


def test_his_compact_tile_is_not_charging_either() -> None:
    assert _chg(_HIS_TILE) is False


def test_an_offer_to_start_charging_is_never_charging() -> None:
    """A button saying 'Start charging' read as charging. It must not read as
    charging now — None (not a state label) or False, never True."""
    assert _chg(_HIS_BUTTON) is not True


def test_his_labels_are_the_ones_in_the_committed_fixture() -> None:
    """Guards the three strings above against drifting away from the dump.

    Without this, the tests could keep passing against labels that no longer
    match what he actually sent.
    """
    raw = _HIS_FIXTURE.read_text(encoding="utf-8")
    found = set(re.findall(r'(?:text|content-desc)="([^"]+)"', raw))
    for label in (_HIS_STATUS, _HIS_TILE, _HIS_BUTTON):
        assert label in found, f"not in the fixture any more: {label!r}"


def test_the_fixture_carries_no_identifying_data() -> None:
    """It is a stranger's screen in our repository. Re-checked on every run,
    with a planted VIN as a control so an empty result means empty rather than
    a scan that looked in the wrong place."""
    raw = _HIS_FIXTURE.read_text(encoding="utf-8")
    patterns = {
        "VIN-shaped": r"\b[A-HJ-NPR-Z0-9]{17}\b",
        "e-mail": r"[\w.+-]+@[\w-]+\.[a-z]{2,}",
        "coordinate pair": r"-?\d{1,3}\.\d{4,},\s*-?\d{1,3}\.\d{4,}",
        "bearer/JWT": r"eyJ[A-Za-z0-9_-]{10,}",
    }
    for name, pattern in patterns.items():
        assert not re.search(pattern, raw), f"{name} in the fixture"
    # control
    assert re.search(patterns["VIN-shaped"], raw + " WVWZZZAUZFW000000 "), (
        "the VIN scan cannot find a planted VIN, so its clean result is worthless"
    )


def test_the_fixture_provenance_is_recorded() -> None:
    """Someone else's capture does not go in unattributed."""
    entries = json.loads((_FIXTURES / "sources.json").read_text(encoding="utf-8"))
    mine = [e for e in entries if e.get("fixture") == _HIS_FIXTURE.name]
    assert len(mine) == 1, "no sources.json entry for the new fixture"
    assert mine[0].get("contributor")
    assert "968" in mine[0].get("source", "")


# ── the grammar, across every dump we hold ─────────────────────────────────

# (label, expected) — every case taken from a real dump, not invented.
_CORPUS: list[tuple[str, object]] = [
    # genuinely charging, three different phrasings
    ("Charging status. Battery charge level: 40 per cent. Currently charging", True),
    ("Ladestatus. Batterieladung: 34 Prozent. Lädt gerade", True),
    # the ID.3 on 4.3.2: the BARE verb as the complete state, car charging
    ("Charging status. Battery charge level: 42 per cent. Charging", True),
    ("40% • Charging", True),
    ("13 % • Laden", True),
    ("Range overview. Currently charging. Battery range: 88 kilometres.", True),
    # not charging
    ("Charging status. Battery charge level: 79 per cent. Charging stopped", False),
    (
        "Charging status. Battery charge level: 80 per cent. "
        "Target charge level reached",
        False,
    ),
    ("Battery 80 % • Target charge level reached", False),
    (_HIS_STATUS, False),
    (_HIS_TILE, False),
    # An explicit negative with NO clause grammar around it. This is the only
    # thing the not-charging phrase list still does: the clause logic above
    # already answers every long-form and compact label correctly without it,
    # which the mutation check proved by deleting the list and watching nothing
    # fail. A bare "Not connected" tile has no clause to extract, so without
    # the list it would come back unknown — and here an explicit statement
    # deserves an explicit False.
    ("Not connected", False),
    ("Nicht verbunden", False),
    ("Fully charged", False),
    # not a charging state at all: settings screens and detail labels
    ("Charging up to (50-100%)", None),
    ("Reduced AC charging current", None),
    ("Charging", None),
    ("Charging details. 2 hours and. 15 minutes of charging time left", None),
    ("Stop charging", None),
    (_HIS_BUTTON, None),
]


@pytest.mark.parametrize(
    "label,expected", _CORPUS, ids=lambda v: str(v)[:44] if isinstance(v, str) else str(v)
)
def test_the_corpus(label: str, expected: object) -> None:
    assert _chg(label) is expected


def test_the_bare_verb_is_an_answer_only_as_a_whole_clause() -> None:
    """The principle, stated directly, because it is what the fix turns on."""
    assert _chg("Charging status. Battery charge level: 42 per cent. Charging") is True
    assert _chg("Charging status. Battery charge level: 42 per cent. Charging "
                "station shows current status") is False


def test_every_fixture_label_gets_an_answer_or_an_honest_unknown() -> None:
    """No label anywhere in the corpus may produce a value that is not one of
    True / False / None — and none of the charging fixtures may read as
    not-charging, nor the stopped one as charging."""
    for path in sorted(_FIXTURES.glob("*.xml")):
        raw = path.read_text(encoding="utf-8", errors="replace")
        labels = [
            t for t in re.findall(r'(?:text|content-desc)="([^"]+)"', raw)
            if re.search(r"charg|lad|geladen|lädt", t, re.I)
        ]
        for label in labels:
            assert _chg(label) in (True, False, None), (path.name, label)
        # the status label of a charging dump must read True
        if "charging" in path.name and "no_power" not in path.name:
            status = [
                t for t in labels
                if re.search(r"(?:Charging status|Ladestatus)", t, re.I)
            ]
            assert status, f"{path.name} has no status label to check"
            assert any(_chg(t) is True for t in status), (
                f"{path.name} no longer reads as charging"
            )


# ── the screen-identity anchor, and which brands have one ──────────────────

#: Presets with no ``screen_anchor``, each with the reason it has none.
#: An anchor gates a read so a stray screen (an update-or-ignore modal, for
#: instance — @plainmad reported seeing one) yields no-data instead of a wrong
#: value. ``has_anchor`` returns True when a preset has none, so these four are
#: unprotected: a modal in front of the expected screen is parsed as if it were
#: the screen.
#:
#: Not fixed here on purpose. An anchor has to be a resource id that is really
#: present on the target screen and really absent elsewhere, and we hold no ADB
#: dump for Audi or Škoda at all. The CUPRA dumps we do have show the
#: ``CarFragment_*`` ids on the main screen and NOT on the doors screen, so a
#: plausible-looking guess would refuse legitimate reads. A wrong anchor breaks
#: a brand's reads entirely, which is worse than the gap.
_NO_ANCHOR_REASON = {
    "audi": "no ADB dump held",
    "skoda": "no ADB dump held",
    "seat": "no ADB dump held; shares the CUPRA app family",
    "cupra": "dumps held, but the ids differ per screen - needs a per-screen anchor",
}


def _all_presets() -> dict[str, object]:
    out = {}
    for name, obj in vars(P).items():
        brand = getattr(obj, "brand", None)
        if brand and hasattr(obj, "screen_anchor") and not isinstance(obj, type):
            out[brand] = obj
    return out


def test_every_preset_either_has_an_anchor_or_a_recorded_reason() -> None:
    """So a new brand cannot join the unprotected set silently.

    This is the gap @plainmad's popup question exposed. It does not fix it — it
    stops it from being invisible, and the moment a dump arrives for one of
    these brands the entry comes out of the list with the anchor that replaces
    it.
    """
    presets = _all_presets()
    assert presets, "no presets found; this test is not checking anything"
    unexplained = [
        brand for brand, preset in presets.items()
        if preset.screen_anchor is None and brand not in _NO_ANCHOR_REASON
    ]
    assert not unexplained, (
        f"presets with no screen anchor and no recorded reason: {unexplained}. "
        "An unanchored preset parses whatever screen happens to be in front of "
        "it; if that is intended, say why here."
    )
    # and the reverse: a brand that has gained an anchor must leave the list
    stale = [
        brand for brand in _NO_ANCHOR_REASON
        if brand in presets and presets[brand].screen_anchor is not None
    ]
    assert not stale, f"these now have an anchor and can be removed: {stale}"


def test_volkswagen_is_anchored() -> None:
    """The one brand that is protected, pinned so it stays that way."""
    presets = _all_presets()
    assert "volkswagen" in presets
    assert presets["volkswagen"].screen_anchor is not None
