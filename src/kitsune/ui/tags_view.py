# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune.api import AniLibriaClient
from kitsune.models import Release
from kitsune import release_cache, tags_store
from kitsune.ui.tag_releases_view import TagReleasesView
from kitsune.ui.widgets.content_grid import ContentGrid
from kitsune.ui.widgets.tag_card import TagCard
from kitsune.ui.image_cache import load_image
from kitsune.ui import register_css

_TAGS_CSS = (
    '.tag-add-card { border: 2px dashed alpha(currentColor, 0.15);'
    '   border-radius: 12px; background: none; }'
)


class TagsView(Gtk.Box):

    def __init__(self, client: AniLibriaClient, **kwargs):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, **kwargs)
        self._client = client
        self._on_release_activated = None
        self._on_navigation_changed = None
        self._on_tags_changed_ext = None
        self._narrow = False
        self._current_tag = None
        self._view_mode = 'cards'
        # Live login state — controls whether builtin tags carry the
        # cloud sync badge (grid) or the cloud suffix (list). Window
        # flips this via set_synced on login / logout / sync_complete.
        self._synced = False
        register_css(_TAGS_CSS)

        # Navigation stack: main (cards/list) + releases detail
        self._nav_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.SLIDE_LEFT_RIGHT,
        )

        # Card grid mode
        self._card_grid = ContentGrid()
        self._card_grid.set_on_child_activated(self._on_card_activated)

        # List mode
        self._list_scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True,
        )
        self._list_container = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=12,
            margin_start=24, margin_end=24, margin_top=12, margin_bottom=12,
        )
        self._list_scroll.set_child(self._list_container)

        # Mode stack
        self._mode_stack = Gtk.Stack(
            transition_type=Gtk.StackTransitionType.CROSSFADE,
        )
        self._mode_stack.add_named(self._card_grid, 'cards')
        self._mode_stack.add_named(self._list_scroll, 'list')

        self._nav_stack.add_named(self._mode_stack, 'main')

        self._releases_placeholder = Gtk.Box()
        self._nav_stack.add_named(self._releases_placeholder, 'releases')

        self.append(self._nav_stack)
        self._populate()

    @property
    def in_releases(self) -> bool:
        return self._nav_stack.get_visible_child_name() == 'releases'

    @property
    def current_tag_name(self) -> str:
        if not self._current_tag:
            return ''
        return tags_store.display_name(self._current_tag)

    @property
    def current_tag(self) -> dict | None:
        return self._current_tag

    def toggle_mode(self):
        if self._view_mode == 'cards':
            self._view_mode = 'list'
        else:
            self._view_mode = 'cards'
        self._mode_stack.set_visible_child_name(self._view_mode)

    def set_narrow(self, narrow: bool):
        self._narrow = narrow
        self._card_grid.set_narrow(narrow)
        self._list_container.set_margin_bottom(64 if narrow else 12)
        releases = self._nav_stack.get_child_by_name('releases')
        if releases and isinstance(releases, TagReleasesView):
            releases.set_narrow(narrow)

    def set_on_release_activated(self, callback):
        self._on_release_activated = callback

    def set_on_navigation_changed(self, callback):
        self._on_navigation_changed = callback

    def set_on_tags_changed(self, callback):
        self._on_tags_changed_ext = callback

    def go_back(self):
        self._nav_stack.set_visible_child_name('main')
        self._current_tag = None
        if self._on_navigation_changed:
            self._on_navigation_changed()

    def refresh(self):
        self._populate()

    def set_synced(self, synced: bool):
        """Update the visible-sync flag and re-render if it changed.

        Triggered by window.py when the auth session flips (login /
        logout) or when a full sync completes — both events change the
        meaning of the builtin tags (locally-isolated → server-mirrored
        or vice versa), and the cloud badges must follow.
        """
        if self._synced == synced:
            return
        self._synced = synced
        self._populate()

    def _populate(self):
        tags = tags_store.get_all_tags()
        self._populate_cards(tags)
        self._populate_list(tags)

    def _populate_cards(self, tags: list[dict]):
        self._card_grid.clear()
        for tag in tags:
            self._card_grid.append_child(
                TagCard(tag, on_delete=self._confirm_delete_tag,
                        is_synced=self._synced))

        # "Add new" card — same layout as TagCard
        add_child = Gtk.FlowBoxChild()
        outer = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=6, width_request=180,
            margin_start=6, margin_end=6, margin_top=6, margin_bottom=6,
        )
        clamp = Adw.Clamp(maximum_size=180)
        add_frame = Gtk.Frame(
            width_request=180, height_request=140,
            css_classes=['tag-add-card'],
        )
        add_frame.set_child(Gtk.Label(
            label='+', css_classes=['title-1'],
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        ))
        clamp.set_child(add_frame)
        outer.append(clamp)
        outer.append(Gtk.Label(
            label=_('New tag'), css_classes=['dim-label'],
            halign=Gtk.Align.CENTER,
        ))
        add_child.set_child(outer)
        add_child._is_add_card = True
        self._card_grid.append_child(add_child)

    def _populate_list(self, tags: list[dict]):
        while child := self._list_container.get_first_child():
            self._list_container.remove(child)

        for tag in tags:
            block = Gtk.ListBox(
                selection_mode=Gtk.SelectionMode.NONE,
                css_classes=['boxed-list'],
            )
            row = self._create_list_row(tag)
            block.append(row)
            self._list_container.append(block)

    def _create_list_row(self, tag: dict) -> Adw.ExpanderRow:
        from kitsune.ui.widgets.tag_card import create_color_circle
        row = Adw.ExpanderRow(title=tags_store.display_name(tag))

        if tag['icon_type'] == 'emoji':
            icon = Gtk.Label(
                label=tag['icon_value'],
                valign=Gtk.Align.CENTER,
            )
            icon.set_size_request(32, 32)
            css = Gtk.CssProvider()
            css.load_from_string('label { font-size: 22px; }')
            icon.get_style_context().add_provider(
                css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
            )
        elif tag['icon_type'] == 'symbolic':
            icon = Gtk.Image.new_from_icon_name(tag['icon_value'])
            icon.set_pixel_size(24)
            icon.set_valign(Gtk.Align.CENTER)
            from kitsune.ui import resolved_tag_color
            color = resolved_tag_color(tag)
            if color:
                css = Gtk.CssProvider()
                css.load_from_string(f"image {{ color: {color}; }}")
                icon.get_style_context().add_provider(
                    css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
        else:
            icon = create_color_circle(tag['icon_value'], 28)
        row.add_prefix(icon)

        count = len(tag.get('releases', []))
        row.add_suffix(Gtk.Label(
            label=str(count),
            css_classes=['dim-label'],
            valign=Gtk.Align.CENTER,
        ))

        if not tag.get('builtin'):
            del_btn = Gtk.Button(
                icon_name='net.armatik.Kitsune.user-trash-symbolic',
                css_classes=['flat', 'error'],
                valign=Gtk.Align.CENTER,
            )
            del_btn.connect('clicked', lambda _b, t=tag: self._confirm_delete_tag(t))
            row.add_suffix(del_btn)
        elif self._synced:
            # Synced-with-server marker — only attached when the user
            # is actually signed in (otherwise the badge would be
            # misleading: the tag is local-only until login). Appears
            # just left of the Adw.ExpanderRow's auto-rendered expand
            # arrow.
            sync_icon = Gtk.Image.new_from_icon_name(
                'net.armatik.Kitsune.cloud-filled-symbolic')
            sync_icon.set_pixel_size(14)
            sync_icon.set_valign(Gtk.Align.CENTER)
            sync_icon.add_css_class('dim-label')
            sync_icon.set_tooltip_text(_('Synced with your AniLibria account'))
            row.add_suffix(sync_icon)

        row._tag = tag
        row._loaded = False
        row.connect('notify::expanded', self._on_row_expanded)

        return row

    def _on_row_expanded(self, row, _pspec):
        if not row.get_expanded() or row._loaded:
            return
        row._loaded = True

        release_ids = tags_store.get_release_ids_for_tag(row._tag['id'])
        if not release_ids:
            row.add_row(Adw.ActionRow(title=_('No releases')))
            return

        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.AUTOMATIC,
            vscrollbar_policy=Gtk.PolicyType.NEVER,
            height_request=220,
        )
        strip = Gtk.Box(
            spacing=12, margin_start=12, margin_end=12,
            margin_top=8, margin_bottom=8,
        )

        for rid in release_ids:
            cached = release_cache.get(rid)
            if cached:
                release = Release.from_dict(cached)
                card_box = self._create_mini_card(release)
                strip.append(card_box)

        scroll.set_child(strip)
        row.add_row(scroll)

    def _create_mini_card(self, release: Release) -> Gtk.Box:
        from kitsune.ui import apply_adult_blur
        box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            spacing=4, width_request=120,
        )
        # Picture lives inside a clipping wrapper that carries the
        # `card` style (radius + shadow). Picture itself is unstyled so
        # its CSS blur filter, when applied for 18+ content, gets cut by
        # the wrapper's overflow:hidden instead of bleeding past edges.
        clipper = Gtk.Box(
            width_request=120, height_request=170,
            css_classes=['card'],
        )
        clipper.set_overflow(Gtk.Overflow.HIDDEN)
        pic = Gtk.Picture(
            content_fit=Gtk.ContentFit.COVER,
            hexpand=True, vexpand=True,
        )
        apply_adult_blur(pic, release.is_adult)
        clipper.append(pic)
        if release.poster:
            load_image(release.poster, lambda tex, err, p=pic:
                       p.set_paintable(tex) if tex else None)
        box.append(clipper)
        box.append(Gtk.Label(
            label=release.name.main, xalign=0,
            max_width_chars=14, ellipsize=3,
            css_classes=['caption'],
        ))
        gesture = Gtk.GestureClick()
        gesture.connect('released', lambda _g, _n, _x, _y, r=release:
                        self._on_release_activated(r)
                        if self._on_release_activated else None)
        box.add_controller(gesture)
        return box

    def _on_card_activated(self, child):
        if hasattr(child, '_is_add_card') and child._is_add_card:
            self._show_create_dialog()
            return
        if isinstance(child, TagCard):
            self._show_tag_releases(child.tag)

    def _show_tag_releases(self, tag: dict):
        self._current_tag = tag

        old = self._nav_stack.get_child_by_name('releases')
        if old:
            self._nav_stack.remove(old)

        releases_view = TagReleasesView(
            tag=tag, client=self._client,
        )
        releases_view.set_on_release_activated(self._on_release_activated)
        releases_view.set_narrow(self._narrow)
        self._nav_stack.add_named(releases_view, 'releases')
        self._nav_stack.set_visible_child_name('releases')

        if self._on_navigation_changed:
            self._on_navigation_changed()

    def _show_create_dialog(self):
        from kitsune.ui.create_tag_dialog import show_create_tag_dialog
        show_create_tag_dialog(
            self.get_root(),
            callback=self._on_tag_created,
        )

    def delete_current_tag(self):
        if self._current_tag:
            self._confirm_delete_tag(self._current_tag)

    def _confirm_delete_tag(self, tag: dict):
        dialog = Adw.AlertDialog(
            heading=_('Delete Tag?'),
            body=_('Tag "%s" will be removed. This cannot be undone.') % tag['name'],
        )
        dialog.add_response('cancel', _('Cancel'))
        dialog.add_response('delete', _('Delete'))
        dialog.set_response_appearance('delete', Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response('cancel')
        dialog.set_close_response('cancel')

        def on_response(_dialog, response):
            if response == 'delete':
                release_ids = tags_store.get_release_ids_for_tag(tag['id'])
                tags_store.delete_tag(tag['id'])
                if self.in_releases and self._current_tag and self._current_tag['id'] == tag['id']:
                    self.go_back()
                self._populate()
                if self._on_tags_changed_ext:
                    self._on_tags_changed_ext(release_ids)

        dialog.connect('response', on_response)
        dialog.present(self.get_root())

    def _on_tag_created(self, tag: dict | None):
        if tag:
            self._populate()

