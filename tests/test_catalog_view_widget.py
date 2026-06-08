# SPDX-License-Identifier: GPL-3.0-or-later

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from kitsune.ui.catalog_view import CatalogView


def test_narrow_propagates_to_grid(mock_client):
    view = CatalogView(client=mock_client)
    view.set_narrow(True)
    assert view._grid.flowbox.get_min_children_per_line() == 1
    assert view._grid.flowbox.get_max_children_per_line() == 1
    view.set_narrow(False)
    assert view._grid.flowbox.get_min_children_per_line() == 2


def test_release_activated_callback_stored(mock_client):
    view = CatalogView(client=mock_client)
    cb = lambda r: None
    view.set_on_release_activated(cb)
    assert view._on_release_activated is cb


def test_initial_page_state(mock_client):
    view = CatalogView(client=mock_client)
    # _load_next_page increments _page to 1 on init
    assert view._page == 1
    assert view._last_page == 1


def test_reset_catalog_clears_state(mock_client):
    view = CatalogView(client=mock_client)
    view._page = 3
    view._reached_end = True
    view._reset_catalog()
    assert view._page == 0
    assert view._last_page == 1
    assert view._loading is False
    assert view._reached_end is False
