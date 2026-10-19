# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The email-and-password reauth must discard the token it is replacing.

@ratclma reauthenticated a former device-login Audi entry with an email address
and a password, the sign-in succeeded, and no data followed. He then noticed
something in his diagnostics: ``dag_initial_tokens`` with ``strategy:
device_grant`` was still sitting there next to the new password, and asked
whether the calls that answered 400 had been made with it.

Half of that reading is right and the half that is right is not the half he
meant, which is why it is worth a test rather than a one-line change.

* The copy in ``entry.data`` is NOT what made the calls. The coordinator reads
  it only when the persistent token store is empty, as a one-time bootstrap for
  a freshly created entry — ``if persisted is None``. His entry had been
  running for months, so that branch is unreachable for him. What the leftover
  does is sit in the diagnostics looking exactly like a cause, which is how it
  misled the person who filed the report.
* The copy in the STORE is still live, and nothing in that reauth replaced it.
  So the reload loads an expired device-grant token, hands it to the client and
  spends a doomed refresh against a client the manufacturer retired, before
  falling through to a login with the new credentials. The storm guard counts
  that wasted refresh.

The rule was already written down one function away. ``async_step_reauth_qr``
says it in its own docstring: the coordinator prefers the store over
``dag_initial_tokens``, so a reauth MUST overwrite the store or it no-ops. The
QR branch overwrites both. The credential branch — the one added for exactly
these retired-grant entries — overwrote neither.

Scope matters as much as the fix. The one place that already discards a stale
device-grant token set is deliberately gated to Volkswagen, because
``device_grant`` is ALSO the normal durable strategy for Audi, Škoda, SEAT and
CUPRA device-login entries, and discarding theirs would throw away a
refreshable token on every restart (#118). So this cleanup is gated on the
entry having had NO password at all, which is the only shape that reaches this
branch: an ordinary device-login entry is routed to the code sign-in above, and
the durable MBB channels attach their tokens to entries that already have a
password. Both tests for that are below, and they are the ones that would
catch a well-meant widening.
"""
from __future__ import annotations

import inspect
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.vag_connect import config_flow as cf
from custom_components.vag_connect.const import (
    CONF_BRAND,
    CONF_PASSWORD,
    CONF_SPIN,
    CONF_USERNAME,
)

_DAG = {
    "access_token": "dead-access",
    "refresh_token": "dead-refresh",
    "id_token": "dead-id",
    "expires_at": 1.0,
    "strategy": "device_grant",
}


class _Entries:
    def __init__(self, entry: SimpleNamespace) -> None:
        self._entry = entry
        self.reloaded: list[str] = []

    def async_get_entry(self, entry_id: str) -> SimpleNamespace | None:
        return self._entry if entry_id == self._entry.entry_id else None

    def async_update_entry(self, entry: SimpleNamespace, *, data: dict) -> None:
        entry.data = data

    async def async_reload(self, entry_id: str) -> None:
        self.reloaded.append(entry_id)


def _flow(entry_data: dict[str, Any]) -> tuple[cf.VagConnectConfigFlow, SimpleNamespace, list[str]]:
    entry = SimpleNamespace(entry_id="e1", data=dict(entry_data))
    entries = _Entries(entry)
    flow = cf.VagConnectConfigFlow()
    flow.hass = SimpleNamespace(config_entries=entries)  # type: ignore[assignment]
    flow.context = {"entry_id": "e1"}  # type: ignore[assignment]
    cleared: list[str] = []

    async def _clear(entry_id: str) -> None:
        cleared.append(entry_id)

    flow._clear_token_store = _clear  # type: ignore[assignment]
    return flow, entry, cleared


@pytest.fixture(autouse=True)
def _stub_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    """The credential check itself is not under test; a real one would reach
    the network. Returning None is the non-Porsche answer."""
    async def _ok(hass, brand, username, password, *, country="us"):  # noqa: ANN001, ANN202
        return None

    monkeypatch.setattr(cf, "_validate_credentials", _ok)


@pytest.fixture(autouse=True)
def _dead_code_signin(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the routing input, so these tests state their own precondition
    instead of inheriting whichever brands are retired today."""
    import custom_components.vag_connect.cariad.auth._device_grant as dg

    monkeypatch.setattr(dg, "qr_signin_live", lambda brand, strategy: False)


class TestAPasswordlessEntryLosesItsDeadTokens:
    BASE = {
        CONF_BRAND: "audi",
        CONF_USERNAME: "11111111-2222-3333-4444-555555555555",
        CONF_SPIN: "",
        "dag_initial_tokens": _DAG,
        "mbb_client_id": "keep-me",
    }

    @pytest.mark.asyncio
    async def test_the_store_is_cleared(self):
        flow, _entry, cleared = _flow(self.BASE)
        await flow.async_step_reauth_confirm(
            {CONF_USERNAME: "me@example.invalid", CONF_PASSWORD: "pw"}
        )
        assert cleared == ["e1"], (
            "the expired device-grant token set survived the reauth, so the "
            "reload refreshes it before logging in with the new password"
        )

    @pytest.mark.asyncio
    async def test_the_leftover_in_entry_data_is_dropped(self):
        flow, entry, _cleared = _flow(self.BASE)
        await flow.async_step_reauth_confirm(
            {CONF_USERNAME: "me@example.invalid", CONF_PASSWORD: "pw"}
        )
        assert "dag_initial_tokens" not in entry.data

    @pytest.mark.asyncio
    async def test_the_new_credentials_are_stored(self):
        flow, entry, _cleared = _flow(self.BASE)
        await flow.async_step_reauth_confirm(
            {CONF_USERNAME: "me@example.invalid", CONF_PASSWORD: "pw"}
        )
        assert entry.data[CONF_USERNAME] == "me@example.invalid"
        assert entry.data[CONF_PASSWORD] == "pw"

    @pytest.mark.asyncio
    async def test_everything_else_on_the_entry_survives(self):
        """The reason this branch is the right place rather than Reconfigure:
        it merges. A rebuild drops whatever ``_build_entry_data`` does not own."""
        flow, entry, _cleared = _flow(self.BASE)
        await flow.async_step_reauth_confirm(
            {CONF_USERNAME: "me@example.invalid", CONF_PASSWORD: "pw"}
        )
        assert entry.data["mbb_client_id"] == "keep-me"
        assert entry.data[CONF_BRAND] == "audi"


class TestAnEntryWithAPasswordIsNotTouched:
    """#118 in a new place. ``device_grant`` is the normal durable strategy for
    Audi, Škoda, SEAT and CUPRA device-login entries, and the durable MBB
    channels attach their tokens to entries that already HAVE a password. A
    cleanup that keyed on "has a stored token set" instead of "had no password"
    would throw those away on every reauth."""

    WITH_PW = {
        CONF_BRAND: "audi",
        CONF_USERNAME: "me@example.invalid",
        CONF_PASSWORD: "old",
        CONF_SPIN: "",
        "dag_initial_tokens": {**_DAG, "strategy": "mbb"},
        "mbb_client_id": "keep-me",
    }

    @pytest.mark.asyncio
    async def test_the_store_is_left_alone(self):
        flow, _entry, cleared = _flow(self.WITH_PW)
        await flow.async_step_reauth_confirm({CONF_PASSWORD: "new"})
        assert cleared == [], "a working durable channel's tokens were discarded"

    @pytest.mark.asyncio
    async def test_its_tokens_stay_in_entry_data(self):
        flow, entry, _cleared = _flow(self.WITH_PW)
        await flow.async_step_reauth_confirm({CONF_PASSWORD: "new"})
        assert entry.data["dag_initial_tokens"]["strategy"] == "mbb"
        assert entry.data[CONF_PASSWORD] == "new"


class TestTheCleanupIsGatedAndNotUnconditional:
    """Structural, and deliberately so: the two behavioural classes above
    cannot see a cleanup that was moved outside the gate but happens to be
    unreachable for both fixtures."""

    def _src(self) -> str:
        return inspect.getsource(cf.VagConnectConfigFlow.async_step_reauth_confirm)

    def test_the_cleanup_sits_behind_the_no_password_gate(self):
        src = self._src()
        assert "_needs_username" in src
        assert src.index("_needs_username:") < src.index("_clear_token_store")

    def test_the_helper_clears_rather_than_saves(self):
        src = inspect.getsource(cf.VagConnectConfigFlow._clear_token_store)
        assert ".clear()" in src
        assert ".save(" not in src

    def test_the_qr_branch_still_overwrites_instead_of_clearing(self):
        """The two branches want opposite things: the code sign-in has fresh
        tokens to install, this one has none to install. Clearing there would
        discard the sign-in that just succeeded.

        Asserted on ``_finish_reauth_qr``, which is where that branch actually
        writes — ``async_step_reauth_qr`` only hands off to the pending-login
        step, so the first version of this test looked for the write in the
        wrong method and failed for a reason that had nothing to do with the
        behaviour it claims to pin.
        """
        src = inspect.getsource(cf.VagConnectConfigFlow._finish_reauth_qr)
        assert ".save(fresh)" in src
        assert "_clear_token_store" not in src
