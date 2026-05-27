# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Gio, Gtk

from kitsune.api import AniLibriaClient
from kitsune.models import Franchise
from kitsune.ui.widgets.content_grid import ContentGrid
from kitsune.ui.widgets.release_card import ReleaseCard


class FranchiseReleasesView(Gtk.Box):

    def __init__(self, franchise: Franchise, client: AniLibriaClient, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._franchise = franchise
        self._client = client
        self._on_release_activated = None
        self._cancellable = None

        self._grid = ContentGrid()
        self._grid.set_on_child_activated(self._on_child_activated)
        self.append(self._grid)

        if franchise.releases:
            self._populate(franchise.releases)
        else:
            self._load_franchise()

    def set_narrow(self, narrow: bool):
        self._grid.set_narrow(narrow)

    def set_on_release_activated(self, callback):
        self._on_release_activated = callback

    def _load_franchise(self):
        self._grid.set_spinner_visible(True)
        if self._cancellable:
            self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()
        self._client.get_franchise(
            self._franchise.id,
            callback=self._on_franchise_loaded,
            cancellable=self._cancellable,
        )

    def retry(self):
        self._grid.clear_error()
        self._load_franchise()

    def _on_franchise_loaded(self, franchise, error):
        if error or not franchise:
            self._grid.show_error()
            return
        self._franchise = franchise
        self._populate(franchise.releases)

    def _populate(self, releases):
        for release in releases:
            self._grid.append_child(ReleaseCard(release))
        self._grid.show_end()

    def _on_child_activated(self, child):
        if self._on_release_activated and isinstance(child, ReleaseCard):
            self._on_release_activated(child.release)
