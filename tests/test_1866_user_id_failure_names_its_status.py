# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A failure the log cannot describe is a failure nobody can diagnose.

@georghaberkorn68-ctrl (#1866) attached a twelve-minute debug log from a CUPRA
entry and the only trace of the failure in it was one sentence: *Could not
fetch SEAT/CUPRA user ID*. The question that had been put to him — was the OLA
user-id call answered 401 or 403? — could not be answered from any log this
code was able to produce, because the handler was

    except Exception:
        _LOGGER.warning("Could not fetch SEAT/CUPRA user ID")

A 401, a 403, a timeout and a malformed body all came out identical. He did the
work and the work could not land anywhere.

The fix adds the exception class and, where there is one, the HTTP status —
**and nothing else**. An ``APIError`` keeps the request URL and the response
body as attributes, and that URL carries VIN and UUID path segments while the
body can carry a token. Those are the two things this line must never grow, so
the test below asserts their absence as firmly as it asserts the status's
presence; a well-meant ``%s`` on the exception itself is exactly how this would
regress.
"""
from __future__ import annotations

import logging

import pytest

from custom_components.vag_connect.cariad.exceptions import APIError


class _Boom:
    """Minimal stand-in: only ``_get`` and the attributes it touches.

    ``_tokens = None`` on purpose. ``_fetch_user_id`` first tries to read the
    subject out of a stored id_token, and an entry that has one never reaches
    the HTTP call at all — so a stub without this attribute does not exercise
    the handler under test, it just raises somewhere earlier.
    """

    def __init__(self, err: Exception) -> None:
        self._err = err
        self._tokens = None
        self._user_id: str | None = None
        self._access_token = ""

    async def _get(self, url: str) -> dict:
        raise self._err


async def _run(err: Exception) -> _Boom:
    from custom_components.vag_connect.cariad.api.seat_cupra import SeatCupraClient

    obj = _Boom(err)
    await SeatCupraClient._fetch_user_id(obj)  # type: ignore[arg-type]
    return obj


_SECRET_VIN = "WVWZZZ1KZAW123456"
_SECRET_BODY = '{"access_token":"eyJhbGciOiJSUzI1NiJ9.verysecret"}'


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 403, 429, 500])
async def test_the_status_is_named(caplog: pytest.LogCaptureFixture, status: int) -> None:
    """The distinction the reporter needed and could not get."""
    caplog.set_level(logging.WARNING)
    await _run(APIError(status, f"https://example.invalid/{_SECRET_VIN}/users"))
    msgs = [r.getMessage() for r in caplog.records]
    assert any(f"HTTP {status}" in m for m in msgs), msgs
    assert any("APIError" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_a_non_http_failure_names_its_class_and_no_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timeout has no status, and inventing one would be worse than silence."""
    caplog.set_level(logging.WARNING)
    await _run(TimeoutError())
    msgs = [r.getMessage() for r in caplog.records]
    assert any("TimeoutError" in m for m in msgs), msgs
    assert not any("HTTP" in m for m in msgs), msgs


@pytest.mark.asyncio
async def test_neither_the_url_nor_the_body_reaches_the_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The reason this line stays narrow. An APIError keeps both as attributes;
    logging the exception itself would publish a VIN-bearing path, and logging
    the body would publish a token."""
    caplog.set_level(logging.DEBUG)
    err = APIError(403, f"https://example.invalid/vehicles/{_SECRET_VIN}", _SECRET_BODY)
    await _run(err)
    blob = " ".join(r.getMessage() for r in caplog.records)
    assert _SECRET_VIN not in blob
    assert "eyJhbGciOiJSUzI1NiJ9" not in blob
    assert "verysecret" not in blob
    assert "example.invalid" not in blob


@pytest.mark.asyncio
async def test_the_failure_is_still_swallowed(caplog: pytest.LogCaptureFixture) -> None:
    """The user id is optional — a missing one must not sink the login, which is
    why the bare ``except`` was there in the first place. That part was right."""
    caplog.set_level(logging.WARNING)
    obj = await _run(APIError(403, "https://example.invalid/users"))
    assert obj._user_id is None
