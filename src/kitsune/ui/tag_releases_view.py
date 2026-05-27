# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import GLib, Gtk

from kitsune.api import AniLibriaClient
from kitsune.models import Release
from kitsune import release_cache, tags_store
from kitsune.ui.widgets.content_grid import ContentGrid
from kitsune.ui.widgets.release_card import ReleaseCard


class TagReleasesView(Gtk.Box):

    def __init__(self, tag: dict, client: AniLibriaClient, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._tag = tag
        self._client = client
        self._on_release_activated = None
        self._batch_idle = 0
        self._loaded_release_ids: set[int] = set()

        self._grid = ContentGrid()
        self._grid.set_on_child_activated(self._on_child_activated)
        self.append(self._grid)
        self._batch = []

        self.connect('map', self._on_map)
        self._load_releases()

    def set_narrow(self, narrow: bool):
        self._grid.set_narrow(narrow)

    def set_on_release_activated(self, callback):
        self._on_release_activated = callback

    def _load_releases(self):
        release_ids = tags_store.get_release_ids_for_tag(self._tag['id'])
        self._loaded_release_ids = set(release_ids)
        if not release_ids:
            self._grid.show_end()
            return

        self._grid.set_spinner_visible(True)
        self._pending_releases = []
        self._missing_ids = []

        for rid in release_ids:
            cached = release_cache.get(rid)
            if cached:
                self._pending_releases.append(Release.from_dict(cached))
            else:
                self._missing_ids.append(rid)

        if self._missing_ids:
            self._fetch_count = 0
            for rid in self._missing_ids:
                self._client.get_release_raw(
                    str(rid), callback=self._on_release_fetched,
                )
        else:
            self._show_all()

    def _on_release_fetched(self, data, error):
        self._fetch_count += 1
        if data:
            release_cache.save(data.get('id', 0), data)
            self._pending_releases.append(Release.from_dict(data))

        if self._fetch_count >= len(self._missing_ids):
            self._show_all()

    def _show_all(self):
        self._grid.set_spinner_visible(False)
        if not self._pending_releases:
            self._grid.show_end()
            return
        self._batch = list(self._pending_releases)
        self._add_batch()

    def _add_batch(self):
        self._batch_idle = 0
        if not self.get_mapped():
            return GLib.SOURCE_REMOVE
        batch = self._batch[:4]
        self._batch = self._batch[4:]
        for release in batch:
            self._grid.append_child(ReleaseCard(release))
        if self._batch:
            self._batch_idle = GLib.idle_add(self._add_batch)
        else:
            self._grid.show_end()

    def _on_child_activated(self, child):
        if self._on_release_activated and isinstance(child, ReleaseCard):
            self._on_release_activated(child.release)

    def _on_map(self, _widget):
        if self._batch and not self._batch_idle:
            self._batch_idle = GLib.idle_add(self._add_batch)
            return
        # On navigation return, the release's tag membership may have
        # changed in the detail page. Detect mismatch and reload the
        # grid so cards reflect the up-to-date tags_store state.
        fresh = set(tags_store.get_release_ids_for_tag(self._tag['id']))
        if fresh != self._loaded_release_ids:
            self._grid.clear()
            self._load_releases()

    def do_unmap(self):
        try:
            if self._batch_idle:
                GLib.source_remove(self._batch_idle)
                self._batch_idle = 0
        finally:
            Gtk.Box.do_unmap(self)
