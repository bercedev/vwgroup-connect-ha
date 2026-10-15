# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1827 — VW put an optional advertising opt-in in the way of the login.

On 2026-10-13/14 the sign-in chain started ending on a page VW serves from
``consent.vwgroup.io/hooks/<name>``: "Personalised advertising tailored to your
requirements". It is not the marketing-consent page the identity provider
already served — different host, different path — and it is optional. The page
offers a plain "Not now" link, and following that finishes the login on the
portal exactly as before.

Two people reported the failure within the same hours, and one attached a log
whose landing was ``consent.vwgroup.io/hooks/data-clean-room``. That string is
in no source file here, so our own code printed what it saw: ``refresh()`` ends
on a host that is not volkswagen.de and raises "refresh did not land on the
portal" (``_website_authproxy.py``, the final raise). An integration riding the
same authproxy channel reported and fixed the identical thing independently.

**We decline, and we never accept.** The opt-in is advertising consent, which is
not ours to give on an owner's behalf, and declining is what the page's own "Not
now" does. Only a skip link the page itself offers is followed — never one we
construct — so if VW stops offering it we stop skipping instead of guessing at a
URL. The link is matched by shape (``/hooks/<anything>/skip``) so a second hook
of the same kind needs no code change, and the host is pinned so the shape
cannot match somewhere it was never meant to.
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.vag_connect.cariad.auth._website_authproxy import (
    WebsiteAuthProxyConnector,
)
from custom_components.vag_connect.cariad.exceptions import AuthenticationError

PORTAL_OK = "https://www.volkswagen.de/de/besitzer-und-nutzer/myvolkswagen.html"
HOOK = "https://consent.vwgroup.io/hooks/data-clean-room?session=S1&state=ST"

# The real shape: a root-relative link on the page, carrying the session.
_SKIP_HREF = "/hooks/data-clean-room/skip?session=S1&state=ST"
HOOK_PAGE = (
    '<html><body><h1>Personalised advertising</h1>'
    '<form method="post" action="/hooks/data-clean-room/accept">'
    '<button>Yes please</button></form>'
    f'<a href="{_SKIP_HREF}">Not now</a>'
    '</body></html>'
)
# Same page with the decline link removed — nothing for us to follow.
HOOK_PAGE_NO_SKIP = HOOK_PAGE.replace(f'<a href="{_SKIP_HREF}">Not now</a>', "")


class _Resp:
    def __init__(self, url: str, status: int, text: str) -> None:
        self.url, self.status, self._text = url, status, text
        self.history: tuple[Any, ...] = ()

    async def __aenter__(self) -> "_Resp":
        return self

    async def __aexit__(self, *_exc: Any) -> bool:
        return False

    async def text(self, errors: str | None = None) -> str:  # noqa: ARG002
        return self._text


class _Session:
    """Answers successive GETs from a script, and records what was asked for."""

    def __init__(self, *steps: tuple[str, int, str]) -> None:
        self._steps = list(steps)
        self.requested: list[str] = []

    def get(self, url: str, **_kw: Any) -> _Resp:
        self.requested.append(url)
        landed, status, body = self._steps.pop(0) if self._steps else (
            PORTAL_OK, 200, "",
        )
        return _Resp(landed, status, body)


def _client(session: _Session) -> WebsiteAuthProxyConnector:
    c = WebsiteAuthProxyConnector.__new__(WebsiteAuthProxyConnector)
    c._session = session
    c._resume_dead_this_cycle = False
    c.logged_in = False
    c._headers = MagicMock(return_value={})

    async def _no_relogin() -> bool:
        return False

    c.relogin_if_allowed = _no_relogin
    return c


class TestTheHookIsDeclinedAndTheResumeSurvives:
    def test_the_reporters_case_resumes_instead_of_raising(self) -> None:
        """Land on the hook, follow its decline link, end on the portal."""
        sess = _Session(
            (HOOK, 200, HOOK_PAGE),      # the refresh GET lands on the hook
            (PORTAL_OK, 200, ""),        # declining lands back on the portal
        )
        c = _client(sess)
        asyncio.run(c.refresh())
        assert c.logged_in is True
        assert len(sess.requested) == 2, "the decline link was never followed"

    def test_it_follows_the_decline_link_and_not_the_accept_form(self) -> None:
        """The page offers both. Taking the wrong one would agree to advertising
        on the owner's behalf, which is the one outcome that must be impossible."""
        sess = _Session((HOOK, 200, HOOK_PAGE), (PORTAL_OK, 200, ""))
        asyncio.run(_client(sess).refresh())
        followed = sess.requested[1]
        assert "/skip" in followed
        assert "accept" not in followed
        assert followed.startswith("https://consent.vwgroup.io/")
        assert "session=S1" in followed, "the page's own link carries the session"


class TestWhatItDeliberatelyDoesNotDo:
    def test_a_hook_page_without_a_decline_link_keeps_the_old_verdict(self) -> None:
        """The helper may only ever REMOVE a blocker. With nothing to follow,
        the refresh fails exactly as it did before this change."""
        sess = _Session((HOOK, 200, HOOK_PAGE_NO_SKIP))
        with pytest.raises(AuthenticationError, match="did not land on the portal"):
            asyncio.run(_client(sess).refresh())
        assert len(sess.requested) == 1, "nothing may be fetched with no link"

    def test_a_skip_link_on_another_host_is_not_followed(self) -> None:
        """The host is pinned. A page elsewhere that happens to carry a
        skip-shaped link is not an optional VW consent hook."""
        elsewhere = "https://example.invalid/hooks/whatever"
        sess = _Session((elsewhere, 200, HOOK_PAGE))
        with pytest.raises(AuthenticationError, match="did not land on the portal"):
            asyncio.run(_client(sess).refresh())
        assert len(sess.requested) == 1

    def test_a_good_resume_never_reads_the_body(self) -> None:
        """The body is fetched only for that one host. A healthy resume happens
        on every poll, and it must not pull a page down to throw it away."""
        read: list[str] = []

        class _Watching(_Resp):
            async def text(self, errors: str | None = None) -> str:  # noqa: ARG002
                read.append("text")
                return self._text

        class _WatchingSession(_Session):
            def get(self, url: str, **_kw: Any) -> _Resp:
                self.requested.append(url)
                return _Watching(PORTAL_OK, 200, "")

        sess = _WatchingSession()
        c = _client(sess)
        asyncio.run(c.refresh())
        assert c.logged_in is True
        assert read == [], "the body was read on a healthy resume"


class TestTheInteractivePathToo:
    def test_begin_login_declines_the_hook_before_judging_the_landing(self) -> None:
        """The credential POST lands on the same page. Without the decline,
        ``_finalise_login``'s host check reads it as "login did not complete"."""
        c = WebsiteAuthProxyConnector.__new__(WebsiteAuthProxyConnector)
        c.logged_in = False
        c._headers = MagicMock(return_value={})
        c._session = _Session((PORTAL_OK, 200, ""))

        declined = asyncio.run(c._decline_action_hook(HOOK, HOOK_PAGE))
        assert declined is not None, "the hook was not recognised"
        landed, status = declined
        assert landed == PORTAL_OK
        assert status == 200

        # And the same call is a no-op on an ordinary landing, which is what
        # makes it safe to put in front of every landing check.
        assert asyncio.run(c._decline_action_hook(PORTAL_OK, "")) is None
