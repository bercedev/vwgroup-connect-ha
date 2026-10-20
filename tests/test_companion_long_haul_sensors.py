# SPDX-License-Identifier: AGPL-3.0-or-later
"""The app's long-haul card: companion-only fields and entities, no cloud field."""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.vag_connect.cariad.models import VehicleData
from custom_components.vag_connect.companion.presets import PRESETS
from custom_components.vag_connect.companion.resources import read_driving_data
from custom_components.vag_connect.companion.screen import parse_ui_dump
from custom_components.vag_connect.sensor import SENSOR_DESCRIPTIONS
from custom_components.vag_connect.sensor import _DATA_PRESENT_REQUIRED as SENSOR_REQUIRED

ROOT = Path(__file__).parents[1] / "custom_components" / "vag_connect"
FIXTURES = Path(__file__).parent / "fixtures"
FIELDS = tuple(f"companion_long_trip_{part}" for part in (
    "distance_km", "duration_min", "avg_speed_kmh",
    "avg_fuel_consumption_l_100km", "avg_electric_consumption_kwh_100km",
))
STRINGS = {
    k: set(v) for name in ("companion_battery/vw_432_resources.json", "companion_driving/vw_481_extra_resources.json")
    for k, v in json.loads((FIXTURES / name).read_text(encoding="utf-8")).items()
}


def test_fields_nullable_gated_and_translated():
    data = VehicleData(vin="TEST")
    assert all(getattr(data, key) is None for key in FIELDS)
    assert {d.key for d in SENSOR_DESCRIPTIONS if d.key in FIELDS} == set(FIELDS)
    # No entity before the companion read the card: cloud entries never get them.
    assert set(FIELDS) <= SENSOR_REQUIRED
    for path in [ROOT / "strings.json", *(ROOT / "translations").glob("*.json")]:
        sensors = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
        assert all(sensors[key]["name"] for key in FIELDS), path.name


def test_no_cloud_parser_fills_the_long_haul_fields():
    for path in (ROOT / "cariad").rglob("*.py"):
        if path.name == "models.py":
            continue
        assert "companion_long_trip" not in path.read_text(encoding="utf-8"), path


def test_live_481_card_reaches_vehicle_data_and_nav_targets():
    out = read_driving_data(
        parse_ui_dump((FIXTURES / "companion_driving" / "long_first_481.xml").read_text(encoding="utf-8")),
        STRINGS,
    )
    data = VehicleData(vin="TEST")
    for key, val in out.items():
        assert hasattr(data, key), key  # the companion client keeps only model fields
        setattr(data, key, val)
    assert data.companion_long_trip_distance_km == 499
    assert data.companion_long_trip_duration_min == 1317
    assert data.companion_long_trip_avg_electric_consumption_kwh_100km == 19.0
    assert data.lifetime_distance_km is None  # the cloud's own field stays untouched
    nav = next(n for n in PRESETS["volkswagen"].nav_reads if n.name == "driving_data")
    assert {"companion_long_trip_distance_km", "companion_long_trip_duration_min"} <= set(nav.resource_targets)
