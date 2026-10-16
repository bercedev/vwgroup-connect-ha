# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1827 — two separate defects behind one stale state-of-charge.

@Fishermanjb reported a battery level several days old sitting next to a
volkswagen.de channel that was plainly being read. Both halves are here because
the second makes the first load-bearing.

**The relations veto.** ``get_vehicle_data`` opens with a relations preflight
that resolves the car's platform. A 401 there used to ``raise`` on the spot,
unwinding past the five remaining core reads — charging, maintenance, fuel and
the three trip memories. His relations endpoint was walled while master data on
the same poll answered, so the charging read never ran, the vw.de snapshot
carried no ``battery_soc`` at all, and the merge did the right thing with what
it had: it kept the batch feed's last capture. Nothing in the merge was wrong.
This is the same veto #1 removed one read further down, for the same reason —
relations is realm ``vw-de``, charging is realm ``vwag-weconnect``, so a wall on
the first does not establish that the session is dead.

**The dead-session detector that could not fire.** The decision at the end of
the function (#465) keeps the partial snapshot when "the tail delivered
something" and otherwise re-raises, which is what drives the caller's
refresh+retry (``api/base.py:608-613``) and sole mode's reactive refresh
(``api/vw_eu.py:844-846``). But it asked whether a VALUE was present —
``d.model or d.exterior_color or …`` — and ``get_master_data`` answers from a
24 h cache with no network read, and past that TTL answers with the stale cached
value when both of its own reads soft-fail. So from the first successful poll
onward the tail always "delivered", ``_tail_ok`` was permanently True for the
life of the connector instance, and the re-raise was unreachable. A genuinely
dead session had no reactive recovery left — only the proactive 10-minute
``maybe_roll``.

Why the two existing regression tests are green over that hole: both build the
connector with ``__new__`` and mock ``get_master_data`` away, so they only ever
exercise the cold-cache state, where the old check happened to give the right
answer. The tests below drive the real cached getters instead.
"""
from __future__ import annotations

import asyncio
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vag_connect.cariad._authproxy import AuthproxyVehicleInfo
from custom_components.vag_connect.cariad.auth._website_authproxy import (
    _STATIC_MASTER_TTL_S,
    WebsiteAuthProxyConnector,
)
from custom_components.vag_connect.cariad.exceptions import AuthenticationError

MOD = "custom_components.vag_connect.cariad.auth._website_authproxy"
VIN = "WVWZZZ0000000345"

# The model name a warm cache would hand back. Invented.
_CACHED = AuthproxyVehicleInfo(model_name="Tiguan", model_year="2023")

_TAIL_LABELS = ("vwde_master_details", "vwde_master_data", "vwde_images")


def _conn(*, master_age_s: float | None = None) -> WebsiteAuthProxyConnector:
    """A connector double with the REAL tail caches.

    ``master_age_s`` pre-seeds ``_master_cache`` as if an earlier poll had filled
    it that many seconds ago; ``None`` leaves it cold.
    """
    c = WebsiteAuthProxyConnector.__new__(WebsiteAuthProxyConnector)
    c.probe_outcomes = {}
    c.mbb_eligibility = {}
    c._vin_backend = {}
    c._master_cache = {}
    c._images_cache = {}
    if master_age_s is not None:
        c._master_cache[VIN] = (time.monotonic() - master_age_s, _CACHED)
    c.get_relations = AsyncMock(return_value=MagicMock(vehicles=[]))
    c._gdc = MagicMock(return_value="gdc")
    c.get_warning_lights = AsyncMock(return_value=None)
    c.get_last_lock_action = AsyncMock(return_value=None)
    c.get_relation_detail = AsyncMock(return_value=None)
    c._should_probe_position = MagicMock(return_value=False)
    c._should_probe_soh = MagicMock(return_value=False)
    c._should_probe_measurements = MagicMock(return_value=False)
    # The position probe's own budget counters. Set because one test below turns
    # that probe on to observe a read AFTER the enrich block, and ``__new__``
    # skips ``__init__`` where these live (_website_authproxy.py:849-850).
    c._position_probe_tries = 0
    c._position_available = False
    return c


def _reads(**bodies: Any):
    """A ``_get_json`` side effect keyed on ``record_as``.

    Anything named in *bodies* answers with that value; every other core read
    walls. Keyed on the label rather than on call order so adding a read to the
    connector cannot silently re-point an assertion at a different endpoint.
    Every label asked for is recorded on ``.seen``, so a test can assert that a
    read was REACHED rather than only that its value arrived.
    """
    seen: list[str] = []

    async def _side(*_a: Any, **kw: Any) -> Any:
        label = kw.get("record_as") or ""
        seen.append(label)
        if label in bodies:
            return bodies[label]
        if label in _TAIL_LABELS or kw.get("optional"):
            return None  # a tail read, or an optional probe, soft-failed
        raise AuthenticationError(f"{label} -> HTTP 401")

    _side.seen = seen  # type: ignore[attr-defined]
    return _side


class TestTheDeadSessionDetectorCanFireAgain:
    """``_tail_ok`` must describe this poll, not the cache."""

    def test_a_warm_cache_inside_the_ttl_does_not_count_as_delivered(self) -> None:
        """The 24 h cache-hit path: ``get_master_data`` returns without touching
        the network, so nothing about the session was learned. Before the fix the
        cached model satisfied ``_tail_ok`` and the re-raise never happened."""
        c = _conn(master_age_s=100.0)
        c._get_json = AsyncMock(side_effect=_reads())
        with pytest.raises(AuthenticationError):
            asyncio.run(c.get_vehicle_data(VIN))

    def test_a_stale_cache_past_the_ttl_does_not_count_either(self) -> None:
        """The other cached path: past the TTL both master reads are attempted,
        both soft-fail, and the getter returns the stale value WITHOUT
        re-stamping the cache. Same verdict, different route — so the fix cannot
        be satisfied by a TTL check alone."""
        c = _conn(master_age_s=_STATIC_MASTER_TTL_S + 3600.0)
        c._get_json = AsyncMock(side_effect=_reads())
        with pytest.raises(AuthenticationError):
            asyncio.run(c.get_vehicle_data(VIN))
        # the stale entry is still there, still stale — the fix must not have
        # "fixed" this by evicting the cache
        assert c._master_cache[VIN][1] is _CACHED

    def test_a_fresh_tail_read_still_keeps_the_snapshot(self) -> None:
        """Counter-check, and the whole point of #465: when the tail genuinely
        reaches the backend the session IS alive, so the partial snapshot must be
        returned rather than thrown away. Without this the fix would be
        indistinguishable from deleting the snapshot branch.

        The body here deliberately does NOT yield a model name, which is what
        separates the two questions the old check conflated: a 200 proves the
        session answered, and that is all ``_tail_ok`` is entitled to conclude.
        On the old code this case re-raised — a successful read whose payload
        parsed to nothing was treated as a dead session."""
        c = _conn(master_age_s=_STATIC_MASTER_TTL_S + 3600.0)
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_master_details={"unrecognised": "shape"},
        ))
        d = asyncio.run(c.get_vehicle_data(VIN))
        assert d is not None
        # and the cache was re-stamped by this poll
        assert c._master_cache[VIN][1] is not _CACHED

    def test_a_cold_connector_is_unchanged(self) -> None:
        """Regression: the state both existing tests exercise. No cache, no tail
        data, everything walled → re-raise, exactly as before."""
        c = _conn()
        c._get_json = AsyncMock(side_effect=_reads())
        with pytest.raises(AuthenticationError):
            asyncio.run(c.get_vehicle_data(VIN))


class TestARelationsWallNoLongerCostsTheOtherReads:
    """The reporter's actual symptom."""

    def test_a_relations_401_still_lets_the_charging_read_run(self) -> None:
        c = _conn(master_age_s=100.0)
        c.get_relations = AsyncMock(
            side_effect=AuthenticationError("relations -> HTTP 401"),
        )
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        seen: dict[str, bool] = {}

        def _map_charging(payload: Any, d: Any) -> Any:
            d.battery_soc = 61
            seen["charging"] = True
            return d

        with patch(f"{MOD}.map_charging_to_vehicle_data", _map_charging):
            d = asyncio.run(c.get_vehicle_data(VIN))

        # the read the veto used to cost him
        assert seen.get("charging") is True
        assert d.battery_soc == 61
        # and the wall is attributable rather than silent
        assert c.probe_outcomes.get("vwde_core_read:relations") == "401"

    def test_a_relations_401_with_everything_else_walled_still_re_raises(self) -> None:
        """The softening must not swallow a genuinely dead session: with no core
        read and no fresh tail read, the decision at the end still propagates."""
        c = _conn(master_age_s=100.0)
        c.get_relations = AsyncMock(
            side_effect=AuthenticationError("relations -> HTTP 401"),
        )
        c._get_json = AsyncMock(side_effect=_reads())
        with pytest.raises(AuthenticationError):
            asyncio.run(c.get_vehicle_data(VIN))

    def test_a_non_auth_relations_failure_is_unchanged(self) -> None:
        """Regression: the pre-existing fall-through for any other preflight
        error keeps its debug-only, non-recording behaviour."""
        c = _conn(master_age_s=100.0)
        c.get_relations = AsyncMock(side_effect=TypeError("shim signature"))
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert "vwde_core_read:relations" not in c.probe_outcomes


class TestTheWalledReadIsNotFiredAgainDownstream:
    """The softening has to stop the unwind, not move it ten lines later.

    The nickname/plate enrich falls back to the SINGULAR relation detail when
    the list did not carry this VIN. That fallback calls ``_ensure_backend``,
    whose only source for the platform is ``get_relations`` — the read that just
    walled. So after the softening it fired the identical request for a
    guaranteed second 401 and re-raised, unwinding past the cohort-gated
    position, SoH and measurements probes. The unwind is the thing the softening
    exists to stop, so skipping the fallback belongs to the same fix.

    Worth recording how this was missed: the first version of this file mocks
    ``get_relation_detail`` to return None in ``_conn``, and that is precisely
    the call that makes the second request. Mocking the path under test is how
    the two older regression tests stayed green over the cache hole this file
    opens with, and it happened here one function later.
    """

    def test_the_singular_fallback_is_skipped_after_an_auth_wall(self) -> None:
        c = _conn(master_age_s=100.0)
        c.get_relations = AsyncMock(
            side_effect=AuthenticationError("relations -> HTTP 401"),
        )
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert c.get_relation_detail.await_count == 0, (
            "the fallback reads the endpoint that just walled"
        )

    def test_the_reads_after_the_enrich_are_still_reached(self) -> None:
        """The point of not re-firing it: the rest of the block still runs. The
        position probe sits after the enrich and is the nearest observable one,
        so it stands in for everything downstream of it."""
        c = _conn(master_age_s=100.0)
        c._should_probe_position = MagicMock(return_value=True)
        c.get_relations = AsyncMock(
            side_effect=AuthenticationError("relations -> HTTP 401"),
        )
        side = _reads(vwde_charging={"data": {"batteryStatus": {}}})
        c._get_json = AsyncMock(side_effect=side)
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert "parkingposition" in side.seen, (
            "a read after the enrich block was never reached"
        )

    def test_the_fallback_still_fires_when_the_list_merely_omits_the_vin(self) -> None:
        """Counter-check: the guard must be about the WALL, not about the list
        being short. A successful relations read that does not mention this VIN
        is the case the fallback was written for."""
        c = _conn(master_age_s=100.0)
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert c.get_relation_detail.await_count == 1

    def test_a_non_auth_preflight_failure_still_uses_the_fallback(self) -> None:
        """Regression on the #1659 fall-through: only the auth wall sets the
        flag, so a shim-level failure keeps reaching the singular detail exactly
        as it has since then."""
        c = _conn(master_age_s=100.0)
        c.get_relations = AsyncMock(side_effect=TypeError("shim signature"))
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert c.get_relation_detail.await_count == 1


class TestTheOtherReadersStopReFiringTheWalledRead:
    """``_ensure_backend`` is called by five readers and can only ask one thing.

    Warning lights, parking position, battery health, range measurements and
    the capabilities read all call it, and its single source for the platform is
    ``get_relations`` (_website_authproxy.py:2011-2030). After a relations auth
    wall each of those re-asked and had the 401 raised back into it, so one wall
    cost reads sitting on realms that may well have answered. The marker is
    keyed by VIN and cleared at the top of every poll, so it describes this poll
    rather than latching.
    """

    def test_ensure_backend_does_not_re_ask_after_a_wall(self) -> None:
        c = _conn()
        c._relations_walled = {VIN}
        asyncio.run(c._ensure_backend(VIN))
        assert c.get_relations.await_count == 0

    def test_ensure_backend_still_asks_without_the_marker(self) -> None:
        """Counter-check on the guard: without it this test would pass for the
        wrong reason, and the one above would prove nothing."""
        c = _conn()
        c._relations_walled = set()
        asyncio.run(c._ensure_backend(VIN))
        assert c.get_relations.await_count == 1

    def test_a_cached_platform_still_short_circuits_first(self) -> None:
        """Regression: the pre-existing early return must still win, including
        the empty-string 'seen, no backend field' entry the parse writes."""
        c = _conn()
        c._vin_backend = {VIN: ""}
        c._relations_walled = set()
        asyncio.run(c._ensure_backend(VIN))
        assert c.get_relations.await_count == 0

    def test_one_wall_costs_exactly_one_relations_request_per_poll(self) -> None:
        """The whole poll, with the two enrich readers left REAL rather than
        mocked, because mocking them is what hid this in the first place."""
        c = _conn(master_age_s=100.0)
        del c.get_warning_lights
        del c.get_last_lock_action
        c.get_relations = AsyncMock(
            side_effect=AuthenticationError("relations -> HTTP 401"),
        )
        side = _reads(vwde_charging={"data": {"batteryStatus": {}}})
        c._get_json = AsyncMock(side_effect=side)
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert c.get_relations.await_count == 1, (
            f"the walled read was fired {c.get_relations.await_count} times"
        )

    def test_the_marker_does_not_latch_across_polls(self) -> None:
        """A sticky marker would keep suppressing the platform probe after the
        session recovered, which would silently pin an MBB car to the wrong gdc
        for as long as Home Assistant stayed up."""
        c = _conn(master_age_s=100.0)
        c.get_relations = AsyncMock(
            side_effect=AuthenticationError("relations -> HTTP 401"),
        )
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert VIN in c._relations_walled
        # next poll, session recovered
        c.get_relations = AsyncMock(return_value=MagicMock(vehicles=[]))
        c._get_json = AsyncMock(side_effect=_reads(
            vwde_charging={"data": {"batteryStatus": {}}},
        ))
        with patch(f"{MOD}.map_charging_to_vehicle_data", lambda _p, d: d):
            asyncio.run(c.get_vehicle_data(VIN))
        assert VIN not in c._relations_walled
