# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GObject, Gtk

from kitsune.player.display_rotate import check_available
from kitsune.ui import format_size
from kitsune.ui.image_cache import get_cache_size, get_cache_count, clear_cache
from kitsune import release_cache, watch_positions, tags_store
from kitsune.navbar import (
    ALL_TAB_IDS, get_tab, ensure_complete, parse_tab_order, serialize_tab_order,
)

_STYLE_DESCRIPTIONS = {
    'classic': _('Standard layout without background effects'),
    'accent': _('Gradient background from poster accent colors'),
}



@Gtk.Template(resource_path='/net/armatik/Kitsune/preferences_window.ui')
class PreferencesWindow(Adw.PreferencesDialog):
    __gtype_name__ = 'KitsunePreferencesWindow'

    auto_watch_events_row = Gtk.Template.Child()
    auto_idle_scan_row = Gtk.Template.Child()
    adult_warning_row = Gtk.Template.Child()
    cache_size_row = Gtk.Template.Child()
    preview_size_row = Gtk.Template.Child()
    release_size_row = Gtk.Template.Child()
    watch_size_row = Gtk.Template.Child()
    tags_size_row = Gtk.Template.Child()
    style_toggle = Gtk.Template.Child()
    style_description = Gtk.Template.Child()
    accent_group = Gtk.Template.Child()
    mobile_enabled_row = Gtk.Template.Child()
    glass_effect_row = Gtk.Template.Child()
    color_points_row = Gtk.Template.Child()
    fade_duration_row = Gtk.Template.Child()
    blur_unwatched_row = Gtk.Template.Child()
    close_button_row = Gtk.Template.Child()
    rotate_button_row = Gtk.Template.Child()
    navbar_sync_row = Gtk.Template.Child()
    navbar_sheet_style_row = Gtk.Template.Child()
    navbar_desktop_group = Gtk.Template.Child()
    navbar_desktop_list = Gtk.Template.Child()
    navbar_mobile_group = Gtk.Template.Child()
    navbar_mobile_list = Gtk.Template.Child()
    search_categories_group = Gtk.Template.Child()
    search_categories_list = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self._settings = Gio.Settings(schema_id='net.armatik.Kitsune')

        current = self._settings.get_string('release-page-style')
        self.style_toggle.set_active_name(current)
        self._update_style_description(current)
        self._update_accent_group_visibility(current)

        self.mobile_enabled_row.set_active(self._settings.get_boolean('accent-mobile-enabled'))
        self.mobile_enabled_row.connect('notify::active', self._on_mobile_enabled_changed)

        self.glass_effect_row.set_active(self._settings.get_boolean('accent-glass-effect'))
        self.glass_effect_row.connect('notify::active', self._on_glass_effect_changed)

        self.color_points_row.set_value(self._settings.get_int('accent-color-points'))
        self.fade_duration_row.set_value(self._settings.get_int('accent-fade-duration'))

        self.color_points_row.connect('notify::value', self._on_color_points_changed)
        self.fade_duration_row.connect('notify::value', self._on_fade_duration_changed)

        self.blur_unwatched_row.set_active(
            self._settings.get_boolean('blur-unwatched-episodes'))
        self.blur_unwatched_row.connect(
            'notify::active', self._on_blur_unwatched_changed)

        self.close_button_row.set_active(
            self._settings.get_boolean('player-show-close-button'))
        self.close_button_row.connect('notify::active', self._on_close_button_changed)

        self.rotate_button_row.set_active(
            self._settings.get_boolean('player-show-rotate-button'))
        self.rotate_button_row.connect('notify::active', self._on_rotate_button_changed)
        check_available(self._on_rotate_check)

        # Auto-collections preferences — bound directly to GSettings so
        # toggle state survives restarts and the schema's default kicks
        # in cleanly when the key is unset.
        self._settings.bind(
            'auto-collections-watch-events', self.auto_watch_events_row,
            'active', Gio.SettingsBindFlags.DEFAULT,
        )
        self._settings.bind(
            'auto-collections-idle-scan', self.auto_idle_scan_row,
            'active', Gio.SettingsBindFlags.DEFAULT,
        )
        # The schema stores "disabled" semantics; INVERT_BOOLEAN flips
        # it so the toggle reads naturally — switch ON means warning ON.
        self._settings.bind(
            'adult-warning-disabled', self.adult_warning_row,
            'active',
            Gio.SettingsBindFlags.DEFAULT | Gio.SettingsBindFlags.INVERT_BOOLEAN,
        )

        self._update_cache_size()
        self._update_preview_cache()
        self._update_release_cache()
        self._update_watch_progress()
        self._update_tags()
        self._setup_navbar_prefs()
        self._setup_search_categories()

    def _update_style_description(self, name: str):
        self.style_description.set_label(
            _STYLE_DESCRIPTIONS.get(name, '')
        )

    def _update_accent_group_visibility(self, style: str):
        self.accent_group.set_visible(style == 'accent')

    def _update_cache_size(self):
        try:
            count = get_cache_count('posters')
            size = get_cache_size('posters')
        except OSError:
            count, size = 0, 0
        self.cache_size_row.set_subtitle(
            f'{count} — {format_size(size)}')

    def _update_preview_cache(self):
        try:
            count = get_cache_count('previews')
            size = get_cache_size('previews')
        except OSError:
            count, size = 0, 0
        self.preview_size_row.set_subtitle(
            f'{count} — {format_size(size)}')

    @Gtk.Template.Callback()
    def on_style_changed(self, toggle_group, _pspec):
        name = toggle_group.get_active_name()
        if not name:
            return
        self._settings.set_string('release-page-style', name)
        self._update_style_description(name)
        self._update_accent_group_visibility(name)

    def _on_mobile_enabled_changed(self, row, _pspec):
        self._settings.set_boolean('accent-mobile-enabled', row.get_active())

    def _on_glass_effect_changed(self, row, _pspec):
        self._settings.set_boolean('accent-glass-effect', row.get_active())

    def _on_color_points_changed(self, row, _pspec):
        self._settings.set_int('accent-color-points', int(row.get_value()))

    def _on_fade_duration_changed(self, row, _pspec):
        self._settings.set_int('accent-fade-duration', int(row.get_value()))

    def _on_blur_unwatched_changed(self, row, _pspec):
        self._settings.set_boolean('blur-unwatched-episodes', row.get_active())

    def _on_close_button_changed(self, row, _pspec):
        self._settings.set_boolean('player-show-close-button', row.get_active())

    def _on_rotate_button_changed(self, row, _pspec):
        self._settings.set_boolean('player-show-rotate-button', row.get_active())

    def _on_rotate_check(self, available):
        if available:
            self.rotate_button_row.set_visible(True)

    def _update_watch_progress(self):
        try:
            count = watch_positions.get_count()
            size = watch_positions.get_size()
        except OSError:
            count, size = 0, 0
        self.watch_size_row.set_subtitle(
            f'{count} — {format_size(size)}')

    def _update_release_cache(self):
        try:
            count = release_cache.get_count()
            size = release_cache.get_size()
        except OSError:
            count, size = 0, 0
        self.release_size_row.set_subtitle(
            f'{count} — {format_size(size)}')

    @Gtk.Template.Callback()
    def on_clear_release_clicked(self, _button):
        try:
            release_cache.clear_all()
        except OSError:
            pass
        self._update_release_cache()

    @Gtk.Template.Callback()
    def on_clear_clicked(self, _button):
        try:
            clear_cache('posters')
        except OSError:
            pass
        self._update_cache_size()

    @Gtk.Template.Callback()
    def on_clear_preview_clicked(self, _button):
        try:
            clear_cache('previews')
        except OSError:
            pass
        self._update_preview_cache()

    @Gtk.Template.Callback()
    def on_clear_progress_clicked(self, _button):
        try:
            watch_positions.clear_all()
        except OSError:
            pass
        self._update_watch_progress()

    def _update_tags(self):
        try:
            count = tags_store.get_count()
            size = tags_store.get_size()
        except OSError:
            count, size = 0, 0
        self.tags_size_row.set_subtitle(
            f'{count} — {format_size(size)}')

    @Gtk.Template.Callback()
    def on_clear_tags_clicked(self, _button):
        try:
            tags_store.clear_all()
        except OSError:
            pass
        self._update_tags()

    # --- Search Categories ---

    _ALL_SEARCH_CATEGORIES = ['anime', 'genres', 'franchises', 'tags']
    _SEARCH_CAT_LABELS = {
        'anime': _('Anime'),
        'genres': _('Genres'),
        'franchises': _('Franchises'),
        'tags': _('Tags'),
    }
    _SEARCH_CAT_ICONS = {
        'anime': 'net.armatik.Kitsune.view-grid-symbolic',
        'genres': 'net.armatik.Kitsune.genres-symbolic',
        'franchises': 'net.armatik.Kitsune.franchises-symbolic',
        'tags': 'net.armatik.Kitsune.starred-symbolic',
    }

    # --- Navigation Preferences ---

    _NAV_TAB_LABELS = {
        'catalog': _('Catalog'),
        'genres': _('Genres'),
        'franchises': _('Franchises'),
        'tags': _('Favorites and Tags'),
    }

    _SHEET_STYLES = ['grid', 'list']

    def _setup_navbar_prefs(self):
        self.navbar_sync_row.set_active(
            self._settings.get_boolean('navbar-sync'))
        self.navbar_sync_row.connect(
            'notify::active', self._on_navbar_sync_changed)

        # Sheet style combo
        current_style = self._settings.get_string('navbar-sheet-style')
        idx = self._SHEET_STYLES.index(current_style) \
            if current_style in self._SHEET_STYLES else 0
        self.navbar_sheet_style_row.set_selected(idx)
        self.navbar_sheet_style_row.connect(
            'notify::selected', self._on_sheet_style_changed)

        self._rebuild_navbar_list('navbar-desktop', self.navbar_desktop_list)
        self._rebuild_navbar_list('navbar-mobile', self.navbar_mobile_list)
        self._update_mobile_sensitivity()

    def _update_mobile_sensitivity(self):
        is_sync = self.navbar_sync_row.get_active()
        self.navbar_mobile_group.set_sensitive(not is_sync)

    def _on_navbar_sync_changed(self, row, _pspec):
        self._settings.set_boolean('navbar-sync', row.get_active())
        self._update_mobile_sensitivity()

    def _on_sheet_style_changed(self, row, _pspec):
        idx = row.get_selected()
        if 0 <= idx < len(self._SHEET_STYLES):
            self._settings.set_string(
                'navbar-sheet-style', self._SHEET_STYLES[idx])

    def _rebuild_navbar_list(self, settings_key, listbox):
        """Build a tab list with drag handles and visibility toggles."""
        while True:
            row = listbox.get_row_at_index(0)
            if row is None:
                break
            listbox.remove(row)

        visible_ids = parse_tab_order(
            self._settings.get_string(settings_key))
        all_ids = ensure_complete(visible_ids)
        visible_set = set(visible_ids)

        if not hasattr(self, '_navbar_entries'):
            self._navbar_entries = {}
        self._navbar_entries[settings_key] = []

        for tab_id in all_ids:
            tab = get_tab(tab_id)
            if not tab:
                continue

            is_visible = tab_id in visible_set

            row = Adw.ActionRow(
                title=self._NAV_TAB_LABELS.get(tab_id, tab['label']),
            )

            # Tab icon first, then drag handle (add_prefix prepends)
            icon = Gtk.Image(icon_name=tab['icon'])
            row.add_prefix(icon)

            handle = Gtk.Image(icon_name='list-drag-handle-symbolic')
            handle.add_css_class('dim-label')
            handle.set_cursor(Gdk.Cursor.new_from_name('grab'))
            row.add_prefix(handle)

            # DragSource on handle
            drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            drag.connect('prepare', self._on_drag_prepare, tab_id)
            handle.add_controller(drag)

            # DropTarget on row
            drop = Gtk.DropTarget.new(GObject.TYPE_STRING,
                                      Gdk.DragAction.MOVE)
            drop.connect('drop', self._on_nav_drop, settings_key, listbox)
            row.add_controller(drop)

            # Visibility switch
            switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=is_visible)
            switch.connect('notify::active',
                           self._on_tab_visibility_changed,
                           settings_key, listbox)
            row.add_suffix(switch)
            listbox.append(row)
            self._navbar_entries[settings_key].append((tab_id, switch))

    def _on_tab_visibility_changed(self, switch, _pspec,
                                   settings_key, listbox):
        entries = self._navbar_entries.get(settings_key, [])
        visible = [tid for tid, sw in entries if sw.get_active()]
        if not visible:
            fallback = ALL_TAB_IDS[0]
            for tid, sw in entries:
                if tid == fallback:
                    sw.set_active(True)
                    return
            if entries:
                entries[0][1].set_active(True)
                return
        self._settings.set_string(settings_key, serialize_tab_order(visible))
        self._rebuild_navbar_list(settings_key, listbox)

    # --- Drag and drop ---

    def _on_drag_prepare(self, drag_source, x, y, item_id):
        return Gdk.ContentProvider.new_for_value(
            GObject.Value(GObject.TYPE_STRING, item_id))

    def _on_nav_drop(self, drop_target, value, x, y,
                     settings_key, listbox):
        source_id = value
        entries = self._navbar_entries[settings_key]
        visible = [tid for tid, sw in entries if sw.get_active()]

        target_widget = drop_target.get_widget()
        row_idx = target_widget.get_index()
        if row_idx < 0 or row_idx >= len(entries):
            return False

        target_id = entries[row_idx][0]
        if source_id not in visible or target_id not in visible:
            return False
        if source_id == target_id:
            return True

        visible.remove(source_id)
        insert_idx = visible.index(target_id)
        if y > target_widget.get_height() / 2:
            insert_idx += 1
        visible.insert(insert_idx, source_id)

        self._settings.set_string(settings_key,
                                  serialize_tab_order(visible))
        self._rebuild_navbar_list(settings_key, listbox)
        return True

    # --- Search Categories ---

    def _setup_search_categories(self):
        self._rebuild_search_categories()

    def _parse_search_order(self):
        try:
            raw = self._settings.get_string('search-category-order')
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                seen = set()
                result = []
                for c in parsed:
                    if c in self._ALL_SEARCH_CATEGORIES and c not in seen:
                        seen.add(c)
                        result.append(c)
                if result:
                    return result
        except (json.JSONDecodeError, ValueError):
            pass
        return list(self._ALL_SEARCH_CATEGORIES)

    def _rebuild_search_categories(self):
        listbox = self.search_categories_list
        while True:
            row = listbox.get_row_at_index(0)
            if row is None:
                break
            listbox.remove(row)

        visible_ids = self._parse_search_order()
        all_ids = list(visible_ids)
        for cid in self._ALL_SEARCH_CATEGORIES:
            if cid not in all_ids:
                all_ids.append(cid)
        visible_set = set(visible_ids)

        self._search_cat_entries = []

        for cid in all_ids:
            is_visible = cid in visible_set

            row = Adw.ActionRow(
                title=self._SEARCH_CAT_LABELS.get(cid, cid),
            )

            # Category icon first, then drag handle (add_prefix prepends)
            icon_name = self._SEARCH_CAT_ICONS.get(cid, '')
            if icon_name:
                icon = Gtk.Image(icon_name=icon_name)
                row.add_prefix(icon)

            handle = Gtk.Image(icon_name='list-drag-handle-symbolic')
            handle.add_css_class('dim-label')
            handle.set_cursor(Gdk.Cursor.new_from_name('grab'))
            row.add_prefix(handle)

            # DragSource on handle
            drag = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
            drag.connect('prepare', self._on_drag_prepare, cid)
            handle.add_controller(drag)

            # DropTarget on row
            drop = Gtk.DropTarget.new(GObject.TYPE_STRING,
                                      Gdk.DragAction.MOVE)
            drop.connect('drop', self._on_search_drop)
            row.add_controller(drop)

            switch = Gtk.Switch(valign=Gtk.Align.CENTER, active=is_visible)
            switch.connect('notify::active', self._on_search_cat_toggled)
            row.add_suffix(switch)
            listbox.append(row)
            self._search_cat_entries.append((cid, switch))

    def _save_search_order(self):
        visible = [cid for cid, sw in self._search_cat_entries
                   if sw.get_active()]
        if not visible:
            fallback = self._ALL_SEARCH_CATEGORIES[0]
            for cid, sw in self._search_cat_entries:
                if cid == fallback:
                    sw.set_active(True)
                    return
            if self._search_cat_entries:
                self._search_cat_entries[0][1].set_active(True)
                return
        self._settings.set_string('search-category-order',
                                  json.dumps(visible))
        self._rebuild_search_categories()

    def _on_search_cat_toggled(self, _switch, _pspec):
        self._save_search_order()

    def _on_search_drop(self, drop_target, value, x, y):
        source_id = value
        entries = self._search_cat_entries
        visible = [cid for cid, sw in entries if sw.get_active()]

        target_widget = drop_target.get_widget()
        row_idx = target_widget.get_index()
        if row_idx < 0 or row_idx >= len(entries):
            return False

        target_id = entries[row_idx][0]
        if source_id not in visible or target_id not in visible:
            return False
        if source_id == target_id:
            return True

        visible.remove(source_id)
        insert_idx = visible.index(target_id)
        if y > target_widget.get_height() / 2:
            insert_idx += 1
        visible.insert(insert_idx, source_id)

        self._settings.set_string('search-category-order',
                                  json.dumps(visible))
        self._rebuild_search_categories()
        return True
