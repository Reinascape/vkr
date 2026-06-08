# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune import tags_store


def _use_temp_file(tmp_path):
    """Redirect storage to a temp file for testing."""
    f = tmp_path / 'tags.json'
    tags_store._TAGS_FILE = f
    return f


def test_initial_state_has_favorites(tmp_path):
    _use_temp_file(tmp_path)
    tags = tags_store.get_all_tags()
    ids = [t['id'] for t in tags]
    assert 'favorites' in ids
    fav = next(t for t in tags if t['id'] == 'favorites')
    assert fav['builtin'] is True


def test_create_emoji_tag(tmp_path):
    _use_temp_file(tmp_path)
    tag = tags_store.create_tag('Топ сезона', 'emoji', '🔥')
    assert tag['name'] == 'Топ сезона'
    assert tag['icon_type'] == 'emoji'
    assert tag['icon_value'] == '🔥'
    assert tag['builtin'] is False
    assert len(tag['id']) == 8


def test_create_color_tag(tmp_path):
    _use_temp_file(tmp_path)
    tag = tags_store.create_tag('Романтика', 'color', 'pink')
    assert tag['icon_type'] == 'color'
    assert tag['icon_value'] == 'pink'


def test_delete_custom_tag(tmp_path):
    _use_temp_file(tmp_path)
    tag = tags_store.create_tag('Temp', 'emoji', '💎')
    tags_store.delete_tag(tag['id'])
    ids = [t['id'] for t in tags_store.get_all_tags()]
    assert tag['id'] not in ids


def test_cannot_delete_favorites(tmp_path):
    _use_temp_file(tmp_path)
    tags_store.delete_tag('favorites')
    assert any(t['id'] == 'favorites' for t in tags_store.get_all_tags())


def test_add_release_to_tag(tmp_path):
    _use_temp_file(tmp_path)
    tags_store.add_release('favorites', 42)
    tags = tags_store.get_all_tags()
    fav = [t for t in tags if t['id'] == 'favorites'][0]
    assert 42 in fav['releases']


def test_remove_release_from_tag(tmp_path):
    _use_temp_file(tmp_path)
    tags_store.add_release('favorites', 42)
    tags_store.remove_release('favorites', 42)
    tags = tags_store.get_all_tags()
    fav = [t for t in tags if t['id'] == 'favorites'][0]
    assert 42 not in fav['releases']


def test_get_tags_for_release(tmp_path):
    _use_temp_file(tmp_path)
    tag = tags_store.create_tag('Test', 'color', 'blue')
    tags_store.add_release('favorites', 100)
    tags_store.add_release(tag['id'], 100)
    result = tags_store.get_tags_for_release(100)
    assert len(result) == 2
    ids = [t['id'] for t in result]
    assert 'favorites' in ids
    assert tag['id'] in ids


def test_get_tags_for_release_empty(tmp_path):
    _use_temp_file(tmp_path)
    result = tags_store.get_tags_for_release(999)
    assert result == []


def test_is_favorited(tmp_path):
    _use_temp_file(tmp_path)
    assert tags_store.is_favorited(42) is False
    tags_store.add_release('favorites', 42)
    assert tags_store.is_favorited(42) is True


def test_toggle_favorite(tmp_path):
    _use_temp_file(tmp_path)
    tags_store.toggle_favorite(42)
    assert tags_store.is_favorited(42) is True
    tags_store.toggle_favorite(42)
    assert tags_store.is_favorited(42) is False


def test_get_release_ids_for_tag(tmp_path):
    _use_temp_file(tmp_path)
    tags_store.add_release('favorites', 1)
    tags_store.add_release('favorites', 2)
    ids = tags_store.get_release_ids_for_tag('favorites')
    assert set(ids) == {1, 2}


def test_stats(tmp_path):
    _use_temp_file(tmp_path)
    initial = tags_store.get_count()
    assert initial == 6
    tags_store.create_tag('A', 'emoji', '🎯')
    assert tags_store.get_count() == initial + 1


def test_clear_all(tmp_path):
    _use_temp_file(tmp_path)
    tags_store.create_tag('A', 'emoji', '🎯')
    tags_store.add_release('favorites', 42)
    tags_store.clear_all()
    tags = tags_store.get_all_tags()
    assert len(tags) == 6
    for tag in tags:
        assert tag['releases'] == []


def test_builtin_collection_tags_exist(mock_tags):
    tags = tags_store.get_all_tags()
    ids = [t['id'] for t in tags]
    assert 'favorites' in ids
    assert 'watching' in ids
    assert 'watched' in ids
    assert 'planned' in ids
    assert 'postponed' in ids
    assert 'abandoned' in ids


def test_cannot_delete_builtin_tags(mock_tags):
    for tag_id in ('favorites', 'watching', 'watched', 'planned', 'postponed', 'abandoned'):
        tags_store.delete_tag(tag_id)
    tags = tags_store.get_all_tags()
    ids = [t['id'] for t in tags]
    assert 'favorites' in ids
    assert 'watching' in ids


def test_builtin_tags_have_colors(mock_tags):
    tags = tags_store.get_all_tags()
    for tag in tags:
        if tag.get('builtin'):
            assert 'color' in tag
