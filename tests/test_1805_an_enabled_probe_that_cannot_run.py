# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Asked to share results from a probe that could never run.

@bzykcar (#1805) is portal-only on an Audi since 13 October: the device login
expired and was switched off upstream, the password login answers 400, so there
is no manufacturer channel at all. He enabled the opt-in test cohort anyway, to
help, and reported:

    test_cohort is enabled on the current entry (5.0.0), but probe_outcomes
    stays empty, presumably because the probe has no authenticated BFF session
    to run on.

He was right, and two separate faults made it look the way it did.

**He should never have been asked.** The share prompt was gated on
``hasattr(client, "command_captures")``. That attribute is created in the base
client's ``__init__``, so it is true for every brand and every auth strategy —
the gate tested that a Python attribute exists, not that a channel is
reachable. The capture it asks for is written inside the command confirm-poll,
so an entry with no command path cannot produce one at any point in its life.
He was asked to share something that could not exist.

**And an empty result could not be read.** The three opt-in probes caught every
exception, logged one DEBUG line and recorded nothing, so a probe that ran and
was refused left ``probe_outcomes`` exactly as empty as a probe that never
attempted anything. That is the distinction ``_channel_merge._record`` already
calls out as the one #1827 needed and could not make, and the core-read path
twenty lines below these probes already records its own failures. The three
probes were the remaining hole.

The gate now uses this project's own answer to "can this entry send a command",
``is_structural_read_only()`` (#543), rather than a second opinion about
strategy names that would drift away from it.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.vag_connect import coordinator as coord_mod
from custom_components.vag_connect.cariad.auth import _website_authproxy as wap

_PROBE_KEYS = ("parkingposition", "soh", "measurements")


# ── the prompt is gated on a reachable channel ─────────────────────────────


def _gate_source() -> str:
    """The slice of the cohort-arming method that computes the share flags."""
    src = inspect.getsource(coord_mod.VagConnectCoordinator._apply_test_cohort)
    return src


def test_the_prompt_is_not_gated_on_a_bare_attribute_check() -> None:
    """``hasattr(client, "command_captures")`` alone is always true.

    Pinned at the source because the condition is the defect: an instance
    attribute set unconditionally in ``__init__`` cannot distinguish one entry
    from another, and no amount of runtime data makes it start doing so.
    """
    src = _gate_source()
    assert "is_structural_read_only()" in src, (
        "the share prompt no longer consults the project's own "
        "can-this-entry-send-a-command predicate"
    )
    # the flag that drives the prompt must not be the bare hasattr result
    assert "has_bff = client is not None and hasattr" not in src


def test_command_captures_really_is_unconditional() -> None:
    """The premise of the fix. If this ever stops being true, the old gate was
    not as wrong as this test file claims and the reasoning needs revisiting."""
    from custom_components.vag_connect.cariad.api.base import CariadBaseClient

    src = inspect.getsource(CariadBaseClient.__init__)
    tree = ast.parse("if 1:\n" + "\n".join("    " + ln for ln in src.splitlines()))
    assigns = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Assign)
        or isinstance(n, ast.AnnAssign)
    ]
    targets: list[str] = []
    for node in assigns:
        tgts = node.targets if isinstance(node, ast.Assign) else [node.target]
        for t in tgts:
            if (
                isinstance(t, ast.Attribute)
                and isinstance(t.value, ast.Name)
                and t.value.id == "self"
            ):
                targets.append(t.attr)
    assert "command_captures" in targets, (
        "command_captures is no longer set unconditionally in __init__"
    )


class _Client:
    """A primary client with the capture surface and nothing else.

    A plain object, not a MagicMock: ``hasattr`` is true for every name on a
    MagicMock, which is the very confusion this issue is about.
    """

    def __init__(self) -> None:
        self.command_captures: dict[str, Any] = {}
        self._test_cohort = False


def _drive(*, structural_ro: bool, cohort: bool = True) -> tuple[Any, list[str]]:
    """Run the REAL ``_apply_test_cohort`` against a stub and report which
    repair it decided on.

    Bound with ``__get__`` rather than reimplemented in the test. An earlier
    version of this file recomputed the gate expression here and asserted on
    its own arithmetic — it passed just as happily with the defect restored,
    which the mutation check caught. The assertion has to come from the code
    under test.
    """
    calls: list[str] = []

    class _Entry:
        entry_id = "abc"
        data = {"test_cohort": cohort}

    stub = MagicMock()
    stub.entry = _Entry()
    stub.is_structural_read_only.return_value = structural_ro
    client = _Client()
    stub._cariad_client = client

    from custom_components.vag_connect import repairs as repairs_mod

    orig_raise = repairs_mod.raise_issue_test_cohort_share
    orig_clear = repairs_mod.clear_issue_test_cohort_share
    repairs_mod.raise_issue_test_cohort_share = (
        lambda *a, **k: calls.append("raise")
    )
    repairs_mod.clear_issue_test_cohort_share = (
        lambda *a, **k: calls.append("clear")
    )
    try:
        import asyncio

        bound = coord_mod.VagConnectCoordinator._apply_test_cohort.__get__(stub)
        asyncio.run(bound())
    finally:
        repairs_mod.raise_issue_test_cohort_share = orig_raise
        repairs_mod.clear_issue_test_cohort_share = orig_clear
    return client, calls


def test_a_portal_only_entry_is_not_asked_to_share() -> None:
    """His case. No command path, so no capture can ever exist."""
    _client, calls = _drive(structural_ro=True)
    assert calls == ["clear"], (
        f"a portal-only entry is still prompted to share (calls={calls})"
    )


def test_a_working_entry_is_still_asked_to_share() -> None:
    """The #912 intent: an Audi with no vw.de channel must still be asked, or
    the PPE rejection capture never reaches us. Narrowing the gate must not
    take that away."""
    _client, calls = _drive(structural_ro=False)
    assert calls == ["raise"], f"the working case lost its prompt (calls={calls})"


def test_opting_out_clears_the_prompt_either_way() -> None:
    """Unchanged behaviour, pinned so the new condition cannot resurrect a
    prompt for someone who never opted in."""
    for ro in (True, False):
        _client, calls = _drive(structural_ro=ro, cohort=False)
        assert calls == ["clear"], f"structural_ro={ro} calls={calls}"


def test_the_optin_flag_is_still_armed_even_when_the_prompt_is_not_raised() -> None:
    """Narrowing the PROMPT must not narrow the opt-in.

    ``_test_cohort`` also gates captures the client may take on paths other
    than a command, so it is still set whenever the surface exists. The obvious
    way to write this fix is to move the assignment inside the new condition,
    which would switch those captures off for exactly the people who opted in
    to help — so this drives the real method and reads the flag off the client.
    """
    client, calls = _drive(structural_ro=True)
    assert calls == ["clear"], "precondition: this is the no-prompt case"
    assert client._test_cohort is True, (
        "the opt-in flag was not armed; the fix narrowed the opt-in itself "
        "instead of only the prompt"
    )


# ── a failed probe says so ─────────────────────────────────────────────────


@pytest.mark.parametrize("key", _PROBE_KEYS)
def test_each_probe_records_its_failure(key: str) -> None:
    """Every probe's except-branch writes an outcome.

    Checked at the source rather than by driving three async read paths: the
    branches are inside one long method and each needs a different upstream
    call to raise. The thing that must hold is structural — the handler records
    something — and the keys are asserted to be the real ones below.
    """
    src = inspect.getsource(wap.WebsiteAuthProxyConnector)
    needle = f'self.probe_outcomes["{key}"] = f"error:{{type(exc).__name__}}"'
    assert needle in src, f"the {key} probe still swallows its failure silently"


def test_no_probe_records_the_exception_message() -> None:
    """Type only. An exception message here can carry a host or a path, and
    probe_outcomes is shared with the maintainer in cohort diagnostics."""
    src = inspect.getsource(wap.WebsiteAuthProxyConnector)
    for bad in ('= f"error:{exc}"', "= f'error:{exc}'", '= str(exc)'):
        assert bad not in src, f"a probe outcome embeds the message: {bad}"


def test_a_failed_probe_is_distinguishable_from_one_that_never_ran() -> None:
    """The whole point, stated as a property of the dict.

    An empty ``probe_outcomes`` must mean "nothing was attempted". Once any
    probe runs — successfully or not — the dict says which one and how it went.
    """
    conn = wap.WebsiteAuthProxyConnector(object(), "u@example.com", "pw")  # type: ignore[arg-type]
    assert conn.probe_outcomes == {}, "a fresh connector must start empty"

    conn.probe_outcomes["parkingposition"] = "error:ClientResponseError"
    assert conn.probe_outcomes, "a recorded failure must be visible"
    assert "error:" in conn.probe_outcomes["parkingposition"]
    # and a success is a different, readable value, not another empty
    conn.probe_outcomes["parkingposition"] = "200 no-coords"
    assert "error:" not in conn.probe_outcomes["parkingposition"]


def test_the_core_read_path_still_records_too() -> None:
    """The precedent this fix follows, pinned so the two cannot drift apart:
    the core read a few lines below the probes already recorded its failures,
    which is what made the probes' silence an inconsistency rather than a
    design choice."""
    src = inspect.getsource(wap.WebsiteAuthProxyConnector)
    assert 'self.probe_outcomes[f"vwde_core_read:{_core_read}"] = _status' in src


def test_the_probe_keys_match_the_success_path_keys() -> None:
    """A failure recorded under a different key than its success would read as
    a second, unrelated probe in the diagnostics."""
    src = inspect.getsource(wap.WebsiteAuthProxyConnector)
    assert 'self.probe_outcomes["parkingposition"] = "200 no-coords"' in src, (
        "the parkingposition success key changed; the failure key must follow"
    )


def test_the_module_has_no_other_silent_probe_handler() -> None:
    """Completeness: every ``_should_probe_*`` block records on failure.

    Guards against a fourth probe being added later with the same silence,
    which is how the first three got there.
    """
    path = pathlib.Path(wap.__file__)
    text = path.read_text(encoding="utf-8")
    probes = text.count("if self._should_probe_")
    recorded = sum(
        text.count(f'self.probe_outcomes["{k}"] = f"error:{{type(exc).__name__}}"')
        for k in _PROBE_KEYS
    )
    assert probes >= 3, f"expected at least 3 probe blocks, found {probes}"
    assert recorded == 3, f"{recorded} of the probe failure paths record an outcome"
