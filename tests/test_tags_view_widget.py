# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune import tags_store
from kitsune import release_cache

from kitsune.ui.tags_view import TagsView
from kitsune.ui.tag_releases_view import TagReleasesView


def _setup_stores(tmp_path):
    tags_store._TAGS_FILE = tmp_path / 'tags.json'
    cache_dir = tmp_path / 'releases'
    cache_dir.mkdir(exist_ok=True)
    release_cache._CACHE_DIR = cache_dir


def _make_tag(**overrides):
    tag = {
        'id': 'test01', 'name': 'Test', 'icon_type': 'emoji',
        'icon_value': '🔥', 'builtin': False, 'order': 1, 'releases': [],
    }
    tag.update(overrides)
    return tag


def test_initial_mode_cards(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    assert view._view_mode == 'cards'


def test_toggle_mode(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    assert view._view_mode == 'cards'
    view.toggle_mode()
    assert view._view_mode == 'list'
    view.toggle_mode()
    assert view._view_mode == 'cards'


def test_toggle_mode_switches_visible_stack(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    assert view._mode_stack.get_visible_child_name() == 'cards'
    view.toggle_mode()
    assert view._mode_stack.get_visible_child_name() == 'list'


def test_narrow_propagates_to_card_grid(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    view.set_narrow(True)
    assert view._card_grid.flowbox.get_min_children_per_line() == 1
    assert view._card_grid.flowbox.get_max_children_per_line() == 1


def test_narrow_propagates_to_releases_view(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    view._show_tag_releases(_make_tag())
    view.set_narrow(True)
    releases = view._nav_stack.get_child_by_name('releases')
    assert isinstance(releases, TagReleasesView)
    assert releases._grid.flowbox.get_min_children_per_line() == 1


def test_go_back_resets_state(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    view._show_tag_releases(_make_tag())
    assert view.in_releases is True
    view.go_back()
    assert view.in_releases is False
    assert view._current_tag is None


def test_go_back_fires_callback(mock_client, tmp_path):
    _setup_stores(tmp_path)
    called = []
    view = TagsView(client=mock_client)
    view.set_on_navigation_changed(lambda: called.append(True))
    view._show_tag_releases(_make_tag())
    called.clear()
    view.go_back()
    assert len(called) == 1


def test_in_releases_property(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    assert view.in_releases is False
    view._show_tag_releases(_make_tag())
    assert view.in_releases is True


def test_populate_creates_add_card(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    last = None
    child = view._card_grid.flowbox.get_first_child()
    while child:
        last = child
        child = child.get_next_sibling()
    assert last is not None
    assert hasattr(last, '_is_add_card') and last._is_add_card is True


def test_current_tag_name(mock_client, tmp_path):
    _setup_stores(tmp_path)
    view = TagsView(client=mock_client)
    assert view.current_tag_name == ''
    view._show_tag_releases(_make_tag(name='MyTag'))
    assert view.current_tag_name == 'MyTag'
