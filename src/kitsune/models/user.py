# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass

from kitsune import SITE_URL


def _safe_url(path: str | None) -> str | None:
    if not path or not isinstance(path, str):
        return None
    if path.startswith('/'):
        return SITE_URL + path
    return path


@dataclass
class User:
    id: int
    login: str
    email: str
    nickname: str
    avatar: str | None
    is_banned: bool
    created_at: str

    @classmethod
    def from_dict(cls, data: dict | None) -> User | None:
        if data is None:
            return None
        avatar_data = data.get('avatar')
        avatar_url = None
        if isinstance(avatar_data, dict):
            avatar_url = _safe_url(avatar_data.get('preview'))
        elif isinstance(avatar_data, str):
            avatar_url = _safe_url(avatar_data)
        return cls(
            id=data.get('id', 0),
            login=data.get('login', ''),
            email=data.get('email', ''),
            nickname=data.get('nickname', ''),
            avatar=avatar_url,
            is_banned=data.get('is_banned', False),
            created_at=data.get('created_at', ''),
        )
