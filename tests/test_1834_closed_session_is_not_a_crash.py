# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1834 — a poll that outlived its session was filed as a crash.

The portal connector does not own its HTTP session. The coordinator creates it
with ``async_create_clientsession(self.hass)`` and the comment there states the
consequence plainly: unloading the entry closes it, and there is nothing to
unwind by hand. So any poll still in flight when the user reloads the
integration — or when Home Assistant shuts down — arrives at a session aiohttp
has already closed, and aiohttp answers with a bare
``RuntimeError("Session is closed")``.

Nothing caught it. The retry handler in ``_get_json`` takes ``TimeoutError``
and ``ClientConnectionError``; a ``RuntimeError`` is neither, so it travelled
up to the coordinator and into the error reporter, where a shutdown race was
filed as a crash on a released version.

Two things are asserted here, and the second is the one that matters more than
the exception type: a closed session is **not retried**. Retrying is the one
response that cannot possibly help — the session will not reopen — and the
retry loop would spend its full backoff ladder discovering that, three sleeps
deep, during a shutdown.

On the parent commit the soft case raises ``RuntimeError`` and the "no request
was attempted" assertions fail, because the guard does not exist yet.

Deliberately NOT ``UpstreamUnavailableError``, which was already imported and
would have been the cheap move: its message tells the user this is a
"server-side issue at VW" and that they should not reconfigure. Our own session
going away locally is not that, and an explanation that names the wrong cause
is worse than a plain one. ``test_the_message_does_not_blame_vw`` pins that.
"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest

_URL = "https://vwgroup-datahub.vwgroup.com/proxy_api/some/path"


class _ClosedCtx:
    """What aiohttp does: the error surfaces on entry, not on ``.get()``."""

    async def __aenter__(self) -> Any:
        raise RuntimeError("Session is closed")

    async def __aexit__(self, *_a: object) -> None:
        return None


class _Session:
    """A session that reports itself closed and counts attempted requests."""

    def __init__(self, *, closed: bool) -> None:
        self.closed = closed
        self.calls = 0
        self.cookie_jar = type(
            "_J", (), {"filter_cookies": lambda *_a: {}, "update_cookies": lambda *_a: None}
        )()

    def get(self, *_a: object, **_k: object) -> _ClosedCtx:
        self.calls += 1
        return _ClosedCtx()

    post = get


@pytest.fixture(autouse=True)
def _no_real_backoff(monkeypatch: pytest.MonkeyPatch) -> list[float]:
    """Record every backoff sleep, and never actually wait for one."""
    slept: list[float] = []

    async def _instant(delay: float) -> None:
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", _instant)
    return slept


def _connector(*, closed: bool):
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        EUDataActConnector,
    )

    session = _Session(closed=closed)
    return EUDataActConnector(session, brand="volkswagen"), session  # type: ignore[arg-type]


def _run(coro: Any) -> Any:
    return asyncio.new_event_loop().run_until_complete(coro)


# ── The fix ──────────────────────────────────────────────────────────────────

def test_a_soft_read_on_a_closed_session_is_just_no_data() -> None:
    """Soft calls already mean "no data this poll"; a vanished session is that.

    RED on the parent: RuntimeError("Session is closed") escapes instead.
    """
    conn, _ = _connector(closed=True)
    assert _run(conn._get_json(_URL, soft=True)) is None


def test_a_hard_read_on_a_closed_session_does_not_look_like_bad_credentials() -> None:
    """A hard call must still fail — but not as an auth problem, or the caller
    replays the password against a session that no longer exists."""
    from custom_components.vag_connect.cariad.exceptions import (
        AuthenticationError,
        CariadError,
    )

    conn, _ = _connector(closed=True)
    with pytest.raises(CariadError) as excinfo:
        _run(conn._get_json(_URL))
    assert not isinstance(excinfo.value, AuthenticationError), (
        "would send the caller into a re-login it cannot complete"
    )


@pytest.mark.parametrize("soft", [True, False], ids=["soft", "hard"])
def test_a_closed_session_is_never_retried(
    soft: bool, _no_real_backoff: list[float]
) -> None:
    """The part that matters during a shutdown: no request, and no waiting.

    RED on the parent, which attempts the request and then sleeps its way
    through the full backoff ladder before giving up.
    """
    from custom_components.vag_connect.cariad.exceptions import CariadError

    conn, session = _connector(closed=True)
    if soft:
        _run(conn._get_json(_URL, soft=True))
    else:
        with pytest.raises(CariadError):
            _run(conn._get_json(_URL))

    assert session.calls == 0, "reached for a session it had been told was closed"
    assert _no_real_backoff == [], "slept waiting for a session that cannot reopen"


def test_the_message_does_not_blame_vw() -> None:
    """UpstreamUnavailableError was right there and would have said the wrong
    thing. Whatever the wording becomes, it must not pin this on the backend
    or send the user to their account settings."""
    from custom_components.vag_connect.cariad.exceptions import CariadError

    conn, _ = _connector(closed=True)
    with pytest.raises(CariadError) as excinfo:
        _run(conn._get_json(_URL))
    msg = str(excinfo.value).lower()
    assert "unload" in msg or "reload" in msg, "does not say what actually happened"
    for wrong in ("server-side", "vw is", "credential", "password", "reconfigure"):
        assert wrong not in msg, f"blames the wrong thing: {wrong!r}"


# ── Guards: these pass on both sides, by design ──────────────────────────────

def test_an_open_session_is_untouched() -> None:
    """The guard reads ``closed``; an open session must fall straight through
    to the request it would have made before."""
    conn, session = _connector(closed=False)
    with pytest.raises(RuntimeError):
        _run(conn._get_json(_URL, soft=True))
    assert session.calls == 1, "the guard swallowed a live request"


def test_a_session_double_with_no_closed_attribute_still_works() -> None:
    """Most existing tests pass a stub with no ``closed`` at all. The guard
    uses getattr(..., False) precisely so none of them change behaviour; this
    pins that, because the obvious ``self._session.closed`` would break 40+
    tests with an AttributeError."""
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        EUDataActConnector,
    )

    class _NoClosed:
        def __init__(self) -> None:
            self.calls = 0
            self.cookie_jar = type(
                "_J", (), {"filter_cookies": lambda *_a: {}, "update_cookies": lambda *_a: None}
            )()

        def get(self, *_a: object, **_k: object) -> _ClosedCtx:
            self.calls += 1
            return _ClosedCtx()

    session = _NoClosed()
    assert not hasattr(session, "closed")
    conn = EUDataActConnector(session, brand="volkswagen")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        _run(conn._get_json(_URL, soft=True))
    assert session.calls == 1

def test_a_magicmock_session_is_not_mistaken_for_a_closed_one() -> None:
    """The bug this guard shipped with on its first draft, kept as a test.

    ``getattr(mock, "closed", False)`` does not return False — MagicMock
    invents the attribute on access and the result is truthy. The first
    version of the guard read that as "closed" and quietly swallowed nine
    live tests across two files, each of which hands the connector a
    MagicMock and expects its real retry behaviour.

    So the guard compares against True itself. aiohttp's ClientSession.closed
    is a real bool property, which makes the strict test exact for the only
    object whose answer matters.
    """
    from unittest.mock import MagicMock

    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        EUDataActConnector,
    )

    session = MagicMock()
    assert getattr(session, "closed", False), (
        "the premise of this test: a Mock's .closed is truthy, not False"
    )
    session.get = lambda *_a, **_k: _ClosedCtx()

    conn = EUDataActConnector(session, brand="volkswagen")  # type: ignore[arg-type]
    with pytest.raises(RuntimeError):
        _run(conn._get_json(_URL, soft=True))

def test_the_whole_poll_makes_no_request_and_never_reaches_the_download() -> None:
    """Why there is exactly ONE guard, and not a second one further down.

    ``_download_dataset_raw`` reaches the session directly, and its own
    docstring says so on purpose: "This GET bypasses ``_get_json``", because
    the download endpoint needs its own filename/type headers. Its except
    clause takes TimeoutError and ClientConnectionError, so a RuntimeError
    would travel exactly as far as #1834's did.

    A guard there would nonetheless be dead code. ``get_vehicle_data`` is the
    only caller, and it reaches the download only after two soft ``_get_json``
    calls — the metadata and the dataset listing — the second of which returns
    early when it comes back empty. With the session closed, the single guard
    turns both into None and the function returns its empty VehicleData before
    the loop exists.

    This is pinned as a test rather than written as a comment because it is a
    claim about reachability, and reachability is what a later refactor
    silently changes. If a route to the download appears that does not pass a
    guarded call, this goes red and the second guard stops being dead code.
    """
    from custom_components.vag_connect.cariad.auth._eu_data_act import (
        EUDataActConnector,
    )

    calls: list[str] = []

    class _Watched(_Session):
        async def _download_dataset_raw(self, *_a: object, **_k: object) -> None:
            calls.append("download")

    session = _Watched(closed=True)
    conn = EUDataActConnector(session, brand="volkswagen")  # type: ignore[arg-type]

    def _boom(*_a: object, **_k: object) -> None:
        calls.append("download")
        raise AssertionError("reached the download with a closed session")

    conn._download_dataset_raw = _boom  # type: ignore[method-assign]

    data = _run(conn.get_vehicle_data("WVWZZZ000M0000001"))

    assert calls == [], "the download loop ran on a closed session"
    assert session.calls == 0, "a request was attempted"
    assert data is not None and data.vin == "WVWZZZ000M0000001", (
        "the poll should still hand back its empty VehicleData, not raise"
    )
