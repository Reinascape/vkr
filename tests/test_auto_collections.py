# SPDX-License-Identifier: GPL-3.0-or-later

import time

import pytest

from kitsune import tags_store
from kitsune.storage import auto_collections, watch_positions


@pytest.fixture
def isolated(mock_tags, mock_positions):
    """All auto_collections tests need both tags + watch_positions
    redirected to temp files; pull both fixtures together."""
    yield


def _set_pos(release_id, ordinal, pos, episode_id='e1', when=None):
    """Direct write into watch_positions storage to control timestamps."""
    entries = watch_positions._load()
    key = f'{release_id}_{ordinal}'
    entries[key] = {
        'pos': pos,
        'episode_id': episode_id,
        'updated_at': when if when is not None else time.time(),
    }
    watch_positions._save(entries)


def test_first_watch_untagged_release_auto_to_watching(isolated):
    actions = auto_collections.evaluate_position_change(42, 120.0)
    assert len(actions) == 1
    a = actions[0]
    assert a.type == 'auto'
    assert a.from_tag is None
    assert a.to_tag == 'watching'
    assert a.reason == 'first_watch'


def test_first_watch_already_watching_no_action(isolated):
    tags_store.add_release('watching', 99)
    actions = auto_collections.evaluate_position_change(99, 50.0)
    assert actions == []


def test_first_watch_already_watched_no_action_rewatch(isolated):
    tags_store.add_release('watched', 7)
    actions = auto_collections.evaluate_position_change(7, 30.0)
    assert actions == []


def test_first_watch_in_planned_suggests_move(isolated):
    tags_store.add_release('planned', 5)
    actions = auto_collections.evaluate_position_change(5, 10.0)
    assert len(actions) == 1
    a = actions[0]
    assert a.type == 'suggest'
    assert a.from_tag == 'planned'
    assert a.to_tag == 'watching'


def test_first_watch_in_postponed_suggests_move(isolated):
    tags_store.add_release('postponed', 5)
    actions = auto_collections.evaluate_position_change(5, 10.0)
    assert actions[0].type == 'suggest'
    assert actions[0].from_tag == 'postponed'


def test_completion_with_total_auto_to_watched(isolated):
    tags_store.add_release('watching', 100)
    _set_pos(100, 1, -1, 'e1')
    _set_pos(100, 2, -1, 'e2')
    _set_pos(100, 3, -1, 'e3')
    actions = auto_collections.evaluate_position_change(
        100, -1, release_meta={'episodes_total': 3, 'is_ongoing': False},
    )
    # Expect single 'auto' action: watching → watched
    assert any(
        a.type == 'auto' and a.to_tag == 'watched' for a in actions
    )


def test_completion_partial_progress_no_watched_move(isolated):
    tags_store.add_release('watching', 101)
    _set_pos(101, 1, -1, 'e1')
    _set_pos(101, 2, 500.0, 'e2')
    actions = auto_collections.evaluate_position_change(
        101, -1, release_meta={'episodes_total': 3, 'is_ongoing': False},
    )
    assert all(a.to_tag != 'watched' for a in actions)


def test_completion_ongoing_no_total_does_not_move(isolated):
    tags_store.add_release('watching', 102)
    _set_pos(102, 1, -1, 'e1')
    _set_pos(102, 2, -1, 'e2')
    actions = auto_collections.evaluate_position_change(
        102, -1, release_meta={'episodes_total': None, 'is_ongoing': True,
                                'episodes': [{'id': 'e1'}, {'id': 'e2'}]},
    )
    # is_ongoing=True without episodes_total — keep in Watching
    assert all(a.to_tag != 'watched' for a in actions)


def test_completion_finished_no_total_uses_episode_count(isolated):
    tags_store.add_release('watching', 103)
    _set_pos(103, 1, -1, 'e1')
    _set_pos(103, 2, -1, 'e2')
    actions = auto_collections.evaluate_position_change(
        103, -1, release_meta={'episodes_total': None, 'is_ongoing': False,
                                'episodes': [{'id': 'e1'}, {'id': 'e2'}]},
    )
    assert any(a.to_tag == 'watched' for a in actions)


def test_completion_chains_from_untagged(isolated):
    """Edge case: never tagged, marks last episode complete in one shot.
    Should produce TWO auto actions: untagged→watching, watching→watched.
    """
    _set_pos(104, 1, -1, 'e1')
    actions = auto_collections.evaluate_position_change(
        104, -1, release_meta={'episodes_total': 1, 'is_ongoing': False},
    )
    auto_actions = [a for a in actions if a.type == 'auto']
    targets = [a.to_tag for a in auto_actions]
    assert 'watching' in targets
    assert 'watched' in targets


def test_idle_30d_in_watching_auto_to_postponed(isolated):
    tags_store.add_release('watching', 200)
    long_ago = time.time() - 35 * 86400
    _set_pos(200, 1, 100.0, 'e1', when=long_ago)
    suggestions = auto_collections.scan_all()
    assert len(suggestions) == 1
    assert suggestions[0].type == 'auto'
    assert suggestions[0].to_tag == 'postponed'
    assert suggestions[0].reason == 'idle_30d'


def test_idle_180d_in_watching_auto_to_abandoned(isolated):
    tags_store.add_release('watching', 201)
    long_ago = time.time() - 200 * 86400
    _set_pos(201, 1, 100.0, 'e1', when=long_ago)
    suggestions = auto_collections.scan_all()
    assert len(suggestions) == 1
    assert suggestions[0].type == 'auto'
    assert suggestions[0].to_tag == 'abandoned'
    assert suggestions[0].reason == 'idle_180d'


def test_idle_180d_in_postponed_auto_to_abandoned(isolated):
    tags_store.add_release('postponed', 202)
    long_ago = time.time() - 200 * 86400
    _set_pos(202, 1, 100.0, 'e1', when=long_ago)
    suggestions = auto_collections.scan_all()
    assert len(suggestions) == 1
    assert suggestions[0].type == 'auto'
    assert suggestions[0].to_tag == 'abandoned'


def test_idle_recent_no_suggestion(isolated):
    tags_store.add_release('watching', 203)
    _set_pos(203, 1, 100.0, 'e1', when=time.time() - 5 * 86400)
    suggestions = auto_collections.scan_all()
    assert suggestions == []


def test_idle_postponed_under_180d_no_suggestion(isolated):
    tags_store.add_release('postponed', 204)
    _set_pos(204, 1, 100.0, 'e1', when=time.time() - 60 * 86400)
    suggestions = auto_collections.scan_all()
    assert suggestions == []


def test_last_activity_uses_max_across_episodes(isolated):
    older = time.time() - 100 * 86400
    newer = time.time() - 10 * 86400
    _set_pos(300, 1, 100.0, 'e1', when=older)
    _set_pos(300, 2, 200.0, 'e2', when=newer)
    assert abs(auto_collections._last_activity(300) - newer) < 1.0


def test_last_activity_none_for_unknown_release(isolated):
    assert auto_collections._last_activity(999) is None


# --- apply_action routing ---

class _SyncStub:
    """Minimal SyncManager stand-in tracking which write method was called."""

    def __init__(self):
        self.calls = []

    def move_collection(self, rid, from_tag, to_tag):
        self.calls.append(('move', rid, from_tag, to_tag))

    def add_to_tag_synced(self, tag, rid):
        self.calls.append(('add', tag, rid))

    def remove_from_tag_synced(self, tag, rid):
        self.calls.append(('remove', tag, rid))


def test_apply_action_transition_routes_through_move_collection(isolated):
    """A from→to auto action must use move_collection so the server
    gets a single ADD (collections are mutually exclusive). The previous
    DELETE+ADD pair would split-fail under backoff and leave the
    release in no collection server-side."""
    sync = _SyncStub()
    action = auto_collections.CollectionAction(
        type='auto', release_id=42, from_tag='watching',
        to_tag='watched', reason='all_episodes_watched',
    )
    auto_collections.apply_action(action, sync)
    assert sync.calls == [('move', 42, 'watching', 'watched')]


def test_apply_action_first_watch_uses_add_to_tag_synced(isolated):
    """No prior collection → plain add path (no DELETE to skip)."""
    sync = _SyncStub()
    action = auto_collections.CollectionAction(
        type='auto', release_id=42, from_tag=None,
        to_tag='watching', reason='first_watch',
    )
    auto_collections.apply_action(action, sync)
    assert sync.calls == [('add', 'watching', 42)]


def test_apply_action_suggest_is_a_noop(isolated):
    """Suggest actions are surfaced to the user as a toast, not applied
    automatically. apply_action must not touch the sync manager."""
    sync = _SyncStub()
    action = auto_collections.CollectionAction(
        type='suggest', release_id=42, from_tag='postponed',
        to_tag='watching', reason='resumed_watching',
    )
    auto_collections.apply_action(action, sync)
    assert sync.calls == []
