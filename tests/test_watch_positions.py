# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune import watch_positions as wp


def _setup_tmp(monkeypatch, tmp_path):
    f = tmp_path / 'watch_positions.json'
    monkeypatch.setattr(wp, '_POSITIONS_FILE', f)
    return f


def test_mark_completed(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(1, 1.0, 120.0)
    wp.mark_completed(1, 1.0)
    assert wp.get_position(1, 1.0) == -1


def test_mark_completed_creates_entry(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.mark_completed(1, 2.0)
    assert wp.get_position(1, 2.0) == -1


def test_get_all_for_release(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(1, 1.0, 60.0)
    wp.save_position(1, 2.0, 120.0)
    wp.mark_completed(1, 3.0)
    wp.save_position(2, 1.0, 30.0)  # different release

    result = wp.get_all_for_release(1)
    assert result == {1.0: 60.0, 2.0: 120.0, 3.0: -1}


def test_get_all_for_release_empty(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    result = wp.get_all_for_release(99)
    assert result == {}


def test_is_completed_minus_one():
    assert wp.is_completed(-1, 1440) is True


def test_is_completed_90_percent():
    # 1300 / 1440 = 90.3% → completed
    assert wp.is_completed(1300, 1440) is True


def test_is_completed_below_90_percent():
    # 1200 / 1440 = 83.3% → not completed
    assert wp.is_completed(1200, 1440) is False


def test_is_completed_zero():
    assert wp.is_completed(0, 1440) is False


def test_is_completed_no_duration():
    assert wp.is_completed(100, None) is False
    assert wp.is_completed(100, 0) is False


def test_is_completed_exact_90_percent():
    # pos = 1296, duration = 1440 → 1296 / 1440 = 0.9 exactly → completed
    assert wp.is_completed(1296, 1440) is True


def test_is_completed_short_episode():
    # 90s episode, 82s watched = 91% → completed
    assert wp.is_completed(82, 90) is True
    # 90s episode, 6s watched = 6.7% → not completed
    assert wp.is_completed(6, 90) is False


def test_is_completed_short_episode_minus_one():
    assert wp.is_completed(-1, 90) is True


def test_load_v1_format_migrates_lazily(monkeypatch, tmp_path):
    """v1 file (bare dict of floats) loads into v2 shape in memory."""
    f = _setup_tmp(monkeypatch, tmp_path)
    import json as _json
    f.write_text(_json.dumps({'42_1.0': 120.5, '42_2.0': -1}))
    assert wp.get_position(42, 1.0) == 120.5
    assert wp.get_position(42, 2.0) == -1
    entries = wp._load()
    assert entries['42_1.0']['pos'] == 120.5
    assert entries['42_1.0']['episode_id'] is None
    assert entries['42_1.0']['updated_at'] > 0


def test_load_v2_format_direct(monkeypatch, tmp_path):
    """v2 file loads with full entry shape preserved."""
    f = _setup_tmp(monkeypatch, tmp_path)
    import json as _json
    f.write_text(_json.dumps({
        'version': 2,
        'entries': {
            '42_1.0': {'pos': 120.5, 'episode_id': 'ep.0', 'updated_at': 1000.0},
        },
    }))
    assert wp.get_position(42, 1.0) == 120.5
    entries = wp._load()
    assert entries['42_1.0']['episode_id'] == 'ep.0'
    assert entries['42_1.0']['updated_at'] == 1000.0


def test_load_unknown_version_returns_empty(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    import json as _json
    f.write_text(_json.dumps({'version': 99, 'entries': {'42_1.0': {'pos': 120}}}))
    assert wp._load() == {}
    assert wp.get_count() == 0


def test_save_writes_v2_format(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 120.5)
    import json as _json
    raw = _json.loads(f.read_text())
    assert raw['version'] == 2
    assert 'entries' in raw
    assert raw['entries']['42_1.0']['pos'] == 120.5


def test_v1_to_v2_migration_preserves_on_next_save(monkeypatch, tmp_path):
    """v1 file + save → file rewritten as v2 with original entries intact."""
    f = _setup_tmp(monkeypatch, tmp_path)
    import json as _json
    f.write_text(_json.dumps({'42_1.0': 30.0, '42_2.0': -1}))
    wp.save_position(43, 1.0, 60.0)
    raw = _json.loads(f.read_text())
    assert raw['version'] == 2
    assert set(raw['entries'].keys()) == {'42_1.0', '42_2.0', '43_1.0'}
    assert raw['entries']['42_1.0']['pos'] == 30.0
    assert raw['entries']['42_1.0']['episode_id'] is None
    assert raw['entries']['43_1.0']['pos'] == 60.0


def test_save_position_with_episode_id(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 120.0, episode_id='ep.0')
    assert wp.get_episode_id(42, 1.0) == 'ep.0'


def test_save_position_without_episode_id_preserves_existing(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    wp.save_position(42, 1.0, 60.0)
    assert wp.get_episode_id(42, 1.0) == 'ep.0'
    assert wp.get_position(42, 1.0) == 60.0


def test_save_position_bumps_updated_at(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0)
    t1 = wp.get_updated_at(42, 1.0)
    import time as _time
    _time.sleep(0.01)
    wp.save_position(42, 1.0, 60.0)
    t2 = wp.get_updated_at(42, 1.0)
    assert t2 > t1


def test_get_episode_id_none_if_entry_missing(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    assert wp.get_episode_id(42, 1.0) is None


def test_get_updated_at_none_if_entry_missing(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    assert wp.get_updated_at(42, 1.0) is None


def test_iter_pushable_yields_entries_with_episode_id_only(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    wp.save_position(42, 2.0, 30.0)
    wp.mark_completed(43, 1.0, episode_id='ep.x')
    results = list(wp.iter_pushable())
    keys = {(rid, ord_) for rid, ord_, _ in results}
    assert keys == {(42, 1.0), (43, 1.0)}


def test_find_by_episode_id_scans_local_entries(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    wp.save_position(42, 2.0, 30.0, episode_id='ep.1')
    assert wp.find_by_episode_id('ep.1') == (42, 2.0)


def test_find_by_episode_id_unknown_returns_none(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    assert wp.find_by_episode_id('unknown') is None


def test_apply_server_entry_unmapped_when_episode_unknown(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    result = wp.apply_server_entry('ep.unknown', pos=30, is_watched=False,
                                    updated_at=1000.0)
    assert result == 'unmapped'


def test_apply_server_entry_applied_when_server_newer(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    local_ts = wp.get_updated_at(42, 1.0)
    result = wp.apply_server_entry(
        'ep.0', pos=60, is_watched=False, updated_at=local_ts + 100)
    assert result == 'applied'
    assert wp.get_position(42, 1.0) == 60


def test_apply_server_entry_skipped_when_local_newer(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    local_ts = wp.get_updated_at(42, 1.0)
    result = wp.apply_server_entry(
        'ep.0', pos=60, is_watched=False, updated_at=local_ts - 100)
    assert result == 'skipped'
    assert wp.get_position(42, 1.0) == 30.0


def test_apply_server_entry_is_watched_becomes_minus_one(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    local_ts = wp.get_updated_at(42, 1.0)
    result = wp.apply_server_entry(
        'ep.0', pos=60, is_watched=True, updated_at=local_ts + 100)
    assert result == 'applied'
    assert wp.get_position(42, 1.0) == -1


def test_apply_server_entry_equal_timestamps_skipped(monkeypatch, tmp_path):
    _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 30.0, episode_id='ep.0')
    local_ts = wp.get_updated_at(42, 1.0)
    result = wp.apply_server_entry(
        'ep.0', pos=60, is_watched=False, updated_at=local_ts)
    assert result == 'skipped'


def test_find_by_episode_id_falls_back_to_episode_index(
        monkeypatch, tmp_path):
    """If a server sends an episode_id never watched locally,
    find_by_episode_id consults episode_index."""
    from kitsune.storage import episode_index
    _setup_tmp(monkeypatch, tmp_path)
    idx_file = tmp_path / 'episode_index.json'
    monkeypatch.setattr(episode_index, '_INDEX_FILE', idx_file)
    monkeypatch.setattr(episode_index, '_cache', None)
    episode_index.add_from_release_data(
        9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    assert wp.find_by_episode_id('ep.0') == (9275, 1.0)


def test_apply_server_entry_resolves_via_episode_index(monkeypatch, tmp_path):
    """apply_server_entry uses index when episode is not locally known."""
    from kitsune.storage import episode_index
    _setup_tmp(monkeypatch, tmp_path)
    idx_file = tmp_path / 'episode_index.json'
    monkeypatch.setattr(episode_index, '_INDEX_FILE', idx_file)
    monkeypatch.setattr(episode_index, '_cache', None)
    episode_index.add_from_release_data(
        9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    result = wp.apply_server_entry(
        'ep.0', pos=120, is_watched=False, updated_at=1000.0)
    assert result == 'applied'
    assert wp.get_position(9275, 1.0) == 120
    assert wp.get_episode_id(9275, 1.0) == 'ep.0'


# --- Post-review robustness coverage (Stage 4 final) ---

def test_load_malformed_json_returns_empty(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    f.write_text('{not valid json')
    assert wp._load() == {}


def test_load_unknown_version_leaves_disk_unchanged(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    import json as _json
    original = _json.dumps({'version': 99, 'entries': {'42_1.0': {'pos': 120}}})
    f.write_text(original)
    # Read via _load — must not trigger any disk write
    assert wp._load() == {}
    # File content is bit-identical to before
    assert f.read_text() == original


def test_clear_all_removes_file_and_next_load_empty(monkeypatch, tmp_path):
    f = _setup_tmp(monkeypatch, tmp_path)
    wp.save_position(42, 1.0, 60.0)
    assert f.exists()
    wp.clear_all()
    assert not f.exists()
    assert wp._load() == {}


def test_v1_migrated_entry_overwritten_by_server_with_newer_timestamp(
        monkeypatch, tmp_path):
    """v1-migrated entry (no episode_id) + server sends data with episode_id
    for same (release_id, ordinal): server wins if its updated_at is newer.

    This is the real migration+sync interaction path: user upgraded, old
    v1 data became v2 with updated_at=mtime, then server sent a fresh
    timecode with a proper episode_id. The server's authoritative data
    should replace the stale local entry.
    """
    from kitsune.storage import episode_index
    f = _setup_tmp(monkeypatch, tmp_path)
    idx_file = tmp_path / 'episode_index.json'
    monkeypatch.setattr(episode_index, '_INDEX_FILE', idx_file)
    monkeypatch.setattr(episode_index, '_cache', None)
    # Write v1 file directly (simulating pre-upgrade state)
    import json as _json
    import os as _os
    f.write_text(_json.dumps({'9275_1.0': 30.0}))
    # Force an old mtime on the file (v1 entry "from the past")
    _os.utime(f, (1000.0, 1000.0))
    # Index knows the episode (release was opened after upgrade)
    episode_index.add_from_release_data(
        9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    # Server sends fresh data for this episode
    result = wp.apply_server_entry(
        'ep.0', pos=120, is_watched=False, updated_at=5000.0)
    assert result == 'applied'
    assert wp.get_position(9275, 1.0) == 120
    assert wp.get_episode_id(9275, 1.0) == 'ep.0'
    assert wp.get_updated_at(9275, 1.0) == 5000.0
