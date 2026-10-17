# SPDX-License-Identifier: AGPL-3.0-or-later
"""Map navigation across app generations; no marker or Share taps."""
from dataclasses import replace
from pathlib import Path

import pytest

from custom_components.vag_connect.companion.channel import CompanionChannel
from custom_components.vag_connect.companion.presets import PRESETS
from custom_components.vag_connect.companion.resources import VEHICLE_LOCATION
from custom_components.vag_connect.companion.screen import UiNode, parse_ui_dump

VW = PRESETS['volkswagen']
STEP = next(n for n in VW.nav_reads if n.name == 'parking_position').path[1]
BUTTON = UiNode('', 'Vehicle location', '', 'android.widget.Button', True, (800, 900, 950, 1050))


@pytest.mark.parametrize('label', ['Vehicle location', 'Find vehicle', 'Fahrzeug finden'])
def test_new_and_legacy_labels_without_tables(label):
    node = replace(BUTTON, content_desc=label)
    channel = CompanionChannel(object(), VW, time_fn=lambda: 0)
    assert channel._step_node([node], STEP) is node


def test_installed_translation_takes_precedence_over_fallback():
    translated = replace(BUTTON, content_desc='Fahrzeugstandort')
    channel = CompanionChannel(object(), VW, time_fn=lambda: 0)
    channel._app_strings = {VEHICLE_LOCATION: {'Fahrzeugstandort'}}
    assert channel._step_node([BUTTON, translated], STEP) is translated


@pytest.mark.parametrize('change', [{'enabled': False}, {'bounds': (0, 0, 0, 0)},
                                  {'content_desc': 'My location'}])
def test_unavailable_or_unrelated_controls_do_not_match(change):
    channel = CompanionChannel(object(), VW, time_fn=lambda: 0)
    assert channel._step_node([replace(BUTTON, **change)], STEP) is None


MAP_481 = parse_ui_dump(
    (Path(__file__).parent / 'fixtures' / 'companion_parking' / 'map_481.xml').read_text(encoding='utf-8')
)
BUTTON_481 = (901, 1397, 1027, 1523)  # the clickable button around the labelled icon


@pytest.mark.parametrize('strings', [{}, {VEHICLE_LOCATION: {'Vehicle location'}}])
def test_live_481_map_taps_the_button_around_the_label(strings):
    channel = CompanionChannel(object(), VW, time_fn=lambda: 0)
    channel._app_strings = strings
    node = channel._step_node(MAP_481, STEP)
    assert node is not None and node.clickable and node.bounds == BUTTON_481


def test_label_without_its_own_button_never_taps_the_map():
    # Only the full-screen map is clickable around the label: no target.
    nodes = [n for n in MAP_481 if not (n.clickable and n.bounds == BUTTON_481)]
    channel = CompanionChannel(object(), VW, time_fn=lambda: 0)
    assert channel._step_node(nodes, STEP) is None
    channel._app_strings = {VEHICLE_LOCATION: {'Vehicle location'}}
    assert channel._step_node(nodes, STEP) is None
