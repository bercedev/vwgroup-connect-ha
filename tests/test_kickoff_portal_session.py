# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Data Act kickoff scraper must ride the entry's isolated portal session.

#1793 moved the portal login onto a per-entry cookie jar, but the kickoff kept
building its scraper on Home Assistant's shared session. After a restart that
jar holds no portal cookie, the portal answers 401 and the session-expired
repair opened on every startup.
"""
from __future__ import annotations

from types import SimpleNamespace

from custom_components.vag_connect.coordinator import _portal_scraper_session


def test_scraper_uses_the_isolated_portal_session() -> None:
    own, shared = object(), object()
    assert _portal_scraper_session(SimpleNamespace(_portal_session=own), shared) is own


def test_falls_back_to_shared_without_an_isolated_session() -> None:
    shared = object()
    assert _portal_scraper_session(SimpleNamespace(_portal_session=None), shared) is shared
    assert _portal_scraper_session(None, shared) is shared
