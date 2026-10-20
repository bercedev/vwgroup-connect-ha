# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1902 (@keylogicjohan) — an ID.7 whose EU Data Act feed carries the pack's
energy content (``REPORT_TYPE_ENERGY_CONTENTS``: available and maximal kWh) but
no SoC, electric range, charging state or pack voltage came out
``has_battery=False``, so every ``condition="electric"`` entity was skipped,
including the available-energy and capacity sensors whose values were there.

A capacity in the traction-pack range now counts as evidence of a drive battery,
like the >= 60 V pack voltage of #1661. It sets ``has_battery`` only.
"""
from __future__ import annotations

from custom_components.vag_connect.cariad.auth._eu_data_act import (
    _walk_fields,
    map_dataset_to_vehicle_data,
)
from custom_components.vag_connect.cariad.models import VehicleData


def _map(fields: dict) -> VehicleData:
    return map_dataset_to_vehicle_data(
        _walk_fields({"eu_data_act": fields}), VehicleData(vin="X")
    )


def _energy(current_raw: str, maximal_raw: str) -> dict:
    # physical_value is in 0.1 kWh units
    return {
        "energy_contents.current_energy_content.physical_value": current_raw,
        "energy_contents.maximal_energy_content.physical_value": maximal_raw,
    }


def test_energy_content_alone_gives_the_car_a_battery() -> None:
    # the reporter's feed: 56.65 of 83.7 kWh and nothing else electric
    d = _map(_energy("566.5", "837"))
    assert d.battery_available_kwh == 56.65
    assert d.battery_cap_kwh == 83.7
    assert d.battery_soc is None
    assert d.has_battery is True


def test_it_does_not_claim_the_car_is_a_pure_ev() -> None:
    d = _map(_energy("566.5", "837"))
    assert d.is_electric is not True


def test_energy_content_plus_fuel_reads_as_a_hybrid_battery_not_an_ev() -> None:
    d = _map({**_energy("80", "104"), "fuel_level": "42"})
    assert d.has_battery is True
    assert d.has_combustion is True
    assert d.is_electric is not True


def test_mild_hybrid_sized_pack_is_not_a_traction_battery() -> None:
    # a 48 V mild-hybrid pack holds well under 2 kWh
    assert _map(_energy("4", "9")).has_battery is not True


def test_threshold_boundary() -> None:
    assert _map(_energy("40", "49")).has_battery is not True
    assert _map(_energy("40", "50")).has_battery is True
