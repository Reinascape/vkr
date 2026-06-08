# SPDX-License-Identifier: GPL-3.0-or-later

"""Widget tests for ProfileView — require xvfb + compiled gresource."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.ui.profile_view import ProfileView


class FakeSession:
    def __init__(self):
        self._logged_in_cbs = []
        self._logged_out_cbs = []

    def is_logged_in(self):
        return True

    def get_user(self):
        return None

    def connect_logged_in(self, cb):
        self._logged_in_cbs.append(cb)

    def connect_logged_out(self, cb):
        self._logged_out_cbs.append(cb)

    def logout(self, callback=None):
        if callback:
            callback(True, None)


class FakeSyncManager:
    def __init__(self, size=0, has_errors=False, last_error=None):
        self._size = size
        self._has_errors = has_errors
        self._last_error = last_error
        self._queue_cbs = []
        self._complete_cbs = []
        self._error_cbs = []
        self.force_drain_calls = 0

    def queue_size(self):
        return self._size

    def queue_has_errors(self):
        return self._has_errors

    def last_queue_error(self):
        return self._last_error

    def connect_queue_changed(self, cb):
        self._queue_cbs.append(cb)

    def connect_sync_complete(self, cb):
        self._complete_cbs.append(cb)

    def connect_sync_error(self, cb):
        self._error_cbs.append(cb)

    def force_drain(self):
        self.force_drain_calls += 1

    def fire_queue_changed(self, size):
        self._size = size
        for cb in self._queue_cbs:
            cb(size)


def _make_view(sync_manager):
    return ProfileView(
        session_manager=FakeSession(),
        on_navigate_tag=lambda t: None,
        sync_manager=sync_manager)


def test_indicator_hidden_when_queue_empty():
    sync = FakeSyncManager(size=0)
    view = _make_view(sync)
    assert view.pending_row.get_visible() is False
    assert view.error_row.get_visible() is False
    assert view.retry_button.get_visible() is False


def test_indicator_shows_pending_count_singular():
    sync = FakeSyncManager(size=1)
    view = _make_view(sync)
    assert view.pending_row.get_visible() is True
    assert '1' in view.pending_label.get_label()


def test_indicator_shows_pending_count_plural():
    sync = FakeSyncManager(size=5)
    view = _make_view(sync)
    assert view.pending_row.get_visible() is True
    assert '5' in view.pending_label.get_label()


def test_error_row_shows_last_error():
    sync = FakeSyncManager(size=2, has_errors=True, last_error='Network timeout')
    view = _make_view(sync)
    assert view.error_row.get_visible() is True
    assert 'Network timeout' in view.error_text_label.get_label()


def test_retry_button_visible_only_on_errors():
    sync = FakeSyncManager(size=2, has_errors=False)
    view = _make_view(sync)
    assert view.retry_button.get_visible() is False
    sync2 = FakeSyncManager(size=2, has_errors=True, last_error='x')
    view2 = _make_view(sync2)
    assert view2.retry_button.get_visible() is True


def test_retry_click_calls_force_drain():
    sync = FakeSyncManager(size=2, has_errors=True, last_error='x')
    view = _make_view(sync)
    view.on_retry_clicked(view.retry_button)
    assert sync.force_drain_calls == 1


def test_queue_changed_updates_indicator():
    sync = FakeSyncManager(size=0)
    view = _make_view(sync)
    assert view.pending_row.get_visible() is False
    sync.fire_queue_changed(3)
    assert view.pending_row.get_visible() is True
    assert '3' in view.pending_label.get_label()
