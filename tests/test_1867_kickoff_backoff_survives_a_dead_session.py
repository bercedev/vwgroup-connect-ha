# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failed attempt that is not recorded is an attempt that repeats forever.

@alagerqv read the kickoff loop and found what his own entry was doing: his
``data_act_kickoff_ts`` sat frozen at ``2026-10-10T06:53:06Z``, long past the
24 h re-verify window, so every setup re-POSTed and re-raised the
"portal session expired" card.

The cause is one word. The loop stamps the attempt and says why:

    # No fresh cached request — kick one off. Stamp the attempt FIRST
    # (and mark changed) so the backoff holds even if this POST fails,
    # persisted below — an unpersisted stamp wouldn't survive a restart.
    new_ts[vin] = datetime.now(tz=timezone.utc).isoformat()

and the session-expired handler then did ``return``, which leaves the method
before the ``if changed:`` block that is the only thing writing ``new_ts``. The
comment promised a persist that this path jumped over.

``break`` instead of ``return`` fixes the POST-side 401. It does **not** fix the
other one: a 401 from the probe (``get_active_custom_request_identifier``)
raises *before* the stamp exists, and a refused probe is a failed attempt
exactly as much as a refused POST. So the handler stamps the VIN itself if
nothing has yet.

Three raise sites also logged nothing at all, which is why his card appeared
with no line next to it saying where it came from — including the one branch
whose immediate sibling four lines below *does* log its status.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vag_connect.cariad.auth._data_act_scraper import (
    DataActSessionExpiredError,
)
from custom_components.vag_connect.cariad.models import TokenSet
from custom_components.vag_connect.coordinator import VagConnectCoordinator

_SCRAPER = "custom_components.vag_connect.cariad.auth._data_act_scraper.DataActScraper"
_SESS = "homeassistant.helpers.aiohttp_client.async_get_clientsession"
_VIN = "WVWZZZAUZFW000000"
_VIN2 = "WVWZZZAUZFW000001"
_COORD_LOG = "custom_components.vag_connect.coordinator"


def _stub(identifiers: dict, kickoff_ts: dict, vins: list[str] | None = None) -> Any:
    stub = type("S", (), {})()
    stub._active_vins = lambda v: v
    stub._data_act_kickoff_error = {}
    stub.entry = MagicMock()
    stub.entry.options = {
        "eu_data_act_auto_kickoff": True,
        "data_act_identifiers": dict(identifiers),
        "data_act_kickoff_ts": dict(kickoff_ts),
    }
    stub.entry.data = {"brand": "volkswagen", "eu_data_act_auto_kickoff": True}
    client = MagicMock()
    client._tokens = TokenSet(
        access_token="a", refresh_token="r", id_token="i",
        strategy="data_act_portal",
    )
    stub._cariad_client = client
    stub.vehicles = {v: {} for v in (vins or [_VIN])}
    stub.hass = MagicMock()
    stub.raised = 0

    def _raise_repair(self: Any = None) -> None:
        stub.raised += 1

    stub._raise_data_act_session_expired_repair = _raise_repair
    return stub


def _run(stub: Any, *, active_side: Any = None, kickoff_side: Any = None) -> Any:
    scraper = MagicMock()
    scraper.get_active_custom_request_identifier = AsyncMock(
        return_value=None if active_side is None else None,
        side_effect=active_side,
    )
    scraper.kickoff_custom_data_request = AsyncMock(
        return_value="NEWID", side_effect=kickoff_side,
    )
    with patch(_SCRAPER, return_value=scraper), patch(_SESS, return_value=MagicMock()):
        asyncio.run(
            VagConnectCoordinator._ensure_data_act_custom_request_kickoff(stub)
        )
    return scraper


def _written_options(stub: Any) -> dict:
    call = stub.hass.config_entries.async_update_entry.call_args
    assert call is not None, "nothing was persisted at all"
    return call.kwargs["options"]


# ── the POST-side 401: the stamp exists and must survive ───────────────────


def test_a_401_from_the_post_still_persists_the_attempt() -> None:
    stub = _stub({}, {})
    _run(stub, kickoff_side=DataActSessionExpiredError("401"))
    opts = _written_options(stub)
    assert _VIN in opts["data_act_kickoff_ts"], (
        "the stamp written before the POST was thrown away by the early return"
    )
    assert stub.raised == 1, "the repair must still be raised"


# ── the probe-side 401: no stamp exists yet, so the handler makes one ──────


def test_a_401_from_the_probe_records_the_attempt_too() -> None:
    """The half a bare `break` does not reach: the probe raises before the
    stamp line, so without the handler stamping, this path keeps re-firing."""
    stub = _stub({}, {})
    scraper = _run(stub, active_side=DataActSessionExpiredError("401"))
    scraper.kickoff_custom_data_request.assert_not_awaited()
    opts = _written_options(stub)
    assert _VIN in opts["data_act_kickoff_ts"]
    assert stub.raised == 1


def test_the_stamp_is_recent_enough_to_start_the_backoff() -> None:
    """A stamp that is not roughly now would not hold the window off."""
    stub = _stub({}, {})
    _run(stub, active_side=DataActSessionExpiredError("401"))
    stamped = datetime.fromisoformat(
        _written_options(stub)["data_act_kickoff_ts"][_VIN]
    )
    assert datetime.now(tz=timezone.utc) - stamped < timedelta(minutes=1)


def test_an_existing_stamp_is_not_overwritten() -> None:
    """The POST site stamps first; the handler must not stomp on it."""
    stub = _stub({}, {})
    _run(stub, kickoff_side=DataActSessionExpiredError("401"))
    first = _written_options(stub)["data_act_kickoff_ts"][_VIN]
    assert first  # the POST-site stamp, not a second one from the handler


# ── the loop still stops, and earlier work is not lost ────────────────────


def test_the_session_being_dead_stops_the_loop() -> None:
    """A dead session is dead for every VIN. The second car must not be
    probed — that was the point of the original `return` and it still holds."""
    stub = _stub({}, {}, vins=[_VIN, _VIN2])
    scraper = _run(stub, active_side=DataActSessionExpiredError("401"))
    assert scraper.get_active_custom_request_identifier.await_count == 1
    assert stub.raised == 1, "and the card is raised once, not once per VIN"


def test_an_identifier_adopted_before_the_401_is_still_persisted() -> None:
    """The first car adopts a live request, the second car's probe 401s. The
    adoption must survive — with `return` it was discarded along with the
    stamp, so a successful adoption could be lost to an unrelated 401."""
    stub = _stub({}, {}, vins=[_VIN, _VIN2])
    _run(stub, active_side=["LIVEID", DataActSessionExpiredError("401")])
    opts = _written_options(stub)
    assert opts["data_act_identifiers"][_VIN] == "LIVEID"
    assert _VIN2 in opts["data_act_kickoff_ts"]


# ── the card no longer appears without a cause line ───────────────────────


def test_raising_the_card_says_so_in_the_log(caplog) -> None:
    with caplog.at_level(logging.WARNING, logger=_COORD_LOG):
        VagConnectCoordinator._raise_data_act_session_expired_repair(
            _real_stub_for_repair()
        )
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING]
    assert any("401" in m for m in msgs), msgs
    assert any("portal" in m.lower() for m in msgs), msgs


def _real_stub_for_repair() -> Any:
    s = type("S", (), {})()
    s.hass = MagicMock()
    s.entry = MagicMock()
    s.entry.entry_id = "entry1"
    s.entry.data = {"brand": "volkswagen"}
    return s


@pytest.mark.parametrize("site", ["metadata/partial", "requests/partial"])
def test_both_scraper_401_sites_log_before_raising(site: str) -> None:
    """Checked against the source rather than by driving aiohttp: the point is
    that neither branch is silent, and the sibling non-200 branch four lines
    below one of them has always logged."""
    import inspect

    from custom_components.vag_connect.cariad.auth import _data_act_scraper

    src = inspect.getsource(_data_act_scraper)
    marker = f'"{site} HTTP 401 for VIN %s'
    assert marker in src, f"{site} raises its 401 with no log line"
