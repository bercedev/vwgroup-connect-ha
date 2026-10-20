# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The FAQ entry for the vanished trip sensors, checked against the code.

@fschulte2812 (#1437) went thirteen days unanswered on a thread whose settled
conclusion — "VW dropped the short-term cluster, there is nothing to fix" —
was true of the EU Data Act portal and **too broad as a general answer**. The
volkswagen.de channel had carried those figures since v4.11.0, and the parser
for it was already owed on #1313 when two reporters were told to stop looking.

Nothing in the README or the FAQ mentioned any of it, so anyone hitting the
same symptom found the thread, read the old conclusion and stopped. The
documentation gap is what turned one wrong answer into a standing one, so the
fix is a FAQ entry.

A FAQ entry can go stale the same way the answer did, and a stale instruction
is worse than none — so this file does not check that the prose exists, it
checks that **what the prose claims is still true of the code**: the sensor
keys it names, the brands it says can enable the channel, and the attribute
string it tells people to look for. If any of those move, this fails and the
entry gets corrected instead of quietly misleading the next reader.
"""
from __future__ import annotations

import pathlib
import re

from custom_components.vag_connect import _channel_labels as labels
from custom_components.vag_connect import config_flow, sensor

_FAQ = pathlib.Path(__file__).resolve().parents[1] / "docs" / "FAQ.md"
_HEADING = "My last-trip / short-term sensors went empty"


def _faq() -> str:
    return _FAQ.read_text(encoding="utf-8")


def _section() -> str:
    """The entry's own text, so a match elsewhere in the FAQ cannot satisfy
    these assertions."""
    text = _faq()
    start = text.index(_HEADING)
    rest = text[start:]
    end = rest.find("\n## ")
    return rest if end == -1 else rest[:end]


def test_the_entry_exists() -> None:
    assert _HEADING in _faq(), (
        "the FAQ entry is gone; #1437 was caused by this gap, not by the code"
    )


# ── every sensor key it names must exist ───────────────────────────────────

def _keys_named_in_the_faq() -> list[str]:
    """The ``last_trip_*`` identifiers the entry actually prints.

    Read out of the document, not listed here. The first version of this file
    parametrised over a hardcoded tuple, which checked my own list against the
    code and let the FAQ say anything at all — the mutation check caught it by
    renaming a key in the document and watching nothing fail.
    """
    return sorted(set(re.findall(r"`(last_trip_[a-z0-9_]+)`", _section())))


def test_the_entry_names_some_keys_at_all() -> None:
    """Guards the extraction above: if the formatting changes so no key is
    found, every per-key check below would pass on an empty set."""
    assert len(_keys_named_in_the_faq()) >= 5, (
        f"only found {_keys_named_in_the_faq()} in the FAQ entry; the "
        "per-key checks would be vacuous"
    )


def test_each_key_named_in_the_faq_is_real() -> None:
    """The entry tells people which sensors come back. A key renamed on either
    side — in the document or in the code — makes it name entities that do not
    exist."""
    unknown = [
        key for key in _keys_named_in_the_faq()
        if key not in sensor._TRIP_STATS_KEYS
    ]
    assert not unknown, (
        f"the FAQ entry names {unknown}, which are not trip-stats keys"
    )


def test_the_entry_names_the_keys_the_code_actually_restores() -> None:
    """And the other direction: a key added to the short-term family that the
    entry does not mention would leave the list quietly incomplete."""
    section = _section()
    short_term = {k for k in sensor._TRIP_STATS_KEYS if k.startswith("last_trip_")}
    missing = sorted(k for k in short_term if k not in section)
    assert not missing, (
        f"short-term keys the FAQ entry does not list: {missing}"
    )


# ── the brands it says can enable the channel ──────────────────────────────


def test_the_brands_match_the_gate() -> None:
    """The entry says Volkswagen and Volkswagen Commercial Vehicles. If the
    gate widens or narrows (#1801 widened it once), the entry has to follow or
    it sends the wrong people to a button they do not have."""
    section = _section().lower()
    assert config_flow._VWDE_BRANDS == frozenset(
        {"volkswagen", "volkswagen_commercial"}
    ), (
        "the volkswagen.de channel gate changed; update the FAQ entry's brand "
        f"list to match {sorted(config_flow._VWDE_BRANDS)}"
    )
    assert "volkswagen" in section
    assert "commercial" in section


def test_it_does_not_promise_the_channel_to_audi() -> None:
    """#1805 is a different fault with a different cause, and the entry points
    there instead. Promising this channel to an Audi owner would send them
    through a sign-in that cannot help them."""
    section = _section()
    assert "1805" in section, "the entry no longer points Audi owners anywhere"

    # Audi has to be addressed in its own subsection, which says the cause is
    # different. Checking it line by line was the first version of this and it
    # was wrong: a perfectly good sentence inside that subsection ("needs one
    # diagnostic run from an Audi owner…") carries neither word, because the
    # heading above it already does.
    audi_heading = [
        ln for ln in section.splitlines()
        if ln.startswith("###") and "Audi" in ln
    ]
    assert audi_heading, "no dedicated Audi subsection in the entry"
    audi_part = section[section.index(audi_heading[0]):]
    assert "different" in audi_part.lower(), (
        "the Audi subsection no longer says the cause is a different one"
    )

    # And the actual instruction — the numbered steps that send someone to
    # enable the channel — must not mention Audi at all.
    steps = section[:section.index(audi_heading[0])]
    numbered = [ln for ln in steps.splitlines() if re.match(r"^\d+\.", ln.strip())]
    assert numbered, "the entry has lost its how-to steps"
    for line in numbered:
        assert "audi" not in line.lower(), (
            f"the how-to step mentions Audi, which the channel cannot help: {line!r}"
        )


# ── the attribute string it tells people to look for ───────────────────────


def test_the_source_attribute_string_matches() -> None:
    """The entry says the values arrive with ``source: vw.de website``. That
    label is generated from a token, and if the label changes the instruction
    becomes a wild goose chase."""
    expected = labels._CHANNEL_LABELS["website_authproxy"]
    assert expected == "vw.de website", (
        f"the channel label is now {expected!r}; update the FAQ entry"
    )
    assert expected in _section()


# ── honesty about the limits ───────────────────────────────────────────────


def test_the_entry_still_says_what_it_does_not_restore() -> None:
    """The limits are the part most likely to be trimmed later, and they are
    the reason the entry is not itself a too-broad answer. Three things are
    deliberately named as still missing."""
    section = _section().lower()
    for claim in ("recuperation", "zero-emission", "range gained"):
        assert claim in section, (
            f"the FAQ entry no longer records {claim!r} as missing; it would "
            "then read as a complete restoration, which is the mistake #1437 "
            "was about"
        )


def test_the_entry_does_not_blame_the_portal_in_general() -> None:
    """The failure mode this entry exists to correct, applied to the entry.

    Discussion #1415 caught the first draft doing exactly what #1437 was
    about. A portal-fed CUPRA Tavascan owner had been told, correctly, that
    last-trip distance *does* come from the portal on his car — while the draft
    said flatly that "the portal stopped carrying that cluster". Both cannot be
    read as general truths.

    Measured rather than argued: the ``short_term_data_*`` reader in
    ``_eu_data_act.py`` has no brand or realm condition anywhere near it, so
    the integration reads the cluster for whatever brand's portal delivers it.
    What stopped is the **Volkswagen** realm. So the entry has to say which
    realm, and must not state the unqualified version.
    """
    section = _section()
    # Strip markdown emphasis and collapse whitespace before matching. The
    # entry writes "**Volkswagen's** portal realm", so the asterisks sit
    # between the words and a plain substring search misses it - the same
    # invisible-character trap as the NBSP separator in #968.
    low = re.sub(r"\s+", " ", re.sub(r"[*_`]+", "", section)).lower()

    # the unqualified claim, in the shapes it would plausibly be written
    for bad in (
        "the portal stopped carrying",
        "the portal no longer carries",
        "the portal dropped",
    ):
        assert bad not in low, (
            f"the entry says {bad!r} without naming a realm, which is the "
            "too-broad wording #1437 was caused by"
        )

    assert "volkswagen's portal realm" in low or "volkswagen portal realm" in low, (
        "the entry does not say WHICH realm stopped carrying the cluster"
    )
    # and it has to tell CUPRA/SEAT owners the opposite
    assert "cupra" in low and "seat" in low, (
        "the entry does not mention CUPRA/SEAT, whose portal may still carry "
        "the cluster - those owners would read this as 'gone' and stop looking"
    )


def test_the_portal_reader_really_is_brand_agnostic() -> None:
    """The premise of the paragraph above. If a brand gate ever appears around
    the short-term reader, the FAQ's claim becomes wrong and this fails
    first."""
    import inspect

    from custom_components.vag_connect.cariad.auth import _eu_data_act as eda

    src = inspect.getsource(eda)
    idx = src.index('first_src("short_term_data_mileage")')
    window = src[max(0, idx - 1200):idx + 1200]
    for gate in ("brand ==", 'brand in (', "state_brand ==", "realm =="):
        assert gate not in window, (
            f"a {gate!r} condition now sits around the short-term reader; the "
            "FAQ says the reader is brand-agnostic and would need correcting"
        )


def test_the_entry_gives_the_version_floor() -> None:
    """Without a version the instruction fails silently for anyone older."""
    assert re.search(r"v?4\.11\.0", _section()), (
        "the FAQ entry no longer names the release the channel arrived in"
    )
