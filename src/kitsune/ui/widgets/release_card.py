# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gio, Gtk

from kitsune.models import Release
from kitsune import tags_store
from kitsune.ui import apply_adult_blur, register_css, resolved_tag_color
from kitsune.ui.image_cache import load_image

_BADGE_CSS = (
    '.tag-badge-pill { padding: 5px 8px; border-radius: 9999px;'
    ' background: alpha(black, 0.55);'
    ' border: 1px solid alpha(white, 0.08); }'
    ' .tag-badge-emoji { font-size: 16px; }'
    ' .tag-badge-fallback-fg image { color: white; }'
)


@Gtk.Template(resource_path='/net/armatik/Kitsune/release_card.ui')
class ReleaseCard(Gtk.FlowBoxChild):
    __gtype_name__ = 'KitsuneReleaseCard'

    picture = Gtk.Template.Child()
    picture_clipper = Gtk.Template.Child()
    placeholder = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    subtitle_label = Gtk.Template.Child()
    tag_badges = Gtk.Template.Child()

    def __init__(self, release: Release, **kwargs):
        super().__init__(**kwargs)
        register_css(_BADGE_CSS)
        self.release = release

        # Clip Picture rendering to the wrapper's rounded card shape so
        # the CSS blur filter cannot bloom past the card edges. The
        # wrapper carries the `card` style (radius + shadow); Picture
        # itself is unstyled, so its blur output gets cut by the
        # wrapper's GSK clip node.
        self.picture_clipper.set_overflow(Gtk.Overflow.HIDDEN)

        apply_adult_blur(self.picture, release.is_adult)

        self.title_label.set_label(release.name.main)

        subtitle_parts = []
        if release.type:
            subtitle_parts.append(release.type)
        if release.year:
            subtitle_parts.append(str(release.year))
        if subtitle_parts:
            self.subtitle_label.set_label(' / '.join(subtitle_parts))
            self.subtitle_label.set_visible(True)

        if release.poster:
            if release.poster_preview:
                load_image(release.poster_preview, self._on_preview_loaded)
            load_image(release.poster, self._on_poster_loaded)
        elif release.poster_preview:
            load_image(release.poster_preview, self._on_poster_loaded)
        else:
            self.spinner.set_visible(False)
            self.placeholder.set_visible(True)

        self._populate_tag_badges()

    def _populate_tag_badges(self):
        register_css(_BADGE_CSS)
        tags = tags_store.get_tags_for_release(self.release.id)
        if not tags:
            return

        self.tag_badges.set_visible(True)
        max_visible = 3
        visible_tags = tags[:max_visible]
        has_more = len(tags) > max_visible

        pill = Gtk.Box(
            spacing=4,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            css_classes=['tag-badge-pill'],
        )

        for tag in visible_tags:
            if tag['icon_type'] == 'emoji':
                pill.append(Gtk.Label(
                    label=tag['icon_value'],
                    css_classes=['tag-badge-emoji'],
                ))
            elif tag['icon_type'] == 'symbolic':
                image = Gtk.Image.new_from_icon_name(tag['icon_value'])
                image.set_pixel_size(16)
                color = resolved_tag_color(tag, on_osd=True)
                if color:
                    css = Gtk.CssProvider()
                    css.load_from_string(f"image {{ color: {color}; }}")
                    image.get_style_context().add_provider(
                        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                    )
                else:
                    image.add_css_class('tag-badge-fallback-fg')
                pill.append(image)
            else:
                from kitsune.ui.widgets.tag_card import create_color_circle
                pill.append(create_color_circle(tag['icon_value'], 16))

        if has_more:
            more = Gtk.Image(
                icon_name='net.armatik.Kitsune.plus-circle-symbolic',
                pixel_size=16,
                valign=Gtk.Align.CENTER,
            )
            more.add_css_class('tag-badge-fallback-fg')
            pill.append(more)

        self.tag_badges.append(pill)

    def refresh_tag_badges(self):
        while child := self.tag_badges.get_first_child():
            self.tag_badges.remove(child)
        self.tag_badges.set_visible(False)
        self._populate_tag_badges()

    def refresh_adult_blur(self):
        # Re-evaluate the blur for this card. Strips the class first so
        # apply_adult_blur can re-add it only if the setting and the
        # release's is_adult flag both still warrant blurring.
        self.picture.remove_css_class('adult-blur')
        apply_adult_blur(self.picture, self.release.is_adult)

    def _on_preview_loaded(self, texture, error):
        if texture and not self.picture.get_paintable():
            self.picture.set_paintable(texture)

    def _on_poster_loaded(self, texture, error):
        self.spinner.set_visible(False)
        if texture:
            self.picture.set_paintable(texture)
        elif not self.picture.get_paintable():
            self.placeholder.set_visible(True)
