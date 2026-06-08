# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.navbar import (
    ALL_TAB_IDS,
    TAB_REGISTRY,
    ensure_complete,
    parse_tab_order,
)


def test_tab_registry_has_all_ids():
    ids = [t['id'] for t in TAB_REGISTRY]
    assert ids == list(ALL_TAB_IDS)
    assert len(ids) == len(set(ids))


def test_parse_tab_order_valid():
    raw = '["genres","catalog"]'
    result = parse_tab_order(raw)
    assert result == ['genres', 'catalog']


def test_parse_tab_order_filters_unknown():
    raw = '["catalog","nonexistent","genres"]'
    result = parse_tab_order(raw)
    assert result == ['catalog', 'genres']


def test_parse_tab_order_deduplicates():
    raw = '["catalog","catalog","genres"]'
    result = parse_tab_order(raw)
    assert result == ['catalog', 'genres']


def test_parse_tab_order_invalid_json_returns_all():
    result = parse_tab_order('not json')
    assert result == list(ALL_TAB_IDS)


def test_parse_tab_order_empty_array_returns_first():
    result = parse_tab_order('[]')
    assert result == [ALL_TAB_IDS[0]]


def test_ensure_complete_adds_missing():
    tabs = ['catalog', 'genres']
    result = ensure_complete(tabs)
    assert result[:2] == ['catalog', 'genres']
    assert set(result) == set(ALL_TAB_IDS)


def test_ensure_complete_no_change_when_full():
    tabs = list(ALL_TAB_IDS)
    assert ensure_complete(tabs) == tabs
