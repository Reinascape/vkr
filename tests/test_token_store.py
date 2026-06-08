# tests/test_token_store.py
# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from unittest.mock import patch, MagicMock
from kitsune.auth import token_store


def test_save_and_load_token():
    with (
        patch('keyring.set_password') as mock_set,
        patch('keyring.get_password', return_value='test-token-123') as mock_get,
    ):
        token_store.save_token('test-token-123')
        mock_set.assert_called_once_with(
            token_store.SERVICE_NAME, token_store.ACCOUNT_NAME, 'test-token-123'
        )

        token = token_store.load_token()
        assert token == 'test-token-123'
        mock_get.assert_called_once_with(
            token_store.SERVICE_NAME, token_store.ACCOUNT_NAME
        )


def test_load_token_none():
    with patch('keyring.get_password', return_value=None) as mock_get:
        assert token_store.load_token() is None


def test_delete_token():
    with patch('keyring.delete_password') as mock_delete:
        token_store.delete_token()
        mock_delete.assert_called_once_with(
            token_store.SERVICE_NAME, token_store.ACCOUNT_NAME
        )


def test_delete_token_already_deleted():
    """delete_token must not raise even if entry doesn't exist."""
    from keyring.errors import PasswordDeleteError

    with patch('keyring.delete_password', side_effect=PasswordDeleteError('gone')):
        token_store.delete_token()  # must not raise


def test_service_name():
    assert token_store.SERVICE_NAME == 'net.armatik.Kitsune'
    assert token_store.ACCOUNT_NAME == 'session'
