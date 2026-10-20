# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling someone their password is wrong when VW's servers are down.

``CariadBaseClient.authenticate`` walks a list of login strategies. Its
catch-all exists for a good reason, written at the site (v2.7.2):

    aiohttp.InvalidURL and similar low-level errors can carry tokens or full
    OAuth callback URLs in their __str__.

So the message is redacted and only the exception TYPE is logged. That part is
right and stays. What was wrong is that the **class** was thrown away too: on
the last strategy, everything became a bare ``AuthenticationError``.

That is not only a wording problem. On the poll path an ``AuthenticationError``
becomes ``invalid_credentials`` and starts a reauth flow — so a VW 5xx storm or
an account the backend had rate-limited asked the owner to re-enter credentials
that were never wrong. Both of those already have their own class, their own
translated message in the config flow, and (for ``UpstreamUnavailableError``)
their own handling in the coordinator's self-healing-error classifier. All of it
was simply unreachable from here.

Three classes are now re-raised unchanged. The bar is deliberately narrow and
the tests below pin it from both sides: the message must be one WE wrote, built
from literals plus a status code and a brand name, never able to carry a URL, a
token or an account identifier. ``APIError`` is excluded on purpose despite also
being ours — its message is *derived* from a request URL, redacted or not.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.vag_connect.cariad.api import base as _base
from custom_components.vag_connect.cariad.exceptions import (
    APIError,
    AuthenticationError,
    AuthProxyUnavailableError,
    RateLimitError,
    UpstreamUnavailableError,
)


def _client(_monkeypatch: Any, err: BaseException) -> Any:
    """A real VWEUClient whose every login strategy raises *err*.

    Built the way ``test_cariad.py`` builds one, so the strategy list, its
    order and the catch-all under test are the real ones -- a hand-rolled
    stub would stop exercising the code the moment the resolver changed.
    The portal fallback is stubbed to fail too, so the last-strategy branch
    is the one that answers.
    """
    from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient

    c = VWEUClient(MagicMock(), "u@t.de", "pw")

    async def _boom(*_a: Any, **_k: Any) -> Any:
        raise err

    c._auth.authenticate = _boom          # the "idk" strategies
    c._arm_eu_portal = _boom              # the data_act_portal fallback
    return c


def _authenticate(c: Any) -> BaseException:
    try:
        asyncio.run(c.authenticate())
    except BaseException as raised:  # noqa: BLE001 - the point of the test
        return raised
    raise AssertionError("authenticate() did not raise")


# ── the allowlist is what it says it is ────────────────────────────────────


def test_the_allowlist_holds_exactly_the_three_self_describing_classes() -> None:
    """Pinned so the next addition is deliberate. Anything added here must be
    constructible without a URL — that is the whole criterion."""
    assert _base._SELF_DESCRIBING_ERRORS == (
        UpstreamUnavailableError,
        RateLimitError,
        AuthProxyUnavailableError,
    )


@pytest.mark.parametrize(
    "err",
    [
        UpstreamUnavailableError(502, "volkswagen"),
        RateLimitError(),
        AuthProxyUnavailableError(
            "Website authproxy: the site answered 502 — this is a server-side "
            "failure, not your session"
        ),
    ],
    ids=["upstream", "ratelimit", "authproxy"],
)
def test_none_of_the_allowlisted_messages_can_carry_a_url(err: Exception) -> None:
    """The criterion for being on the list, checked rather than asserted in a
    comment. If one of these ever starts interpolating a request URL, it must
    come off the list and this fails first."""
    text = str(err)
    # A URL, an e-mail address or a token -- not the literal word "HTTP",
    # which the upstream message legitimately uses to report a status code.
    assert "://" not in text
    assert "@" not in text
    assert not any(len(w) > 40 for w in text.split()), "no token-shaped run"


# ── and authenticate() honours it ──────────────────────────────────────────


def test_a_backend_5xx_surfaces_as_itself(monkeypatch) -> None:
    """The reported case. A 502 from VW is not a credential problem, and the
    config flow already has a separate translated message for it."""
    original = UpstreamUnavailableError(502, "volkswagen")
    raised = _authenticate(_client(monkeypatch, original))
    assert isinstance(raised, UpstreamUnavailableError)
    assert raised is original, "re-raised, not rebuilt"


def test_a_rate_limit_surfaces_as_itself(monkeypatch) -> None:
    raised = _authenticate(_client(monkeypatch, RateLimitError()))
    assert isinstance(raised, RateLimitError)
    assert not isinstance(raised, AuthenticationError)


def test_an_authproxy_outage_surfaces_as_itself(monkeypatch) -> None:
    raised = _authenticate(
        _client(monkeypatch, AuthProxyUnavailableError("the site answered 502"))
    )
    assert isinstance(raised, AuthProxyUnavailableError)


# ── the redaction it had to preserve ───────────────────────────────────────


def test_a_foreign_exception_is_still_redacted(monkeypatch) -> None:
    """The reason the catch-all exists. An aiohttp error's text can carry the
    OAuth callback URL, and those JWTs decode to the owner's e-mail and a
    working access token."""
    url = "weconnect://authenticated#access_token=eyJsecret&id_token=eyJalso"
    raised = _authenticate(_client(monkeypatch, ValueError(f"Invalid URL {url}")))
    assert isinstance(raised, AuthenticationError)
    text = str(raised)
    assert "access_token" not in text
    assert "weconnect://" not in text
    assert "ValueError" in text, "the TYPE is still reported, which is the useful half"


def test_an_api_error_is_still_flattened(monkeypatch) -> None:
    """``APIError`` is ours and already redacts its own URL, but its message is
    *derived* from one. It stays on the safe side of the line deliberately, so
    the allowlist criterion keeps meaning what it says."""
    raised = _authenticate(
        _client(monkeypatch, APIError(403, "https://x.invalid/vehicles/VIN123/s"))
    )
    assert isinstance(raised, AuthenticationError)
    assert "VIN123" not in str(raised)


def test_the_coordinator_already_knows_what_to_do_with_the_real_class() -> None:
    """The change is only worth making because the downstream handling exists.
    Checked rather than assumed: the poll-error classifier treats an upstream
    outage as self-healing, so surfacing the real class stops a server outage
    being escalated — and stops it starting a reauth flow."""
    from custom_components.vag_connect.coordinator import _is_selfhealing_poll_error

    assert _is_selfhealing_poll_error(UpstreamUnavailableError(502, "volkswagen")) is True
    assert _is_selfhealing_poll_error(AuthenticationError("nope")) is False
