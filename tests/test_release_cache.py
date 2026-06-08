# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.storage import release_cache


def _setup_tmp(monkeypatch, tmp_path):
    d = tmp_path / 'releases'
    d.mkdir()
    monkeypatch.setattr(release_cache, '_CACHE_DIR', d)
    return d


def test_get_returns_none_for_missing(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    assert release_cache.get(999) is None


def test_get_returns_none_when_dir_missing(monkeypatch, tmp_path):
    d = tmp_path / 'nonexistent' / 'releases'
    monkeypatch.setattr(release_cache, '_CACHE_DIR', d)
    assert release_cache.get(1) is None


@patch('kitsune.storage.search_index.index_release')
def test_save_and_get_roundtrip(mock_idx, monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    data = {'id': 42, 'name': {'main': 'Test'}}
    release_cache.save(42, data)
    result = release_cache.get(42)
    assert result == data


@patch('kitsune.storage.search_index.index_release')
def test_save_overwrites(mock_idx, monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(42, {'v': 1})
    release_cache.save(42, {'v': 2})
    assert release_cache.get(42) == {'v': 2}


@patch('kitsune.storage.search_index.index_release')
def test_save_coerces_id_to_int(mock_idx, monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    release_cache.save('42', {'id': 42})
    assert release_cache.get(42) is not None


@patch('kitsune.storage.search_index.index_release')
def test_get_count_empty(mock_idx, monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    assert release_cache.get_count() == 0


@patch('kitsune.storage.search_index.index_release')
def test_get_count(mock_idx, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    release_cache.save(2, {'id': 2})
    release_cache.save(3, {'id': 3})
    assert release_cache.get_count() == 3


def test_get_count_no_dir(monkeypatch, tmp_path):
    d = tmp_path / 'nonexistent'
    monkeypatch.setattr(release_cache, '_CACHE_DIR', d)
    assert release_cache.get_count() == 0


@patch('kitsune.storage.search_index.index_release')
def test_get_count_ignores_non_json(mock_idx, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    (d / 'readme.txt').write_text('not a cache file')
    assert release_cache.get_count() == 1


@patch('kitsune.storage.search_index.index_release')
def test_get_size_empty(mock_idx, monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    assert release_cache.get_size() == 0


@patch('kitsune.storage.search_index.index_release')
def test_get_size(mock_idx, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    release_cache.save(2, {'id': 2})
    expected = sum(f.stat().st_size for f in d.iterdir() if f.suffix == '.json')
    assert release_cache.get_size() == expected
    assert release_cache.get_size() > 0


def test_get_size_no_dir(monkeypatch, tmp_path):
    d = tmp_path / 'nonexistent'
    monkeypatch.setattr(release_cache, '_CACHE_DIR', d)
    assert release_cache.get_size() == 0


@patch('kitsune.storage.search_index.index_release')
def test_get_size_ignores_non_json(mock_idx, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    (d / 'notes.txt').write_text('x' * 1000)
    size_with_txt = release_cache.get_size()
    (d / 'notes.txt').unlink()
    size_without_txt = release_cache.get_size()
    assert size_with_txt == size_without_txt


@patch('kitsune.storage.search_index.clear_releases')
@patch('kitsune.storage.search_index.index_release')
def test_clear_all(mock_idx, mock_clear, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    release_cache.save(2, {'id': 2})
    assert release_cache.get_count() == 2
    release_cache.clear_all()
    assert release_cache.get_count() == 0
    assert release_cache.get(1) is None
    assert release_cache.get(2) is None


@patch('kitsune.storage.search_index.clear_releases')
@patch('kitsune.storage.search_index.index_release')
def test_clear_all_preserves_non_json(mock_idx, mock_clear, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    (d / 'notes.txt').write_text('keep me')
    release_cache.clear_all()
    assert release_cache.get_count() == 0
    assert (d / 'notes.txt').exists()


@patch('kitsune.storage.search_index.clear_releases')
def test_clear_all_no_dir(mock_clear, monkeypatch, tmp_path):
    d = tmp_path / 'nonexistent'
    monkeypatch.setattr(release_cache, '_CACHE_DIR', d)
    release_cache.clear_all()  # should not raise


@patch('kitsune.storage.search_index.index_release')
def test_save_calls_search_index(mock_idx, monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    data = {'id': 42, 'name': {'main': 'Test'}}
    release_cache.save(42, data)
    mock_idx.assert_called_once_with(42, data)


@patch('kitsune.storage.search_index.clear_releases')
@patch('kitsune.storage.search_index.index_release')
def test_clear_all_calls_search_index(mock_idx, mock_clear, monkeypatch, tmp_path):
    d = _setup_tmp(monkeypatch, tmp_path)
    release_cache.save(1, {'id': 1})
    release_cache.clear_all()
    mock_clear.assert_called_once()


def test_save_populates_episode_index(monkeypatch, tmp_path):
    """release_cache.save should push episode ids into episode_index."""
    from kitsune.storage import episode_index
    f = tmp_path / 'episode_index.json'
    monkeypatch.setattr(episode_index, '_INDEX_FILE', f)
    monkeypatch.setattr(episode_index, '_cache', None)
    release_data = {
        'id': 9275,
        'episodes': [
            {'id': 'ep.0', 'ordinal': 1.0},
            {'id': 'ep.1', 'ordinal': 2.0},
        ],
    }
    from kitsune import release_cache
    release_cache.save(9275, release_data)
    assert episode_index.lookup('ep.0') == (9275, 1.0)
    assert episode_index.lookup('ep.1') == (9275, 2.0)
