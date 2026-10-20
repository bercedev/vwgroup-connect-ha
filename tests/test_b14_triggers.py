# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""b13 (experimental) — vehicle state-transition detector + named trigger/condition
platform registration.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
import voluptuous as vol

from custom_components.vag_connect.trigger_detect import (
    EVENT_KEYS,
    VehicleTransitionDetector,
)

# The named trigger/condition platform only exists on HA 2026.7+. The detector
# tests below run everywhere; the two platform-registration tests need the real
# base classes and are skipped on an older HA baseline (e.g. the CI floor).
try:
    from homeassistant.helpers.condition import Condition  # noqa: F401
    from homeassistant.helpers.trigger import Trigger  # noqa: F401

    _HAS_TRIGGER_PLATFORM = True
except ImportError:
    _HAS_TRIGGER_PLATFORM = False

_needs_platform = pytest.mark.skipif(
    not _HAS_TRIGGER_PLATFORM,
    reason="named trigger/condition platform requires HA 2026.7+",
)


# ── detector edge logic ───────────────────────────────────────────────────────

def _fires(prev_veh, cur_veh, vin="V1"):
    """Feed two snapshots, return the list of fired event keys for `vin`."""
    d = VehicleTransitionDetector()
    fired: list[str] = []
    for ev in EVENT_KEYS:
        d.register(ev, None, lambda p, ev=ev: fired.append(p["event"]))
    d.feed({vin: prev_veh})   # first snapshot — must NOT fire
    d.feed({vin: cur_veh})    # second — the edge
    return fired


def test_first_snapshot_never_fires() -> None:
    d = VehicleTransitionDetector()
    fired: list[str] = []
    d.register("started_charging", None, lambda p: fired.append(p["event"]))
    d.feed({"V1": {"is_charging": True}})   # first sight of V1
    assert fired == []


def test_none_to_value_does_not_fire() -> None:
    # unknown → known (e.g. after a restart) must be silent
    assert _fires({"is_charging": None}, {"is_charging": True}) == []


def test_started_and_stopped_charging() -> None:
    assert _fires({"is_charging": False}, {"is_charging": True}) == ["started_charging"]
    assert _fires({"is_charging": True}, {"is_charging": False}) == ["stopped_charging"]


def test_plug_and_precondition_edges() -> None:
    assert _fires({"plug_connected": False}, {"plug_connected": True}) == ["plugged_in"]
    assert _fires({"climatisation_active": True}, {"climatisation_active": False}) == [
        "stopped_preconditioning"
    ]


def test_lock_inversion() -> None:
    # doors_locked True→False = unlocked; False→True = locked
    assert _fires({"doors_locked": True}, {"doors_locked": False}) == ["unlocked"]
    assert _fires({"doors_locked": False}, {"doors_locked": True}) == ["locked"]


def test_charge_target_reached_upward_only() -> None:
    # crosses up into target → fires once
    assert _fires({"battery_soc": 70, "target_soc": 80},
                  {"battery_soc": 82, "target_soc": 80}) == ["charge_target_reached"]
    # already at/above target, stays there → does NOT re-fire
    assert _fires({"battery_soc": 82, "target_soc": 80},
                  {"battery_soc": 83, "target_soc": 80}) == []
    # dropping does not fire
    assert _fires({"battery_soc": 82, "target_soc": 80},
                  {"battery_soc": 78, "target_soc": 80}) == []


def test_unsub_stops_delivery() -> None:
    d = VehicleTransitionDetector()
    fired: list[str] = []
    unsub = d.register("locked", None, lambda p: fired.append(p["event"]))
    d.feed({"V1": {"doors_locked": False}})
    unsub()
    d.feed({"V1": {"doors_locked": True}})   # would be "locked" but unsubscribed
    assert fired == []


def test_vin_scoped_listener() -> None:
    d = VehicleTransitionDetector()
    fired: list[str] = []
    d.register("started_charging", "V2", lambda p: fired.append(p["vin"]))
    d.feed({"V1": {"is_charging": False}, "V2": {"is_charging": False}})
    d.feed({"V1": {"is_charging": True}, "V2": {"is_charging": True}})
    assert fired == ["V2"]   # only the V2-scoped listener fired


def test_underscore_keys_ignored() -> None:
    d = VehicleTransitionDetector()
    fired: list[str] = []
    d.register("started_charging", None, lambda p: fired.append(p["event"]))
    d.feed({"_meta": {"is_charging": False}})
    d.feed({"_meta": {"is_charging": True}})
    assert fired == []


def test_listener_exception_never_breaks_feed() -> None:
    d = VehicleTransitionDetector()
    ok: list[str] = []
    d.register("locked", None, lambda p: (_ for _ in ()).throw(ValueError("boom")))
    d.register("locked", None, lambda p: ok.append("ok"))
    d.feed({"V1": {"doors_locked": False}})
    d.feed({"V1": {"doors_locked": True}})   # first listener raises, second still runs
    assert ok == ["ok"]


# ── platform registration ─────────────────────────────────────────────────────

@_needs_platform
def test_trigger_platform_registers_all_events() -> None:
    from custom_components.vag_connect import trigger as trig
    got = asyncio.run(trig.async_get_triggers(MagicMock()))
    assert set(got) == set(EVENT_KEYS)
    # every value is a concrete Trigger subclass
    from homeassistant.helpers.trigger import Trigger
    assert all(issubclass(c, Trigger) for c in got.values())


@_needs_platform
def test_condition_platform_registers_expected() -> None:
    from custom_components.vag_connect import condition as cond
    got = asyncio.run(cond.async_get_conditions(MagicMock()))
    assert set(got) == {
        "is_charging", "is_plugged_in", "is_locked", "is_preconditioning",
        "is_charge_target_reached",
    }
    from homeassistant.helpers.condition import Condition
    assert all(issubclass(c, Condition) for c in got.values())


def test_condition_any_vehicle_matches() -> None:
    from custom_components.vag_connect import condition as cond
    coord = MagicMock()
    coord.vehicles = {"V1": {"is_charging": False}, "V2": {"is_charging": True}}
    entry = MagicMock()
    entry.runtime_data = coord
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [entry]
    assert cond._any_vehicle_matches(hass, "is_charging") is True
    coord.vehicles = {"V1": {"is_charging": False}}
    assert cond._any_vehicle_matches(hass, "is_charging") is False


# ── v4.7.11 (trigger-vin-targeting) — optional per-vehicle scoping ─────────────

_VIN17 = "WVWZZZ1JZ3W000001"


def _hass_with_coord(coord: MagicMock) -> MagicMock:
    entry = MagicMock()
    entry.runtime_data = coord
    hass = MagicMock()
    hass.config_entries.async_entries.return_value = [entry]
    return hass


def test_resolve_target_vins_from_option() -> None:
    from custom_components.vag_connect import trigger as trig
    cfg = SimpleNamespace(options={"vin": "V2"}, target=None)
    assert trig._resolve_target_vins(MagicMock(), cfg) == {"V2"}


def test_resolve_target_vins_none_when_unset() -> None:
    """Nothing asked for is None, not an empty set.

    It used to be an empty set, and so was "asked for a scope and resolved to
    no vehicle" -- the two outcomes were indistinguishable and both callers
    read them as "all vehicles". None now means only the first.
    """
    from custom_components.vag_connect import trigger as trig
    cfg = SimpleNamespace(options=None, target=None)
    assert trig._resolve_target_vins(MagicMock(), cfg) is None
    # a differently-shaped (experimental) config still degrades to "all vehicles"
    assert trig._resolve_target_vins(MagicMock(), object()) is None


def test_resolve_target_vins_from_device_id(monkeypatch: pytest.MonkeyPatch) -> None:
    from custom_components.vag_connect import trigger as trig
    from custom_components.vag_connect.const import DOMAIN
    device = SimpleNamespace(
        # the vehicle VIN + the per-entry settings device (which must be ignored)
        identifiers={(DOMAIN, "V2"), (DOMAIN, "entry1_settings"), ("other", "x")},
    )
    registry = MagicMock()
    registry.async_get.return_value = device
    monkeypatch.setattr(trig.dr, "async_get", lambda hass: registry)
    cfg = SimpleNamespace(options=None, target={"device_id": ["dev2"]})
    assert trig._resolve_target_vins(MagicMock(), cfg) == {"V2"}
    # An unknown device id resolves to nothing, not an error -- and to an EMPTY
    # SET rather than None, because a scope WAS requested. That distinction is
    # what stops the caller falling back to every vehicle on the account.
    registry.async_get.return_value = None
    assert trig._resolve_target_vins(MagicMock(), cfg) == set()
    assert trig._resolve_target_vins(MagicMock(), cfg) is not None


def test_vin_option_validated_and_uppercased() -> None:
    from custom_components.vag_connect import trigger as trig
    # lowercase input is normalised to uppercase so it matches the coordinator's
    # upper-cased VIN keys
    out = asyncio.run(
        trig.TRIGGERS["started_charging"].async_validate_config(
            MagicMock(), {"options": {"vin": _VIN17.lower()}}
        )
    )
    assert out["options"]["vin"] == _VIN17
    # a malformed VIN is rejected at config-validation time
    with pytest.raises(vol.Invalid):
        asyncio.run(
            trig.TRIGGERS["started_charging"].async_validate_config(
                MagicMock(), {"options": {"vin": "TOOSHORT"}}
            )
        )
    # no options ⇒ passthrough, byte-identical to v1
    assert asyncio.run(
        trig.TRIGGERS["started_charging"].async_validate_config(MagicMock(), {})
    ) == {}


@_needs_platform
def test_trigger_unset_vin_fires_for_all_vehicles() -> None:
    from custom_components.vag_connect import trigger as trig
    det = VehicleTransitionDetector()
    coord = MagicMock()
    coord.register_transition_listener = det.register
    hass = _hass_with_coord(coord)
    cfg = SimpleNamespace(key="started_charging", options=None, target=None)
    t = trig.TRIGGERS["started_charging"](hass, cfg)
    fired: list[str] = []
    asyncio.run(t.async_attach_runner(lambda payload, desc: fired.append(payload["vin"])))
    det.feed({"V1": {"is_charging": False}, "V2": {"is_charging": False}})
    det.feed({"V1": {"is_charging": True}, "V2": {"is_charging": True}})
    assert sorted(fired) == ["V1", "V2"]


@_needs_platform
def test_trigger_vin_option_fires_only_that_vehicle() -> None:
    from custom_components.vag_connect import trigger as trig
    det = VehicleTransitionDetector()
    coord = MagicMock()
    coord.register_transition_listener = det.register
    hass = _hass_with_coord(coord)
    cfg = SimpleNamespace(key="started_charging", options={"vin": "V2"}, target=None)
    t = trig.TRIGGERS["started_charging"](hass, cfg)
    fired: list[str] = []
    asyncio.run(t.async_attach_runner(lambda payload, desc: fired.append(payload["vin"])))
    det.feed({"V1": {"is_charging": False}, "V2": {"is_charging": False}})
    det.feed({"V1": {"is_charging": True}, "V2": {"is_charging": True}})
    assert fired == ["V2"]


@_needs_platform
def test_trigger_device_id_fires_only_that_vehicle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.vag_connect import trigger as trig
    from custom_components.vag_connect.const import DOMAIN
    device = SimpleNamespace(identifiers={(DOMAIN, "V2")})
    registry = MagicMock()
    registry.async_get.return_value = device
    monkeypatch.setattr(trig.dr, "async_get", lambda hass: registry)
    det = VehicleTransitionDetector()
    coord = MagicMock()
    coord.register_transition_listener = det.register
    hass = _hass_with_coord(coord)
    cfg = SimpleNamespace(
        key="started_charging", options=None, target={"device_id": ["dev2"]}
    )
    t = trig.TRIGGERS["started_charging"](hass, cfg)
    fired: list[str] = []
    asyncio.run(t.async_attach_runner(lambda payload, desc: fired.append(payload["vin"])))
    det.feed({"V1": {"is_charging": False}, "V2": {"is_charging": False}})
    det.feed({"V1": {"is_charging": True}, "V2": {"is_charging": True}})
    assert fired == ["V2"]


def test_condition_vin_scopes_to_one_vehicle() -> None:
    from custom_components.vag_connect import condition as cond
    coord = MagicMock()
    coord.vehicles = {"V1": {"is_charging": True}, "V2": {"is_charging": False}}
    hass = _hass_with_coord(coord)
    # unset ⇒ any vehicle charging (V1) matches
    assert cond._any_vehicle_matches(hass, "is_charging") is True
    # scoped to V2 (not charging) ⇒ no match, even though V1 is charging
    assert cond._any_vehicle_matches(hass, "is_charging", {"V2"}) is False
    # scoped to V1 ⇒ matches
    assert cond._any_vehicle_matches(hass, "is_charging", {"V1"}) is True


@_needs_platform
def test_condition_check_honours_vin_option() -> None:
    from custom_components.vag_connect import condition as cond
    coord = MagicMock()
    coord.vehicles = {"V1": {"is_charging": True}, "V2": {"is_charging": False}}
    hass = _hass_with_coord(coord)
    cfg = SimpleNamespace(options={"vin": "V2"}, target=None)
    c = cond.CONDITIONS["is_charging"](hass, cfg)
    assert c._async_check() is False   # V2 is not charging
    cfg_all = SimpleNamespace(options=None, target=None)
    c_all = cond.CONDITIONS["is_charging"](hass, cfg_all)
    assert c_all._async_check() is True   # any vehicle (V1) charging
