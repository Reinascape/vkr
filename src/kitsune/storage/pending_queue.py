# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import logging
import os
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from kitsune.storage import _atomic_write_json

log = logging.getLogger('kitsune.pending_queue')

_PENDING_OPS_FILE = Path(
    os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
) / 'kitsune' / 'pending_ops.json'

VERSION = 1

# Retry backoff table (seconds). After 6 failures the interval stays at 600s
# and retries continue indefinitely — ops are never given up on.
BACKOFF_STEPS = [10, 30, 60, 120, 300, 600]

# Truncate error messages to avoid bloating the file
MAX_ERROR_LEN = 200

# Operation kind constants — used instead of an enum for simpler JSON round-trip
OP_ADD_FAVORITE = 'add_favorite'
OP_REMOVE_FAVORITE = 'remove_favorite'
OP_ADD_COLLECTION = 'add_collection'
OP_REMOVE_COLLECTION = 'remove_collection'
OP_SAVE_TIMECODE = 'save_timecode'


@dataclass
class Op:
    id: str
    op: str
    release_id: int
    user_id: int
    payload: dict
    created_at: float
    attempt_count: int = 0
    next_retry_at: float = 0.0
    last_error: str | None = None


class PendingQueue:
    """Persistent FIFO queue of sync operations waiting to reach the server.

    Exception semantics: if _save() raises (disk full, permission error),
    in-memory state will be ahead of on-disk state. Callers should treat
    the exception as fatal for the current operation cycle and allow the
    next cycle to reload from disk.
    """

    def __init__(self, path: Path | None = None):
        self._path = Path(path) if path is not None else _PENDING_OPS_FILE
        self._ops: list[Op] = []
        self._in_flight: set[str] = set()

    @classmethod
    def load(cls, path: Path | None = None) -> PendingQueue:
        q = cls(path)
        q._load_from_disk()
        return q

    def _load_from_disk(self):
        try:
            raw = json.loads(self._path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            self._ops = []
            log.debug('load: no prior queue at %s', self._path)
            return
        if not isinstance(raw, dict) or raw.get('version') != VERSION:
            log.warning(
                'pending_ops.json has unknown format or version, dropping contents'
            )
            self._ops = []
            return
        raw_ops = raw.get('ops', [])
        if not isinstance(raw_ops, list):
            self._ops = []
            return
        self._ops = []
        for op_dict in raw_ops:
            try:
                self._ops.append(Op(**op_dict))
            except TypeError:
                log.warning('Dropping malformed pending op: %s', op_dict)
        log.debug('load: restored %d ops from %s', len(self._ops), self._path)

    def _save(self):
        data = {
            'version': VERSION,
            'ops': [asdict(op) for op in self._ops],
        }
        _atomic_write_json(self._path, data)

    def mark_in_flight(self, op_id: str):
        """Mark an op as currently being dispatched (HTTP request in flight).

        Ops in this state are hidden from peek_ready and are not matched by
        coalescing — so a rapid click that would have cancelled an in-flight
        op is instead enqueued as a new op, preserving intent.

        The set is in-memory only; a process restart begins with an empty set.
        """
        self._in_flight.add(op_id)

    def enqueue(
        self,
        op_kind: str,
        release_id: int,
        user_id: int,
        payload: dict | None = None,
    ) -> str | None:
        """Add a new op to the queue and persist.

        Coalescing rules (see spec A1/E2):
          - add_favorite + existing remove_favorite on same release → cancel both
          - remove_favorite + existing add_favorite on same release → cancel both
          - add_collection + existing remove_collection on same
            (release_id, collection_type) → cancel both
          - remove_collection + existing add_collection → cancel both
          - duplicate add_* or remove_* on same key → dedupe (new op dropped)
          - save_timecode on existing (release_id, episode_id) → update payload
            in place and reset retry state (queue size unchanged)

        Coalescing never matches against an op that is currently in flight.

        Returns the new op id, or None if the op was coalesced.
        """
        if payload is None:
            payload = {}
        if self._try_coalesce(op_kind, release_id, payload):
            log.debug('enqueue %s rid=%d coalesced (size=%d)',
                      op_kind, release_id, len(self._ops))
            return None
        new_op = Op(
            id=str(uuid.uuid4()),
            op=op_kind,
            release_id=release_id,
            user_id=user_id,
            payload=dict(payload),
            created_at=time.time(),
        )
        self._ops.append(new_op)
        self._save()
        log.debug('enqueue %s rid=%d id=%s (size=%d)',
                  op_kind, release_id, new_op.id, len(self._ops))
        return new_op.id

    _OPPOSITES = {
        OP_ADD_FAVORITE: OP_REMOVE_FAVORITE,
        OP_REMOVE_FAVORITE: OP_ADD_FAVORITE,
        OP_ADD_COLLECTION: OP_REMOVE_COLLECTION,
        OP_REMOVE_COLLECTION: OP_ADD_COLLECTION,
    }

    def _try_coalesce(
        self,
        op_kind: str,
        release_id: int,
        payload: dict,
    ) -> bool:
        """Return True if the new op was absorbed into an existing one."""
        if op_kind == OP_SAVE_TIMECODE:
            episode_id = payload.get('episode_id')
            for existing in self._ops:
                if existing.id in self._in_flight:
                    continue
                if existing.op != OP_SAVE_TIMECODE:
                    continue
                if existing.release_id != release_id:
                    continue
                if existing.payload.get('episode_id') != episode_id:
                    continue
                # Update payload and reset retry state
                existing.payload = dict(payload)
                existing.attempt_count = 0
                existing.next_retry_at = 0.0
                existing.last_error = None
                self._save()
                return True
            return False

        if op_kind not in self._OPPOSITES:
            return False

        opposite = self._OPPOSITES[op_kind]
        needs_collection_match = op_kind in (OP_ADD_COLLECTION, OP_REMOVE_COLLECTION)
        new_collection_type = payload.get('collection_type') if needs_collection_match else None

        # In-flight ops on the same key short-circuit the second-loop
        # coalescing pass, which would otherwise silently lose intent by
        # cancelling pending opposites while the in-flight op continues
        # to commit. Two cases:
        #   - same-kind in-flight: the new click expresses the same
        #     intent the server is about to satisfy, so drop the new op
        #     to avoid a redundant POST after drain completes.
        #   - opposite in-flight: enqueue the new op separately so its
        #     dispatch reverses the in-flight result on the server.
        for existing in self._ops:
            if existing.id not in self._in_flight:
                continue
            if existing.release_id != release_id:
                continue
            if needs_collection_match:
                if existing.op not in (OP_ADD_COLLECTION, OP_REMOVE_COLLECTION):
                    continue
                if existing.payload.get('collection_type') != new_collection_type:
                    continue
            else:
                if existing.op not in (op_kind, opposite):
                    continue
            if existing.op == op_kind:
                return True
            return False

        for existing in list(self._ops):
            if existing.id in self._in_flight:
                continue
            if existing.release_id != release_id:
                continue
            if needs_collection_match:
                if existing.op not in (OP_ADD_COLLECTION, OP_REMOVE_COLLECTION):
                    continue
                if existing.payload.get('collection_type') != new_collection_type:
                    continue
            if existing.op == opposite:
                self._ops.remove(existing)
                self._save()
                return True
            if existing.op == op_kind:
                return True
        return False

    def peek_ready(self, now: float) -> list[Op]:
        """Return ops ready to be dispatched: not in flight and next_retry_at <= now.

        Ops are returned in the order they were enqueued (FIFO by created_at).
        Callers must not mutate the returned list; use mark_success/mark_failure
        to update queue state.
        """
        return [
            op for op in self._ops
            if op.id not in self._in_flight and op.next_retry_at <= now
        ]

    def mark_success(self, op_id: str):
        """Remove a successfully dispatched op from the queue."""
        before = len(self._ops)
        self._ops = [op for op in self._ops if op.id != op_id]
        self._in_flight.discard(op_id)
        if len(self._ops) != before:
            self._save()
            log.debug('mark_success id=%s (size=%d)', op_id, len(self._ops))

    def mark_failure(self, op_id: str, error: str):
        """Increment attempt_count, schedule next retry per backoff table, persist.

        After BACKOFF_STEPS is exhausted, next_retry_at stays at the last
        (largest) step — retries continue forever until success or clear.
        """
        for op in self._ops:
            if op.id != op_id:
                continue
            op.attempt_count += 1
            idx = min(op.attempt_count - 1, len(BACKOFF_STEPS) - 1)
            backoff = BACKOFF_STEPS[idx]
            op.next_retry_at = time.time() + backoff
            op.last_error = str(error)[:MAX_ERROR_LEN]
            self._in_flight.discard(op_id)
            self._save()
            log.debug(
                'mark_failure id=%s attempt=%d backoff=%ds error=%s',
                op_id, op.attempt_count, backoff, op.last_error)
            return

    def release_ids(self) -> set[int]:
        """Set of release ids with at least one op in the queue."""
        return {op.release_id for op in self._ops}

    def has_errors(self) -> bool:
        """True if any queued op has failed at least once."""
        return any(op.attempt_count > 0 for op in self._ops)

    def last_error(self) -> str | None:
        """The error message of the most recently failed op, or None.

        'Most recent' is determined by next_retry_at — higher means later,
        and a fresh failure always sets next_retry_at in the future.
        """
        errored = [op for op in self._ops if op.last_error]
        if not errored:
            return None
        return max(errored, key=lambda op: op.next_retry_at).last_error

    def clear(self):
        """Remove all ops and in-flight markers, persist."""
        cleared = len(self._ops)
        self._ops = []
        self._in_flight = set()
        self._save()
        log.debug('clear: dropped %d ops', cleared)

    def clear_for_user(self, user_id: int) -> int:
        """Drop every op belonging to a given user. Returns count removed."""
        before = len(self._ops)
        self._ops = [op for op in self._ops if op.user_id != user_id]
        removed = before - len(self._ops)
        if removed:
            self._save()
        log.debug('clear_for_user user_id=%d: removed %d ops (size=%d)',
                  user_id, removed, len(self._ops))
        return removed

    def reset_all_retries(self):
        """Set next_retry_at to 0 for every op so they become immediately ready.

        Used by the "Retry now" button. Attempt counts and last_error values
        are preserved — this is a user-initiated wake-up, not a reset.
        """
        for op in self._ops:
            op.next_retry_at = 0.0
        self._save()

    def size(self) -> int:
        return len(self._ops)
