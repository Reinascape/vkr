# SPDX-License-Identifier: GPL-3.0-or-later

"""Reverse index from episode_id to (release_id, ordinal).

Populated opportunistically by release_cache.save() when a release with
episodes lands. Used by watch_positions.find_by_episode_id() as a fallback
when a server-sent timecode arrives for an episode that was never watched
locally.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from kitsune.storage import _atomic_write_json

log = logging.getLogger('kitsune.episode_index')

_INDEX_FILE = Path(
    os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
) / 'kitsune' / 'episode_index.json'

VERSION = 1

_cache: dict | None = None


def _load() -> dict:
    """Return the index dict: {episode_id: {release_id, ordinal}}.

    Caches the result in memory for cheap lookups. Call `_cache = None`
    to force a reload (used by tests).
    """
    global _cache
    if _cache is not None:
        return _cache
    try:
        raw = json.loads(_INDEX_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        _cache = {}
        return _cache
    if not isinstance(raw, dict) or raw.get('version') != VERSION:
        log.warning(
            'episode_index.json has unsupported version %r, dropping contents',
            raw.get('version') if isinstance(raw, dict) else None)
        _cache = {}
        return _cache
    index = raw.get('index', {})
    _cache = index if isinstance(index, dict) else {}
    return _cache


def _save(index: dict):
    global _cache
    _cache = index
    _atomic_write_json(_INDEX_FILE, {'version': VERSION, 'index': index})


def add_from_release_data(release_id: int, data: dict):
    """Index every episode in a raw release dict (as from AniLibriaClient).

    Missing/empty `id` or missing `ordinal` entries are silently skipped.
    Existing episode_ids are overwritten (last write wins).
    """
    episodes = data.get('episodes') if isinstance(data, dict) else None
    if not episodes:
        return
    index = dict(_load())
    changed = False
    for ep in episodes:
        if not isinstance(ep, dict):
            continue
        ep_id = ep.get('id')
        ordinal = ep.get('ordinal')
        if not ep_id or ordinal is None:
            continue
        index[ep_id] = {
            'release_id': int(release_id),
            'ordinal': float(ordinal),
        }
        changed = True
    if changed:
        _save(index)


def lookup(episode_id: str):
    """Return (release_id, ordinal) for an episode_id, or None."""
    entry = _load().get(episode_id)
    if entry is None:
        return None
    return (entry['release_id'], entry['ordinal'])


def clear():
    """Wipe the index and remove the file."""
    global _cache
    _cache = {}
    if _INDEX_FILE.exists():
        _INDEX_FILE.unlink()
