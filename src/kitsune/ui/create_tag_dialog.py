# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune import tags_store
from kitsune.ui import register_css
from kitsune.ui.widgets.tag_card import COLOR_MAP, create_color_circle

_COLOR_NAME_KEYS = {
    'blue': 'Blue',
    'teal': 'Teal',
    'green': 'Green',
    'yellow': 'Yellow',
    'orange': 'Orange',
    'red': 'Red',
    'pink': 'Pink',
    'purple': 'Purple',
    'slate': 'Slate',
}
# Ensure xgettext can extract color names:
_COLOR_GETTEXT = [
    _('Blue'), _('Teal'), _('Green'), _('Yellow'), _('Orange'),
    _('Red'), _('Pink'), _('Purple'), _('Slate'),
]


def _color_display_name(key: str) -> str:
    return _(_COLOR_NAME_KEYS.get(key, key))

_DIALOG_CSS = (
    '.color-ring { border-radius: 50%;'
    ' border: 2.5px solid transparent; padding: 2px;'
    ' transition: border-color 150ms ease-in-out; }'
    ' .color-ring-selected { border-color: currentColor; }'
    ' .color-ring:hover { border-color: alpha(currentColor, 0.4); }'
)


def _get_existing_tag_names() -> set[str]:
    return {t['name'].lower() for t in tags_store.get_all_tags()}


def show_create_tag_dialog(parent, callback=None, prefill_name=''):
    """Show a dialog to create a new tag. Calls callback(tag_dict) or callback(None)."""
    register_css(_DIALOG_CSS)

    dialog = Adw.AlertDialog(heading=_('New Tag'))
    dialog.add_response('cancel', _('Cancel'))
    dialog.add_response('create', _('Create'))
    dialog.set_response_appearance('create', Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response('create')
    dialog.set_close_response('cancel')

    existing_names = _get_existing_tag_names()

    content = Gtk.Box(
        orientation=Gtk.Orientation.VERTICAL,
        spacing=12, margin_start=12, margin_end=12,
    )

    state = {'icon_type': 'emoji', 'icon_value': '⭐'}

    # --- Type toggle: Adw.ToggleGroup ---
    type_toggle = Adw.ToggleGroup(homogeneous=True)
    type_toggle.add(Adw.Toggle(name='emoji', label=_('Emoji')))
    type_toggle.add(Adw.Toggle(name='color', label=_('Color')))
    type_toggle.set_active_name('emoji')
    content.append(type_toggle)

    # --- Combined row: chooser + separator + name entry ---
    # Emoji MenuButton
    emoji_chooser_btn = Gtk.MenuButton(
        label='⭐',
        valign=Gtk.Align.CENTER,
        css_classes=['flat'],
    )
    emoji_chooser = Gtk.EmojiChooser()
    emoji_chooser_btn.set_popover(emoji_chooser)

    def on_emoji_picked(_chooser, emoji):
        state['icon_value'] = emoji
        emoji_chooser_btn.set_label(emoji)

    emoji_chooser.connect('emoji-picked', on_emoji_picked)

    # Color MenuButton
    _btn_circle_css = Gtk.CssProvider()
    _btn_circle = Gtk.Box(
        width_request=22, height_request=22,
        halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
    )
    btn_inner = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
    btn_inner.append(_btn_circle)
    btn_inner.append(Gtk.Image(
        icon_name='pan-down-symbolic',
        pixel_size=10,
        valign=Gtk.Align.CENTER,
    ))

    color_menu_btn = Gtk.MenuButton(
        child=btn_inner,
        valign=Gtk.Align.CENTER,
        css_classes=['flat'],
    )

    color_popover = Gtk.Popover()
    pop_flow = Gtk.FlowBox(
        selection_mode=Gtk.SelectionMode.NONE,
        max_children_per_line=5,
        min_children_per_line=5,
        homogeneous=True,
        column_spacing=6, row_spacing=6,
        halign=Gtk.Align.CENTER,
        margin_top=8, margin_bottom=8,
        margin_start=8, margin_end=8,
    )

    rings = {}
    for color_name in COLOR_MAP:
        ring = Gtk.Box(
            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
            css_classes=['color-ring'],
        )
        ring.append(create_color_circle(color_name, 28))
        rings[color_name] = ring

        fb_child = Gtk.FlowBoxChild()
        fb_child.set_child(ring)
        fb_child._color_name = color_name
        pop_flow.append(fb_child)

    color_popover.set_child(pop_flow)
    color_menu_btn.set_popover(color_popover)

    # Chooser stack (switches between emoji and color buttons)
    chooser_stack = Gtk.Stack(
        transition_type=Gtk.StackTransitionType.CROSSFADE,
    )
    chooser_stack.add_named(emoji_chooser_btn, 'emoji')
    chooser_stack.add_named(color_menu_btn, 'color')

    # EntryRow with chooser as prefix
    name_row = Adw.EntryRow(title=_('Tag name'))
    if prefill_name:
        name_row.set_text(prefill_name)
    name_row.add_prefix(Gtk.Separator(
        orientation=Gtk.Orientation.VERTICAL,
        margin_top=8, margin_bottom=8,
    ))
    name_row.add_prefix(chooser_stack)

    name_group = Adw.PreferencesGroup()
    name_group.add(name_row)
    content.append(name_group)

    # Duplicate warning. wrap+max_width_chars keep the error short
    # enough to fit a narrow-window dialog.
    dup_label = Gtk.Label(
        label=_('A tag with this name already exists'),
        css_classes=['error', 'caption'],
        visible=False,
        wrap=True,
        max_width_chars=24,
        xalign=0,
    )
    content.append(dup_label)

    # --- Validation ---
    def _validate(*_args):
        name = name_row.get_text().strip()
        is_emoji = state['icon_type'] == 'emoji'

        if is_emoji and not name:
            dialog.set_response_enabled('create', False)
            dup_label.set_visible(False)
            return

        check_name = name if name else (
            _color_display_name(state['icon_value'])
            if not is_emoji else state['icon_value']
        )
        if check_name.lower() in existing_names:
            dialog.set_response_enabled('create', False)
            dup_label.set_visible(True)
            return

        dup_label.set_visible(False)
        dialog.set_response_enabled('create', True)

    name_row.connect('changed', _validate)

    # --- Color selection ---
    _ring_provider = Gtk.CssProvider()

    def _update_btn_circle(color_name):
        hex_val = COLOR_MAP.get(color_name, '#6e7781')
        _btn_circle_css.load_from_string(
            f'box {{ background: {hex_val}; border-radius: 50%;'
            f' min-width: 22px; min-height: 22px;'
            f' border: 1.5px solid alpha(white, 0.25); }}'
        )
        _btn_circle.get_style_context().add_provider(
            _btn_circle_css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

    def select_color(color_name):
        hex_val = COLOR_MAP.get(color_name, '#6e7781')
        for cname, r in rings.items():
            if cname == color_name:
                r.add_css_class('color-ring-selected')
                r.get_style_context().add_provider(
                    _ring_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
                )
            else:
                r.remove_css_class('color-ring-selected')
        _ring_provider.load_from_string(
            f'.color-ring-selected {{ border-color: {hex_val}; }}'
        )
        state['icon_value'] = color_name
        _update_btn_circle(color_name)
        _validate()

    def on_color_activated(_flow, child):
        select_color(child._color_name)
        color_popover.popdown()

    pop_flow.connect('child-activated', on_color_activated)
    select_color('blue')

    # --- Toggle handler ---
    def on_type_changed(_toggle, _pspec):
        active = type_toggle.get_active_name()
        if active == 'emoji':
            state['icon_type'] = 'emoji'
            state['icon_value'] = '⭐'
            chooser_stack.set_visible_child_name('emoji')
            name_row.set_title(_('Tag name'))
        else:
            state['icon_type'] = 'color'
            state['icon_value'] = 'blue'
            select_color('blue')
            chooser_stack.set_visible_child_name('color')
            name_row.set_title(_('Tag name (optional)'))
        _validate()

    type_toggle.connect('notify::active-name', on_type_changed)
    _validate()

    dialog.set_extra_child(content)

    def on_response(_dialog, response):
        if response == 'create':
            name = name_row.get_text().strip()
            if not name:
                if state['icon_type'] == 'color':
                    name = _color_display_name(state['icon_value'])
                else:
                    name = state['icon_value']
            tag = tags_store.create_tag(
                name, state['icon_type'], state['icon_value'],
            )
            if callback:
                callback(tag)
            return
        if callback:
            callback(None)

    dialog.connect('response', on_response)
    dialog.present(parent)
