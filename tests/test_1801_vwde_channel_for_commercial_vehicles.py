# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A restriction with no evidence behind it is not a restriction, it is a typo.

@benbrummer (#1801) could not add the read-only volkswagen.de channel to a T6.1
set up as Volkswagen Commercial Vehicles. I went looking for the reason,
expecting to find one. The whole thing was a single equality check at the top of
the sign-in step:

    if self._config_entry.data.get(CONF_BRAND) != "volkswagen":
        return self.async_abort(reason="not_volkswagen")

Nothing downstream of it is brand-specific. The connector is constructed with
``brand="volkswagen"`` either way, because that names *volkswagen.de*, not our
brand enum. The two brands are already one thing where it matters most:
Commercial Vehicles uses the same We Connect app, and its EU Data Act
configuration is the Volkswagen one with a different state name (#1316). And his
own report says the same from outside — identical entity count, identical
values, whether his van is set up as Volkswagen EU or as Commercial Vehicles.

What is still **not** established is whether volkswagen.de serves a Commercial
VIN once the sign-in is allowed to start. That is a question about VW's site,
and the gate was the thing stopping anyone from asking it. The channel is
read-only, so being wrong costs a failed sign-in the owner sees immediately.
If Commercial VINs turn out not to be served, the gate comes back **with a
reason attached**.

The tests below are therefore as much about what did NOT change: every other
brand is still refused, the abort string is untouched so its twelve
translations stay accurate, and — the one that makes this honest rather than
hopeful — the commercial client really does inherit the arming method, so a
widened gate cannot hand someone a channel that silently never arms.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock


def _flow(brand: str) -> Any:
    from custom_components.vag_connect.config_flow import VagConnectOptionsFlow

    entry = MagicMock()
    entry.data = {"brand": brand}
    entry.options = {}
    flow = VagConnectOptionsFlow(entry)
    flow.hass = MagicMock()
    return flow


def _step(brand: str) -> dict[str, Any]:
    """Run the vw.de step with no input: it either aborts or shows its form."""
    flow = _flow(brand)
    return asyncio.new_event_loop().run_until_complete(
        flow.async_step_add_vwde()
    )


# ── who may reach the sign-in ──────────────────────────────────────────────


def test_passenger_volkswagen_still_reaches_the_login() -> None:
    res = _step("volkswagen")
    assert res.get("type") != "abort"
    assert res.get("step_id") == "add_vwde"


def test_commercial_vehicles_now_reaches_the_login() -> None:
    """#1801 itself."""
    res = _step("volkswagen_commercial")
    assert res.get("type") != "abort", "still refused for Commercial Vehicles"
    assert res.get("step_id") == "add_vwde"


def test_every_other_brand_is_still_refused() -> None:
    """The widening is two brands, not an opening. A Škoda or a Porsche has no
    business on volkswagen.de and would meet a sign-in that cannot know it."""
    for brand in ("audi", "skoda", "seat", "cupra", "porsche", "bentley",
                  "volkswagen_na", "audi_na", "lamborghini", ""):
        res = _step(brand)
        assert res.get("type") == "abort", f"{brand} was let through"
        assert res.get("reason") == "not_volkswagen", brand


def test_a_missing_brand_is_refused_rather_than_assumed() -> None:
    """An entry with no brand at all must not fall through to the sign-in."""
    from custom_components.vag_connect.config_flow import VagConnectOptionsFlow

    entry = MagicMock()
    entry.data = {}
    entry.options = {}
    flow = VagConnectOptionsFlow(entry)
    flow.hass = MagicMock()
    res = asyncio.new_event_loop().run_until_complete(flow.async_step_add_vwde())
    assert res.get("type") == "abort"
    assert res.get("reason") == "not_volkswagen"


# ── what did not change ────────────────────────────────────────────────────


def test_the_allowed_set_is_exactly_the_two_volkswagen_brands() -> None:
    """Pinned deliberately. The next person to widen this has to do it on
    purpose, and with something measured rather than the hope that a brand
    might work."""
    from custom_components.vag_connect.config_flow import _VWDE_BRANDS

    assert _VWDE_BRANDS == frozenset({"volkswagen", "volkswagen_commercial"})


def test_the_abort_string_still_says_volkswagen_in_every_language() -> None:
    """The reason key is unchanged, so no translation has to move: both members
    of the set ARE Volkswagen, and "only available for Volkswagen" stays true.
    A new key would have silently fallen back to English in twelve files."""
    import json
    import pathlib

    from custom_components.vag_connect import config_flow

    root = pathlib.Path(config_flow.__file__).parent

    def _find(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == key:
                    return v
                found = _find(v, key)
                if found is not None:
                    return found
        return None

    files = sorted((root / "translations").glob("*.json"))
    assert len(files) == 12, "the language set changed; check this test"
    for path in [root / "strings.json", *files]:
        text = _find(json.loads(path.read_text(encoding="utf-8")),
                     "not_volkswagen")
        assert isinstance(text, str) and text.strip(), path.name
        assert "olkswagen" in text, path.name


# ── the check that makes the widening honest ───────────────────────────────


def test_the_commercial_client_can_actually_arm_the_channel() -> None:
    """The gate is only half the path. If the commercial client did not inherit
    the arming method, widening it would let someone add a channel that is
    stored, shown as configured, and never armed — a worse outcome than the
    refusal it replaces, because nothing would say so.
    """
    from custom_components.vag_connect.cariad.api.commercial import (
        VWCommercialClient,
    )
    from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient

    assert issubclass(VWCommercialClient, VWEUClient)
    assert callable(getattr(VWCommercialClient, "arm_supplementary_authproxy",
                            None))
    # Same implementation, not a stub that happens to share the name.
    assert (VWCommercialClient.arm_supplementary_authproxy
            is VWEUClient.arm_supplementary_authproxy)


def test_the_login_identifies_itself_to_the_site_as_volkswagen() -> None:
    """``brand="volkswagen"`` in the connector names *volkswagen.de*, not the
    entry's brand. If that ever started following the entry, a Commercial entry
    would hand the site a brand it does not know — which is exactly the kind of
    breakage a widened gate could cause and nobody would look for.
    """
    import inspect

    from custom_components.vag_connect import _vwde_reauth

    src = inspect.getsource(_vwde_reauth.VwDeReauthMixin._ovw_begin_login)
    assert 'brand="volkswagen"' in src
    assert "CONF_BRAND" not in src
