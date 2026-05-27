# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

import time

from gi.repository import Gdk, Gio, GLib, Gtk

from kitsune.api import AniLibriaClient
from kitsune.ui.widgets.content_grid import ContentGrid
from kitsune.ui.widgets.release_card import ReleaseCard

# Card dimensions used to estimate viewport capacity. ReleaseCard's
# .blp pins the picture at 180×250 plus 6×4 margins around the card
# container + ~62px below the poster for title/subtitle labels.
_CARD_WIDTH_PX = 192
_CARD_HEIGHT_PX = 312
# Visible rows + this many extra rows of lookahead get loaded on each
# page. Keeps a row of off-screen cards ready so an infinite-scroll
# trigger near the bottom does not show an empty viewport while the
# network catches up.
_BUFFER_ROWS = 1
_MIN_PAGE_LIMIT = 6
_MAX_PAGE_LIMIT = 36
_DEFAULT_PAGE_LIMIT = 12


class CatalogView(Gtk.Box):

    def __init__(self, client: AniLibriaClient, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._client = client
        self._page = 0
        self._last_page = 1
        self._loading = False
        self._reached_end = False
        self._on_release_activated = None
        self._filters: dict = {}
        self._genres_data: list = []
        self._year_range: tuple[int, int] | None = None
        self._filter_panel = None
        self._batch_idle = 0
        self._cancellable = None
        self._pending_releases = []

        self._grid = ContentGrid()
        self._grid.set_on_scroll_near_end(self._on_scroll_near_end)
        self._grid.set_on_child_activated(self._on_child_activated)
        self.append(self._grid)

        # Pull-to-refresh: fires on kinetic overshoot at the top edge.
        # Source-device tracking distinguishes touchpad/touchscreen
        # (which feel natural with pull-to-refresh) from mouse wheel
        # (where the gesture doesn't map well and would surprise users).
        # Last-known source updates on every scroll event; edge-overshot
        # consults it to decide whether to fire.
        self._narrow = False
        self._last_overshot = 0.0
        self._pull_refresh_active = False
        self._pull_timer_id = 0
        self._last_scroll_source = None
        # Page size is computed lazily once the viewport has an
        # allocation; cached for the session so consecutive page=N
        # requests stay aligned (server pagination is offset =
        # (page-1)*limit, so a mid-session limit change would dupe or
        # skip items).
        self._page_size = 0
        self._grid.scrolled.connect('edge-overshot', self._on_edge_overshot)

        # Resize-driven prefetch: if the user grows the window the
        # already-loaded rows may no longer cover the new viewport,
        # leaving an empty stripe at the bottom until they scroll.
        # `changed` fires whenever the vadjustment's upper/page-size
        # is recomputed (resize, content append) — we re-check the
        # near-end condition and trigger another fetch if needed.
        self._grid.scrolled.get_vadjustment().connect(
            'changed', self._on_viewport_changed)

        scroll_ctrl = Gtk.EventControllerScroll.new(
            Gtk.EventControllerScrollFlags.VERTICAL
            | Gtk.EventControllerScrollFlags.KINETIC,
        )
        scroll_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        scroll_ctrl.connect('scroll', self._on_scroll_event)
        self._grid.scrolled.add_controller(scroll_ctrl)

        self.connect('map', self._on_map)
        self._load_next_page()

    @property
    def flowbox(self):
        return self._grid.flowbox

    def set_narrow(self, narrow: bool):
        self._narrow = narrow
        self._grid.set_narrow(narrow)

    def _on_scroll_event(self, controller, _dx, _dy):
        event = controller.get_current_event()
        if event is None:
            return False
        device = event.get_device()
        if device is not None:
            self._last_scroll_source = device.get_source()
        return False  # don't consume — pass through to ScrolledWindow

    def _on_edge_overshot(self, _scrolled, position):
        if position != Gtk.PositionType.TOP:
            return
        # Touchpad / touchscreen / unknown → allow. Mouse-wheel
        # overshoots are accidental and should not refresh.
        if self._last_scroll_source == Gdk.InputSource.MOUSE:
            return
        if self._loading:
            return
        now = time.monotonic()
        if now - self._last_overshot < 2.0:
            return
        self._last_overshot = now
        self._pull_refresh_active = True
        self._grid.set_pull_refresh_active(True)
        self._set_header_elevated(True)
        # 1-second deliberate hold before firing the network request so
        # the spinner is visible for a clear beat first — without it the
        # whole interaction can finish in <100ms when the cache is warm
        # and the user never sees a refresh actually happened. The
        # source-id is tracked so an unmap (tab switch, window close)
        # can cancel a pending fire.
        if self._pull_timer_id:
            GLib.source_remove(self._pull_timer_id)
        self._pull_timer_id = GLib.timeout_add(1000, self._fire_pull_refresh)

    def _fire_pull_refresh(self):
        self._pull_timer_id = 0
        self.refresh()
        return GLib.SOURCE_REMOVE

    def set_on_release_activated(self, callback):
        self._on_release_activated = callback

    def get_or_create_filter_panel(self):
        if not self._filter_panel:
            from kitsune.ui.filter_dialog import FilterPanel
            if not self._genres_data:
                self._load_genres()
            if not self._year_range:
                self._load_year_range()
            self._filter_panel = FilterPanel(
                genres=self._genres_data, year_range=self._year_range,
            )
            self._filter_panel.set_filters(self._filters)
            self._filter_panel.set_on_apply(self._on_filters_applied)
        return self._filter_panel

    def _load_genres(self):
        from kitsune.storage import search_index
        cached = search_index.get_genres()
        if cached is not None:
            from kitsune.models.release import Genre
            genres = [Genre(id=g['id'], name=g['name'], image=g.get('image'),
                            total_releases=g.get('total_releases', 0))
                      for g in cached]
            self._on_genres_loaded(genres, None)
        else:
            self._client.get_genres(callback=self._on_genres_loaded)

    def _on_genres_loaded(self, genres, error):
        if genres:
            self._genres_data = [{'id': g.id, 'name': g.name} for g in genres]
            if self._filter_panel:
                self._filter_panel.update_genres(self._genres_data)
            from kitsune.storage import search_index
            search_index.update_genres(genres)

    def _load_year_range(self):
        self._client.get_year_range(callback=self._on_year_range_loaded)

    def _on_year_range_loaded(self, year_range, error):
        if year_range:
            self._year_range = year_range
            if self._filter_panel:
                self._filter_panel.update_year_range(self._year_range)

    def _on_filters_applied(self, filters: dict):
        if filters == self._filters:
            return
        self._filters = filters
        self._reset_catalog()
        self._load_next_page()

    def _reset_catalog(self):
        self._page = 0
        self._last_page = 1
        self._loading = False
        self._reached_end = False
        self._grid.clear()

    def _on_scroll_near_end(self):
        if not self._loading and not self._reached_end:
            self._load_next_page()

    def _on_viewport_changed(self, adj):
        """Resize-aware prefetch.

        When the viewport grows (window enlarged, narrow→wide breakpoint
        flip) the existing card stack may not reach the new bottom edge.
        We check the same near-end criterion the scroll handler uses and
        fire a load so the user does not see an empty stripe below.

        Reentrancy: appending a card via `_add_pending_batch` mutates the
        vadjustment's upper, which fires `changed` again. If we don't
        also block on the batch-append phase, a second page request can
        start mid-batch — the earlier `_cancellable.cancel()` only stops
        the prior HTTP call, leaving the in-flight batch to be stomped
        when `_pending_releases` is reassigned in `_on_catalog_loaded`.
        """
        if self._loading or self._reached_end:
            return
        if self._pending_releases or self._batch_idle:
            return
        upper = adj.get_upper()
        page_size = adj.get_page_size()
        if upper - adj.get_value() <= page_size + 200:
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
            page=self._page, limit=self._compute_page_size(),
            filters=self._filters or None,
            callback=self._on_catalog_loaded,
            cancellable=self._cancellable,
        )

    def _compute_page_size(self) -> int:
        """Items per page = (visible rows + buffer) × cards per row.

        Locked in on first computation so the page=N math stays valid
        across the session. Falls back to a sane default before the
        viewport has been allocated (first call during widget
        construction, before any draw cycle).
        """
        if self._page_size:
            return self._page_size
        alloc = self._grid.scrolled.get_allocation()
        if alloc.width <= 0 or alloc.height <= 0:
            return _DEFAULT_PAGE_LIMIT
        cards_per_row = max(1, alloc.width // _CARD_WIDTH_PX)
        visible_rows = max(1, alloc.height // _CARD_HEIGHT_PX)
        limit = (visible_rows + _BUFFER_ROWS) * cards_per_row
        self._page_size = max(_MIN_PAGE_LIMIT, min(_MAX_PAGE_LIMIT, limit))
        return self._page_size

    def retry(self):
        self._grid.clear_error()
        self._loading = False
        self._reached_end = False
        self._load_next_page()

    def refresh(self):
        if self._cancellable:
            self._cancellable.cancel()
        self._pending_releases = []
        if self._batch_idle:
            GLib.source_remove(self._batch_idle)
            self._batch_idle = 0
        self._reset_catalog()
        self._load_next_page()

    def _on_catalog_loaded(self, catalog_response, error):
        self._loading = False

        if error:
            self._page = max(0, self._page - 1)
            self._grid.show_error()
            self._end_pull_refresh()
            return

        self._last_page = catalog_response.meta.last_page
        self._pending_releases = list(catalog_response.releases)
        self._add_pending_batch()

    def _add_pending_batch(self):
        self._batch_idle = 0
        if not self.get_mapped():
            return GLib.SOURCE_REMOVE
        batch = self._pending_releases[:4]
        self._pending_releases = self._pending_releases[4:]
        for release in batch:
            self._grid.append_child(ReleaseCard(release))

        if self._pending_releases:
            self._batch_idle = GLib.idle_add(self._add_pending_batch)
        else:
            self._grid.set_spinner_visible(False)
            self._end_pull_refresh()
            if self._page >= self._last_page:
                self._show_end()

    def _show_end(self):
        self._reached_end = True
        self._grid.show_end()

    def _end_pull_refresh(self):
        if self._pull_refresh_active:
            self._pull_refresh_active = False
            self._grid.set_pull_refresh_active(False)
            self._set_header_elevated(False)

    def _set_header_elevated(self, active):
        root = self.get_root()
        if root is not None and hasattr(root, 'set_pull_refresh_header_elevated'):
            root.set_pull_refresh_header_elevated(active)

    def _on_child_activated(self, child):
        if self._on_release_activated and isinstance(child, ReleaseCard):
            self._on_release_activated(child.release)

    def _on_map(self, _widget):
        if self._pending_releases and not self._batch_idle:
            self._batch_idle = GLib.idle_add(self._add_pending_batch)

    def do_unmap(self):
        try:
            if self._batch_idle:
                GLib.source_remove(self._batch_idle)
                self._batch_idle = 0
            if self._pull_timer_id:
                GLib.source_remove(self._pull_timer_id)
                self._pull_timer_id = 0
        finally:
            Gtk.Box.do_unmap(self)
