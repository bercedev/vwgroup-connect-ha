# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A supplementary channel that drops out does not say WHY in the diagnostics.

``gather_and_merge`` reads the read-only channels concurrently and merges them
onto the primary snapshot. A supplier that raises, or returns ``None``, is
skipped — which is right, because a read-only fallback failing must never sink
the whole poll. What is wrong is that the REASON for the skip is invisible:

    _LOGGER.debug("supplementary channel %s failed, skipped: %s", ...)

DEBUG only, and ``probe_outcomes`` held per-READ entries rather than per-channel
ones, so the skip was not there either. The consequence is not merely a missing
line: when every supplier is skipped, ``len(sources) == 1`` returns the primary
verbatim, so the live-supersede rule — the thing that stops a stale batch value
outranking a live reading — never runs at all.

What this docstring used to claim next, and what is NOT true: that the skip was
"not in ``channel_status``, nowhere in the download". The diagnostics attached to
#1827 contradicts that. The download does carry a per-channel block, and that block
IS dated to the poll the file was taken on: ``_compute_channel_status``
(coordinator.py:3525-3582) derives ``active`` / ``active_values`` / ``total_values``
from ``field_sources``, which the merge rebuilds from scratch every time
(``field_sources: dict[str, str] = {}``, _channel_merge.py:148). In that file the
vw.de channel reads ``armed: true, active: true, active_values: 4``, it owns four
named fields in ``field_sources``, and ``source_channel`` is
``eu_data_act+website_authproxy``. So "was the vw.de channel skipped entirely" was
in fact answerable from the download, and the answer there was no.

What ``channel_status`` genuinely cannot do is say WHY a channel owns nothing.
Three different events all render as ``active: false, active_values: 0``: the read
raised, the read returned ``None``, and the read returned a snapshot that won no
field because the primary already carried every one. Those have different causes
and different fixes, and collapsing them is the part of #1827 that stayed
undecidable — the one line that separated them was a DEBUG log nobody has.

This is the same shape as #1712, where the line identifying a login page existed
only at DEBUG and every report therefore cost a round of "please enable debug
logging first". Lifting it made the next reporter able to deliver the evidence
unprompted, and that is the whole value here too: the next person to hit this
answers the question by attaching a file.

Deliberately NOT done: raising the log level. A read-only channel failing is an
ordinary event on a flaky portal, so WARNING would be noise, and noise gets
filtered and then the signal is gone again. A structured outcome costs nothing per
poll and is already how this project surfaces this class of thing
(``_skoda_probe`` writes exactly such a label).

Only the exception TYPE is recorded, never its message: a message on this path can
carry a request URL, and on the authproxy path that URL has carried tokens.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock

from custom_components.vag_connect.cariad._channel_merge import gather_and_merge
from custom_components.vag_connect.cariad.api.vw_eu import VWEUClient
from custom_components.vag_connect.cariad.models import VehicleData

_VIN = "WVWZZZAUZFW000000"


def _vd(**kw: object) -> VehicleData:
    return VehicleData(vin=_VIN, **kw)  # type: ignore[arg-type]


async def _raises(exc: BaseException) -> VehicleData | None:
    raise exc


async def _returns(value: VehicleData | None) -> VehicleData | None:
    return value


class TestAFailedChannelIsRecorded:
    def test_an_exception_is_recorded_by_type(self):
        outcomes: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("website_authproxy", _raises(RuntimeError("boom")))],
            outcomes=outcomes,
        ))
        assert "channel:website_authproxy" in outcomes
        assert "RuntimeError" in outcomes["channel:website_authproxy"]

    def test_the_exception_message_is_not_recorded(self):
        """A message here can carry a request URL, and on the authproxy path such
        a URL has carried tokens. Type only."""
        outcomes: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("website_authproxy", _raises(RuntimeError("https://x/?token=SEKRET")))],
            outcomes=outcomes,
        ))
        assert "SEKRET" not in outcomes["channel:website_authproxy"]
        assert "https" not in outcomes["channel:website_authproxy"]

    def test_a_none_return_is_distinguishable_from_a_failure(self):
        """'the channel ran and had nothing' and 'the channel fell over' are the
        two cases #1827 could not tell apart. They must not share a label."""
        o_none: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(), [("website_authproxy", _returns(None))],
            outcomes=o_none,
        ))
        o_fail: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(), [("website_authproxy", _raises(RuntimeError()))],
            outcomes=o_fail,
        ))
        assert o_none["channel:website_authproxy"] != o_fail["channel:website_authproxy"]

    def test_a_channel_that_delivered_is_recorded_too(self):
        """Otherwise a missing key is ambiguous between 'fine' and 'never ran'."""
        outcomes: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("website_authproxy", _returns(_vd(battery_soc=35)))],
            outcomes=outcomes,
        ))
        assert outcomes.get("channel:website_authproxy") == "ok"

    def test_every_supplier_gets_its_own_key(self):
        outcomes: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "mbb", _vd(),
            [("eu_data_act", _returns(_vd(battery_soc=19))),
             ("website_authproxy", _raises(RuntimeError()))],
            outcomes=outcomes,
        ))
        assert outcomes.get("channel:eu_data_act") == "ok"
        assert "RuntimeError" in outcomes.get("channel:website_authproxy", "")


class TestTheMergeItselfIsUnchanged:
    """The fix must be observation only. These are the regression guards."""

    def test_the_supersede_still_happens_when_a_live_channel_has_the_value(self):
        merged = asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("website_authproxy", _returns(_vd(battery_soc=35)))],
            outcomes={},
        ))
        assert merged.battery_soc == 35

    def test_a_skipped_supplier_still_yields_the_primary_verbatim(self):
        primary = _vd(battery_soc=19)
        merged = asyncio.run(gather_and_merge(
            "eu_data_act", primary,
            [("website_authproxy", _raises(RuntimeError()))],
            outcomes={},
        ))
        assert merged is primary

    def test_it_works_without_an_outcomes_sink(self):
        """Backwards compatible: every existing caller passes none."""
        merged = asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("website_authproxy", _raises(RuntimeError()))],
        ))
        assert merged.battery_soc == 19

    def test_no_suppliers_is_still_a_short_circuit(self):
        primary = _vd(battery_soc=19)
        merged = asyncio.run(gather_and_merge("eu_data_act", primary, [], outcomes={}))
        assert merged is primary


class TestTheLabelSetDescribesThisPollOnly:
    """The labels must be exclusive, or they repeat the trap they close.

    ``probe_outcomes`` is never cleared — its two ``= {}`` sites are lazy-init
    guards for ``__new__``-built doubles followed by ``.update()``
    (api/base.py:620-624, api/porsche.py:486-487). So a cumulative label set
    would keep asserting ``ok`` for a channel that has since stopped being
    offered at all, which is the same "an entry here does not date a poll"
    mistake that made #1827 hard to read in the first place.
    """

    def test_a_channel_no_longer_offered_loses_its_label(self):
        sink: dict[str, str] = {}
        # poll 1 — both channels present, one delivers
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("website_authproxy", _returns(_vd(battery_soc=35))),
             ("mbb", _returns(None))],
            outcomes=sink,
        ))
        assert sink["channel:website_authproxy"] == "ok"
        assert sink["channel:mbb"] == "no_data"

        # poll 2 — vw.de was switched off, so it never reaches ``suppliers``
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(battery_soc=19),
            [("mbb", _returns(None))],
            outcomes=sink,
        ))
        assert "channel:website_authproxy" not in sink, (
            "a channel that is not being attempted must not still claim an outcome"
        )
        assert sink["channel:mbb"] == "no_data"

    def test_a_label_that_changed_is_replaced_not_accumulated(self):
        sink: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(),
            [("website_authproxy", _returns(_vd(battery_soc=35)))],
            outcomes=sink,
        ))
        assert sink["channel:website_authproxy"] == "ok"
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(),
            [("website_authproxy", _raises(TimeoutError("x")))],
            outcomes=sink,
        ))
        assert sink["channel:website_authproxy"] == "failed:TimeoutError"
        assert len([k for k in sink if k.startswith("channel:")]) == 1

    def test_the_per_read_entries_beside_it_are_left_alone(self):
        """Only the ``channel:`` namespace is this function's to manage; the
        per-read outcomes belong to the connector and are merged up elsewhere."""
        sink = {"vwde_charging": "200", "vwde_core_read:relations": "401"}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(),
            [("website_authproxy", _returns(_vd(battery_soc=35)))],
            outcomes=sink,
        ))
        assert sink["vwde_charging"] == "200"
        assert sink["vwde_core_read:relations"] == "401"
        assert sink["channel:website_authproxy"] == "ok"

    def test_the_sweep_runs_even_when_every_supplier_is_skipped(self):
        """The clearing sits before the loop and the loop before the
        ``len(sources) == 1`` short-circuit, so the all-skipped poll — the case
        that produced the reporter's download — still ends up with an accurate
        set rather than the previous poll's."""
        sink: dict[str, str] = {}
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(),
            [("website_authproxy", _returns(_vd(battery_soc=35)))],
            outcomes=sink,
        ))
        assert sink["channel:website_authproxy"] == "ok"
        asyncio.run(gather_and_merge(
            "eu_data_act", _vd(),
            [("website_authproxy", _returns(None))],
            outcomes=sink,
        ))
        assert sink["channel:website_authproxy"] == "no_data"


def _coord_stub(client):
    """The attributes ``_merge_supplementary`` actually reaches for."""
    from custom_components.vag_connect.coordinator import VagConnectCoordinator
    stub = type("S", (), {})()
    stub._cariad_client = client
    stub.entry = MagicMock()
    stub.entry.data = {}
    stub.entry.options = {}
    stub._primary_channel_name = (
        VagConnectCoordinator._primary_channel_name.__get__(stub)
    )
    stub._read_priority_channel = (
        VagConnectCoordinator._read_priority_channel.__get__(stub)
    )
    return stub


class TestTheCallerWiresItUp:
    """#584's lesson: a sink that nothing writes to passes its own test forever.

    This used to be a grep of ``inspect.getsource(VagConnectCoordinator)`` for the
    string ``"outcomes="``, which does not test the wiring at all: it passes just as
    happily on ``outcomes=None`` as on the real sink, so the one failure mode it was
    written to catch is the one it cannot see. It now drives
    ``_merge_supplementary`` and reads the label off the CLIENT's own dict, which is
    what diagnostics exports.
    """

    def test_the_merge_writes_the_label_into_the_clients_own_sink(self):
        from custom_components.vag_connect.coordinator import VagConnectCoordinator
        client = MagicMock()
        client.probe_outcomes = {}
        client.supplementary_readers = MagicMock(
            return_value=[("website_authproxy", _raises(TimeoutError("x")))]
        )
        out = asyncio.run(VagConnectCoordinator._merge_supplementary(
            _coord_stub(client), _VIN, _vd(battery_soc=19),
        ))
        # observation only — the primary still comes back untouched
        assert out.battery_soc == 19
        assert client.probe_outcomes["channel:website_authproxy"] == (
            "failed:TimeoutError"
        ), (
            "the coordinator must hand gather_and_merge the client's real sink; "
            "passing None, or a throwaway dict, loses the label before export"
        )


class TestTheSinkSurvivesTheVwdeSoleModePath:
    """The labels and the sink they live in must have ONE lifetime.

    ``get_status`` on the volkswagen.de sole-mode path used to REBIND the client's
    sink to a fresh copy of the proxy's (``self.probe_outcomes = dict(getattr(web,
    "probe_outcomes", {}))``). The proxy's own dict never holds a ``channel:`` key —
    only ``_record`` writes those — so every label the previous poll's merge had
    written was dropped at the start of the next poll, and anything else the CLIENT
    had accumulated went with it. That rebind was the only unconditional one in the
    tree: every other path creates the sink once (api/base.py:287) and mutates it in
    place, and the two other ``= {}`` sites are guarded lazy-inits for
    ``__new__``-built doubles followed by ``.update()`` (api/base.py:620-624,
    api/porsche.py:486-487) — not resets.
    """

    @staticmethod
    def _sole_mode_client(proxy_outcomes=None):
        client = VWEUClient(MagicMock(), "u@t.de", "pw")
        proxy = MagicMock()
        proxy.maybe_roll = AsyncMock()
        proxy.get_vehicle_data = AsyncMock(return_value=_vd(battery_soc=35))
        proxy.last_raw_responses = {}
        proxy.probe_outcomes = dict(proxy_outcomes or {})
        client._website_proxy = proxy
        return client

    def test_get_status_merges_proxy_outcomes_rather_than_replacing_the_sink(self):
        client = self._sole_mode_client({"vwde_charging": "200"})
        sink = client.probe_outcomes
        sink["channel:website_authproxy"] = "ok"
        sink["channel:mbb"] = "no_data"
        asyncio.run(client.get_status(_VIN))
        assert client.probe_outcomes is sink, (
            "the sink must be mutated in place, not rebound — base.py:624 is the "
            "contract every other path already keeps"
        )
        assert sink["channel:website_authproxy"] == "ok"
        assert sink["channel:mbb"] == "no_data"
        assert sink["vwde_charging"] == "200", (
            "the proxy's per-read entries still have to be surfaced — that is what "
            "the rebind was there for (#923/#1157)"
        )

    def test_a_label_written_by_the_merge_outlives_the_next_get_status(self):
        """The real sequence, and the window that made this matter: the poll loop
        runs every ``get_status`` first and only then merges per VIN
        (coordinator.py:4620 before :4777; :4436 before :4465 at setup). So between
        the rebind and the merge that refills the sink lie the supplementary
        channels' network reads for every car — and a diagnostics download taken in
        that window carried no channel lines at all, which under this scheme reads
        as "not attempted" for channels that were being attempted right then."""
        client = self._sole_mode_client({"vwde_charging": "200"})
        # poll 1 — get_status, then the merge writes this poll's labels
        asyncio.run(client.get_status(_VIN))
        asyncio.run(gather_and_merge(
            "website_authproxy", _vd(battery_soc=35),
            [("mbb", _returns(None))],
            outcomes=client.probe_outcomes,
        ))
        assert client.probe_outcomes["channel:mbb"] == "no_data"
        # poll 2 — get_status has run, the merge has not yet
        asyncio.run(client.get_status(_VIN))
        assert client.probe_outcomes.get("channel:mbb") == "no_data", (
            "get_status must not be able to drop a label the merge wrote"
        )

    def test_a_new_built_client_without_init_still_works(self):
        """``.update()`` needs the attribute to exist, and this client is built
        via ``__new__`` without ``__init__`` all over this suite (vw_eu.py:430
        documents the pattern). The old rebind created the attribute on the way
        past; the in-place version needs base.py:622's guard to do the same, or
        the fix would trade a lost label for an AttributeError."""
        client = VWEUClient.__new__(VWEUClient)
        proxy = MagicMock()
        proxy.maybe_roll = AsyncMock()
        proxy.get_vehicle_data = AsyncMock(return_value=_vd(battery_soc=35))
        proxy.last_raw_responses = {}
        proxy.probe_outcomes = {"vwde_charging": "200"}
        client._website_proxy = proxy
        assert not hasattr(client, "probe_outcomes")
        out = asyncio.run(client.get_status(_VIN))
        assert out.battery_soc == 35
        assert client.probe_outcomes == {"vwde_charging": "200"}
