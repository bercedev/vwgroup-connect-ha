# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1766 — read the MBB operationList from the EU-DP host when the legacy host
refuses it.

On an enrolled ``MBB_ODP`` car (Tiguan eHybrid MY2026, DE) the legacy host
``mal-1a.prd.ece`` answers ``401 gw.error.authentication`` while the same
``operationlist/v3`` on ``mal-3a.prd.eu.dp`` returns a full, parseable list.
Before recording the no-legacy verdict the client now asks the EU-DP host, and
accepts the answer only when it parses into a list with services. That list is
for reads only: commands stay hidden and are refused before any S-PIN leg.
"""
from __future__ import annotations

import asyncio
import logging
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient
from custom_components.vag_connect.cariad.exceptions import (
    APIError,
    VehicleCommandError,
)
from custom_components.vag_connect.coordinator import _mbb_command_capability

VIN = "WVWZZZAUZFW805377"
_LEGACY = "https://mal-1a.prd.ece.vwg-connect.com/api/rolesrights/operationlist/v3"
_EUDP = "https://mal-3a.prd.eu.dp.vwg-connect.com/api/rolesrights/operationlist/v3"
_AUTH_401 = APIError(
    401, _LEGACY,
    '{"error":{"errorCode":"gw.error.authentication","description":"Unauthorized"}}',
)
_ACL_403 = APIError(
    403, _LEGACY,
    '{"error":{"errorCode":"mbbc.rolesandrights.unauthorized"}}',
)
_EUDP_404 = APIError(404, _EUDP, "")


def _list(*services: dict) -> dict:
    return {"operationList": {"vin": VIN, "role": "PRIMARY_USER",
                              "status": "ENABLED", "serviceInfo": list(services)}}


_RCLIMA = {
    "serviceId": "rclima_v1",
    "serviceStatus": {"status": "Enabled"},
    "invocationUrl": {"content":
        "https://mal-3a.prd.eu.dp.vwg-connect.com/api/bs/climatisation/v1/vehicles/{vin}/"},
    "operation": [{"id": "P_START_CLIMA_NOSET", "permission": "granted"}],
}
_SPIN = {
    "serviceId": "rolesrights_securitypin_v1",
    "serviceStatus": {"status": "Enabled"},
    "invocationUrl": {"content":
        "https://mal-1a.prd.ece.vwg-connect.com/api/rolesrights/securitypin/v1/users/{userId}/"},
}
_GOOD = _list(_RCLIMA, _SPIN)


def _client(side_effect) -> VWEUClient:
    c = VWEUClient.__new__(VWEUClient)
    c._brand = types.SimpleNamespace(name="volkswagen")
    c._mbb_country_from_id_token = lambda: "DE"
    c._mbb_get = AsyncMock(side_effect=side_effect)
    c._refresh_tokens = AsyncMock(return_value=None)
    return c


def _urls(c: VWEUClient) -> list[str]:
    return [call.args[0] for call in c._mbb_get.await_args_list]


def test_legacy_200_is_unchanged() -> None:
    c = _client([_GOOD])
    ol = asyncio.run(c._get_mbb_operationlist(VIN))
    assert ol is not None
    assert len(_urls(c)) == 1 and "mal-1a" in _urls(c)[0]
    assert c.mbb_eudp_oplist_vins == set()
    assert c.mbb_no_legacy_vins == set()


def test_legacy_401_and_eudp_list_clears_the_verdict() -> None:
    c = _client([_AUTH_401, _GOOD])
    ol = asyncio.run(c._get_mbb_operationlist(VIN, for_command=True))
    assert ol is not None and "rclima_v1" in ol.services
    assert "mal-3a.prd.eu.dp" in _urls(c)[1]
    assert VIN not in c.mbb_no_legacy_vins
    assert VIN in c.mbb_eudp_oplist_vins
    # cached like a legacy list: the next poll does not touch the wire
    assert asyncio.run(c._get_mbb_operationlist(VIN)) is ol
    assert c._mbb_get.await_count == 2


def test_legacy_401_and_eudp_404_records_the_verdict_as_before() -> None:
    c = _client([_AUTH_401, _EUDP_404])
    assert asyncio.run(c._get_mbb_operationlist(VIN)) is None
    assert VIN in c.mbb_no_legacy_vins
    assert VIN not in c.mbb_eudp_oplist_vins


@pytest.mark.parametrize("body", [
    {"operationList": {"vin": VIN}},                       # envelope, no services
    _list(),                                               # empty serviceInfo
    {"error": {"errorCode": "gw.error.authentication"}},   # 200 with error body
    {},
    None,
])
def test_unusable_eudp_200_does_not_clear_the_verdict(body) -> None:
    c = _client([_AUTH_401, body])
    assert asyncio.run(c._get_mbb_operationlist(VIN)) is None
    assert VIN in c.mbb_no_legacy_vins
    assert VIN not in c.mbb_eudp_oplist_vins


def test_eudp_network_error_records_the_verdict() -> None:
    c = _client([_AUTH_401, TimeoutError()])
    assert asyncio.run(c._get_mbb_operationlist(VIN)) is None
    assert VIN in c.mbb_no_legacy_vins


def test_bare_acl_denial_does_not_ask_the_eudp_host() -> None:
    c = _client([_ACL_403])
    assert asyncio.run(c._get_mbb_operationlist(VIN)) is None
    assert c._mbb_get.await_count == 1


def test_legacy_recovery_drops_the_eudp_marker() -> None:
    c = _client([_AUTH_401, _GOOD, _GOOD])
    asyncio.run(c._get_mbb_operationlist(VIN))
    assert VIN in c.mbb_eudp_oplist_vins
    asyncio.run(c._get_mbb_operationlist(VIN, force_refresh=True))
    assert VIN not in c.mbb_eudp_oplist_vins


def test_host_per_service_is_logged(caplog) -> None:
    c = _client([_AUTH_401, _GOOD])
    with caplog.at_level(logging.DEBUG):
        asyncio.run(c._get_mbb_operationlist(VIN))
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "'rclima_v1': 'mal-3a.prd.eu.dp.vwg-connect.com'" in text
    assert "'rolesrights_securitypin_v1': 'mal-1a.prd.ece.vwg-connect.com'" in text
    assert VIN not in text


def test_commands_stay_hidden_for_an_eudp_list() -> None:
    c = _client([_AUTH_401, _GOOD])
    asyncio.run(c._get_mbb_operationlist(VIN))
    c._tokens = SimpleNamespace(strategy="mbb")
    coord = SimpleNamespace(_cariad_client=c)
    assert _mbb_command_capability(coord, VIN, "command_start_climate") is False
    # the same list from the legacy host would show the control
    c.mbb_eudp_oplist_vins.discard(VIN)
    assert _mbb_command_capability(coord, VIN, "command_start_climate") is True


def test_command_is_refused_before_any_spin_leg() -> None:
    c = _client([_AUTH_401, _GOOD])
    asyncio.run(c._get_mbb_operationlist(VIN))
    c._spin = "1234"
    with pytest.raises(VehicleCommandError, match="EU-DP"):
        asyncio.run(c._command_mbb_op(VIN, "climate_start"))
    assert c._mbb_get.await_count == 2  # only the two operationList reads
