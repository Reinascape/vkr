# SPDX-License-Identifier: GPL-3.0-or-later

"""Test double for AniLibriaClient with deferred callbacks.

Every API method records the call in `call_log` and stashes the callback
in `_pending`. Callbacks are NOT fired automatically — the test controls
timing via flush_next(), fail_next(), or flush_all().
"""


class FakeApiClient:
    def __init__(self):
        self.call_log = []
        self._pending = []  # list of (callback, default_data)
        self._get_token = lambda: 'fake-token'
        self._token_expired_handler = None

    # --- Control methods (called by tests) ---

    def flush_next(self, data=None):
        """Fire the oldest pending callback with success (data, None)."""
        if not self._pending:
            raise IndexError('No pending callbacks to flush')
        cb, default_data = self._pending.pop(0)
        cb(data if data is not None else default_data, None)

    def fail_next(self, error='network error'):
        """Fire the oldest pending callback with an error (None, error)."""
        if not self._pending:
            raise IndexError('No pending callbacks to fail')
        cb, _ = self._pending.pop(0)
        cb(None, error)

    def flush_all(self, data=None):
        """Fire all pending callbacks with success."""
        while self._pending:
            self.flush_next(data)

    def pending_count(self):
        return len(self._pending)

    def set_token_expired_handler(self, callback):
        """Register SessionManager's 401-handler."""
        self._token_expired_handler = callback

    def trigger_token_expired(self):
        """Simulate an incoming 401 from the server."""
        if self._token_expired_handler is not None:
            self._token_expired_handler()

    # --- API methods (mirror AniLibriaClient) ---

    def add_favorites(self, release_ids, callback=None):
        self.call_log.append(('add_favorites', list(release_ids)))
        if callback:
            self._pending.append((callback, None))

    def remove_favorites(self, release_ids, callback=None):
        self.call_log.append(('remove_favorites', list(release_ids)))
        if callback:
            self._pending.append((callback, None))

    def add_to_collection(self, release_id, collection_type, callback=None):
        self.call_log.append(('add_to_collection', release_id, collection_type))
        if callback:
            self._pending.append((callback, None))

    def remove_from_collection(self, release_ids, callback=None):
        self.call_log.append(('remove_from_collection', list(release_ids)))
        if callback:
            self._pending.append((callback, None))

    def save_timecodes(self, timecodes, callback=None):
        self.call_log.append(('save_timecodes', list(timecodes)))
        if callback:
            self._pending.append((callback, None))

    # --- Stubs for methods used by SyncManager but not by drain ---

    def get_favorite_ids(self, callback=None):
        self.call_log.append(('get_favorite_ids',))
        if callback:
            self._pending.append((callback, []))

    def get_collection_ids(self, callback=None):
        self.call_log.append(('get_collection_ids',))
        if callback:
            self._pending.append((callback, []))

    def get_timecodes(self, since=None, callback=None):
        self.call_log.append(('get_timecodes',))
        if callback:
            default = getattr(self, 'get_timecodes_response', [])
            self._pending.append((callback, default))

    # --- Network event stubs ---

    def set_on_network_error(self, cb):
        pass

    def set_on_network_ok(self, cb):
        pass

    def set_token_getter(self, getter):
        pass
