# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Two buttons that drew a success tick over a refusal.

@Dulcow pressed "Create EU Data Act data request", watched his Home Assistant
activity list, and saw the press logged and then nothing at all — no success,
no failure. He worked out only by opening the portal himself that no request
had been created. The kickoff had recorded the portal's status the whole time;
it is read back for diagnostics, and had no way to reach the person standing in
front of the button.

Its sibling had the same shape. ``async_request_historical_export`` returned
``False`` when the portal refused the kickoff and ``False`` again when the
session had expired, and the button awaited it and ignored the result. The
*import* half of that same feature was fixed for exactly this a few hours
earlier, after @placidcasual98 got a green tick over an expired export — two
halves of one feature disagreeing about whether a failure is worth mentioning.

Both raise now. The checks that matter most here are the negative ones: a
success must stay silent, and a refusal must not leave a half-finished state
behind.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.exceptions import ServiceValidationError

from custom_components.vag_connect.cariad.auth._data_act_scraper import (
    DataActSessionExpiredError,
)
from custom_components.vag_connect.cariad.models import TokenSet
from custom_components.vag_connect.coordinator import VagConnectCoordinator

_SCRAPER = "custom_components.vag_connect.cariad.auth._data_act_scraper.DataActScraper"
_SESS = "homeassistant.helpers.aiohttp_client.async_get_clientsession"
_VIN = "WVWZZZAUZFW000000"


# ── the "create data request" button ───────────────────────────────────────


def _kickoff_stub(error: str | None) -> Any:
    """A coordinator whose kickoff records *error* for the VIN, as the real one
    does from the portal's HTTP status."""
    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c._data_act_kickoff_error = {}
    c._last_runtime_kickoff = None
    c.refreshed = False

    async def _kickoff(*_a: Any, **_k: Any) -> None:
        if error is not None:
            c._data_act_kickoff_error[_VIN] = error

    async def _refresh() -> None:
        c.refreshed = True

    c._ensure_data_act_custom_request_kickoff = _kickoff
    c.async_request_refresh = _refresh
    return c


def test_a_refused_create_raises_with_the_portal_status() -> None:
    c = _kickoff_stub("HTTP 400")
    with pytest.raises(ServiceValidationError) as err:
        asyncio.run(c.async_create_data_act_request(_VIN))
    assert err.value.translation_key == "data_act_request_refused"
    assert (err.value.translation_placeholders or {}).get("reason") == "HTTP 400"


def test_a_successful_create_stays_quiet_and_refreshes() -> None:
    """The half that must not change: a button that cried wolf on success
    would be worse than one that said nothing."""
    c = _kickoff_stub(None)
    asyncio.run(c.async_create_data_act_request(_VIN))
    assert c.refreshed is True


def test_a_refused_create_does_not_claim_to_have_refreshed() -> None:
    c = _kickoff_stub("HTTP 500")
    with pytest.raises(ServiceValidationError):
        asyncio.run(c.async_create_data_act_request(_VIN))
    assert c.refreshed is False, "a refused request has nothing new to poll for"


def test_a_stale_status_from_an_earlier_press_is_not_re_reported() -> None:
    """Press once and get refused, fix it in the portal, press again: the
    second press must read ITS OWN verdict, not the one left behind."""
    c = _kickoff_stub(None)
    c._data_act_kickoff_error[_VIN] = "HTTP 400"  # left by an earlier press
    asyncio.run(c.async_create_data_act_request(_VIN))
    assert c.refreshed is True


def test_without_a_vin_the_old_signature_still_works() -> None:
    """The service path calls this with no VIN. It cannot report a per-car
    verdict, and must not break trying."""
    c = _kickoff_stub("HTTP 400")
    asyncio.run(c.async_create_data_act_request())
    assert c.refreshed is True


def test_the_button_passes_its_vin() -> None:
    """Without the VIN the coordinator cannot tell which car's verdict to read
    back, so the press ends quietly whatever the portal answered -- which is
    the defect, one layer up from where it is fixed."""
    from custom_components.vag_connect.button import VagDataActRequestButton

    btn = VagDataActRequestButton.__new__(VagDataActRequestButton)
    btn._vin = _VIN
    seen: list[Any] = []

    async def _create(vin: Any = None) -> None:
        seen.append(vin)

    btn.coordinator = MagicMock()
    btn.coordinator.async_create_data_act_request = _create
    asyncio.run(btn.async_press())
    assert seen == [_VIN]


# ── the "one-time historical export" button ────────────────────────────────


def _export_stub() -> Any:
    c = VagConnectCoordinator.__new__(VagConnectCoordinator)
    c.entry = MagicMock()
    c.entry.data = {"brand": "volkswagen"}
    c.entry.options = {}
    client = MagicMock()
    client._tokens = TokenSet(
        access_token="a", refresh_token="r", id_token="i",
        strategy="data_act_portal",
    )
    c._cariad_client = client
    c.hass = MagicMock()
    c.recorded = False
    c.repair_raised = 0

    def _record(_vin: str) -> None:
        c.recorded = True

    def _repair(*_a: Any) -> None:
        c.repair_raised += 1

    c._record_historical_pending = _record
    c._raise_data_act_session_expired_repair = _repair
    c.historical_export_state = lambda vin: "idle"
    return c


def _request(c: Any, *, ok: bool = True, side: Any = None) -> Any:
    scraper = MagicMock()
    scraper.kickoff_historical_export = AsyncMock(return_value=ok, side_effect=side)
    with patch(_SCRAPER, return_value=scraper), patch(_SESS, return_value=MagicMock()):
        return asyncio.run(c.async_request_historical_export(_VIN))


def test_a_refused_export_is_reported_by_the_button() -> None:
    """The raise lives on the BUTTON, not in the coordinator method.

    That method is also reached while Home Assistant tears the entry down --
    ``test_export_unload_returns_false_without_reauth_or_pending`` pins that an
    unload must return a quiet False -- and an unload cannot be told apart from
    a refusal there without the scraper reporting which it was. A button press
    always has someone in front of it, so that is where the False becomes
    visible. The full suite caught this: the first version raised in the
    coordinator and broke the unload contract in three parametrised cases.
    """
    from custom_components.vag_connect.button import VagHistoricalExportButton

    btn = VagHistoricalExportButton.__new__(VagHistoricalExportButton)
    btn._vin = _VIN
    btn.coordinator = MagicMock()
    btn.coordinator.async_request_historical_export = AsyncMock(return_value=False)
    with pytest.raises(ServiceValidationError) as err:
        asyncio.run(btn.async_press())
    assert err.value.translation_key == "historical_export_refused"


def test_an_accepted_export_leaves_the_button_quiet() -> None:
    from custom_components.vag_connect.button import VagHistoricalExportButton

    btn = VagHistoricalExportButton.__new__(VagHistoricalExportButton)
    btn._vin = _VIN
    btn.coordinator = MagicMock()
    btn.coordinator.async_request_historical_export = AsyncMock(return_value=True)
    asyncio.run(btn.async_press())  # must not raise


def test_a_refused_export_does_not_start_the_deadline_clock() -> None:
    """The state that must not be left behind. ``_record_historical_pending``
    starts a client-side 24-36 h timer; starting it for a request the portal
    never accepted would show a pending export that does not exist."""
    c = _export_stub()
    assert _request(c, ok=False) is False
    assert c.recorded is False


def test_the_coordinator_still_returns_a_quiet_false_for_an_unload() -> None:
    """The contract the button's raise depends on, asserted here too so the
    reason it lives there is visible from this file."""
    c = _export_stub()
    assert _request(c, ok=False) is False


def test_an_expired_session_raises_and_still_raises_the_card() -> None:
    """Both, not one or the other: the Repairs card is for later, the error is
    for the person who just pressed the button."""
    c = _export_stub()
    with pytest.raises(ServiceValidationError) as err:
        _request(c, side=DataActSessionExpiredError("401"))
    assert err.value.translation_key == "portal_session_expired"
    assert c.repair_raised == 1


def test_an_accepted_export_stays_quiet_and_starts_the_clock() -> None:
    c = _export_stub()
    assert _request(c, ok=True) is True
    assert c.recorded is True


# ── every message exists in every language ─────────────────────────────────


@pytest.mark.parametrize(
    "key",
    ["data_act_request_refused", "historical_export_refused",
     "portal_session_expired"],
)
def test_the_new_messages_are_translated_everywhere(key: str) -> None:
    """A key missing from one language silently falls back to English for
    those users and nothing reports it, so the check is mechanical."""
    import json
    import pathlib

    from custom_components.vag_connect import const

    root = pathlib.Path(const.__file__).parent
    files = [root / "strings.json", *sorted((root / "translations").glob("*.json"))]
    assert len(files) == 13, "the language set changed; check this test"
    for path in files:
        exc = json.loads(path.read_text(encoding="utf-8")).get("exceptions", {})
        assert key in exc, f"{key} missing from {path.name}"
        msg = exc[key].get("message", "")
        assert msg.strip(), f"{key} is empty in {path.name}"
        if key == "data_act_request_refused":
            assert "{reason}" in msg, f"{path.name} dropped the placeholder"
