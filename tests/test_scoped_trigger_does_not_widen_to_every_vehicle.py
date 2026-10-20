# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""An automation written for one car must not quietly apply to all of them.

``_resolve_target_vins`` returned a bare ``set()`` for four different
situations, and its docstring described one of them:

1. nothing was asked for — account-wide is correct;
2. a differently-shaped (experimental) config — account-wide is the safe
   degradation;
3. ``target.device_id`` was given and the registry no longer knows it — a car
   removed and re-added is enough;
4. a ``target:`` expressed with any field other than ``device_id`` (an area, a
   label, an entity).

Both consumers then failed **open** on all four. The trigger did
``tuple(target_vins) or (None,)``, and ``vin=None`` is the account-wide
subscription — ``trigger_detect`` fires a ``None`` listener for every VIN. The
condition guarded with ``if vins and str(vin) not in vins``, which is skipped
entirely on a falsy empty set, so every vehicle was tested.

So cases 3 and 4 — both of which say "I meant one car" — did the opposite of
what was asked, and said nothing about it.

The resolver now answers three ways: ``None`` for "nothing asked", a non-empty
set for a resolved scope, and an **empty set** for "asked and resolved to
nothing". The callers honour the difference, and the unresolved case logs a
warning naming the target, because narrowing silently would replace one
surprise with another for anyone whose automation has been firing account-wide
by accident.
"""
from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.vag_connect import condition as cond
from custom_components.vag_connect import trigger as trig
from custom_components.vag_connect.const import DOMAIN

_LOG_TRIGGER = "custom_components.vag_connect.trigger"


# ── the resolver tells the three cases apart ───────────────────────────────


def test_nothing_asked_for_is_none() -> None:
    assert trig._resolve_target_vins(MagicMock(), SimpleNamespace(options=None, target=None)) is None


def test_a_resolved_scope_is_the_vins() -> None:
    assert trig._resolve_target_vins(
        MagicMock(), SimpleNamespace(options={"vin": "V2"}, target=None)
    ) == {"V2"}


def test_a_stale_device_id_is_an_empty_set_not_none(caplog, monkeypatch) -> None:
    """The case a user actually hits: the car was removed and re-added, so the
    automation still names a device id the registry has forgotten."""
    registry = MagicMock()
    registry.async_get.return_value = None
    monkeypatch.setattr(trig.dr, "async_get", lambda hass: registry)
    cfg = SimpleNamespace(options=None, target={"device_id": ["gone"]})
    with caplog.at_level(logging.WARNING, logger=_LOG_TRIGGER):
        got = trig._resolve_target_vins(MagicMock(), cfg)
    assert got == set()
    assert got is not None, "an empty set and None must not be confused"
    assert any("gone" in r.getMessage() for r in caplog.records), \
        "the warning has to name the target, or it cannot be acted on"


def test_a_target_we_cannot_read_is_an_empty_set(caplog) -> None:
    """A ``target:`` with an area or a label resolves to no device here.
    Widening to the whole account would be the same failure in a new coat."""
    cfg = SimpleNamespace(options=None, target={"area_id": ["garage"]})
    with caplog.at_level(logging.WARNING, logger=_LOG_TRIGGER):
        got = trig._resolve_target_vins(MagicMock(), cfg)
    assert got == set()
    assert any("area_id" in r.getMessage() for r in caplog.records)


def test_an_unreadable_config_still_degrades_to_account_wide() -> None:
    """The experimental-config guard stays: a shape we cannot read at all means
    nothing was asked, not that something failed."""
    assert trig._resolve_target_vins(MagicMock(), object()) is None


# ── the condition honours the difference ───────────────────────────────────


def _coord(vehicles: dict[str, Any]) -> Any:
    return SimpleNamespace(vehicles=vehicles)


@pytest.fixture
def _two_cars(monkeypatch: pytest.MonkeyPatch) -> None:
    """Two locked cars, so "any vehicle matches" is True account-wide."""
    cars = {"VIN_A": {"doors_locked": True}, "VIN_B": {"doors_locked": True}}
    monkeypatch.setattr(cond, "_coordinators", lambda hass: [_coord(cars)])
    monkeypatch.setattr(cond, "_vehicle_matches", lambda key, veh: bool(veh.get("doors_locked")))


def test_condition_unscoped_still_checks_every_vehicle(_two_cars) -> None:
    assert cond._any_vehicle_matches(MagicMock(), "locked", None) is True


def test_condition_scoped_to_a_real_vin_checks_only_that_one(_two_cars) -> None:
    assert cond._any_vehicle_matches(MagicMock(), "locked", {"VIN_A"}) is True
    assert cond._any_vehicle_matches(MagicMock(), "locked", {"VIN_NOPE"}) is False


def test_condition_scoped_to_nothing_matches_nothing(_two_cars) -> None:
    """The defect, from the condition's side. Both cars are locked, so the old
    behaviour returned True for a condition the user scoped to a car that no
    longer resolves."""
    assert cond._any_vehicle_matches(MagicMock(), "locked", set()) is False


# ── the trigger honours it too ─────────────────────────────────────────────


class _Coord:
    def __init__(self) -> None:
        self.registered: list[str | None] = []

    def register_transition_listener(self, key: str, vin: str | None, cb: Any) -> Any:
        self.registered.append(vin)
        return lambda: None


def _attach(monkeypatch: pytest.MonkeyPatch, cfg: Any, device_vin: str | None = None) -> _Coord:
    coord = _Coord()
    monkeypatch.setattr(trig, "_coordinators", lambda hass: [coord])
    if device_vin is not None:
        registry = MagicMock()
        registry.async_get.return_value = SimpleNamespace(
            identifiers={(DOMAIN, device_vin)}
        )
        monkeypatch.setattr(trig.dr, "async_get", lambda hass: registry)
    t = trig._make_trigger("unlocked")(MagicMock(), cfg)
    asyncio.run(t.async_attach_runner(lambda *a, **k: None))
    return coord


def test_trigger_unscoped_subscribes_account_wide(monkeypatch) -> None:
    coord = _attach(monkeypatch, SimpleNamespace(options=None, target=None))
    assert coord.registered == [None], "None is the account-wide subscription"


def test_trigger_scoped_subscribes_only_for_that_vin(monkeypatch) -> None:
    coord = _attach(
        monkeypatch, SimpleNamespace(options={"vin": "VIN_A"}, target=None)
    )
    assert coord.registered == ["VIN_A"]


def test_trigger_scoped_to_nothing_subscribes_to_nothing(monkeypatch) -> None:
    """The defect, from the trigger's side. ``tuple(set()) or (None,)`` used to
    produce ``(None,)`` here — the account-wide listener, which
    ``trigger_detect`` fires for every VIN."""
    registry = MagicMock()
    registry.async_get.return_value = None  # stale device id
    monkeypatch.setattr(trig.dr, "async_get", lambda hass: registry)
    coord = _attach(
        monkeypatch, SimpleNamespace(options=None, target={"device_id": ["gone"]})
    )
    assert coord.registered == [], (
        "a scope that resolved to nothing must subscribe to nothing, "
        "not fall back to every vehicle"
    )


def test_the_account_wide_listener_really_does_fire_for_every_vin() -> None:
    """The claim the two tests above rest on, checked at its source rather than
    assumed: a listener registered with vin=None matches any VIN."""
    import inspect

    from custom_components.vag_connect import trigger_detect

    src = inspect.getsource(trigger_detect)
    assert "lvin is None or lvin == vin" in src, (
        "the dispatcher's None-means-all rule moved; this test's premise needs "
        "re-checking before the two above can be trusted"
    )
