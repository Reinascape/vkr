# SPDX-License-Identifier: GPL-3.0-or-later

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.models.release import (
    Episode, Genre, Release, ReleaseName, SkipTimecode,
)
from kitsune.models.catalog import CatalogResponse, PaginationMeta


def test_skip_timecode_from_dict():
    tc = SkipTimecode.from_dict({'start': 5.0, 'stop': 90.0})
    assert tc.start == 5.0
    assert tc.stop == 90.0


def test_skip_timecode_from_none():
    assert SkipTimecode.from_dict(None) is None


def test_genre_from_dict():
    g = Genre.from_dict({'id': 1, 'name': 'Action'})
    assert g.id == 1
    assert g.name == 'Action'


def test_release_name_from_dict():
    n = ReleaseName.from_dict({'main': 'Test', 'english': 'Test EN', 'alternative': None})
    assert n.main == 'Test'
    assert n.english == 'Test EN'
    assert n.alternative is None


def test_episode_from_dict():
    data = {
        'id': 'ep-1',
        'name': 'Episode 1',
        'ordinal': 1.0,
        'hls_480': 'http://a/480.m3u8',
        'hls_720': 'http://a/720.m3u8',
        'hls_1080': 'http://a/1080.m3u8',
        'duration': 1440,
        'opening': {'start': 0, 'stop': 90},
        'ending': {'start': 1350, 'stop': 1440},
        'preview': None,
        'sort_order': 1,
    }
    ep = Episode.from_dict(data)
    assert ep.id == 'ep-1'
    assert ep.ordinal == 1.0
    assert ep.hls_1080 == 'http://a/1080.m3u8'
    assert ep.opening.start == 0
    assert ep.opening.stop == 90


def test_episode_get_hls_url_preferred():
    ep = Episode(id='1', name=None, ordinal=1, hls_720='http://720', hls_480='http://480')
    assert ep.get_hls_url('720') == 'http://720'
    assert ep.get_hls_url('1080') == 'http://720'  # fallback to best available
    assert ep.get_hls_url('480') == 'http://480'


def test_release_from_dict():
    data = {
        'id': 42,
        'name': {'main': 'Naruto', 'english': 'Naruto', 'alternative': None},
        'alias': 'naruto',
        'description': 'A ninja story',
        'poster': {'src': '/storage/poster.jpg', 'optimized': {'src': '/storage/poster.avif'}},
        'type': {'value': 'TV', 'description': 'TV Series'},
        'year': 2002,
        'season': {'value': 'autumn', 'description': 'Autumn'},
        'age_rating': {'value': 'R12_PLUS', 'label': '12+', 'is_adult': False, 'description': ''},
        'episodes_total': 220,
        'is_ongoing': False,
        'genres': [{'id': 1, 'name': 'Action'}, {'id': 2, 'name': 'Adventure'}],
        'episodes': [],
    }
    r = Release.from_dict(data)
    assert r.id == 42
    assert r.name.main == 'Naruto'
    assert r.alias == 'naruto'
    assert r.type == 'TV'
    assert r.year == 2002
    assert r.season == 'autumn'
    assert r.poster == 'https://anilibria.top/storage/poster.avif'
    assert len(r.genres) == 2


def test_catalog_response_from_dict():
    data = {
        'data': [
            {
                'id': 1,
                'name': {'main': 'Test'},
                'alias': 'test',
                'type': {'value': 'TV'},
                'year': 2024,
                'age_rating': {'value': 'R0_PLUS'},
            },
        ],
        'meta': {
            'pagination': {
                'current_page': 1,
                'last_page': 5,
                'total': 100,
            },
        },
    }
    resp = CatalogResponse.from_dict(data)
    assert len(resp.releases) == 1
    assert resp.releases[0].name.main == 'Test'
    assert resp.meta.current_page == 1
    assert resp.meta.last_page == 5
    assert resp.meta.total == 100
