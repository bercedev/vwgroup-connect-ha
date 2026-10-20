# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Telling someone their password is wrong at a step they only reached because
it was right.

@kalwados (#1843) added the volkswagen.de read channel on v5.0.0, received the
e-mail code, typed it, and was told:

    Login failed; your username or password are wrong

He had just proved his password — the code exists *because* the credential check
passed. He tried several times, gave up, signed in directly on volkswagen.de in
a browser, and only then could he add the channel. The message sent him looking
in the one place that could not possibly be the problem.

Two routes produced that verdict, on each of the two flows that have an OTP
step -- four sites, all fixed:

* ``_ovw_submit_otp`` (options flow + repair) mapped **every**
  ``AuthenticationError`` from the OTP POST to ``invalid_credentials``, and the
  options step set it again on the ``ok is False`` path, where the login simply
  did not complete;
* ``_wap_submit_otp`` and ``async_step_website_authproxy_otp`` are the
  setup-time twins and did exactly the same thing. He reached the options flow
  because he already had an entry; someone installing the channel for the first
  time reaches the setup flow, and would have been told the same wrong thing.
  Found while checking the callers of ``submit_otp`` for the class split below.

``submit_otp`` also conflated two outcomes under one exception class: an HTTP
error from the site (the site failing) and landing back on the challenge page
(the only outcome plausibly about the code). It raises
``AuthProxyUnavailableError`` for the first now — the class that already means
"server-side failure, not your session" — so the flow can tell a broken site
from a refused code, and neither of them is reported as a password problem.

The sibling path ``_ovw_begin_login`` was hardened for exactly this class in
#1313/#1679 ("only a genuine 401 keeps the credential verdict"). The OTP step
never was.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import unicodedata
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.vag_connect._vwde_reauth import VwDeReauthMixin
from custom_components.vag_connect.cariad.exceptions import (
    AuthenticationError,
    AuthProxyUnavailableError,
)
from custom_components.vag_connect.config_flow import _map_error


class _Flow(VwDeReauthMixin):
    """The mixin with only what ``_ovw_submit_otp`` touches."""

    def __init__(self, err: BaseException | None, ok: bool = True) -> None:
        self.hass = MagicMock()
        self._config_entry = MagicMock()
        self.ovw_reset()
        connector = MagicMock()
        connector.submit_otp = AsyncMock(return_value=ok, side_effect=err)
        self._ovw_connector = connector
        self.closed = False

    async def _ovw_close_session(self) -> None:
        self.closed = True

    def _ovw_capture_cookies(self) -> list[dict[str, Any]]:
        return [{"name": "sso", "value": "x"}]


def _submit(err: BaseException | None = None, ok: bool = True) -> tuple[Any, Any]:
    flow = _Flow(err, ok)
    try:
        got: Any = asyncio.run(flow._ovw_submit_otp("123456"))
        return flow, got
    except ValueError as raised:
        return flow, raised


# ── the verdict is never about the password ────────────────────────────────


def test_a_refused_code_is_reported_as_a_refused_code() -> None:
    _flow, got = _submit(AuthenticationError("OTP rejected"))
    assert isinstance(got, ValueError)
    assert str(got) == "invalid_otp"
    assert str(got) != "invalid_credentials", "the password was already accepted"


def test_a_failing_site_is_not_reported_as_a_refused_code_either() -> None:
    """An HTTP error while checking the code is the site failing. Sending the
    user round the loop for a new code cannot help."""
    _flow, got = _submit(AuthProxyUnavailableError("the site answered 502"))
    assert isinstance(got, ValueError)
    assert str(got) == "upstream_unavailable"


def test_an_unknown_failure_still_says_cannot_connect() -> None:
    _flow, got = _submit(RuntimeError("something else"))
    assert isinstance(got, ValueError)
    assert str(got) == "cannot_connect"


def test_a_good_code_still_succeeds_and_captures_the_cookies() -> None:
    flow, got = _submit(None, ok=True)
    assert got is True
    assert flow._ovw_cookies, "the session cookies are the point of the step"


def test_the_session_is_closed_on_every_path() -> None:
    """``finally`` — a half-open aiohttp session per failed attempt would leak
    one for every code the user tries."""
    for err in (None, AuthenticationError("x"), AuthProxyUnavailableError("x"),
                RuntimeError("x")):
        flow, _ = _submit(err)
        assert flow.closed, f"session left open after {type(err).__name__}"


# ── both routes through the flow ───────────────────────────────────────────


def test_the_error_code_survives_the_flow_mapper() -> None:
    """``_map_error`` drops anything not on its allowlist to ``cannot_connect``,
    which would have silently undone this."""
    assert _map_error("invalid_otp") == "invalid_otp"
    assert _map_error("upstream_unavailable") == "upstream_unavailable"


def test_the_not_completed_route_no_longer_blames_the_password() -> None:
    """The second route: ``submit_otp`` returned False rather than raising."""
    import inspect

    from custom_components.vag_connect import config_flow

    src = inspect.getsource(config_flow.VagConnectOptionsFlow.async_step_add_vwde_otp)
    assert "invalid_otp" in src
    assert "invalid_credentials" not in src, (
        "the ok-is-False branch still reports a password problem"
    )


# ── the setup flow, which is the path a first-time user takes ──


def _wap_submit(err: BaseException | None = None, ok: bool = True) -> Any:
    """Drive the setup-time twin with the same stub treatment.

    Bound to a stub rather than a real flow: ``_wap_submit_otp`` touches only
    the connector, the cookie export and the session close, and a real
    ConfigFlow would drag in a HomeAssistant instance for nothing.
    """
    from custom_components.vag_connect.config_flow import VagConnectConfigFlow

    stub = MagicMock()
    connector = MagicMock()
    connector.submit_otp = AsyncMock(return_value=ok, side_effect=err)
    stub._wap_connector = connector
    stub._wap_capture_cookies = MagicMock(return_value=[{"name": "sso"}])
    stub._wap_close_session = AsyncMock()
    bound = VagConnectConfigFlow._wap_submit_otp.__get__(stub)
    try:
        return asyncio.run(bound("123456")), stub
    except ValueError as raised:
        return raised, stub


def test_the_setup_flow_also_stops_blaming_the_password() -> None:
    got, _stub = _wap_submit(AuthenticationError("OTP rejected"))
    assert isinstance(got, ValueError)
    assert str(got) == "invalid_otp"


def test_the_setup_flow_tells_a_failing_site_apart_too() -> None:
    got, _stub = _wap_submit(AuthProxyUnavailableError("the site answered 502"))
    assert isinstance(got, ValueError)
    assert str(got) == "upstream_unavailable"


def test_the_setup_flow_still_closes_its_session() -> None:
    for err in (None, AuthenticationError("x"), AuthProxyUnavailableError("x"),
                RuntimeError("x")):
        _got, stub = _wap_submit(err)
        assert stub._wap_close_session.await_count == 1, (
            f"session not closed after {type(err).__name__}"
        )


def test_the_setup_not_completed_route_no_longer_blames_the_password() -> None:
    """The setup twin of the ``ok is False`` branch."""
    import inspect

    from custom_components.vag_connect import config_flow

    src = inspect.getsource(
        config_flow.VagConnectConfigFlow.async_step_website_authproxy_otp
    )
    assert "invalid_otp" in src
    assert "invalid_credentials" not in src, (
        "the setup ok-is-False branch still reports a password problem"
    )


# ── the two outcomes really are raised apart ───────────────────────────────


def test_submit_otp_raises_the_site_failure_as_its_own_class() -> None:
    """Checked at the source: the split has to happen in the connector, because
    by the time the flow sees one exception class the distinction is gone."""
    import inspect

    from custom_components.vag_connect.cariad.auth import _website_authproxy

    src = inspect.getsource(_website_authproxy.WebsiteAuthProxyConnector.submit_otp)
    assert "if landed_status >= 400:" in src
    assert "AuthProxyUnavailableError(" in src
    # and the challenge-page landing keeps the auth class
    assert "AuthenticationError(" in src


# ── every language has the message ─────────────────────────────────────────


def _files() -> list[pathlib.Path]:
    from custom_components.vag_connect import const

    root = pathlib.Path(const.__file__).parent
    return [root / "strings.json", *sorted((root / "translations").glob("*.json"))]


def test_invalid_otp_exists_in_every_language() -> None:
    files = _files()
    assert len(files) == 13, "the language set changed; check this test"
    for path in files:
        errs = json.loads(path.read_text(encoding="utf-8"))["config"]["error"]
        assert "invalid_otp" in errs, f"missing from {path.name}"
        msg = errs["invalid_otp"]
        assert msg.strip(), path.name


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name)
def test_no_language_tells_them_the_password_is_wrong(path: pathlib.Path) -> None:
    """The message must say the opposite of what he was told, in every
    language: the password is explicitly *not* the problem."""
    errs = json.loads(path.read_text(encoding="utf-8"))["config"]["error"]
    # Fold accents away before matching. Without this the check misses Czech
    # "kód" and Spanish "código" and fails two languages whose text is
    # perfectly correct -- which is what the first version of this test did.
    # NFD strips accents, but not letters it does not decompose at all --
    # Polish "hasła" keeps its ł and the check missed it. Map those few
    # first, then fold.
    _plain = errs["invalid_otp"].lower()
    for _src, _dst in (("ł", "l"), ("ø", "o"), ("ß", "ss"),
                       ("đ", "d")):
        _plain = _plain.replace(_src, _dst)
    msg = "".join(
        c for c in unicodedata.normalize("NFD", _plain)
        if not unicodedata.combining(c)
    )
    # it has to mention the code, and it has to mention the password only to
    # rule it out — a message that simply omits the password would leave him
    # guessing again.
    # Stems, not whole words: Italian "codice", Spanish "código" and Czech
    # "heslem" are all inflections my first version of this test did not match,
    # and it failed three languages whose text was perfectly correct. The test
    # was the thing that was wrong.
    assert any(w in msg for w in ("cod", "kod", "koodi")), path.name
    assert any(
        w in msg for w in
        ("passw", "wachtwoord", "mot de passe", "contrase", "hesl",
         "hasl", "adgangskode", "passord", "losenord", "salasana")
    ), f"{path.name} does not rule the password out"
