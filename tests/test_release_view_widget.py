# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune import tags_store
from kitsune import release_cache

from kitsune.models import Release
from kitsune.ui.release_view import ReleaseView


def _make_release():
    return Release.from_dict({
        'id': 42,
        'name': {'main': 'Test', 'english': 'Test EN', 'alternative': ''},
        'alias': 'test',
        'description': 'Desc.',
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


def _setup(tmp_path):
    tags_store._TAGS_FILE = tmp_path / 'tags.json'
    cache_dir = tmp_path / 'releases'
    cache_dir.mkdir(exist_ok=True)
    release_cache._CACHE_DIR = cache_dir


def test_narrow_mode_default_false(mock_client, tmp_path):
    _setup(tmp_path)
    view = ReleaseView(release=_make_release(), client=mock_client)
    assert view._narrow_mode is False


def test_tag_pill_threshold_wide(mock_client, tmp_path):
    _setup(tmp_path)
    # Create 6 tags (> threshold 5 for wide mode)
    for i in range(5):
        tag = tags_store.create_tag(f'T{i}', 'emoji', '🎯')
        tags_store.add_release(tag['id'], 42)
    tags_store.add_release('favorites', 42)

    view = ReleaseView(release=_make_release(), client=mock_client)
    assert view._narrow_mode is False

    # 6 tags > threshold 5 -> compact pills (release-chip-compact class)
    child = view._tag_pills_wrap.get_first_child()
    assert child is not None
    assert 'release-chip-compact' in child.get_css_classes()


def test_tag_pill_threshold_narrow(mock_client, tmp_path):
    _setup(tmp_path)
    # Create 4 tags (> threshold 3 for narrow, but <= threshold 5 for wide)
    for i in range(3):
        tag = tags_store.create_tag(f'T{i}', 'emoji', '🎯')
        tags_store.add_release(tag['id'], 42)
    tags_store.add_release('favorites', 42)

    view = ReleaseView(release=_make_release(), client=mock_client)
    view._narrow_mode = True
    view._update_tag_pills()

    # 4 tags > threshold 3 -> compact
    child = view._tag_pills_wrap.get_first_child()
    assert child is not None
    assert 'release-chip-compact' in child.get_css_classes()


def test_few_tags_always_full(mock_client, tmp_path):
    _setup(tmp_path)
    # 2 tags — below both thresholds (3 narrow, 5 wide)
    tags_store.add_release('favorites', 42)
    tag = tags_store.create_tag('One', 'emoji', '🎯')
    tags_store.add_release(tag['id'], 42)

    view = ReleaseView(release=_make_release(), client=mock_client)

    # Wide mode: 2 <= 5 -> full pills
    child = view._tag_pills_wrap.get_first_child()
    assert child is not None
    assert 'release-chip' in child.get_css_classes()
    assert 'release-chip-compact' not in child.get_css_classes()

    # Narrow mode: 2 <= 3 -> still full pills
    view._narrow_mode = True
    view._update_tag_pills()
    child = view._tag_pills_wrap.get_first_child()
    assert 'release-chip' in child.get_css_classes()
    assert 'release-chip-compact' not in child.get_css_classes()
