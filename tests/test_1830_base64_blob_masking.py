# Copyright 2026 Prash Balan (@its-me-prash) — GNU AGPL v3.0-or-later
# SPDX-License-Identifier: AGPL-3.0-or-later
"""#1830 — the Scout's masker decoded hex and nothing else.

``_encoded_identifier`` exists because a hex blob can carry an identifier that
no plain-text pattern will ever see, and its docstring states the premise it was
built on: "Only hex is decoded, because that is the encoding the EU Data Act
export actually uses for its credential blobs."

#1830 (@autosklo, Audi Q4 Sportback e-tron) retired that premise. The export
also ships base64: ``viwi_DriverNotification_1.2.0_get_notifications`` arrives as
a base64 JSON array of the notifications shown to the driver. Base64 defeats
every rule beneath it — the plain-text patterns cannot match what is not plain
text — and the 80-character truncation then publishes whatever the first ~60
decoded bytes happen to be.

That is the #1690 failure one encoding over, and #1690's own comment is the
reason this is not filed as a nice-to-have: it records that the hex version
happened **five times**, was hand-redacted four times before anyone fixed the
masker, and that truncation is not redaction.

Nothing escaped in #1830. The cut landed inside a classification name, 57
decoded characters in, which is luck rather than a protection — a longer
notification list puts its content in those same bytes.

What counts as worth removing whole, in both encodings, is an identifier inside
the run or a decode that is almost entirely printable, i.e. the encoding was
carrying a document. The tests below pin both, and four of them pin what must
NOT be touched: the hex path's own labels and lengths, a value that merely looks
base64-ish, a base64 run that decodes to binary (its shape is the field-discovery
signal and survives), and ordinary values.
"""
from __future__ import annotations

import base64
import json
import re

from custom_components.vag_connect.cariad._unexpected_keys import mask_value

# Invented, not anybody's car: a repeated-digit serial so it reads as made up.
_FAKE_VIN = "WVGZZZE21TE099999"

# The shape #1830 carried, rebuilt from the classification name visible in the
# issue. The values are invented — the point is that a DOCUMENT was in there.
_NOTIFICATIONS = [
    {
        "classification": ["PersistentDriverNotification"],
        "configuration": {"priority": 2, "dismissible": False},
        "text": "Service due in 1200 km",
    },
    {
        "classification": ["TransientDriverNotification"],
        "configuration": {"priority": 1, "dismissible": True},
        "text": "Charging cable connected",
    },
]
_DOC_B64 = base64.b64encode(json.dumps(_NOTIFICATIONS).encode()).decode()

_VINISH = re.compile(rb"[A-HJ-NPR-Z0-9]{17}")


def _recoverable_vin(masked: str) -> bool:
    """Can a VIN still be decoded out of what we would publish?

    The same question #1690's test asks of hex, asked of base64. Deliberately
    re-implemented here rather than imported: a shared helper that changed with
    the code would stop being an independent check.
    """
    for run in re.findall(r"[A-Za-z0-9+/]{16,}={0,2}", masked):
        trimmed = run.rstrip("=")
        trimmed = trimmed[: len(trimmed) // 4 * 4]
        if not trimmed:
            continue
        try:
            raw = base64.b64decode(trimmed, validate=True)
        except Exception:  # noqa: BLE001
            continue
        if _VINISH.search(raw):
            return True
    return False


class TestABase64DocumentIsRemovedWhole:
    def test_the_1830_shape_is_not_published(self) -> None:
        out = mask_value(_DOC_B64)
        assert "redacted" in out
        # neither the encoding nor any of its content survives
        assert _DOC_B64[:40] not in out
        assert "classification" not in out
        assert "Service due" not in out

    def test_the_replacement_says_what_it_removed_and_how_big(self) -> None:
        """Same contract as the hex path: name the kind and the length, so a
        field can still be identified and sized without its content."""
        out = mask_value(_DOC_B64)
        assert out == f'"[redacted text blob, {len(_DOC_B64)} chars]"'

    def test_truncation_alone_would_not_have_been_enough(self) -> None:
        """The counter-factual, stated as a test: the first 77 characters — what
        the old 80-char truncation would have left — decode to readable content
        from the document. This is why the whole run goes."""
        head = _DOC_B64[:76]
        head = head[: len(head) // 4 * 4]
        decoded = base64.b64decode(head, validate=True)
        printable = sum(1 for b in decoded if 32 <= b < 127)
        assert printable / len(decoded) > 0.9
        assert b"classification" in decoded


class TestABase64IdentifierIsCaughtToo:
    def test_a_vin_inside_base64_is_not_recoverable_from_the_output(self) -> None:
        blob = base64.b64encode(
            b"\x01\x02\x03" + _FAKE_VIN.encode() + b"\x04\x05\x06" * 8,
        ).decode()
        assert _recoverable_vin(blob), "fixture must be a real leak before the fix"
        out = mask_value(blob)
        assert not _recoverable_vin(out)
        assert "credential" in out

    def test_a_plain_text_vin_is_still_masked_the_old_way(self) -> None:
        """Regression: the new branch sits after hex and before the plain-text
        rules, and must not have displaced them."""
        out = mask_value(f"vehicle {_FAKE_VIN} ok")
        assert _FAKE_VIN not in out
        assert "***" in out


class TestWhatMustNotBeTouched:
    def test_a_long_hex_run_keeps_the_hex_path_label(self) -> None:
        """Hex is checked first on purpose. A hex blob with an identifier must
        still be reported as the hex path reports it, not relabelled by the new
        branch — the two give different lengths and different words."""
        blob = "2378d141538eb5bca400190100000" + _FAKE_VIN.encode().hex()
        out = mask_value(blob)
        assert out == f'"[redacted credential blob, {len(blob)} chars]"'

    def test_a_long_hex_run_without_an_identifier_is_still_kept(self) -> None:
        """The #1690 guardrail, re-asserted from this side: hex characters are a
        subset of the base64 alphabet, so a benign hex run reaches the new branch
        and must survive it. It decodes to binary, so it does."""
        benign = "ab12cd34" * 12
        out = mask_value(benign)
        assert "redacted" not in out
        assert out.startswith('"ab12cd34')

    def test_a_long_alphanumeric_word_is_not_mistaken_for_base64(self) -> None:
        """A value that merely uses the base64 alphabet. Two things keep it:
        it is under the run threshold, and it decodes to binary anyway."""
        for value in (
            "PersistentDriverNotification",
            "TransientDriverNotification",
            "CHARGING_SCENARIO_FINISHED_DEPARTURE_TIMER",
            "JOBSTATUS_FLASHING",
        ):
            out = mask_value(value)
            assert "redacted" not in out, value
            assert value[:12] in out, value

    def test_base64_carrying_binary_keeps_its_shape(self) -> None:
        """Field discovery needs to know a field exists and roughly what it
        looks like. A base64 image or signature carries no document and no
        identifier, so it is truncated as before rather than removed."""
        blob = base64.b64encode(bytes(range(256)) * 4).decode()
        out = mask_value(blob)
        assert "redacted" not in out
        assert out.endswith('..."')

    def test_the_ordinary_rules_are_unchanged(self) -> None:
        assert mask_value(48.123456) == "48.1"
        assert mask_value("a@b.example") == '"***@***"'
        assert mask_value(None) == "null"
        assert mask_value(True) == "true"
        assert mask_value(42) == "42"
        assert mask_value({"a": 1, "b": 2}) == "{2 keys}"
        assert mask_value([1, 2, 3]) == "[3 items]"
