# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.ui.widgets.content_grid import ContentGrid

from gi.repository import Gtk


def test_narrow_sets_single_column():
    grid = ContentGrid()
    grid.set_narrow(True)
    assert grid.flowbox.get_min_children_per_line() == 1
    assert grid.flowbox.get_max_children_per_line() == 1


def test_wide_sets_multi_column():
    grid = ContentGrid()
    grid.set_narrow(False)
    assert grid.flowbox.get_min_children_per_line() == 2
    assert grid.flowbox.get_max_children_per_line() == 6


def test_narrow_toggle():
    grid = ContentGrid()
    grid.set_narrow(True)
    assert grid.flowbox.get_min_children_per_line() == 1
    grid.set_narrow(False)
    assert grid.flowbox.get_min_children_per_line() == 2
    assert grid.flowbox.get_max_children_per_line() == 6


def test_default_columns():
    grid = ContentGrid()
    assert grid.flowbox.get_min_children_per_line() == 2
    assert grid.flowbox.get_max_children_per_line() == 6


def test_append_child_hides_initial_spinner():
    grid = ContentGrid()
    assert grid._has_content is False
    grid.append_child(Gtk.Label(label='test'))
    assert grid._has_content is True
    assert grid.initial_spinner.get_visible() is False


def test_clear_resets_state():
    grid = ContentGrid()
    grid.append_child(Gtk.Label(label='test'))
    assert grid._has_content is True
    grid.clear()
    assert grid._has_content is False
    assert grid.end_label.get_visible() is False


def test_show_end_hides_spinner():
    grid = ContentGrid()
    grid.show_end()
    assert grid.spinner.get_visible() is False
    assert grid.initial_spinner.get_visible() is False
    assert grid.end_label.get_visible() is True


def test_scroll_callback_stored():
    grid = ContentGrid()
    cb = lambda: None
    grid.set_on_scroll_near_end(cb)
    assert grid._on_scroll_near_end is cb


def test_child_activated_callback_stored():
    grid = ContentGrid()
    cb = lambda child: None
    grid.set_on_child_activated(cb)
    assert grid._on_child_activated is cb


def test_show_error_creates_widget():
    grid = ContentGrid()
    assert grid._error_widget is None
    grid.show_error()
    assert grid._error_widget is not None


def test_clear_error_removes_widget():
    grid = ContentGrid()
    grid.show_error()
    assert grid._error_widget is not None
    grid.clear_error()
    assert grid._error_widget is None
