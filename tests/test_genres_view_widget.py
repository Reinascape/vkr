# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.ui.genres_view import GenresView
from kitsune.ui.franchises_view import FranchisesView


# --- GenresView ---

def test_genres_lazy_load_flag(mock_client):
    view = GenresView(client=mock_client, auto_load=False)
    assert view._loaded is False


def test_genres_load_sets_flag(mock_client):
    view = GenresView(client=mock_client, auto_load=False)
    view.load()
    assert view._loaded is True


def test_genres_double_load_noop(mock_client):
    calls = []
    view = GenresView(client=mock_client, auto_load=False)

    original = view._load_items
    def tracking_load():
        calls.append(True)
        original()
    view._load_items = tracking_load

    view.load()
    view.load()
    assert len(calls) == 1


def test_genres_narrow_propagates(mock_client):
    view = GenresView(client=mock_client, auto_load=False)
    view.set_narrow(True)
    assert view._narrow is True
    assert view._grid.flowbox.get_min_children_per_line() == 1


def test_genres_go_back_resets_navigation(mock_client):
    view = GenresView(client=mock_client, auto_load=False)
    assert view.in_releases is False
    assert view.current_genre_name == ''


def test_genres_navigation_callback(mock_client):
    called = []
    view = GenresView(client=mock_client, auto_load=False)
    view.set_on_navigation_changed(lambda: called.append(True))
    view.go_back()
    assert len(called) == 1


# --- FranchisesView (same structure) ---

def test_franchises_lazy_load_flag(mock_client):
    view = FranchisesView(client=mock_client, auto_load=False)
    assert view._loaded is False


def test_franchises_load_sets_flag(mock_client):
    view = FranchisesView(client=mock_client, auto_load=False)
    view.load()
    assert view._loaded is True


def test_franchises_narrow_propagates(mock_client):
    view = FranchisesView(client=mock_client, auto_load=False)
    view.set_narrow(True)
    assert view._narrow is True
    assert view._grid.flowbox.get_min_children_per_line() == 1


def test_franchises_go_back_resets(mock_client):
    view = FranchisesView(client=mock_client, auto_load=False)
    assert view.in_releases is False
    assert view.current_franchise_name == ''
