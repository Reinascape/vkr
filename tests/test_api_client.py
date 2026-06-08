# SPDX-License-Identifier: GPL-3.0-or-later

"""Unit tests for the pure-Python helpers in kitsune.api.client.

Does not exercise the Soup HTTP stack — those paths are integration-
tested via FakeApiClient in test_sync_manager.py.
"""

from kitsune.api.client import _parse_success_body


def test_parse_success_body_empty_is_success():
    """AniLibria write endpoints (POST /views/timecodes) return 200 with
    a 0-byte body on success. Must not surface as error — doing so left
    queued ops in-flight forever."""
    data, err = _parse_success_body(b'')
    assert data is None
    assert err is None


def test_parse_success_body_valid_json():
    data, err = _parse_success_body(b'{"ok": 1}')
    assert data == {'ok': 1}
    assert err is None


def test_parse_success_body_valid_json_list():
    data, err = _parse_success_body(b'[1, 2, 3]')
    assert data == [1, 2, 3]
    assert err is None


def test_parse_success_body_malformed_json_is_error():
    """Malformed JSON on 200 must produce an error (not a silent success
    with data=None), otherwise callers would treat garbage as success."""
    data, err = _parse_success_body(b'{not json')
    assert data is None
    assert err is not None
    assert 'invalid JSON' in err


def test_parse_success_body_none_bytes_is_error():
    data, err = _parse_success_body(None)
    assert data is None
    assert err == 'Empty response'


def test_parse_success_body_too_large_is_error():
    huge = b'x' * (10 * 1024 * 1024 + 1)
    data, err = _parse_success_body(huge)
    assert data is None
    assert err == 'Response too large'
