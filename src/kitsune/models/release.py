# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass, field

from kitsune import SITE_URL


@dataclass
class SkipTimecode:
    start: float
    stop: float

    @classmethod
    def from_dict(cls, data: dict | None) -> SkipTimecode | None:
        if not data:
            return None
        start = data.get('start')
        stop = data.get('stop')
        if start is None or stop is None:
            return None
        return cls(start=start, stop=stop)


def _safe_url(path: str | None) -> str | None:
    """Prepend base URL only if path is a valid relative path."""
    if not path or not isinstance(path, str):
        return None
    if not path.startswith('/') or path.startswith('//'):
        return None
    return SITE_URL + path


def _genre_image_url(data: dict | None) -> str | None:
    if not data:
        return None
    optimized = data.get('optimized')
    if optimized and optimized.get('preview'):
        return _safe_url(optimized['preview'])
    preview = data.get('preview')
    if preview:
        return _safe_url(preview)
    return None


@dataclass
class Genre:
    id: int
    name: str
    image: str | None = None
    total_releases: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> Genre:
        return cls(
            id=data.get('id', 0),
            name=data.get('name', ''),
            image=_genre_image_url(data.get('image')),
            total_releases=data.get('total_releases', 0),
        )


@dataclass
class ReleaseName:
    main: str
    english: str | None = None
    alternative: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> ReleaseName:
        return cls(
            main=data.get('main', ''),
            english=data.get('english'),
            alternative=data.get('alternative'),
        )


def _poster_url(data: dict | None) -> str | None:
    if not data:
        return None
    optimized = data.get('optimized')
    if optimized and optimized.get('src'):
        return _safe_url(optimized['src'])
    src = data.get('src')
    if src:
        return _safe_url(src)
    return None


def _poster_preview_url(data: dict | None) -> str | None:
    if not data:
        return None
    optimized = data.get('optimized')
    if optimized and optimized.get('preview'):
        return _safe_url(optimized['preview'])
    preview = data.get('preview')
    if preview:
        return _safe_url(preview)
    return None


@dataclass
class Member:
    id: str
    nickname: str
    role: str
    role_value: str
    avatar: str | None = None

    @classmethod
    def from_dict(cls, data: dict) -> Member:
        role_data = data.get('role', {})
        user_data = data.get('user', {})
        avatar = _genre_image_url(user_data.get('avatar')) if user_data else None
        return cls(
            id=data.get('id', ''),
            nickname=data.get('nickname', ''),
            role=role_data.get('description', ''),
            role_value=role_data.get('value', ''),
            avatar=avatar,
        )


@dataclass
class Torrent:
    id: int
    hash: str
    size: int
    label: str
    codec: str
    codec_value: str
    quality: str
    magnet: str
    seeders: int = 0
    leechers: int = 0
    completed_times: int = 0
    episode_range: str = ''
    is_hardsub: bool = False
    created_at: str = ''
    updated_at: str = ''

    @classmethod
    def from_dict(cls, data: dict) -> Torrent:
        codec_data = data.get('codec', {})
        quality_data = data.get('quality', {})
        return cls(
            id=data.get('id', 0),
            hash=data.get('hash', ''),
            size=data.get('size', 0),
            label=data.get('label', ''),
            codec=codec_data.get('label', '') if isinstance(codec_data, dict) else str(codec_data),
            codec_value=codec_data.get('value', '') if isinstance(codec_data, dict) else '',
            quality=quality_data.get('value', '') if isinstance(quality_data, dict) else str(quality_data),
            magnet=data.get('magnet', ''),
            seeders=data.get('seeders', 0),
            leechers=data.get('leechers', 0),
            completed_times=data.get('completed_times', 0),
            episode_range=data.get('description', ''),
            is_hardsub=data.get('is_hardsub', False),
            created_at=data.get('created_at', ''),
            updated_at=data.get('updated_at', ''),
        )


@dataclass
class Episode:
    id: str
    name: str | None
    ordinal: float
    hls_480: str | None = None
    hls_720: str | None = None
    hls_1080: str | None = None
    duration: int | None = None
    opening: SkipTimecode | None = None
    ending: SkipTimecode | None = None
    preview: str | None = None
    sort_order: int = 0

    @classmethod
    def from_dict(cls, data: dict) -> Episode:
        return cls(
            id=data.get('id', ''),
            name=data.get('name'),
            ordinal=data.get('ordinal', 0),
            hls_480=data.get('hls_480'),
            hls_720=data.get('hls_720'),
            hls_1080=data.get('hls_1080'),
            duration=data.get('duration'),
            opening=SkipTimecode.from_dict(data.get('opening')),
            ending=SkipTimecode.from_dict(data.get('ending')),
            preview=_poster_url(data.get('preview')),
            sort_order=data.get('sort_order', 0),
        )

    def get_hls_url(self, quality: str = '1080') -> str | None:
        urls = {'1080': self.hls_1080, '720': self.hls_720, '480': self.hls_480}
        url = urls.get(quality)
        if url:
            return url
        for q in ('1080', '720', '480'):
            if urls.get(q):
                return urls[q]
        return None


@dataclass
class Release:
    id: int
    name: ReleaseName
    alias: str
    description: str | None = None
    poster: str | None = None
    poster_preview: str | None = None
    type: str = ''
    year: int = 0
    season: str | None = None
    age_rating: str = ''
    is_adult: bool = False
    episodes_total: int | None = None
    is_ongoing: bool = False
    publish_day: str = ''
    genres: list[Genre] = field(default_factory=list)
    episodes: list[Episode] = field(default_factory=list)
    members: list[Member] = field(default_factory=list)
    torrents: list[Torrent] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict) -> Release:
        name_data = data.get('name', {})
        type_data = data.get('type', {})
        season_data = data.get('season')
        age_data = data.get('age_rating', {})
        publish_day_data = data.get('publish_day')

        genres = [Genre.from_dict(g) for g in data.get('genres', [])]
        episodes = [Episode.from_dict(e) for e in data.get('episodes', [])]
        episodes.sort(key=lambda e: e.sort_order)
        members = [Member.from_dict(m) for m in data.get('members', [])]
        torrents = [Torrent.from_dict(t) for t in data.get('torrents', [])]

        return cls(
            id=data.get('id', 0),
            name=ReleaseName.from_dict(name_data) if isinstance(name_data, dict) else ReleaseName(main=str(name_data)),
            alias=data.get('alias', ''),
            description=data.get('description'),
            poster=_poster_url(data.get('poster')),
            poster_preview=_poster_preview_url(data.get('poster')),
            type=type_data.get('value', '') if isinstance(type_data, dict) else str(type_data),
            year=data.get('year', 0),
            season=season_data.get('value') if isinstance(season_data, dict) else season_data,
            age_rating=age_data.get('value', '') if isinstance(age_data, dict) else str(age_data),
            # Prefer the explicit `is_adult` flag when present; fall back
            # to deriving it from the `value` field for cache entries
            # written before we started capturing `is_adult` directly.
            is_adult=(
                bool(age_data.get('is_adult'))
                or age_data.get('value') == 'R18_PLUS'
                if isinstance(age_data, dict) else False
            ),
            episodes_total=data.get('episodes_total'),
            is_ongoing=data.get('is_ongoing', False),
            publish_day=(
                publish_day_data.get('description', '')
                if isinstance(publish_day_data, dict) else ''
            ),
            genres=genres,
            episodes=episodes,
            members=members,
            torrents=torrents,
        )
