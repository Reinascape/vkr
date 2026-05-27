# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import GLib, Gtk

from kitsune.api import AniLibriaClient
from kitsune.ui.widgets.content_grid import ContentGrid


class ItemsGridView(Gtk.Box):
    """Base for views showing a grid of items with drill-down to releases."""

    def __init__(self, client: AniLibriaClient, auto_load: bool = True, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._client = client
        self._on_release_activated = None
        self._on_navigation_changed = None
        self._releases_view = None
        self._current_item = None
        self._narrow = False
        self._loaded = False
        self._batch_idle = 0
        self._pending_items = []

        self._stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
        )

        self._grid = ContentGrid()
        self._grid.set_on_child_activated(self._on_child_activated)
        self._stack.add_named(self._grid, 'grid')

        self._releases_placeholder = Gtk.Box()
        self._stack.add_named(self._releases_placeholder, 'releases')

        self.append(self._stack)
        self.connect('map', self._on_map)
        if auto_load:
            self.load()

    @property
    def in_releases(self) -> bool:
        return self._stack.get_visible_child_name() == 'releases'

    @property
    def current_item_name(self) -> str:
        return self._current_item.name if self._current_item else ''

    def set_narrow(self, narrow: bool):
        self._narrow = narrow
        self._grid.set_narrow(narrow)
        if self._releases_view:
            self._releases_view.set_narrow(narrow)

    def set_on_release_activated(self, callback):
        self._on_release_activated = callback

    def set_on_navigation_changed(self, callback):
        self._on_navigation_changed = callback

    def go_back(self):
        self._stack.set_visible_child_name('grid')
        self._current_item = None
        if self._on_navigation_changed:
            self._on_navigation_changed()

    def retry(self):
        if self.in_releases and self._releases_view and hasattr(self._releases_view, 'retry'):
            self._releases_view.retry()
            return
        self._loaded = False
        self._grid.clear_error()
        self.load()

    def load(self):
        if self._loaded:
            return
        self._loaded = True
        self._load_items()

    def _load_items(self):
        raise NotImplementedError

    def _on_items_loaded(self, items, error):
        if error:
            self._grid.show_error()
            return
        if not items:
            self._grid.set_spinner_visible(False)
            return
        self._pending_items = sorted(items, key=lambda i: i.name)
        self._add_pending_batch()

    def _create_card(self, item):
        raise NotImplementedError

    def _get_item_from_card(self, card):
        raise NotImplementedError

    def _show_item_releases(self, item):
        raise NotImplementedError

    def _add_pending_batch(self):
        self._batch_idle = 0
        if not self.get_mapped():
            return GLib.SOURCE_REMOVE
        batch = self._pending_items[:4]
        self._pending_items = self._pending_items[4:]
        for item in batch:
            self._grid.append_child(self._create_card(item))
        if self._pending_items:
            self._batch_idle = GLib.idle_add(self._add_pending_batch)
        else:
            self._grid.show_end()

    def _on_child_activated(self, child):
        item = self._get_item_from_card(child)
        if item is not None:
            self._show_item_releases(item)

    def _on_map(self, _widget):
        if self._pending_items and not self._batch_idle:
            self._batch_idle = GLib.idle_add(self._add_pending_batch)

    def _show_releases(self, item, releases_view):
        """Helper for subclasses: swap in the releases view."""
        self._current_item = item

        old = self._stack.get_child_by_name('releases')
        if old:
            self._stack.remove(old)

        self._releases_view = releases_view
        self._releases_view.set_on_release_activated(self._on_release_activated)
        self._releases_view.set_narrow(self._narrow)
        self._stack.add_named(self._releases_view, 'releases')
        self._stack.set_visible_child_name('releases')

        if self._on_navigation_changed:
            self._on_navigation_changed()

    def do_unmap(self):
        try:
            if self._batch_idle:
                GLib.source_remove(self._batch_idle)
                self._batch_idle = 0
        finally:
            Gtk.Box.do_unmap(self)
