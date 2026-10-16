"""Review M2 — a companion unload or reload never interleaves taps.

On a reload HA unloads the old entry and then sets up the new one. The old
client's walk (a read or a command) used to keep going after the unload: the
shutdown only closed the transport, the poll and app-sync loops were not tied
to the entry, and an add-on /shell already in flight still answered. The new
client's first read could then dump and tap the same phone in between.

Now the unload waits (bounded) for the running walk to finish its cleanup,
refuses anything queued behind it, closes the transport for good, and HA
cancels the companion background tasks with the entry.
"""
from __future__ import annotations

import asyncio
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.vag_connect.cariad.models import VehicleData
from custom_components.vag_connect.companion.addon_transport import AddOnAdbTransport
from custom_components.vag_connect.companion.client import CompanionClient
from custom_components.vag_connect.companion.transport import CompanionTransportError
from custom_components.vag_connect.const import (
    CONF_STRATEGY,
    DOMAIN,
    STRATEGY_COMPANION_ADB,
)

VIN = "WVWZZZTESTVIN0001"


@pytest.fixture(autouse=True)
def _no_unsettled_phones():
    from custom_components.vag_connect.companion import channel

    channel._UNSETTLED.clear()
    yield
    channel._UNSETTLED.clear()


class _Phone:
    """One phone shared by the old and the new client; logs who did what."""

    def __init__(self) -> None:
        self.events: list[tuple[str, str]] = []


class _PhoneTransport:
    """A transport onto the shared phone. Every call is logged and yields."""

    def __init__(self, phone: _Phone, who: str) -> None:
        self._phone = phone
        self._who = who
        self.connected = True

    async def _op(self, what: str) -> None:
        self._phone.events.append((self._who, what))
        await asyncio.sleep(0.01)

    async def connect(self, timeout_s: float = 10.0) -> None:  # noqa: ARG002
        self.connected = True

    async def close(self) -> None:
        # Like an add-on /shell already sent: closing does not stop the walk.
        self.connected = False

    async def shutdown(self) -> None:
        await self.close()

    async def foreground_app(self, package: str, timeout_s: float = 10.0) -> None:  # noqa: ARG002
        await self._op("foreground")

    async def current_app_version(self, package: str) -> str | None:  # noqa: ARG002
        return None

    async def dump_ui(self, timeout_s: float = 15.0) -> str:  # noqa: ARG002
        await self._op("dump")
        return "<hierarchy/>"

    async def tap(self, x: int, y: int, timeout_s: float = 10.0) -> None:  # noqa: ARG002
        await self._op("tap")

    async def key_back(self, timeout_s: float = 10.0) -> None:  # noqa: ARG002
        await self._op("back")

    def __getattr__(self, name: str) -> Any:
        async def _noop(*_a: Any, **_k: Any) -> None:
            return None

        return _noop


def _client(transport: Any) -> CompanionClient:
    client = CompanionClient(
        brand="volkswagen", vin=VIN, host="phone", port=5555,
        adbkey_path="/nonexistent", time_fn=lambda: 10_000.0,
    )
    client._transport = transport
    client._channel._t = transport
    return client


def _shutdown_coord(client: CompanionClient) -> Any:
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    coord = VagConnectCoordinator.__new__(VagConnectCoordinator)
    coord.hass = MagicMock()
    coord.entry = MagicMock()
    coord.entry.entry_id = "old"
    coord._started = True
    coord._cariad_client = client
    coord.async_stop_push_managers = AsyncMock()
    return coord


def _slow_walk(transport: _PhoneTransport, done: list[str]):
    """A read that walks into a detail screen and back, slowly."""

    async def _walk() -> dict[str, object]:
        await transport.dump_ui()
        await transport.tap(1, 1)
        await transport.dump_ui()
        await transport.tap(2, 2)
        await transport.key_back()  # cleanup: back to the overview
        await transport.dump_ui()
        done.append("walk")
        return {}

    return _walk


class TestReloadDuringWalk:
    @pytest.mark.asyncio
    async def test_new_first_dump_comes_after_the_old_walks_last_tap(self) -> None:
        phone = _Phone()
        old_t = _PhoneTransport(phone, "old")
        old = _client(old_t)
        done: list[str] = []
        old._channel._read_serialized = _slow_walk(old_t, done)

        walk = asyncio.create_task(old._channel.read())
        await asyncio.sleep(0.005)  # the walk is under way
        assert phone.events and not done

        # reload: HA unloads the old entry, then sets up the new one
        await _shutdown_coord(old).async_shutdown()
        new_t = _PhoneTransport(phone, "new")
        new = _client(new_t)

        async def _first_read() -> dict[str, object]:
            await new_t.dump_ui()
            return {}

        new._channel._read_serialized = _first_read
        await new._channel.read()

        assert done == ["walk"]
        await walk
        last_old = max(
            i for i, (who, what) in enumerate(phone.events)
            if who == "old" and what in ("tap", "back")
        )
        first_new = phone.events.index(("new", "dump"))
        assert first_new > last_old

    @pytest.mark.asyncio
    async def test_a_read_queued_behind_the_walk_is_refused(self) -> None:
        phone = _Phone()
        old_t = _PhoneTransport(phone, "old")
        old = _client(old_t)
        done: list[str] = []
        old._channel._read_serialized = _slow_walk(old_t, done)

        walk = asyncio.create_task(old._channel.read())
        await asyncio.sleep(0.005)
        queued = asyncio.create_task(old._channel.read())  # a poll waiting its turn
        await asyncio.sleep(0)

        await old.close()
        await walk
        with pytest.raises(CompanionTransportError):
            await queued
        assert done == ["walk"]  # only the one walk ran; the queued one never did
        assert [w for w in phone.events if w[1] == "tap"] == [("old", "tap")] * 2

    @pytest.mark.asyncio
    async def test_unload_wait_is_bounded(self) -> None:
        phone = _Phone()
        old_t = _PhoneTransport(phone, "old")
        old = _client(old_t)
        stuck = asyncio.Event()

        async def _hung() -> dict[str, object]:
            await stuck.wait()
            return {}

        old._channel._read_serialized = _hung
        walk = asyncio.create_task(old._channel.read())
        await asyncio.sleep(0)
        with patch("custom_components.vag_connect.companion.client._CLOSE_WAIT_S", 0.05):
            await asyncio.wait_for(old.close(), timeout=2)
        assert old_t.connected is False
        stuck.set()
        await walk


class TestTransportShutdown:
    @pytest.mark.asyncio
    async def test_shut_down_transport_never_reconnects(self) -> None:
        t = AddOnAdbTransport("addon", 8129)
        await t.shutdown()
        with pytest.raises(CompanionTransportError):
            await t.connect()

    @pytest.mark.asyncio
    async def test_close_alone_still_allows_a_reconnect(self) -> None:
        # close() is also the reconnect-on-next-read path after a shell error
        t = AddOnAdbTransport("addon", 8129)
        await t.close()
        t._get_session = AsyncMock(side_effect=RuntimeError("reached connect"))
        with pytest.raises(RuntimeError, match="reached connect"):
            await t.connect()

    @pytest.mark.asyncio
    async def test_addon_shutdown_waits_for_the_shell_in_flight(self) -> None:
        t = AddOnAdbTransport("addon", 8129)
        t._device = "serial"
        started = asyncio.Event()

        async def _slow_post(_cmd: str, _timeout: float) -> str:
            started.set()
            await asyncio.sleep(0.05)
            return "ok"

        t._post_shell = _slow_post
        call = asyncio.create_task(t.shell("input tap 1 1"))
        await started.wait()
        assert await t.shutdown() is True
        assert call.done() and call.result() == "ok"
        with pytest.raises(CompanionTransportError):
            await t.shell("input tap 2 2")  # nothing further is sent

    @pytest.mark.asyncio
    async def test_addon_shutdown_reports_an_unanswered_command(self) -> None:
        t = AddOnAdbTransport("addon", 8129)
        t._device = "serial"

        async def _timed_out(_cmd: str, _timeout: float) -> str:
            await asyncio.sleep(0.01)
            raise CompanionTransportError("the ADB Bridge add-on became unreachable")

        t._post_shell = _timed_out
        call = asyncio.create_task(t.shell("input tap 1 1"))
        await asyncio.sleep(0)
        assert await t.shutdown() is False
        with pytest.raises(CompanionTransportError):
            await call

    @pytest.mark.asyncio
    async def test_cancelling_the_caller_still_cancels(self) -> None:
        t = AddOnAdbTransport("addon", 8129)
        t._device = "serial"
        started = asyncio.Event()

        async def _slow_post(_cmd: str, _timeout: float) -> str:
            started.set()
            await asyncio.sleep(30)
            return ""

        t._post_shell = _slow_post
        call = asyncio.create_task(t.shell("input tap 1 1"))
        await started.wait()
        call.cancel()
        with pytest.raises(asyncio.CancelledError):
            await call


# ── a command the add-on finishes on its own ─────────────────────────────────


class _AddOnBackend:
    """The add-on side: a /shell it accepted runs to completion on the phone
    whatever happens to the HTTP request that sent it.

    Driven by events, not by durations. The first version took a run time and an
    HTTP timeout in real seconds and let them race the unload: the walk's second
    command had to be attempted AFTER ``close()`` had shut the transport, and on
    the (0.3, 0.05) pair those two moments were about 30 ms apart. It passed
    alone and failed under load, which is the worst kind of test, because every
    "suite green" statement in the project became a coin toss.

    None of it needs a clock. ``close()`` shuts the transport and marks the
    phone unsettled before it returns (client.py:645-659), and the transport
    then refuses on a flag (addon_transport.py:195-196), so the ordering under
    test follows from causality once the test says when each step happens.

    ``answers`` picks the case the old parametrisation expressed in seconds:
    True for a request answered late (the phone finishes and the reply gets
    through), False for one that gives up while the add-on carries on anyway.
    """

    def __init__(self, phone: _Phone, *, answers: bool = True) -> None:
        self._phone = phone
        self._answers = answers
        self.accepted = asyncio.Event()         # the add-on has the command
        self.finish_on_phone = asyncio.Event()  # it finishes running it
        self.give_up_on_http = asyncio.Event()  # the request stops waiting
        self.jobs: list[asyncio.Task] = []

    async def _run(self, cmd: str) -> str:
        await self.finish_on_phone.wait()
        self._phone.events.append(("old", "tap" if "tap" in cmd else "shell"))
        return ""

    async def post(self, cmd: str, _timeout: float) -> str:
        job = asyncio.ensure_future(self._run(cmd))
        self.jobs.append(job)
        self.accepted.set()
        if self._answers:
            # Answered late: the reply is whatever the finished job returned.
            return await asyncio.shield(job)
        await self.give_up_on_http.wait()
        raise CompanionTransportError("the add-on became unreachable")


def _addon_client(phone: _Phone, backend: _AddOnBackend) -> CompanionClient:
    t = AddOnAdbTransport("phone", 5555)
    t._device = "serial"
    t._post_shell = backend.post
    return _client(t)


def _new_client(phone: _Phone) -> tuple[CompanionClient, Any]:
    new_t = _PhoneTransport(phone, "new")
    new = _client(new_t)

    async def _first_read() -> dict[str, object]:
        await new_t.dump_ui()
        return {}

    new._channel._read_serialized = _first_read
    return new, new_t


class TestCommandTheAddOnFinishesAlone:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "answers", [True, False], ids=["answered-late", "never-answered"],
    )
    async def test_new_first_dump_waits_for_the_old_tap(self, answers: bool) -> None:
        """The walk outlives the unload wait with a tap still running in the
        add-on: answered late, or never, its HTTP request giving up while the
        add-on carries on.

        Every step is sequenced by an event rather than by a duration, and
        ``_CLOSE_WAIT_S`` is 0.0, which is exactly what "the wait ran out"
        means. ``_AddOnBackend`` records why the earlier timings were a race.

        Which assertion carries what, since the two cases differ in the
        production code and not only in the fixture: the refusal of the second
        command is the same in both. The ordering is only genuinely at risk in
        the never-answered case — when the request IS answered late, the reply
        is the finished job, so the tap cannot land after the walk by
        construction. The parked assertion covers the gate in both.
        """
        phone = _Phone()
        backend = _AddOnBackend(phone, answers=answers)
        old = _addon_client(phone, backend)
        sent_more: list[BaseException] = []

        async def _walk() -> dict[str, object]:
            try:
                await old._transport.shell("input tap 1 1")
            except CompanionTransportError:
                pass
            try:
                await old._transport.shell("input tap 2 2")
            except CompanionTransportError as err:
                sent_more.append(err)
            return {}

        old._channel._read_serialized = _walk
        walk = asyncio.create_task(old._channel.read())
        await backend.accepted.wait()   # the add-on is holding the first command

        # _INFLIGHT_WAIT_S is 0.0 for the same reason _CLOSE_WAIT_S is: the
        # scenario is a wait that ran out, not a wait of some length. The
        # add-on is still holding the first command here, and shutdown()
        # waits for it (addon_transport.py:174) — at the shipped 40 s that
        # would be 40 s of real time per case for an outcome that is already
        # decided. That the wait HAPPENS, and that it reports an unanswered
        # command, is covered by TestTransportShutdown above.
        with patch("custom_components.vag_connect.companion.client._CLOSE_WAIT_S", 0.0), \
             patch("custom_components.vag_connect.companion.addon_transport._INFLIGHT_WAIT_S", 0.0), \
             patch("custom_components.vag_connect.companion.client._SETTLE_S", 30.0):
            await old.close()
        # close() has returned, so the transport is shut and the phone is marked
        # unsettled — both before the first command has resolved. That is the
        # ordering the old durations were trying to arrange by hand.

        if answers:
            backend.finish_on_phone.set()   # the phone finishes; the reply lands
        else:
            backend.give_up_on_http.set()   # the request gives up on it
        await walk
        assert sent_more, "the old walk sent a second command after the unload"

        # The next client's first walk has to be held by the settle the unload
        # left behind. Parking it ON the gate rather than next to it is the
        # point: awaiting the add-on's job first would make the ordering below
        # true by construction, which is an arrangement and not a test.
        from custom_components.vag_connect.companion import channel

        parked = asyncio.Event()
        resume = asyncio.Event()

        async def _park(_delay: float) -> None:
            parked.set()
            await resume.wait()

        new, _ = _new_client(phone)
        with patch.object(channel.asyncio, "sleep", _park):
            read = asyncio.create_task(new._channel.read())
            # Bounded only so a missing gate fails instead of hanging: on the
            # passing path this is set on the first scheduling, so the size
            # cannot matter. It is the one clock left here and it can only fire
            # in the failing direction.
            await asyncio.wait_for(parked.wait(), 10)
            assert ("new", "dump") not in phone.events, (
                "the next walk dumped without waiting out the settle"
            )
            backend.finish_on_phone.set()   # now the add-on finishes its tap
            await asyncio.gather(*backend.jobs)
            resume.set()
            await read

        assert phone.events.count(("old", "tap")) == 1, "the tap must run once"
        assert phone.events.index(("new", "dump")) > phone.events.index(("old", "tap"))

    @pytest.mark.asyncio
    async def test_a_clean_unload_leaves_no_settle_wait(self) -> None:
        phone = _Phone()
        old = _addon_client(phone, _AddOnBackend(phone))
        with patch("custom_components.vag_connect.companion.client._SETTLE_S", 30.0):
            await old.close()
        new, _ = _new_client(phone)
        await asyncio.wait_for(new._channel.read(), timeout=1)


class TestTheSettleGate:
    """``_ScreenLock._settle`` is what keeps the next client off a phone the
    previous unload could not confirm idle (channel.py:246-271).

    It used to be covered only from one side: the test above asserts that a
    CLEAN unload leaves no wait. Nothing asserted that an unconfirmed one does
    leave one, which is the half the reload ordering actually depends on — so
    the whole gate could have been deleted with the suite staying green.

    Both tests here are clock-free on purpose. The gate sleeps the remaining
    time and then drops its own mark, so a test that sets a short real deadline
    and hopes to arrive before it expires is the same race this file just had.
    Instead: a deadline in the future with ``asyncio.sleep`` captured proves the
    wait was asked for, and a deadline already past proves the mark is dropped
    without waiting.
    """

    @pytest.mark.asyncio
    async def test_an_unconfirmed_unload_makes_the_next_walk_wait(self) -> None:
        from custom_components.vag_connect.companion import channel

        phone = _Phone()
        new, _ = _new_client(phone)
        keys = new._phone_keys
        assert keys, "without a phone key the gate cannot apply to anything"
        channel._UNSETTLED.update({k: time.monotonic() + 30.0 for k in keys})

        asked: list[float] = []

        async def _record(delay: float) -> None:
            asked.append(delay)

        with patch.object(channel.asyncio, "sleep", _record):
            await new._channel.read()

        # Module-wide capture: patching this module's ``asyncio`` patches the
        # asyncio module, so the transport's own 0.01 s yields are in here too.
        # The question is whether a settle-SIZED wait was asked for.
        assert any(d > 25.0 for d in asked), (
            f"the first walk did not wait out the settle; waits asked for: {asked}"
        )
        assert phone.events == [("new", "dump")]

    @pytest.mark.asyncio
    async def test_a_settle_that_has_passed_is_dropped_without_waiting(self) -> None:
        from custom_components.vag_connect.companion import channel

        phone = _Phone()
        new, _ = _new_client(phone)
        keys = new._phone_keys
        channel._UNSETTLED.update({k: time.monotonic() - 1.0 for k in keys})

        asked: list[float] = []

        async def _record(delay: float) -> None:
            asked.append(delay)

        with patch.object(channel.asyncio, "sleep", _record):
            await new._channel.read()

        assert not [d for d in asked if d > 1.0], (
            f"waited on a settle that had already passed; waits asked for: {asked}"
        )
        for key in keys:
            assert key not in channel._UNSETTLED, "the gate must drop its own mark"


class TestCancelledClose:
    @pytest.mark.asyncio
    async def test_cancelling_close_still_shuts_the_transport(self) -> None:
        phone = _Phone()
        old_t = _PhoneTransport(phone, "old")
        old = _client(old_t)
        stuck = asyncio.Event()

        async def _hung() -> dict[str, object]:
            await stuck.wait()
            return {}

        old._channel._read_serialized = _hung
        walk = asyncio.create_task(old._channel.read())
        await asyncio.sleep(0)
        closing = asyncio.create_task(old.close())
        await asyncio.sleep(0.01)
        closing.cancel()
        with pytest.raises(asyncio.CancelledError):
            await closing
        assert old_t.connected is False
        from custom_components.vag_connect.companion import channel

        assert f"vin:{VIN}" in channel._UNSETTLED  # the walk may still be acting
        stuck.set()
        await walk


# ── background tasks are tied to the entry ──────────────────────────────────


class _Entry:
    """The two ConfigEntry hooks used, with HA's unload semantics."""

    def __init__(self, data: dict[str, Any]) -> None:
        self.data = data
        self.options: dict[str, Any] = {}
        self.entry_id = "e1"
        self.tasks: dict[str, asyncio.Task] = {}
        self._on_unload: list[Any] = []

    def async_create_background_task(self, _hass: Any, coro: Any, name: str) -> asyncio.Task:
        task = asyncio.ensure_future(coro)
        self.tasks[name] = task
        return task

    def async_on_unload(self, func: Any) -> None:
        self._on_unload.append(func)

    async def unload(self) -> None:
        while self._on_unload:
            assert self._on_unload.pop()() is None
        for task in self.tasks.values():
            task.cancel()
        await asyncio.gather(*self.tasks.values(), return_exceptions=True)


def _loop_coord(strategy: str | None) -> Any:
    from custom_components.vag_connect.coordinator import VagConnectCoordinator

    coord = VagConnectCoordinator.__new__(VagConnectCoordinator)
    data: dict[str, Any] = {"brand": "volkswagen", "scan_interval": 60}
    if strategy:
        data[CONF_STRATEGY] = strategy
    coord.entry = _Entry(data)
    coord.hass = MagicMock()
    coord._started = True
    coord._cariad_client = MagicMock()
    coord.async_request_refresh = AsyncMock()
    return coord


class TestTasksCancelledOnUnload:
    @pytest.mark.asyncio
    async def test_companion_poll_loop_is_cancelled(self) -> None:
        coord = _loop_coord(STRATEGY_COMPANION_ADB)
        loop = asyncio.create_task(coord._poll_loop())
        await asyncio.sleep(0.01)  # into its interval sleep
        assert not loop.done()
        await coord.entry.unload()
        await asyncio.wait({loop}, timeout=1)
        assert loop.cancelled()

    @pytest.mark.asyncio
    async def test_other_strategies_keep_their_poll_loop_as_it_was(self) -> None:
        coord = _loop_coord(None)
        loop = asyncio.create_task(coord._poll_loop())
        await asyncio.sleep(0.01)
        assert coord.entry._on_unload == []
        loop.cancel()
        await asyncio.gather(loop, return_exceptions=True)

    @pytest.mark.asyncio
    async def test_sync_readback_is_cancelled(self) -> None:
        coord = _loop_coord(STRATEGY_COMPANION_ADB)
        coord.async_companion_sync_vehicle = AsyncMock(return_value=True)
        await coord.async_companion_force_refresh()
        readback = coord.entry.tasks[f"{DOMAIN}_app_sync_readback"]
        coord.hass.async_create_background_task.assert_not_called()
        await coord.entry.unload()
        assert readback.cancelled()
        coord.async_request_refresh.assert_not_awaited()

    def test_app_sync_loop_is_started_on_the_entry(self) -> None:
        from custom_components.vag_connect.cariad.api.factory import CariadClientFactory
        from custom_components.vag_connect.coordinator import VagConnectCoordinator

        coord = VagConnectCoordinator.__new__(VagConnectCoordinator)
        coord.hass = MagicMock()
        coord.hass.loop = MagicMock()
        coord.entry = MagicMock()
        coord.entry.entry_id = "test"
        coord.entry.data = {
            "brand": "volkswagen", "username": "", "password": "", "spin": "",
            "scan_interval": 5, CONF_STRATEGY: STRATEGY_COMPANION_ADB,
            "vin": VIN, "adb_host": "phone",
        }
        coord.entry.options = {}
        coord._vehicles_lock = threading.Lock()
        coord._cariad_client = None
        coord._started = False
        coord._was_available = True
        coord.vehicles = {}
        coord.data = None
        coord.async_set_updated_data = MagicMock()
        coord.async_request_refresh = AsyncMock()
        coord.logger = MagicMock()
        coord._poll_loop = MagicMock(return_value=None)
        coord._companion_app_sync_loop = MagicMock(return_value="app-sync-loop")
        coord.refresh_capabilities = AsyncMock()
        coord.refresh_static_info = AsyncMock()
        coord._refresh_mbb_command_capabilities = AsyncMock()
        coord._ensure_data_act_custom_request_kickoff = AsyncMock()
        client = MagicMock()
        client.authenticate = AsyncMock()
        client.get_vehicles = AsyncMock(return_value=[VIN])
        client.get_status = AsyncMock(return_value=VehicleData(vin=VIN))
        with patch.object(CariadClientFactory, "create", return_value=client), \
             patch(
                 "custom_components.vag_connect.companion.CompanionClient",
                 return_value=client,
             ), \
             patch(
                 "homeassistant.helpers.aiohttp_client.async_get_clientsession",
                 return_value=MagicMock(),
             ):
            assert asyncio.run(coord.async_setup()) is True
        finish = coord.hass.async_create_background_task.call_args.args[0]
        asyncio.run(finish)
        coord.entry.async_create_background_task.assert_called_once_with(
            coord.hass, "app-sync-loop", f"{DOMAIN}_app_sync"
        )
        hass_names = [
            c.args[1] for c in coord.hass.async_create_background_task.call_args_list
        ]
        assert f"{DOMAIN}_app_sync" not in hass_names
