# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune import tags_store

from kitsune.models import Release
from kitsune.ui.widgets.release_card import ReleaseCard


def _make_release(**overrides):
    data = {
        'id': 42,
        'name': {'main': 'Naruto', 'english': 'Naruto', 'alternative': ''},
        'alias': 'naruto',
        'description': '',
        'poster': None,
        'type': {'value': 'TV', 'description': 'TV'},
        'year': 2002,
        'season': {'value': 'fall', 'description': 'Fall'},
        'age_rating': {'value': 'R12_PLUS', 'label': '12+'},
        'episodes_total': 220,
        'is_ongoing': False,
        'genres': [],
        'episodes': [],
        'members': [],
        'torrents': [],
    }
    data.update(overrides)
    return Release.from_dict(data)


def _setup_tags(tmp_path):
    tags_store._TAGS_FILE = tmp_path / 'tags.json'


def test_title_from_release(tmp_path):
    _setup_tags(tmp_path)
    release = _make_release()
    card = ReleaseCard(release)
    assert card.title_label.get_label() == 'Naruto'


def test_subtitle_format(tmp_path):
    _setup_tags(tmp_path)
    release = _make_release()
    card = ReleaseCard(release)
    text = card.subtitle_label.get_label()
    assert 'TV' in text
    assert '2002' in text


def test_subtitle_type_only(tmp_path):
    _setup_tags(tmp_path)
    release = _make_release(year=None)
    card = ReleaseCard(release)
    assert card.subtitle_label.get_label() == 'TV'


def test_tag_badges_populated(tmp_path):
    _setup_tags(tmp_path)
    tags_store.add_release('favorites', 42)
    release = _make_release()
    card = ReleaseCard(release)
    assert card.tag_badges.get_visible() is True
    assert card.tag_badges.get_first_child() is not None


def test_tag_badges_max_three(tmp_path):
    _setup_tags(tmp_path)
    tags_store.add_release('favorites', 42)
    for i in range(3):
        tag = tags_store.create_tag(f'Tag{i}', 'emoji', '🎯')
        tags_store.add_release(tag['id'], 42)

    release = _make_release()
    card = ReleaseCard(release)

    pill = card.tag_badges.get_first_child()
    assert pill is not None

    # Count children in the pill (should be 3 tags + "+" = 4)
    count = 0
    child = pill.get_first_child()
    while child:
        count += 1
        child = child.get_next_sibling()
    assert count == 4  # 3 visible + "+" indicator


def test_refresh_tag_badges(tmp_path):
    _setup_tags(tmp_path)
    release = _make_release()
    card = ReleaseCard(release)
    assert card.tag_badges.get_visible() is False

    tags_store.add_release('favorites', 42)
    card.refresh_tag_badges()
    assert card.tag_badges.get_visible() is True
