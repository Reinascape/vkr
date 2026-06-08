# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.models import Release, Episode
from kitsune.ui.player_view import PlayerView


def _make_release():
    return Release.from_dict({
        'id': 42,
        'name': {'main': 'Test', 'english': '', 'alternative': ''},
        'alias': 'test',
        'description': '',
        'poster': None,
        'type': {'value': 'TV', 'description': 'TV'},
        'year': 2025,
        'season': {'value': 'winter', 'description': 'Winter'},
        'age_rating': {'value': 'R12_PLUS', 'label': '12+'},
        'episodes_total': 12,
        'is_ongoing': False,
        'genres': [],
        'episodes': [],
        'members': [],
        'torrents': [],
    })


def _make_episode():
    return Episode.from_dict({
        'id': 1,
        'name': 'Episode 1',
        'ordinal': 1.0,
        'hls_480': 'http://example.com/480.m3u8',
        'hls_720': 'http://example.com/720.m3u8',
        'hls_1080': None,
        'duration': 1440,
        'opening': None,
        'ending': None,
        'preview': None,
        'sort_order': 0,
    })


def test_creation():
    release = _make_release()
    episode = _make_episode()
    view = PlayerView(release=release, episode=episode)
    assert view is not None


def test_has_controls():
    release = _make_release()
    episode = _make_episode()
    view = PlayerView(release=release, episode=episode)
    assert view.play_btn is not None
    assert view.progress is not None
    assert view.volume_scale is not None
    assert view.speed_dropdown is not None
    assert view.rotate_btn is not None
    assert view.quality_dropdown is not None
