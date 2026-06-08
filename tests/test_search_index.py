# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.storage import search_index


def test_load_empty(mock_index):
    data = search_index.load()
    assert data['version'] == 1
    assert data['releases'] == {}
    assert data['genres'] == {}
    assert data['franchises'] == {}


def test_load_corrupt_file(mock_index):
    mock_index.write_text('not json')
    data = search_index.load()
    assert data['version'] == 1
    assert data['releases'] == {}


def test_load_wrong_version(mock_index):
    mock_index.write_text(json.dumps({'version': 999}))
    data = search_index.load()
    assert data['version'] == 1
    assert data['releases'] == {}


_SAMPLE_RAW = {
    'id': 42,
    'name': {'main': 'Наруто', 'english': 'Naruto', 'alternative': 'ナルト'},
    'description': 'Молодой ниндзя мечтает стать хокаге.',
    'poster': {
        'optimized': {'preview': '/storage/poster/preview.jpg'},
        'preview': '/storage/poster/fallback.jpg',
    },
    'type': {'value': 'TV', 'description': 'TV'},
    'year': 2002,
    'is_ongoing': False,
    'genres': [{'id': 1, 'name': 'Сёнен'}, {'id': 5, 'name': 'Экшен'}],
}


def test_index_release(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    meta = search_index.get_release_meta(42)
    assert meta is not None
    assert meta['main'] == 'Наруто'
    assert meta['english'] == 'Naruto'
    assert meta['alternative'] == 'ナルト'
    assert meta['description'] == 'Молодой ниндзя мечтает стать хокаге.'
    assert meta['type'] == 'TV'
    assert meta['year'] == 2002
    assert meta['is_ongoing'] is False
    assert meta['genres'] == [1, 5]
    assert 'cached_at' in meta


def test_index_release_poster_preview(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    meta = search_index.get_release_meta(42)
    assert meta['poster_preview'] is not None
    assert 'preview' in meta['poster_preview']


def test_index_release_persists_to_disk(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    assert mock_index.exists()
    data = json.loads(mock_index.read_text())
    assert '42' in data['releases']


def test_get_release_meta_missing(mock_index):
    assert search_index.get_release_meta(999) is None


def test_remove_release(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    search_index.remove_release(42)
    assert search_index.get_release_meta(42) is None


def test_index_release_overwrites(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    updated = {**_SAMPLE_RAW, 'year': 2023}
    search_index.index_release(42, updated)
    meta = search_index.get_release_meta(42)
    assert meta['year'] == 2023


# --- Task 3: Genre and franchise caching ---

from kitsune.models.release import Genre
from kitsune.models.franchise import Franchise


def test_update_genres(mock_index):
    genres = [Genre(id=1, name='Сёнен', image='https://img/1', total_releases=50)]
    search_index.update_genres(genres)
    cached = search_index.get_genres()
    assert cached is not None
    assert len(cached) == 1
    assert cached[0]['id'] == 1
    assert cached[0]['name'] == 'Сёнен'


def test_get_genres_expired(mock_index):
    genres = [Genre(id=1, name='Сёнен', image=None, total_releases=50)]
    search_index.update_genres(genres)
    idx = search_index.load()
    idx['genres']['fetched_at'] = 0
    assert search_index.get_genres() is None


def test_update_franchises(mock_index):
    franchises = [Franchise(id='naruto', name='Наруто', name_english='Naruto',
                            image='https://img/f', first_year=2002, last_year=2017,
                            total_releases=6)]
    search_index.update_franchises(franchises)
    cached = search_index.get_franchises()
    assert cached is not None
    assert len(cached) == 1
    assert cached[0]['name'] == 'Наруто'
    assert cached[0]['name_english'] == 'Naruto'


def test_get_franchises_expired(mock_index):
    franchises = [Franchise(id='x', name='X')]
    search_index.update_franchises(franchises)
    idx = search_index.load()
    idx['franchises']['fetched_at'] = 0
    assert search_index.get_franchises() is None


def test_genres_persists_to_disk(mock_index):
    genres = [Genre(id=1, name='Тест', image=None, total_releases=10)]
    search_index.update_genres(genres)
    data = json.loads(mock_index.read_text())
    assert len(data['genres']['items']) == 1


# --- Task 4: Search methods ---

_SAMPLE_RAW_2 = {
    'id': 100,
    'name': {'main': 'Ван Пис', 'english': 'One Piece', 'alternative': 'ワンピース'},
    'description': 'Пираты ищут сокровище.',
    'poster': None,
    'type': {'value': 'TV'},
    'year': 1999,
    'is_ongoing': True,
    'genres': [{'id': 1, 'name': 'Сёнен'}],
}


def test_search_releases_by_main(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    search_index.index_release(100, _SAMPLE_RAW_2)
    results = search_index.search_releases('наруто')
    assert len(results) == 1
    assert results[0]['main'] == 'Наруто'


def test_search_releases_by_english(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    results = search_index.search_releases('naruto')
    assert len(results) == 1


def test_search_releases_by_description(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    search_index.index_release(100, _SAMPLE_RAW_2)
    results = search_index.search_releases('пираты')
    assert len(results) == 1
    assert results[0]['main'] == 'Ван Пис'


def test_search_releases_case_insensitive(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    results = search_index.search_releases('НАРУТО')
    assert len(results) == 1


def test_search_releases_empty_query(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    results = search_index.search_releases('')
    assert results == []


def test_index_release_stores_episodes(mock_index):
    raw = {**_SAMPLE_RAW, 'episodes': [
        {'ordinal': 1, 'duration': 1440, 'sort_order': 1},
        {'ordinal': 2, 'duration': 1380, 'sort_order': 2},
    ]}
    search_index.index_release(42, raw)
    eps = search_index.get_episodes(42)
    assert eps is not None
    assert len(eps) == 2
    assert eps[0]['ordinal'] == 1
    assert eps[0]['duration'] == 1440
    assert eps[1]['ordinal'] == 2


def test_get_episodes_missing(mock_index):
    assert search_index.get_episodes(999) is None


def test_get_episodes_no_episodes_field(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    eps = search_index.get_episodes(42)
    assert eps == []


def test_index_release_null_episodes(mock_index):
    """API returning 'episodes': null should not crash."""
    raw = {**_SAMPLE_RAW, 'episodes': None}
    search_index.index_release(42, raw)
    eps = search_index.get_episodes(42)
    assert eps == []


def test_index_release_null_genres(mock_index):
    """API returning 'genres': null should not crash."""
    raw = {**_SAMPLE_RAW, 'genres': None}
    search_index.index_release(42, raw)
    meta = search_index.get_release_meta(42)
    assert meta['genres'] == []


def test_index_release_episodes_total(mock_index):
    raw = {**_SAMPLE_RAW, 'episodes_total': 220}
    search_index.index_release(42, raw)
    meta = search_index.get_release_meta(42)
    assert meta['episodes_total'] == 220


def test_index_release_episodes_total_none(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    meta = search_index.get_release_meta(42)
    assert meta['episodes_total'] is None


def test_search_genres(mock_index):
    genres = [
        Genre(id=1, name='Сёнен', image=None, total_releases=50),
        Genre(id=2, name='Комедия', image=None, total_releases=30),
    ]
    search_index.update_genres(genres)
    results = search_index.search_genres('комед')
    assert len(results) == 1
    assert results[0]['name'] == 'Комедия'


def test_search_franchises(mock_index):
    franchises = [
        Franchise(id='naruto', name='Наруто', name_english='Naruto'),
        Franchise(id='op', name='Ван Пис', name_english='One Piece'),
    ]
    search_index.update_franchises(franchises)
    results = search_index.search_franchises('piece')
    assert len(results) == 1
    assert results[0]['name'] == 'Ван Пис'


# --- Task 5: Stats and cleanup ---

def test_get_count(mock_index):
    assert search_index.get_count() == 0
    search_index.index_release(42, _SAMPLE_RAW)
    assert search_index.get_count() == 1
    search_index.index_release(100, _SAMPLE_RAW_2)
    assert search_index.get_count() == 2


def test_get_size_zero_when_missing(mock_index):
    assert search_index.get_size() == 0


def test_get_size_nonzero_after_write(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    assert search_index.get_size() > 0


def test_clear_releases(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    genres = [Genre(id=1, name='Тест', image=None, total_releases=10)]
    search_index.update_genres(genres)
    search_index.clear_releases()
    assert search_index.get_count() == 0
    assert search_index.get_genres() is not None


def test_clear_all(mock_index):
    search_index.index_release(42, _SAMPLE_RAW)
    genres = [Genre(id=1, name='Тест', image=None, total_releases=10)]
    search_index.update_genres(genres)
    search_index.clear_all()
    assert search_index.get_count() == 0
    assert search_index.get_genres() is None
