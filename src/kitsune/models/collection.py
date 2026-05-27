# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CollectionEntry:
    release_id: int
    type_of_collection: str

    @classmethod
    def from_dict(cls, data: dict | None) -> CollectionEntry | None:
        if data is None:
            return None
        return cls(
            release_id=data.get('release_id', 0),
            type_of_collection=data.get('type_of_collection', ''),
        )


@dataclass
class Timecode:
    episode_id: str
    time: float
    is_watched: bool

    @classmethod
    def from_dict(cls, data: dict | None) -> Timecode | None:
        if data is None:
            return None
        return cls(
            episode_id=data.get('episode_id', ''),
            time=data.get('time', 0.0),
            is_watched=data.get('is_watched', False),
        )
