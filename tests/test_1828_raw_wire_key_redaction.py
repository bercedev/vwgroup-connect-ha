# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""The raw scrubber matches wire keys exactly, and misses the camelCase ones.

``_scrub_raw`` decides by key: ``if k in _REDACT_KEYS or kl in _RAW_REDACT_KEYS``.
Two sets, two spellings. ``_REDACT_KEYS`` holds our own snake_case field names,
``_RAW_REDACT_KEYS`` holds the bare wire names a backend actually sends. The gap
is a wire name that is neither bare nor ours.

``vehicleNickname`` is exactly that. We redact our own ``vehicle_nickname``, and
we redact a bare ``nickname``, but the key vw.de sends lowercases to
``vehiclenickname``, which is in neither set — and no value regex catches it,
because a free-text name is not a VIN, a JWT, a UUID, an email or a coordinate.
So an owner-chosen vehicle nickname goes out in cleartext in the raw-payload
section of every diagnostics download for a vw.de car, and that section is not
opt-in.

This is not hypothetical. It happened in a public issue the same day this was
found: a reporter pasted his diagnostics inline, and the nickname sat in
cleartext two lines below ``"licensePlate": "**REDACTED**"`` and
``"vin": "**REDACTED**"``. Those redacted siblings are the proof that the
scrubber ran and that this key walked past it. He had hand-edited other fields
out of the same block and did not notice this one, which is the whole argument
for fixing it in the scrubber rather than in the instructions.

Counted rather than estimated, because the first version of this note said
"three more paths" and named three files: the same wire key is read at five
places in four files — ``cariad/_authproxy.py``:651 and :688,
``cariad/api/seat_cupra.py``:416, ``cariad/auth/_eu_data_act.py``:6845, and
``cariad/api/vw_na.py``:726, which spells it ``vehicleNickName`` with a capital
N. All five lowercase to the same thing, so one entry covers them. A sixth
reader, ``cariad/api/vw_eu.py``:557, uses a shorter spelling that lowercases to
something else and needs its own entry — see
tests/test_1832_render_url_spellings.py.

Deliberately NOT added here: a bare ``url``. It would match the structural
grounding data the raw export exists for, and the raw export's whole purpose is
to let a new field be mapped from a real payload shape. The image-URL case from
the same report needs its own decision rather than a blanket key.
"""
from __future__ import annotations

import json

import pytest

from custom_components.vag_connect.diagnostics import (
    _RAW_REDACT_KEYS,
    _REDACT_KEYS,
    _scrub_raw,
)

# Invented, and not anybody's car: the point is the KEY, not the value.
_NICK = "Testwagen"


class TestTheWireSpellingIsCovered:
    def test_the_lowercased_wire_key_is_in_the_raw_set(self):
        assert "vehiclenickname" in _RAW_REDACT_KEYS

    @pytest.mark.parametrize("key", [
        "vehicleNickname",   # vw.de / authproxy
        "vehicleNickName",   # VW North America, capital N
        "vehiclenickname",   # already-lowercase, defensive
    ])
    def test_every_spelling_is_redacted(self, key):
        out = _scrub_raw({key: _NICK})
        assert out[key] == "**REDACTED**"
        assert _NICK not in json.dumps(out)

    def test_it_is_redacted_when_nested_the_way_it_actually_arrives(self):
        """The real payload has it under relations[0] inside raw_responses, so a
        top-level-only fix would have looked green and changed nothing."""
        payload = {
            "vwde:app/authproxy/vw-de/proxy/v2/users/me/relations": {
                "relations": [
                    {"vehicleNickname": _NICK, "licensePlate": "AB123CD"},
                ],
            },
        }
        blob = json.dumps(_scrub_raw(payload))
        assert _NICK not in blob

    def test_the_siblings_that_already_worked_still_work(self):
        """Counter-check: these were redacted before this change and prove the
        scrubber is running at all, which is what made the gap visible."""
        out = _scrub_raw({"licensePlate": "AB123CD", "vin": "WVWZZZAUZFW000000"})
        assert out["licensePlate"] == "**REDACTED**"
        assert out["vin"] == "**REDACTED**"


class TestOurOwnSpellingStillWorks:
    """Regression: the snake_case name is matched by the other set, and that
    path must not be disturbed by adding the wire name."""

    def test_the_snake_case_field_is_still_redacted(self):
        assert "vehicle_nickname" in _REDACT_KEYS
        out = _scrub_raw({"vehicle_nickname": _NICK})
        assert out["vehicle_nickname"] == "**REDACTED**"

    def test_a_bare_nickname_is_still_redacted(self):
        assert "nickname" in _RAW_REDACT_KEYS
        out = _scrub_raw({"nickname": _NICK})
        assert out["nickname"] == "**REDACTED**"


class TestTheReportersOwnTwoItems:
    """What @Fishermanjb actually asked for in #1828.

    Worth being explicit: the nickname above is NOT one of his three items. He
    reported image links, model specifications and a dealer id; looking at the
    file for those three is what turned up the nickname. Two of his three are
    here, and they are here on their own merits.
    """

    def test_the_render_image_urls_are_redacted_in_both_shapes(self):
        """`image_urls`, our own name, was already redacted with the recorded
        reason that render URLs 'encode the exact model/colour/config'. The wire
        sends two other shapes of the same thing, and both walked past: a bare
        `imageUrls` array and an `images` array of objects each carrying a `url`.
        Masking our spelling and not theirs protected nothing."""
        out = _scrub_raw({"imageUrls": ["https://x/a.png", "https://x/b.png"]})
        assert out["imageUrls"] == "**REDACTED**"
        out2 = _scrub_raw({"images": [{"url": "https://x/a.png", "view": "front"}]})
        assert out2["images"] == "**REDACTED**"

    def test_the_dealer_id_is_redacted(self):
        """His reasoning is the right one and it is the same class as
        parking_city: a dealer id is not his data, but it localises him. He put
        it as 'often in the area of the user'."""
        out = _scrub_raw({"dealerId": "DE-12345"})
        assert out["dealerId"] == "**REDACTED**"

    def test_the_images_entry_is_registered_and_other_containers_are_left_alone(self):
        """Two different things, renamed so neither is read as the other.

        The membership assertion goes red if the `images` entry is removed. The
        `batteryStatus` assertion does not: both of its keys are in neither
        redaction set, so it passes with or without that entry. It is a liveness
        check on the walker, NOT evidence for the key choice — which is what the
        old name for this test claimed. The choice rests on reading the one place
        `images` is parsed (cariad/_authproxy.py:868), and no assertion over
        these two unrelated keys can show that."""
        assert "images" in _RAW_REDACT_KEYS
        out = _scrub_raw({"batteryStatus": {"currentSOC_pct": 35}})
        assert out["batteryStatus"]["currentSOC_pct"] == 35


class TestWhatIsDeliberatelyLeftAlone:
    """Records the decisions, so the next reader does not take the omission for
    an oversight the way this one was."""

    def test_a_bare_url_is_not_blanket_redacted(self):
        """Redacting every 'url' would gut the raw export's reason to exist. The
        two image shapes above are covered by their CONTAINER keys instead,
        which is why this stays out."""
        assert "url" not in _RAW_REDACT_KEYS

    def test_model_name_is_not_treated_as_personal(self):
        """His third item. Payload size, not privacy — and saying so plainly is
        better than quietly masking it and implying it was sensitive."""
        assert "modelname" not in _RAW_REDACT_KEYS
        out = _scrub_raw({"modelName": "ID.4 Pro Performance"})
        assert out["modelName"] == "ID.4 Pro Performance"
