# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""v2.14.10 — website-authproxy: submit the OTP into the form's REAL code field.

Live symptom: entering the email code kept failing with a "credential issue",
and a NEW code was emailed after every attempt. Root cause: the Auth0
email-challenge form's code input is not always named ``code`` — it can be
``mfa-code`` / ``otp`` / ``token``. ``submit_otp`` hardcoded ``fields["code"]``,
so the real field stayed empty → the IDP treated it as "no code entered",
re-issued a fresh challenge (a new email) and the flow bounced on
"email-challenge" forever.

v2.14.10 sets the code into whichever recognised field the parsed form actually
carries (the form parser surfaces value-less inputs), falling back to ``code``
only if none is present. Mirrors the working rafaelhutter authproxy flow.
"""
from __future__ import annotations

import logging
from typing import Any

import pytest

from custom_components.vag_connect.cariad.auth._website_authproxy import (
    WebsiteAuthProxyConnector,
)

_LANDED_OK = "https://www.volkswagen.de/de/besitzer-und-nutzer/myvolkswagen.html"


class _Resp:
    def __init__(self, url: str, status: int = 200, text: str = "") -> None:
        self.url = url
        self.status = status
        self._text = text

    async def __aenter__(self) -> "_Resp":
        return self

    async def __aexit__(self, *_a: Any) -> bool:
        return False

    async def text(self, errors: str | None = None) -> str:
        return self._text


class _CaptureSession:
    """Captures the POST body and lands the OTP submit back on volkswagen.de."""

    def __init__(self) -> None:
        self.posted: dict[str, Any] = {}

    def post(self, url: str, **kw: Any) -> _Resp:
        self.posted = dict(kw.get("data") or {})
        return _Resp(_LANDED_OK, status=200, text="<html>welcome</html>")


def _armed_conn(html: str) -> tuple[WebsiteAuthProxyConnector, _CaptureSession]:
    sess = _CaptureSession()
    conn = WebsiteAuthProxyConnector(sess, "u@x.z", "pw")  # type: ignore[arg-type]
    conn._otp_url = "https://identity.vwgroup.io/u/mfa-email-challenge?state=ST"
    conn._otp_html = html
    conn._otp_state = "ST"
    return conn, sess


@pytest.mark.asyncio
async def test_code_goes_into_mfa_code_field() -> None:
    """A form whose code input is named 'mfa-code' (value-less) gets the code —
    not left empty with the code misfiled under 'code'."""
    html = (
        '<form action="/u/mfa-email-challenge?state=ST">'
        '<input name="state" value="ST">'
        '<input name="mfa-code">'
        '<input name="hmac" value="h">'
        '</form>'
    )
    conn, sess = _armed_conn(html)
    ok = await conn.submit_otp("123456")
    assert ok is True
    assert sess.posted["mfa-code"] == "123456"


@pytest.mark.asyncio
async def test_code_goes_into_otp_field_and_remember_opt_in() -> None:
    """Field named 'otp' gets the code; a 'remember' checkbox is set true."""
    html = (
        '<form action="/u/mfa-email-challenge?state=ST">'
        '<input name="state" value="ST">'
        '<input name="otp">'
        '<input name="rememberBrowser">'
        '</form>'
    )
    conn, sess = _armed_conn(html)
    assert await conn.submit_otp("654321") is True
    assert sess.posted["otp"] == "654321"
    assert sess.posted["rememberBrowser"] == "true"


@pytest.mark.asyncio
async def test_standard_code_field_still_works() -> None:
    """Regression: a form that DOES name it 'code' still works."""
    html = (
        '<form action="/u/mfa-email-challenge?state=ST">'
        '<input name="state" value="ST">'
        '<input name="code">'
        '</form>'
    )
    conn, sess = _armed_conn(html)
    assert await conn.submit_otp("111222") is True
    assert sess.posted["code"] == "111222"


@pytest.mark.asyncio
async def test_no_recognisable_field_falls_back_to_code() -> None:
    """If the form exposes no recognisable code input, fall back to 'code'."""
    html = (
        '<form action="/u/mfa-email-challenge?state=ST">'
        '<input name="state" value="ST">'
        '</form>'
    )
    conn, sess = _armed_conn(html)
    assert await conn.submit_otp("999000") is True
    assert sess.posted["code"] == "999000"


# ── #1827 — the step had no logging, and two reporters needed it ─────────────

# Deliberately not a digit run: the negative assertions below prove the code
# never reaches the log, and a six-digit value could collide with a status, a
# length or a timestamp in the captured text and pass for the wrong reason.
_SENTINEL_CODE = "OTPSENTINELZZ41"


class TestTheOtpStepSaysWhatHappened:
    """``submit_otp`` logged nothing at all, while ``begin_login`` logged five
    lines. #1827 is two reporters failing on this exact step within the same
    hours; one attached a full debug log and it ends at "email-OTP challenge
    pending", saying nothing about the step that broke.

    The form's FIELD NAMES are the load-bearing fact, because the matching loop
    this file is about exists precisely for an IDP that renames its code input:
    when none of the recognised names is present the fallback writes "code", the
    IDP reads that as no code entered, re-issues a fresh challenge — a new
    email — and the flow bounces for ever. Both reporters describe that shape,
    so one log line decides whether that is what is happening to them.

    Two of the tests here are negative and can genuinely fail: the code itself
    and the URL's query must never appear. The query carries the OAuth state.
    """

    @pytest.mark.asyncio
    async def test_the_forms_field_names_are_logged(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        html = (
            '<form action="/u/mfa-email-challenge?state=ST">'
            '<input name="state" value="ST">'
            '<input name="mfa-code">'
            '<input name="hmac" value="h">'
            '</form>'
        )
        conn, _ = _armed_conn(html)
        with caplog.at_level(logging.DEBUG):
            assert await conn.submit_otp(_SENTINEL_CODE) is True
        assert "mfa-code" in caplog.text
        assert "hmac" in caplog.text
        assert "code set on" in caplog.text

    @pytest.mark.asyncio
    async def test_the_code_itself_is_never_logged(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The one-time code is a credential for the length of its validity."""
        html = (
            '<form action="/u/mfa-email-challenge?state=ST">'
            '<input name="otp">'
            '</form>'
        )
        conn, sess = _armed_conn(html)
        with caplog.at_level(logging.DEBUG):
            await conn.submit_otp(_SENTINEL_CODE)
        assert sess.posted["otp"] == _SENTINEL_CODE, "the code must still be SENT"
        assert _SENTINEL_CODE not in caplog.text

    @pytest.mark.asyncio
    async def test_an_unrecognised_form_says_so(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """The reporters' hypothesised case. If the IDP has renamed the field
        again, the fallback fires and the log has to name it — otherwise the
        symptom is indistinguishable from a wrong code."""
        html = (
            '<form action="/u/mfa-email-challenge?state=ST">'
            '<input name="state" value="ST">'
            '<input name="verificationCodeInput">'
            '</form>'
        )
        conn, _ = _armed_conn(html)
        with caplog.at_level(logging.DEBUG):
            await conn.submit_otp(_SENTINEL_CODE)
        assert "NO recognised code field" in caplog.text
        assert "verificationCodeInput" in caplog.text, (
            "the unrecognised name is the whole point — it tells us what to add"
        )

    @pytest.mark.asyncio
    async def test_the_status_and_host_chain_are_logged_without_the_query(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        html = '<form action="/u/mfa-email-challenge?state=ST"><input name="otp"></form>'
        conn, _ = _armed_conn(html)
        with caplog.at_level(logging.DEBUG):
            await conn.submit_otp(_SENTINEL_CODE)
        assert "submit_otp POST" in caplog.text
        assert "status 200" in caplog.text
        assert "www.volkswagen.de" in caplog.text
        # _redirect_hosts emits hostnames only, on purpose: the OAuth state and
        # any tokens live in the query. On the SUCCESS path nothing else is
        # logged, so neither the path nor the query appears anywhere.
        assert "state=ST" not in caplog.text
        assert "mfa-email-challenge" not in caplog.text

    @pytest.mark.asyncio
    async def test_a_rejection_names_which_condition_fired(
        self, caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Landing back on the challenge page and an HTTP error are different
        causes with the same user-visible symptom, so the log must separate
        them. The page's own error text stays out: it is the IDP's message, it
        can name the account, and it already travels in the exception."""
        from custom_components.vag_connect.cariad.exceptions import (
            AuthenticationError,
        )

        class _Bounce(_CaptureSession):
            def post(self, url: str, **kw: Any) -> _Resp:
                self.posted = dict(kw.get("data") or {})
                return _Resp(
                    "https://identity.vwgroup.io/u/mfa-email-challenge?state=ST",
                    status=200,
                    # _login_error reads the page's templateModel, not an HTML
                    # node (see _eu_data_act._extract_template_model), so a div
                    # would leave err None and make the negative assertion
                    # below vacuous.
                    text=(
                        '<script>var templateModel = '
                        '{"error": {"text": "Account some.one@example.invalid '
                        'could not be verified"}};</script>'
                    ),
                )

        sess = _Bounce()
        conn = WebsiteAuthProxyConnector(sess, "u@x.z", "pw")  # type: ignore[arg-type]
        conn._otp_url = "https://identity.vwgroup.io/u/mfa-email-challenge?state=ST"
        conn._otp_html = '<form action="/u/mfa-email-challenge"><input name="otp"></form>'
        conn._otp_state = "ST"

        with caplog.at_level(logging.DEBUG), pytest.raises(AuthenticationError):
            await conn.submit_otp(_SENTINEL_CODE)

        assert "landed back on the email-challenge page" in caplog.text
        assert "page error text: present" in caplog.text, (
            "the fixture must actually carry an error, or the next assertion "
            "has nothing to not-log and proves nothing"
        )
        assert "example.invalid" not in caplog.text, (
            "the IDP's page text must not be logged — it can name the account"
        )
        # The path IS logged, deliberately: the rejection branch decides on the
        # substring "email-challenge", so a renamed page would stop firing it
        # silently, and the path is how that becomes visible. The query is not.
        assert "mfa-email-challenge" in caplog.text
        assert "state=ST" not in caplog.text
