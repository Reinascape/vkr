# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')

from gi.repository import Gtk

from kitsune import tags_store
from kitsune.ui import resolved_tag_color


def _make_symbolic_image(tag: dict, pixel_size: int) -> Gtk.Image:
    image = Gtk.Image.new_from_icon_name(tag['icon_value'])
    image.set_pixel_size(pixel_size)
    image.set_valign(Gtk.Align.CENTER)
    color = resolved_tag_color(tag)
    if color:
        css = Gtk.CssProvider()
        css.load_from_string(f"image {{ color: {color}; }}")
        image.get_style_context().add_provider(
            css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
    return image


def _make_color_circle(color_name: str, color_map: dict) -> Gtk.Box:
    hex_c = color_map.get(color_name, '#6e7781')
    circle = Gtk.Box(
        width_request=14, height_request=14,
        valign=Gtk.Align.CENTER, halign=Gtk.Align.CENTER,
    )
    css = Gtk.CssProvider()
    css.load_from_string(
        f'box {{ background: {hex_c}; border-radius: 50%;'
        f' min-width: 14px; min-height: 14px;'
        f' border: 1px solid alpha(currentColor, 0.2); }}'
    )
    circle.get_style_context().add_provider(
        css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
    )
    return circle


def create_full_tag_pill(tag: dict) -> Gtk.Button:
    from kitsune.ui.widgets.tag_card import COLOR_MAP
    box = Gtk.Box(spacing=5, halign=Gtk.Align.CENTER)
    if tag['icon_type'] == 'emoji':
        box.append(Gtk.Label(label=tag['icon_value']))
    elif tag['icon_type'] == 'symbolic':
        box.append(_make_symbolic_image(tag, 16))
    else:
        box.append(_make_color_circle(tag['icon_value'], COLOR_MAP))
    box.append(Gtk.Label(label=tag['name']))
    return Gtk.Button(child=box, css_classes=['pill', 'release-chip'])


def create_compact_tag_pill(tag: dict) -> Gtk.Button:
    from kitsune.ui.widgets.tag_card import COLOR_MAP
    if tag['icon_type'] == 'emoji':
        child = Gtk.Label(label=tag['icon_value'])
    elif tag['icon_type'] == 'symbolic':
        child = _make_symbolic_image(tag, 16)
    else:
        child = _make_color_circle(tag['icon_value'], COLOR_MAP)
    return Gtk.Button(
        child=child, css_classes=['release-chip-compact'],
        tooltip_text=tag['name'],
    )


def update_tag_pills(wrap_widget, release_id: int, narrow_mode: bool, on_clicked):
    while child := wrap_widget.get_first_child():
        wrap_widget.remove(child)

    release_tags = tags_store.get_tags_for_release(release_id)
    if not release_tags:
        wrap_widget.set_visible(False)
        return
    wrap_widget.set_visible(True)

    threshold = 3 if narrow_mode else 5
    compact = len(release_tags) > threshold
    for tag in release_tags:
        if compact:
            btn = create_compact_tag_pill(tag)
        else:
            btn = create_full_tag_pill(tag)
        btn.connect('clicked', lambda _b, t=tag: on_clicked(t))
        wrap_widget.append(btn)
