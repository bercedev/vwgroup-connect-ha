# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1813 follow-up — the expired-sign-in notice sends an Audi into a dead end.

#1813 gave a passwordless entry its own error instead of the credential
catch-all, and that part was right. What it got wrong is the sentence it then
shows, which says to choose Reauthenticate and scan the sign-in code again.

@ratclma did exactly that and reported back: the reauth went straight to the
``device_grant_retired`` banner, the same wall he had already hit six times. He
was right and the advice was wrong, so this is the correction.

Only five ``(brand, strategy)`` pairs can reach the notice at all. The raise gate
(``cariad/api/base.py``) needs ``not self._password`` and a strategy of
``device_grant``, ``device_grant_portal`` or ``mbb``, and only two config-flow
sites ever write ``dag_initial_tokens``: the device-grant one, whose brand is
constrained to ``DAG_ENABLED_BRANDS``, and the MBB one, whose brand is pinned to
Volkswagen. Of those five, exactly one is a dead end:

- ``audi`` + ``device_grant`` — DEAD. ``async_step_reauth_qr`` sets
  ``_dag_mbb = (strategy == "mbb")``, so an Audi takes the non-MBB branch and
  the retired app client, ``/device_authorization`` answers 403, and the flow
  drops back to the brand picker showing ``device_grant_retired``. Both users on
  the issue are this pair.
- ``seat`` / ``cupra`` / ``audi_na`` + ``device_grant`` — live clients.
- ``volkswagen`` + ``mbb`` — the durable e-Remote client, which also has an
  ``unauthorized_client`` failover to a backup client.

``audi`` + ``mbb`` looks like a way out and is not: Audi IS in
``MBB_DAG_BRANDS``, but both Audi MBB paths write their tokens onto an entry
that already has a password, so the gate's ``not self._password`` can never
fire for them. There is no in-place QR route for this user, which is why the
copy has to point somewhere else entirely.

Where it points: Reconfigure takes email and password and merges them onto the
existing entry (``merged = {**entry.data, **base}``), so the entry and its
history survive. That is also what the ``device_grant_retired`` banner itself
tells Audi users to do, so the two now agree.

Two further defects in the same notice, both found while grounding the above:

- The title renders "Sign in again to restore **VW Group**" and never names the
  brand, because the setup-path call omits ``brand=`` while the coordinator-path
  call passes it.
- There is no Repair button at all: the reason is absent from
  ``_FIXABLE_REASONS``, so a notice whose text says "choose Reauthenticate"
  offers nothing to click. Both reasons get a button here.

Where that button leads changed after this file was written, and the reasoning is
in ``test_1813c_reauth_routes_past_dead_grant.py``. In short: Home Assistant opens
the reauth flow itself on a ``ConfigEntryAuthFailed``, so pointing the repair
somewhere else left the dead end reachable without the user clicking anything;
and reconfigure, the first destination chosen here, does a clean rebuild of
``entry.data`` on exactly these entries. So both reasons open reauth now, and the
routing gate inside it tells them apart.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "vag_connect"
_STRINGS = json.loads((_ROOT / "strings.json").read_text(encoding="utf-8"))
_TRANSLATIONS = sorted((_ROOT / "translations").glob("*.json"))

_LIVE = "device_login_expired"
_DEAD = "device_login_grant_retired"


class TestTheClassifier:
    """One source of truth for 'can this entry still scan a code?'."""

    def test_audi_device_grant_is_the_dead_pair(self):
        from custom_components.vag_connect.cariad.auth._device_grant import (
            qr_signin_live,
        )
        assert qr_signin_live("audi", "device_grant") is False

    @pytest.mark.parametrize("brand", ["seat", "cupra", "audi_na"])
    def test_the_other_device_grant_brands_stay_live(self, brand):
        from custom_components.vag_connect.cariad.auth._device_grant import (
            qr_signin_live,
        )
        assert qr_signin_live(brand, "device_grant") is True

    def test_volkswagen_mbb_is_live(self):
        from custom_components.vag_connect.cariad.auth._device_grant import (
            qr_signin_live,
        )
        assert qr_signin_live("volkswagen", "mbb") is True

    def test_audi_mbb_is_live_even_though_unreachable(self):
        """Audi IS in MBB_DAG_BRANDS. The classifier answers about the CLIENT,
        not about whether a config flow can produce that pair — keeping those
        two questions apart is what stops the next reader 'fixing' this into a
        brand blocklist."""
        from custom_components.vag_connect.cariad.auth._device_grant import (
            qr_signin_live,
        )
        assert qr_signin_live("audi", "mbb") is True

    def test_the_retired_set_is_named_not_inlined(self):
        """A bare 'brand == audi' in the classifier would be invisible to the
        next person who checks which brands are dead."""
        from custom_components.vag_connect.cariad.auth import _device_grant
        assert "audi" in _device_grant.DAG_RETIRED_BRANDS

    def test_retired_brands_are_a_subset_of_enabled_ones(self):
        """A brand can only be retired FROM the picker if it is still in it —
        DAG_ENABLED_BRANDS deliberately keeps audi so the banner can explain."""
        from custom_components.vag_connect.cariad.auth import _device_grant
        assert _device_grant.DAG_RETIRED_BRANDS <= _device_grant.DAG_ENABLED_BRANDS

    def test_an_unknown_strategy_is_not_claimed_live(self):
        from custom_components.vag_connect.cariad.auth._device_grant import (
            qr_signin_live,
        )
        assert qr_signin_live("volkswagen", "website_authproxy") is False


class TestTheReasonIsActuallyChosen:
    """The classifier has to be WIRED, not merely present.

    This is the test shape #584 needed and did not have: there, a refusal
    classifier was unit-tested and passed while the code path that was failing
    never called it. So these assert the call site, deliberately by source
    inspection rather than by a second helper function — the decision is one
    expression in ``async_setup_entry`` and does not earn a wrapper of its own.
    """

    def _setup_src(self) -> str:
        import inspect

        from custom_components import vag_connect
        return inspect.getsource(vag_connect.async_setup_entry)

    def test_the_setup_path_calls_the_classifier(self):
        assert "qr_signin_live" in self._setup_src()

    def test_the_setup_path_can_produce_the_retired_reason(self):
        assert _DEAD in self._setup_src()

    def test_the_decision_reads_the_stored_strategy(self):
        """Brand alone is not enough: the same brand is live on one strategy
        and dead on the other, which is the whole trap in this issue."""
        assert "dag_initial_tokens" in self._setup_src()

    def test_the_decision_reads_the_brand(self):
        assert "CONF_BRAND" in self._setup_src()


class TestTheRepairWiring:
    """Both reasons must be wired everywhere the original one is."""

    @pytest.mark.parametrize("reason", [_LIVE, _DEAD])
    def test_both_are_hard_setup_errors(self, reason):
        from custom_components.vag_connect import _HARD_AUTH_SETUP_ERRORS
        assert reason in _HARD_AUTH_SETUP_ERRORS

    @pytest.mark.parametrize("reason", [_LIVE, _DEAD])
    def test_both_have_setup_copy(self, reason):
        from custom_components.vag_connect import _SETUP_ERRORS
        assert reason in _SETUP_ERRORS

    @pytest.mark.parametrize("reason", [_LIVE, _DEAD])
    def test_both_are_cleared_after_a_successful_login(self, reason):
        """The issue id is f'{entry_id}_{reason}', so a reason missing from the
        clear list leaves its repair standing after the login that fixed it."""
        import inspect

        from custom_components.vag_connect import repairs
        src = inspect.getsource(repairs.clear_auth_issues)
        assert f'"{reason}"' in src

    def test_the_dead_reason_also_opens_reauth(self):
        """Superseded destination. This pointed at reconfigure until a review of
        the branch showed reconfigure does a clean rebuild of entry.data on
        exactly these entries, and that HA opens reauth by itself anyway. The
        routing inside reauth now decides, so both reasons land there and the
        branch's own test_1813c pins the gate that tells them apart."""
        from custom_components.vag_connect.repairs import _FIXABLE_REASONS
        assert _FIXABLE_REASONS[_DEAD] == "reauth"

    def test_the_live_reason_opens_reauth(self):
        from custom_components.vag_connect.repairs import _FIXABLE_REASONS
        assert _FIXABLE_REASONS[_LIVE] == "reauth"

    def test_the_setup_path_passes_the_brand(self):
        """Without brand= the title renders 'restore VW Group' for an Audi."""
        import inspect

        from custom_components import vag_connect
        src = inspect.getsource(vag_connect.async_setup_entry)
        assert "brand=" in src.split("raise_issue_auth_required(")[1][:300]


class TestTheCopy:
    """What the two notices may and may not say."""

    def test_the_dead_notice_never_tells_anyone_to_scan(self):
        desc = _STRINGS["issues"][_DEAD]["description"].lower()
        assert "scan" not in desc

    def test_the_dead_notice_names_email_and_password(self):
        desc = _STRINGS["issues"][_DEAD]["description"].lower()
        assert "password" in desc

    def test_the_dead_notice_says_the_entry_survives(self):
        """The whole point of reconfigure over delete-and-re-add."""
        desc = _STRINGS["issues"][_DEAD]["description"].lower()
        assert "history" in desc or "keeps" in desc

    def test_the_live_notice_still_tells_those_users_to_scan(self):
        desc = _STRINGS["issues"][_LIVE]["description"].lower()
        assert "scan" in desc

    @pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.stem)
    def test_both_issues_exist_in_every_language(self, path):
        data = json.loads(path.read_text(encoding="utf-8"))
        issues = data.get("issues", {})
        for reason in (_LIVE, _DEAD):
            assert reason in issues, f"{path.stem} is missing {reason}"
            assert issues[reason].get("title"), f"{path.stem}/{reason} title empty"
            assert issues[reason].get("description"), (
                f"{path.stem}/{reason} description empty"
            )

    @pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.stem)
    def test_no_translation_tells_a_dead_end_user_to_scan_a_code(self, path):
        """The defect this whole change exists to remove, checked in every
        language rather than only in English."""
        data = json.loads(path.read_text(encoding="utf-8"))
        desc = data.get("issues", {}).get(_DEAD, {}).get("description", "")
        for word in ("scan", "scanne", "skann", "escane", "scansion"):
            assert word not in desc.lower(), f"{path.stem} still says {word!r}"

    @pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.stem)
    def test_the_brand_placeholder_survives_translation(self, path):
        """repairs.py always supplies {brand}; a translation that drops or
        renames it renders a literal brace or raises at render time."""
        data = json.loads(path.read_text(encoding="utf-8"))
        title = data.get("issues", {}).get(_DEAD, {}).get("title", "")
        assert "{brand}" in title, f"{path.stem} lost the brand placeholder"
