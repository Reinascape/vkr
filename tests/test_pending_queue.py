# SPDX-License-Identifier: GPL-3.0-or-later

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.storage.pending_queue import PendingQueue
from kitsune.storage.pending_queue import (
    OP_ADD_FAVORITE,
    OP_ADD_COLLECTION,
    OP_SAVE_TIMECODE,
)
from kitsune.storage.pending_queue import OP_REMOVE_FAVORITE, OP_REMOVE_COLLECTION


def test_load_nonexistent_file_returns_empty_queue(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue.load(path)
    assert q.size() == 0


def test_load_malformed_json_returns_empty_queue(tmp_path):
    path = tmp_path / 'pending_ops.json'
    path.write_text('{not valid json')
    q = PendingQueue.load(path)
    assert q.size() == 0


def test_load_version_mismatch_drops_file(tmp_path):
    path = tmp_path / 'pending_ops.json'
    path.write_text(json.dumps({'version': 99, 'ops': [{'bogus': 1}]}))
    q = PendingQueue.load(path)
    assert q.size() == 0


def test_load_missing_version_field_drops_file(tmp_path):
    path = tmp_path / 'pending_ops.json'
    path.write_text(json.dumps({'ops': []}))
    q = PendingQueue.load(path)
    assert q.size() == 0


def test_enqueue_creates_op_with_uuid_id(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert isinstance(op_id, str)
    assert len(op_id) > 10
    assert q.size() == 1


def test_enqueue_persists_to_disk(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert path.exists()
    raw = json.loads(path.read_text())
    assert raw['version'] == 1
    assert len(raw['ops']) == 1
    assert raw['ops'][0]['op'] == 'add_favorite'
    assert raw['ops'][0]['release_id'] == 9275
    assert raw['ops'][0]['user_id'] == 42


def test_enqueue_roundtrip_through_load(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q1 = PendingQueue(path)
    q1.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q1.enqueue(
        OP_ADD_COLLECTION, 1000, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    q1.enqueue(
        OP_SAVE_TIMECODE, 2000, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 120.5, 'is_watched': False},
    )
    q2 = PendingQueue.load(path)
    assert q2.size() == 3


def test_enqueue_sets_defaults(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    raw = json.loads(path.read_text())
    op = raw['ops'][0]
    assert op['attempt_count'] == 0
    assert op['next_retry_at'] == 0.0
    assert op['last_error'] is None
    assert op['payload'] == {}
    assert op['created_at'] > 0


def test_peek_ready_returns_all_ops_when_next_retry_zero(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    ready = q.peek_ready(now=1000.0)
    assert len(ready) == 2


def test_peek_ready_returns_ops_in_created_order(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9277, user_id=42)
    ready = q.peek_ready(now=10_000_000_000)
    assert [op.release_id for op in ready] == [9275, 9276, 9277]


def test_mark_success_removes_op(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_success(op_id)
    assert q.size() == 0


def test_mark_success_persists(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_success(op_id)
    q2 = PendingQueue.load(path)
    assert q2.size() == 0


def test_mark_success_unknown_id_is_noop(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_success('no-such-id')
    assert q.size() == 1


def test_mark_failure_first_attempt_schedules_10s(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    q.mark_failure(op_id, 'timeout')
    ready_at_1005 = q.peek_ready(now=1005.0)
    ready_at_1010 = q.peek_ready(now=1010.0)
    assert ready_at_1005 == []
    assert len(ready_at_1010) == 1


def test_mark_failure_progression_matches_backoff_table(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    expected = [10, 30, 60, 120, 300, 600]
    for step in expected:
        q.mark_failure(op_id, 'timeout')
        op = q._ops[0]
        assert op.next_retry_at == 1000.0 + step
        # Undo the next_retry bump for the next iteration — we want to exercise
        # attempt_count progression, not calendar time.
        op.next_retry_at = 0.0
    assert op.attempt_count == 6


def test_mark_failure_caps_at_600s(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    for _ in range(10):
        q.mark_failure(op_id, 'timeout')
        q._ops[0].next_retry_at = 0.0
    q.mark_failure(op_id, 'timeout')
    assert q._ops[0].next_retry_at == 1000.0 + 600
    assert q._ops[0].attempt_count == 11


def test_mark_failure_stores_error_message(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_failure(op_id, 'connection refused')
    assert q._ops[0].last_error == 'connection refused'


def test_mark_failure_truncates_long_error(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    long_msg = 'x' * 500
    q.mark_failure(op_id, long_msg)
    assert len(q._ops[0].last_error) == 200


def test_mark_failure_persists(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_failure(op_id, 'net error')
    q2 = PendingQueue.load(path)
    assert q2.size() == 1
    assert q2._ops[0].last_error == 'net error'
    assert q2._ops[0].attempt_count == 1


def test_mark_failure_unknown_id_is_noop(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_failure('no-such-id', 'wat')
    assert q._ops[0].attempt_count == 0


def test_coalesce_add_then_remove_favorite_cancels_both(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    result = q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    assert result is None
    assert q.size() == 0


def test_coalesce_remove_then_add_favorite_cancels_both(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert q.size() == 0


def test_coalesce_dedupe_duplicate_add(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    result = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert result is None
    assert q.size() == 1


def test_coalesce_favorite_does_not_affect_other_releases(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_REMOVE_FAVORITE, 9276, user_id=42)
    assert q.size() == 2


def test_coalesce_collection_add_then_remove_same_type_cancels(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    q.enqueue(
        OP_REMOVE_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    assert q.size() == 0


def test_coalesce_collection_different_types_do_not_cancel(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    q.enqueue(
        OP_REMOVE_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHED'},
    )
    assert q.size() == 2


def test_coalesce_dedupe_collection_same_type(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    q.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    assert q.size() == 1


def test_mark_in_flight_hides_op_from_peek_ready(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(op_id)
    assert q.peek_ready(now=10_000_000_000) == []


def test_mark_success_removes_from_in_flight(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(op_id)
    q.mark_success(op_id)
    assert op_id not in q._in_flight


def test_mark_failure_removes_from_in_flight(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(op_id)
    q.mark_failure(op_id, 'oops')
    assert op_id not in q._in_flight


def test_coalesce_skips_in_flight_ops_opposite(tmp_path):
    """An opposite op does NOT cancel an in-flight op — both are kept."""
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    first_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(first_id)
    second_id = q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    assert second_id is not None
    assert q.size() == 2


def test_coalesce_dedups_in_flight_same_kind(tmp_path):
    """A same-kind duplicate is deduped against an in-flight op.

    The in-flight POST will satisfy the same intent (e.g. add_favorite),
    so enqueueing a second add for the same release would just generate
    a wasted server call after drain completes. Dropping it via
    coalescing keeps the queue tight without changing observable state.
    """
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    first_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(first_id)
    second_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert second_id is None
    assert q.size() == 1


def test_load_clears_in_flight(tmp_path):
    """In-flight state is in-memory only — a reload starts with empty set."""
    path = tmp_path / 'pending_ops.json'
    q1 = PendingQueue(path)
    op_id = q1.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q1.mark_in_flight(op_id)
    q2 = PendingQueue.load(path)
    assert q2._in_flight == set()


def test_coalesce_timecode_same_episode_updates_in_place(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    first_id = q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    second_id = q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 120.5, 'is_watched': False},
    )
    assert q.size() == 1
    assert second_id is None
    op = q._ops[0]
    assert op.id == first_id
    assert op.payload['time'] == 120.5


def test_coalesce_timecode_different_episodes_stay_separate(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.1', 'time': 30.0, 'is_watched': False},
    )
    assert q.size() == 2


def test_coalesce_timecode_different_releases_stay_separate(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    q.enqueue(
        OP_SAVE_TIMECODE, 9276, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    assert q.size() == 2


def test_coalesce_timecode_resets_retry_state(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    first_id = q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    q.mark_failure(first_id, 'network')
    assert q._ops[0].attempt_count == 1
    assert q._ops[0].next_retry_at == 1010.0
    assert q._ops[0].last_error == 'network'
    q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 120.5, 'is_watched': False},
    )
    assert q._ops[0].attempt_count == 0
    assert q._ops[0].next_retry_at == 0.0
    assert q._ops[0].last_error is None


def test_coalesce_timecode_skips_in_flight(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    first_id = q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    q.mark_in_flight(first_id)
    second_id = q.enqueue(
        OP_SAVE_TIMECODE, 9275, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 120.5, 'is_watched': False},
    )
    assert second_id is not None
    assert q.size() == 2


def test_release_ids_empty(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    assert q.release_ids() == set()


def test_release_ids_from_multiple_ops(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(
        OP_ADD_COLLECTION, 9276, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    q.enqueue(
        OP_SAVE_TIMECODE, 9277, user_id=42,
        payload={'episode_id': 'ep.0', 'time': 30.0, 'is_watched': False},
    )
    assert q.release_ids() == {9275, 9276, 9277}


def test_release_ids_dedupes(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(
        OP_ADD_COLLECTION, 9275, user_id=42,
        payload={'collection_type': 'WATCHING'},
    )
    assert q.release_ids() == {9275}


def test_has_errors_false_on_empty_queue(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    assert q.has_errors() is False


def test_has_errors_false_on_fresh_op(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert q.has_errors() is False


def test_has_errors_true_after_failure(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_failure(op_id, 'timeout')
    assert q.has_errors() is True


def test_last_error_none_on_empty(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    assert q.last_error() is None


def test_last_error_none_on_fresh_ops(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert q.last_error() is None


def test_last_error_returns_most_recent_failure(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_a = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    op_b = q.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    q.mark_failure(op_a, 'first error')
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 2000.0
    )
    q.mark_failure(op_b, 'second error')
    assert q.last_error() == 'second error'


def test_clear_empties_queue(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    q.clear()
    assert q.size() == 0


def test_clear_persists(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.clear()
    q2 = PendingQueue.load(path)
    assert q2.size() == 0


def test_clear_drops_in_flight(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(op_id)
    q.clear()
    assert q._in_flight == set()


def test_clear_for_user_removes_matching_ops(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9276, user_id=42)
    q.enqueue(OP_ADD_FAVORITE, 9277, user_id=999)
    removed = q.clear_for_user(42)
    assert removed == 2
    assert q.size() == 1
    assert q._ops[0].user_id == 999


def test_clear_for_user_zero_when_no_match(tmp_path):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    removed = q.clear_for_user(999)
    assert removed == 0
    assert q.size() == 1


def test_reset_all_retries_sets_next_retry_to_zero(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    q.mark_failure(op_id, 'timeout')
    assert q._ops[0].next_retry_at > 0
    q.reset_all_retries()
    assert q._ops[0].next_retry_at == 0.0


def test_reset_all_retries_keeps_attempt_count(tmp_path, monkeypatch):
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_id = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    monkeypatch.setattr(
        'kitsune.storage.pending_queue.time.time', lambda: 1000.0
    )
    q.mark_failure(op_id, 'timeout')
    q.mark_failure(op_id, 'timeout again')
    q.reset_all_retries()
    assert q._ops[0].attempt_count == 2
    assert q._ops[0].last_error == 'timeout again'


def test_coalesce_preserves_remove_when_in_flight_add(tmp_path):
    """Regression: an opposite op must reach the queue even when the
    matching op is in-flight — otherwise the user's last intent (the
    remove) would be lost after the in-flight add commits server-side.

    With same-kind in-flight dedup, the second add is dropped (good —
    intent already covered by the in-flight one), but the subsequent
    remove must still be enqueued.
    """
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_a = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(op_a)
    # Second add is deduped against in-flight A
    op_b = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert op_b is None
    assert q.size() == 1
    # Remove must reach the queue so the server eventually unfavorites
    op_c = q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    assert op_c is not None
    assert q.size() == 2
    ops_by_kind = {op.op for op in q._ops}
    assert OP_ADD_FAVORITE in ops_by_kind
    assert OP_REMOVE_FAVORITE in ops_by_kind


def test_coalesce_preserves_add_when_in_flight_remove(tmp_path):
    """Same invariant, opposite direction."""
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    op_a = q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    q.mark_in_flight(op_a)
    op_b = q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    assert op_b is None
    op_c = q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    assert op_c is not None
    assert q.size() == 2
    ops_by_kind = {op.op for op in q._ops}
    assert OP_REMOVE_FAVORITE in ops_by_kind
    assert OP_ADD_FAVORITE in ops_by_kind


def test_coalesce_still_works_when_no_in_flight_conflict(tmp_path):
    """Normal coalescing still works when there's no in-flight interference."""
    path = tmp_path / 'pending_ops.json'
    q = PendingQueue(path)
    q.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    q.enqueue(OP_REMOVE_FAVORITE, 9275, user_id=42)
    assert q.size() == 0  # cancelled as before
