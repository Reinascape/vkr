# SPDX-License-Identifier: GPL-3.0-or-later

import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.models.collection import CollectionEntry, Timecode


def test_collection_entry_from_dict():
    data = {'release_id': 42, 'type_of_collection': 'WATCHING'}
    entry = CollectionEntry.from_dict(data)
    assert entry.release_id == 42
    assert entry.type_of_collection == 'WATCHING'


def test_collection_entry_from_dict_minimal():
    entry = CollectionEntry.from_dict({})
    assert entry.release_id == 0
    assert entry.type_of_collection == ''


def test_collection_entry_from_dict_none():
    assert CollectionEntry.from_dict(None) is None


def test_timecode_from_dict():
    data = {
        'episode_id': 'abc-123',
        'time': 123.5,
        'is_watched': True,
    }
    tc = Timecode.from_dict(data)
    assert tc.episode_id == 'abc-123'
    assert tc.time == 123.5
    assert tc.is_watched is True


def test_timecode_from_dict_minimal():
    tc = Timecode.from_dict({})
    assert tc.episode_id == ''
    assert tc.time == 0.0
    assert tc.is_watched is False


def test_timecode_from_dict_none():
    assert Timecode.from_dict(None) is None
