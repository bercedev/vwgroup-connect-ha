# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A guard that announces an action it does not take teaches people to ignore it.

@georghaberkorn68-ctrl (#1866) ran a CUPRA entry whose primary sign-in was
walled, so every poll cycle asked for a token refresh and every one of them was
refused. The log he sent showed the refusal three times per cycle, every ~180
seconds, for hours across 17 and 18 October. Two things about that line were
wrong, and neither is the rate limit itself — 3 refreshes per rolling hour is
correct and stays:

  1. It said *"pausing to prevent IP ban"*. **Nothing pauses.** The call refuses
     this one attempt and returns; the caller comes back next cycle and is
     refused again. A reader who checks that claim finds it false, and then has
     no reason to believe the next line either.
  2. It said *"please reauthenticate from the UI"* — unconditionally. On his
     entry the primary exchange was failing with ``invalid_client`` before the
     portal fallback took over, so no credential he could type would change the
     answer. The one instruction the error gave was the one thing that could
     not work.

And the level was wrong a third way: ERROR on *every* refusal. The first refusal
is news. The two-hundredth is the same news, and at ERROR it reads as a runaway
loop rather than as a limit doing its job.

So: ERROR once per episode, DEBUG while the budget stays spent, the flag cleared
whenever an attempt is actually booked (a later episode is news again), the
message describing a refused attempt and naming when the next slot frees, and
the reauthenticate advice made conditional in wording rather than stated as the
fix.

The same guard exists twice — ``CariadBaseClient._refresh_tokens`` and
``PorscheClient._refresh``, which is a standalone class with its own copy. Both
are covered here, and so is the absence of the old sentence anywhere in the
shipped component, because one forgotten copy keeps the bug alive for one brand.
"""
from __future__ import annotations

import asyncio
import logging
import pathlib
import time
from typing import Any

import pytest

from custom_components.vag_connect.cariad.api import base as _base
from custom_components.vag_connect.cariad.api.porsche import PorscheClient
from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient
from custom_components.vag_connect.cariad.auth import _mbboauth
from custom_components.vag_connect.cariad.exceptions import AuthenticationError
from custom_components.vag_connect.cariad.models import TokenSet

_BASE_LOGGER = "custom_components.vag_connect.cariad.api.base"
_PORSCHE_LOGGER = "custom_components.vag_connect.cariad.api.porsche"


class _Sess:
    def get(self, url: str, headers: Any = None) -> Any:  # pragma: no cover
        raise AssertionError("no network in these tests")


def _client() -> VWEUClient:
    """A client whose token state is irrelevant: every call below is refused
    by the budget before it reaches any refresh machinery."""
    c = VWEUClient(_Sess(), "u@t.de", "pw")
    c._tokens = TokenSet(
        access_token="READ_BEARER", refresh_token="RT", id_token="i",
        expires_at=0.0, strategy="mbb",
    )
    c._mbb_client_id = "CID"
    return c


def _spend(client: Any, *, for_command: bool = False, age_s: float = 0.0) -> None:
    """Fill the plane's budget by hand, as if N attempts had been booked.

    ``age_s`` backdates them, which is what lets a test assert on the
    "next slot in about N min" figure without waiting an hour.
    """
    history = client._cmd_refresh_history if for_command else client._refresh_history
    now = time.monotonic()
    history[:] = [now - age_s] * _base._REFRESH_MAX_PER_HOUR


def _refuse(client: Any, *, for_command: bool = False) -> None:
    """One refused refresh attempt. Fails the test if it is NOT refused —
    otherwise every assertion below about the log would pass vacuously on a
    build where the guard had stopped guarding."""
    with pytest.raises(AuthenticationError):
        asyncio.run(client._refresh_tokens(for_command=for_command))


def _lines(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


# ── the limit itself is unchanged ──────────────────────────────────────────


def test_the_budget_still_refuses_at_three_per_hour() -> None:
    """The guard's job is unchanged: three booked attempts inside the window
    and the fourth is refused. This is the test that would fail if the reword
    had quietly turned the limit off, which is the only way the rest of this
    file could be passing for the wrong reason."""
    c = _client()
    assert _base._REFRESH_MAX_PER_HOUR == 3
    assert _base._REFRESH_WINDOW_S == 3600
    _spend(c)
    _refuse(c)
    # And it stays refused: the refusal does not book an attempt, so the
    # history is still exactly the three that were there.
    assert len(c._refresh_history) == _base._REFRESH_MAX_PER_HOUR


def test_data_plane_still_flags_the_storm_for_the_repair() -> None:
    """#1078's Repair hangs off this flag; the data plane must still raise it
    and the command plane must still not."""
    c = _client()
    _spend(c)
    _refuse(c)
    assert c.refresh_storm_detected is True

    c2 = _client()
    _spend(c2, for_command=True)
    _refuse(c2, for_command=True)
    assert c2.refresh_storm_detected is False


# ── once per episode, not once per poll ────────────────────────────────────


def test_first_refusal_is_error_and_the_rest_are_debug(caplog) -> None:
    c = _client()
    _spend(c)
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        for _ in range(8):
            _refuse(c)

    errors = _lines(caplog, logging.ERROR)
    debugs = [m for m in _lines(caplog, logging.DEBUG) if "budget" in m]
    assert len(errors) == 1, f"expected one ERROR per episode, got {len(errors)}"
    assert len(debugs) == 7, f"expected the rest at DEBUG, got {len(debugs)}"


def test_a_booked_attempt_makes_the_next_refusal_news_again(caplog, monkeypatch) -> None:
    """The flag is cleared when an attempt is actually booked. Otherwise a
    single episode would silence the guard for the lifetime of the entry —
    including a genuinely new storm three days later."""
    c = _client()

    async def _fake_refresh(session, refresh_token, *, client_id):
        return _mbboauth.MbbTokenSet(
            access_token="READ_BEARER", refresh_token="RT",
            token_type="Bearer", expires_at=0.0,
        )

    # A successful refresh books an attempt and returns; stub the MBB exchange
    # so the test is about the flag, not about the network.
    monkeypatch.setattr(_mbboauth, "refresh", _fake_refresh)

    _spend(c)
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        _refuse(c)                      # episode 1 → ERROR
        _refuse(c)                      # still spent → DEBUG
        assert c._refresh_budget_reported[False] is True

        c._refresh_history.clear()      # window aged out
        asyncio.run(c._refresh_tokens())  # an attempt is booked
        assert c._refresh_budget_reported[False] is False

        _spend(c)
        _refuse(c)                      # episode 2 → ERROR again

    assert len(_lines(caplog, logging.ERROR)) == 2


def test_the_two_planes_report_independently(caplog) -> None:
    """A command-plane episode must not silence the data plane's first
    refusal; they are separate budgets (#584) and separate news."""
    c = _client()
    _spend(c, for_command=True)
    _spend(c)
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        _refuse(c, for_command=True)
        _refuse(c)

    errors = _lines(caplog, logging.ERROR)
    assert len(errors) == 2
    assert any("(command)" in m for m in errors)
    assert any("(data)" in m for m in errors)


# ── what the message says ──────────────────────────────────────────────────


def test_the_message_does_not_claim_to_pause(caplog) -> None:
    c = _client()
    _spend(c)
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        _refuse(c)
    msg = _lines(caplog, logging.ERROR)[0].lower()
    assert "pausing" not in msg
    assert "pause" not in msg
    assert "refus" in msg, "it should say what it actually does"


def test_the_message_does_not_prescribe_reauthentication(caplog) -> None:
    """It may mention credentials — to say they will not help when the
    sign-in is the thing being refused. It must not issue the instruction."""
    c = _client()
    _spend(c)
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        _refuse(c)
    msg = _lines(caplog, logging.ERROR)[0].lower()
    assert "please reauthenticate" not in msg
    assert "reauthenticate from the ui" not in msg


def test_the_message_names_when_the_next_slot_frees(caplog) -> None:
    """Derived from the OLDEST booked attempt, not from a constant. Three
    attempts booked 50 minutes ago → the window frees in about 10 minutes."""
    c = _client()
    _spend(c, age_s=50 * 60)
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        _refuse(c)
    assert "about 10 min" in _lines(caplog, logging.ERROR)[0]

    c2 = _client()
    _spend(c2, age_s=0.0)
    caplog.clear()
    with caplog.at_level(logging.DEBUG, logger=_BASE_LOGGER):
        _refuse(c2)
    assert "about 60 min" in _lines(caplog, logging.ERROR)[0]


def test_the_raised_error_does_not_promise_reauth_either() -> None:
    """The exception text reaches the user as the entry's failure reason, so
    it carries the same obligation as the log line."""
    c = _client()
    _spend(c)
    with pytest.raises(AuthenticationError) as err:
        asyncio.run(c._refresh_tokens())
    text = str(err.value).lower()
    assert "please reauthenticate" not in text
    assert "refusing" in text
    # "storm" stays, deliberately: the reporter asked for it to stay in #1866's
    # title, and six existing tests elsewhere discriminate this exception from
    # every other AuthenticationError by that word. Asserting it here makes
    # that dependency explicit instead of leaving it to be discovered by a
    # reword that breaks an unrelated file.
    assert "storm" in text


# ── the Porsche copy ───────────────────────────────────────────────────────


def _porsche() -> PorscheClient:
    p = PorscheClient(_Sess(), "u@t.de", "pw")
    p._tokens = TokenSet(
        access_token="A", refresh_token="RT", id_token="i", expires_at=0.0,
    )
    return p


def test_porsche_guard_also_reports_once_and_says_what_it_does(caplog) -> None:
    """``PorscheClient`` is not a subclass of ``CariadBaseClient`` — it holds
    its own copy of this guard, so fixing only the base would have left
    Porsche owners with the old behaviour."""
    p = _porsche()
    now = time.monotonic()
    p._refresh_history = [now - 50 * 60] * _base._REFRESH_MAX_PER_HOUR

    with caplog.at_level(logging.DEBUG, logger=_PORSCHE_LOGGER):
        for _ in range(5):
            with pytest.raises(AuthenticationError):
                asyncio.run(p._refresh())

    errors = _lines(caplog, logging.ERROR)
    debugs = [m for m in _lines(caplog, logging.DEBUG) if "budget" in m]
    assert len(errors) == 1
    assert len(debugs) == 4
    msg = errors[0].lower()
    assert "pausing" not in msg
    assert "please reauthenticate" not in msg
    assert "about 10 min" in errors[0]


# ── no forgotten copy ──────────────────────────────────────────────────────


def test_no_shipped_source_still_claims_to_pause() -> None:
    """A third copy of this guard appearing later, or a revert of either of
    the two above, fails here. Comments are allowed to quote the old wording
    (the fix explains itself); logged strings are not."""
    root = pathlib.Path(_base.__file__).resolve().parents[3]
    offenders: list[str] = []
    for path in sorted(root.rglob("*.py")):
        for n, line in enumerate(
            path.read_text(encoding="utf-8", errors="replace").splitlines(), 1
        ):
            if "pausing to prevent IP ban" not in line:
                continue
            if line.lstrip().startswith("#"):
                continue
            offenders.append(f"{path.relative_to(root)}:{n}")
    assert not offenders, f"old wording still shipped at: {offenders}"
