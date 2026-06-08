# SPDX-License-Identifier: GPL-3.0-or-later

"""End-to-end integration tests for the sync overhaul.

These tests drive SessionManager + SyncManager + PendingQueue +
watch_positions + episode_index together through fake HTTP clients,
validating the full sync-lifecycle scenarios:
  - explicit logout wipes synced data but preserves custom tags
  - expired → same-user re-login resumes pending ops
  - expired → different-user re-login drops old-user data
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))
sys.path.insert(0, os.path.dirname(__file__))

from kitsune.auth.session import SessionManager
from kitsune.storage import tags_store, watch_positions, episode_index
from kitsune.storage.pending_queue import (
    PendingQueue, OP_ADD_FAVORITE,
)
from kitsune.storage.sync_manager import SyncManager
from kitsune.models.user import User


class FakeClient:
    """Minimal client supporting the surface both SessionManager and
    SyncManager need for these integration tests."""
    def __init__(self):
        self._get_token = None
        self._token_expired_handler = None
        self.logout_calls = 0
        self.add_favorites_calls = []
        self.profile_to_return = None

    def set_token_getter(self, getter):
        self._get_token = getter

    def set_token_expired_handler(self, handler):
        self._token_expired_handler = handler

    def set_on_network_error(self, cb): pass
    def set_on_network_ok(self, cb): pass

    def logout(self, callback=None):
        self.logout_calls += 1
        if callback:
            callback(None, None)

    def add_favorites(self, release_ids, callback=None):
        self.add_favorites_calls.append(list(release_ids))
        if callback:
            callback(None, None)

    def remove_favorites(self, release_ids, callback=None):
        if callback:
            callback(None, None)

    def add_to_collection(self, release_id, collection_type, callback=None):
        if callback:
            callback(None, None)

    def remove_from_collection(self, release_ids, callback=None):
        if callback:
            callback(None, None)

    def save_timecodes(self, timecodes, callback=None):
        if callback:
            callback(None, None)

    def get_favorite_ids(self, callback=None):
        if callback:
            callback([], None)

    def get_collection_ids(self, callback=None):
        if callback:
            callback([], None)

    def get_timecodes(self, since=None, callback=None):
        if callback:
            callback([], None)

    def get_profile(self, callback=None):
        if callback:
            callback(self.profile_to_return, None)


def _setup_storage_tmp(monkeypatch, tmp_path):
    """Redirect all storage files to tmp_path for test isolation."""
    tags_file = tmp_path / 'tags.json'
    wp_file = tmp_path / 'watch_positions.json'
    idx_file = tmp_path / 'episode_index.json'
    monkeypatch.setattr(tags_store, '_TAGS_FILE', tags_file)
    monkeypatch.setattr(watch_positions, '_POSITIONS_FILE', wp_file)
    monkeypatch.setattr(episode_index, '_INDEX_FILE', idx_file)
    monkeypatch.setattr(episode_index, '_cache', None)


def test_logout_wipes_synced_data_but_preserves_custom_tags(
        monkeypatch, tmp_path):
    """Explicit logout: synced tags + watch_positions + episode_index +
    pending queue all cleared; custom tags survive."""
    _setup_storage_tmp(monkeypatch, tmp_path)
    client = FakeClient()
    session = SessionManager(client)
    session._token = 'test-token'
    sync = SyncManager(client)
    sync._queue = PendingQueue(tmp_path / 'pending.json')
    sync.set_user_id(42)
    session.connect_logged_out(sync.clear_queue_on_logout)

    # Populate ALL the data
    tags_store.add_release('favorites', 9275)
    tags_store.add_release('watching', 9276)
    tags_store.create_tag('My Custom Tag', 'emoji', '🔥')
    custom_id = [t['id'] for t in tags_store.get_all_tags()
                 if not t.get('builtin')][0]
    tags_store.add_release(custom_id, 8888)
    watch_positions.save_position(9275, 1.0, 60.0, episode_id='ep.0')
    episode_index.add_from_release_data(
        9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    sync._queue.enqueue(OP_ADD_FAVORITE, 7777, user_id=42)
    assert sync._queue.size() == 1

    # Logout
    session.logout()

    # Synced data wiped
    assert tags_store.get_release_ids_for_tag('favorites') == []
    assert tags_store.get_release_ids_for_tag('watching') == []
    assert watch_positions.get_count() == 0
    assert episode_index.lookup('ep.0') is None
    assert sync._queue.size() == 0

    # Custom tag survived
    assert 8888 in tags_store.get_release_ids_for_tag(custom_id)

    # Server logout attempted
    assert client.logout_calls == 1

    # Session is logged out and not expired
    assert session.is_logged_in() is False
    assert session.is_expired() is False


def test_expired_same_user_relogin_resumes_sync(monkeypatch, tmp_path):
    """Token expires mid-session, user re-logs in as same user, pending
    ops resume dispatch via session-restored → resume_after_expired_session."""
    _setup_storage_tmp(monkeypatch, tmp_path)
    client = FakeClient()
    session = SessionManager(client)
    session._token = 'old-token'
    session._user = User(id=42, login='u', email='', nickname='',
                         avatar=None, is_banned=False, created_at='')
    sync = SyncManager(client)
    sync._queue = PendingQueue(tmp_path / 'pending.json')
    sync.set_user_id(42)
    session.connect_session_expired(sync.pause_for_expired_session)
    session.connect_session_restored(sync.resume_after_expired_session)

    # User queues an op, then token expires
    sync._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)
    session._on_token_expired()
    assert session.is_expired() is True

    # Same-user re-login (auth_dialog._apply_login_to_session path)
    same_user = User(id=42, login='u', email='', nickname='',
                     avatar=None, is_banned=False, created_at='')
    # Simulate the auth_dialog decision
    if session.is_expired():
        if same_user.id != session.get_user().id:
            session.force_logout_cleanup()
            sync._queue.clear_for_user(session.get_user().id)
        session.clear_expired()

    # Queue op survived (same user)
    assert sync._queue.size() == 1
    assert session.is_expired() is False


def test_expired_different_user_relogin_wipes_old_user_data(
        monkeypatch, tmp_path):
    """Token expires, user re-logs in as DIFFERENT user, old data wiped."""
    _setup_storage_tmp(monkeypatch, tmp_path)
    client = FakeClient()
    session = SessionManager(client)
    session._token = 'old-token'
    session._user = User(id=42, login='old', email='', nickname='',
                         avatar=None, is_banned=False, created_at='')
    sync = SyncManager(client)
    sync._queue = PendingQueue(tmp_path / 'pending.json')
    sync.set_user_id(42)

    # Old user's data populated
    tags_store.add_release('favorites', 9275)
    tags_store.create_tag('Persistent Custom', 'emoji', '⭐')
    custom_id = [t['id'] for t in tags_store.get_all_tags()
                 if not t.get('builtin')][0]
    tags_store.add_release(custom_id, 8888)
    sync._queue.enqueue(OP_ADD_FAVORITE, 7777, user_id=42)

    # Token expires, then new user logs in
    session._on_token_expired()
    new_user = User(id=999, login='other', email='', nickname='',
                    avatar=None, is_banned=False, created_at='')
    # Simulate the auth_dialog.different-user decision
    if session.is_expired():
        if new_user.id != session.get_user().id:
            session.force_logout_cleanup()
            sync._queue.clear_for_user(session.get_user().id)
        session.clear_expired()

    # Old user's synced data wiped
    assert tags_store.get_release_ids_for_tag('favorites') == []
    # Queue ops for old user dropped
    assert sync._queue.size() == 0
    # Custom tag preserved
    assert 8888 in tags_store.get_release_ids_for_tag(custom_id)
    # Session restored
    assert session.is_expired() is False


def test_logout_then_fresh_login_starts_clean(monkeypatch, tmp_path):
    """After logout + fresh login, the app starts with no residual
    pending ops or stale data."""
    _setup_storage_tmp(monkeypatch, tmp_path)
    client = FakeClient()
    session = SessionManager(client)
    session._token = 'old-token'
    sync = SyncManager(client)
    sync._queue = PendingQueue(tmp_path / 'pending.json')
    sync.set_user_id(42)
    session.connect_logged_out(sync.clear_queue_on_logout)

    tags_store.add_release('favorites', 9275)
    sync._queue.enqueue(OP_ADD_FAVORITE, 9275, user_id=42)

    session.logout()

    assert tags_store.get_release_ids_for_tag('favorites') == []
    assert sync._queue.size() == 0
    assert session.is_logged_in() is False

    # Fresh login — no previous data should haunt us
    session._set_token('new-token')
    assert session.is_logged_in() is True
    assert session.is_expired() is False
    assert tags_store.get_release_ids_for_tag('favorites') == []
    assert sync._queue.size() == 0
