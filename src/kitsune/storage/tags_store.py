# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import copy
import json
import os
import secrets
from pathlib import Path

from kitsune.storage import _atomic_write_json

_TAGS_FILE = Path(
    os.environ.get('XDG_DATA_HOME', os.path.expanduser('~/.local/share'))
) / 'kitsune' / 'tags.json'

# Anchor for xgettext: the gettext extractor only finds strings inside
# `_()` calls, but built-in tag names live as dict-literal values in
# `_BUILTIN_TAGS` below. This unused constant forces the names into
# the .pot catalogue so `display_name()` can look them up at runtime.
_TRANSLATABLE_TAG_NAMES = [
    _('Favorites'), _('Watching'), _('Watched'),
    _('Planned'), _('Postponed'), _('Abandoned'),
]

_BUILTIN_TAGS = [
    {
        'id': 'favorites',
        'name': 'Favorites',
        'icon_type': 'symbolic',
        'icon_value': 'net.armatik.Kitsune.starred-symbolic',
        'builtin': True,
        'order': 0,
        'releases': [],
        'color': '#e5a50a',
    },
    {
        'id': 'watching',
        'name': 'Watching',
        'icon_type': 'symbolic',
        'icon_value': 'net.armatik.Kitsune.media-playback-start-symbolic',
        'builtin': True,
        'order': 1,
        'releases': [],
        'color': '#dc8add',
    },
    {
        'id': 'watched',
        'name': 'Watched',
        'icon_type': 'symbolic',
        'icon_value': 'net.armatik.Kitsune.object-select-symbolic',
        'builtin': True,
        'order': 2,
        'releases': [],
        'color': '#26a269',
    },
    {
        'id': 'planned',
        'name': 'Planned',
        'icon_type': 'symbolic',
        'icon_value': 'net.armatik.Kitsune.view-list-bullet-symbolic',
        'builtin': True,
        'order': 3,
        'releases': [],
        'color': '#1c71d8',
    },
    {
        'id': 'postponed',
        'name': 'Postponed',
        'icon_type': 'symbolic',
        'icon_value': 'net.armatik.Kitsune.media-playback-pause-symbolic',
        'builtin': True,
        'order': 4,
        'releases': [],
        'color': '#c64600',
    },
    {
        'id': 'abandoned',
        'name': 'Abandoned',
        'icon_type': 'symbolic',
        'icon_value': 'net.armatik.Kitsune.cross-large-symbolic',
        'builtin': True,
        'order': 5,
        'releases': [],
        'color': '#c01c28',
    },
]

# Map collection API types to local tag IDs
COLLECTION_TYPE_MAP = {
    'WATCHING': 'watching',
    'WATCHED': 'watched',
    'PLANNED': 'planned',
    'POSTPONED': 'postponed',
    'ABANDONED': 'abandoned',
}

TAG_COLORS = (
    'blue', 'teal', 'green', 'yellow',
    'orange', 'red', 'pink', 'purple', 'slate',
)


def _load() -> dict:
    file_existed = _TAGS_FILE.exists()
    if file_existed:
        try:
            with open(_TAGS_FILE) as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError):
            data = {'tags': []}
    else:
        data = {'tags': []}

    migrated = False

    existing_ids = {t['id'] for t in data['tags']}
    for bt in _BUILTIN_TAGS:
        if bt['id'] not in existing_ids:
            data['tags'].insert(bt['order'], copy.deepcopy(bt))
            migrated = True

    # Migrate built-in tags from the legacy emoji icon set to Adwaita
    # symbolic icons, and refresh stored icon_value when the bundled
    # name changes. User-created tags keep their saved icon.
    builtin_by_id = {bt['id']: bt for bt in _BUILTIN_TAGS}
    for tag in data['tags']:
        if not tag.get('builtin'):
            continue
        latest = builtin_by_id.get(tag['id'])
        if not latest:
            continue
        if tag.get('icon_type') == 'emoji':
            tag['icon_type'] = latest['icon_type']
            tag['icon_value'] = latest['icon_value']
            migrated = True
        elif (tag.get('icon_type') == latest['icon_type']
                and tag.get('icon_value') != latest['icon_value']):
            tag['icon_value'] = latest['icon_value']
            migrated = True
        if latest.get('color') and tag.get('color') != latest['color']:
            tag['color'] = latest['color']
            migrated = True

    # Persist the migrated shape so subsequent loads are no-ops. Without
    # this, every _load on an old store re-runs the migration in memory.
    if migrated and file_existed:
        _save(data)

    return data


def _save(data: dict):
    _atomic_write_json(_TAGS_FILE, data, ensure_ascii=False)


def _find_tag(data: dict, tag_id: str) -> dict | None:
    for tag in data['tags']:
        if tag['id'] == tag_id:
            return tag
    return None


def get_all_tags() -> list[dict]:
    return _load()['tags']


def display_name(tag: dict) -> str:
    """Localised display label for a tag.

    Built-in tags carry stable English source names in storage so the
    gettext catalogue can translate them at render time. User-created
    tags are passed through verbatim — they're arbitrary user input
    and must not be translated.
    """
    name = tag.get('name', '')
    if tag.get('builtin'):
        # Importing builtins._ at module load would create a cycle on
        # tests that bind _ later; deferring keeps the lookup tied to
        # the current locale at the call site.
        import builtins
        gettext_fn = getattr(builtins, '_', None)
        if callable(gettext_fn):
            return gettext_fn(name)
    return name


def create_tag(name: str, icon_type: str, icon_value: str) -> dict:
    data = _load()
    tag = {
        'id': secrets.token_hex(4),
        'name': name,
        'icon_type': icon_type,
        'icon_value': icon_value,
        'builtin': False,
        'order': len(data['tags']),
        'releases': [],
    }
    data['tags'].append(tag)
    _save(data)
    return tag


def delete_tag(tag_id: str):
    data = _load()
    tag = _find_tag(data, tag_id)
    if not tag or tag.get('builtin'):
        return
    data['tags'] = [t for t in data['tags'] if t['id'] != tag_id]
    _save(data)


def add_release(tag_id: str, release_id: int):
    data = _load()
    tag = _find_tag(data, tag_id)
    if tag and release_id not in tag['releases']:
        tag['releases'].append(release_id)
        _save(data)


def remove_release(tag_id: str, release_id: int):
    data = _load()
    tag = _find_tag(data, tag_id)
    if tag and release_id in tag['releases']:
        tag['releases'].remove(release_id)
        _save(data)


def get_tags_for_release(release_id: int) -> list[dict]:
    data = _load()
    return [t for t in data['tags'] if release_id in t['releases']]


def get_release_ids_for_tag(tag_id: str) -> list[int]:
    data = _load()
    tag = _find_tag(data, tag_id)
    return list(tag['releases']) if tag else []


def is_favorited(release_id: int) -> bool:
    data = _load()
    fav = _find_tag(data, 'favorites')
    return fav is not None and release_id in fav['releases']


def toggle_favorite(release_id: int) -> bool:
    """Toggle favorite status. Returns new state."""
    data = _load()
    fav = _find_tag(data, 'favorites')
    if not fav:
        return False
    if release_id in fav['releases']:
        fav['releases'].remove(release_id)
        _save(data)
        return False
    fav['releases'].append(release_id)
    _save(data)
    return True


def get_count() -> int:
    return len(_load()['tags'])


def get_size() -> int:
    try:
        return _TAGS_FILE.stat().st_size
    except FileNotFoundError:
        return 0


def clear_all():
    _save({'tags': copy.deepcopy(_BUILTIN_TAGS)})
