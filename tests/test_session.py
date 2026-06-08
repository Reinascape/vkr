# tests/test_session.py
# SPDX-License-Identifier: GPL-3.0-or-later

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

import pytest
from unittest.mock import patch, MagicMock
from kitsune.auth.session import SessionManager


class FakeClient:
    def __init__(self):
        self._token_getter = None
        self._token_expired_handler = None

    def set_token_getter(self, getter):
        self._token_getter = getter

    def set_token_expired_handler(self, handler):
        self._token_expired_handler = handler

    def login(self, login, password, callback=None):
        if login == 'good' and password == 'pass':
            callback('token-abc', None)
        else:
            callback(None, 'HTTP 401')

    def logout(self, callback=None):
        callback({'token': None}, None)

    def get_profile(self, callback=None):
        callback(MagicMock(id=1, nickname='Test'), None)

    def get_otp(self, device_id, callback=None):
        callback({'otp': {'code': '058701'}, 'remaining_time': 120}, None)

    def login_otp(self, code, device_id, callback=None):
        if code == 58701:
            callback('token-otp', None)
        else:
            callback(None, 'HTTP 404')


@patch('kitsune.auth.session.token_store')
def test_login_success(mock_store):
    mock_store.load_token.return_value = None
    sm = SessionManager(FakeClient())
    results = []
    sm.login_with_credentials('good', 'pass', lambda ok, err: results.append((ok, err)))
    assert results[0] == (True, None)
    mock_store.save_token.assert_called_with('token-abc')
    assert sm.is_logged_in()


@patch('kitsune.auth.session.token_store')
def test_login_failure(mock_store):
    mock_store.load_token.return_value = None
    sm = SessionManager(FakeClient())
    results = []
    sm.login_with_credentials('bad', 'bad', lambda ok, err: results.append((ok, err)))
    assert results[0] == (False, 'HTTP 401')
    assert not sm.is_logged_in()


@patch('kitsune.auth.session.token_store')
def test_logout(mock_store, mock_synced_storage):
    mock_store.load_token.return_value = 'existing-token'
    sm = SessionManager(FakeClient())
    sm.logout(lambda ok, err: None)
    mock_store.delete_token.assert_called_once()
    assert not sm.is_logged_in()


@patch('kitsune.auth.session.token_store')
def test_restore_session(mock_store):
    mock_store.load_token.return_value = 'saved-token'
    sm = SessionManager(FakeClient())
    assert sm.is_logged_in()
    assert sm.get_token() == 'saved-token'


@patch('kitsune.auth.session.token_store')
def test_no_saved_token(mock_store):
    mock_store.load_token.return_value = None
    sm = SessionManager(FakeClient())
    assert not sm.is_logged_in()


@patch('kitsune.auth.session.token_store')
def test_otp_login(mock_store):
    mock_store.load_token.return_value = None
    sm = SessionManager(FakeClient())
    results = []
    sm.login_with_otp(58701, 'device-1', lambda ok, err: results.append((ok, err)))
    assert results[0] == (True, None)
    mock_store.save_token.assert_called_with('token-otp')


# --- Stage 6: expired state ---

@pytest.fixture
def client_stub():
    """FakeClient instance with token_store patched to return None."""
    with patch('kitsune.auth.session.token_store') as mock_store:
        mock_store.load_token.return_value = None
        yield FakeClient()


def test_is_expired_false_by_default(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    assert sm.is_expired() is False


def test_on_token_expired_sets_flag(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._on_token_expired()
    assert sm.is_expired() is True


def test_on_token_expired_is_idempotent(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    emitted = []
    sm.connect_session_expired(lambda: emitted.append(True))
    sm._on_token_expired()
    sm._on_token_expired()
    sm._on_token_expired()
    assert len(emitted) == 1
    assert sm.is_expired() is True


def test_clear_expired_resets_flag_and_emits_restored(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    emitted = []
    sm.connect_session_restored(lambda: emitted.append(True))
    sm._on_token_expired()
    sm.clear_expired()
    assert sm.is_expired() is False
    assert emitted == [True]


def test_clear_expired_noop_when_not_expired(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    emitted = []
    sm.connect_session_restored(lambda: emitted.append(True))
    sm.clear_expired()
    assert sm.is_expired() is False
    assert emitted == []


def test_is_logged_in_still_true_when_expired(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._token = 'some-token'
    sm._on_token_expired()
    assert sm.is_logged_in() is True
    assert sm.is_expired() is True


def test_session_registers_token_expired_handler_with_client(client_stub):
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    assert sm.is_expired() is False
    assert client_stub._token_expired_handler is not None
    client_stub._token_expired_handler()
    assert sm.is_expired() is True


def test_clear_token_wipes_expired_flag(client_stub):
    """Logout wipes the expired flag so a reused SessionManager starts fresh.

    This is a latent-bug regression test: without clearing _expired on
    logout, a reused SessionManager would report is_expired()=True after
    a subsequent fresh login until something explicitly cleared the flag.
    """
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._token = 'some-token'
    sm._on_token_expired()
    assert sm.is_expired() is True
    # Simulate logout via _clear_token
    restored_events = []
    sm.connect_session_restored(lambda: restored_events.append(True))
    sm._clear_token()
    assert sm.is_expired() is False
    # Logout is terminal — no session-restored emit
    assert restored_events == []


def test_force_logout_cleanup_clears_synced_tags(
        client_stub, mock_synced_storage):
    """Synced tags (favorites, watching, etc.) are cleared; custom tags stay."""
    from kitsune.auth.session import SessionManager
    from kitsune.storage import tags_store
    tags_store.add_release('favorites', 9275)
    tags_store.add_release('watching', 9276)
    tags_store.create_tag('Custom', 'emoji', '🔥')
    custom_id = [t['id'] for t in tags_store.get_all_tags()
                 if not t.get('builtin')][0]
    tags_store.add_release(custom_id, 8888)

    sm = SessionManager(client_stub)
    sm.force_logout_cleanup()

    assert tags_store.get_release_ids_for_tag('favorites') == []
    assert tags_store.get_release_ids_for_tag('watching') == []
    # Custom tag preserved
    assert 8888 in tags_store.get_release_ids_for_tag(custom_id)


def test_force_logout_cleanup_clears_watch_positions(
        client_stub, mock_synced_storage):
    from kitsune.auth.session import SessionManager
    from kitsune.storage import watch_positions
    mock_tags, mock_positions, _ = mock_synced_storage
    watch_positions.save_position(9275, 1.0, 60.0, episode_id='ep.0')
    assert mock_positions.exists()

    sm = SessionManager(client_stub)
    sm.force_logout_cleanup()

    assert not mock_positions.exists()


def test_force_logout_cleanup_clears_episode_index(
        client_stub, mock_synced_storage):
    from kitsune.auth.session import SessionManager
    from kitsune.storage import episode_index
    _, _, mock_index = mock_synced_storage
    episode_index.add_from_release_data(
        9275, {'episodes': [{'id': 'ep.0', 'ordinal': 1.0}]})
    assert mock_index.exists()

    sm = SessionManager(client_stub)
    sm.force_logout_cleanup()

    assert not mock_index.exists()


def test_force_logout_cleanup_does_not_call_server_logout(
        client_stub, mock_synced_storage):
    """force_logout_cleanup must NOT call client.logout() — the token
    is already rejected, a server logout call would just 401."""
    from kitsune.auth.session import SessionManager
    calls = []

    # Extend the stub with a logout method that records calls
    original_cls = type(client_stub)

    class InstrumentedStub(original_cls):
        def logout(self, callback=None):
            calls.append('logout')
            if callback:
                callback(None, None)

    stub = InstrumentedStub()
    sm = SessionManager(stub)
    sm.force_logout_cleanup()
    assert calls == []


# --- Stage 6 post-review coverage ---

def test_concurrent_401_storm_emits_session_expired_once(client_stub):
    """Three rapid 401s (e.g. _sync_favorites, _sync_collections,
    pull_timecodes all failing in startup validation) must coalesce
    into exactly ONE session-expired emit."""
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    emitted = []
    sm.connect_session_expired(lambda: emitted.append(True))
    # Three 401s as would arrive from concurrent in-flight requests
    client_stub._token_expired_handler()
    client_stub._token_expired_handler()
    client_stub._token_expired_handler()
    assert len(emitted) == 1
    assert sm.is_expired() is True


def test_expired_to_restored_chain_fires_session_restored(client_stub):
    """End-to-end restore chain: _on_token_expired → clear_expired →
    session-restored subscribers fire. Stage 7's auth_dialog will drive
    this chain after successful re-login."""
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    expired_events = []
    restored_events = []
    sm.connect_session_expired(lambda: expired_events.append(True))
    sm.connect_session_restored(lambda: restored_events.append(True))
    # Simulate: server 401
    client_stub._token_expired_handler()
    assert expired_events == [True]
    assert restored_events == []
    # Simulate: successful re-login by the same user
    sm.clear_expired()
    assert restored_events == [True]
    assert sm.is_expired() is False


def test_validate_session_failure_enters_expired_not_logged_out(client_stub):
    """On startup, if the saved token is rejected (401 / 403), we must
    NOT wipe it — that would trigger connect_logged_out and drop the
    pending sync queue. Instead enter the expired state so the banner
    shows and the queue is preserved for the next re-login."""
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._token = 'stale-token'
    logged_out_events = []
    expired_events = []
    sm.connect_logged_out(lambda: logged_out_events.append(True))
    sm.connect_session_expired(lambda: expired_events.append(True))

    # Fake client's get_profile returns an error
    def fake_get_profile(callback=None):
        if callback:
            callback(None, 'HTTP forbidden')
    client_stub.get_profile = fake_get_profile

    results = []
    sm.validate_session(lambda ok, err: results.append((ok, err)))

    assert results == [(False, 'HTTP forbidden')]
    assert expired_events == [True]
    assert logged_out_events == []  # queue must NOT be dropped
    assert sm.is_expired() is True
    assert sm.is_logged_in() is True  # token still there for re-login path
    assert sm.get_token() == 'stale-token'


def test_logged_out_during_401_flow_leaves_clean_state(client_stub):
    """Startup 401 scenario: validate_session fails, _clear_token fires
    logged_out. Expired flag must be False after logged_out so any
    future login starts fresh."""
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._token = 'expiring-token'
    logged_out_events = []
    sm.connect_logged_out(lambda: logged_out_events.append(True))
    # Server returns 401 on the validation request
    client_stub._token_expired_handler()
    assert sm.is_expired() is True
    # Then validate_session's error callback runs _clear_token
    sm._clear_token()
    # Logged out → expired flag wiped → any subsequent fresh login
    # starts clean
    assert sm.is_expired() is False
    assert sm.is_logged_in() is False
    assert logged_out_events == [True]


# --- AuthDialog account-switch decision logic ---

def test_auth_dialog_same_user_relogin_clears_expired(client_stub):
    """Same-user re-login during expired: clear_expired + no cleanup."""
    from kitsune.auth.session import SessionManager
    from kitsune.ui.auth_dialog import AuthDialog
    from kitsune.models.user import User
    sm = SessionManager(client_stub)
    sm._user = User(id=42, login='u', email='', nickname='', avatar=None,
                    is_banned=False, created_at='')
    sm._on_token_expired()
    assert sm.is_expired() is True

    # Mock the cleanup path to prove it's NOT called
    called_force = []
    sm.force_logout_cleanup = lambda: called_force.append(True)

    class FakeSync:
        class _Queue:
            cleared_for = []
            def clear_for_user(self, uid):
                type(self).cleared_for.append(uid)
        _queue = _Queue()
        set_user_id_calls = []
        def set_user_id(self, uid):
            type(self).set_user_id_calls.append(uid)

    fake_sync = FakeSync()

    # Simulate AuthDialog instance (bypass __init__ to avoid GTK setup)
    dialog = AuthDialog.__new__(AuthDialog)
    dialog._session = sm
    dialog._sync = fake_sync

    new_user = User(id=42, login='u2', email='', nickname='',
                    avatar=None, is_banned=False, created_at='')
    dialog._apply_login_to_session(new_user, old_user_id=42, was_expired=True)

    assert sm.is_expired() is False
    assert called_force == []  # no cleanup on same-user
    assert FakeSync._Queue.cleared_for == []
    assert FakeSync.set_user_id_calls == [42]


def test_auth_dialog_different_user_relogin_force_cleanup(client_stub):
    """Different-user re-login during expired: force_logout_cleanup +
    queue wipe + clear_expired."""
    from kitsune.auth.session import SessionManager
    from kitsune.ui.auth_dialog import AuthDialog
    from kitsune.models.user import User
    sm = SessionManager(client_stub)
    sm._user = User(id=42, login='u', email='', nickname='', avatar=None,
                    is_banned=False, created_at='')
    sm._on_token_expired()

    called_force = []
    sm.force_logout_cleanup = lambda: called_force.append(True)

    class FakeSync:
        class _Queue:
            cleared_for = []
            def clear_for_user(self, uid):
                type(self).cleared_for.append(uid)
        _queue = _Queue()
        set_user_id_calls = []
        def set_user_id(self, uid):
            type(self).set_user_id_calls.append(uid)

    dialog = AuthDialog.__new__(AuthDialog)
    dialog._session = sm
    dialog._sync = FakeSync()

    new_user = User(id=999, login='other', email='', nickname='',
                    avatar=None, is_banned=False, created_at='')
    dialog._apply_login_to_session(new_user, old_user_id=42, was_expired=True)

    assert sm.is_expired() is False
    assert called_force == [True]
    assert FakeSync._Queue.cleared_for == [42]
    assert FakeSync.set_user_id_calls == [999]


def test_auth_dialog_fresh_login_no_session_transitions(client_stub):
    """Fresh login (not expired): no cleanup, no clear_expired emit."""
    from kitsune.auth.session import SessionManager
    from kitsune.ui.auth_dialog import AuthDialog
    from kitsune.models.user import User
    sm = SessionManager(client_stub)
    assert sm.is_expired() is False

    called_force = []
    sm.force_logout_cleanup = lambda: called_force.append(True)
    restored_events = []
    sm.connect_session_restored(lambda: restored_events.append(True))

    dialog = AuthDialog.__new__(AuthDialog)
    dialog._session = sm
    dialog._sync = None

    new_user = User(id=42, login='u', email='', nickname='',
                    avatar=None, is_banned=False, created_at='')
    dialog._apply_login_to_session(new_user, old_user_id=None, was_expired=False)

    assert called_force == []
    assert restored_events == []  # clear_expired wasn't called


def test_auth_dialog_first_login_during_expired_is_safe(client_stub):
    """Edge case: was_expired=True but no prior user (app started with
    a stale token whose validate_session failed — _clear_token was
    called, _user is None, but _expired flag re-flipped on some later
    request). Logging in should clear_expired without calling the
    cleanup path (there's nothing to clean)."""
    from kitsune.auth.session import SessionManager
    from kitsune.ui.auth_dialog import AuthDialog
    from kitsune.models.user import User
    sm = SessionManager(client_stub)
    sm._on_token_expired()
    assert sm.is_expired() is True
    assert sm.get_user() is None

    called_force = []
    sm.force_logout_cleanup = lambda: called_force.append(True)

    class FakeSync:
        class _Queue:
            cleared_for = []
            def clear_for_user(self, uid):
                type(self).cleared_for.append(uid)
        _queue = _Queue()
        set_user_id_calls = []
        def set_user_id(self, uid):
            type(self).set_user_id_calls.append(uid)

    dialog = AuthDialog.__new__(AuthDialog)
    dialog._session = sm
    dialog._sync = FakeSync()

    new_user = User(id=42, login='u', email='', nickname='',
                    avatar=None, is_banned=False, created_at='')
    dialog._apply_login_to_session(new_user, old_user_id=None, was_expired=True)

    # clear_expired called but no cleanup fired
    assert sm.is_expired() is False
    assert called_force == []
    assert FakeSync._Queue.cleared_for == []
    assert FakeSync.set_user_id_calls == [42]


def test_expired_flag_is_true_during_on_logged_in_callback(client_stub):
    """Session _on_logged_in listeners are fired BEFORE auth_dialog's
    _finalize_login runs clear_expired. So window._on_logged_in can
    check is_expired() to skip the merge dialog — this test pins that
    invariant: _set_token does not touch _expired.
    """
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._on_token_expired()
    assert sm.is_expired() is True
    observed_expired_during_login = []
    sm.connect_logged_in(
        lambda: observed_expired_during_login.append(sm.is_expired()))
    # Simulate successful re-login
    sm._set_token('new-token')
    assert observed_expired_during_login == [True]  # flag still True in callback


# --- Extended logout ---

def test_logout_calls_force_logout_cleanup_before_server(client_stub, mock_tags):
    """logout() wipes synced local data before the server POST.

    Even if the server POST fails or hangs, the local data is already
    cleared — the user sees an empty profile immediately after clicking
    Log out.
    """
    from kitsune.auth.session import SessionManager
    from kitsune.storage import tags_store
    tags_store.add_release('favorites', 9275)
    tags_store.add_release('watching', 9276)

    sm = SessionManager(client_stub)
    sm._token = 'some-token'
    # Mock client.logout to simulate server call
    call_order = []

    class InstrumentedClient:
        def logout(self, callback=None):
            call_order.append('server_logout')
            if callback:
                callback(None, None)

    sm._client = InstrumentedClient()
    # Wrap force_logout_cleanup to record order
    original_cleanup = sm.force_logout_cleanup
    def wrapped_cleanup():
        call_order.append('force_logout_cleanup')
        original_cleanup()
    sm.force_logout_cleanup = wrapped_cleanup

    sm.logout()

    # Local cleanup BEFORE server call
    assert call_order == ['force_logout_cleanup', 'server_logout']
    # Synced tags actually wiped
    assert tags_store.get_release_ids_for_tag('favorites') == []
    assert tags_store.get_release_ids_for_tag('watching') == []


def test_logout_fires_logged_out_signal_after_cleanup(
        client_stub, mock_tags):
    """logged_out signal fires AFTER local cleanup so subscribers see
    the clean state."""
    from kitsune.auth.session import SessionManager
    from kitsune.storage import tags_store
    tags_store.add_release('favorites', 9275)

    sm = SessionManager(client_stub)
    sm._token = 'some-token'

    observed_tags_at_logout = []
    sm.connect_logged_out(
        lambda: observed_tags_at_logout.append(
            list(tags_store.get_release_ids_for_tag('favorites'))))

    sm.logout()

    # Subscriber sees empty tags (cleanup happened first)
    assert observed_tags_at_logout == [[]]


def test_logout_resets_expired_flag(client_stub, mock_synced_storage):
    """logout on an expired session correctly resets the _expired flag."""
    from kitsune.auth.session import SessionManager
    sm = SessionManager(client_stub)
    sm._token = 'some-token'
    sm._on_token_expired()
    assert sm.is_expired() is True
    sm.logout()
    assert sm.is_expired() is False
    assert sm.is_logged_in() is False
