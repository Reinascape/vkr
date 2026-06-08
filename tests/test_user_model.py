# SPDX-License-Identifier: GPL-3.0-or-later

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.models.user import User


def test_from_dict_full():
    data = {
        'id': 42,
        'login': 'testuser',
        'email': 'test@example.com',
        'nickname': 'TestNick',
        'avatar': {'preview': '/storage/avatars/42.jpg'},
        'is_banned': False,
        'created_at': '2024-01-15T10:30:00Z',
    }
    user = User.from_dict(data)
    assert user.id == 42
    assert user.login == 'testuser'
    assert user.email == 'test@example.com'
    assert user.nickname == 'TestNick'
    assert user.avatar == 'https://anilibria.top/storage/avatars/42.jpg'
    assert user.is_banned is False
    assert user.created_at == '2024-01-15T10:30:00Z'


def test_from_dict_minimal():
    data = {'id': 1, 'nickname': 'Nick'}
    user = User.from_dict(data)
    assert user.id == 1
    assert user.login == ''
    assert user.email == ''
    assert user.nickname == 'Nick'
    assert user.avatar is None
    assert user.is_banned is False


def test_from_dict_none():
    user = User.from_dict(None)
    assert user is None


def test_from_dict_empty():
    user = User.from_dict({})
    assert user.id == 0
    assert user.nickname == ''
