# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A portal value you cannot trace is a portal value you cannot fix.

@Joassens reported (#465) that his Passat GTE's fuel level sat at **exactly
100 %** on every portal snapshot while the car drove 467 km and the tank went
to about half. He killed his own first explanation — "stale because I hadn't
used any fuel" — with the odometer, and ended with the sentence this change
exists for:

    I can't tell which from diagnostics, since they don't include the raw
    portal field.

He is right. The mapper reads most fields from a list of candidate leaf names,
first present wins:

    fuel = _to_int(first("fuel_level_current_level", "tank_current_level",
                         "fuelLevel_pct", "fuel_level", "fuelLevel"))

``field_sources`` records which *channel* won and ``field_captured_ts`` when
the car measured it. Neither records which *leaf* won. And elimination cannot
recover it: ``first()`` marks every candidate of the call as used, plus their
walker synonyms, so all five disappear from ``raw_unmapped_fields`` together.

``_record`` is handed the chosen leaf already — it used it to look up a
timestamp and threw the name away. It now keeps it, in a map that follows the
same path as its two siblings: never gap-filled field by field, rebuilt in the
channel merge from the surviving owner, and carried with the value through a
historical import.

The last of those is the one with teeth. A leaf name that outlived the value it
described would be worse than no leaf name at all — it would attribute a live
channel's reading to a portal field. The merge test below is what holds that.
"""
from __future__ import annotations

from custom_components.vag_connect.cariad import _channel_merge as _cm
from custom_components.vag_connect.cariad.models import VehicleData


def _vd(vin: str = "WVWZZZ3CZZW000000", **kw: object) -> VehicleData:
    return VehicleData(vin=vin, **kw)  # type: ignore[arg-type]


# ── the map is filled where the value is decided ───────────────────────────


def test_the_parser_records_the_leaf_it_chose() -> None:
    """Driven through the real parser rather than by calling ``_record``: a
    test that pokes the helper directly would pass even if no mapping site
    ever asked for it."""
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        map_dataset_to_vehicle_data,
    )

    d = _vd()
    # ``tank_current_level`` is the SECOND alias; the first is absent, so a
    # recorder that stored names[0] instead of the match would fail here.
    map_dataset_to_vehicle_data({"tank_current_level": 42}, d)
    assert d.fuel_level == 42
    assert d.field_source_leaf.get("fuel_level") == "tank_current_level"


def test_a_different_alias_is_recorded_as_itself() -> None:
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        map_dataset_to_vehicle_data,
    )

    d = _vd()
    map_dataset_to_vehicle_data({"fuel_level": 80}, d)
    assert d.fuel_level == 80
    assert d.field_source_leaf.get("fuel_level") == "fuel_level"


def test_first_present_wins_is_what_gets_recorded() -> None:
    """Both aliases present: the recorded leaf must be the one the value came
    from, which is the whole point — @Joassens's question is unanswerable
    unless this is the winner rather than a candidate."""
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        map_dataset_to_vehicle_data,
    )

    d = _vd()
    map_dataset_to_vehicle_data(
        {"fuel_level_current_level": 55, "tank_current_level": 100}, d
    )
    assert d.fuel_level == 55
    assert d.field_source_leaf.get("fuel_level") == "fuel_level_current_level"


def test_nothing_is_recorded_when_no_alias_is_present() -> None:
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        map_dataset_to_vehicle_data,
    )

    d = _vd()
    map_dataset_to_vehicle_data({"odometer": 1000}, d)
    assert "fuel_level" not in d.field_source_leaf


# ── the merge: a leaf must never outlive its value ─────────────────────────


def test_the_merge_takes_the_leaf_from_the_channel_that_owns_the_value() -> None:
    """The base is the FIRST source and is deep-copied, so a merge that simply
    inherited the base's map would pass a one-channel test without rebuilding
    anything. Here the base does not carry the field at all: the leaf can only
    reach the result through the rebuild."""
    live = _vd()  # no fuel_level, no leaf map
    portal = _vd(fuel_level=100)
    portal.field_source_leaf = {"fuel_level": "tank_current_level"}

    merged = _cm.merge_channels([("vwde", live), ("eu_data_act", portal)])
    assert merged.fuel_level == 100
    assert merged.field_sources.get("fuel_level") == "eu_data_act"
    assert merged.field_source_leaf.get("fuel_level") == "tank_current_level"


def test_a_leaf_is_dropped_for_a_field_a_live_channel_took_over() -> None:
    """The one that matters, and it has to be measured in the same merge as a
    field that DOES keep its leaf — otherwise "not in" passes on an empty map
    and proves nothing.

    The portal carries a leaf for both fields. The live channel supersedes the
    fuel level but has nothing to say about the oil level, so the result must
    keep exactly one of the two leaves. A leaf that outlived its value would
    attribute a live channel's reading to a portal field, which is worse than
    recording nothing.
    """
    live = _vd(fuel_level=48)
    portal = _vd(fuel_level=100, oil_level_pct=70)
    portal.field_source_leaf = {
        "fuel_level": "tank_current_level",
        "oil_level_pct": "oil_level_actual_level",
    }

    merged = _cm.merge_channels([("vwde", live), ("eu_data_act", portal)])

    assert merged.fuel_level == 48
    assert merged.field_sources.get("fuel_level") == "vwde"
    assert "fuel_level" not in merged.field_source_leaf

    assert merged.oil_level_pct == 70
    assert merged.field_sources.get("oil_level_pct") == "eu_data_act"
    assert merged.field_source_leaf.get("oil_level_pct") == "oil_level_actual_level"


def test_a_channel_without_the_map_merges_without_error() -> None:
    """Every brand parser builds a VehicleData and none of them fill this map.
    An empty one must merge as an empty one, not raise."""
    brand = _vd(fuel_level=60)
    brand.field_sources = {"fuel_level": "vw_eu"}
    merged = _cm.merge_channels([("vw_eu", brand)])
    assert merged.fuel_level == 60
    assert merged.field_source_leaf == {}


# ── the provenance maps stay a closed set ──────────────────────────────────


def test_the_new_map_is_treated_as_provenance_everywhere_it_has_to_be() -> None:
    """Three places know these maps are attribute-keyed provenance rather than
    readings. Missing any one of them produces a different silent bug, so the
    membership is asserted rather than left to a reviewer's eye:

    * the merge's skip set — otherwise the generic gap-fill mixes one
      channel's leaf onto another channel's field;
    * the historical-import provenance list — otherwise an imported value
      arrives anonymous, which is the #1780 bug exactly.
    """
    from custom_components.vag_connect.coordinator import _PROVENANCE_MAPS

    assert "field_source_leaf" in _cm._SKIP_FIELDS
    assert "field_source_leaf" in _PROVENANCE_MAPS
    # and its two siblings are still there, since this test is also the guard
    # against someone "tidying" the set.
    for sibling in ("field_captured_ts", "ambiguous_fields"):
        assert sibling in _cm._SKIP_FIELDS
    assert "field_captured_ts" in _PROVENANCE_MAPS


def test_the_leaf_name_is_never_a_value() -> None:
    """A leaf NAME is safe to publish; a leaf VALUE may not be. The recorder
    must store the key it matched, never what was under it."""
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        map_dataset_to_vehicle_data,
    )

    d = _vd()
    map_dataset_to_vehicle_data({"tank_current_level": 77}, d)
    assert d.field_source_leaf.get("fuel_level") == "tank_current_level"
    assert "77" not in str(d.field_source_leaf)
