# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gio, Gtk

from kitsune.api import AniLibriaClient
from kitsune.models import Genre
from kitsune.ui.widgets.content_grid import ContentGrid
from kitsune.ui.widgets.release_card import ReleaseCard


class GenreReleasesView(Gtk.Box):

    def __init__(self, genre: Genre, client: AniLibriaClient, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._genre = genre
        self._client = client
        self._page = 0
        self._last_page = 1
        self._loading = False
        self._reached_end = False
        self._on_release_activated = None
        self._cancellable = None

        self._grid = ContentGrid()
        self._grid.set_on_scroll_near_end(self._on_scroll_near_end)
        self._grid.set_on_child_activated(self._on_child_activated)
        self.append(self._grid)

        self._load_next_page()

    def set_narrow(self, narrow: bool):
        self._grid.set_narrow(narrow)

    def set_on_release_activated(self, callback):
        self._on_release_activated = callback

    def _on_scroll_near_end(self):
        if not self._loading and not self._reached_end:
            self._load_next_page()

    def _load_next_page(self):
        if self._page >= self._last_page:
            self._show_end()
            return
        self._loading = True
        self._page += 1
        self._grid.set_spinner_visible(True)
        if self._cancellable:
            self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        self._client.get_catalog(
            page=self._page, limit=20,
            filters={'genres': [self._genre.id]},
            callback=self._on_catalog_loaded,
            cancellable=self._cancellable,
        )

    def retry(self):
        self._grid.clear_error()
        self._loading = False
        self._reached_end = False
        self._load_next_page()

    def _on_catalog_loaded(self, catalog_response, error):
        self._loading = False
        if error or not catalog_response:
            self._page = max(0, self._page - 1)
            self._grid.show_error()
            return
        self._last_page = catalog_response.meta.last_page
        for release in catalog_response.releases:
            self._grid.append_child(ReleaseCard(release))
        if self._page >= self._last_page:
            self._show_end()

    def _show_end(self):
        self._reached_end = True
        self._grid.show_end()

    def _on_child_activated(self, child):
        if self._on_release_activated and isinstance(child, ReleaseCard):
            self._on_release_activated(child.release)
