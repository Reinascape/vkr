# SPDX-License-Identifier: GPL-3.0-or-later

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest

from kitsune.storage.sync_manager import SyncManager, MergeStrategy
from kitsune.storage import tags_store

sys.path.insert(0, os.path.dirname(__file__))

from fakes.fake_api_client import FakeApiClient
from kitsune.storage.pending_queue import (
    PendingQueue, OP_ADD_FAVORITE, OP_REMOVE_FAVORITE,
    OP_ADD_COLLECTION, OP_REMOVE_COLLECTION,
)


@pytest.fixture(autouse=True)
def _isolate_pending_queue(mock_pending_queue):
    """Every SyncManager(client) instance in this module loads PendingQueue.
    Without isolation the dev user's real ~/.local/share/kitsune/pending_ops.json
    leaks in and skews assertions like `sm.queue_size() == 0`.
    """
    pass


class FakeSyncClient:
    def __init__(self):
        self.server_favorites = [10, 20, 30]
        self.server_collections = [
            {'release_id': 10, 'type_of_collection': 'WATCHING'},
            {'release_id': 40, 'type_of_collection': 'WATCHED'},
        ]
        self.pushed_favorites = []
        self.removed_favorites = []
        self.pushed_collections = []
        self._get_token = lambda: 'test-token'

    def get_favorite_ids(self, callback=None):
        callback(self.server_favorites, None)

    def get_collection_ids(self, callback=None):
        callback(self.server_collections, None)

    def add_favorites(self, release_ids, callback=None):
        self.pushed_favorites.extend(release_ids)
        if callback:
            callback(None, None)

    def remove_favorites(self, release_ids, callback=None):
        self.removed_favorites.extend(release_ids)
        if callback:
            callback(None, None)

    def add_to_collection(self, release_id, collection_type, callback=None):
        self.pushed_collections.append((release_id, collection_type))
        if callback:
            callback(None, None)

    def remove_from_collection(self, release_ids, callback=None):
        if callback:
            callback(None, None)

    def get_timecodes(self, since=None, callback=None):
        callback([], None)

    def save_timecodes(self, timecodes, callback=None):
        if callback:
            callback(None, None)


# --- Merge strategy tests ---

def test_merge_strategy_default(mock_tags):
    tags_store.add_release('favorites', 99)
    client = FakeSyncClient()
    sm = SyncManager(client)

    sm.initial_sync(lambda ok, err: None)

    local_favs = tags_store.get_release_ids_for_tag('favorites')
    assert 10 in local_favs  # from server
    assert 99 in local_favs  # kept local
    assert 99 in client.pushed_favorites  # pushed to server


def test_prefer_server(mock_tags):
    tags_store.add_release('favorites', 99)
    client = FakeSyncClient()
    sm = SyncManager(client)

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_SERVER)

    local_favs = tags_store.get_release_ids_for_tag('favorites')
    assert 10 in local_favs
    assert 99 not in local_favs  # local-only removed
    assert len(client.pushed_favorites) == 0  # nothing pushed


def test_prefer_local(mock_tags):
    tags_store.add_release('favorites', 99)
    client = FakeSyncClient()
    sm = SyncManager(client)

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_LOCAL)

    local_favs = tags_store.get_release_ids_for_tag('favorites')
    assert 99 in local_favs  # local kept
    assert 99 in client.pushed_favorites  # pushed to server
    # Server-only items removed from server
    assert set(client.removed_favorites) == {10, 20, 30}


def test_merge_collections(mock_tags):
    client = FakeSyncClient()
    sm = SyncManager(client)
    sm.initial_sync(lambda ok, err: None)

    assert 10 in tags_store.get_release_ids_for_tag('watching')
    assert 40 in tags_store.get_release_ids_for_tag('watched')


def test_sync_sets_last_sync_time(mock_tags):
    client = FakeSyncClient()
    sm = SyncManager(client)
    assert sm.get_last_sync_time() is None
    sm.initial_sync(lambda ok, err: None)
    assert sm.get_last_sync_time() is not None


# --- Server counts ---

def test_fetch_server_counts(mock_tags):
    client = FakeSyncClient()
    sm = SyncManager(client)

    results = []
    sm.fetch_server_counts(lambda counts, err: results.append(counts))

    assert results[0]['favorites'] == 3
    assert results[0]['collections']['watching'] == 1
    assert results[0]['collections']['watched'] == 1


def test_fetch_server_counts_parses_list_of_lists(mock_tags):
    """Live AniLibria /collections/ids returns [[rid, type], ...], not
    [{release_id, type_of_collection}, ...]. Old fixtures kept the dict
    shape; the parser must handle both so production sync isn't a no-op."""
    client = FakeSyncClient()
    client.server_collections = [
        [10, 'WATCHING'],
        [40, 'WATCHED'],
        [50, 'WATCHED'],
    ]
    sm = SyncManager(client)

    results = []
    sm.fetch_server_counts(lambda counts, err: results.append(counts))

    assert results[0]['collections']['watching'] == 1
    assert results[0]['collections']['watched'] == 2


def test_initial_sync_parses_list_of_lists(mock_tags):
    """Regression: _sync_collections was assuming list-of-dicts shape,
    so against the real server it would silently drop every entry and
    leave local collections empty. List-of-lists must be accepted."""
    client = FakeSyncClient()
    client.server_favorites = []
    client.server_collections = [
        [9275, 'WATCHING'],
        [10089, 'WATCHED'],
    ]
    sm = SyncManager(client)
    sm.initial_sync(lambda ok, err: None)

    assert 9275 in tags_store.get_release_ids_for_tag('watching')
    assert 10089 in tags_store.get_release_ids_for_tag('watched')


# --- Syncing state ---

def test_syncing_flag(mock_tags):
    client = FakeSyncClient()
    sm = SyncManager(client)
    assert not sm.is_syncing
    # After sync completes (synchronous fake), flag is cleared
    sm.initial_sync(lambda ok, err: None)
    assert not sm.is_syncing


def test_sync_manager_exposes_pending_queue():
    client = FakeSyncClient()
    sm = SyncManager(client)
    # The queue attribute exists and starts empty; actual usage comes in Stage 2.
    assert hasattr(sm, '_queue')
    assert sm._queue.size() == 0


# --- Pub/sub and accessor tests (Stage 2) ---

def test_connect_sync_error_fires_on_emit():
    client = FakeSyncClient()
    sm = SyncManager(client)
    received = []
    sm.connect_sync_error(lambda op, rid, err: received.append((op, rid, err)))
    sm._emit_sync_error('add_favorite', 9275, 'timeout')
    assert received == [('add_favorite', 9275, 'timeout')]


def test_connect_queue_changed_fires_on_emit():
    client = FakeSyncClient()
    sm = SyncManager(client)
    received = []
    sm.connect_queue_changed(lambda size: received.append(size))
    sm._emit_queue_changed()
    assert received == [0]


def test_connect_sync_complete_fires_on_emit():
    client = FakeSyncClient()
    sm = SyncManager(client)
    received = []
    sm.connect_sync_complete(lambda ok: received.append(ok))
    sm._emit_sync_complete(True)
    assert received == [True]


def test_set_user_id():
    client = FakeSyncClient()
    sm = SyncManager(client)
    assert sm._user_id == 0
    sm.set_user_id(42)
    assert sm._user_id == 42


def test_queue_size_delegates():
    client = FakeSyncClient()
    sm = SyncManager(client)
    assert sm.queue_size() == 0


def test_queue_has_errors_delegates():
    client = FakeSyncClient()
    sm = SyncManager(client)
    assert sm.queue_has_errors() is False


def test_last_queue_error_delegates():
    client = FakeSyncClient()
    sm = SyncManager(client)
    assert sm.last_queue_error() is None


# --- Drain tests (Stage 2) ---

def _make_sm_with_fake(tmp_path):
    """Helper: SyncManager with FakeApiClient and tmp queue."""
    client = FakeApiClient()
    sm = SyncManager(client)
    # Redirect queue to tmp to avoid touching real user dir
    sm._queue = PendingQueue(tmp_path / 'pending_ops.json')
    sm.set_user_id(42)
    return sm, client


def test_drain_dispatches_add_favorite(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._drain_queue()
    assert client.call_log == [('add_favorites', [9275])]
    client.flush_all()
    assert sm._queue.size() == 0


def test_drain_dispatches_remove_favorite(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    sm._drain_queue()
    assert client.call_log == [('remove_favorites', [9275])]
    client.flush_all()
    assert sm._queue.size() == 0


def test_drain_dispatches_add_collection(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    sm._drain_queue()
    assert client.call_log == [('add_to_collection', 9275, 'WATCHING')]
    client.flush_all()
    assert sm._queue.size() == 0


def test_drain_dispatches_remove_collection(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_REMOVE_COLLECTION, 9275, user_id=42)
    sm._drain_queue()
    assert client.call_log == [('remove_from_collection', [9275])]
    client.flush_all()
    assert sm._queue.size() == 0


def test_drain_emits_queue_changed_on_success(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sizes = []
    sm.connect_queue_changed(lambda s: sizes.append(s))
    sm._drain_queue()
    client.flush_all()
    assert 0 in sizes


def test_drain_emits_sync_error_on_failure(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    errors = []
    sm.connect_sync_error(lambda op, rid, err: errors.append((op, rid, err)))
    sm._drain_queue()
    client.fail_next('server 500')
    assert len(errors) == 1
    assert errors[0] == ('add_favorite', 9275, 'server 500')
    assert sm._queue.size() == 1  # op still in queue for retry


def test_drain_reentrancy_guard(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._draining = True  # simulate already draining
    sm._drain_queue()
    assert client.call_log == []  # nothing dispatched


def test_drain_chains_multiple_ops(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    sm._drain_queue()
    # First op dispatched
    assert len(client.call_log) == 1
    client.flush_next()  # first succeeds → second dispatched
    assert len(client.call_log) == 2
    client.flush_next()  # second succeeds
    assert sm._queue.size() == 0


# --- Write-through tests (updated for queue routing) ---

def test_toggle_favorite_synced_enqueues_and_drains(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)

    result = sm.toggle_favorite_synced(42)
    assert result is True
    assert tags_store.is_favorited(42)
    # Op is in queue, not dispatched yet (idle-scheduled)
    assert sm._queue.size() == 1
    assert client.call_log == []
    # Drain and flush to simulate real async cycle
    sm._drain_queue()
    client.flush_all()
    assert ('add_favorites', [42]) in client.call_log
    assert sm._queue.size() == 0

    result = sm.toggle_favorite_synced(42)
    assert result is False
    assert not tags_store.is_favorited(42)
    sm._drain_queue()
    client.flush_all()
    assert ('remove_favorites', [42]) in client.call_log


def test_add_to_collection_synced_enqueues(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)

    sm.add_to_tag_synced('watching', 55)
    assert 55 in tags_store.get_release_ids_for_tag('watching')
    assert sm._queue.size() == 1
    sm._drain_queue()
    client.flush_all()
    assert ('add_to_collection', 55, 'WATCHING') in client.call_log


def test_remove_from_tag_synced_enqueues(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.add_release('watching', 55)

    sm.remove_from_tag_synced('watching', 55)
    assert 55 not in tags_store.get_release_ids_for_tag('watching')
    assert sm._queue.size() == 1
    sm._drain_queue()
    client.flush_all()
    assert ('remove_from_collection', [55]) in client.call_log


def test_write_through_schedules_drain(mock_tags, tmp_path, monkeypatch):
    """Verify that write-through calls GLib.idle_add to schedule drain."""
    sm, client = _make_sm_with_fake(tmp_path)
    scheduled = []
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: scheduled.append(fn),
    )
    sm.add_to_tag_synced('favorites', 9275)
    assert len(scheduled) == 1  # _schedule_drain called once


def test_write_through_not_logged_in_skips_enqueue(mock_tags, tmp_path):
    """If not logged in, local change happens but no enqueue."""
    sm, client = _make_sm_with_fake(tmp_path)
    sm._client._get_token = lambda: None  # simulate not logged in
    sm.add_to_tag_synced('favorites', 9275)
    assert tags_store.is_favorited(9275)  # local still applies
    assert sm._queue.size() == 0  # not enqueued


def test_write_through_does_not_double_schedule(mock_tags, tmp_path, monkeypatch):
    """Two write-throughs before the idle fires only schedule one drain."""
    sm, client = _make_sm_with_fake(tmp_path)
    scheduled = []
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: scheduled.append(fn),
    )
    sm.add_to_tag_synced('favorites', 9275)
    sm.add_to_tag_synced('favorites', 9276)
    assert len(scheduled) == 1  # second write-through suppresses double schedule


def test_write_through_custom_tag_skips_enqueue(mock_tags, tmp_path):
    """Custom (non-synced) tags apply locally but are not enqueued."""
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.create_tag('Custom Test', 'emoji', '🔥')
    custom_id = [t['id'] for t in tags_store.get_all_tags()
                 if not t.get('builtin')][0]
    sm.add_to_tag_synced(custom_id, 9275)
    assert 9275 in tags_store.get_release_ids_for_tag(custom_id)
    assert sm._queue.size() == 0  # custom tag not enqueued


# --- Retry timer tests (Stage 2) ---

def test_force_drain_resets_retries_and_drains(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    op_id = sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._queue.mark_failure(op_id, 'timeout')
    # Op is now in backoff — peek_ready should not return it for 10s
    assert sm._queue.peek_ready(time.time()) == []
    # force_drain resets retries and drains immediately
    sm.force_drain()
    assert len(client.call_log) == 1
    client.flush_all()
    assert sm._queue.size() == 0


def test_retry_tick_schedules_drain_when_ready(tmp_path, mock_tags, monkeypatch):
    sm, client = _make_sm_with_fake(tmp_path)
    op_id = sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    # Make op immediately ready (bypass backoff)
    sm._queue._ops[0].next_retry_at = 0.0
    sm._queue._ops[0].attempt_count = 1
    scheduled = []
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: scheduled.append(fn),
    )
    result = sm._retry_tick()
    assert result is True  # GLib.SOURCE_CONTINUE
    assert len(scheduled) == 1  # drain was scheduled


def test_retry_tick_noop_when_queue_empty(tmp_path, mock_tags, monkeypatch):
    sm, client = _make_sm_with_fake(tmp_path)
    scheduled = []
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: scheduled.append(fn),
    )
    result = sm._retry_tick()
    assert result is True
    assert len(scheduled) == 0


# --- E6 regression test: tag_popover._on_tag_created (Stage 2 Task 6) ---

def test_add_to_tag_synced_custom_tag_does_not_enqueue(mock_tags, tmp_path):
    """Custom (non-synced) tags go through tags_store directly, not queue.

    This is the invariant that the tag_popover fix must preserve: synced
    built-in tags go through sync_manager, custom tags don't.
    """
    sm, client = _make_sm_with_fake(tmp_path)
    # Create a custom tag
    tags_store.create_tag('Custom Test', 'emoji', '🔥')
    custom_tags = [t for t in tags_store.get_all_tags()
                   if not t.get('builtin')]
    assert len(custom_tags) >= 1
    custom_id = custom_tags[0]['id']
    sm.add_to_tag_synced(custom_id, 9275)
    assert 9275 in tags_store.get_release_ids_for_tag(custom_id)
    assert sm._queue.size() == 0  # custom tag — NOT enqueued


# --- Robustness tests (post-review fixes) ---

def test_drain_skips_unknown_op_kinds_without_dropping(tmp_path, mock_tags):
    """Unknown op kinds are skipped, not silently deleted.

    If a newer version enqueued an op kind this binary doesn't know about
    (e.g. after a downgrade), we must not discard the op — leave it in the
    queue so a future upgrade can process it.
    """
    from kitsune.storage.pending_queue import Op
    import uuid, time as _time
    sm, client = _make_sm_with_fake(tmp_path)
    # Inject an unknown op directly
    sm._queue._ops.append(Op(
        id=str(uuid.uuid4()), op='future_op_kind',
        release_id=9275, user_id=42, payload={},
        created_at=_time.time(),
    ))
    # Also enqueue a known op after it
    sm._queue.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    assert sm._queue.size() == 2

    sm._drain_queue()
    client.flush_all()

    # Known op was dispatched and removed; unknown op still in queue
    assert ('add_favorites', [9276]) in client.call_log
    assert sm._queue.size() == 1
    assert sm._queue._ops[0].op == 'future_op_kind'


def test_drain_recovers_after_mark_success_raises(tmp_path, mock_tags, monkeypatch):
    """If mark_success raises (e.g. disk full), _draining is reset.

    Without this safety net, a single disk error would permanently dead-
    lock the sync pipeline for the session.
    """
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._drain_queue()
    # Make mark_success raise on the in-flight op
    original = sm._queue.mark_success

    def boom(op_id):
        raise OSError('disk full')

    monkeypatch.setattr(sm._queue, 'mark_success', boom)
    # Fire the callback — this triggers _on_op_result which calls the
    # now-broken mark_success
    client.flush_next()

    # The guard must have reset so future drains can proceed
    assert sm._draining is False

    # Restore and verify recovery by draining again
    monkeypatch.setattr(sm._queue, 'mark_success', original)
    # Queue still has the op (mark_success never completed)
    # On next drain, the in-flight flag is still set from the failed attempt,
    # so peek_ready returns nothing — verify the drain loop is not stuck
    sm._drain_queue()
    assert sm._draining is False


def test_force_drain_during_in_flight_still_effective(tmp_path, mock_tags):
    """force_drain while a drain is in-flight: reset_all_retries still applies.

    Scenario: user clicks 'Retry now' while the retry timer already kicked
    off a drain. The direct _drain_queue call hits the reentrancy guard,
    but reset_all_retries() has already zeroed next_retry_at on every op,
    so the in-progress drain's next _drain_next iteration will pick up any
    ops that were previously in backoff.
    """
    sm, client = _make_sm_with_fake(tmp_path)
    op_a = sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    op_b = sm._queue.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    # Put op_a in-flight so op_b is "behind" it waiting to dispatch
    sm._drain_queue()
    # Meanwhile, mark op_b as failed so it's in backoff
    sm._queue.mark_failure(op_b, 'simulated earlier failure')
    assert sm._queue._ops[1].next_retry_at > time.time()
    # User clicks 'Retry now' while op_a's HTTP is still in-flight
    sm.force_drain()
    # reset_all_retries took effect even though _drain_queue was a no-op
    assert sm._queue._ops[1].next_retry_at == 0.0
    # Fire the in-flight callback → chain continues → op_b dispatched
    client.flush_next()  # op_a succeeds
    assert ('add_favorites', [9276]) in client.call_log
    client.flush_next()  # op_b succeeds
    assert sm._queue.size() == 0


def test_end_to_end_write_fail_retry_success(tmp_path, mock_tags, monkeypatch):
    """Full async cycle: write-through → dispatch → fail → backoff → retry_tick → success."""
    sm, client = _make_sm_with_fake(tmp_path)
    # Stub idle_add so _schedule_drain is a no-op (we drain manually)
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: None,
    )
    sm.add_to_tag_synced('favorites', 9275)
    assert sm._queue.size() == 1
    # First dispatch attempt — simulate server error
    sm._drain_queue()
    client.fail_next('server 500')
    assert sm._queue.size() == 1
    assert sm._queue.has_errors()
    # Backoff now blocks peek_ready
    assert sm._queue.peek_ready(time.time()) == []
    # retry_tick fires but sees op still in backoff — no-op
    sm._retry_tick()
    assert client.call_log == [('add_favorites', [9275])]  # still just one call
    # Simulate backoff expiring
    sm._queue._ops[0].next_retry_at = 0.0
    # Now retry_tick schedules drain (monkeypatched) — call manually
    sm._retry_tick()
    sm._drain_queue()
    assert len(client.call_log) == 2  # retry dispatched
    client.flush_next()  # success
    assert sm._queue.size() == 0
    assert not sm._queue.has_errors()


def test_enqueued_op_carries_current_user_id(tmp_path, mock_tags):
    """set_user_id value is captured at enqueue time."""
    sm, client = _make_sm_with_fake(tmp_path)
    sm.set_user_id(777)
    sm.add_to_tag_synced('favorites', 9275)
    sm.add_to_tag_synced('watching', 9276)
    assert sm._queue._ops[0].user_id == 777
    assert sm._queue._ops[1].user_id == 777


# --- Stage 3: pull coordination / snapshot protection ---

def test_initial_sync_captures_queue_snapshot(mock_tags, tmp_path, monkeypatch):
    """initial_sync snapshots queue release_ids BEFORE the pull begins.

    Observes the intermediate (mid-sync) state by monkey-patching
    _sync_favorites to capture `self._pull_snapshot` at entry time. This
    is the only way to verify the eager-capture invariant, since
    FakeSyncClient runs callbacks synchronously — the final state alone
    would only prove that _sync_done cleared the snapshot.
    """
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    assert sm._pull_snapshot == set()

    captured = {}
    original = sm._sync_favorites

    def capture_at_entry(then):
        captured['mid_sync'] = set(sm._pull_snapshot)
        original(then)

    monkeypatch.setattr(sm, '_sync_favorites', capture_at_entry)

    sync_client = FakeSyncClient()
    sm._client = sync_client
    sm.initial_sync(lambda ok, err: None)

    # During sync (before _sync_done runs): snapshot contains pending ids
    assert captured['mid_sync'] == {9275, 9276}
    # After sync: _sync_done cleared it
    assert sm._pull_snapshot == set()


def test_snapshot_cleared_between_successive_syncs(mock_tags, tmp_path):
    """Each initial_sync call starts with a fresh snapshot."""
    sm, client = _make_sm_with_fake(tmp_path)
    sync_client = FakeSyncClient()
    sm._client = sync_client
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm.initial_sync(lambda ok, err: None)
    assert sm._pull_snapshot == set()  # cleared after first sync
    # Enqueue a different op before second sync
    sm._queue.enqueue(OP_ADD_FAVORITE, 9999, user_id=42)
    sm.initial_sync(lambda ok, err: None)
    assert sm._pull_snapshot == set()  # cleared again


def test_initial_sync_kicks_drain(mock_tags, tmp_path, monkeypatch):
    """initial_sync should schedule a drain of pending ops as part of its setup."""
    sm, client = _make_sm_with_fake(tmp_path)
    scheduled = []
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: scheduled.append(fn),
    )
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    # Swap to synchronous client so initial_sync completes in-call
    sync_client = FakeSyncClient()
    sm._client = sync_client
    sm.initial_sync(lambda ok, err: None)
    # The drain was kicked via GLib.idle_add at least once
    assert len(scheduled) >= 1


def test_pull_prefer_server_does_not_remove_pending_favorite(mock_tags, tmp_path):
    """PREFER_SERVER must not evict a locally-favorited release if its
    add_favorite op is still pending (E3 regression test)."""
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.add_release('favorites', 9275)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sync_client = FakeSyncClient()
    sync_client.server_favorites = []
    sync_client.server_collections = []
    sm._client = sync_client

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_SERVER)

    assert 9275 in tags_store.get_release_ids_for_tag('favorites')


def test_pull_merge_does_not_add_when_pending_remove(mock_tags, tmp_path):
    """MERGE must not re-add a locally-removed release if its
    remove_favorite op is still pending."""
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    sync_client = FakeSyncClient()
    sync_client.server_favorites = [9275]
    sync_client.server_collections = []
    sm._client = sync_client

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.MERGE)

    assert 9275 not in tags_store.get_release_ids_for_tag('favorites')


def test_pull_prefer_local_skips_pending_from_push(mock_tags, tmp_path):
    """PREFER_LOCAL must not double-push: releases already in the queue
    will be pushed via the queue drain, so the sync push should skip them."""
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.add_release('favorites', 9275)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    tags_store.add_release('favorites', 8888)

    sync_client = FakeSyncClient()
    sync_client.server_favorites = []
    sync_client.server_collections = []
    sm._client = sync_client

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_LOCAL)

    assert 8888 in sync_client.pushed_favorites
    assert 9275 not in sync_client.pushed_favorites


def test_pull_prefer_server_does_not_remove_pending_collection(mock_tags, tmp_path):
    """PREFER_SERVER must not evict a collection entry if its add_collection
    op is still pending."""
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.add_release('watching', 9275)
    sm._queue.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    sync_client = FakeSyncClient()
    sync_client.server_favorites = []
    sync_client.server_collections = []
    sm._client = sync_client

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_SERVER)

    assert 9275 in tags_store.get_release_ids_for_tag('watching')


def test_pull_merge_collection_respects_snapshot(mock_tags, tmp_path):
    """MERGE: a pending remove must not be undone by server data."""
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_REMOVE_COLLECTION, 9275, user_id=42)
    sync_client = FakeSyncClient()
    sync_client.server_favorites = []
    sync_client.server_collections = [
        {'release_id': 9275, 'type_of_collection': 'WATCHED'},
    ]
    sm._client = sync_client

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.MERGE)

    assert 9275 not in tags_store.get_release_ids_for_tag('watched')


def test_pull_prefer_local_collection_skips_pending_from_push(mock_tags, tmp_path):
    """PREFER_LOCAL: releases with pending collection ops are not double-pushed."""
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.add_release('watching', 9275)
    tags_store.add_release('watching', 8888)
    sm._queue.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    sync_client = FakeSyncClient()
    sync_client.server_favorites = []
    sync_client.server_collections = []
    sm._client = sync_client

    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_LOCAL)

    pushed_ids = [entry[0] for entry in sync_client.pushed_collections]
    assert 8888 in pushed_ids
    assert 9275 not in pushed_ids


def test_snapshot_stable_even_if_drain_succeeds_mid_sync(mock_tags, tmp_path):
    """Snapshot is frozen at initial_sync entry — a successful drain does
    not remove its release ids from the snapshot. This defends against
    server replication lag.
    """
    sm, client = _make_sm_with_fake(tmp_path)
    tags_store.add_release('favorites', 9275)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)

    class HybridClient:
        def __init__(self):
            self._get_token = lambda: 'test-token'
            self.pending_drain_cbs = []

        def add_favorites(self, ids, cb=None):
            if cb:
                self.pending_drain_cbs.append(cb)

        def remove_favorites(self, ids, cb=None):
            if cb:
                cb(None, None)

        def add_to_collection(self, rid, ctype, cb=None):
            if cb:
                cb(None, None)

        def remove_from_collection(self, ids, cb=None):
            if cb:
                cb(None, None)

        def get_favorite_ids(self, callback=None):
            # Server hasn't received our write yet (replication lag)
            callback([], None)

        def get_collection_ids(self, callback=None):
            callback([], None)

        def get_timecodes(self, since=None, callback=None):
            callback([], None)

    sm._client = HybridClient()
    sm.initial_sync(lambda ok, err: None,
                    strategy=MergeStrategy.PREFER_SERVER)

    # Local state survived the sync — snapshot protection held.
    assert 9275 in tags_store.get_release_ids_for_tag('favorites')


# --- Stage 5: timecode sync ---

def test_enqueue_timecode_creates_op(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    from kitsune.storage.pending_queue import OP_SAVE_TIMECODE
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0',
        pos=120.5, is_watched=False)
    assert sm._queue.size() == 1
    op = sm._queue._ops[0]
    assert op.op == OP_SAVE_TIMECODE
    assert op.release_id == 9275
    assert op.payload == {
        'episode_id': 'ep.0',
        'time': 120.5,
        'is_watched': False,
    }


def test_enqueue_timecode_coalesces_rapid_saves_same_episode(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0',
        pos=30.0, is_watched=False)
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0',
        pos=120.5, is_watched=False)
    assert sm._queue.size() == 1
    assert sm._queue._ops[0].payload['time'] == 120.5


def test_drain_batches_timecode_ops_into_single_call(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    for i in range(3):
        sm.enqueue_timecode(
            release_id=9275,
            episode_id=f'ep.{i}', pos=60.0 * (i + 1),
            is_watched=False)
    sm._drain_queue()
    save_calls = [c for c in client.call_log if c[0] == 'save_timecodes']
    assert len(save_calls) == 1
    payloads = save_calls[0][1]
    assert len(payloads) == 3
    # Wire format uses `release_episode_id` (server POST schema). The
    # internal queue payload still uses `episode_id`.
    for p in payloads:
        assert 'release_episode_id' in p
        assert 'episode_id' not in p
    client.flush_all()
    assert sm._queue.size() == 0


def test_timecode_dispatch_translates_episode_id_to_release_episode_id(mock_tags, tmp_path):
    """Server POST schema requires `release_episode_id`; we store `episode_id`
    internally and translate at the wire boundary."""
    sm, client = _make_sm_with_fake(tmp_path)
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep-uuid-123',
        pos=120.5, is_watched=False)
    # Internal payload uses episode_id
    assert sm._queue._ops[0].payload['episode_id'] == 'ep-uuid-123'
    sm._drain_queue()
    save_calls = [c for c in client.call_log if c[0] == 'save_timecodes']
    assert len(save_calls) == 1
    payload = save_calls[0][1][0]
    assert payload == {
        'release_episode_id': 'ep-uuid-123',
        'time': 120.5,
        'is_watched': False,
    }


def test_drain_batch_cap_50_per_call(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    for i in range(75):
        sm.enqueue_timecode(
            release_id=9275,
            episode_id=f'ep.{i}', pos=60.0,
            is_watched=False)
    sm._drain_queue()
    save_calls = [c for c in client.call_log if c[0] == 'save_timecodes']
    assert len(save_calls) == 1
    assert len(save_calls[0][1]) == 50
    client.flush_all()
    sm._drain_queue()
    save_calls = [c for c in client.call_log if c[0] == 'save_timecodes']
    assert len(save_calls) == 2
    assert len(save_calls[1][1]) == 25


def test_drain_timecode_batch_success_marks_all_ops_success(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    for i in range(3):
        sm.enqueue_timecode(
            release_id=9275,
            episode_id=f'ep.{i}', pos=60.0, is_watched=False)
    sm._drain_queue()
    client.flush_all()
    assert sm._queue.size() == 0


def test_drain_timecode_batch_failure_marks_all_ops_failure(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    for i in range(3):
        sm.enqueue_timecode(
            release_id=9275,
            episode_id=f'ep.{i}', pos=60.0, is_watched=False)
    errors = []
    sm.connect_sync_error(lambda op, rid, err: errors.append((op, rid, err)))
    sm._drain_queue()
    client.fail_next('server 500')
    assert sm._queue.size() == 3
    assert len(errors) == 3
    assert all(e[2] == 'server 500' for e in errors)
    assert all(op.attempt_count == 1 for op in sm._queue._ops)


def test_drain_mixed_ops_processes_non_timecodes_first(mock_tags, tmp_path):
    from kitsune.storage.pending_queue import OP_ADD_FAVORITE
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 111, user_id=42)
    sm.enqueue_timecode(
        release_id=222, episode_id='ep.0',
        pos=60.0, is_watched=False)
    sm._queue.enqueue(OP_ADD_FAVORITE, 333, user_id=42)
    sm._drain_queue()
    assert client.call_log[0][0] == 'add_favorites'
    client.flush_next()
    assert client.call_log[1][0] == 'add_favorites'
    client.flush_next()
    assert client.call_log[2][0] == 'save_timecodes'
    client.flush_next()
    assert sm._queue.size() == 0


def test_pull_timecodes_applies_via_apply_server_entry(mock_tags, tmp_path):
    from kitsune.storage import watch_positions, episode_index
    sm, client = _make_sm_with_fake(tmp_path)
    wp_file = tmp_path / 'wp.json'
    idx_file = tmp_path / 'idx.json'
    import unittest.mock as um
    with um.patch.object(watch_positions, '_POSITIONS_FILE', wp_file), \
         um.patch.object(episode_index, '_INDEX_FILE', idx_file), \
         um.patch.object(episode_index, '_cache', None):
        episode_index.add_from_release_data(
            9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
        client.get_timecodes_response = [
            {'episode_id': 'ep.0', 'time': 120.5,
             'is_watched': False, 'updated_at': 5000.0},
        ]
        done = [False]
        sm._pull_and_save_timecodes(lambda: done.__setitem__(0, True))
        client.flush_all()
        assert done[0] is True
        assert watch_positions.get_position(9275, 1.0) == 120.5
        assert watch_positions.get_episode_id(9275, 1.0) == 'ep.0'


def test_pull_timecodes_handles_list_format(mock_tags, tmp_path):
    from kitsune.storage import watch_positions, episode_index
    sm, client = _make_sm_with_fake(tmp_path)
    wp_file = tmp_path / 'wp.json'
    idx_file = tmp_path / 'idx.json'
    import unittest.mock as um
    with um.patch.object(watch_positions, '_POSITIONS_FILE', wp_file), \
         um.patch.object(episode_index, '_INDEX_FILE', idx_file), \
         um.patch.object(episode_index, '_cache', None):
        episode_index.add_from_release_data(
            9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
        client.get_timecodes_response = [['ep.0', 60.0, False]]
        done = [False]
        sm._pull_and_save_timecodes(lambda: done.__setitem__(0, True))
        client.flush_all()
        assert done[0] is True
        assert watch_positions.get_position(9275, 1.0) == 60.0


def test_pull_timecodes_skips_unmapped(mock_tags, tmp_path):
    from kitsune.storage import watch_positions, episode_index
    sm, client = _make_sm_with_fake(tmp_path)
    wp_file = tmp_path / 'wp.json'
    idx_file = tmp_path / 'idx.json'
    import unittest.mock as um
    with um.patch.object(watch_positions, '_POSITIONS_FILE', wp_file), \
         um.patch.object(episode_index, '_INDEX_FILE', idx_file), \
         um.patch.object(episode_index, '_cache', None):
        client.get_timecodes_response = [
            {'episode_id': 'unknown.0', 'time': 60.0,
             'is_watched': False, 'updated_at': 1000.0},
        ]
        done = [False]
        sm._pull_and_save_timecodes(lambda: done.__setitem__(0, True))
        client.flush_all()
        assert done[0] is True
        assert watch_positions.get_count() == 0


def test_pull_timecodes_handles_empty_response(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    client.get_timecodes_response = []
    done = [False]
    sm._pull_and_save_timecodes(lambda: done.__setitem__(0, True))
    client.flush_all()
    assert done[0] is True


def test_pull_timecodes_handles_error(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    done = [False]
    sm._pull_and_save_timecodes(lambda: done.__setitem__(0, True))
    client.fail_next('server error')
    assert done[0] is True


def test_flush_timecodes_enqueues_pushable_entries(mock_tags, tmp_path):
    from kitsune.storage import watch_positions
    sm, client = _make_sm_with_fake(tmp_path)
    sm.set_user_id(42)
    wp_file = tmp_path / 'wp.json'
    import unittest.mock as um
    with um.patch.object(watch_positions, '_POSITIONS_FILE', wp_file):
        watch_positions.save_position(9275, 1.0, 60.0, episode_id='ep.0')
        watch_positions.save_position(9275, 2.0, 90.0, episode_id='ep.1')
        watch_positions.save_position(9275, 3.0, 30.0)
        sm._queue.clear()
        sm.flush_timecodes()
        assert sm._queue.size() == 2


def test_flush_timecodes_noop_when_not_logged_in(mock_tags, tmp_path):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._client._get_token = lambda: None
    sm.flush_timecodes()
    assert sm._queue.size() == 0
    assert client.call_log == []


def test_flush_timecodes_coalesces_with_existing_ops(mock_tags, tmp_path):
    from kitsune.storage import watch_positions
    sm, client = _make_sm_with_fake(tmp_path)
    sm.set_user_id(42)
    wp_file = tmp_path / 'wp.json'
    import unittest.mock as um
    with um.patch.object(watch_positions, '_POSITIONS_FILE', wp_file):
        watch_positions.save_position(9275, 1.0, 60.0, episode_id='ep.0')
        sm._queue.clear()
        sm.enqueue_timecode(
            release_id=9275, episode_id='ep.0',
            pos=30.0, is_watched=False)
        sm.flush_timecodes()
        assert sm._queue.size() == 1
        assert sm._queue._ops[0].payload['time'] == 60.0


# --- Post-review coverage (Stage 5 final) ---

def test_enqueue_timecode_noop_when_not_logged_in(mock_tags, tmp_path):
    """enqueue_timecode early-returns without touching queue if no token."""
    sm, client = _make_sm_with_fake(tmp_path)
    sm._client._get_token = lambda: None
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0',
        pos=120.5, is_watched=False)
    assert sm._queue.size() == 0


def test_flush_timecodes_filters_by_release_id(mock_tags, tmp_path):
    """flush_timecodes(release_id=X) enqueues only entries for that release."""
    from kitsune.storage import watch_positions
    sm, client = _make_sm_with_fake(tmp_path)
    sm.set_user_id(42)
    wp_file = tmp_path / 'wp.json'
    import unittest.mock as um
    with um.patch.object(watch_positions, '_POSITIONS_FILE', wp_file):
        watch_positions.save_position(9275, 1.0, 60.0, episode_id='ep.a')
        watch_positions.save_position(8888, 1.0, 90.0, episode_id='ep.b')
        sm._queue.clear()
        sm.flush_timecodes(release_id=9275)
        assert sm._queue.size() == 1
        assert sm._queue._ops[0].release_id == 9275


def test_parse_timecode_item_handles_mixed_malformed_entries():
    """_parse_timecode_item returns None for garbage entries; callers skip them."""
    from kitsune.storage.sync_manager import _parse_timecode_item
    assert _parse_timecode_item(None) is None
    assert _parse_timecode_item('string') is None
    assert _parse_timecode_item(['too', 'short']) is None
    assert _parse_timecode_item({'no_episode_id': 'xxx'}) is None
    assert _parse_timecode_item({'episode_id': '', 'time': 0}) is None
    # Well-formed dict and list still work
    parsed = _parse_timecode_item({'episode_id': 'ep.0', 'time': 30, 'is_watched': False, 'updated_at': 100.0})
    assert parsed == ('ep.0', 30.0, False, 100.0)
    parsed = _parse_timecode_item(['ep.0', 30, False, 100.0])
    assert parsed == ('ep.0', 30.0, False, 100.0)


def test_parse_timecode_item_preserves_explicit_zero_updated_at():
    """updated_at=0 is valid (loses to positive local ts); must not be replaced by now()."""
    from kitsune.storage.sync_manager import _parse_timecode_item
    parsed = _parse_timecode_item({'episode_id': 'ep.0', 'time': 30, 'is_watched': False, 'updated_at': 0})
    assert parsed == ('ep.0', 30.0, False, 0.0)
    parsed = _parse_timecode_item(['ep.0', 30, False, 0])
    assert parsed == ('ep.0', 30.0, False, 0.0)


def test_coalesce_absorbs_new_save_after_failed_timecode(tmp_path, mock_tags, monkeypatch):
    """Regression: a new timecode for the same (release, episode) as a failed op
    coalesces into the failed op's slot, replacing the stale payload.

    This closes the stale-op race: if op A fails and later op B fires for the
    same key, B must update A's payload in place so the retry dispatches B's
    value. Stage 1 coalescing rules for save_timecode skip only IN-FLIGHT ops;
    failed ops (which are NOT in-flight) are coalesce-eligible — so this
    scenario works correctly by design.
    """
    sm, client = _make_sm_with_fake(tmp_path)
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0', pos=30.0, is_watched=False)
    sm._drain_queue()
    # Fail the batch → op stays in queue with backoff
    client.fail_next('server 500')
    assert sm._queue.size() == 1
    first_op_id = sm._queue._ops[0].id
    assert sm._queue._ops[0].payload['time'] == 30.0
    assert sm._queue._ops[0].attempt_count == 1
    # New save for SAME (release, episode) while A is failed (not in-flight)
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0', pos=120.0, is_watched=False)
    # B coalesced into A's slot — payload updated, retry state reset
    assert sm._queue.size() == 1
    assert sm._queue._ops[0].id == first_op_id  # same op, updated
    assert sm._queue._ops[0].payload['time'] == 120.0
    assert sm._queue._ops[0].attempt_count == 0  # reset by timecode coalescing


# --- Stage 6: token expired handler hook ---

def test_fake_client_supports_token_expired_handler(tmp_path):
    """FakeApiClient records the handler and fires it on trigger."""
    client = FakeApiClient()
    fired = []
    client.set_token_expired_handler(lambda: fired.append(True))
    client.trigger_token_expired()
    assert fired == [True]


def test_fake_client_trigger_without_handler_is_noop(tmp_path):
    """Firing with no handler registered must not raise."""
    client = FakeApiClient()
    client.trigger_token_expired()  # should not raise


# --- Stage 6: session-expired reaction ---

def test_pause_for_expired_session_stops_retry_timer(tmp_path, mock_tags, monkeypatch):
    sm, client = _make_sm_with_fake(tmp_path)
    # Kick a drain so the retry timer starts. The HTTP response is NOT
    # flushed — the op stays in-flight, so the post-success retry-timer
    # stop in _on_op_result does not fire and the timer keeps running
    # until pause_for_expired_session explicitly cancels it.
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0', pos=30.0, is_watched=False)
    sm._drain_queue()
    assert sm._retry_timer_id is not None
    sm.pause_for_expired_session()
    assert sm._retry_timer_id is None


def test_pause_for_expired_session_safe_when_no_timer(tmp_path, mock_tags):
    """Calling pause before any drain ever happened must not raise."""
    sm, client = _make_sm_with_fake(tmp_path)
    assert sm._retry_timer_id is None
    sm.pause_for_expired_session()
    assert sm._retry_timer_id is None


def test_resume_after_expired_session_kicks_drain(tmp_path, mock_tags, monkeypatch):
    sm, client = _make_sm_with_fake(tmp_path)
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.0', pos=30.0, is_watched=False)
    sm._drain_queue()
    client.flush_all()
    sm.pause_for_expired_session()
    # Enqueue another while "paused"
    sm.enqueue_timecode(
        release_id=9275, episode_id='ep.1', pos=30.0, is_watched=False)
    # Clear state to see what resume triggers
    client.call_log.clear()
    scheduled = []
    monkeypatch.setattr(
        'kitsune.storage.sync_manager.GLib.idle_add',
        lambda fn: scheduled.append(fn),
    )
    sm.resume_after_expired_session()
    assert len(scheduled) == 1
    assert sm._retry_timer_id is not None


# --- Queue cleanup on logged_out (Stage 8) ---

def test_clear_queue_on_logout_wipes_pending_ops(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    assert sm._queue.size() == 2
    sm.clear_queue_on_logout()
    assert sm._queue.size() == 0


def test_clear_queue_on_logout_also_stops_retry_timer(tmp_path, mock_tags):
    sm, client = _make_sm_with_fake(tmp_path)
    sm._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    sm._schedule_drain()
    sm._drain_queue()
    # Do NOT flush HTTP — the op stays in-flight so the timer keeps
    # running until clear_queue_on_logout cancels it explicitly. (After
    # H5, a successful drain that empties the queue also stops the
    # timer, but that's covered by other tests.)
    assert sm._retry_timer_id is not None
    sm.clear_queue_on_logout()
    # Timer stopped (no point retrying after logout)
    assert sm._retry_timer_id is None
