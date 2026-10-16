# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The parking map URLs go into the diagnostics download unredacted.

``parking_map_url_dark`` and ``parking_map_url_light`` hold, in the words of the
comment where they are populated, "Google-Maps-style URLs (dark + light theme) of
the parking location". Every sibling of theirs is redacted: ``parking_address``,
``parking_city``, and ``latitude`` / ``longitude`` each have their own branch in
``_scrub``. These two have nothing.

Why the existing string-value masking does not save them, which is the part worth
pinning down rather than assuming:

``_scrub`` does reach into string VALUES, and it masks three things there — an
email, a VIN, and a GPS pair in a query string. The third is the near miss. Its
helper ``_mask_location_qs`` begins ``if "latitude=" not in value: return value``,
because it was written in v1.15.0 for a specific leak: an error trace carrying a
``/v1/maps/positions?latitude=...&longitude=...`` URL. A static map URL does not
spell its coordinates that way. It writes ``center=`` or ``markers=`` or an
``@lat,lon,zoom`` path segment, so the guard returns the string untouched.

What this test does NOT claim: that these URLs definitely contain coordinates. No
real sample exists in the repo's fixtures — they are all the literal string
"https://...". The argument for redacting is the one the field's own comment
makes: it is a map of where the car is parked. If it encodes that position as an
opaque token instead of as numbers, redacting costs nothing; if it encodes it as
numbers, not redacting costs the reporter their parking location in a public
issue. That asymmetry is the whole case, and it is the same one
``vehicle_nickname`` and ``parking_city`` were each added on.
"""
from __future__ import annotations

import json

import pytest

from custom_components.vag_connect.diagnostics import _REDACT_KEYS, _scrub

_DARK = "parking_map_url_dark"
_LIGHT = "parking_map_url_light"

# Deliberately not a real place: the same 48.0/11.0 pair CONTRIBUTING.md uses in
# its fixture redaction template.
_MAPS_URL = "https://maps.example/staticmap?center=48.0,11.0&zoom=15&size=600x300"


class TestTheKeysAreRedacted:
    def test_dark_is_in_the_redact_list(self):
        assert _DARK in _REDACT_KEYS

    def test_light_is_in_the_redact_list(self):
        assert _LIGHT in _REDACT_KEYS

    @pytest.mark.parametrize("key", [_DARK, _LIGHT])
    def test_the_url_does_not_survive_a_scrub(self, key):
        out = _scrub({key: _MAPS_URL}, gps_round=False)
        blob = json.dumps(out)
        assert "48.0" not in blob
        assert "11.0" not in blob
        assert out[key] == "**REDACTED**"

    @pytest.mark.parametrize("key", [_DARK, _LIGHT])
    def test_it_is_redacted_even_in_gps_round_mode(self, key):
        """Rounding is an opt-in for COORDINATE fields. A URL cannot be rounded,
        so the opt-in must not become a way to publish it whole."""
        out = _scrub({key: _MAPS_URL}, gps_round=True)
        assert out[key] == "**REDACTED**"

    @pytest.mark.parametrize("key", [_DARK, _LIGHT])
    def test_an_empty_value_stays_visibly_empty(self, key):
        """#923 (@naked-head) — masking an empty field makes "no position" look
        the same as "position hidden", which misleads triage. The redaction
        branch already honours this; these keys must inherit it rather than
        getting a blanket mask of their own."""
        for empty in (None, ""):
            out = _scrub({key: empty}, gps_round=False)
            assert out[key] == empty


class TestWhyTheStringMaskingDoesNotCover:
    """Pins the near miss, so a future reader does not remove the key entries
    believing the string branch already handles it."""

    def test_the_query_string_masker_ignores_a_static_map_url(self):
        from custom_components.vag_connect.diagnostics import _mask_location_qs
        assert _mask_location_qs(_MAPS_URL, gps_round=False) == _MAPS_URL

    def test_it_does_still_catch_the_url_shape_it_was_written_for(self):
        """Counter-check: the masker is not simply broken."""
        from custom_components.vag_connect.diagnostics import _mask_location_qs
        url = "https://x/v1/maps/positions?latitude=48.0&longitude=11.0"
        assert _mask_location_qs(url, gps_round=False) != url
        assert "48.0" not in _mask_location_qs(url, gps_round=False)

    def test_a_bare_scrub_of_such_a_url_under_a_neutral_key_still_leaks(self):
        """The honest limit of this fix: it is key-based. The same URL under a
        key nobody listed still goes out whole. Documented rather than fixed,
        because widening the string masker to every 'lat,lon'-looking pair would
        also eat version numbers and image dimensions."""
        out = _scrub({"some_unlisted_key": _MAPS_URL}, gps_round=False)
        assert out["some_unlisted_key"] == _MAPS_URL


class TestTheSiblingsStayRedacted:
    """Regression: the fields this one is modelled on."""

    @pytest.mark.parametrize("key", ["parking_address", "parking_city",
                                     "vehicle_nickname"])
    def test_sibling_still_redacted(self, key):
        assert key in _REDACT_KEYS
        out = _scrub({key: "something"}, gps_round=False)
        assert out[key] == "**REDACTED**"

    def test_coordinates_still_redacted(self):
        out = _scrub({"latitude": 48.0, "longitude": 11.0}, gps_round=False)
        assert out["latitude"] == "**REDACTED**"
        assert out["longitude"] == "**REDACTED**"
