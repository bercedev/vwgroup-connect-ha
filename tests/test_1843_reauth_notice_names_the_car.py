# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1843 — two cars, two accounts, two identical notices and no way to tell them apart.

@kalwados runs two vehicles on two different e-mail addresses. When the
volkswagen.de read channel needs a re-login he gets the notice twice, and
neither copy said which car it was about, so he could not tell which password
belonged to which. He asked for the last six characters of the VIN.

The gap was already admitted in the code: raise_issue_supplementary_reauth's own
docstring says what the Fix button saves is "the six steps between seeing this
notice and reaching the login form, including having to guess WHICH entry it
refers to when several are configured". The button removed the walk to Settings.
It did not remove the guess.

Six characters and not the whole VIN, via the same mask_vin this project already
uses for logs and diagnostics: a full VIN ties to registration and ownership
records and does not belong in text a user may screenshot into an issue.
"""
from __future__ import annotations

from unittest.mock import patch

from custom_components.vag_connect.repairs import raise_issue_supplementary_reauth


def _captured(**kwargs) -> dict:
    """Call the raiser and return the kwargs Home Assistant was handed."""
    with patch(
        "custom_components.vag_connect.repairs.ir.async_create_issue"
    ) as created:
        raise_issue_supplementary_reauth(object(), "entry-1", **kwargs)  # type: ignore[arg-type]
    assert created.call_count == 1
    return created.call_args.kwargs


def test_the_notice_carries_the_car_it_is_about() -> None:
    """RED before this change: there was no translation_placeholders at all."""
    kwargs = _captured(car="***000001")
    assert kwargs["translation_placeholders"] == {"car": "***000001"}


def test_the_placeholder_is_never_empty() -> None:
    """Home Assistant raises when a declared placeholder is missing, so a caller
    that cannot name a car must still produce something renderable."""
    assert _captured()["translation_placeholders"]["car"] == "this entry"
    assert _captured(car="")["translation_placeholders"]["car"] == "this entry"


def test_the_title_in_every_language_has_somewhere_to_put_it() -> None:
    """A placeholder present in one language and absent in another renders the
    raw key for everybody else, which is the defect this repository's own
    parity test exists to catch. Pinned here too, because this is the change
    that introduced the placeholder."""
    import glob
    import io
    import json

    paths = ["custom_components/vag_connect/strings.json"] + sorted(
        glob.glob("custom_components/vag_connect/translations/*.json")
    )
    assert len(paths) >= 12, "expected strings.json plus the shipped languages"
    for p in paths:
        d = json.loads(io.open(p, encoding="utf-8").read())
        title = d["issues"]["supplementary_reauth"]["title"]
        assert "{car}" in title, f"{p} cannot render the car identifier"


def test_the_full_vin_is_not_what_gets_shown() -> None:
    """The guard against the obvious wrong fix. Asserted on mask_vin's output
    rather than on a hand-written string, so it still holds if the masking
    convention changes."""
    from custom_components.vag_connect.cariad._util import mask_vin

    vin = "WVWZZZ000M0000001"
    shown = mask_vin(vin)
    assert vin not in shown
    assert shown.endswith(vin[-6:])
    assert len(shown.replace("*", "")) == 6
