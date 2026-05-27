# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from kitsune.api import AniLibriaClient

log = logging.getLogger('kitsune.ui.release')
from kitsune.models import Release, Episode
from kitsune.ui.image_cache import load_image
from kitsune import release_cache, watch_positions, tags_store
from kitsune.ui import register_css, format_size
from kitsune.ui import release_view_episodes as episodes_helper
from kitsune.ui import release_view_related as related_helper
from kitsune.ui import release_view_tags as tags_helper

_RELEASE_CSS = (
    '.release-chip { padding: 4px 10px; border-radius: 9999px;'
    ' background: alpha(currentColor, 0.1);'
    ' transition: background 150ms ease-in-out; }'
    ' .release-chip:hover { background: alpha(currentColor, 0.18); }'
    ' .release-chip-compact { padding: 8px; border-radius: 50%;'
    ' background: alpha(currentColor, 0.1);'
    ' min-width: 0; min-height: 0;'
    ' transition: background 150ms ease-in-out; }'
    ' .release-chip-compact:hover { background: alpha(currentColor, 0.18); }'
    ' .poster-fade { background: linear-gradient(to bottom,'
    ' transparent 40%, @window_bg_color 100%); }'
    ' button.favorite-active > image,'
    ' splitbutton.favorite-active > button > image { color: #f5c211; }'
)


@Gtk.Template(resource_path='/net/armatik/Kitsune/release_view.ui')
class ReleaseView(Adw.NavigationPage):
    __gtype_name__ = 'KitsuneReleaseView'

    toolbar = Gtk.Template.Child()
    header_bar = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    hero = Gtk.Template.Child()
    header = Gtk.Template.Child()
    poster = Gtk.Template.Child()
    info_box = Gtk.Template.Child()
    bg_wrapper = Gtk.Template.Child()
    bg_poster = Gtk.Template.Child()
    content_area = Gtk.Template.Child()
    description_label = Gtk.Template.Child()
    gradient_bg = Gtk.Template.Child()

    loading_spinner = Gtk.Template.Child()
    tabs_scroll = Gtk.Template.Child()
    tabs_header = Gtk.Template.Child()
    tabs_stack = Gtk.Template.Child()

    episodes_page = Gtk.Template.Child()
    episodes_toolbar = Gtk.Template.Child()
    episodes_search = Gtk.Template.Child()
    episodes_controls = Gtk.Template.Child()
    episodes_spinner = Gtk.Template.Child()
    episodes_list = Gtk.Template.Child()
    episodes_empty = Gtk.Template.Child()
    episodes_grid = Gtk.Template.Child()

    related_page = Gtk.Template.Child()
    related_spinner = Gtk.Template.Child()
    related_empty = Gtk.Template.Child()
    related_header = Gtk.Template.Child()
    related_list = Gtk.Template.Child()

    team_page = Gtk.Template.Child()
    team_empty = Gtk.Template.Child()
    team_list = Gtk.Template.Child()

    torrents_page = Gtk.Template.Child()
    torrents_empty = Gtk.Template.Child()
    torrents_list = Gtk.Template.Child()

    tag_split_btn = Gtk.Template.Child()
    home_btn = Gtk.Template.Child()

    _TAB_PAGES = ('episodes', 'related', 'team', 'torrents')

    def __init__(self, release: Release, client: AniLibriaClient,
                 sync_manager=None, **kwargs):
        super().__init__(title=release.name.main, **kwargs)
        self._release = release
        self._client = client
        self._sync = sync_manager
        self._on_episode_play = None
        self._on_genre_navigate = None
        self._on_tag_navigate = None
        self._on_tags_changed_ext = None
        self._on_home = None
        self._narrow_mode = False
        self._fade_anim = None
        self._accent_mode = False
        self._franchise = None
        self._episodes_view = 'list'  # overridden from settings below
        self._sort_newest_first = False
        self._search_text = ''
        self._refresh_fade_anim = None
        self._gradient_idle = 0
        self._refresh_timer = 0
        self._watch_data = {}
        self._watch_filter = 'all'
        register_css(_RELEASE_CSS)

        self._settings = Gio.Settings(schema_id='net.armatik.Kitsune')

        self._vadjustment = self.scrolled.get_vadjustment()
        self._vadjustment.connect('value-changed', self._on_scroll)

        # Try loading cached release data
        cached = release_cache.get(release.id)
        if cached:
            self._release = Release.from_dict(cached)

        self._setup_tabs_toggle()
        self._setup_episodes_controls()
        self._populate_info()

        if self._release.poster:
            self._load_poster(self._release.poster)

        # Header refresh indicator
        self._header_spinner = Adw.Spinner()
        self._header_check = Gtk.Image(
            icon_name='net.armatik.Kitsune.object-select-symbolic',
            css_classes=['success'],
        )
        self._header_check.set_opacity(0)
        self._header_status = Gtk.Box()
        self._header_status.append(self._header_spinner)
        self.header_bar.pack_end(self._header_status)

        # Show spinner until deferred content loads
        self.episodes_spinner.set_visible(True)

        self._deferred_done = False

        self.connect('realize', self._on_realize)
        self.connect('showing', self._on_showing)
        self.connect('shown', self._on_shown)

    def _deferred_init(self):
        """Populate heavy content after the navigation animation."""
        self.loading_spinner.set_visible(False)
        self.tabs_scroll.set_visible(True)
        self.tabs_stack.set_visible(True)

        if self._release.episodes:
            self._populate_episodes()
            self._apply_episodes_view()
            self.episodes_spinner.set_visible(False)
        else:
            self.episodes_spinner.set_visible(True)

        self._populate_team()
        self._populate_torrents()
        self._load_related()

        # Tag SplitButton popover
        from kitsune.ui.tag_popover import TagPopover
        self._tag_popover = TagPopover(
            release_id=self._release.id,
            on_changed=self._on_tags_changed,
            sync_manager=self._sync,
        )
        self.tag_split_btn.set_popover(self._tag_popover)
        self._update_favorite_icon()

        # External tag mutations (auto_collections moves, toast clicks,
        # write-through from another view) refresh our pills/star. The
        # disconnect is wired to `unrealize` so the bound method does
        # not keep this short-lived page alive past NavigationView pop.
        if self._sync:
            self._sync.connect_tags_changed(self._on_external_tags_changed)
            self.connect('unrealize', self._disconnect_tags_changed)

        # Always refresh from API
        self._start_refresh()

    def set_on_episode_play(self, callback):
        self._on_episode_play = callback

    def set_on_genre_clicked(self, callback):
        self._on_genre_navigate = callback

    def set_on_tag_clicked(self, callback):
        self._on_tag_navigate = callback

    def set_on_tags_changed(self, callback):
        self._on_tags_changed_ext = callback

    def set_on_home_clicked(self, callback):
        self._on_home = callback

    @Gtk.Template.Callback()
    def on_home_clicked(self, _button):
        if self._on_home:
            self._on_home()

    def _on_showing(self, _page):
        """Refresh progress on subsequent visits."""
        if self._deferred_done:
            self._refresh_episodes()
            if self._episodes_view == 'grid':
                self._refresh_episodes_grid()

    def _on_shown(self, _page):
        """Populate heavy content after the navigation animation completes."""
        if not self._deferred_done:
            self._deferred_done = True
            self._deferred_init()

    # --- Tabs (ToggleGroup + Carousel) ---

    _TAB_LABELS = {
        'episodes': _('Episodes'),
        'related': _('Related'),
        'team': _('Team'),
        'torrents': _('Torrents'),
    }

    def _setup_tabs_toggle(self):
        self._tabs_toggle = Adw.ToggleGroup()
        self._visible_tabs = []

        # Tag each Stack page with the matching tab name so we can flip
        # between them via set_visible_child_name(). Pages are defined
        # in the .blp template; their template-child handles are the
        # source of truth — we just attach names here.
        self._tab_pages = {
            'episodes': self.episodes_page,
            'related': self.related_page,
            'team': self.team_page,
            'torrents': self.torrents_page,
        }
        for name, widget in self._tab_pages.items():
            self.tabs_stack.get_page(widget).set_name(name)

        has_data = {
            'episodes': True,
            'related': False,  # async, added later
            'team': bool(self._release.members),
            'torrents': bool(self._release.torrents),
        }

        for name in self._TAB_PAGES:
            if has_data.get(name):
                self._visible_tabs.append(name)
                self._tabs_toggle.add(
                    Adw.Toggle(name=name, label=self._TAB_LABELS[name])
                )

        log.debug('visible tabs: %s', self._visible_tabs)

        self._tabs_toggle.set_active_name('episodes')
        self._tabs_toggle.connect('notify::active-name', self._on_tab_changed)
        self.tabs_header.append(self._tabs_toggle)

    def _add_tab(self, name):
        # All Stack pages exist from blueprint parse-time — there is no
        # "insert into widget tree" work to do for async-arriving tabs
        # like Related. We only manage which toggles the user sees.
        if name in self._visible_tabs:
            return
        log.debug('adding tab %s (async data arrived)', name)

        idx = list(self._TAB_PAGES).index(name)
        insert_at = 0
        for i, t in enumerate(self._visible_tabs):
            if list(self._TAB_PAGES).index(t) < idx:
                insert_at = i + 1
        self._visible_tabs.insert(insert_at, name)

        self._tabs_toggle.add(
            Adw.Toggle(name=name, label=self._TAB_LABELS[name])
        )

    def _on_tab_changed(self, toggle_group, _pspec):
        name = toggle_group.get_active_name()
        if name in self._visible_tabs:
            self.tabs_stack.set_visible_child_name(name)

    # --- Episodes controls ---

    def _setup_episodes_controls(self):
        # Search
        self.episodes_search.connect('search-changed', self._on_episodes_search_changed)
        self.episodes_controls.set_spacing(6)

        # Filter: All / Watched / Unwatched (wide mode)
        self._filter_toggle = Adw.ToggleGroup()
        self._filter_toggle.add(Adw.Toggle(name='all', label=_('All')))
        self._filter_toggle.add(Adw.Toggle(name='watched', label=_('Watched')))
        self._filter_toggle.add(Adw.Toggle(name='unwatched', label=_('Unwatched')))
        self._filter_toggle.set_active_name('all')
        self._filter_toggle.connect('notify::active-name', self._on_filter_changed)
        self.episodes_controls.append(self._filter_toggle)

        # Filter: MenuButton (compact mode, hidden by default)
        self._filter_menu_btn = Gtk.MenuButton(
            icon_name='net.armatik.Kitsune.funnel-symbolic',
            visible=False,
        )
        popover = Gtk.Popover()
        self._filter_pop_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._filter_pop_list.add_css_class('boxed-list')
        self._filter_rows = []
        for name, label in [('all', _('All')), ('watched', _('Watched')), ('unwatched', _('Unwatched'))]:
            row = Adw.ActionRow(title=label, activatable=True)
            row._filter_name = name
            self._filter_rows.append(row)
            self._filter_pop_list.append(row)
        self._filter_rows[0].add_prefix(Gtk.Image(icon_name='net.armatik.Kitsune.object-select-symbolic'))
        self._filter_pop_list.connect('row-activated', self._on_filter_row_activated)
        popover.set_child(self._filter_pop_list)
        self._filter_menu_btn.set_popover(popover)
        self.episodes_controls.append(self._filter_menu_btn)

        # Sort toggle
        sort_box = Gtk.Box(css_classes=['linked'])
        self._sort_btn = Gtk.Button(
            icon_name='view-sort-descending-symbolic',
            tooltip_text=_('Newest first'),
        )
        self._sort_btn.connect('clicked', self._on_sort_clicked)
        sort_box.append(self._sort_btn)
        self.episodes_controls.append(sort_box)

        # View toggle (list / grid)
        saved_view = self._settings.get_string('episodes-view')
        self._episodes_view = saved_view if saved_view in ('list', 'grid') else 'list'

        self._view_toggle = Adw.ToggleGroup()
        self._view_toggle.add(Adw.Toggle(
            name='list', icon_name='net.armatik.Kitsune.view-list-symbolic',
            tooltip=_('List view'),
        ))
        self._view_toggle.add(Adw.Toggle(
            name='grid', icon_name='net.armatik.Kitsune.view-grid-symbolic',
            tooltip=_('Grid view'),
        ))
        self._view_toggle.set_active_name(self._episodes_view)
        self._view_toggle.connect('notify::active-name', self._on_episodes_view_changed)
        self.episodes_controls.append(self._view_toggle)

        # Mark all watched / Unmark all
        mark_box = Gtk.Box(css_classes=['linked'])
        self._mark_all_btn = Gtk.Button(
            icon_name='net.armatik.Kitsune.object-select-symbolic',
            tooltip_text=_('Mark all as watched'),
        )
        self._unmark_all_btn = Gtk.Button(
            icon_name='net.armatik.Kitsune.cross-large-symbolic',
            tooltip_text=_('Unmark all'),
        )
        self._mark_all_btn.connect('clicked', self._on_mark_all_watched)
        self._unmark_all_btn.connect('clicked', self._on_unmark_all)
        mark_box.append(self._mark_all_btn)
        mark_box.append(self._unmark_all_btn)
        self.episodes_controls.append(mark_box)

    def _on_episodes_view_changed(self, toggle, _pspec):
        name = toggle.get_active_name()
        self._episodes_view = name
        self._settings.set_string('episodes-view', name)
        if name == 'grid':
            self._refresh_episodes_grid()
        else:
            # _update_empty_placeholder owns visibility for both list,
            # grid and the empty-state label based on filter results.
            self._update_empty_placeholder(self._get_filtered_episodes())

    def _apply_episodes_view(self):
        if self._episodes_view == 'grid':
            self._populate_episodes_grid()
        else:
            self._update_empty_placeholder(self._get_filtered_episodes())

    def _on_episodes_search_changed(self, entry):
        self._search_text = entry.get_text().strip().lower()
        self._refresh_episodes()

    def _on_filter_changed(self, toggle_group, _pspec):
        self._watch_filter = toggle_group.get_active_name()
        self._refresh_episodes()

    def _on_filter_row_activated(self, listbox, row):
        name = row._filter_name
        self._watch_filter = name
        self._filter_toggle.set_active_name(name)
        for r in self._filter_rows:
            child = r.get_first_child()
            while child:
                if isinstance(child, Gtk.Image):
                    r.remove(child)
                    break
                child = child.get_next_sibling()
        row.add_prefix(Gtk.Image(icon_name='net.armatik.Kitsune.object-select-symbolic'))
        self._filter_menu_btn.get_popover().popdown()
        self._refresh_episodes()

    def _on_sort_clicked(self, _button):
        self._sort_newest_first = not self._sort_newest_first
        if self._sort_newest_first:
            self._sort_btn.set_icon_name('view-sort-ascending-symbolic')
            self._sort_btn.set_tooltip_text(_('Oldest first'))
        else:
            self._sort_btn.set_icon_name('view-sort-descending-symbolic')
            self._sort_btn.set_tooltip_text(_('Newest first'))
        self._refresh_episodes()

    def _on_mark_all_watched(self, _button):
        # Mark every episode of the release as fully watched. Each
        # update touches local watch_positions and enqueues a server
        # push via SyncManager; auto_collections will pick the
        # completed state up through its standard hook.
        for ep in self._release.episodes:
            watch_positions.mark_completed(
                self._release.id, ep.ordinal, episode_id=ep.id,
            )
            if self._sync and ep.id:
                self._sync.enqueue_timecode(
                    release_id=self._release.id, episode_id=ep.id,
                    pos=0, is_watched=True,
                )
        self._reload_watch_data()
        self._refresh_episodes()
        self._maybe_trigger_completion_check()

    def _on_unmark_all(self, _button):
        # Inverse of mark-all: drop every local watch_positions entry
        # and tell the server to reset (pos=0, is_watched=False).
        for ep in self._release.episodes:
            watch_positions.remove_position(self._release.id, ep.ordinal)
            if self._sync and ep.id:
                self._sync.enqueue_timecode(
                    release_id=self._release.id, episode_id=ep.id,
                    pos=0, is_watched=False,
                )
        self._reload_watch_data()
        self._refresh_episodes()
        self._maybe_suggest_remove_from_watched()

    def _maybe_suggest_remove_from_watched(self):
        # If the release was sitting in the Watched collection (likely
        # auto-moved there when all episodes were marked), unmarking
        # everything means it no longer belongs. Offer a one-click
        # cleanup via toast instead of doing it silently — user might
        # be unmarking to rewatch and still want it in Watched.
        if not self._sync:
            return
        if self._release.id not in tags_store.get_release_ids_for_tag('watched'):
            return
        root = self.get_root()
        if root is None or not hasattr(root, 'toast_overlay'):
            return
        toast = Adw.Toast.new(_('Remove this title from “Watched”?'))
        toast.set_button_label(_('Remove'))
        toast.set_timeout(15)
        toast.connect('button-clicked', self._on_remove_from_watched_clicked)
        root.toast_overlay.add_toast(toast)

    def _on_remove_from_watched_clicked(self, _toast):
        if self._sync:
            self._sync.remove_from_tag_synced('watched', self._release.id)

    def _reload_watch_data(self):
        self._watch_data = watch_positions.get_all_for_release(self._release.id)

    def _maybe_trigger_completion_check(self):
        # Marking everything watched should let auto_collections move
        # the release to Watched if the user hasn't opted out. The
        # logic lives in player_view normally; mirror the minimal hook
        # here so bulk-marking has the same effect.
        if not self._sync:
            return
        try:
            settings = Gio.Settings(schema_id='net.armatik.Kitsune')
            if not settings.get_boolean('auto-collections-watch-events'):
                return
        except Exception:
            return
        from kitsune.storage import auto_collections
        release_meta = {
            'episodes_total': self._release.episodes_total,
            'is_ongoing': self._release.is_ongoing,
            'episodes': [
                {'id': e.id, 'ordinal': e.ordinal}
                for e in self._release.episodes
            ],
        }
        actions = auto_collections.evaluate_position_change(
            self._release.id, -1, release_meta,
        )
        for action in actions:
            if action.type == 'auto':
                auto_collections.apply_action(action, self._sync)

    def _get_filtered_episodes(self) -> list[Episode]:
        return episodes_helper.get_filtered_episodes(
            self._release.episodes, self._watch_filter,
            self._search_text, self._sort_newest_first, self._watch_data,
        )

    def _refresh_episodes(self):
        self._populate_episodes()
        if self._episodes_view == 'grid':
            self._refresh_episodes_grid()

    def _refresh_episodes_grid(self):
        if self.episodes_grid.get_visible():
            self._populate_episodes_grid()

    # --- Info ---

    def _populate_info(self):
        title_label = Gtk.Label(
            label=self._release.name.main,
            wrap=True, xalign=0, css_classes=['title-1'],
            use_markup=False,
        )
        self.info_box.append(title_label)

        if self._release.name.english:
            en_label = Gtk.Label(
                label=self._release.name.english,
                wrap=True, xalign=0, css_classes=['dim-label'],
                use_markup=False,
            )
            self.info_box.append(en_label)

        if self._release.genres:
            genre_wrap = Adw.WrapBox(
                line_spacing=6, child_spacing=6, margin_top=8,
            )
            for genre in self._release.genres:
                btn = Gtk.Button(
                    label=genre.name,
                    css_classes=['pill', 'release-chip'],
                )
                btn.connect('clicked', lambda _b, g=genre: self._on_genre_clicked(g))
                genre_wrap.append(btn)
            self.info_box.append(genre_wrap)

        # Tag pills
        self._tag_pills_wrap = Adw.WrapBox(
            line_spacing=6, child_spacing=6, margin_top=4,
        )
        self._update_tag_pills()
        self.info_box.append(self._tag_pills_wrap)

        meta_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4, margin_top=12,
        )
        if self._release.type:
            meta_box.append(self._meta_row(_('Type'), self._release.type))
        if self._release.year:
            meta_box.append(self._meta_row(_('Year'), str(self._release.year)))
        if self._release.season:
            meta_box.append(self._meta_row(_('Season'), self._release.season))
        if self._release.age_rating:
            meta_box.append(self._meta_row(
                _('Age rating'), self._format_age_rating(self._release.age_rating),
            ))
        if self._release.episodes_total:
            meta_box.append(self._meta_row(
                _('Episodes'), str(self._release.episodes_total),
            ))
        status = _('Ongoing') if self._release.is_ongoing else _('Completed')
        meta_box.append(self._meta_row(_('Status'), status))
        if self._release.is_ongoing and self._release.publish_day:
            meta_box.append(self._meta_row(
                _('Release day'), self._release.publish_day,
            ))
        self.info_box.append(meta_box)

        if self._release.description:
            self.description_label.set_label(self._release.description)
            self.description_label.set_visible(True)

    # --- Episodes (list) ---

    def _start_refresh(self):
        self._client.get_release_raw(
            self._release.alias or str(self._release.id),
            callback=self._on_raw_release_loaded,
        )

    def _on_raw_release_loaded(self, data, error):
        if not self.get_mapped():
            return
        self.episodes_spinner.set_visible(False)
        if error or not data:
            self._show_refresh_error()
            if not self._release.episodes:
                self._show_spinner_error(self.episodes_spinner)
            return

        release_cache.save(self._release.id, data)
        self._release = Release.from_dict(data)

        # Clear and repopulate info
        while child := self.info_box.get_first_child():
            self.info_box.remove(child)
        self._populate_info()

        self._populate_episodes()
        self._apply_episodes_view()
        self._populate_team()
        self._populate_torrents()
        if self._release.members:
            self._add_tab('team')
        if self._release.torrents:
            self._add_tab('torrents')
        self.tabs_stack.queue_resize()

        self._show_refresh_done()

    def _show_refresh_error(self):
        self._header_spinner.set_visible(False)
        error = Gtk.Image(
            icon_name='net.armatik.Kitsune.cross-large-symbolic',
            css_classes=['error'],
        )
        self._header_status.append(error)

    def _show_spinner_error(self, spinner):
        spinner.set_visible(False)
        error = Gtk.Image(
            icon_name='net.armatik.Kitsune.cross-large-symbolic',
            pixel_size=32,
            css_classes=['error'],
            halign=Gtk.Align.CENTER,
        )
        parent = spinner.get_parent()
        parent.insert_child_after(error, spinner)

    def _show_refresh_done(self):
        self._header_spinner.set_visible(False)
        self._header_check.set_opacity(1)
        self._header_status.append(self._header_check)
        self._refresh_timer = GLib.timeout_add(3000, self._fade_checkmark)

    def _fade_checkmark(self):
        self._refresh_timer = 0
        target = Adw.PropertyAnimationTarget.new(self._header_check, 'opacity')
        self._refresh_fade_anim = Adw.TimedAnimation.new(
            self._header_check, 1.0, 0.0, 500, target,
        )
        self._refresh_fade_anim.play()
        return GLib.SOURCE_REMOVE

    def _load_watch_data(self):
        self._watch_data = watch_positions.get_all_for_release(self._release.id)

    def _populate_episodes(self):
        self._load_watch_data()
        filtered = self._get_filtered_episodes()
        self._update_empty_placeholder(filtered)
        episodes_helper.populate_episode_list(
            self.episodes_list, filtered,
            self._watch_data, self._play_episode,
        )

    def _update_empty_placeholder(self, filtered):
        """Toggle the 'Nothing here yet' label vs the active list/grid
        based on whether the current filter produced any results.
        Shared by both list and grid populate paths so behaviour stays
        consistent regardless of view mode.
        """
        is_empty = not filtered
        self.episodes_empty.set_visible(is_empty)
        if self._episodes_view == 'grid':
            self.episodes_grid.set_visible(not is_empty)
            self.episodes_list.set_visible(False)
        else:
            self.episodes_list.set_visible(not is_empty)
            self.episodes_grid.set_visible(False)

    # --- Episodes (grid) ---

    def _populate_episodes_grid(self):
        while child := self.episodes_grid.get_first_child():
            self.episodes_grid.remove(child)

        self._load_watch_data()
        filtered = self._get_filtered_episodes()
        self._update_empty_placeholder(filtered)

        for episode in filtered:
            card = episodes_helper.build_episode_card(
                episode, self._watch_data, self._settings, self._play_episode,
            )
            self.episodes_grid.append(card)

    def _play_episode(self, episode: Episode):
        if self._on_episode_play:
            self._on_episode_play(self._release, episode)

    # --- Related (franchise) ---

    def _load_related(self):
        self.related_spinner.set_visible(True)
        self._client.get_franchise_for_release(
            self._release.id,
            callback=self._on_franchise_found,
        )

    def _on_franchise_found(self, franchise, error):
        if not self.get_mapped():
            return
        self.related_spinner.set_visible(False)
        if error:
            return
        if not franchise:
            return
        self._franchise = franchise
        self._add_tab('related')
        self._populate_related()
        self.tabs_stack.queue_resize()

    def _populate_related(self):
        self.related_spinner.set_visible(False)
        related_helper.populate_related(
            self.related_header, self.related_list, self._franchise,
            self._release.id, self._on_related_activated,
        )

    def _on_related_activated(self, release: Release):
        # Route through the window so adult-content guards, sync-manager
        # wiring and other cross-cutting concerns apply uniformly to
        # related-release navigations.
        root = self.get_root()
        if root is not None and hasattr(root, '_show_release_detail'):
            root._show_release_detail(release)

    # --- Team ---

    def _populate_team(self):
        while child := self.team_list.get_first_child():
            self.team_list.remove(child)

        if not self._release.members:
            self.team_empty.set_visible(True)
            return
        self.team_empty.set_visible(False)

        for member in self._release.members:
            row = Adw.ActionRow(
                title=member.nickname,
                subtitle=member.role,
                use_markup=False,
            )
            avatar = Adw.Avatar(size=40, text=member.nickname)
            if member.avatar:
                load_image(member.avatar, lambda tex, err, a=avatar:
                           a.set_custom_image(tex) if tex else None)
            row.add_prefix(avatar)
            self.team_list.append(row)

    # --- Torrents ---

    def _populate_torrents(self):
        while child := self.torrents_list.get_first_child():
            self.torrents_list.remove(child)

        if not self._release.torrents:
            self.torrents_empty.set_visible(True)
            return
        self.torrents_empty.set_visible(False)

        for torrent in self._release.torrents:
            title_parts = []
            if torrent.episode_range:
                title_parts.append(_('Episodes: %s') % torrent.episode_range)
            if torrent.codec:
                title_parts.append(torrent.codec)
            title = '  '.join(title_parts) if title_parts else torrent.label

            subtitle_parts = [format_size(torrent.size)]
            if torrent.quality:
                subtitle_parts.append(torrent.quality)
            if torrent.seeders:
                subtitle_parts.append(f'\u2191{torrent.seeders}')
            if torrent.leechers:
                subtitle_parts.append(f'\u2193{torrent.leechers}')
            if torrent.completed_times:
                subtitle_parts.append(f'\u2713{torrent.completed_times}')
            if torrent.is_hardsub:
                subtitle_parts.append(_('Hardsub'))

            row = Adw.ActionRow(
                title=title,
                subtitle=' \u2022 '.join(subtitle_parts),
                use_markup=False,
            )

            download_btn = Gtk.Button(
                icon_name='folder-download-symbolic',
                valign=Gtk.Align.CENTER,
                css_classes=['flat'],
                tooltip_text=_('Download torrent'),
            )
            download_btn.connect('clicked', self._on_torrent_download, torrent)
            row.add_suffix(download_btn)

            magnet_btn = Gtk.Button(
                icon_name='net.armatik.Kitsune.magnet-symbolic',
                valign=Gtk.Align.CENTER,
                css_classes=['flat'],
                tooltip_text=_('Open magnet link'),
            )
            magnet_btn.connect('clicked', self._on_magnet_clicked, torrent)
            row.add_suffix(magnet_btn)

            self.torrents_list.append(row)

    def _on_torrent_download(self, _button, torrent):
        from kitsune import API_BASE_URL
        url = f'{API_BASE_URL}/anime/torrents/{int(torrent.id)}/file'
        launcher = Gtk.UriLauncher(uri=url)
        launcher.launch(self.get_root(), None, None, None)

    def _on_magnet_clicked(self, _button, torrent):
        if torrent.magnet and torrent.magnet.startswith('magnet:'):
            launcher = Gtk.UriLauncher(uri=torrent.magnet)
            launcher.launch(self.get_root(), None, None, None)

    # --- Toolbar / scroll ---

    @Gtk.Template.Callback()
    def on_bp_apply(self, _bp):
        self._narrow_mode = True
        self._filter_toggle.set_visible(False)
        self._filter_menu_btn.set_visible(True)
        self.episodes_toolbar.reorder_child_after(self.episodes_controls, None)
        self.episodes_controls.set_halign(Gtk.Align.CENTER)
        self._update_toolbar()
        self._update_tag_pills()
        if self._accent_mode:
            mobile_ok = self._settings.get_boolean('accent-mobile-enabled')
            if not mobile_ok:
                self.gradient_bg.set_opacity(0)

    @Gtk.Template.Callback()
    def on_bp_unapply(self, _bp):
        self._narrow_mode = False
        self._filter_toggle.set_visible(True)
        self._filter_menu_btn.set_visible(False)
        self.episodes_toolbar.reorder_child_after(
            self.episodes_controls, self.episodes_search,
        )
        self.episodes_controls.set_halign(Gtk.Align.FILL)
        self._update_toolbar()
        self._update_tag_pills()
        if self._accent_mode:
            self.gradient_bg.set_opacity(0.3)

    def _on_realize(self, _widget):
        root = self.get_root()
        if root:
            self.loading_spinner.set_size_request(-1, root.get_height())
            if root.get_width() <= 500:
                self._narrow_mode = True
                self.toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
                self.toolbar.set_extend_content_to_top_edge(True)

    def _update_toolbar(self):
        hero_h = self.hero.get_height()
        past_hero = hero_h > 0 and self._vadjustment.get_value() > hero_h

        if not self._narrow_mode:
            self.toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
            self.toolbar.set_extend_content_to_top_edge(False)
            self.header_bar.set_show_title(past_hero)
            return

        if past_hero:
            self.toolbar.set_top_bar_style(Adw.ToolbarStyle.RAISED)
            self.toolbar.set_extend_content_to_top_edge(False)
            self.header_bar.set_show_title(True)
        else:
            self.toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
            self.toolbar.set_extend_content_to_top_edge(True)
            self.header_bar.set_show_title(False)

    def _on_scroll(self, _adjustment):
        self._update_toolbar()

    # --- Misc ---

    def _on_genre_clicked(self, genre):
        if self._on_genre_navigate:
            self._on_genre_navigate(genre)

    # --- Tags ---

    @Gtk.Template.Callback()
    def on_favorite_clicked(self, _button):
        if self._sync:
            self._sync.toggle_favorite_synced(self._release.id)
        else:
            tags_store.toggle_favorite(self._release.id)
        self._update_favorite_icon()
        self._update_tag_pills()
        if self._on_tags_changed_ext:
            self._on_tags_changed_ext(self._release.id)

    def _update_favorite_icon(self):
        is_fav = tags_store.is_favorited(self._release.id)
        self.tag_split_btn.set_icon_name(
            'net.armatik.Kitsune.starred-symbolic' if is_fav else 'net.armatik.Kitsune.non-starred-symbolic'
        )
        if is_fav:
            self.tag_split_btn.add_css_class('favorite-active')
        else:
            self.tag_split_btn.remove_css_class('favorite-active')

    def _on_tags_changed(self):
        self._update_favorite_icon()
        self._update_tag_pills()
        if self._on_tags_changed_ext:
            self._on_tags_changed_ext(self._release.id)

    def _on_external_tags_changed(self, release_id):
        # Triggered by sync_manager any time some release's tags change.
        # Filter to current release; bail if widget already detached so
        # leftover subscriptions on dead views don't crash on access.
        if release_id != self._release.id:
            return
        if self.get_root() is None:
            return
        self._on_tags_changed()

    def _disconnect_tags_changed(self, _widget):
        if self._sync:
            self._sync.disconnect_tags_changed(self._on_external_tags_changed)

    def _update_tag_pills(self):
        if not hasattr(self, '_tag_pills_wrap'):
            return
        tags_helper.update_tag_pills(
            self._tag_pills_wrap, self._release.id,
            self._narrow_mode, self._on_tag_pill_clicked,
        )

    def _on_tag_pill_clicked(self, tag):
        if self._on_tag_navigate:
            self._on_tag_navigate(tag)

    @staticmethod
    def _meta_row(label, value):
        row = Gtk.Box(spacing=8)
        row.append(Gtk.Label(
            label=f'{label}:',
            css_classes=['dim-label'], xalign=0,
        ))
        row.append(Gtk.Label(label=value, xalign=0, wrap=True, hexpand=True))
        return row

    @staticmethod
    def _format_age_rating(rating: str) -> str:
        mapping = {
            'R0_PLUS': '0+', 'R6_PLUS': '6+', 'R12_PLUS': '12+',
            'R16_PLUS': '16+', 'R18_PLUS': '18+',
        }
        return mapping.get(rating, rating)

    # --- Poster / accent ---

    def _load_poster(self, url: str):
        load_image(url, self._on_poster_loaded)

    def _on_poster_loaded(self, texture, error):
        if texture:
            self.poster.set_paintable(texture)
            self.bg_poster.set_paintable(texture)
            GLib.idle_add(self._apply_page_style, texture)

    def _apply_page_style(self, texture: Gdk.Texture):
        style = self._settings.get_string('release-page-style')
        if style != 'accent':
            return GLib.SOURCE_REMOVE

        n_points = self._settings.get_int('accent-color-points')
        glass = self._settings.get_boolean('accent-glass-effect')

        # Convert texture to PNG bytes on the main thread (GDK is not thread-safe)
        png_bytes = texture.save_to_png_bytes()

        import threading
        from kitsune.ui.color_extractor import extract_colors_from_bytes, create_gradient_bytes

        def _generate():
            colors = extract_colors_from_bytes(png_bytes)
            gradient_data = create_gradient_bytes(colors, n_points=n_points, noise=glass)
            self._gradient_idle = GLib.idle_add(
                self._on_gradient_ready, gradient_data)

        threading.Thread(target=_generate, daemon=True).start()
        return GLib.SOURCE_REMOVE

    def _on_gradient_ready(self, gradient_data):
        self._gradient_idle = 0
        if not self.get_mapped():
            return GLib.SOURCE_REMOVE
        # Create GDK texture on the main thread
        gbytes = GLib.Bytes.new(gradient_data)
        gradient = Gdk.Texture.new_from_bytes(gbytes)
        self.gradient_bg.set_paintable(gradient)
        self._accent_mode = True

        mobile_ok = self._settings.get_boolean('accent-mobile-enabled')
        if self._narrow_mode and not mobile_ok:
            return

        self._start_gradient_fade()

    def _start_gradient_fade(self):
        duration = self._settings.get_int('accent-fade-duration')
        target = Adw.PropertyAnimationTarget.new(self.gradient_bg, 'opacity')
        self._fade_anim = Adw.TimedAnimation.new(self.gradient_bg, 0, 0.3, duration, target)
        self._fade_anim.play()
        return GLib.SOURCE_REMOVE

    def do_unmap(self):
        try:
            if self._fade_anim:
                self._fade_anim.skip()
                self._fade_anim = None
            if self._refresh_fade_anim:
                self._refresh_fade_anim.skip()
                self._refresh_fade_anim = None
            if self._gradient_idle:
                GLib.source_remove(self._gradient_idle)
                self._gradient_idle = 0
            if self._refresh_timer:
                GLib.source_remove(self._refresh_timer)
                self._refresh_timer = 0
        finally:
            Adw.NavigationPage.do_unmap(self)
