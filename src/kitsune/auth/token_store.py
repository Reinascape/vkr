# src/kitsune/auth/token_store.py
# SPDX-License-Identifier: GPL-3.0-or-later

import keyring
from keyring.errors import PasswordDeleteError

SERVICE_NAME = 'net.armatik.Kitsune'
ACCOUNT_NAME = 'session'


def save_token(token):
    keyring.set_password(SERVICE_NAME, ACCOUNT_NAME, token)


def load_token():
    return keyring.get_password(SERVICE_NAME, ACCOUNT_NAME)


def delete_token():
    try:
        keyring.delete_password(SERVICE_NAME, ACCOUNT_NAME)
    except PasswordDeleteError:
        pass
