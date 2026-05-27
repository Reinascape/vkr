# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune.models import Franchise, Release
from kitsune.ui.image_cache import load_image


def related_subtitle(release: Release) -> str:
    parts = []
    if release.year:
        parts.append(str(release.year))
    if release.season:
        parts.append(release.season)
    if release.type:
        parts.append(release.type)
    if release.episodes_total:
        parts.append(_('%d episodes') % release.episodes_total)
    return ' \u2022 '.join(parts)


def populate_related(header, list_widget, franchise: Franchise,
                     current_release_id: int, on_activated):
    # Franchise header
    header.set_visible(True)
    title = Gtk.Label(
        label=franchise.name, xalign=0, wrap=True,
        margin_start=16, margin_end=16, margin_top=12,
        css_classes=['title-4'],
    )
    header.append(title)

    if franchise.name_english:
        en = Gtk.Label(
            label=franchise.name_english, xalign=0, wrap=True,
            margin_start=16, margin_end=16,
            css_classes=['dim-label'],
        )
        header.append(en)

    meta_parts = []
    if franchise.first_year and franchise.last_year:
        meta_parts.append(f'{franchise.first_year} \u2014 {franchise.last_year}')
    elif franchise.first_year:
        meta_parts.append(str(franchise.first_year))
    if franchise.total_releases:
        meta_parts.append(
            _('%d seasons') % franchise.total_releases
            if franchise.total_releases > 1 else _('1 season')
        )
    if franchise.total_episodes:
        meta_parts.append(_('%d episodes') % franchise.total_episodes)
    if franchise.total_duration:
        meta_parts.append(franchise.total_duration)

    if meta_parts:
        meta = Gtk.Label(
            label=' \u2022 '.join(meta_parts), xalign=0, wrap=True,
            margin_start=16, margin_end=16, margin_bottom=12,
            css_classes=['dim-label', 'caption'],
        )
        header.append(meta)

    # Franchise releases
    list_widget.set_visible(True)
    for idx, release in enumerate(franchise.releases):
        is_current = release.id == current_release_id

        row = Adw.ActionRow(
            title=release.name.main,
            subtitle=related_subtitle(release),
            activatable=not is_current,
            use_markup=False,
        )
        row.add_css_class('heading')

        num_classes = ['title-2']
        num_classes.append('accent' if is_current else 'dim-label')
        num_label = Gtk.Label(
            label=f'#{idx + 1}',
            css_classes=num_classes,
            valign=Gtk.Align.CENTER,
        )
        row.add_suffix(num_label)

        # Top/bottom margins mirror the natural left-side padding the
        # row gives prefix widgets, so the poster has breathing room
        # on all four sides instead of touching the row's edge.
        clamp = Adw.Clamp(
            maximum_size=90, valign=Gtk.Align.CENTER,
            margin_top=8, margin_bottom=8,
        )
        pic_overlay = Gtk.Overlay(
            width_request=90, height_request=126,
            css_classes=['card'],
        )
        pic_overlay.set_overflow(Gtk.Overflow.HIDDEN)
        pic = Gtk.Picture(
            width_request=90, height_request=126,
            content_fit=Gtk.ContentFit.COVER,
        )
        pic_overlay.set_child(pic)
        clamp.set_child(pic_overlay)
        if release.poster:
            load_image(release.poster, lambda tex, err, p=pic:
                       p.set_paintable(tex) if tex else None)
        row.add_prefix(clamp)

        if not is_current:
            row.connect('activated', lambda _r, rel=release:
                        on_activated(rel))

        list_widget.append(row)
