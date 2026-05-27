# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path

from kitsune.storage import _atomic_write_json

log = logging.getLogger('kitsune.watch_positions')

_POSITIONS_FILE = Path(
    os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
) / 'kitsune' / 'watch_positions.json'

VERSION = 2

_cache: dict | None = None
_cache_path = None


def _read_from_disk() -> dict:
    """Parse the on-disk file into v2 entries shape. No caching."""
    try:
        raw = json.loads(_POSITIONS_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    if isinstance(raw, dict) and raw.get('version') == VERSION:
        entries = raw.get('entries', {})
        return entries if isinstance(entries, dict) else {}
    if isinstance(raw, dict) and 'version' in raw:
        log.warning(
            'watch_positions.json has unsupported version %r, dropping contents',
            raw.get('version'))
        return {}
    if isinstance(raw, dict):
        try:
            mtime = _POSITIONS_FILE.stat().st_mtime
        except FileNotFoundError:
            mtime = time.time()
        entries = {}
        for key, value in raw.items():
            if isinstance(value, (int, float)):
                entries[key] = {
                    'pos': float(value),
                    'episode_id': None,
                    'updated_at': mtime,
                }
        return entries
    return {}


def _load() -> dict:
    """Return entries dict in v2 shape, cached in-process.

    The cache holds the same dict object that subsequent _save calls
    persist, so in-place mutations between load and save remain
    consistent. The cache auto-invalidates when `_POSITIONS_FILE` is
    rebound (tests reassign it to tmp paths).
    """
    global _cache, _cache_path
    if _cache is None or _cache_path != _POSITIONS_FILE:
        _cache_path = _POSITIONS_FILE
        _cache = _read_from_disk()
    return _cache


def _save(entries: dict):
    global _cache, _cache_path
    _cache = entries
    _cache_path = _POSITIONS_FILE
    data = {'version': VERSION, 'entries': entries}
    _atomic_write_json(_POSITIONS_FILE, data)


def get_position(release_id: int, ordinal: float) -> float:
    entries = _load()
    entry = entries.get(f'{release_id}_{ordinal}')
    if entry is None:
        return 0
    return entry['pos']


def save_position(release_id: int, ordinal: float, position: float,
                  episode_id: str | None = None):
    entries = _load()
    key = f'{release_id}_{ordinal}'
    changed = False
    if position > 5:
        entries[key] = {
            'pos': round(position, 1),
            'episode_id': episode_id if episode_id is not None
                          else (entries.get(key, {}).get('episode_id')),
            'updated_at': time.time(),
        }
        changed = True
    elif key in entries:
        del entries[key]
        changed = True
    if changed:
        _save(entries)


def mark_completed(release_id: int, ordinal: float,
                   episode_id: str | None = None):
    """Mark episode as fully watched (stores -1 as position)."""
    entries = _load()
    key = f'{release_id}_{ordinal}'
    entries[key] = {
        'pos': -1,
        'episode_id': episode_id if episode_id is not None
                      else (entries.get(key, {}).get('episode_id')),
        'updated_at': time.time(),
    }
    _save(entries)


def remove_position(release_id: int, ordinal: float):
    entries = _load()
    key = f'{release_id}_{ordinal}'
    if key in entries:
        del entries[key]
        _save(entries)


def get_all_for_release(release_id: int) -> dict[float, float]:
    """Return {ordinal: position} for all episodes of a release.

    position > 0 means partially watched, -1 means completed.
    """
    entries = _load()
    prefix = f'{release_id}_'
    result = {}
    for key, entry in entries.items():
        if key.startswith(prefix):
            try:
                ordinal = float(key[len(prefix):])
                result[ordinal] = entry['pos']
            except (ValueError, TypeError, KeyError) as exc:
                log.warning('Skipping malformed watch_positions entry %r: %s', key, exc)
                continue
    return result


def get_count() -> int:
    return len(_load())


def get_completed_count() -> int:
    """Count episodes marked as fully watched (position == -1)."""
    return sum(1 for entry in _load().values() if entry.get('pos') == -1)


def get_size() -> int:
    try:
        return _POSITIONS_FILE.stat().st_size
    except FileNotFoundError:
        return 0


def clear_all():
    global _cache, _cache_path
    _cache = {}
    _cache_path = _POSITIONS_FILE
    if _POSITIONS_FILE.exists():
        _POSITIONS_FILE.unlink()


_WATCHED_FRACTION = 0.9  # 90% watched = completed


def is_completed(pos, duration):
    """True if episode is completed or watched >= 90%."""
    if pos == -1:
        return True
    if pos > 0 and duration and duration > 0 and pos >= duration * _WATCHED_FRACTION:
        return True
    return False


def get_episode_id(release_id: int, ordinal: float) -> str | None:
    entries = _load()
    entry = entries.get(f'{release_id}_{ordinal}')
    if entry is None:
        return None
    return entry.get('episode_id')


def get_updated_at(release_id: int, ordinal: float) -> float | None:
    entries = _load()
    entry = entries.get(f'{release_id}_{ordinal}')
    if entry is None:
        return None
    return entry.get('updated_at')


def get_last_activity(release_id: int, entries: dict | None = None) -> float | None:
    """Latest `updated_at` across all episodes of a release, or None.

    Accepts an optional preloaded entries snapshot so callers that walk
    many releases (e.g. auto_collections.scan_all) can avoid the file
    read on every call.
    """
    if entries is None:
        entries = _load()
    prefix = f'{release_id}_'
    best = 0.0
    for key, entry in entries.items():
        if not key.startswith(prefix):
            continue
        ts = entry.get('updated_at') or 0.0
        if ts > best:
            best = ts
    return best if best > 0 else None


def snapshot() -> dict:
    """Return a single in-memory copy of all entries.

    Use when batch-processing watch positions across many releases to
    avoid re-reading and re-parsing the JSON file per release.
    """
    return _load()


def iter_pushable():
    """Yield (release_id, ordinal, entry) for every entry with an episode_id.

    Used by Stage 5's timecode drain to push to the server.
    """
    entries = _load()
    for key, entry in entries.items():
        if not entry.get('episode_id'):
            continue
        prefix, _, ord_part = key.rpartition('_')
        try:
            release_id = int(prefix)
            ordinal = float(ord_part)
        except ValueError:
            log.warning('Skipping malformed watch_positions key %r in iter_pushable', key)
            continue
        yield (release_id, ordinal, entry)


def _find_key_by_episode_id(episode_id: str, entries: dict) -> str | None:
    """Internal: return the raw storage key (e.g. '42_1.0') for an episode_id.

    Scans the provided entries dict. Returns None if not found or if
    `episode_id` is falsy (guard against matching v1-migrated entries
    whose episode_id is None, and against '' from malformed server data).
    """
    if not episode_id:
        return None
    for key, entry in entries.items():
        if entry.get('episode_id') == episode_id:
            return key
    return None


def find_by_episode_id(episode_id: str):
    """Return (release_id, ordinal) for an episode_id, or None.

    Lookup order:
      1. Local watch_positions entries (episodes the user has opened).
      2. episode_index (populated by release_cache as releases load).

    An episode still returns None only if neither source knows about it.
    """
    entries = _load()
    key = _find_key_by_episode_id(episode_id, entries)
    if key is not None:
        prefix, _, ord_part = key.rpartition('_')
        try:
            return (int(prefix), float(ord_part))
        except ValueError:
            pass
    # Fallback to episode_index
    from kitsune.storage import episode_index
    return episode_index.lookup(episode_id)


def apply_server_entry(episode_id: str, pos: float, is_watched: bool,
                       updated_at: float) -> str:
    """Apply a timecode from the server, respecting local-wins-on-tie.

    Returns one of:
      - 'applied': server state was newer; local entry was updated
      - 'skipped': local state was newer-or-equal; no change
      - 'unmapped': could not resolve episode_id to a (release_id, ordinal)
    """
    entries = _load()
    key = _find_key_by_episode_id(episode_id, entries)
    if key is None:
        # Fall back to episode_index for an episode that was never watched
        # locally. If the index knows it, we'll create a new entry.
        from kitsune.storage import episode_index
        mapping = episode_index.lookup(episode_id)
        if mapping is None:
            return 'unmapped'
        release_id, ordinal = mapping
        key = f'{release_id}_{ordinal}'
    existing = entries.get(key, {})
    local_ts = existing.get('updated_at', 0.0)
    if local_ts >= updated_at:
        return 'skipped'
    new_pos = -1 if is_watched else float(pos)
    entries[key] = {
        'pos': new_pos,
        'episode_id': episode_id,
        'updated_at': updated_at,
    }
    _save(entries)
    return 'applied'
