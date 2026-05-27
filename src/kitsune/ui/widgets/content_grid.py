# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune.ui import register_css

_SCROLL_UP_THRESHOLD = 600
_SCROLL_NEAR_END_OFFSET = 200

# Soft top→fade backdrop for the pull-refresh revealer area. Matches
# the tone libadwaita uses for the native overshoot/undershoot glow:
# alpha-currentColor inverts with the theme (dark on light, light on
# dark) without a hardcoded literal color.
_PULL_REFRESH_CSS = (
    '.pull-refresh-bg {'
    ' background: linear-gradient(to bottom,'
    ' alpha(currentColor, 0.07),'
    ' alpha(currentColor, 0)); }'
)


@Gtk.Template(resource_path='/net/armatik/Kitsune/content_grid.ui')
class ContentGrid(Gtk.Box):
    """Reusable scrollable FlowBox grid with scroll-to-top, spinner, and end label."""
    __gtype_name__ = 'KitsuneContentGrid'

    scrolled = Gtk.Template.Child()
    content_box = Gtk.Template.Child()
    flowbox = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    initial_spinner = Gtk.Template.Child()
    end_label = Gtk.Template.Child()
    scroll_up_revealer = Gtk.Template.Child()
    pull_refresh_revealer = Gtk.Template.Child()

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        register_css(_PULL_REFRESH_CSS)
        self._on_scroll_near_end = None
        self._on_child_activated = None
        self._has_content = False
        self._error_widget = None
        self._vadjustment = self.scrolled.get_vadjustment()
        self._vadjustment.connect('value-changed', self._on_scroll)

    def set_narrow(self, narrow: bool):
        if narrow:
            self.flowbox.set_min_children_per_line(1)
            self.flowbox.set_max_children_per_line(1)
            self.content_box.set_margin_bottom(52)
        else:
            self.flowbox.set_min_children_per_line(2)
            self.flowbox.set_max_children_per_line(6)
            self.content_box.set_margin_bottom(0)

    def set_on_scroll_near_end(self, callback):
        self._on_scroll_near_end = callback

    def set_on_child_activated(self, callback):
        self._on_child_activated = callback

    def set_spinner_visible(self, visible: bool):
        if self._has_content:
            self.spinner.set_visible(visible)
        else:
            self.initial_spinner.set_visible(visible)

    def set_pull_refresh_active(self, active: bool):
        # Revealer's slide_down transition animates the spinner area in
        # from height 0, pushing the grid below it; on hide it slides
        # back up smoothly.
        self.pull_refresh_revealer.set_reveal_child(active)

    def show_error(self):
        self.spinner.set_visible(False)
        self.initial_spinner.set_visible(False)
        self.clear_error()
        error = Gtk.Image(
            icon_name='net.armatik.Kitsune.cross-large-symbolic',
            pixel_size=48,
            css_classes=['error'],
            halign=Gtk.Align.CENTER,
            valign=Gtk.Align.CENTER,
        )
        self._error_widget = error
        if not self._has_content:
            overlay = self.initial_spinner.get_parent()
            overlay.add_overlay(error)
        else:
            error.set_margin_top(24)
            error.set_margin_bottom(24)
            parent = self.spinner.get_parent()
            parent.insert_child_after(error, self.spinner)

    def clear_error(self):
        if self._error_widget:
            parent = self._error_widget.get_parent()
            if parent:
                if isinstance(parent, Gtk.Overlay):
                    parent.remove_overlay(self._error_widget)
                else:
                    parent.remove(self._error_widget)
            self._error_widget = None

    def show_end(self):
        self.spinner.set_visible(False)
        self.initial_spinner.set_visible(False)
        self.end_label.set_visible(True)

    def clear(self):
        self._has_content = False
        self.end_label.set_visible(False)
        while child := self.flowbox.get_first_child():
            self.flowbox.remove(child)
        self._vadjustment.set_value(0)

    def append_child(self, widget):
        if not self._has_content:
            self._has_content = True
            self.initial_spinner.set_visible(False)
        self.flowbox.append(widget)

    def _on_scroll(self, adjustment):
        value = adjustment.get_value()
        self.scroll_up_revealer.set_reveal_child(value > _SCROLL_UP_THRESHOLD)

        if self._on_scroll_near_end:
            upper = adjustment.get_upper()
            page_size = adjustment.get_page_size()
            if value + page_size >= upper - _SCROLL_NEAR_END_OFFSET:
                self._on_scroll_near_end()

    @Gtk.Template.Callback()
    def on_scroll_up(self, _button):
        self._vadjustment.set_value(0)

    @Gtk.Template.Callback()
    def on_activated(self, _flowbox, child):
        if self._on_child_activated:
            self._on_child_activated(child)
