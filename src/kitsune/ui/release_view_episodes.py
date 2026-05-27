# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, Gtk

from kitsune.models import Episode
from kitsune.storage.watch_positions import is_completed
from kitsune.ui.image_cache import load_image
from kitsune.ui import register_css

_EPISODE_CSS = (
    '.episode-card { border-radius: 12px;'
    ' background: alpha(currentColor, 0.08);'
    ' transition: box-shadow 200ms ease; }'
    ' .episode-card:hover {'
    ' box-shadow: 0 0 14px 2px alpha(currentColor, 0.35),'
    ' 0 6px 18px alpha(black, 0.18); }'
    # Strip the FlowBox cell-level hover/active highlight so only the
    # rounded card itself reacts to pointer, not the rectangular slot.
    ' .episodes-grid > flowboxchild { background: none; padding: 0; }'
    ' .episodes-grid > flowboxchild:hover { background: none; }'
    ' .episodes-grid > flowboxchild:active { background: none; }'
    ' .episode-overlay { background: linear-gradient(to top,'
    ' alpha(black, 0.7) 0%, transparent 50%); }'
    ' .ep-overlay-text { color: white; text-shadow: 0 1px 3px alpha(black, 0.8); }'
    ' .episode-progress { min-height: 4px; border-radius: 0; }'
    ' .episode-progress trough { min-height: 4px; background: alpha(white, 0.3); }'
    ' .episode-progress progress { min-height: 4px; background: @accent_bg_color; }'
    ' .episode-blur { filter: blur(8px); }'
    ' .episode-check { background: alpha(black, 0.6); border-radius: 50%;'
    '   min-width: 24px; min-height: 24px; padding: 2px;'
    '   color: @accent_color; text-shadow: none; }'
    ' .episode-separator { min-height: 1px;'
    '   background-color: alpha(currentColor, 0.15); padding: 0; margin: 0; }'
    ' .list-progress { margin-top: 4px; }'
    ' .list-progress trough { min-height: 4px; }'
    ' .list-progress progress { min-height: 4px; background: @accent_bg_color; }'
)

_EP_CARD_W = 240
_EP_CARD_H = 135  # 16:9

def _ensure_css():
    register_css(_EPISODE_CSS)


def episode_title(episode: Episode) -> str:
    ordinal = int(episode.ordinal) if episode.ordinal == int(episode.ordinal) else episode.ordinal
    if episode.name:
        return f'{ordinal}. {episode.name}'
    return _('Episode {}').format(ordinal)


def episode_subtitle(episode: Episode, watch_data: dict) -> str:
    parts = []
    pos = watch_data.get(episode.ordinal, 0)
    if is_completed(pos, episode.duration):
        if episode.duration:
            mins = episode.duration // 60
            parts.append(_('Watched') + f' ({mins} ' + _('min') + ')')
        else:
            parts.append(_('Watched'))
    elif pos > 0 and episode.duration:
        remaining = max(0, episode.duration - pos)
        rem_min = int(remaining) // 60
        total_min = episode.duration // 60
        parts.append(_('Remaining: {} min of {} min').format(rem_min, total_min))
    elif episode.duration:
        mins = episode.duration // 60
        parts.append(f'{mins} ' + _('min'))
    qualities = []
    if episode.hls_1080:
        qualities.append('1080p')
    if episode.hls_720:
        qualities.append('720p')
    if episode.hls_480:
        qualities.append('480p')
    if qualities:
        parts.append(' / '.join(qualities))
    return ' \u2014 '.join(parts) if parts else ''


def get_filtered_episodes(episodes, watch_filter: str, search_text: str,
                          sort_newest_first: bool, watch_data: dict) -> list[Episode]:
    result = list(episodes)
    if watch_filter == 'watched':
        result = [ep for ep in result
                  if watch_data.get(ep.ordinal, 0) != 0]
    elif watch_filter == 'unwatched':
        result = [ep for ep in result
                  if watch_data.get(ep.ordinal, 0) == 0]
    if search_text:
        query = search_text.casefold()
        filtered = []
        for ep in result:
            ordinal = int(ep.ordinal) if ep.ordinal == int(ep.ordinal) else ep.ordinal
            if query in str(ordinal):
                filtered.append(ep)
            elif ep.name and query in ep.name.casefold():
                filtered.append(ep)
        result = filtered
    if sort_newest_first:
        result = list(reversed(result))
    return result


def populate_episode_list(list_widget, episodes, watch_data: dict, on_play):
    _ensure_css()
    while child := list_widget.get_first_child():
        list_widget.remove(child)

    for episode in episodes:
        pos = watch_data.get(episode.ordinal, 0)

        row = Adw.ActionRow(
            title=episode_title(episode),
            subtitle=episode_subtitle(episode, watch_data),
            activatable=True,
            use_markup=False,
        )
        if is_completed(pos, episode.duration):
            check = Gtk.Image(
                icon_name='net.armatik.Kitsune.object-select-symbolic',
                css_classes=['accent'],
                valign=Gtk.Align.CENTER,
            )
            row.add_suffix(check)
        elif pos > 0 and episode.duration and episode.duration > 0:
            fraction = min(1.0, max(0.0, pos / episode.duration))
            prog = Gtk.ProgressBar(
                fraction=fraction,
                valign=Gtk.Align.CENTER,
                css_classes=['list-progress'],
            )
            prog.set_size_request(60, -1)
            row.add_suffix(prog)

        play_btn = Gtk.Button(
            icon_name='net.armatik.Kitsune.media-playback-start-symbolic',
            valign=Gtk.Align.CENTER, css_classes=['flat'],
        )
        play_btn.connect('clicked', lambda _b, ep=episode: on_play(ep))
        row.add_suffix(play_btn)

        row.connect('activated', lambda _r, ep=episode: on_play(ep))
        list_widget.append(row)


def build_episode_card(episode: Episode, watch_data: dict,
                       settings: Gio.Settings, on_play) -> Gtk.Widget:
    _ensure_css()
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
    pos = watch_data.get(episode.ordinal, 0)

    clamp = Adw.Clamp(maximum_size=_EP_CARD_W)

    overlay = Gtk.Overlay(
        css_classes=['episode-card'],
        width_request=_EP_CARD_W,
        height_request=_EP_CARD_H,
    )
    overlay.set_overflow(Gtk.Overflow.HIDDEN)
    overlay.set_cursor(Gdk.Cursor.new_from_name('pointer'))

    pic_classes = []
    if pos == 0 and settings.get_boolean('blur-unwatched-episodes'):
        pic_classes.append('episode-blur')

    picture = Gtk.Picture(
        content_fit=Gtk.ContentFit.COVER,
        width_request=_EP_CARD_W,
        height_request=_EP_CARD_H,
        css_classes=pic_classes,
    )
    overlay.set_child(picture)

    if episode.preview:
        spinner = Adw.Spinner(
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            width_request=32, height_request=32,
        )
        overlay.add_overlay(spinner)

        def _on_preview_loaded(tex, err, pic=picture, sp=spinner, ov=overlay):
            sp.set_visible(False)
            if tex:
                pic.set_paintable(tex)
            else:
                ov.add_overlay(Gtk.Image(
                    icon_name='net.armatik.Kitsune.image-missing-symbolic',
                    pixel_size=48, opacity=0.4,
                    halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                ))

        load_image(episode.preview, _on_preview_loaded,
                   category='previews')
    else:
        placeholder = Gtk.Image(
            icon_name='net.armatik.Kitsune.image-missing-symbolic',
            pixel_size=48, opacity=0.4,
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
        )
        overlay.add_overlay(placeholder)

    gradient = Gtk.Box(
        css_classes=['episode-overlay'],
        hexpand=True, vexpand=True,
    )
    overlay.add_overlay(gradient)

    ordinal = int(episode.ordinal) if episode.ordinal == int(episode.ordinal) else episode.ordinal

    label_box = Gtk.Box(
        spacing=4, margin_start=10, margin_end=10,
        margin_bottom=8, valign=Gtk.Align.END,
    )

    if episode.name:
        title_col = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            hexpand=True, spacing=1,
        )
        ep_label = Gtk.Label(
            label=_('Episode {}').format(ordinal),
            xalign=0,
            css_classes=['heading', 'ep-overlay-text'],
        )
        name_label = Gtk.Label(
            label=episode.name,
            xalign=0, ellipsize=3,  # PANGO_ELLIPSIZE_END
            css_classes=['caption', 'ep-overlay-text'],
        )
        title_col.append(ep_label)
        title_col.append(name_label)
        label_box.append(title_col)
    else:
        ep_label = Gtk.Label(
            label=_('Episode {}').format(ordinal),
            xalign=0, hexpand=True,
            css_classes=['heading', 'ep-overlay-text'],
        )
        label_box.append(ep_label)

    if pos > 0 and episode.duration and not is_completed(pos, episode.duration):
        remaining = max(0, episode.duration - pos)
        rem_min = int(remaining) // 60
        rem_label = Gtk.Label(
            label=_('Remaining: {} min').format(rem_min),
            valign=Gtk.Align.END,
            css_classes=['caption', 'ep-overlay-text'],
        )
        label_box.append(rem_label)
    elif episode.duration:
        mins = episode.duration // 60
        secs = episode.duration % 60
        dur_label = Gtk.Label(
            label=f'{mins}:{secs:02d}',
            valign=Gtk.Align.END,
            css_classes=['caption', 'ep-overlay-text'],
        )
        label_box.append(dur_label)
    overlay.add_overlay(label_box)

    # Progress bar at bottom with 1px separator
    if pos != 0 and episode.duration and episode.duration > 0:
        fraction = 1.0 if is_completed(pos, episode.duration) else min(1.0, max(0.0, pos / episode.duration))
        progress_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            valign=Gtk.Align.END,
            hexpand=True,
        )
        separator = Gtk.Box(
            css_classes=['episode-separator'],
            hexpand=True,
        )
        progress_box.append(separator)
        progress_bar = Gtk.ProgressBar(
            fraction=fraction,
            css_classes=['episode-progress'],
        )
        progress_box.append(progress_bar)
        overlay.add_overlay(progress_box)

    # Checkmark for completed (including near-end)
    if is_completed(pos, episode.duration):
        check_box = Gtk.Box(
            halign=Gtk.Align.END, valign=Gtk.Align.START,
            margin_top=6, margin_end=6,
        )
        check_icon = Gtk.Image(
            icon_name='net.armatik.Kitsune.object-select-symbolic',
            pixel_size=16,
            css_classes=['episode-check'],
        )
        check_box.append(check_icon)
        overlay.add_overlay(check_box)

    gesture = Gtk.GestureClick()
    gesture.connect('released',
                    lambda g, n, x, y, ep=episode: on_play(ep))
    overlay.add_controller(gesture)

    clamp.set_child(overlay)
    box.append(clamp)
    return box
