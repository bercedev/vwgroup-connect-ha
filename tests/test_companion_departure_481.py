# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read observable 4.8.1 timer state without inventing hidden editor values."""
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.vag_connect.companion.channel import CompanionChannel, CompanionWriteBlocked
from custom_components.vag_connect.companion.departure import (
    departure_rows,
    is_compose_timer_page,
    read_timer_details,
    read_timer_page,
)
from custom_components.vag_connect.companion.presets import PRESETS
from custom_components.vag_connect.companion.resources import read_departure_timers
from custom_components.vag_connect.companion.screen import parse_ui_dump

FIXTURES = Path(__file__).parent / 'fixtures' / 'companion_departure'
STRINGS = {
    'departure_timer_subscreen_weekdays': {'Weekdays'},
    'weekday_saturday': {'Saturday'},
}


def xml(name):
    return (FIXTURES / f'{name}.xml').read_text()


def test_new_editor_reads_repeat_but_does_not_invent_time_or_weekdays():
    nodes = parse_ui_dump(xml('slot1_481'))
    assert is_compose_timer_page(nodes)
    assert read_timer_page(nodes) is None  # No verified writable clock/day state.
    assert read_timer_details(nodes, 1) == {'departure_timer_1_repeat': True}
    assert not is_compose_timer_page(parse_ui_dump(xml('list_481')))


@pytest.mark.parametrize('change', ['missing', 'conflicting', 'off', 'unrelated'])
def test_repeat_is_scoped_and_requires_consistent_switch_state(change):
    nodes = parse_ui_dump(xml('slot1_481'))
    switches = [n for n in nodes if n.checkable]
    assert len(switches) == 2
    if change == 'missing':
        nodes = [n for n in nodes if not n.checkable]
    elif change == 'conflicting':
        nodes = [replace(n, checked=False) if n is switches[0] else n for n in nodes]
    elif change == 'off':
        nodes = [replace(n, checked=False) if n.checkable else n for n in nodes]
    else:
        nodes = [replace(n, bounds=(0, 0, 100, 100)) if n.checkable else n for n in nodes]
    assert read_timer_details(nodes, 2) == ({'departure_timer_2_repeat': False} if change == 'off' else {})


def test_list_recurrence_does_not_guess_repeat():
    out = read_departure_timers(parse_ui_dump(xml('list_481')), STRINGS)
    assert out['departure_timer_1_weekdays'] == 'mon,tue,wed,thu,fri'
    assert out['departure_timer_2_weekdays'] == 'mon,tue,wed,thu,fri'
    assert out['departure_timer_3_weekdays'] == 'sat'
    assert not any(k.endswith('_repeat') for k in out)
    assert not any(k.endswith('_weekdays') for k in read_departure_timers(
        parse_ui_dump(xml('list_481')), {'weekday_saturday': {'Unrelated'}}))


def test_legacy_editor_still_exposes_complete_details():
    nodes = parse_ui_dump(xml('slot1_464'))
    assert not is_compose_timer_page(nodes)
    assert read_timer_page(nodes) is not None
    assert read_timer_details(nodes, 1) == {
        'departure_timer_1_repeat': True,
        'departure_timer_1_weekdays': 'mon,tue,wed,thu,fri',
    }


class ReadOnlyTimerPhone:
    """Only opening timer rows and BACK are allowed; any other tap fails."""
    def __init__(self):
        self.page = False
        self.opened = 0
        self.backs = 0
        self.points = {r.clock.tap_point for r in departure_rows(parse_ui_dump(xml('list_481')))}

    async def tap(self, x, y):
        assert not self.page and (x, y) in self.points
        self.page = True
        self.opened += 1

    async def key_back(self):
        assert self.page
        self.page = False
        self.backs += 1

    async def dump_ui(self):
        return xml('slot1_481' if self.page else 'list_481')


@pytest.mark.asyncio
async def test_new_editor_read_walk_returns_to_list_after_every_slot():
    phone = ReadOnlyTimerPhone()
    channel = CompanionChannel(phone, PRESETS['volkswagen'], time_fn=lambda: 0)
    listing = parse_ui_dump(xml('list_481'))
    out = await channel._read_departure_pages(listing)
    assert out == {f'departure_timer_{i}_repeat': True for i in (1, 2, 3)}
    assert (phone.opened, phone.backs, phone.page) == (3, 3, False)


@pytest.mark.asyncio
async def test_editor_without_the_hidden_node_helper_is_not_written():
    # A transport that cannot read the hidden picker (no a11y helper) only
    # opens the page and goes back; the full edit is in test_companion_departure_edit_481.
    phone = ReadOnlyTimerPhone()
    channel = CompanionChannel(phone, PRESETS['volkswagen'], time_fn=lambda: 0)
    with pytest.raises(CompanionWriteBlocked, match='direct ADB connection'):
        await channel._edit_timer(parse_ui_dump(xml('list_481')), 1, (8, 0), ('mon',), True)
    assert (phone.opened, phone.backs, phone.page) == (1, 1, False)
    assert channel.writes_enabled is False


DEPARTURE = next(n for n in PRESETS['volkswagen'].nav_reads if n.name == 'departure_times')


def test_page_day_buttons_win_over_the_list_label():
    # 4.6.4: the page's buttons are the stronger reading, even if the row's
    # label (which cannot tell one-time from repeating) says otherwise.
    channel = CompanionChannel(object(), PRESETS['volkswagen'], time_fn=lambda: 0)
    channel._app_strings = STRINGS
    fields = {}
    page = {'departure_timer_3_weekdays': 'fri,sat', 'departure_timer_3_repeat': False}
    channel._apply_nav_values(DEPARTURE, parse_ui_dump(xml('list_481')), fields, page)
    assert fields['departure_timer_3_weekdays'] == 'fri,sat'
    assert fields['departure_timer_3_repeat'] is False


def test_list_label_fills_the_days_a_481_page_cannot_show():
    channel = CompanionChannel(object(), PRESETS['volkswagen'], time_fn=lambda: 0)
    channel._app_strings = STRINGS
    fields = {}
    page = {f'departure_timer_{i}_repeat': True for i in (1, 2, 3)}
    channel._apply_nav_values(DEPARTURE, parse_ui_dump(xml('list_481')), fields, page)
    assert fields['departure_timer_1_weekdays'] == 'mon,tue,wed,thu,fri'
    assert fields['departure_timer_3_weekdays'] == 'sat'
    assert fields['departure_timer_3_repeat'] is True
