# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json

TAB_REGISTRY = [
    {'id': 'catalog', 'label': 'Catalog', 'icon': 'net.armatik.Kitsune.view-grid-symbolic'},
    {'id': 'genres', 'label': 'Genres', 'icon': 'net.armatik.Kitsune.genres-symbolic'},
    {'id': 'franchises', 'label': 'Franchises', 'icon': 'net.armatik.Kitsune.franchises-symbolic'},
    {'id': 'tags', 'label': 'Favorites and Tags', 'icon': 'net.armatik.Kitsune.starred-symbolic'},
]

ALL_TAB_IDS = tuple(t['id'] for t in TAB_REGISTRY)

_TAB_BY_ID = {t['id']: t for t in TAB_REGISTRY}


def get_tab(tab_id: str) -> dict | None:
    """Return tab dict by id, or None if unknown."""
    return _TAB_BY_ID.get(tab_id)


def parse_tab_order(raw: str) -> list[str]:
    """Parse JSON string into validated list of tab IDs."""
    try:
        ids = json.loads(raw)
        if not isinstance(ids, list):
            return list(ALL_TAB_IDS)
    except (json.JSONDecodeError, TypeError):
        return list(ALL_TAB_IDS)

    seen = set()
    result = []
    for tab_id in ids:
        if isinstance(tab_id, str) and tab_id in _TAB_BY_ID and tab_id not in seen:
            seen.add(tab_id)
            result.append(tab_id)

    if not result:
        return [ALL_TAB_IDS[0]]
    return result


def ensure_complete(tab_ids: list[str]) -> list[str]:
    """Return tab_ids with any missing tabs appended at the end."""
    present = set(tab_ids)
    result = list(tab_ids)
    for tab_id in ALL_TAB_IDS:
        if tab_id not in present:
            result.append(tab_id)
    return result


def get_visible_tabs(settings, is_narrow: bool) -> list[str]:
    """Return ordered list of visible tab IDs based on settings and layout."""
    if is_narrow and settings.get_boolean('navbar-sync'):
        raw = settings.get_string('navbar-desktop')
    elif is_narrow:
        raw = settings.get_string('navbar-mobile')
    else:
        raw = settings.get_string('navbar-desktop')
    return parse_tab_order(raw)


def serialize_tab_order(tab_ids: list[str]) -> str:
    """Serialize tab ID list to JSON string for GSettings."""
    return json.dumps(tab_ids)
