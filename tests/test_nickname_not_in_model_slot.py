# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The owner-set vehicle nickname must not occupy the ``model`` slot.

``model`` is a *published* field: it reaches the Vehicle Data Scout / Error
Reporter GitHub issue TITLES (``_reporter_pipeline`` builds a pre-filled issue
URL the user clicks), the HA device name and every ``entity_id`` for the car.
The CARIAD garage list's ``nickname`` / ``vehicleNick`` is owner-typed free
text, so it used to head the ``_pick_model`` candidate tuple and rode all the
way out to those titles as if it were a model designation.

The nickname is not dropped — it moves to ``d.vehicle_nickname``, which is
already a redacted diagnostics key with its own sensor.

Test VINs here are visibly invented (zeroed serials), and the nicknames are
invented strings, not anyone's data.
"""
from __future__ import annotations

import urllib.parse
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient
from custom_components.vag_connect.cariad.models import TokenSet

# Visibly invented: zeroed serial.
VIN = "WVWZZZ00000000001"
# Invented owner-typed nickname — the shape of value that must NOT be a model.
NICK = "Papas Dienstwagen"


def _client() -> VWEUClient:
    c = VWEUClient(MagicMock(), "u@example.invalid", "pw", "1234")
    c._tokens = TokenSet(
        access_token="t", refresh_token="r", id_token="",
        expires_at=0.0, strategy="device_grant",
    )
    c._eu_portal = None  # not a portal entry → take the BFF garage path
    c._garage_base = MagicMock(return_value="https://bff")  # type: ignore[method-assign]
    c._resolve_home_regions = AsyncMock()  # type: ignore[method-assign]

    async def _fetch_images():
        c._image_data = {}

    c.fetch_images = _fetch_images  # type: ignore[method-assign]
    return c


async def _garage(entry: dict) -> VWEUClient:
    """Run the REAL garage-list code (incl. _pick_model) over one entry."""
    c = _client()
    c._get = AsyncMock(return_value={"data": [entry]})
    await c.get_vehicles()
    return c


def _scout_title(model: str | None) -> str:
    """Build the public Scout issue title the way coordinator.py does."""
    from custom_components.vag_connect.cariad._reporter_pipeline import (
        ensure_unexpected_keys_issue,
    )
    from custom_components.vag_connect.cariad._unexpected_keys import (
        UnexpectedField,
    )

    findings = [
        UnexpectedField("a.b", '"x"', "vehicle-status", "2026-04-29T10:00:00")
    ]
    with patch(
        "custom_components.vag_connect.cariad._reporter_pipeline.ir.async_create_issue"
    ) as mock_create:
        ensure_unexpected_keys_issue(
            hass=object(),
            entry_id="entry1",
            findings=findings,
            brand="vw",
            model=model,
        )
        url = mock_create.call_args.kwargs["learn_more_url"]
        qs = urllib.parse.parse_qs(url.split("?", 1)[1])
        return qs["title"][0]


@pytest.mark.asyncio
async def test_nickname_does_not_reach_the_reporter_model():
    """A nickname in the garage list must not become the published model."""
    c = await _garage({"vin": VIN, "nickname": NICK, "model": "Golf"})

    # The cached model slot took the brand's model field, not the nickname.
    assert c._vehicle_metadata[VIN]["model"] == "Golf"

    d = c._parse_status(VIN, {}, parking={})
    assert d.model == "Golf"
    assert d.model != NICK

    # ...and the public issue title carries the model, never the nickname.
    title = _scout_title(d.model)
    assert title == "[Vehicle Data Scout] 1 new field(s) on vw Golf"
    assert NICK not in title


@pytest.mark.asyncio
async def test_vehicle_nick_alias_also_stays_out_of_the_model():
    """``vehicleNick`` was the second nickname candidate — same rule."""
    c = await _garage({"vin": VIN, "vehicleNick": NICK, "carModel": "ID.4"})

    assert c._vehicle_metadata[VIN]["model"] == "ID.4"
    d = c._parse_status(VIN, {}, parking={})
    assert d.model == "ID.4"
    assert NICK not in _scout_title(d.model)


@pytest.mark.asyncio
async def test_real_model_name_still_reaches_the_reporter_model():
    """Regression guard: moving the nickname must not break the good path.

    Invariant to the candidate-tuple change by design — it goes red only if the
    genuine model fields stop feeding the slot.
    """
    c = await _garage({"vin": VIN, "model": "ID.4"})

    assert c._vehicle_metadata[VIN]["model"] == "ID.4"
    d = c._parse_status(VIN, {}, parking={})
    assert d.model == "ID.4"
    assert _scout_title(d.model) == "[Vehicle Data Scout] 1 new field(s) on vw ID.4"


@pytest.mark.asyncio
async def test_nickname_still_reaches_its_own_field():
    """The owner does not lose the nickname — it lands on vehicle_nickname."""
    c = await _garage({"vin": VIN, "nickname": NICK, "model": "Golf"})

    assert c._vehicle_metadata[VIN]["nickname"] == NICK
    d = c._parse_status(VIN, {}, parking={})
    assert d.vehicle_nickname == NICK


@pytest.mark.asyncio
async def test_nickname_defaulted_to_the_vin_is_dropped_not_relocated():
    """CARIAD ships the raw VIN as the "nickname" on a never-renamed car.

    Moving the nickname into its own field must not relocate a full VIN into
    the nickname sensor, so the ==VIN guard travels with it.
    """
    c = await _garage({"vin": VIN, "nickname": VIN, "model": "Golf"})

    assert c._vehicle_metadata[VIN]["nickname"] is None
    d = c._parse_status(VIN, {}, parking={})
    assert d.vehicle_nickname is None
    assert d.model == "Golf"


@pytest.mark.asyncio
async def test_eu_data_act_portal_nickname_is_not_the_model():
    """The portal relation path wrote its nickname straight into ``model``.

    It bypassed ``_pick_model`` entirely, so it needed its own fix.
    """
    c = _client()
    portal = MagicMock()
    portal.login = AsyncMock()
    portal.list_vehicle_vins = AsyncMock(return_value=[VIN])
    portal.get_relation_nickname = AsyncMock(return_value=NICK)
    c._eu_portal = portal

    vins = await c.get_vehicles()

    assert vins == [VIN]
    meta = c._vehicle_metadata[VIN]
    assert meta["nickname"] == NICK
    assert meta["model"] is None          # nickname must not fill the model slot
    assert meta["model"] != NICK
