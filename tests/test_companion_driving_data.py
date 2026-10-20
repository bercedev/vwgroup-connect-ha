# SPDX-License-Identifier: AGPL-3.0-or-later
"""The Driving data trip cards, read by the app's own labels and units."""
from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from custom_components.vag_connect.companion.channel import CompanionChannel
from custom_components.vag_connect.companion.presets import PRESETS
from custom_components.vag_connect.companion.resources import (
    DRIVING_TILE,
    find_tile_entry,
    read_driving_data,
    trip_carousel_row,
)
from custom_components.vag_connect.companion.screen import UiNode, parse_ui_dump

FIXTURES = Path(__file__).parent / "fixtures"
STRINGS = {
    k: set(v) for k, v in json.loads(
        (FIXTURES / "companion_battery" / "vw_432_resources.json").read_text(encoding="utf-8")
    ).items()
}
VW = PRESETS["volkswagen"]
NAV = next(nav for nav in VW.nav_reads if nav.name == "driving_data")
LAST = {
    "last_trip_distance_km": 3.0, "last_trip_avg_electric_consumption_kwh_100km": 40.3,
    "last_trip_avg_fuel_consumption_l_100km": 0.0, "last_trip_avg_speed_kmh": 18.0,
    "last_trip_duration_min": 12,
}
REFUEL = {
    "refuel_trip_distance_km": 299.0, "refuel_trip_avg_electric_consumption_kwh_100km": 16.5,
    "refuel_trip_avg_fuel_consumption_l_100km": 1.3, "refuel_trip_avg_speed_kmh": 27.0,
    "refuel_trip_duration_min": 659,
}


def _xml(name: str) -> str:
    return (FIXTURES / "companion_driving" / f"{name}.xml").read_text(encoding="utf-8")


def test_first_card_is_read_and_the_clipped_one_gives_nothing():
    assert read_driving_data(parse_ui_dump(_xml("tiguan_driving")), STRINGS) == LAST


def test_second_card_after_the_swipe():
    assert read_driving_data(parse_ui_dump(_xml("tiguan_driving_swiped")), STRINGS) == REFUEL


def test_miles_are_converted_and_unknown_units_skipped():
    xml = (_xml("tiguan_driving_swiped").replace('text="299 km"', 'text="186 mi"')
           .replace('text="27 km/h"', 'text="17 mph"')
           .replace('text="16.5 kWh/100 km"', 'text="3.8 mi/kWh"'))
    out = read_driving_data(parse_ui_dump(xml), STRINGS)
    assert out["refuel_trip_distance_km"] == 299.3
    assert out["refuel_trip_avg_speed_kmh"] == 27.4
    assert "refuel_trip_avg_electric_consumption_kwh_100km" not in out


@pytest.mark.parametrize(("text", "expected"), [
    ("1.234,5 km", 1234.5), ("1,234.5 km", 1234.5), ("1,234 km", 1234.0), ("16,5 km", 16.5),
    ("⌀ 299 km", 299.0), ("12 345 km", 12345.0),
])
def test_number_forms(text, expected):
    xml = _xml("tiguan_driving_swiped").replace('text="299 km"', f'text="{text}"')
    assert read_driving_data(parse_ui_dump(xml), STRINGS)["refuel_trip_distance_km"] == expected


def test_carousel_band_spans_both_cards_only_while_both_are_drawn():
    assert trip_carousel_row(parse_ui_dump(_xml("tiguan_driving")), STRINGS) == (53, 261, 1080, 1050)


def test_tile_found_by_its_translated_label():
    strings = {DRIVING_TILE: {"Fahrdaten"}, "acc_common_hint_details": {"Details öffnen"}}
    tile = UiNode("", "Fahrdaten. Zuletzt gefahren: 3 km. Details öffnen", "",
                  "android.view.View", False, (0, 0, 10, 10))
    assert find_tile_entry([tile], strings, DRIVING_TILE) is tile


class Carousel:
    """The Driving data screen: one sideways swipe brings the second card."""

    connected = True

    def __init__(self) -> None:
        self.swipes: list[tuple[int, int, int, int]] = []

    async def dump_ui(self) -> str:
        return _xml("tiguan_driving_swiped" if self.swipes else "tiguan_driving")

    async def swipe(self, x1, y1, x2, y2, _ms=300):
        self.swipes.append((x1, y1, x2, y2))

    async def key_back(self):
        raise AssertionError("no BACK expected")


@pytest.mark.asyncio
async def test_channel_swipes_sideways_once_and_keeps_both_cards():
    phone = Carousel()
    channel = CompanionChannel(phone, VW, time_fn=time.monotonic, nav_opt_ins={"driving_data"})
    channel._app_strings = STRINGS
    channel._trip_families = {"last_trip", "refuel_trip"}  # after the first sweep
    channel._trip_swept_at = channel._now()
    out = await channel._read_driving_data(parse_ui_dump(_xml("tiguan_driving")))
    assert out == {**LAST, **REFUEL}
    [(x1, y1, x2, y2)] = phone.swipes
    assert y1 == y2 and x1 > x2  # sideways, never a pull-to-refresh


@pytest.mark.asyncio
async def test_walk_runs_only_when_opted_in_and_until_the_trips_are_known():
    walked = []

    async def fake_walk(path, here=None):
        walked.append(path[0].action)
        return None, 0

    async def noop(*_a, **_k):
        return None

    off = CompanionChannel(object(), VW, time_fn=time.monotonic, nav_opt_ins=set())
    off._walk_to_detail, off._return_to_overview = fake_walk, noop
    await off._augment_via_nav({})
    assert walked == []

    on = CompanionChannel(object(), VW, time_fn=time.monotonic, nav_opt_ins={"driving_data"})
    on._walk_to_detail, on._return_to_overview = fake_walk, noop
    await on._augment_via_nav({})
    assert walked == ["open_driving_data"]
    await on._augment_via_nav({t: 1 for t in NAV.resource_targets})
    assert walked == ["open_driving_data"]


STRINGS_481 = {**STRINGS, **{
    k: set(v) for k, v in json.loads(
        (FIXTURES / 'companion_driving' / 'vw_481_extra_resources.json').read_text()
    ).items()
}}


def test_long_haul_is_a_separate_family_and_single_trip_can_follow_it():
    out = read_driving_data(parse_ui_dump(_xml('long_first_481')), STRINGS_481)
    assert out['companion_long_trip_distance_km'] == 499
    assert out['companion_long_trip_duration_min'] == 1317
    assert not any(k.startswith(('last_trip_', 'refuel_trip_')) for k in out)
    second = read_driving_data(parse_ui_dump(_xml('single_second_481')), STRINGS_481)
    assert second['last_trip_distance_km'] == 3
    assert second['last_trip_duration_min'] == 13


def test_carousel_discovery_does_not_require_known_titles():
    nodes = parse_ui_dump(_xml('long_first_481').replace('Last single trip', 'Unknown single')
                          .replace('Last long-haul trip', 'Unknown long'))
    assert trip_carousel_row(nodes, STRINGS_481) == (0, 261, 1080, 1050)
    # The outer vertical scroller alone must never be used as a sideways band.
    from dataclasses import replace
    no_horizontal = [replace(n, scrollable=False) if n.bounds == (0, 261, 1080, 1050)
                     else n for n in nodes]
    # Remove the second card: no remaining evidence of a horizontal carousel.
    no_horizontal = [n for n in no_horizontal if not (n.bounds and n.bounds[0] >= 970)]
    assert trip_carousel_row(no_horizontal, STRINGS_481) is None


class OrderedCarousel(Carousel):
    def __init__(self, screens):
        super().__init__()
        self.screens = screens

    async def dump_ui(self):
        return _xml(self.screens[min(len(self.swipes), len(self.screens) - 1)])


@pytest.mark.asyncio
async def test_channel_collects_single_trip_after_long_haul_then_refuel():
    phone = OrderedCarousel(['long_first_481', 'single_second_481', 'tiguan_driving_swiped'])
    channel = CompanionChannel(phone, VW, time_fn=time.monotonic)
    channel._app_strings = STRINGS_481
    channel._trip_families = {"companion_long_trip", "last_trip", "refuel_trip"}
    channel._trip_swept_at = channel._now()
    out = await channel._read_driving_data(parse_ui_dump(_xml('long_first_481')))
    assert out['companion_long_trip_distance_km'] == 499
    assert out['last_trip_distance_km'] == 3
    assert out['last_trip_duration_min'] == 13
    assert out['refuel_trip_distance_km'] == 299
    assert len(phone.swipes) == 2


@pytest.mark.asyncio
async def test_channel_searches_backwards_when_initial_card_is_at_the_end():
    phone = OrderedCarousel(['tiguan_driving_swiped', 'tiguan_driving_swiped', 'tiguan_driving'])
    channel = CompanionChannel(phone, VW, time_fn=time.monotonic)
    channel._app_strings = STRINGS
    channel._trip_families = {"last_trip", "refuel_trip"}
    channel._trip_swept_at = channel._now()
    out = await channel._read_driving_data(parse_ui_dump(_xml('tiguan_driving_swiped')))
    assert out == {**REFUEL, **LAST}
    assert len(phone.swipes) == 2
    assert phone.swipes[0][0] > phone.swipes[0][2]
    assert phone.swipes[1][0] < phone.swipes[1][2]


@pytest.mark.asyncio
async def test_stationary_carousel_stops_with_a_bounded_number_of_swipes():
    phone = OrderedCarousel(['long_first_481'])
    channel = CompanionChannel(phone, VW, time_fn=time.monotonic)
    channel._app_strings = STRINGS_481
    out = await channel._read_driving_data(parse_ui_dump(_xml('long_first_481')))
    assert out['companion_long_trip_distance_km'] == 499
    assert len(phone.swipes) == 2


class DirectedCarousel:
    """Cards in a row; a right-to-left swipe moves one card forward."""

    def __init__(self, screens, start):
        self.screens, self.index = screens, start
        self.swipes: list[tuple[int, int, int, int]] = []

    async def dump_ui(self):
        return self.screens[self.index]

    async def swipe(self, x1, y1, x2, y2, _ms=300):
        self.swipes.append((x1, y1, x2, y2))
        step = 1 if x1 > x2 else -1
        self.index = max(0, min(len(self.screens) - 1, self.index + step))


@pytest.mark.asyncio
async def test_first_read_sweeps_both_ends_and_finds_a_hidden_long_haul_card():
    """Long-haul sits left of the starting card and shows no title there."""
    # The refuel card without its long-haul peek: nothing on screen to the
    # right of the start names the long-haul card.
    refuel = _xml('tiguan_driving_swiped').replace('text="Last long-haul trip"', 'text=""')
    screens = [_xml('long_first_481'), _xml('single_second_481'), refuel]
    phone = DirectedCarousel(screens, start=1)
    channel = CompanionChannel(phone, VW, time_fn=time.monotonic)
    channel._app_strings = STRINGS_481
    out = await channel._read_driving_data(parse_ui_dump(screens[1]))
    assert out['companion_long_trip_distance_km'] == 499
    assert out['last_trip_distance_km'] == 3
    assert out['refuel_trip_distance_km'] == 299
    assert channel._trip_families == {"companion_long_trip", "last_trip", "refuel_trip"}

    # The next read starts in the middle again: the remembered card is searched
    # for, and the walk stops as soon as it is read.
    phone.index, phone.swipes = 1, []
    out = await channel._read_driving_data(parse_ui_dump(screens[1]))
    assert out['companion_long_trip_distance_km'] == 499
    assert phone.index == 0 and len(phone.swipes) <= 4


@pytest.mark.asyncio
async def test_a_card_added_later_is_found_by_the_daily_sweep():
    refuel = _xml('tiguan_driving_swiped').replace('text="Last long-haul trip"', 'text=""')
    screens = [_xml('single_second_481'), refuel]  # no long-haul card yet
    clock = [0.0]
    phone = DirectedCarousel(screens, start=0)
    channel = CompanionChannel(phone, VW, time_fn=lambda: clock[0])
    channel._app_strings = STRINGS_481
    await channel._read_driving_data(parse_ui_dump(screens[0]))
    assert channel._trip_families == {"last_trip", "refuel_trip"}

    # The app now shows a long-haul card left of the single trip.
    screens.insert(0, _xml('long_first_481'))
    phone.index = 1
    out = await channel._read_driving_data(parse_ui_dump(screens[1]))
    assert 'companion_long_trip_distance_km' not in out  # same day: not searched

    clock[0] = 24 * 3600.0
    phone.index = 1
    out = await channel._read_driving_data(parse_ui_dump(screens[1]))
    assert out['companion_long_trip_distance_km'] == 499
    assert "companion_long_trip" in channel._trip_families
