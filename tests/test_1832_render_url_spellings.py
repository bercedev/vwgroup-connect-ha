# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""Render and map URLs: our names were masked, the wire's spellings were not.

The same shape of mistake #1828 found in the nickname, in the URL family. Two
key sets, two spellings. ``_REDACT_KEYS`` holds our own snake_case field names
and is consulted by both scrubbers; ``_RAW_REDACT_KEYS`` holds the bare wire
names, lowercased, and is consulted only by ``_scrub_raw``. A name that is in
neither walks out whole, because no value regex catches a URL — the string
branch masks an email, a VIN, a JWT, a UUID and a ``latitude=&longitude=`` query
pair, and a render link is none of those. #1310 established exactly that for
``render_url``: it masked the VIN inside the blob filename and left the rest of
the URL standing.

What was open on this branch, each re-checked at file:line before being listed:

* ``render_url`` — cariad/models.py:1153, the Skoda widget's ``vehicle.renderUrl``.
* ``composite_render_urls`` — cariad/models.py:1166, written at
  coordinator.py:7134 and merged INTO ``image_urls``, so the merge target was
  masked while the source dict went out whole.
* ``renderUrl`` / ``lightMapUrl`` / ``darkMapUrl`` — the wire spellings of those
  and of ``parking_map_url_dark`` / ``_light``. These arrive on a RECORDED
  channel, so they are live rather than latent: cariad/api/skoda.py:1480 writes
  the widget body into ``last_raw_responses`` under the channel name "widget",
  the expected-key table lists all three
  (cariad/_unexpected_keys.py:353 and :365-366), and SEAT/CUPRA read the two map
  keys at cariad/api/seat_cupra.py:1849-1850.
* ``renders`` / ``compositeRenders`` — the two render containers registered on
  the recorded Skoda ``vehicle-status`` channel
  (cariad/_unexpected_keys.py:114-116). Found by sweeping that table rather than
  by guessing at spellings; the sweep's method and its counter-check are
  recorded in ``TestTheSweepThatFoundTheContainers`` below.
* ``vehicleNick`` — cariad/api/vw_eu.py:557. LATENT, not live: see
  ``TestTheLatentNicknameSpelling``.

Deliberately still absent, and asserted as such in
``TestDecisionsThatAreInvariantToThisDiff``: a bare ``url``, and the ``maps``
container. The container is the interesting one — keeping it is what makes the
sibling test below necessary.
"""
from __future__ import annotations

import json

import pytest

from custom_components.vag_connect.diagnostics import (
    _RAW_REDACT_KEYS,
    _REDACT_KEYS,
    _scrub,
    _scrub_raw,
)

# Invented hosts and paths. The point of every case here is the KEY.
_RENDER = "https://renders.example/widget-renders/a.png?etag=0x00000000000000"
# No coordinate pair in these on purpose: the whole value is asserted gone, so
# the test needs no position in it to mean anything.
_MAP_LIGHT = "https://maps.example/staticmap?tile=0000&zoom=15&theme=light"
_MAP_DARK = "https://maps.example/staticmap?tile=0000&zoom=15&theme=dark"
# Visibly invented: zeroed serial, and still 17 characters so the VIN regex sees
# it. Used only to prove the string branch is alive where it is still the thing
# doing the work.
_FAKE_VIN = "WVWZZZAUZFW000000"
# Not anybody's plate.
_FAKE_PLATE = "XX000000"


class TestOurOwnTwoFieldNames:
    """``_REDACT_KEYS`` — the per-vehicle section, reached by ``_scrub``."""

    @pytest.mark.parametrize("key", ["render_url", "composite_render_urls"])
    def test_the_key_is_in_the_set(self, key):
        assert key in _REDACT_KEYS

    def test_the_render_url_does_not_survive_a_scrub(self):
        out = _scrub({"render_url": _RENDER}, gps_round=False)
        assert out["render_url"] == "**REDACTED**"
        assert "renders.example" not in json.dumps(out)

    def test_the_composite_map_does_not_survive_a_scrub(self):
        """The dict form. Reverting the fix leaves the viewPoint keys AND the
        URLs behind, so this is the half of this test that measures the diff."""
        out = _scrub(
            {"composite_render_urls": {"exterior_side": _RENDER}},
            gps_round=False,
        )
        assert out["composite_render_urls"] == "**REDACTED**"
        assert "renders.example" not in json.dumps(out)

    @pytest.mark.parametrize("key", ["render_url", "composite_render_urls"])
    def test_an_empty_value_stays_visibly_empty(self, key):
        """#923 (@naked-head) — a masked empty field is indistinguishable from a
        masked real one, which misleads triage. These two inherit the existing
        ``_is_empty_value`` guard rather than getting a blanket mask.

        Honest about what this measures: an empty dict comes back empty with or
        without the fix, so this assertion does not prove the fix. It is here so
        the next person who widens the branch cannot drop the guard unnoticed."""
        for empty in (None, "", {}):
            out = _scrub({key: empty}, gps_round=False)
            assert out[key] == empty

    def test_it_is_redacted_in_gps_round_mode_too(self):
        """A URL cannot be rounded, so the coordinate opt-in must not become a
        way to publish it whole — the same argument
        tests/test_diagnostics_parking_map_url.py makes for the map links."""
        out = _scrub({"render_url": _RENDER}, gps_round=True)
        assert out["render_url"] == "**REDACTED**"


class TestTheWireSpellingsOnARecordedChannel:
    """``_RAW_REDACT_KEYS`` — the raw-payload section, reached by ``_scrub_raw``."""

    @pytest.mark.parametrize("key", ["renderurl", "lightmapurl", "darkmapurl"])
    def test_the_lowercased_wire_key_is_in_the_raw_set(self, key):
        assert key in _RAW_REDACT_KEYS

    @pytest.mark.parametrize("key,value", [
        ("renderUrl", _RENDER),
        ("lightMapUrl", _MAP_LIGHT),
        ("darkMapUrl", _MAP_DARK),
    ])
    def test_the_spelling_the_wire_actually_sends_is_redacted(self, key, value):
        out = _scrub_raw({key: value})
        assert out[key] == "**REDACTED**"
        assert value not in json.dumps(out)

    def test_it_is_redacted_where_it_actually_arrives(self):
        """The recorded "widget" channel, nested as skoda.py:1480 records it. A
        top-level-only fix would have looked green and changed nothing: both
        keys sit two and three levels down."""
        payload = {
            "widget": {
                "vehicle": {"renderUrl": _RENDER},
                "parkingPosition": {
                    "maps": {"lightMapUrl": _MAP_LIGHT, "darkMapUrl": _MAP_DARK},
                },
            },
        }
        blob = json.dumps(_scrub_raw(payload))
        assert "renders.example" not in blob
        assert "maps.example" not in blob

    def test_a_redacted_key_beside_the_url_is_still_redacted(self):
        """The failure mode this fix had to avoid, asserted rather than assumed.

        The ``maps`` container is deliberately left OFF the key set so the block
        keeps its shape, which means the two URLs inside it are taken one key at
        a time. A fix that kept the shape by handing the container to a helper of
        its own — masking url-ish keys and passing every other VALUE on — would
        stop testing the sibling KEYS against the key sets, and a licence plate
        standing next to a map link would go out in cleartext.

        Two halves, and they measure different things. The map-URL assertions
        fail if this commit's entries are reverted; the ``licencePlate`` and
        ``vin`` assertions pass either way and are the counter-check that the
        key-based path is still the thing handling siblings."""
        out = _scrub_raw({
            "maps": {
                "lightMapUrl": _MAP_LIGHT,
                "licencePlate": _FAKE_PLATE,
                "vin": _FAKE_VIN,
                "zoomLevel": 15,
            },
        })
        inner = out["maps"]
        assert inner["lightMapUrl"] == "**REDACTED**"       # this commit
        assert inner["licencePlate"] == "**REDACTED**"      # counter-check
        assert inner["vin"] == "**REDACTED**"               # counter-check
        assert inner["zoomLevel"] == 15                     # shape survives
        blob = json.dumps(out)
        assert _FAKE_PLATE not in blob
        assert _FAKE_VIN not in blob
        assert "maps.example" not in blob


class TestTheTwoRenderContainers:
    @pytest.mark.parametrize("key", ["renders", "compositerenders"])
    def test_the_container_key_is_in_the_raw_set(self, key):
        assert key in _RAW_REDACT_KEYS

    def test_the_vehicle_status_renders_block_is_redacted(self):
        """The shape a community Scout report put on the recorded
        ``vehicle-status`` channel (cariad/_unexpected_keys.py:114-115, #107):
        ``renders`` with a lightMode / darkMode block under it. The child keys
        are brand enums, so they are not listed individually — taking the
        container is what closes them."""
        out = _scrub_raw({"renders": {"lightMode": {"main": _RENDER}}})
        assert out["renders"] == "**REDACTED**"
        assert "renders.example" not in json.dumps(out)

    def test_the_composite_renders_block_is_redacted(self):
        """The documented wire shape (cariad/api/skoda.py:1226): a list of
        entries each carrying ``layers[].url``. The URLs sit under a bare
        ``url``, which this set deliberately does not carry, so the container is
        the only key that reaches them."""
        payload = {
            "compositeRenders": [
                {
                    "layers": [
                        {"url": _RENDER, "viewPoint": "EXTERIOR_SIDE",
                         "type": "REAL", "order": 0},
                    ],
                    "viewType": "UNMODIFIED_EXTERIOR_SIDE",
                },
            ],
        }
        out = _scrub_raw(payload)
        assert out["compositeRenders"] == "**REDACTED**"
        assert "renders.example" not in json.dumps(out)


class TestTheLatentNicknameSpelling:
    """``vehicleNick`` — registered, and NOT a second live leak.

    cariad/api/vw_eu.py:557 reads it inside ``_pick_model`` (:553) inside
    ``get_vehicles`` (:440). The raw section walks ``last_raw_responses``, which
    this client rebuilds per poll from the status endpoints (:1047-1053) or
    copies from the website proxy (:851) — the garage body ``get_vehicles``
    parses is never recorded there. So the entry is defensive, the same way the
    token field names in ``_REDACT_KEYS`` are.
    """

    def test_the_spelling_is_registered(self):
        assert "vehiclenick" in _RAW_REDACT_KEYS

    def test_the_compound_entry_does_not_already_cover_it(self):
        """Why it needs its own entry: the raw match is exact lowercased
        membership, not a prefix or substring test. Counter-check first, with
        the compound spelling that WAS already covered."""
        assert "vehiclenickname" in _RAW_REDACT_KEYS
        assert "vehiclenick" != "vehiclenickname"
        out = _scrub_raw({"vehicleNick": "Testwagen", "vehicleNickname": "Testwagen"})
        assert out["vehicleNick"] == "**REDACTED**"
        assert out["vehicleNickname"] == "**REDACTED**"

    def test_the_garage_body_shape_is_covered_if_it_is_ever_recorded(self):
        payload = {"data": [{"vehicleNick": "Testwagen", "modelYear": 2021}]}
        out = _scrub_raw(payload)
        assert out["data"][0]["vehicleNick"] == "**REDACTED**"
        assert out["data"][0]["modelYear"] == 2021
        assert "Testwagen" not in json.dumps(out)


class TestTheSweepThatFoundTheContainers:
    """How the container spellings were found, as a runnable check rather than a
    claim in a commit message.

    The method: take the integration's own catalogue of wire keys
    (``EXPECTED_KEYS`` in cariad/_unexpected_keys.py, keyed by the same channel
    names the export uses), split every dotted path into segments, keep the
    segments that read as a url/render/map/image word, and test each against
    both key sets. The nil part of the result only means something if the
    scanner can see a key it should: ``licensePlate`` and ``gpsCoordinates`` are
    in the table and covered, and are used here as the known positives.
    """

    @staticmethod
    def _segments():
        from custom_components.vag_connect.cariad._unexpected_keys import (
            EXPECTED_KEYS,
        )
        segs = set()
        for eps in EXPECTED_KEYS.values():
            for paths in eps.values():
                for path in paths:
                    for seg in path.split("."):
                        if seg != "*":
                            segs.add(seg)
        return segs

    def test_the_scanner_sees_a_key_it_should(self):
        """Counter-check on the instrument, before any nil result is read as
        evidence. Both of these are in the table and both are covered."""
        segs = self._segments()
        assert "licensePlate" in segs
        assert "gpsCoordinates" in segs
        assert "licensePlate".lower() in _RAW_REDACT_KEYS
        assert "gpsCoordinates".lower() in _RAW_REDACT_KEYS

    def test_every_url_shaped_segment_in_the_table_is_now_covered(self):
        """The result. Reverting this commit leaves renderUrl, lightMapUrl,
        darkMapUrl, renders and compositeRenders in the uncovered list."""
        import re

        pattern = re.compile(r"url|render|map|image|img|photo|picture|thumb", re.I)
        uncovered = sorted(
            seg for seg in self._segments()
            if pattern.search(seg)
            and seg not in _REDACT_KEYS
            and seg.lower() not in _RAW_REDACT_KEYS
        )
        # ``maps`` is the one deliberate omission: its two known children are
        # covered by their own keys, so the container keeps its shape. See
        # TestDecisionsThatAreInvariantToThisDiff.
        assert uncovered == ["maps"], uncovered


class TestDecisionsThatAreInvariantToThisDiff:
    """Deliberately not sensitive to this commit.

    Every assertion in this class passes with or without the entries added here,
    so none of it is evidence that the fix works — the classes above are. It is
    here so a later widening cannot reverse a decision silently.
    """

    def test_a_bare_url_is_still_not_blanket_redacted(self):
        """#1828's decision, unchanged: ``_RAW_REDACT_KEYS`` is matched at every
        depth of the walk, so a bare ``url`` would blank the URLs in every
        container in the payload, which is what the raw export exists to show.
        The render containers are taken by their container keys instead."""
        assert "url" not in _RAW_REDACT_KEYS
        out = _scrub_raw({"softwareUpdate": {"url": "https://vendor.example/notes"}})
        assert out["softwareUpdate"]["url"] == "https://vendor.example/notes"

    def test_the_maps_container_itself_is_not_a_key(self):
        """Keeping it is what preserves the parking block's shape. The two URL
        children are covered by their own keys above, and a sibling inside it
        still goes through the normal key-based path — that is asserted in
        ``test_a_redacted_key_beside_the_url_is_still_redacted``, where it can
        fail."""
        assert "maps" not in _RAW_REDACT_KEYS
        out = _scrub_raw({"maps": {"zoomLevel": 15}})
        assert out["maps"] == {"zoomLevel": 15}

    def test_the_ota_release_notes_link_is_not_treated_as_personal(self):
        """The fourth url-shaped field on ``VehicleData``
        (cariad/models.py:1683, set at cariad/api/skoda.py:2271). It is the
        manufacturer's release-notes page for a software version — the same
        page for every car on that version — so it carries nothing of the
        owner's and is left readable, as the update entity's
        ``release_url`` (update.py:97) needs it to be."""
        assert "ota_release_notes_url" not in _REDACT_KEYS
        url = "https://vendor.example/release-notes/1.2.3"
        out = _scrub({"ota_release_notes_url": url}, gps_round=False)
        assert out["ota_release_notes_url"] == url
