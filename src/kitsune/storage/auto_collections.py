# SPDX-License-Identifier: GPL-3.0-or-later

"""Automatic collection management.

Decides when to move a release between user-collection tags
(watching/watched/postponed/abandoned/planned) based on watch
behavior. The result is a list of `CollectionAction` records that the
caller (player or window) decides how to apply: 'auto' actions run
silently, 'suggest' actions are turned into a toast offering the move.

This module never touches sync_manager directly — that is the caller's
job, so the same code path can be exercised from tests with a stub.
"""

from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

from kitsune import tags_store
from kitsune.storage import _atomic_write_json, watch_positions

log = logging.getLogger('kitsune.auto_collections')

_USER_COLLECTION_TAGS = (
    'watching', 'watched', 'planned', 'postponed', 'abandoned',
)

_PAUSE_THRESHOLD_DAYS = 30
_ABANDON_THRESHOLD_DAYS = 180
_SCAN_INTERVAL_SECONDS = 86400  # 24h between full scans

_SCAN_FILE = Path(
    os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
) / 'kitsune' / 'auto_collections.json'


@dataclass
class CollectionAction:
    """A decided move for a single release.

    type:
        'auto'    — apply immediately, no UI prompt
        'suggest' — surface to the user as a toast with a Move button
    from_tag:
        Current collection tag, or None if release is not in any
        user-collection (only happens on the very first watch).
    to_tag:
        Target collection tag.
    reason:
        Stable identifier for the trigger; used for toast wording.
    """
    type: str
    release_id: int
    from_tag: str | None
    to_tag: str
    reason: str


def _current_user_collection(release_id: int) -> str | None:
    """Return the user-collection tag this release is in, or None.

    User collections are conceptually mutually exclusive. If a release
    somehow ends up in two (legacy state, race during sync), we return
    the first match by tuple order, which matches the natural lifecycle
    watching → watched → planned → postponed → abandoned.
    """
    for tag_id in _USER_COLLECTION_TAGS:
        if release_id in tags_store.get_release_ids_for_tag(tag_id):
            return tag_id
    return None


def _last_activity(release_id: int, entries: dict | None = None) -> float | None:
    """Max(updated_at) across all watch_positions entries for the release.

    Returns None if the release has no recorded watch activity yet.
    Delegates to `watch_positions.get_last_activity` so the storage
    schema details stay encapsulated in that module.
    """
    return watch_positions.get_last_activity(release_id, entries=entries)


def _is_complete(release_id: int, release_meta: dict | None) -> bool:
    """True if the user has marked enough episodes as fully watched.

    Prefers `episodes_total` from API metadata. Falls back to the
    cached episode list count, but only when the release is not
    ongoing — for ongoing shows we cannot know whether more episodes
    are coming, so completion is deferred until the show ends or the
    API reports a total.
    """
    if not release_meta:
        return False
    positions = watch_positions.get_all_for_release(release_id)
    completed = sum(1 for pos in positions.values() if pos == -1)
    if completed == 0:
        return False
    total = release_meta.get('episodes_total')
    if total is not None and total > 0:
        return completed >= total
    if release_meta.get('is_ongoing'):
        return False
    episodes = release_meta.get('episodes') or []
    if not episodes:
        return False
    return completed >= len(episodes)


def evaluate_position_change(
    release_id: int,
    pos: float,
    release_meta: dict | None = None,
) -> list[CollectionAction]:
    """Evaluate triggers fired by a single watch_positions update."""
    actions: list[CollectionAction] = []
    current = _current_user_collection(release_id)

    # Trigger 1 — started (or just made progress on) a release.
    # pos > 0 covers partial progress; pos == -1 covers "completed in
    # one shot" without a prior partial save.
    if pos > 0 or pos == -1:
        if current is None:
            actions.append(CollectionAction(
                type='auto', release_id=release_id, from_tag=None,
                to_tag='watching', reason='first_watch',
            ))
            # Simulate the post-action state so a one-shot
            # complete (untagged → watched) chains into Trigger 2.
            current = 'watching'
        elif current in ('planned', 'postponed', 'abandoned'):
            actions.append(CollectionAction(
                type='suggest', release_id=release_id, from_tag=current,
                to_tag='watching', reason='resumed_watching',
            ))
        # 'watching' / 'watched' → no action

    # Trigger 2 — finished an episode that completes the whole title.
    if pos == -1 and current == 'watching':
        if _is_complete(release_id, release_meta):
            actions.append(CollectionAction(
                type='auto', release_id=release_id, from_tag='watching',
                to_tag='watched', reason='all_episodes_watched',
            ))

    return actions


def evaluate_idle(
    release_id: int,
    current_tag: str,
    last_activity_ts: float,
    now: float,
) -> CollectionAction | None:
    """Return an idle-driven auto-move based on time since last activity."""
    days_idle = (now - last_activity_ts) / 86400.0
    if current_tag == 'watching':
        if days_idle >= _ABANDON_THRESHOLD_DAYS:
            return CollectionAction(
                type='auto', release_id=release_id, from_tag='watching',
                to_tag='abandoned', reason='idle_180d',
            )
        if days_idle >= _PAUSE_THRESHOLD_DAYS:
            return CollectionAction(
                type='auto', release_id=release_id, from_tag='watching',
                to_tag='postponed', reason='idle_30d',
            )
    elif current_tag == 'postponed':
        if days_idle >= _ABANDON_THRESHOLD_DAYS:
            return CollectionAction(
                type='auto', release_id=release_id, from_tag='postponed',
                to_tag='abandoned', reason='idle_180d',
            )
    return None


def scan_all() -> list[CollectionAction]:
    """Daily walk of Watching+Postponed for stale items.

    Preloads watch_positions once and reuses the snapshot across all
    releases — for a user with N releases this turns N file reads into
    1, making the scan effectively free even on large collections.
    """
    suggestions: list[CollectionAction] = []
    now = time.time()
    snap = watch_positions.snapshot()
    for tag_id in ('watching', 'postponed'):
        for rid in tags_store.get_release_ids_for_tag(tag_id):
            last = _last_activity(rid, entries=snap)
            if last is None:
                continue
            action = evaluate_idle(rid, tag_id, last, now)
            if action is not None:
                suggestions.append(action)
    return suggestions


def get_last_scan_time() -> float:
    try:
        with open(_SCAN_FILE) as f:
            return float(json.load(f).get('last_scan', 0.0))
    except (FileNotFoundError, json.JSONDecodeError, OSError, TypeError, ValueError):
        return 0.0


def record_scan_time(ts: float | None = None):
    if ts is None:
        ts = time.time()
    _atomic_write_json(_SCAN_FILE, {'last_scan': ts})


def should_scan_now() -> bool:
    """True if the previous scan is older than 24h (or never ran)."""
    return (time.time() - get_last_scan_time()) >= _SCAN_INTERVAL_SECONDS


def apply_action(action: CollectionAction, sync_manager) -> None:
    """Apply an 'auto' action via the sync manager (server + local).

    Uses move_collection for from→to transitions so the move reaches
    the server as a single ADD (AniLibria collections are mutually
    exclusive: the POST auto-evicts the prior entry). Without this,
    the previous remove+add pair could split-fail under backoff and
    leave the release in no collection server-side.
    """
    if action.type != 'auto':
        return
    if action.from_tag:
        sync_manager.move_collection(
            action.release_id, action.from_tag, action.to_tag)
    else:
        sync_manager.add_to_tag_synced(action.to_tag, action.release_id)
