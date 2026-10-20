# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""One unmappable field, three people asked to file it.

``eu_data_act.jobStatus`` raised the user-facing Scout repair three times — on
volkswagen (#1831, @Dirk-fs), on an ID.7 Tourer GTX (#1838) and on a CUPRA
Tavascan (#1861, @sssfamilientermine-wq). Three reporters were prompted to
hand-file the same already-known field.

Grounded before suppressing anything: the shipped catalogue has **zero** names
containing "jobstatus", "job_status" or "job space status" (control: "mileage"
matches, so the search works), nothing maps or allowlists it, and
``lookup("eu_data_act.jobStatus")`` is None under both the shipped and the
#1899 lookup. It is genuinely unmapped rather than a victim of a broken lookup.

It is **repair**-skipped and deliberately not added to the envelope-noise set,
which would drop it from ``raw_unmapped_fields`` altogether. The samples are
``JOBSTATUS_FLASHING`` and ``JOBSTATUS_DONE``, and those read two ways: a
portal export-job envelope status (plumbing) or a software-flash job actually
running on the car (a real signal worth an entity). Three samples cannot settle
that, and the two readings call for opposite actions — so stop the nagging and
keep the data, which leaves the question answerable.

Two defects turned up while doing it.

**The skip set was matched case-sensitively.** Every entry in it is lower case
and the portal spells this field ``jobStatus``, so adding it as ``jobstatus``
would have skipped nothing while the set *looked* like it covered the field.
The envelope-noise check in ``_eu_data_act.py`` already lowercases its leaf;
this one did not.

**And deleting the card threw away the user's "ignore".** ``findings`` being
empty and every finding being repair-skipped were one branch, and both deleted
the repair. HA's ``async_delete_issue`` pops the whole registry entry, and a
dismissal lives on that entry as ``dismissed_version`` — so a field that keeps
reappearing kept raising a fresh, un-ignored card at someone who had already
said no. A refresh is safe; only the delete is not. The two cases are separate
now: nothing found deletes, all-skipped leaves the card alone.
"""
from __future__ import annotations

import dataclasses
import inspect
from typing import Any

import pytest

from custom_components.vag_connect.cariad import _reporter_pipeline as rp
from custom_components.vag_connect.cariad._unexpected_keys import UnexpectedField


def _f(path: str, sample: str = "") -> UnexpectedField:
    return UnexpectedField(
        path=path,
        sample_masked=sample,
        endpoint="eu_data_act",
        first_seen_at="2026-10-20T12:00:00+00:00",
    )


# ── the field stops nagging, in either spelling ────────────────────────────


@pytest.mark.parametrize("path", [
    "eu_data_act.jobStatus",
    "eu_data_act.jobstatus",
    "eu_data_act.JOBSTATUS",
])
def test_jobstatus_does_not_raise_the_repair(path: str) -> None:
    assert rp._is_scout_repair_skipped(_f(path, "JOBSTATUS_FLASHING")) is True


def test_the_skip_set_is_matched_case_insensitively() -> None:
    """The defect that would have made the fix a no-op.

    Pinned separately from the parametrised case above, because a
    case-sensitive match plus a lower-case entry fails silently: nothing is
    skipped and the set reads as though the field were handled.
    """
    assert rp._is_scout_repair_skipped(_f("eu_data_act.IS_SET")) is True
    assert rp._is_scout_repair_skipped(_f("eu_data_act.Scope_Potential_Total")) is True


# ── and is not data-suppressed ─────────────────────────────────────────────


def test_jobstatus_is_not_removed_from_the_data() -> None:
    """Repair suppression only. The field must stay in raw_unmapped_fields so
    the FLASHING-vs-export-job question can still be answered from a user's
    diagnostics; dropping it would close the question by deletion."""
    from custom_components.vag_connect.cariad.auth import _eu_data_act as eda

    noise = {leaf.lower() for leaf in eda._ENVELOPE_NOISE_LEAVES}
    assert "jobstatus" not in noise, (
        "jobStatus is in the envelope-noise set, which removes it from "
        "raw_unmapped_fields entirely - that is data suppression, not repair "
        "suppression, and the Scout policy is map, never suppress"
    )


def test_the_other_unmapped_field_still_raises() -> None:
    """``eu_data_act.trigger`` (#1779) resolves to nothing either, but it is a
    live mapping question rather than known-unmappable, so it must keep
    raising. A skip list that quietly grew to cover it would end discovery."""
    assert rp._is_scout_repair_skipped(_f("eu_data_act.trigger", "TRIGGER_IMMEDIATELY")) is False


def test_leaf_matching_does_not_over_reach() -> None:
    """The existing guarantee in the skip-set comment: a meaningful field whose
    name merely contains a skipped word stays visible."""
    for path in (
        "eu_data_act.ocpp_transaction_id",
        "eu_data_act.hvbattery_soc",
        "eu_data_act.jobStatusDetail",
    ):
        assert rp._is_scout_repair_skipped(_f(path)) is False, path


# ── nothing-found and all-skipped are not the same thing ───────────────────


class _Recorder:
    """Stands in for homeassistant.helpers.issue_registry."""

    #: The production call passes ``severity=ir.IssueSeverity.WARNING``, so the
    #: stand-in has to carry the enum as well as the two functions.
    IssueSeverity = __import__(
        "homeassistant.helpers.issue_registry", fromlist=["IssueSeverity"]
    ).IssueSeverity

    def __init__(self) -> None:
        self.calls: list[str] = []

    def async_create_issue(self, *_a: Any, **_k: Any) -> None:
        self.calls.append("create")

    def async_delete_issue(self, *_a: Any, **_k: Any) -> None:
        self.calls.append("delete")


def _drive(findings: list[UnexpectedField]) -> list[str]:
    rec = _Recorder()
    original = rp.ir
    rp.ir = rec  # type: ignore[assignment]
    try:
        rp.ensure_unexpected_keys_issue(
            object(),  # type: ignore[arg-type]
            entry_id="e1",
            findings=findings,
            brand="volkswagen",
            integration_version="5.1.0",
        )
    finally:
        rp.ir = original
    return rec.calls


def test_no_findings_at_all_deletes_the_stale_card() -> None:
    """Unchanged behaviour: the surface stabilised, the card is stale."""
    assert _drive([]) == ["delete"]


def test_only_skipped_findings_neither_raise_nor_delete() -> None:
    """The fix. Deleting here would discard the user's dismissal."""
    assert _drive([_f("eu_data_act.jobStatus", "JOBSTATUS_DONE")]) == []


def test_a_real_finding_still_raises_the_card() -> None:
    assert _drive([_f("eu_data_act.something_new", "1")]) == ["create"]


def test_a_mix_raises_and_reports_only_the_unskipped_ones() -> None:
    assert _drive([
        _f("eu_data_act.jobStatus", "JOBSTATUS_DONE"),
        _f("eu_data_act.something_new", "1"),
    ]) == ["create"]


def test_a_one_pass_iterable_is_not_consumed_twice() -> None:
    """The two branches need the findings twice and the signature accepts an
    ``Iterable``, so they have to be materialised first.

    The discriminating case is an **empty** generator. A generator object is
    truthy whether or not it has anything in it, so without the ``list()`` the
    ``if all_findings:`` guard reads an exhausted generator as "findings
    exist", takes the leave-it-alone branch, and the stale card is never
    deleted. My first version of this test passed a generator holding a skipped
    finding, where the bug happens to give the same answer — the mutation
    check is what showed it proved nothing.
    """
    empty: Any = (f for f in [])
    assert _drive(empty) == ["delete"], (
        "an exhausted generator was read as 'findings exist', so the stale "
        "card was left in place"
    )
    only_skipped: Any = (f for f in [_f("eu_data_act.jobStatus", "x")])
    assert _drive(only_skipped) == []
    real: Any = (f for f in [_f("eu_data_act.something_new", "1")])
    assert _drive(real) == ["create"]


# ── the premise: why deleting is the unsafe one ────────────────────────────


def test_ha_delete_drops_the_dismissal_but_a_refresh_does_not() -> None:
    """Pins the Home Assistant behaviour the fix is built on.

    Read from the installed HA rather than assumed: ``async_delete`` pops the
    registry entry, so the ``dismissed_version`` that records the user's
    "ignore" goes with it, while ``async_get_or_create`` replaces the entry
    through ``dataclasses.replace`` without naming that field — so a refresh
    keeps it. If a future HA changes either, this test fails before anyone
    wonders why the dismissal came back.
    """
    import homeassistant.helpers.issue_registry as ha_ir

    delete_src = inspect.getsource(ha_ir.IssueRegistry.async_delete)
    assert "self.issues.pop(" in delete_src, (
        "HA no longer removes the whole entry on delete; re-check whether "
        "deleting still discards the dismissal"
    )

    create_src = inspect.getsource(ha_ir.IssueRegistry.async_get_or_create)
    # Only the REPLACE branch matters. The new-entry branch sets
    # dismissed_version=None, which is correct — a brand-new issue has no
    # dismissal — and my first version of this assertion searched the whole
    # function, found that line, and declared the premise broken. Read from the
    # syntax tree rather than by slicing text, which is also how the second
    # version broke: HA's closing paren is not where I guessed.
    import ast
    import textwrap

    tree = ast.parse(textwrap.dedent(create_src))
    replace_kwargs: set[str] = set()
    entry_kwargs: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = ast.unparse(node.func)
        if name.endswith("dataclasses.replace") or name == "replace":
            replace_kwargs |= {k.arg for k in node.keywords if k.arg}
        elif name == "IssueEntry":
            entry_kwargs |= {k.arg for k in node.keywords if k.arg}

    assert replace_kwargs, "no dataclasses.replace call found in get_or_create"
    assert "dismissed_version" not in replace_kwargs, (
        "HA's get_or_create now rewrites dismissed_version when refreshing an "
        "existing issue; a refresh may no longer preserve the user's ignore"
    )
    # ...and the new-entry branch does zero it, which is exactly why
    # delete-then-recreate loses the dismissal while a plain refresh does not.
    assert "dismissed_version" in entry_kwargs
    # and the field really is where the dismissal lives
    assert "dismissed_version" in {
        fld.name for fld in dataclasses.fields(ha_ir.IssueEntry)
    }
    assert "dismissed_version" in inspect.getsource(ha_ir.IssueRegistry.async_ignore)
