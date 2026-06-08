# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.models import Episode
from kitsune.ui.release_view_episodes import (
    episode_subtitle,
    episode_title,
    get_filtered_episodes,
)


def _ep(ordinal, duration=1440, name=None, hls_1080=None):
    return Episode(
        id=str(ordinal), name=name, ordinal=ordinal,
        duration=duration, hls_1080=hls_1080,
    )


# --- episode_title ---

def test_episode_title_integer_ordinal():
    assert episode_title(_ep(3)) == 'Episode 3'


def test_episode_title_float_ordinal():
    assert episode_title(_ep(3.5)) == 'Episode 3.5'


def test_episode_title_with_name():
    ep = _ep(1, name='Pilot')
    assert episode_title(ep) == '1. Pilot'


# --- episode_subtitle: near-end detection ---

def test_subtitle_completed():
    """pos=-1 → 'Watched (24 min)'."""
    ep = _ep(1, duration=1440)
    result = episode_subtitle(ep, {1: -1})
    assert 'Watched' in result or 'min' in result


def test_subtitle_90_percent_treated_as_completed():
    """pos=1300, duration=1440 → 90.3%, should show Watched."""
    ep = _ep(1, duration=1440)
    result = episode_subtitle(ep, {1: 1300})
    assert 'Watched' in result


def test_subtitle_exact_90_percent():
    """pos=1296, duration=1440 → exactly 90%, treated as completed."""
    ep = _ep(1, duration=1440)
    result = episode_subtitle(ep, {1: 1296})
    assert 'Watched' in result


def test_subtitle_partial_not_near_end():
    """pos=600, duration=1440 → 840s remaining, should show Remaining."""
    ep = _ep(1, duration=1440)
    result = episode_subtitle(ep, {1: 600})
    assert 'Remaining' in result or 'min' in result
    assert 'Watched' not in result


def test_subtitle_unwatched():
    """pos=0, duration=1440 → should show just duration."""
    ep = _ep(1, duration=1440)
    result = episode_subtitle(ep, {})
    assert 'Watched' not in result
    assert 'Remaining' not in result


def test_subtitle_no_duration_completed():
    """Completed but no duration → 'Watched' without minutes."""
    ep = _ep(1, duration=None)
    result = episode_subtitle(ep, {1: -1})
    assert 'Watched' in result
    assert 'min' not in result


def test_subtitle_with_quality():
    """Quality info appended after watch status."""
    ep = _ep(1, duration=1440, hls_1080='http://x')
    result = episode_subtitle(ep, {})
    assert '1080p' in result


# --- get_filtered_episodes ---

def _make_episodes():
    return [_ep(i, duration=1440) for i in range(1, 6)]


def test_filter_watched_includes_completed():
    episodes = _make_episodes()
    watch_data = {1: -1, 2: 600, 3: 0}
    result = get_filtered_episodes(episodes, 'watched', '', False, watch_data)
    ordinals = [ep.ordinal for ep in result]
    assert 1 in ordinals  # completed
    assert 2 in ordinals  # partial


def test_filter_watched_includes_90_percent():
    """90%+ episodes (pos>0) are included in 'watched' filter."""
    episodes = _make_episodes()
    watch_data = {1: 1300}  # >90%
    result = get_filtered_episodes(episodes, 'watched', '', False, watch_data)
    ordinals = [ep.ordinal for ep in result]
    assert 1 in ordinals


def test_filter_unwatched_excludes_90_percent():
    """90%+ episodes should not appear in 'unwatched'."""
    episodes = _make_episodes()
    watch_data = {1: 1300}  # >90%
    result = get_filtered_episodes(episodes, 'unwatched', '', False, watch_data)
    ordinals = [ep.ordinal for ep in result]
    assert 1 not in ordinals


def test_filter_unwatched_only_zero():
    episodes = _make_episodes()
    watch_data = {1: -1, 2: 600, 3: 1300}
    result = get_filtered_episodes(episodes, 'unwatched', '', False, watch_data)
    ordinals = [ep.ordinal for ep in result]
    assert 4 in ordinals
    assert 5 in ordinals
    assert len(ordinals) == 2


def test_filter_search_by_ordinal():
    episodes = _make_episodes()
    result = get_filtered_episodes(episodes, 'all', '3', False, {})
    assert len(result) == 1
    assert result[0].ordinal == 3


def test_filter_sort_newest_first():
    episodes = _make_episodes()
    result = get_filtered_episodes(episodes, 'all', '', True, {})
    assert result[0].ordinal == 5
    assert result[-1].ordinal == 1


def test_filter_all_no_filter():
    episodes = _make_episodes()
    result = get_filtered_episodes(episodes, 'all', '', False, {})
    assert len(result) == 5


def test_filter_search_case_insensitive():
    episodes = [_ep(1, name='The Beginning'), _ep(2, name='second')]
    result = get_filtered_episodes(episodes, 'all', 'BEGINNING', False, {})
    assert len(result) == 1
    assert result[0].ordinal == 1
