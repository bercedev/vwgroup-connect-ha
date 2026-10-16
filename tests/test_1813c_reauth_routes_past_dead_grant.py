# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Reauth must not route a dead-grant entry to the sign-in code.

This supersedes the approach in the first half of this branch, and the reason is
worth writing down because it changes where the fix belongs.

That version left the reauth routing alone and pointed the repair notice at
Reconfigure instead. An adversarial review of the branch found two things wrong
with that:

1. Home Assistant starts the reauth flow ITSELF. ``ConfigEntryAuthFailed``
   reaches ``async_start_reauth_if_available`` in HA core, and because this
   handler defines ``async_step_reauth`` a flow is initiated on every setup
   attempt. So the user does not have to click anything to be put back into the
   dead end: the reauth card appears unprompted, next to our repair notice, and
   it is the more prominent of the two. Routing the repair elsewhere does not
   help while the routing itself is wrong.

2. Reconfigure is the worse destination anyway. A passwordless entry's unique id
   is ``f"{brand}_{dag_user_id}"`` — the account UUID — so a reconfigure that
   takes an email address always computes a different unique id, which makes
   ``account_changed`` True, which selects ``merged = base``: a clean rebuild.
   That drops everything ``_build_entry_data`` does not own, including the MBB
   command channel, the supplementary portal credentials and the test-cohort
   flag. It can also abort ``already_configured`` for anyone who followed the
   older banner's advice and already has an email entry for the same account.
   Meanwhile the copy promised the entry "keeps its history, its settings and its
   entity names". History and names survive; settings do not.

The reauth path does not have that problem. It updates with
``data={**reauth_entry.data, ...}`` — a real merge — and leaves the unique id
alone, so nothing is dropped and nothing can collide. It is the right
destination, and it is the one HA opens by itself.

So the fix moves to the routing gate: consult ``qr_signin_live`` before sending a
passwordless entry to the code sign-in, and where that sign-in is gone, show the
credential form instead. One extra field is needed there, because the stored
username on these entries is the account UUID rather than an email address, so
asking only for a password cannot work.
"""
from __future__ import annotations

import inspect

import pytest

from custom_components.vag_connect import config_flow as cf
from custom_components.vag_connect.cariad.auth._device_grant import qr_signin_live


class TestTheRoutingGateConsultsTheClassifier:
    """The gate currently sends every passwordless entry to the QR step."""

    def _src(self) -> str:
        return inspect.getsource(cf.VagConnectConfigFlow.async_step_reauth_confirm)

    def test_the_gate_calls_qr_signin_live(self):
        assert "qr_signin_live" in self._src()

    def test_the_gate_reads_the_stored_strategy(self):
        """Brand alone cannot decide it: the same brand is live on one strategy
        and dead on the other."""
        assert "dag_initial_tokens" in self._src()

    def test_a_dead_grant_does_not_reach_the_qr_step(self):
        """Structural: the QR hand-off must sit behind the classifier, not before
        it. Asserted on ordering because the two appear in the same function."""
        src = self._src()
        assert src.index("qr_signin_live") < src.index("async_step_reauth_qr")


class TestTheFormAsksForWhatItNeeds:
    """A form that asks only for a password cannot fix an entry whose stored
    username is an account UUID."""

    def _src(self) -> str:
        return inspect.getsource(cf.VagConnectConfigFlow.async_step_reauth_confirm)

    def test_the_username_can_be_supplied(self):
        assert "CONF_USERNAME" in self._src()

    def test_the_update_writes_the_supplied_username(self):
        """Otherwise the entry keeps the UUID and the next login fails the same
        way, which would make the whole exercise pointless."""
        src = self._src()
        update = src[src.index("async_update_entry"):]
        assert "CONF_USERNAME" in update[:600]

    def test_the_update_still_merges_rather_than_rebuilds(self):
        """The property that makes reauth the right destination. If this ever
        becomes a plain dict the entry silently loses its other channels."""
        src = self._src()
        update = src[src.index("async_update_entry"):]
        assert "**reauth_entry.data" in update[:400]


class TestTheClassifierStillSaysWhatItSaid:
    """Guards against someone 'simplifying' the classifier while moving the gate."""

    def test_the_dead_pair_is_still_dead(self):
        assert qr_signin_live("audi", "device_grant") is False

    @pytest.mark.parametrize(("brand", "strategy"), [
        ("seat", "device_grant"),
        ("cupra", "device_grant"),
        ("audi_na", "device_grant"),
        ("volkswagen", "mbb"),
    ])
    def test_the_live_pairs_are_still_live(self, brand, strategy):
        assert qr_signin_live(brand, strategy) is True


class TestTheRepairPointsAtReauthNow:
    """Both reasons end up at the same flow, because reauth is where the fix is."""

    def test_the_retired_reason_opens_reauth_not_reconfigure(self):
        from custom_components.vag_connect.repairs import _FIXABLE_REASONS
        assert _FIXABLE_REASONS["device_login_grant_retired"] == "reauth"

    def test_the_live_reason_opens_reauth(self):
        from custom_components.vag_connect.repairs import _FIXABLE_REASONS
        assert _FIXABLE_REASONS["device_login_expired"] == "reauth"


class TestTheCopyNoLongerPromisesSettings:
    """The review found this promise false for the reconfigure route. On the
    reauth route it is true again — but the word stays out, because what the user
    needs to know is what to type, not what survives."""

    def test_the_retired_notice_does_not_send_anyone_to_reconfigure(self):
        import json
        from pathlib import Path
        root = Path(cf.__file__).parent
        s = json.loads((root / "strings.json").read_text(encoding="utf-8"))
        desc = s["issues"]["device_login_grant_retired"]["description"].lower()
        assert "reconfigure" not in desc

    def test_the_retired_notice_asks_for_email_and_password(self):
        import json
        from pathlib import Path
        root = Path(cf.__file__).parent
        s = json.loads((root / "strings.json").read_text(encoding="utf-8"))
        desc = s["issues"]["device_login_grant_retired"]["description"].lower()
        assert "email" in desc
        assert "password" in desc
