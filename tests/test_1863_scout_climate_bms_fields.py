# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1863 (Scout, @Massen88, VW ID.4 Pro on vag_connect 5.0.0) — six leaves.

All six were already in the official dictionary this integration ships, and all
six carried ``"unit": null, "type": null`` there. The Scout report is what made
them mappable: a real car supplied the unit tokens VW's own catalogue omits —
``"65.5 Unit_PerCent"`` and ``"0.0 Unit_Watt"``.

Two of the six are traps, and both are documented as such by the catalogue
itself rather than guessed at here:

* ``BMS_Ladevorgang_aktiv`` — "Signal bleibt nach Kl.15 ein aktiv, bis
  ESP_Stillstandsflag geloescht". It LATCHES: it stays active after
  ignition-on until the standstill flag clears, so it reads active when nothing
  is charging. It must therefore never reach ``charging_state``, which the
  brand channels own. A user automation on "charging" that fired every time the
  car was parked would be the consequence.
* ``BCM1_FStatus_Aussentemp_ungef`` — the name reads like a temperature and the
  value is ``"iO "``. It is the FAULT STATUS of the outside-temperature sensor
  (``Fehlerstatus fuer Aussentemperatur``), and specifically of the same
  ``Klima_Sensor_02`` module one of the three outside-temperature paths already
  reads. Published as a status, never as a number.

The third thing these samples teach: the portal ships these enum strings with a
trailing space. Unstripped, the state is ``"inaktiv "`` and every comparison a
user writes against ``inaktiv`` fails.
"""
from __future__ import annotations

import json
from pathlib import Path

from custom_components.vag_connect.cariad.auth._eu_data_act import (
    _walk_fields,
    map_dataset_to_vehicle_data,
)
from custom_components.vag_connect.cariad.models import VehicleData

E3 = "_XIX_E3V_VLAN_Connect"

#: Verbatim from the Scout report, trailing spaces included.
REAL = {
    f"FS_Luftfeuchte_rel_XIX_Klima_Sensor_01{E3}": "65.5 Unit_PerCent",
    f"KL_IstLeistung_XIX_Klima_EV_06{E3}": "0.0 Unit_Watt",
    f"BMS_DC_Ladeprofil_XIX_BMS_26{E3}": "DCLaden ",
    f"BMS_KundenWarnung_XIX_BMS_07{E3}": "keine_Warnung ",
    f"BMS_Ladevorgang_aktiv_XIX_BMS_24{E3}": "inaktiv ",
    f"BCM1_FStatus_Aussentemp_ungef_XIX_Klima_Sensor_02_MQB{E3}": "iO ",
}

_ROOT = Path(__file__).resolve().parent.parent
_PKG = _ROOT / "custom_components" / "vag_connect"
_KEYS = (
    "climate_humidity_pct",
    "climate_power_w",
    "hv_dc_charging_profile",
    "hv_battery_warning",
    "hv_charging_signal_active",
    "outside_temp_sensor_status",
)


def _map(fields: dict, base: VehicleData | None = None) -> VehicleData:
    return map_dataset_to_vehicle_data(
        _walk_fields({"eu_data_act": fields}), base or VehicleData(vin="X")
    )


# ── the reported car ──────────────────────────────────────────────────────────
def test_the_reported_car_yields_all_six() -> None:
    d = _map(dict(REAL))
    assert d.climate_humidity_pct == 65.5
    assert d.climate_power_w == 0.0
    assert d.hv_dc_charging_profile == "DCLaden"
    assert d.hv_battery_warning == "keine_Warnung"
    assert d.hv_charging_signal_active == "inaktiv"
    assert d.outside_temp_sensor_status == "iO"


def test_the_trailing_space_is_stripped() -> None:
    """The portal ships these with one. Left on, every state carries it and a
    user comparing against ``inaktiv`` or ``iO`` never matches."""
    d = _map(dict(REAL))
    for value in (
        d.hv_dc_charging_profile, d.hv_battery_warning,
        d.hv_charging_signal_active, d.outside_temp_sensor_status,
    ):
        assert value is not None
        assert value == value.strip(), repr(value)


def test_the_power_reading_keeps_a_true_zero() -> None:
    """0 W with the climate off is a reading, not a missing value. A falsy
    check instead of ``is not None`` would drop it and the sensor would go
    unavailable exactly when the answer is "nothing"."""
    d = _map({f"KL_IstLeistung_XIX_Klima_EV_06{E3}": "0.0 Unit_Watt"})
    assert d.climate_power_w == 0.0
    assert d.climate_power_w is not None


# ── the two traps ─────────────────────────────────────────────────────────────
def test_the_latching_charge_signal_never_becomes_the_charging_state() -> None:
    """The trap the catalogue documents itself: the signal stays active after
    ignition-on until the standstill flag clears, so "aktiv" does not mean
    charging. Wiring it to ``charging_state`` would fire every "car is
    charging" automation each time the owner parked.
    """
    d = _map({f"BMS_Ladevorgang_aktiv_XIX_BMS_24{E3}": "aktiv "})
    assert d.hv_charging_signal_active == "aktiv"
    assert d.charging_state is None, (
        "the latching BMS signal reached charging_state — it reads active when "
        "the car is merely switched on"
    )


def test_an_existing_charging_state_is_left_alone() -> None:
    """And the other direction: a real state from a brand channel must survive
    the portal parse untouched."""
    base = VehicleData(vin="X")
    base.charging_state = "charging"
    d = _map({f"BMS_Ladevorgang_aktiv_XIX_BMS_24{E3}": "inaktiv "}, base)
    assert d.charging_state == "charging"
    assert d.hv_charging_signal_active == "inaktiv"


def test_the_sensor_fault_status_is_not_read_as_a_temperature() -> None:
    """``BCM1_FStatus_Aussentemp_ungef`` reads like a temperature and is not
    one. The outside temperature comes off ``BCM1_Aussen_Temp_ungef`` — same
    module, different leaf — and that one must still win."""
    d = _map({
        f"BCM1_FStatus_Aussentemp_ungef_XIX_Klima_Sensor_02_MQB{E3}": "iO ",
        f"BCM1_Aussen_Temp_ungef_XIX_Klima_Sensor_02_MQB{E3}": "18.0 Unit_DegreCelsi",
    })
    assert d.outside_temp_sensor_status == "iO"
    assert d.outside_temp == 18.0


# ── bounds and absence ────────────────────────────────────────────────────────
def test_an_impossible_humidity_is_refused() -> None:
    """A relative humidity outside 0-100 is not a reading. These portal leaves
    do ship out-of-range sentinels — the SAC temperature in this same parser is
    consumed and dropped at exactly -40."""
    for sample in ("-1.0 Unit_PerCent", "255.0 Unit_PerCent", "1000 Unit_PerCent"):
        d = _map({f"FS_Luftfeuchte_rel_XIX_Klima_Sensor_01{E3}": sample})
        assert d.climate_humidity_pct is None, sample
    # The edges themselves are valid.
    for sample, want in (("0.0 Unit_PerCent", 0.0), ("100.0 Unit_PerCent", 100.0)):
        assert _map(
            {f"FS_Luftfeuchte_rel_XIX_Klima_Sensor_01{E3}": sample}
        ).climate_humidity_pct == want


def test_a_car_without_these_leaves_gets_nothing() -> None:
    """No phantom values on the cars that do not report them — which is most of
    them, since this is one ID.4 Pro's feed."""
    d = _map({f"BMS_Spannung_XIX_BMS_20{E3}": "355.0 Unit_Volt"})
    for key in _KEYS:
        assert getattr(d, key) is None, key


def test_an_empty_string_is_absence_not_a_state() -> None:
    d = _map({
        f"BMS_DC_Ladeprofil_XIX_BMS_26{E3}": "   ",
        f"BMS_KundenWarnung_XIX_BMS_07{E3}": "",
    })
    assert d.hv_dc_charging_profile is None
    assert d.hv_battery_warning is None


# ── the entity chain, which is where a half-done field shows up ──────────────
def test_every_field_has_an_entity_and_is_portal_gated() -> None:
    """Three separate places have to agree or the field is invisible, or worse,
    appears as a phantom on cars that never report it."""
    src = (_PKG / "sensor.py").read_text(encoding="utf-8")
    for key in _KEYS:
        assert f'key="{key}"' in src, f"{key} has no sensor description"
        assert f'data_key="{key}"' in src, f"{key} is not wired to the data key"
        assert f'translation_key="{key}"' in src, f"{key} has no translation key"
        assert f'    "{key}",' in src, (
            f"{key} is not in the portal-gated list, so it would surface as a "
            "phantom entity on cars whose feed does not carry it"
        )


def test_every_field_is_named_in_english_and_in_all_twelve_languages() -> None:
    """A missing translation shows the raw key in the UI, which is the kind of
    thing that ships because nothing fails."""
    files = [_PKG / "strings.json"] + sorted(
        (_PKG / "translations").glob("*.json")
    )
    assert len(files) == 13, f"expected strings.json + 12 translations, got {len(files)}"
    for path in files:
        section = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
        for key in _KEYS:
            assert key in section, f"{path.name} is missing {key}"
            name = section[key].get("name", "")
            assert name, f"{path.name}: {key} has an empty name"
            assert name != key, f"{path.name}: {key} was left as the raw key"


def test_the_translations_are_translated_and_not_copies_of_english() -> None:
    """The project's own bar: every other entity is in real local vocabulary.
    Checked against the languages where every one of these six genuinely
    differs from English, so the test cannot pass on a copy-paste.
    """
    en = json.loads((_PKG / "strings.json").read_text(encoding="utf-8"))
    en_names = {k: en["entity"]["sensor"][k]["name"] for k in _KEYS}
    for lang in ("de", "fr", "es", "it", "nl", "pl", "cs", "fi"):
        path = _PKG / "translations" / f"{lang}.json"
        section = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
        for key in _KEYS:
            assert section[key]["name"] != en_names[key], (
                f"{lang}.json: {key} is still the English string"
            )
