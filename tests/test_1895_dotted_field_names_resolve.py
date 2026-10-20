# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A field the catalogue describes, reported with an empty spec column.

@GiuseppeAlbano (#1895) filed a Scout report for one new Audi field:

    | `eu_data_act.viwi_DriverNotification_1.2.0_get_notifications` | — | ...

The middle column is the official spec name, and it was empty — yet that field
is in the catalogue twice, under two UUIDs, with a description. The row said
"we have never heard of this" about something we ship the documentation for.

``lookup`` reduced a dotted path to its **last segment**, on the assumption
that a dot separates the container from the field. 3206 of the 7990 field names
contain dots of their own: every ``viwi_*`` resource carries its interface
version, and the charging families are spelled as nested paths. His field was
looked up as ``0_get_notifications``. Only 349 of those 3206 names resolved.

The second, quieter half: the name index was never consulted with the whole
key, only with that last segment. So ``01_msp_elli.msp_elli_ocpi_cdr.currency``
resolved to the generic ``currency`` field — a different field, with a
different unit and description, which raw discovery would then have used to
name and unit a sensor.

The measurement that made this safe to change: across every name, every
``eu_data_act.``-prefixed name and every UUID in the catalogue — 21226 keys —
5714 that resolved to nothing now resolve, 698 now resolve to their own exact
entry instead of a generic one, and **nothing that resolved before stops
resolving**. The last number is the one this file pins.
"""
from __future__ import annotations

import json
import pathlib

import pytest

from custom_components.vag_connect.cariad import _reporter_pipeline as rp
from custom_components.vag_connect.cariad._unexpected_keys import UnexpectedField
from custom_components.vag_connect.cariad.auth import eu_data_dictionary as dd

REPORTED = "eu_data_act.viwi_DriverNotification_1.2.0_get_notifications"
BARE = "viwi_DriverNotification_1.2.0_get_notifications"
# both UUIDs the catalogue files that name under
UUIDS = ("0198f4be-42dc-7127-ba6f-fb11c6ecd044",
         "0199b88a-3c9e-7f41-ac22-d009bf607e5b")
NESTED = "01_msp_elli.msp_elli_ocpi_cdr.currency"


# ── the row he actually filed ──────────────────────────────────────────────


def test_the_reported_path_resolves() -> None:
    assert dd.describe(REPORTED) == BARE


def test_the_bare_dotted_name_resolves_too() -> None:
    """The portal sends the name with and without the container prefix."""
    assert dd.describe(BARE) == BARE


@pytest.mark.parametrize("uuid", UUIDS)
def test_the_uuids_still_resolve(uuid: str) -> None:
    """The UUID path was the one that always worked; it must keep working."""
    assert dd.describe(uuid) == BARE


def test_the_scout_row_is_no_longer_empty() -> None:
    """End to end, through the thing that printed the dash.

    Pinned at the report level and not only at ``describe``, because the empty
    column is what he saw and what made the field look unknown.
    """
    report = rp.build_unexpected_keys_report(
        [UnexpectedField(
            path=REPORTED,
            sample_masked="[redacted text blob, 1701 chars]",
            endpoint="eu_data_act",
            first_seen_at="2026-10-20T13:03:55+00:00",
        )],
        brand="audi",
        integration_version="5.1.0",
    )
    row = next(ln for ln in report.splitlines() if REPORTED in ln)
    assert BARE in row, row
    cells = [c.strip() for c in row.strip().strip("|").split("|")]
    assert cells[1] == BARE, f"spec column is {cells[1]!r}"
    assert cells[1] != "—", "the spec column is still the empty dash"


# ── the quieter half: specific beats generic ───────────────────────────────


def test_a_nested_path_resolves_to_itself_not_to_its_last_segment() -> None:
    generic = dd.lookup("currency")
    specific = dd.lookup(NESTED)
    assert specific is not None
    assert specific.get("name") == NESTED
    assert specific is not generic, (
        "a nested field still inherits the generic field's entry, so an "
        "auto-created sensor would take the wrong unit and description"
    )


def test_the_prefix_is_stripped_before_the_last_segment_is_tried() -> None:
    """Candidate order is the whole fix: most specific first."""
    cands = dd._candidates("eu_data_act." + NESTED)
    assert cands[0] == "eu_data_act." + NESTED
    assert cands[1] == NESTED
    assert cands[-1] == "currency"


def test_candidates_does_not_repeat_a_spelling() -> None:
    """A key with no dots is one candidate, not three."""
    assert dd._candidates("overall") == ["overall"]
    assert len(set(dd._candidates(REPORTED))) == len(dd._candidates(REPORTED))


# ── the invariants that made the change safe ───────────────────────────────


def _catalogue_names() -> list[str]:
    return sorted({
        e["name"] for e in dd._load().values()
        if isinstance(e.get("name"), str) and e["name"]
    })


def test_every_dotted_name_in_the_catalogue_resolves_by_its_own_name() -> None:
    """The headline. 3206 names carry dots; 349 of them used to resolve."""
    dotted = [n for n in _catalogue_names() if "." in n]
    assert len(dotted) > 3000, f"only {len(dotted)} dotted names — check this"
    unresolved = [n for n in dotted if dd.lookup(n) is None]
    assert not unresolved, (
        f"{len(unresolved)} of {len(dotted)} dotted names do not resolve, "
        f"e.g. {unresolved[:3]}"
    )


def test_nothing_in_the_catalogue_resolves_to_nothing() -> None:
    """Every spelling the portal can send for a known field resolves: the bare
    name, the prefixed name, and the UUID."""
    names = _catalogue_names()
    for key in names:
        assert dd.lookup(key) is not None, key
        assert dd.lookup("eu_data_act." + key) is not None, key
    for uuid in dd._load():
        assert dd.lookup(uuid) is not None, uuid


def test_an_unknown_field_is_still_unknown() -> None:
    """The enrichment must not start inventing. A plausible-looking path that
    is not in the catalogue has to come back None, or the Scout would print a
    confident name for a field nobody has documented — which is the failure
    this fix would be worth undoing for."""
    for key in (
        "eu_data_act.viwi_DriverNotification_9.9.9_get_notifications",
        "eu_data_act.not_a_real_field_at_all",
        "00000000-0000-0000-0000-000000000000",
        "",
    ):
        assert dd.lookup(key) is None, key
        assert dd.describe(key) is None, key


def test_a_bare_last_segment_still_resolves() -> None:
    """The behaviour the old code was written for is kept: a genuine
    ``container.field`` path still falls back to the field."""
    assert dd.lookup("something.unknown.currency") is not None


def test_the_dictionary_file_is_still_well_formed() -> None:
    """Cheap guard: the lookup is only as good as the file under it."""
    path = pathlib.Path(dd.__file__).with_name("eu_data_dictionary.json")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "_meta" in raw
    assert dd.field_count() == len(raw) - 1
