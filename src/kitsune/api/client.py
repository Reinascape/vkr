# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import logging

import gi

gi.require_version('Soup', '3.0')

from gi.repository import GLib, Gio, Soup

from kitsune import API_BASE_URL
from kitsune.models import CatalogResponse, Franchise, Genre, Release

log = logging.getLogger('kitsune.api')

_REQUEST_TIMEOUT_MS = 10000
_OFFLINE_TIMEOUT_MS = 2000


def _make_callback(callback, parser):
    """Wrap a user callback with a parser for successful responses."""
    def on_data(data, error):
        if error:
            callback(None, error)
            return
        callback(parser(data), None)
    return on_data


_MAX_RESPONSE_BYTES = 10 * 1024 * 1024


def _parse_success_body(raw):
    """Parse a 200-OK response body. Returns (data, error_message).

    Empty body is a valid success (some AniLibria write endpoints return
    200 with 0 bytes, e.g. POST /me/views/timecodes). If we tried to
    json.loads('') the ValueError used to propagate up and silently
    leave the caller's op stuck in-flight forever.
    """
    if raw is None:
        return None, 'Empty response'
    if len(raw) > _MAX_RESPONSE_BYTES:
        return None, 'Response too large'
    if not raw:
        return None, None
    try:
        return json.loads(raw), None
    except ValueError as e:
        return None, f'invalid JSON: {e}'


class AniLibriaClient:

    def __init__(self, version='0.0.0'):
        self._session = Soup.Session()
        self._session.set_user_agent(f'Kitsune/{version}')
        self._on_network_error = None
        self._on_network_ok = None
        self._offline = False
        self._get_token = None
        self._token_expired_handler = None

    def set_on_network_error(self, callback):
        self._on_network_error = callback

    def set_on_network_ok(self, callback):
        self._on_network_ok = callback

    def set_token_getter(self, getter):
        self._get_token = getter

    def is_authenticated(self) -> bool:
        return bool(self._get_token and self._get_token())

    def set_token_expired_handler(self, callback):
        """Register a callback invoked when the server returns 401.

        Fires before the regular error callback — the caller still receives
        the standard 'HTTP unauthorized' error string and can decide how to
        handle it locally (e.g. login dialog shows "wrong credentials" for
        an explicit login attempt). The handler is typically registered by
        SessionManager to transition into the expired state.
        """
        self._token_expired_handler = callback

    def _fetch(self, path: str, callback, cancellable: Gio.Cancellable | None = None):
        uri = f'{API_BASE_URL}{path}'
        msg = Soup.Message.new('GET', uri)

        token = self._get_token() if self._get_token else None
        if token:
            msg.get_request_headers().append('Authorization', f'Bearer {token}')

        timeout_ms = _OFFLINE_TIMEOUT_MS if self._offline else _REQUEST_TIMEOUT_MS
        state = [False]  # [handled]

        log.debug('GET %s (auth=%s, timeout=%dms)',
                  path, bool(token), timeout_ms)

        def on_timeout():
            if not state[0]:
                state[0] = True
                self._offline = True
                log.debug('GET %s timed out after %dms', path, timeout_ms)
                callback(None, 'timeout')
                if self._on_network_error:
                    self._on_network_error()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add(timeout_ms, on_timeout)

        self._session.send_and_read_async(
            msg, GLib.PRIORITY_DEFAULT, cancellable,
            self._on_response, (callback, msg, state, timeout_id),
        )

    def _handle_error(self, state, timeout_id, callback, error_msg):
        """Mark request handled, cancel timeout, always notify caller."""
        if state[0]:
            return
        state[0] = True
        GLib.source_remove(timeout_id)
        log.debug('request failed: %s', error_msg)
        callback(None, error_msg)
        if not self._offline:
            self._offline = True
            log.debug('marking offline=True (error: %s)', error_msg)
            if self._on_network_error:
                self._on_network_error()

    def _on_response(self, session, result, user_data):
        callback, msg, state, timeout_id = user_data
        if state[0]:
            return  # timeout already handled
        method = msg.get_method()
        path = msg.get_uri().get_path()

        # One-shot callback wrapper. Prevents re-entry if a subscriber
        # inside callback raises and the exception bubbles back up into
        # the except-block below — we must not surface that exception as
        # a second "(None, error)" invocation (that would bury a real
        # logic bug under a bogus sync error and confuse callers that
        # already handled success).
        called = [False]
        def safe_call(data, err):
            if called[0]:
                return
            called[0] = True
            callback(data, err)

        try:
            gbytes = session.send_and_read_finish(result)
            status = msg.get_status()
            if status != Soup.Status.OK:
                # HTTP errors (including 401) are valid server responses,
                # not network failures — we intentionally do NOT flip
                # `self._offline` here. The offline banner stays hidden
                # and _on_network_error is not invoked.
                state[0] = True
                GLib.source_remove(timeout_id)
                log.debug('%s %s → HTTP %d %s',
                          method, path, status.real, status.value_nick)
                if status == Soup.Status.UNAUTHORIZED and self._token_expired_handler:
                    log.debug('%s %s → 401, firing token_expired_handler',
                              method, path)
                    try:
                        self._token_expired_handler()
                    except Exception:
                        log.exception('token_expired_handler raised')
                safe_call(None, f'HTTP {status.value_nick}')
                return
            if gbytes is None:
                self._handle_error(state, timeout_id, safe_call,
                                   'Empty response')
                return
            state[0] = True
            GLib.source_remove(timeout_id)
            raw = gbytes.get_data()
            log.debug('%s %s → HTTP 200 (%d bytes)', method, path, len(raw))
            data, err = _parse_success_body(raw)
            if err:
                log.debug('%s %s: %s', method, path, err)
                safe_call(None, err)
                return
            safe_call(data, None)
            if self._offline:
                self._offline = False
                log.debug('marking offline=False (ok response)')
                if self._on_network_ok:
                    self._on_network_ok()
        except GLib.Error as e:
            if e.matches(Gio.io_error_quark(), Gio.IOErrorEnum.CANCELLED):
                log.debug('%s %s cancelled', method, path)
                if not state[0]:
                    state[0] = True
                    GLib.source_remove(timeout_id)
                return
            self._handle_error(state, timeout_id, safe_call, str(e))
        except Exception as e:
            # If the exception came from a subscriber *after* safe_call
            # already fired, called[0] is True and the second safe_call
            # below is a no-op — the caller has already seen its success
            # result, which is the truth. Only when we crashed *before*
            # any callback does this become a real terminal error signal.
            log.exception('%s %s: unexpected error in response handler',
                          method, path)
            if not state[0]:
                state[0] = True
                GLib.source_remove(timeout_id)
            safe_call(None, str(e))

    def _post(self, path: str, body, callback, cancellable: Gio.Cancellable | None = None):
        uri = f'{API_BASE_URL}{path}'
        msg = Soup.Message.new('POST', uri)

        token = self._get_token() if self._get_token else None
        if token:
            msg.get_request_headers().append('Authorization', f'Bearer {token}')

        body_bytes = 0
        if body is not None:
            encoded = json.dumps(body).encode('utf-8')
            body_bytes = len(encoded)
            msg.set_request_body_from_bytes('application/json', GLib.Bytes.new(encoded))

        timeout_ms = _OFFLINE_TIMEOUT_MS if self._offline else _REQUEST_TIMEOUT_MS
        state = [False]

        log.debug('POST %s (auth=%s, body=%d bytes, timeout=%dms)',
                  path, bool(token), body_bytes, timeout_ms)

        def on_timeout():
            if not state[0]:
                state[0] = True
                self._offline = True
                log.debug('POST %s timed out after %dms', path, timeout_ms)
                callback(None, 'timeout')
                if self._on_network_error:
                    self._on_network_error()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add(timeout_ms, on_timeout)

        self._session.send_and_read_async(
            msg, GLib.PRIORITY_DEFAULT, cancellable,
            self._on_response, (callback, msg, state, timeout_id),
        )

    def _delete(self, path: str, body, callback, cancellable: Gio.Cancellable | None = None):
        uri = f'{API_BASE_URL}{path}'
        msg = Soup.Message.new('DELETE', uri)

        token = self._get_token() if self._get_token else None
        if token:
            msg.get_request_headers().append('Authorization', f'Bearer {token}')

        body_bytes = 0
        if body is not None:
            encoded = json.dumps(body).encode('utf-8')
            body_bytes = len(encoded)
            msg.set_request_body_from_bytes('application/json', GLib.Bytes.new(encoded))

        timeout_ms = _OFFLINE_TIMEOUT_MS if self._offline else _REQUEST_TIMEOUT_MS
        state = [False]

        log.debug('DELETE %s (auth=%s, body=%d bytes, timeout=%dms)',
                  path, bool(token), body_bytes, timeout_ms)

        def on_timeout():
            if not state[0]:
                state[0] = True
                self._offline = True
                log.debug('DELETE %s timed out after %dms', path, timeout_ms)
                callback(None, 'timeout')
                if self._on_network_error:
                    self._on_network_error()
            return GLib.SOURCE_REMOVE

        timeout_id = GLib.timeout_add(timeout_ms, on_timeout)

        self._session.send_and_read_async(
            msg, GLib.PRIORITY_DEFAULT, cancellable,
            self._on_response, (callback, msg, state, timeout_id),
        )

    # --- Authentication ---

    def login(self, login, password, callback=None):
        def on_data(data, error):
            if error:
                callback(None, error)
                return
            callback(data.get('token') if data else None, None)
        self._post('/accounts/users/auth/login',
                   {'login': login, 'password': password}, on_data)

    def logout(self, callback=None):
        self._post('/accounts/users/auth/logout', None, callback)

    def get_otp(self, device_id, callback=None):
        self._post('/accounts/otp/get', {'device_id': device_id}, callback)

    def login_otp(self, code, device_id, callback=None):
        def on_data(data, error):
            if error:
                callback(None, error)
                return
            callback(data.get('token') if data else None, None)
        self._post('/accounts/otp/login',
                   {'code': code, 'device_id': device_id}, on_data)

    def get_social_login_url(self, provider, callback=None):
        from urllib.parse import quote
        self._fetch(f'/accounts/users/auth/social/{quote(provider)}/login', callback)

    def poll_social_auth(self, state, callback=None):
        from urllib.parse import quote
        def on_data(data, error):
            if error:
                callback(None, error)
                return
            callback(data.get('token') if data else None, None)
        self._fetch(f'/accounts/users/auth/social/authenticate?state={quote(state)}', on_data)

    # --- Profile ---

    def get_profile(self, callback=None):
        from kitsune.models.user import User
        self._fetch('/accounts/users/me/profile',
                    _make_callback(callback, User.from_dict))

    # --- Favorites ---

    def get_favorite_ids(self, callback=None):
        self._fetch('/accounts/users/me/favorites/ids', callback)

    def add_favorites(self, release_ids, callback=None):
        body = [{'release_id': rid} for rid in release_ids]
        self._post('/accounts/users/me/favorites', body, callback)

    def remove_favorites(self, release_ids, callback=None):
        body = [{'release_id': rid} for rid in release_ids]
        self._delete('/accounts/users/me/favorites', body, callback)

    # --- Collections ---

    def get_collection_ids(self, callback=None):
        self._fetch('/accounts/users/me/collections/ids', callback)

    def add_to_collection(self, release_id, collection_type, callback=None):
        body = [{'release_id': release_id, 'type_of_collection': collection_type}]
        self._post('/accounts/users/me/collections', body, callback)

    def remove_from_collection(self, release_ids, callback=None):
        body = [{'release_id': rid} for rid in release_ids]
        self._delete('/accounts/users/me/collections', body, callback)

    # --- Timecodes ---

    def get_timecodes(self, since=None, callback=None):
        path = '/accounts/users/me/views/timecodes'
        if since is not None:
            from urllib.parse import quote
            path += f'?since={quote(str(since))}'
        self._fetch(path, callback)

    def save_timecodes(self, timecodes, callback=None):
        self._post('/accounts/users/me/views/timecodes', timecodes, callback)

    def get_catalog(self, page: int = 1, limit: int = 20,
                    filters: dict | None = None,
                    callback=None, cancellable=None):
        params = f'page={page}&limit={limit}'
        if filters:
            from urllib.parse import quote
            for key, value in filters.items():
                if isinstance(value, dict):
                    for sub_key, sub_val in value.items():
                        params += f'&f%5B{quote(key)}%5D%5B{quote(sub_key)}%5D={quote(str(sub_val))}'
                elif isinstance(value, list):
                    for item in value:
                        params += f'&f%5B{quote(key)}%5D%5B%5D={quote(str(item))}'
                elif value is not None:
                    params += f'&f%5B{quote(key)}%5D={quote(str(value))}'
        self._fetch(f'/anime/catalog/releases?{params}',
                    _make_callback(callback, CatalogResponse.from_dict),
                    cancellable)

    def search_releases(self, query: str, callback=None, cancellable=None):
        from urllib.parse import quote
        self._fetch(f'/app/search/releases?query={quote(query)}',
                    _make_callback(callback, lambda d: [Release.from_dict(r) for r in d]),
                    cancellable)

    def get_release(self, id_or_alias: str, callback=None, cancellable=None):
        from urllib.parse import quote
        self._fetch(f'/anime/releases/{quote(str(id_or_alias))}',
                    _make_callback(callback, Release.from_dict),
                    cancellable)

    def get_release_raw(self, id_or_alias: str, callback=None, cancellable=None):
        from urllib.parse import quote
        self._fetch(f'/anime/releases/{quote(str(id_or_alias))}', callback, cancellable)

    def get_genres(self, callback=None, cancellable=None):
        self._fetch('/anime/genres',
                    _make_callback(callback, lambda d: [Genre.from_dict(g) for g in d]),
                    cancellable)

    def get_franchises(self, callback=None, cancellable=None):
        self._fetch('/anime/franchises',
                    _make_callback(callback, lambda d: [Franchise.from_dict(f) for f in d]),
                    cancellable)

    def get_franchise(self, franchise_id: str, callback=None, cancellable=None):
        from urllib.parse import quote
        self._fetch(f'/anime/franchises/{quote(franchise_id)}',
                    _make_callback(callback, Franchise.from_dict),
                    cancellable)

    def get_franchise_for_release(self, release_id: int, callback=None, cancellable=None):
        def on_data(data, error):
            if error:
                callback(None, error)
                return
            if isinstance(data, list) and data:
                callback(Franchise.from_dict(data[0]), None)
            else:
                callback(None, None)

        self._fetch(f'/anime/franchises/release/{release_id}', on_data, cancellable)

    def get_year_range(self, callback=None, cancellable=None):
        """Fetch min and max years from catalog. callback((min_year, max_year), error)."""
        import datetime
        result = {}

        def on_oldest(data, error):
            if error or not data:
                callback(None, error)
                return
            releases = data.get('data', [])
            result['min'] = releases[0].get('year', 2000) if releases else 2000
            self._fetch(
                '/anime/catalog/releases?page=1&limit=1&f%5Bsorting%5D=YEAR_DESC',
                on_newest, cancellable,
            )

        def on_newest(data, error):
            if error or not data:
                callback(None, error)
                return
            releases = data.get('data', [])
            result['max'] = releases[0].get('year', datetime.date.today().year) \
                if releases else datetime.date.today().year
            callback((result['min'], result['max']), None)

        self._fetch(
            '/anime/catalog/releases?page=1&limit=1&f%5Bsorting%5D=YEAR_ASC',
            on_oldest, cancellable,
        )
