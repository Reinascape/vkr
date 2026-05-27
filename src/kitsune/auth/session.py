# src/kitsune/auth/session.py
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import uuid

from kitsune.auth import token_store

log = logging.getLogger('kitsune.session')


class SessionManager:
    def __init__(self, client):
        self._client = client
        self._token = token_store.load_token()
        self._user = None
        self._device_id = str(uuid.uuid4())
        self._expired = False
        self._on_logged_in = []
        self._on_logged_out = []
        self._on_pre_logout = []
        self._on_session_expired = []
        self._on_session_restored = []

        log.debug('init: token_loaded=%s device_id=%s',
                  bool(self._token), self._device_id)

        client.set_token_getter(self.get_token)
        if hasattr(client, 'set_token_expired_handler'):
            client.set_token_expired_handler(self._on_token_expired)

    def is_logged_in(self):
        return self._token is not None

    def get_token(self):
        return self._token

    def get_user(self):
        return self._user

    def connect_logged_in(self, callback):
        self._on_logged_in.append(callback)

    def connect_logged_out(self, callback):
        self._on_logged_out.append(callback)

    def connect_pre_logout(self, callback):
        """callback() — fired BEFORE force_logout_cleanup runs.

        Subscribers should pause anything that could race-commit on the
        still-valid token (sync drain, periodic pulls, …) so the local
        wipe doesn't fight with an in-flight HTTP that the server has
        not yet seen.
        """
        self._on_pre_logout.append(callback)

    def is_expired(self):
        return self._expired

    def connect_session_expired(self, callback):
        """callback() — fired when server rejects our token (401)."""
        self._on_session_expired.append(callback)

    def connect_session_restored(self, callback):
        """callback() — fired when expired session is cleared (re-login)."""
        self._on_session_restored.append(callback)

    def _emit_session_expired(self):
        for cb in self._on_session_expired:
            cb()

    def _emit_session_restored(self):
        for cb in self._on_session_restored:
            cb()

    def _on_token_expired(self):
        """Called by ApiClient when the server returns 401.

        Idempotent — repeated 401s during a single expired window only
        emit session-expired once.
        """
        if self._expired:
            log.debug('token_expired (already expired, ignored)')
            return
        log.debug('token_expired → marking expired, emitting session-expired')
        self._expired = True
        self._emit_session_expired()

    def clear_expired(self):
        """Reset the expired flag after a successful re-login. No-op
        if the session was never expired."""
        if not self._expired:
            return
        log.debug('clear_expired → emitting session-restored')
        self._expired = False
        self._emit_session_restored()

    def _set_token(self, token):
        log.debug('set_token: new_token=%s', bool(token))
        self._token = token
        token_store.save_token(token)
        for cb in self._on_logged_in:
            cb()

    def _clear_token(self):
        log.debug('clear_token: had_token=%s', bool(self._token))
        self._token = None
        self._user = None
        # Logout is terminal: wipe expired flag so a reused SessionManager
        # starts fresh. No session-restored emit — the session is gone,
        # not restored.
        self._expired = False
        token_store.delete_token()
        for cb in self._on_logged_out:
            cb()

    def login_with_credentials(self, login, password, callback=None):
        log.debug('login_with_credentials: login=%s', login)
        def on_result(token, error):
            if error or not token:
                log.debug('login_with_credentials failed: %s', error)
                if callback:
                    callback(False, error)
                return
            self._set_token(token)
            if callback:
                callback(True, None)
        self._client.login(login, password, on_result)

    def login_with_otp(self, code, device_id, callback=None):
        log.debug('login_with_otp: device_id=%s', device_id)
        def on_result(token, error):
            if error or not token:
                log.debug('login_with_otp failed: %s', error)
                if callback:
                    callback(False, error)
                return
            self._set_token(token)
            if callback:
                callback(True, None)
        self._client.login_otp(code, device_id, on_result)

    def start_otp(self, callback=None):
        self._client.get_otp(self._device_id, callback)

    def get_device_id(self):
        return self._device_id

    def start_social_login(self, provider, callback=None):
        self._client.get_social_login_url(provider, callback)

    def poll_social_login(self, state, callback=None):
        def on_result(token, error):
            if error or not token:
                if callback:
                    callback(False, error)
                return
            self._set_token(token)
            if callback:
                callback(True, None)
        self._client.poll_social_auth(state, on_result)

    def logout(self, callback=None):
        """Wipe synced local data first so the user sees an empty profile
        immediately even if the server POST fails or 401s with an
        already-invalid token.

        Pre-logout callbacks fire before the local wipe so the sync
        drain stops cleanly — without that, an in-flight HTTP could
        commit a server-side change against data we just deleted.
        """
        log.debug('logout requested — running pre-logout callbacks')
        for cb in self._on_pre_logout:
            try:
                cb()
            except Exception:
                log.exception('pre-logout callback raised')
        log.debug('logout: wiping synced local data')
        self.force_logout_cleanup()

        def on_result(data, error):
            log.debug('server logout: error=%s', error)
            self._clear_token()
            if callback:
                callback(True, None)
        self._client.logout(on_result)

    def force_logout_cleanup(self):
        """Clear synced local data without calling the server.

        Used by the account-switch path (Stage 7): when re-login brings a
        different user, we must wipe the previous account's synced data
        locally because the token from the OLD session has already been
        rejected (calling server logout would just 401). This is a
        destructive operation — custom (non-builtin) tags are preserved.
        """
        # Local imports to avoid a circular dependency: sync_manager already
        # imports from kitsune.storage, and importing SYNCED_TAGS at module
        # top would create a cycle at startup.
        from kitsune.storage import tags_store, watch_positions, episode_index
        from kitsune.storage.sync_manager import SYNCED_TAGS
        cleared = 0
        # Clear releases in synced built-in tags (but keep the tags themselves)
        for tag_id in SYNCED_TAGS:
            ids = list(tags_store.get_release_ids_for_tag(tag_id))
            for rid in ids:
                tags_store.remove_release(tag_id, rid)
            cleared += len(ids)
        watch_positions.clear_all()
        episode_index.clear()
        log.debug('force_logout_cleanup: cleared %d synced-tag entries + '
                  'watch_positions + episode_index', cleared)

    def validate_session(self, callback=None):
        if not self._token:
            log.debug('validate_session: no token, skipping')
            if callback:
                callback(False, None)
            return
        log.debug('validate_session: fetching profile')
        def on_profile(user, error):
            if error:
                # The saved token was rejected (401 / 403 / etc.). Don't
                # wipe it — that would take the pending sync queue with it
                # via connect_logged_out. Instead enter the expired flow:
                # banner shown, queue paused, re-login as the same user
                # flushes the queue, re-login as a different user triggers
                # force_logout_cleanup + clear_for_user in the UI.
                log.debug('validate_session failed: %s → entering expired state',
                          error)
                self._on_token_expired()
                if callback:
                    callback(False, error)
                return
            self._user = user
            log.debug('validate_session ok: user_id=%s', user.id if user else None)
            if callback:
                callback(True, None)
        self._client.get_profile(on_profile)

    def fetch_profile(self, callback=None):
        def on_profile(user, error):
            if error:
                if callback:
                    callback(None, error)
                return
            self._user = user
            if callback:
                callback(user, None)
        self._client.get_profile(on_profile)
