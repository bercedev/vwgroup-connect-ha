# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""A supplementary channel that drops out leaves no trace in the diagnostics.

``gather_and_merge`` reads the read-only channels concurrently and merges them
onto the primary snapshot. A supplier that raises, or returns ``None``, is
skipped — which is right, because a read-only fallback failing must never sink
the whole poll. What is wrong is that the skip is invisible:

    _LOGGER.debug("supplementary channel %s failed, skipped: %s", ...)

DEBUG only. Not in ``probe_outcomes`` (those are per-read, not per-channel), not
in ``channel_status``, nowhere in the download. And the consequence is not merely
a missing line: when every supplier is skipped, ``len(sources) == 1`` returns the
primary verbatim, so the live-supersede rule — the thing that stops a stale batch
value outranking a live reading — never runs at all.

That is what made #1827 undecidable. The reporter shows a charge level of 19 owned
by the batch channel while his own raw vw.de body carries 35, and from the
download alone there is no way to tell whether the vw.de channel was skipped
entirely or was present and simply had no charge level that poll. Both produce the
same artefact. The one line that distinguishes them is a DEBUG log nobody has.

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

from custom_components.vag_connect.cariad._channel_merge import gather_and_merge
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


class TestTheCallerWiresItUp:
    """#584's lesson: a sink that nothing writes to passes its own test forever.
    The coordinator has to actually pass the client's probe_outcomes."""

    def test_the_coordinator_passes_an_outcomes_sink(self):
        import inspect

        from custom_components.vag_connect.coordinator import VagConnectCoordinator
        src = inspect.getsource(VagConnectCoordinator)
        call = src[src.index("gather_and_merge("):]
        assert "outcomes=" in call[:600]
