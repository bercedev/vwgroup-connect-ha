# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The refusal that explains itself must explain itself in the log too.

@Joassens tested the v5.0.0 MBB leg-3 refusal on a Passat GTE and confirmed the
message reads correctly in the Home Assistant UI — the gateway accepted the
S-PIN and then refused this integration's client identity, none of it the
owner's fault. Then he looked at the log file for the same event and found:

    command_start_climate(***074295) failed: VehicleCommandError (status=None)

That is all of it. The explanation existed only in the error Home Assistant
surfaced, so anyone reading a log — which, when a command fails, is usually the
person who was asked to send one — saw a class name and a null status.

The reason the log line was built that way is sound and stays, though not for
the reason its own comment gave. It said a raw ``str(err)`` can carry the
VIN-bearing command URL. That is no longer true of ``APIError``, whose message
has been redacted to ``<vin>`` since #1355 — measured, and pinned by a test
below so the premise stays checked rather than assumed. It IS true of the
arbitrary exceptions that also land in this one handler: an aiohttp, httpx or
adb-shell error whose text nobody here has vetted. Either way it does not apply
to ``VehicleCommandError``.
Every reason that class carries is prose written for the owner to read and is
already shown to them in the UI, so withholding it from the log protected
nothing. (``classify_command_failure``'s verdict is logged, at INFO, a level
plenty of installs never record.)

So: the reason joins the ERROR line for a ``VehicleCommandError``, and nothing
else changes. The two tests that matter most here are the ones that keep the
original protection intact — an ``APIError``'s text must still never reach the
log, and the VIN must still be masked.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

import pytest

from homeassistant.exceptions import ServiceValidationError

from custom_components.vag_connect import coordinator as _coord
from custom_components.vag_connect.cariad.exceptions import (
    APIError,
    VehicleCommandError,
)

_LOG = "custom_components.vag_connect.coordinator"
# Synthetic, per CONTRIBUTING: the shape is real, the serial is zeroed.
# The reporter's own masked tail went in here first and the repo's VIN
# guard refused the commit -- which is the guard doing its job, and worth
# leaving a note about rather than quietly correcting.
_VIN = "WVWZZZ3CZZW000000"
# The real leg-3 client-identity refusal, trimmed to its first sentence.
_PROSE = (
    "the car's gateway accepted your S-PIN and then refused this "
    "integration's client identity for commands."
)


class _Client:
    """A client whose one command raises whatever the test handed it."""

    def __init__(self, err: BaseException) -> None:
        self._err = err

    async def command_start_climate(self, vin: str, **kwargs: Any) -> None:
        raise self._err


class _Coord:
    """``_dispatch_cmd_locked`` with only the attributes it touches.

    Borrowing the real method rather than reimplementing it: a copy would keep
    passing after the original changed, which is the whole failure mode this
    file exists to prevent.
    """

    _dispatch_cmd_locked = _coord.VagConnectCoordinator._dispatch_cmd_locked

    def __init__(self, err: BaseException) -> None:
        self._cariad_client = _Client(err)
        self.failures: list[tuple[str, str, Any]] = []

    def record_command_success(self, vin: str, method: str) -> None:
        raise AssertionError("the command raised; this must not be called")

    def record_command_failure(self, vin: str, method: str, reason: Any) -> None:
        self.failures.append((vin, method, reason))

    def _mbb_command_fallback(self, method: str, err: Exception) -> None:
        return None  # no fallback armed: the original error surfaces

    def _skoda_official_command_fallback(
        self, vin: str, method: str, err: Exception, kwargs: dict
    ) -> None:
        return None  # likewise


def _dispatch(err: BaseException) -> tuple[_Coord, BaseException | None]:
    c = _Coord(err)
    caught: BaseException | None = None
    try:
        asyncio.run(c._dispatch_cmd_locked(_VIN, "command_start_climate"))
    except BaseException as raised:  # noqa: BLE001 - it is re-raised by design
        caught = raised
    return c, caught


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]


def test_the_reason_reaches_the_error_line(caplog) -> None:
    _, raised = None, None
    with caplog.at_level(logging.DEBUG, logger=_LOG):
        _, raised = _dispatch(VehicleCommandError("start_climate", _PROSE))
    # It surfaces as a ServiceValidationError carrying the same prose: a
    # VehicleCommandError is a CariadError, not a HomeAssistantError, so
    # v2.24.2 wraps it rather than letting HA log an "Unexpected exception"
    # traceback for a button press.
    assert isinstance(raised, ServiceValidationError)
    assert "accepted your S-PIN" in str(raised)
    line = _errors(caplog)[0]
    assert "accepted your S-PIN" in line
    # and the old content is still there, not replaced by it
    assert "VehicleCommandError" in line
    assert "status=None" in line


def test_a_reasonless_command_error_keeps_the_short_line(caplog) -> None:
    """``VehicleCommandError(command)`` with no reason has nothing to add, and
    must not gain a dangling separator."""
    with caplog.at_level(logging.DEBUG, logger=_LOG):
        _dispatch(VehicleCommandError("start_climate"))
    line = _errors(caplog)[0]
    assert line.endswith("(status=None)")
    assert "—" not in line


def test_an_unvetted_exceptions_text_still_never_reaches_the_log(caplog) -> None:
    """The protection this change had to preserve, stated accurately.

    The handler catches bare ``Exception``, so an aiohttp/httpx/adb-shell error
    lands here with text nobody has vetted — and that text can carry the full
    command URL, VIN and all. Only ``VehicleCommandError.reason`` is exempt.
    """
    url = (
        "https://msg.volkswagen.de/fs-car/bs/climatisation/v1/vehicles/"
        f"{_VIN}/climater/actions"
    )
    with caplog.at_level(logging.DEBUG, logger=_LOG):
        _dispatch(RuntimeError(f"Cannot connect to host for {url}"))
    for line in _errors(caplog):
        assert url not in line
        assert _VIN not in line
        assert "Cannot connect" not in line


def test_api_errors_own_message_is_redacted() -> None:
    """The premise the handler's comment rests on, measured rather than
    assumed. If ``_redact_url`` ever stops masking the VIN path, the comment
    above the log line becomes wrong and this fails -- which is the point: the
    old comment claimed the opposite of what the code does, and nothing caught
    it."""
    url = (
        "https://msg.volkswagen.de/fs-car/bs/climatisation/v1/vehicles/"
        f"{_VIN}/climater/actions"
    )
    text = str(APIError(403, url))
    assert _VIN not in text
    assert "<vin>" in text


def test_the_vin_is_masked_on_both_branches(caplog) -> None:
    for err in (VehicleCommandError("start_climate", _PROSE),
                VehicleCommandError("start_climate")):
        caplog.clear()
        with caplog.at_level(logging.DEBUG, logger=_LOG):
            _dispatch(err)
        line = _errors(caplog)[0]
        assert _VIN not in line
        assert "000000" in line, "the masked tail is what makes it identifiable"


def test_the_classification_is_still_recorded(caplog) -> None:
    """The reason-in-the-log change must not disturb the bookkeeping that the
    command-health sensors are built on."""
    c, _ = _dispatch(VehicleCommandError("start_climate", _PROSE))
    assert len(c.failures) == 1
    vin, method, _reason = c.failures[0]
    assert (vin, method) == (_VIN, "command_start_climate")


def test_the_failure_still_surfaces_and_keeps_its_text() -> None:
    """Logging the reason must not change what the caller sees. A failure
    swallowed here would turn a refused command into a silent one, and a
    failure stripped of its prose would undo the thing #584 confirmed works."""
    _, raised = _dispatch(VehicleCommandError("start_climate", _PROSE))
    assert isinstance(raised, ServiceValidationError)
    assert _PROSE in str(raised)
