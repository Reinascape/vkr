# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune import tags_store
from kitsune.ui import register_css
from kitsune.ui.widgets.tag_card import create_color_circle

_POPOVER_CSS = '.tag-popover-emoji { font-size: 22px; }'


class TagPopover(Gtk.Popover):

    def __init__(self, release_id: int, on_changed=None,
                 sync_manager=None, **kwargs):
        super().__init__(**kwargs)
        register_css(_POPOVER_CSS)
        self._release_id = release_id
        self._on_changed = on_changed
        self._sync = sync_manager
        self.set_has_arrow(True)

        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4, margin_top=8, margin_bottom=8,
            margin_start=8, margin_end=8,
            width_request=240,
        )

        # Search + add button row
        search_row = Gtk.Box(spacing=4)
        self._search = Gtk.SearchEntry(
            placeholder_text=_('Search tags…'),
            hexpand=True,
        )
        self._search.connect('search-changed', self._on_search_changed)
        search_row.append(self._search)

        add_btn = Gtk.Button(
            icon_name='list-add-symbolic',
            css_classes=['suggested-action'],
            valign=Gtk.Align.CENTER,
        )
        add_btn.connect('clicked', self._on_create_clicked)
        search_row.append(add_btn)
        box.append(search_row)

        self._list = Gtk.ListBox(
            selection_mode=Gtk.SelectionMode.NONE,
            css_classes=['boxed-list'],
            margin_top=4,
        )
        box.append(self._list)

        # "Not found" message
        self._not_found = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4, margin_top=12, margin_bottom=8,
            visible=False,
        )
        self._not_found.append(Gtk.Label(
            label=_('Tag not found'),
            css_classes=['dim-label'],
        ))
        self._not_found.append(Gtk.Label(
            label=_('Press + to create it'),
            css_classes=['dim-label', 'caption'],
        ))
        box.append(self._not_found)

        self.set_child(box)
        self._populate()

    def _populate(self):
        while row := self._list.get_first_child():
            self._list.remove(row)

        tags = tags_store.get_all_tags()
        release_tag_ids = {
            t['id'] for t in tags_store.get_tags_for_release(self._release_id)
        }

        for tag in tags:
            row = Adw.ActionRow(title=tags_store.display_name(tag))
            row._tag = tag

            if tag['icon_type'] == 'emoji':
                row.add_prefix(Gtk.Label(
                    label=tag['icon_value'],
                    css_classes=['tag-popover-emoji'],
                ))
            elif tag['icon_type'] == 'symbolic':
                image = Gtk.Image.new_from_icon_name(tag['icon_value'])
                image.set_pixel_size(20)
                if tag.get('color'):
                    css = Gtk.CssProvider()
                    css.load_from_string(
                        f"image {{ color: {tag['color']}; }}"
                    )
                    image.get_style_context().add_provider(
                        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
                row.add_prefix(image)
            else:
                row.add_prefix(create_color_circle(tag['icon_value'], 22))

            check = Gtk.CheckButton(
                active=tag['id'] in release_tag_ids,
                valign=Gtk.Align.CENTER,
            )
            check._tag_id = tag['id']
            check.connect('toggled', self._on_tag_toggled)
            row.add_suffix(check)
            row.set_activatable_widget(check)

            self._list.append(row)

    _COLLECTION_TAGS = frozenset(
        ('watching', 'watched', 'planned', 'postponed', 'abandoned'))

    @staticmethod
    def _collection_name(tag_id):
        # gettext lookup deferred to call time so the locale that's
        # active when the toast fires wins, not the one at module import.
        return {
            'watching':  _('Watching'),
            'watched':   _('Watched'),
            'planned':   _('Planned'),
            'postponed': _('Paused'),
            'abandoned': _('Abandoned'),
        }.get(tag_id, tag_id)

    def _current_collection(self):
        for tag in tags_store.get_tags_for_release(self._release_id):
            if tag['id'] in self._COLLECTION_TAGS:
                return tag['id']
        return None

    def _toast_moved(self, from_tag):
        name = self._collection_name(from_tag)
        toast = Adw.Toast.new(_('Removed from "%s"') % name)
        toast.set_timeout(4)
        root = self.get_root()
        if root is not None and hasattr(root, 'toast_overlay'):
            root.toast_overlay.add_toast(toast)

    def _on_tag_toggled(self, check):
        tag_id = check._tag_id
        activating = check.get_active()
        # Server collections (watching/watched/planned/postponed/abandoned)
        # are mutually exclusive — adding to a second one auto-evicts
        # the first server-side. Mirror that locally and surface a
        # toast so the user understands why the previous tag's badge
        # disappeared.
        if activating and tag_id in self._COLLECTION_TAGS:
            prev = self._current_collection()
            if prev and prev != tag_id:
                if self._sync:
                    self._sync.move_collection(
                        self._release_id, prev, tag_id)
                else:
                    tags_store.remove_release(prev, self._release_id)
                    tags_store.add_release(tag_id, self._release_id)
                self._toast_moved(prev)
                self._populate()
                if self._on_changed:
                    self._on_changed()
                return
        if self._sync and self._is_synced_tag(tag_id):
            if activating:
                self._sync.add_to_tag_synced(tag_id, self._release_id)
            else:
                self._sync.remove_from_tag_synced(tag_id, self._release_id)
        else:
            if activating:
                tags_store.add_release(tag_id, self._release_id)
            else:
                tags_store.remove_release(tag_id, self._release_id)
        if self._on_changed:
            self._on_changed()

    @staticmethod
    def _is_synced_tag(tag_id):
        from kitsune.storage.sync_manager import SYNCED_TAGS
        return tag_id in SYNCED_TAGS

    def _on_search_changed(self, entry):
        text = entry.get_text().lower()
        any_visible = False
        row = self._list.get_first_child()
        while row:
            if hasattr(row, '_tag'):
                visible = not text or text in row._tag['name'].lower()
                row.set_visible(visible)
                if visible:
                    any_visible = True
            row = row.get_next_sibling()
        self._not_found.set_visible(bool(text) and not any_visible)

    def _on_create_clicked(self, _btn):
        from kitsune.ui.create_tag_dialog import show_create_tag_dialog
        search_text = self._search.get_text().strip()
        self.popdown()
        show_create_tag_dialog(
            self.get_root(),
            callback=self._on_tag_created,
            prefill_name=search_text,
        )

    def _on_tag_created(self, tag):
        if tag:
            if self._sync and self._is_synced_tag(tag['id']):
                self._sync.add_to_tag_synced(tag['id'], self._release_id)
            else:
                tags_store.add_release(tag['id'], self._release_id)
            self._populate()
            if self._on_changed:
                self._on_changed()
