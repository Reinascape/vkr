# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.storage import episode_index


def _setup_tmp(monkeypatch, tmp_path):
    f = tmp_path / 'episode_index.json'
    monkeypatch.setattr(episode_index, '_INDEX_FILE', f)
    monkeypatch.setattr(episode_index, '_cache', None)
    return f


def test_load_empty_file_returns_empty_lookup(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    assert episode_index.lookup('anything') is None


def test_add_from_release_data_indexes_all_episodes(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    release_data = {
        'episodes': [
            {'id': 'ep.0', 'ordinal': 1.0},
            {'id': 'ep.1', 'ordinal': 2.0},
            {'id': 'ep.2', 'ordinal': 3.0},
        ],
    }
    episode_index.add_from_release_data(9275, release_data)
    assert episode_index.lookup('ep.0') == (9275, 1.0)
    assert episode_index.lookup('ep.1') == (9275, 2.0)
    assert episode_index.lookup('ep.2') == (9275, 3.0)


def test_add_skips_episodes_without_id_or_ordinal(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    release_data = {
        'episodes': [
            {'id': 'ep.good', 'ordinal': 1.0},
            {'ordinal': 2.0},
            {'id': 'ep.no-ord'},
            {'id': '', 'ordinal': 3.0},
        ],
    }
    episode_index.add_from_release_data(9275, release_data)
    assert episode_index.lookup('ep.good') == (9275, 1.0)
    assert episode_index.lookup('ep.no-ord') is None


def test_add_handles_missing_episodes_key(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    episode_index.add_from_release_data(9275, {})
    assert episode_index.lookup('anything') is None


def test_add_overwrites_existing_episode_id(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    episode_index.add_from_release_data(100, {'episodes': [{'id': 'ep.x', 'ordinal': 1.0}]})
    episode_index.add_from_release_data(200, {'episodes': [{'id': 'ep.x', 'ordinal': 5.0}]})
    assert episode_index.lookup('ep.x') == (200, 5.0)


def test_persistence_roundtrip(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    episode_index.add_from_release_data(9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    monkeypatch.setattr(episode_index, '_cache', None)
    assert episode_index.lookup('ep.0') == (9275, 1.0)


def test_saved_file_has_version_and_index_keys(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    episode_index.add_from_release_data(9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    raw = json.loads(f.read_text())
    assert raw['version'] == 1
    assert 'index' in raw
    assert raw['index']['ep.0']['release_id'] == 9275
    assert raw['index']['ep.0']['ordinal'] == 1.0


def test_version_mismatch_drops_contents(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    f.write_text(json.dumps({'version': 99, 'index': {'ep.0': {'release_id': 9, 'ordinal': 1}}}))
    assert episode_index.lookup('ep.0') is None


def test_clear_removes_all_entries(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    episode_index.add_from_release_data(9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    assert episode_index.lookup('ep.0') == (9275, 1.0)
    episode_index.clear()
    assert episode_index.lookup('ep.0') is None
