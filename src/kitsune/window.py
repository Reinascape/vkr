# SPDX-License-Identifier: GPL-3.0-or-later

import logging
import sys

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, GLib, Gtk, Gio

from kitsune import ADW_TRANSITION
from kitsune.api import AniLibriaClient
from kitsune.auth import SessionManager
from kitsune.navbar import get_tab, get_visible_tabs
from kitsune.storage import tags_store
from kitsune.storage.sync_manager import SyncManager
from kitsune.ui import register_css
from kitsune.ui.auth_dialog import AuthDialog
from kitsune.ui.catalog_view import CatalogView

_T = ADW_TRANSITION

log = logging.getLogger('kitsune.window')
_NAV_CSS = (
    '.nav-tab { background: none;'
    ' border-radius: 12px; padding: 6px 8px;'
    ' transition: background ' + _T + '; }'
    ' .nav-tab:hover { background: alpha(currentColor, 0.07); }'
    ' .nav-tab-active { background: alpha(currentColor, 0.1); }'
    ' .nav-tab-active:hover { background: alpha(currentColor, 0.14); }'
    ' .drag-handle-pill { background: alpha(currentColor, 0.25);'
    ' border-radius: 2px; }'
    ' .sheet-grid-item { padding: 8px 6px;'
    ' border-radius: 12px; }'
    ' .sheet-grid flowboxchild { background: none; }'
    ' .sheet-grid flowboxchild:hover { background: none; }'
    ' .sheet-grid flowboxchild:active { background: none; }'
    # Bold the user's nickname in the sidebar auth row so the
    # identity reads at a glance against the dim sync-time
    # subtitle right below it.
    ' .auth-row-bold .title { font-weight: bold; }'
    # Elevation hint applied to the narrow headerbar during the catalog
    # pull-refresh animation. The drop-shadow visually anchors the bar
    # as the revealer pushes content downward, so the bar reads as a
    # fixed top layer rather than slipping with the gradient below it.
    ' headerbar.kitsune-narrow-header {'
    ' transition: box-shadow 250ms ease; }'
    ' headerbar.kitsune-narrow-header.pull-refresh-elevated {'
    ' box-shadow: 0 2px 8px alpha(black, 0.18); }'
)


@Gtk.Template(resource_path='/net/armatik/Kitsune/window.ui')
class KitsuneWindow(Adw.ApplicationWindow):
    __gtype_name__ = 'KitsuneWindow'

    toast_overlay = Gtk.Template.Child()
    session_expired_banner = Gtk.Template.Child()
    nav_view = Gtk.Template.Child()
    offline_banner = Gtk.Template.Child()
    multi = Gtk.Template.Child()
    content_stack = Gtk.Template.Child()
    filter_btn = Gtk.Template.Child()
    refresh_btn = Gtk.Template.Child()
    mode_btn = Gtk.Template.Child()
    add_tag_btn = Gtk.Template.Child()
    delete_tag_btn = Gtk.Template.Child()
    filter_split = Gtk.Template.Child()
    sidebar_list = Gtk.Template.Child()
    auth_sidebar_list = Gtk.Template.Child()
    sidebar_title = Gtk.Template.Child()
    wide_content_title = Gtk.Template.Child()
    back_btn = Gtk.Template.Child()
    narrow_back_btn = Gtk.Template.Child()
    narrow_sheet = Gtk.Template.Child()
    narrow_bottom_bar = Gtk.Template.Child()
    narrow_drag_handle = Gtk.Template.Child()
    narrow_tabs_box = Gtk.Template.Child()
    narrow_sheet_box = Gtk.Template.Child()
    narrow_toolbar = Gtk.Template.Child()
    narrow_header = Gtk.Template.Child()

    def __init__(self, client=None, session_manager=None, **kwargs):
        super().__init__(**kwargs)
        if client:
            self._client = client
        else:
            app = self.get_application() or kwargs.get('application')
            version = app._version if app else '0.0.0'
            self._client = AniLibriaClient(version=version)
        self._client.set_on_network_error(self._on_network_error)
        self._client.set_on_network_ok(self._on_network_ok)
        self._session = session_manager
        self._sync = SyncManager(self._client)
        self._sync_timer_id = 0
        self._profile_view = None
        # Suppresses on_sidebar_row_selected when _switch_tab calls
        # select_row programmatically — otherwise the signal handler
        # would call _switch_tab again, recursing.
        self._suppress_sidebar_callback = False
        self._settings = Gio.Settings(schema_id='net.armatik.Kitsune')
        # Restore last-known user_id BEFORE any sync work can start so
        # the pending queue is correctly tagged from the very first
        # enqueue — even before validate_session has returned a fresh
        # profile. Without this, write-through ops on app startup would
        # land in the queue with user_id=0, and a subsequent account
        # switch would fail to evict them via clear_for_user().
        stored_uid = self._settings.get_int('last-user-id')
        if stored_uid:
            self._sync.set_user_id(stored_uid)
        register_css(_NAV_CSS)
        # macOS renders the app name in its native title bar, so a second
        # "Kitsune" inside the sidebar header would just duplicate it. On
        # any other platform (Linux desktop, Phosh wide mode) we draw our
        # own CSD without an OS-level title, so the label belongs here.
        if sys.platform != 'darwin':
            self.sidebar_title.set_title('Kitsune')
        self._active_player = None
        self._setup_window_state()
        self._setup_actions()
        self._setup_views()
        self.nav_view.connect('popped', self._on_nav_popped)

        if self._session:
            self._session.connect_logged_in(self._on_logged_in)
            self._session.connect_logged_out(self._on_logged_out)
            self._session.connect_session_expired(
                self._sync.pause_for_expired_session)
            self._session.connect_session_restored(
                self._sync.resume_after_expired_session)
            # Clear queue + stop drain BEFORE force_logout_cleanup wipes
            # local data, so no in-flight op can race-commit to the
            # soon-to-be-invalidated session.
            self._session.connect_pre_logout(
                self._sync.clear_queue_on_logout)
            if self._session.is_logged_in():
                self._session.validate_session(self._on_session_validated)

        # Session-expired banner wiring — button-clicked handler is in
        # window.blp ($on_session_banner_login); reveal is toggled by
        # session callbacks below.
        if self._session:
            self._session.connect_session_expired(
                self._on_session_expired_show_banner)
            self._session.connect_session_restored(
                self._on_session_restored_hide_banner)
            self._session.connect_logged_out(
                self._on_session_logged_out_hide_banner)

        # Sync-error toast wiring with 5-second throttle
        self._last_sync_error_toast_at = 0.0
        self._sync.connect_sync_error(self._on_sync_error)
        # Refresh the sidebar's "Synced at HH:MM" subtitle when a sync
        # completes — the time was set by _sync_done minutes/seconds
        # before this callback fires, so the subtitle would otherwise
        # remain stale until the next manual auth-sidebar rebuild.
        self._sync.connect_sync_complete(
            lambda _ok: self._refresh_auth_sidebar_subtitle())

        # Auto-collection daily idle scan — fire 30s after launch (UI is
        # idle by then), and re-check every hour. Each tick respects
        # the 24h debounce inside auto_collections.should_scan_now().
        GLib.timeout_add_seconds(30, self._auto_collections_tick, True)
        GLib.timeout_add_seconds(3600, self._auto_collections_tick, False)

        # Live-refresh all visible release posters when the adult
        # warning setting flips (via the dialog checkbox or the
        # preferences toggle) — otherwise blurred posters would persist
        # until tab switch or scroll.
        self._settings.connect(
            'changed::adult-warning-disabled',
            lambda *_: self._refresh_all_adult_blur(),
        )

    def _setup_window_state(self):
        self.set_default_size(
            self._settings.get_int('window-width'),
            self._settings.get_int('window-height'),
        )
        self.connect('close-request', self._on_close_request)

    def _on_close_request(self, _window):
        self._stop_active_player()
        # Flush watch positions to server before closing
        if self._sync and self._sync.is_logged_in():
            self._sync.flush_timecodes()
        size = self.get_default_size()
        self._settings.set_int('window-width', size[0])
        self._settings.set_int('window-height', size[1])

    def _setup_actions(self):
        prefs_action = Gio.SimpleAction.new('preferences', None)
        prefs_action.connect('activate', self._on_preferences)
        self.add_action(prefs_action)

        shortcut_ctrl = Gtk.ShortcutController()
        shortcut_ctrl.set_scope(Gtk.ShortcutScope.MANAGED)
        shortcut = Gtk.Shortcut(
            trigger=Gtk.ShortcutTrigger.parse_string('<Control>f'),
            action=Gtk.CallbackAction.new(
                lambda *_: self._open_search_dialog() or True
            ),
        )
        shortcut_ctrl.add_shortcut(shortcut)
        self.add_controller(shortcut_ctrl)

    def _setup_views(self):
        self._narrow = False
        self._genres_view = None
        self._franchises_view = None
        self._tags_view = None
        self._sidebar_tab_ids = []

        self._catalog_view = CatalogView(client=self._client)
        self._catalog_view.set_on_release_activated(self._show_release_detail)
        self.content_stack.add_named(self._catalog_view, 'catalog')

        for name in ('genres', 'franchises', 'tags'):
            box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            box.append(Adw.Spinner(width_request=48, height_request=48))
            self.content_stack.add_named(box, name)

        self._narrow_tab_buttons = {}
        self._narrow_tab_ids = []
        self._drag_handle_gesture = None

        self._build_sidebar()
        self._build_bottom_bar()
        self._setup_auth_sidebar()

        for key in ('navbar-desktop', 'navbar-mobile',
                    'navbar-sync', 'navbar-sheet-style'):
            self._settings.connect(
                f'changed::{key}', self._on_navbar_settings_changed)

    def _build_sidebar(self):
        """Populate sidebar from GSettings."""
        while True:
            row = self.sidebar_list.get_row_at_index(0)
            if row is None:
                break
            self.sidebar_list.remove(row)

        tab_ids = get_visible_tabs(self._settings, is_narrow=False)
        self._sidebar_tab_ids = tab_ids

        _TAB_LABELS = {
            'catalog': _('Catalog'),
            'genres': _('Genres'),
            'franchises': _('Franchises'),
            'tags': _('Favorites and Tags'),
        }

        for tab_id in tab_ids:
            tab = get_tab(tab_id)
            if not tab:
                continue
            row = Adw.ActionRow(
                title=_TAB_LABELS.get(tab_id, tab['label']),
                icon_name=tab['icon'],
            )
            self.sidebar_list.append(row)

        self.sidebar_list.select_row(self.sidebar_list.get_row_at_index(0))

    def _build_bottom_bar(self):
        """Populate narrow bottom bar tabs and sheet from GSettings."""
        # Clear existing tab buttons
        child = self.narrow_tabs_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.narrow_tabs_box.remove(child)
            child = next_child

        # Clear sheet box
        child = self.narrow_sheet_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.narrow_sheet_box.remove(child)
            child = next_child

        tab_ids = get_visible_tabs(self._settings, is_narrow=True)
        self._narrow_tab_ids = tab_ids
        self._narrow_tab_buttons = {}

        _TAB_LABELS = {
            'catalog': _('Catalog'),
            'genres': _('Genres'),
            'franchises': _('Franchises'),
            'tags': _('Favorites'),
        }

        # Bottom bar: first 3 tabs as buttons
        for tab_id in tab_ids[:3]:
            tab = get_tab(tab_id)
            if not tab:
                continue
            btn = Gtk.Button()
            btn.add_css_class('flat')
            btn.add_css_class('nav-tab')
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=2, halign=Gtk.Align.CENTER,
            )
            box.append(Gtk.Image(icon_name=tab['icon']))
            label = Gtk.Label(label=_TAB_LABELS.get(tab_id, tab['label']))
            label.add_css_class('caption')
            box.append(label)
            btn.set_child(box)
            btn.connect('clicked', self._on_narrow_tab_clicked, tab_id)
            self.narrow_tabs_box.append(btn)
            self._narrow_tab_buttons[tab_id] = btn

        # Always show drag handle (sheet has Preferences + About)
        self.narrow_drag_handle.set_visible(True)

        # Drag handle pill at top of sheet content
        sheet_handle = Gtk.Box(halign=Gtk.Align.CENTER,
                               margin_top=8, margin_bottom=4)
        pill = Gtk.Box(width_request=32, height_request=4,
                       valign=Gtk.Align.CENTER)
        pill.add_css_class('drag-handle-pill')
        sheet_handle.append(pill)
        gesture = Gtk.GestureClick.new()
        gesture.connect(
            'released',
            lambda *_: self.narrow_sheet.set_open(False),
        )
        sheet_handle.add_controller(gesture)
        self.narrow_sheet_box.append(sheet_handle)

        # Sheet content: grid or list style
        sheet_style = self._settings.get_string('navbar-sheet-style')
        if sheet_style == 'grid':
            self._build_sheet_grid(tab_ids, _TAB_LABELS)
        else:
            self._build_sheet_list(tab_ids, _TAB_LABELS)

        # Click on drag handle opens the sheet
        if self._drag_handle_gesture:
            self.narrow_drag_handle.remove_controller(
                self._drag_handle_gesture)
        self._drag_handle_gesture = Gtk.GestureClick.new()
        self._drag_handle_gesture.connect(
            'released',
            lambda *_: self.narrow_sheet.set_open(True),
        )
        self.narrow_drag_handle.add_controller(
            self._drag_handle_gesture)

    def _build_sheet_list(self, tab_ids, labels):
        """Build sheet content as a list of rows."""
        listbox = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        listbox.add_css_class('navigation-sidebar')
        for tab_id in tab_ids:
            tab = get_tab(tab_id)
            if not tab:
                continue
            row = Adw.ActionRow(
                title=labels.get(tab_id, tab['label']),
                icon_name=tab['icon'],
                activatable=True,
            )
            row._tab_id = tab_id
            listbox.append(row)
        listbox.connect('row-activated', self._on_sheet_row_activated)
        self.narrow_sheet_box.append(listbox)

        # Separator + auth row
        self.narrow_sheet_box.append(Gtk.Separator(
            margin_top=4, margin_bottom=4))
        auth_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        auth_list.add_css_class('navigation-sidebar')
        self._narrow_auth_row = Adw.ActionRow(activatable=True)
        self._narrow_auth_avatar = Adw.Avatar(size=24, show_initials=True)
        self._narrow_auth_row.add_prefix(self._narrow_auth_avatar)
        self._narrow_auth_row._action = 'auth'
        self._update_narrow_auth_row()
        auth_list.append(self._narrow_auth_row)
        auth_list.connect('row-activated', self._on_sheet_menu_activated)
        self.narrow_sheet_box.append(auth_list)

        # Separator + app menu items
        self.narrow_sheet_box.append(Gtk.Separator(
            margin_top=4, margin_bottom=4))
        menu_list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        menu_list.add_css_class('navigation-sidebar')
        prefs_row = Adw.ActionRow(
            title=_('Preferences'),
            icon_name='preferences-system-symbolic',
            activatable=True,
        )
        prefs_row._action = 'preferences'
        menu_list.append(prefs_row)
        about_row = Adw.ActionRow(
            title=_('About Kitsune'),
            icon_name='help-about-symbolic',
            activatable=True,
        )
        about_row._action = 'about'
        menu_list.append(about_row)
        menu_list.connect('row-activated', self._on_sheet_menu_activated)
        self.narrow_sheet_box.append(menu_list)

    def _build_sheet_grid(self, tab_ids, labels):
        """Build sheet content as a grid of icon buttons."""
        flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            max_children_per_line=4,
            min_children_per_line=3,
            row_spacing=8,
            column_spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        flow.add_css_class('sheet-grid')
        for tab_id in tab_ids:
            tab = get_tab(tab_id)
            if not tab:
                continue
            btn = Gtk.Button()
            btn.add_css_class('flat')
            btn.add_css_class('sheet-grid-item')
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=4, halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
            )
            box.append(Gtk.Image(icon_name=tab['icon']))
            lbl = Gtk.Label(label=labels.get(tab_id, tab['label']))
            lbl.add_css_class('caption')
            box.append(lbl)
            btn.set_child(box)
            btn.connect('clicked', self._on_sheet_grid_clicked, tab_id)
            flow.append(btn)
        self.narrow_sheet_box.append(flow)

        # Separator + auth button
        self.narrow_sheet_box.append(Gtk.Separator(
            margin_start=12, margin_end=12))
        auth_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            max_children_per_line=4,
            min_children_per_line=3,
            row_spacing=8,
            column_spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        auth_flow.add_css_class('sheet-grid')
        self._narrow_auth_btn = Gtk.Button()
        self._narrow_auth_btn.add_css_class('flat')
        self._narrow_auth_btn.add_css_class('sheet-grid-item')
        auth_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4, halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        # Adw.Avatar is roughly square; cap height to match the
        # neighbouring nav-tab icon pixel-size (24px) so the grid row
        # heights stay aligned across the auth cell and the rest.
        self._narrow_auth_grid_avatar = Adw.Avatar(
            size=24, show_initials=True)
        auth_box.append(self._narrow_auth_grid_avatar)
        self._narrow_auth_label = Gtk.Label(label=_('Login'))
        self._narrow_auth_label.add_css_class('caption')
        auth_box.append(self._narrow_auth_label)
        self._narrow_auth_btn.set_child(auth_box)
        self._narrow_auth_btn.connect(
            'clicked', self._on_sheet_menu_clicked, 'auth')
        auth_flow.append(self._narrow_auth_btn)
        self._update_narrow_auth_grid()
        self.narrow_sheet_box.append(auth_flow)

        # Separator + app menu items
        self.narrow_sheet_box.append(Gtk.Separator(
            margin_start=12, margin_end=12))
        menu_flow = Gtk.FlowBox(
            selection_mode=Gtk.SelectionMode.NONE,
            homogeneous=True,
            max_children_per_line=4,
            min_children_per_line=3,
            row_spacing=8,
            column_spacing=8,
            margin_top=12,
            margin_bottom=12,
            margin_start=12,
            margin_end=12,
        )
        menu_flow.add_css_class('sheet-grid')
        for icon, label, action in (
            ('preferences-system-symbolic', _('Preferences'), 'preferences'),
            ('help-about-symbolic', _('About Kitsune'), 'about'),
        ):
            btn = Gtk.Button()
            btn.add_css_class('flat')
            btn.add_css_class('sheet-grid-item')
            box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=4, halign=Gtk.Align.CENTER,
                valign=Gtk.Align.CENTER,
            )
            box.append(Gtk.Image(icon_name=icon))
            lbl = Gtk.Label(label=label)
            lbl.add_css_class('caption')
            box.append(lbl)
            btn.set_child(box)
            btn.connect('clicked', self._on_sheet_menu_clicked, action)
            menu_flow.append(btn)
        self.narrow_sheet_box.append(menu_flow)

    def _on_narrow_tab_clicked(self, _button, tab_id):
        self._switch_tab(tab_id)

    def _on_sheet_row_activated(self, _listbox, row):
        self.narrow_sheet.set_open(False)
        self._switch_tab(row._tab_id)

    def _on_sheet_grid_clicked(self, _button, tab_id):
        self.narrow_sheet.set_open(False)
        self._switch_tab(tab_id)

    def _on_sheet_menu_activated(self, _listbox, row):
        self.narrow_sheet.set_open(False)
        self._activate_menu_action(row._action)

    def _on_sheet_menu_clicked(self, _button, action):
        self.narrow_sheet.set_open(False)
        self._activate_menu_action(action)

    def _activate_menu_action(self, action):
        if action == 'preferences':
            self._on_preferences(None, None)
        elif action == 'about':
            self.get_application().activate_action('about', None)
        elif action == 'auth':
            if self._session and self._session.is_logged_in():
                self._switch_tab('profile')
            else:
                self._show_auth_dialog()

    def _on_navbar_settings_changed(self, _settings, _key):
        """Rebuild navigation when settings change."""
        self._build_sidebar()
        self._build_bottom_bar()
        self._setup_auth_sidebar()
        tab_ids = get_visible_tabs(self._settings, is_narrow=self._narrow)
        if tab_ids:
            self._switch_tab(tab_ids[0])

    def _create_genres_view(self):
        if self._genres_view:
            return
        from kitsune.ui.genres_view import GenresView
        old = self.content_stack.get_child_by_name('genres')
        if old:
            self.content_stack.remove(old)
        self._genres_view = GenresView(client=self._client)
        self._genres_view.set_on_release_activated(self._show_release_detail)
        self._genres_view.set_on_navigation_changed(self._on_sub_navigation_changed)
        self._genres_view.set_narrow(self._narrow)
        self.content_stack.add_named(self._genres_view, 'genres')

    def _create_franchises_view(self):
        if self._franchises_view:
            return
        from kitsune.ui.franchises_view import FranchisesView
        old = self.content_stack.get_child_by_name('franchises')
        if old:
            self.content_stack.remove(old)
        self._franchises_view = FranchisesView(client=self._client)
        self._franchises_view.set_on_release_activated(self._show_release_detail)
        self._franchises_view.set_on_navigation_changed(self._on_sub_navigation_changed)
        self._franchises_view.set_narrow(self._narrow)
        self.content_stack.add_named(self._franchises_view, 'franchises')

    def _create_tags_view(self):
        if self._tags_view:
            return
        from kitsune.ui.tags_view import TagsView
        old = self.content_stack.get_child_by_name('tags')
        if old:
            self.content_stack.remove(old)
        saved_mode = self._settings.get_string('tags-view-mode')
        self._tags_view = TagsView(client=self._client)
        self._tags_view.set_on_release_activated(self._show_release_detail)
        self._tags_view.set_on_navigation_changed(self._on_sub_navigation_changed)
        self._tags_view.set_on_tags_changed(self._on_tags_bulk_changed)
        self._tags_view.set_narrow(self._narrow)
        # Seed the synced flag from the current session — without this,
        # opening Tags for the first time after login would show
        # builtin tags without cloud badges until the next set_synced
        # call (which only happens on session-state changes).
        self._tags_view.set_synced(
            bool(self._session and self._session.is_logged_in()))
        self._tags_mode_is_list = saved_mode == 'list'
        if self._tags_mode_is_list:
            self._tags_view.toggle_mode()
            self.mode_btn.set_icon_name('net.armatik.Kitsune.view-grid-symbolic')
            self.mode_btn.set_tooltip_text(_('Card view'))
        self.content_stack.add_named(self._tags_view, 'tags')

    # --- Template Callbacks ---

    @Gtk.Template.Callback()
    def on_filter_clicked(self, _button):
        if not self.filter_split.get_sidebar():
            panel = self._catalog_view.get_or_create_filter_panel()
            panel.set_on_close(
                lambda: self.filter_split.set_show_sidebar(False)
            )
            self.filter_split.set_sidebar(panel)
        self.filter_split.set_show_sidebar(
            not self.filter_split.get_show_sidebar()
        )

    @Gtk.Template.Callback()
    def on_search_clicked(self, _button):
        self._open_search_dialog()

    @Gtk.Template.Callback()
    def on_refresh_clicked(self, _button):
        self._catalog_view.refresh()

    @Gtk.Template.Callback()
    def on_back_clicked(self, _button):
        tab = self.content_stack.get_visible_child_name()
        if tab == 'genres' and self._genres_view:
            self._genres_view.go_back()
        elif tab == 'franchises' and self._franchises_view:
            self._franchises_view.go_back()
        elif tab == 'tags' and self._tags_view:
            self._tags_view.go_back()
        self._update_content_header()

    @Gtk.Template.Callback()
    def on_sidebar_row_selected(self, listbox, row):
        if self._suppress_sidebar_callback:
            return
        if not row:
            return
        if hasattr(self, '_auth_row') and row is self._auth_row:
            if self._session and self._session.is_logged_in():
                self._switch_tab('profile')
            else:
                self._show_auth_dialog()
                # Deselect on the listbox that actually holds the row
                # (auth_sidebar_list since the split) so clicking
                # again re-triggers the signal. self.sidebar_list
                # was a stale carryover from the pre-split layout.
                listbox.unselect_row(row)
            return
        index = row.get_index()
        if 0 <= index < len(self._sidebar_tab_ids):
            self._switch_tab(self._sidebar_tab_ids[index])

    @Gtk.Template.Callback()
    def on_mode_toggled(self, btn):
        if self._tags_view:
            self._tags_view.toggle_mode()
            self._tags_mode_is_list = not self._tags_mode_is_list
            mode = 'list' if self._tags_mode_is_list else 'cards'
            self._settings.set_string('tags-view-mode', mode)
            if self._tags_mode_is_list:
                btn.set_icon_name('net.armatik.Kitsune.view-grid-symbolic')
                btn.set_tooltip_text(_('Card view'))
            else:
                btn.set_icon_name('net.armatik.Kitsune.view-list-symbolic')
                btn.set_tooltip_text(_('List view'))

    @Gtk.Template.Callback()
    def on_add_tag_clicked(self, _button):
        from kitsune.ui.create_tag_dialog import show_create_tag_dialog
        show_create_tag_dialog(
            self,
            callback=self._on_header_tag_created,
        )

    def _on_header_tag_created(self, tag):
        if tag and self._tags_view:
            self._tags_view.refresh()

    @Gtk.Template.Callback()
    def on_delete_tag_clicked(self, _button):
        if self._tags_view and self._tags_view.current_tag:
            self._tags_view.delete_current_tag()

    @Gtk.Template.Callback()
    def on_narrow_apply(self, _bp):
        self._narrow = True
        self._catalog_view.set_narrow(True)
        if self._genres_view:
            self._genres_view.set_narrow(True)
        if self._franchises_view:
            self._franchises_view.set_narrow(True)
        if self._tags_view:
            self._tags_view.set_narrow(True)
        if self._profile_view:
            self._profile_view.set_narrow(True)
        self._update_narrow_header_for_profile()

    @Gtk.Template.Callback()
    def on_narrow_unapply(self, _bp):
        self._narrow = False
        self._catalog_view.set_narrow(False)
        if self._genres_view:
            self._genres_view.set_narrow(False)
        if self._franchises_view:
            self._franchises_view.set_narrow(False)
        if self._tags_view:
            self._tags_view.set_narrow(False)
        if self._profile_view:
            self._profile_view.set_narrow(False)
        self._update_narrow_header_for_profile()

    # --- Internal Methods ---

    def set_pull_refresh_header_elevated(self, active: bool):
        """Toggle the elevation drop-shadow on the narrow headerbar
        during catalog pull-refresh. Only fires when in narrow mode;
        wide-mode headerbar is left alone. CSS transition smooths the
        appearance / disappearance to match the revealer's slide-down.
        """
        if active and self._narrow:
            self.narrow_header.add_css_class('pull-refresh-elevated')
        else:
            self.narrow_header.remove_css_class('pull-refresh-elevated')

    def _update_narrow_header_for_profile(self):
        # Profile in narrow mode: hero stretches edge-to-edge under a
        # transparent headerbar. Any other case (wide mode, or any other
        # tab while narrow) gets the standard opaque headerbar back.
        on_profile_narrow = (
            self._narrow
            and self.content_stack.get_visible_child_name() == 'profile'
        )
        if on_profile_narrow:
            self.narrow_header.add_css_class('flat')
            self.narrow_toolbar.set_extend_content_to_top_edge(True)
        else:
            self.narrow_header.remove_css_class('flat')
            self.narrow_toolbar.set_extend_content_to_top_edge(False)

    def _switch_tab(self, name: str):
        self.filter_split.set_show_sidebar(False)
        if self._genres_view and self._genres_view.in_releases:
            self._genres_view.go_back()
        if self._franchises_view and self._franchises_view.in_releases:
            self._franchises_view.go_back()
        if self._tags_view and self._tags_view.in_releases:
            self._tags_view.go_back()
        if name == 'genres':
            self._create_genres_view()
        elif name == 'franchises':
            self._create_franchises_view()
        elif name == 'tags':
            self._create_tags_view()
            if self._tags_view:
                self._tags_view.refresh()
        elif name == 'profile':
            if not self._profile_view:
                self._create_profile_view()
            else:
                self._profile_view.refresh_hero()
                self._profile_view.refresh_counts()
        self.content_stack.set_visible_child_name(name)
        self._sync_sidebar_selection(name)
        self._update_content_header()
        self._update_nav_tabs(name)
        self._update_narrow_header_for_profile()

    def _sync_sidebar_selection(self, name: str):
        """Mirror the active content tab into the sidebar listbox.

        Without this, programmatic tab changes (post-login _switch_tab,
        narrow bottom-bar clicks, content-stack updates from anywhere)
        leave the sidebar showing whatever was selected before. After
        login the user sees Profile content but Catalog highlighted in
        the sidebar — confusing.

        The row-selected handler is suppressed via the
        `_suppress_sidebar_callback` flag so this programmatic update
        does not recurse back into _switch_tab.
        """
        target_list = None
        target_row = None
        if name == 'profile':
            target_row = getattr(self, '_auth_row', None)
            target_list = self.auth_sidebar_list
        else:
            try:
                idx = self._sidebar_tab_ids.index(name)
            except ValueError:
                pass
            else:
                target_row = self.sidebar_list.get_row_at_index(idx)
                target_list = self.sidebar_list
        if target_row is None or target_list is None:
            return
        self._suppress_sidebar_callback = True
        try:
            # Single-select each list independently, then deselect the
            # other list so only one row appears active at a time
            # across the whole sidebar.
            target_list.select_row(target_row)
            other = (self.auth_sidebar_list if target_list is self.sidebar_list
                     else self.sidebar_list)
            other.unselect_all()
        finally:
            self._suppress_sidebar_callback = False

    def _update_nav_tabs(self, active: str):
        for tab_id, btn in self._narrow_tab_buttons.items():
            if tab_id == active:
                btn.add_css_class('nav-tab-active')
            else:
                btn.remove_css_class('nav-tab-active')

    def _update_content_header(self):
        tab = self.content_stack.get_visible_child_name()
        show_back = False
        titles = {
            'catalog': _('Catalog'),
            'genres': _('Genres'),
            'franchises': _('Franchises'),
            'tags': _('Favorites and Tags'),
            'profile': _('Profile'),
        }
        title = titles.get(tab, '')

        if tab == 'genres' and self._genres_view and self._genres_view.in_releases:
            title = self._genres_view.current_genre_name
            show_back = True
        elif tab == 'franchises' and self._franchises_view and self._franchises_view.in_releases:
            title = self._franchises_view.current_franchise_name
            show_back = True
        elif tab == 'tags' and self._tags_view and self._tags_view.in_releases:
            title = self._tags_view.current_tag_name
            show_back = True

        show_filter = (tab == 'catalog')
        show_refresh = (tab == 'catalog')
        show_tags_controls = (tab == 'tags' and not show_back)
        show_delete_tag = (
            tab == 'tags' and show_back
            and self._tags_view and self._tags_view.current_tag
            and not self._tags_view.current_tag.get('builtin')
        )

        self.wide_content_title.set_title(title)
        self.back_btn.set_visible(show_back)
        self.narrow_back_btn.set_visible(show_back)
        self.filter_btn.set_visible(show_filter)
        self.refresh_btn.set_visible(show_refresh)
        self.mode_btn.set_visible(show_tags_controls)
        self.add_tag_btn.set_visible(show_tags_controls)
        self.delete_tag_btn.set_visible(show_delete_tag)

    def _on_sub_navigation_changed(self):
        self._update_content_header()

    def _on_preferences(self, _action, _param):
        from kitsune.ui.preferences_window import PreferencesWindow
        prefs = PreferencesWindow()
        prefs.present(self)

    def _go_home(self):
        self.nav_view.pop_to_tag('main')

    def _show_release_detail(self, release):
        # 18+ gate. The warning is suppressible — once the user has
        # toggled "don't show again" we open the page directly. Reading
        # the setting on every navigation avoids stale state from a
        # toggle made earlier in this session.
        if release.is_adult and not self._settings.get_boolean(
                'adult-warning-disabled'):
            self._show_adult_warning_then(release)
            return
        self._do_show_release_detail(release)

    def _show_adult_warning_then(self, release):
        dialog = Adw.AlertDialog.new(
            _('Adult content'),
            _('This title is marked 18+ and may contain explicit material. Continue?'),
        )
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('continue', _('Continue'))
        dialog.set_response_appearance(
            'continue', Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response('continue')
        dialog.set_close_response('cancel')

        check = Gtk.CheckButton(label=_("Don't show again"))
        check.set_margin_top(8)
        dialog.set_extra_child(check)

        def on_response(_dialog, response):
            if response != 'continue':
                return
            if check.get_active():
                self._settings.set_boolean('adult-warning-disabled', True)
            self._do_show_release_detail(release)

        dialog.connect('response', on_response)
        dialog.present(self)

    def _do_show_release_detail(self, release):
        from kitsune.ui.release_view import ReleaseView
        view = ReleaseView(release=release, client=self._client,
                           sync_manager=self._sync)
        view.set_on_episode_play(self._play_episode)
        view.set_on_genre_clicked(self._navigate_to_genre)
        view.set_on_tag_clicked(self._navigate_to_tag)
        view.set_on_tags_changed(self._on_release_tags_changed)
        view.set_on_home_clicked(self._go_home)
        at_main = self.nav_view.get_visible_page().get_tag() == 'main'
        view.home_btn.set_visible(not at_main)
        self.nav_view.push(view)

    def _on_release_tags_changed(self, release_id):
        """Called when tags change on a release detail page."""
        self._refresh_visible_cards(release_id)
        if self._tags_view:
            self._tags_view.refresh()

    def _on_tags_bulk_changed(self, release_ids):
        """Called when a tag is deleted, affecting multiple releases."""
        for rid in release_ids:
            self._refresh_visible_cards(rid)

    def _refresh_visible_cards(self, release_id):
        """Find and refresh tag badges on visible ReleaseCard widgets."""
        from kitsune.ui.widgets.release_card import ReleaseCard
        flowboxes = []
        if self._catalog_view:
            flowboxes.append(self._catalog_view.flowbox)
        # Genre/franchise release sub-views contain ReleaseCards
        for view in (self._genres_view, self._franchises_view):
            if view and view._releases_view and hasattr(view._releases_view, '_grid'):
                flowboxes.append(view._releases_view._grid.flowbox)
        # Tags release sub-view
        if self._tags_view and self._tags_view.in_releases:
            releases = self._tags_view._nav_stack.get_child_by_name('releases')
            if releases and hasattr(releases, '_grid'):
                flowboxes.append(releases._grid.flowbox)
        for flowbox in flowboxes:
            child = flowbox.get_first_child()
            while child:
                if isinstance(child, ReleaseCard) and child.release.id == release_id:
                    child.refresh_tag_badges()
                child = child.get_next_sibling()

    def _refresh_all_adult_blur(self):
        """Walk every ReleaseCard in every visible flowbox and re-apply
        (or strip) the adult-blur class based on the current setting.
        Also re-renders the tags view since it builds its own mini-cards
        outside the ReleaseCard widget tree.
        """
        from kitsune.ui.widgets.release_card import ReleaseCard
        flowboxes = []
        if self._catalog_view:
            flowboxes.append(self._catalog_view.flowbox)
        for view in (self._genres_view, self._franchises_view):
            if view and view._releases_view and hasattr(view._releases_view, '_grid'):
                flowboxes.append(view._releases_view._grid.flowbox)
        if self._tags_view and self._tags_view.in_releases:
            releases = self._tags_view._nav_stack.get_child_by_name('releases')
            if releases and hasattr(releases, '_grid'):
                flowboxes.append(releases._grid.flowbox)
        for flowbox in flowboxes:
            child = flowbox.get_first_child()
            while child:
                if isinstance(child, ReleaseCard):
                    child.refresh_adult_blur()
                child = child.get_next_sibling()
        if self._tags_view:
            self._tags_view.refresh()

    def _make_nav_header(self):
        header = Adw.HeaderBar()
        home_btn = Gtk.Button(
            icon_name='net.armatik.Kitsune.home-symbolic',
            tooltip_text=_('Home'),
        )
        home_btn.connect('clicked', lambda *_: self._go_home())
        header.pack_start(home_btn)
        return header

    def _navigate_to_genre(self, genre):
        from kitsune.ui.genre_releases_view import GenreReleasesView
        releases_view = GenreReleasesView(
            genre=genre, client=self._client,
        )
        releases_view.set_on_release_activated(self._show_release_detail)
        releases_view.set_narrow(self._narrow)
        page = Adw.NavigationPage(
            title=genre.name,
            child=Adw.ToolbarView(
                top_bar_style=Adw.ToolbarStyle.FLAT,
                content=releases_view,
            ),
        )
        page.get_child().add_top_bar(self._make_nav_header())
        self.nav_view.push(page)

    def _open_search_dialog(self):
        from kitsune.ui.search_dialog import SearchDialog
        if not hasattr(self, '_search_dialog') or self._search_dialog is None:
            self._search_dialog = SearchDialog(client=self._client)
            self._search_dialog.set_on_release_activated(self._show_release_detail)
            self._search_dialog.set_on_episode_play(self._play_episode)
            self._search_dialog.set_on_genre_activated(self._navigate_to_genre)
            self._search_dialog.set_on_franchise_activated(self._navigate_to_franchise)
            self._search_dialog.set_on_tag_activated(self._navigate_to_tag)
        self._search_dialog.present(self)
        self._search_dialog.search_entry.grab_focus()

    def _navigate_to_franchise(self, franchise):
        from kitsune.ui.franchise_releases_view import FranchiseReleasesView
        releases_view = FranchiseReleasesView(
            franchise=franchise, client=self._client,
        )
        releases_view.set_on_release_activated(self._show_release_detail)
        releases_view.set_narrow(self._narrow)
        page = Adw.NavigationPage(
            title=franchise.name,
            child=Adw.ToolbarView(
                top_bar_style=Adw.ToolbarStyle.FLAT,
                content=releases_view,
            ),
        )
        page.get_child().add_top_bar(self._make_nav_header())
        self.nav_view.push(page)

    def _navigate_to_tag(self, tag):
        from kitsune.ui.tag_releases_view import TagReleasesView
        releases_view = TagReleasesView(
            tag=tag, client=self._client,
        )
        releases_view.set_on_release_activated(self._show_release_detail)
        releases_view.set_narrow(self._narrow)
        page = Adw.NavigationPage(
            title=tag['name'],
            child=Adw.ToolbarView(
                top_bar_style=Adw.ToolbarStyle.FLAT,
                content=releases_view,
            ),
        )
        page.get_child().add_top_bar(self._make_nav_header())
        self.nav_view.push(page)

    # --- Auth integration ---

    def _setup_auth_sidebar(self):
        """Pin the login/profile row to the bottom of the sidebar.

        Uses a separate Gtk.ListBox (`auth_sidebar_list`) attached to
        the sidebar's Adw.ToolbarView bottom slot so the row stays
        anchored to the foot of the pane regardless of how many tabs
        the user has visible. Selection state is kept in sync across
        the two listboxes via `_sync_sidebar_selection`.
        """
        if hasattr(self, '_auth_row') and self._auth_row.get_parent():
            self._auth_row.get_parent().remove(self._auth_row)
        self._auth_row = Adw.ActionRow()
        # Adw.Avatar as a prefix gives us the server-side avatar image
        # when logged in (loaded via image_cache) and falls back to
        # nickname initials otherwise. set_icon_name on ActionRow would
        # paint the symbolic on top of the avatar, so we use add_prefix
        # exclusively and never touch the icon-name property.
        self._auth_avatar = Adw.Avatar(size=28, show_initials=True)
        self._auth_row.add_prefix(self._auth_avatar)
        self._auth_row.add_css_class('auth-row-bold')
        self.auth_sidebar_list.append(self._auth_row)

        self._update_auth_sidebar()

    def _apply_user_to_avatar(self, avatar_widget, user):
        """Mirror the auth state into an Adw.Avatar (sidebar or sheet).

        On login: set the nickname as the initials fallback and kick an
        async image load — the custom image fades in when the network
        request completes, falling back to initials on failure. On
        logout: clear both so the next user starts fresh.
        """
        if user:
            avatar_widget.set_text(user.nickname or '')
            if user.avatar:
                from kitsune.ui.image_cache import load_image
                load_image(user.avatar, lambda tex, err, a=avatar_widget:
                           a.set_custom_image(tex) if tex else None,
                           category='avatars')
            else:
                avatar_widget.set_custom_image(None)
        else:
            avatar_widget.set_text('')
            avatar_widget.set_custom_image(None)

    def _update_auth_sidebar(self):
        if self._session and self._session.is_logged_in():
            user = self._session.get_user()
            nick = user.nickname if user else '...'
            self._auth_row.set_title(nick)
            self._auth_row.set_subtitle(self._format_last_sync())
            self._apply_user_to_avatar(self._auth_avatar, user)
        else:
            self._auth_row.set_title(_('Login'))
            self._auth_row.set_subtitle('')
            self._apply_user_to_avatar(self._auth_avatar, None)
        self._update_narrow_auth_row()
        self._update_narrow_auth_grid()

    def _format_last_sync(self) -> str:
        """Format the last sync timestamp for the sidebar subtitle.

        Shows a short time-of-day when the last sync ran today, full
        date+time when the last sync was on a prior day. Falls back to
        'Not synced yet' when the session is new — both before the
        first pull and immediately after login on a fresh install.
        """
        ts = self._sync.get_last_sync_time() if self._sync else None
        if not ts:
            return _('Not synced yet')
        import datetime
        dt = datetime.datetime.fromtimestamp(ts)
        today = datetime.date.today()
        if dt.date() == today:
            return _('Synced at %s') % dt.strftime('%H:%M')
        return _('Synced %s') % dt.strftime('%b %d %H:%M')

    def _refresh_auth_sidebar_subtitle(self):
        """Update only the sync-time subtitle, without re-fetching the
        avatar — fired on every sync_complete tick so it can stay
        accurate without burning HTTP on the avatar URL each time."""
        if self._session and self._session.is_logged_in():
            self._auth_row.set_subtitle(self._format_last_sync())

    def _update_narrow_auth_row(self):
        """Update the narrow sheet list auth row."""
        if not hasattr(self, '_narrow_auth_avatar'):
            return
        if self._session and self._session.is_logged_in():
            user = self._session.get_user()
            nick = user.nickname if user else '...'
            self._narrow_auth_row.set_title(nick)
            self._apply_user_to_avatar(self._narrow_auth_avatar, user)
        else:
            self._narrow_auth_row.set_title(_('Login'))
            self._apply_user_to_avatar(self._narrow_auth_avatar, None)

    def _update_narrow_auth_grid(self):
        """Update the narrow sheet grid auth button."""
        if not hasattr(self, '_narrow_auth_label'):
            return
        if self._session and self._session.is_logged_in():
            user = self._session.get_user()
            nick = user.nickname if user else '...'
            self._narrow_auth_label.set_label(nick)
            self._apply_user_to_avatar(self._narrow_auth_grid_avatar, user)
        else:
            self._narrow_auth_label.set_label(_('Login'))
            self._apply_user_to_avatar(self._narrow_auth_grid_avatar, None)

    def _create_profile_view(self):
        from kitsune.ui.profile_view import ProfileView
        self._profile_view = ProfileView(
            session_manager=self._session,
            on_navigate_tag=self._navigate_to_tag,
            sync_manager=self._sync,
        )
        self.content_stack.add_named(self._profile_view, 'profile')
        if self._narrow:
            self._profile_view.set_narrow(True)
        user = self._session.get_user() if self._session else None
        if user:
            self._profile_view.update_profile(user)
        self._profile_view.refresh_counts()

    def _on_logged_in(self):
        if self._session:
            self._session.fetch_profile(
                lambda user, err: self._on_profile_loaded(user))
        self._update_auth_sidebar()
        self._switch_tab('profile')
        # Skip the merge dialog on expired-session re-login: the auth_dialog
        # _finalize_login flow will call session.clear_expired(), which emits
        # session-restored and resumes sync via SyncManager.resume_after_expired_session.
        # Showing the merge dialog on top of that is confusing — the user just
        # wants to continue where they left off, not pick a merge strategy.
        if self._session and self._session.is_expired():
            return
        self._show_merge_dialog()

    def _show_merge_dialog(self):
        """Show merge strategy dialog after first login."""
        from kitsune.storage.sync_manager import MergeStrategy

        # Fetch server counts to show in dialog
        def on_counts(counts, error):
            if error:
                # Can't reach server — just do local
                self._sync.initial_sync(
                    self._on_sync_complete, MergeStrategy.MERGE)
                return

            server_favs = counts.get('favorites', 0)
            server_cols = sum(counts.get('collections', {}).values())
            local_favs = len(tags_store.get_release_ids_for_tag('favorites'))
            local_cols = sum(
                len(tags_store.get_release_ids_for_tag(t))
                for t in ('watching', 'watched', 'planned',
                          'postponed', 'abandoned'))

            # If no differences — just merge silently
            if server_favs == 0 and server_cols == 0 and \
               local_favs == 0 and local_cols == 0:
                self._sync.initial_sync(
                    self._on_sync_complete, MergeStrategy.MERGE)
                return

            body = (
                f'{_("Local")}: {local_favs} {_("favorites")}, '
                f'{local_cols} {_("in collections")}\n'
                f'{_("Server")}: {server_favs} {_("favorites")}, '
                f'{server_cols} {_("in collections")}'
            )

            dialog = Adw.AlertDialog(
                heading=_('Sync data'),
                body=body,
            )
            dialog.add_response('merge', _('Merge'))
            dialog.add_response('local', _('Keep local'))
            dialog.add_response('server', _('Keep server'))
            dialog.set_default_response('merge')
            dialog.set_response_appearance(
                'merge', Adw.ResponseAppearance.SUGGESTED)

            def on_response(d, response):
                strategies = {
                    'merge': MergeStrategy.MERGE,
                    'local': MergeStrategy.PREFER_LOCAL,
                    'server': MergeStrategy.PREFER_SERVER,
                }
                strategy = strategies.get(response, MergeStrategy.MERGE)
                self._sync.initial_sync(
                    self._on_sync_complete, strategy)

            dialog.connect('response', on_response)
            dialog.present(self)

        self._sync.fetch_server_counts(on_counts)

    def _on_sync_complete(self, ok, error):
        if self._profile_view:
            import datetime
            now = datetime.datetime.now().strftime('%H:%M')
            self._profile_view.set_sync_time(now)
            self._profile_view.refresh_counts()
        # After a successful pull, local tag state mirrors the server.
        # Refresh views that visualise that state — the tags page (for
        # counts + cloud badges) and every visible release card (for
        # tag badges that newly-added releases would carry).
        if ok:
            self._refresh_synced_views(synced=True)
        # Start periodic sync if not already running
        if ok and not self._sync_timer_id:
            self._start_periodic_sync()
        if not ok and error and error != 'already_syncing':
            import logging
            logging.getLogger('kitsune.sync').warning(
                'Sync failed: %s', error)

    def _refresh_synced_views(self, synced: bool):
        """Re-render views whose contents change with auth state.

        Two visible side-effects need to follow login / logout / sync
        completion:
          - the Tags page must re-render so builtin tags carry the
            cloud badge (or drop it) and counts match the just-changed
            local state;
          - release cards in catalog / genres / franchises / tag
            sub-views must refresh their tag badges because the tag
            membership of every release may have just changed via the
            server pull or the force_logout_cleanup wipe.
        """
        if self._tags_view:
            self._tags_view.set_synced(synced)
            self._tags_view.refresh()
        self._refresh_all_card_tag_badges()

    def _refresh_all_card_tag_badges(self):
        """Walk every visible release flowbox and re-render the tag
        badge pills on each card. Mirrors `_refresh_all_adult_blur`'s
        traversal so the set of containers stays in sync."""
        from kitsune.ui.widgets.release_card import ReleaseCard
        flowboxes = []
        if self._catalog_view:
            flowboxes.append(self._catalog_view.flowbox)
        for view in (self._genres_view, self._franchises_view):
            if view and view._releases_view and hasattr(view._releases_view, '_grid'):
                flowboxes.append(view._releases_view._grid.flowbox)
        if self._tags_view and self._tags_view.in_releases:
            releases = self._tags_view._nav_stack.get_child_by_name('releases')
            if releases and hasattr(releases, '_grid'):
                flowboxes.append(releases._grid.flowbox)
        for flowbox in flowboxes:
            child = flowbox.get_first_child()
            while child:
                if isinstance(child, ReleaseCard):
                    child.refresh_tag_badges()
                child = child.get_next_sibling()

    def _start_periodic_sync(self):
        """Pull from server every 5 minutes."""
        self._stop_periodic_sync()
        self._sync_timer_id = GLib.timeout_add(
            5 * 60 * 1000, self._periodic_sync)

    def _stop_periodic_sync(self):
        if self._sync_timer_id:
            GLib.source_remove(self._sync_timer_id)
            self._sync_timer_id = 0

    def _periodic_sync(self):
        if self._session and self._session.is_logged_in():
            self._sync.pull_from_server(self._on_sync_complete)
            return GLib.SOURCE_CONTINUE
        self._sync_timer_id = 0
        return GLib.SOURCE_REMOVE

    def _on_logged_out(self):
        self._stop_periodic_sync()
        self._update_auth_sidebar()
        if self._profile_view:
            self._profile_view.update_profile(None)
        if self.content_stack.get_visible_child_name() == 'profile':
            self._switch_tab('catalog')
        # SessionManager.logout has already wiped favorites,
        # collections, watch positions and the pending queue. Mirror
        # that wipe into the visible UI: cloud badges off, tag pills
        # on cards refreshed so the just-removed tags disappear.
        self._refresh_synced_views(synced=False)
        # Forget the user id so the next login starts clean and an
        # interrupted re-login (token typed, profile fetch fails) cannot
        # accidentally reuse the previous account's identity.
        self._settings.set_int('last-user-id', 0)
        self._sync.set_user_id(0)

    def _on_session_validated(self, valid, error):
        if valid:
            if self._session:
                self._session.fetch_profile(
                    lambda user, err: self._on_profile_loaded(user))
            self._update_auth_sidebar()
            # Quiet pull on app restart — no dialog
            self._sync.pull_from_server(self._on_sync_complete)

    def _on_profile_loaded(self, user):
        self._update_auth_sidebar()
        if self._profile_view:
            self._profile_view.update_profile(user)
        # Tag the pending queue with the authenticated user id and
        # persist it so that subsequent app launches (and any 401
        # re-login flow that loses self._session._user) can still
        # identify which account owns the cached data.
        if user and getattr(user, 'id', None):
            self._sync.set_user_id(user.id)
            if self._settings.get_int('last-user-id') != user.id:
                self._settings.set_int('last-user-id', user.id)

    def _show_auth_dialog(self):
        if not self._session:
            return
        # Capture the currently active sidebar row so we can return
        # focus to it once the dialog closes. Without this, dismissing
        # the auth dialog leaves keyboard focus on the auth_row in the
        # bottom listbox, even though the visible content tab hasn't
        # changed — confusing for keyboard users and for screen
        # readers, and aesthetically inconsistent with the highlighted
        # tab above.
        prev_row = self.sidebar_list.get_selected_row()
        dialog = AuthDialog(self._session, sync_manager=self._sync)
        if prev_row is not None:
            dialog.connect(
                'closed',
                lambda *_: GLib.idle_add(prev_row.grab_focus),
            )
        dialog.present(self)

    def _on_network_error(self):
        self.offline_banner.set_revealed(True)

    def _on_network_ok(self):
        self.offline_banner.set_revealed(False)

    def _on_sync_error(self, op_kind, release_id, error):
        """Show a throttled toast when a write-through op fails.

        Throttle at 5 seconds so a burst of failures (e.g. network
        outage) does not stack multiple toasts. The profile indicator is
        the persistent channel — toast is just the shout-out for the
        first one.
        """
        import time
        now = time.monotonic()
        if now - self._last_sync_error_toast_at < 5.0:
            return
        self._last_sync_error_toast_at = now
        toast = Adw.Toast.new(_('Failed to sync your change with the server'))
        toast.set_timeout(4)
        self.toast_overlay.add_toast(toast)

    def _auto_collections_tick(self, is_initial: bool):
        """Daily idle-scan tick. The 24h debounce lives inside
        auto_collections.should_scan_now() — we poll periodically and
        let it decide whether enough time has passed. Idle-driven moves
        are auto-applied (no toast) when the user has the feature on.
        """
        from kitsune.storage import auto_collections
        if not self._settings.get_boolean('auto-collections-idle-scan'):
            return GLib.SOURCE_REMOVE if is_initial else GLib.SOURCE_CONTINUE
        if auto_collections.should_scan_now():
            actions = auto_collections.scan_all()
            auto_collections.record_scan_time()
            for action in actions:
                if action.type == 'auto':
                    auto_collections.apply_action(action, self._sync)
                    log.info(
                        'auto-collection idle %s release=%d → %s',
                        action.reason, action.release_id, action.to_tag,
                    )
        # Initial 30s tick is single-shot; the periodic 1h tick repeats.
        return GLib.SOURCE_REMOVE if is_initial else GLib.SOURCE_CONTINUE

    def show_collection_suggestion(self, action):
        """Surface a suggest-type CollectionAction as a Move toast."""
        title = self._format_collection_suggestion(action)
        toast = Adw.Toast.new(title)
        toast.set_button_label(_('Move'))
        toast.set_timeout(15)
        toast.connect('button-clicked',
                      self._on_collection_toast_clicked, action)
        self.toast_overlay.add_toast(toast)

    def _format_collection_suggestion(self, action):
        target_names = {
            'watching': _('Watching'),
            'watched': _('Watched'),
            'planned': _('Planned'),
            'postponed': _('Paused'),
            'abandoned': _('Abandoned'),
        }
        target = target_names.get(action.to_tag, action.to_tag)
        title = self._lookup_release_name(action.release_id)
        prefix = f'«{title}» — ' if title else ''
        return _('{prefix}move to {target}?').format(
            prefix=prefix, target=target)

    def _lookup_release_name(self, release_id):
        try:
            from kitsune.storage import release_cache
            data = release_cache.get(release_id)
            if data and isinstance(data.get('name'), dict):
                return data['name'].get('main') or None
        except Exception:
            return None
        return None

    def _on_collection_toast_clicked(self, _toast, action):
        if action.from_tag:
            self._sync.remove_from_tag_synced(action.from_tag, action.release_id)
        self._sync.add_to_tag_synced(action.to_tag, action.release_id)

    @Gtk.Template.Callback()
    def on_retry(self, _banner):
        tab = self.content_stack.get_visible_child_name()
        if tab == 'catalog':
            self._catalog_view.retry()
        elif tab == 'genres' and self._genres_view:
            self._genres_view.retry()
        elif tab == 'franchises' and self._franchises_view:
            self._franchises_view.retry()
        elif tab == 'tags' and self._tags_view:
            self._tags_view.refresh()

    def _on_nav_popped(self, _nav_view, page):
        self._stop_active_player()
        # Reopen search dialog if it was closed by navigating to a result
        if (hasattr(self, '_search_dialog') and self._search_dialog
                and self._search_dialog._closed_by_navigation
                and self.nav_view.get_visible_page() == self.nav_view.get_navigation_stack().get_item(0)):
            self._search_dialog._closed_by_navigation = False
            self._search_dialog.present(self)
            self._search_dialog.search_entry.grab_focus()

    def _stop_active_player(self):
        if self._active_player:
            player = self._active_player
            self._active_player = None
            player.cleanup()

    def _on_session_expired_show_banner(self):
        self.session_expired_banner.set_revealed(True)

    def _on_session_restored_hide_banner(self):
        self.session_expired_banner.set_revealed(False)

    def _on_session_logged_out_hide_banner(self):
        self.session_expired_banner.set_revealed(False)

    @Gtk.Template.Callback()
    def on_session_banner_login(self, _banner):
        """button-clicked handler for session_expired_banner (see window.blp)."""
        from kitsune.ui.auth_dialog import AuthDialog
        dialog = AuthDialog(self._session, sync_manager=self._sync)
        dialog.present(self)

    def _play_episode(self, release, episode):
        from kitsune.ui.player_view import PlayerView
        view = PlayerView(release=release, episode=episode,
                          sync_manager=self._sync)
        self._active_player = view._player
        self.nav_view.push(view)
