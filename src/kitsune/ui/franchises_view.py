# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from kitsune.models.franchise import Franchise
from kitsune.storage import search_index
from kitsune.ui.franchise_releases_view import FranchiseReleasesView
from kitsune.ui.items_grid_view import ItemsGridView
from kitsune.ui.widgets.franchise_card import FranchiseCard


def _franchise_from_index(d: dict) -> Franchise:
    """Construct Franchise from flat index dict (image is already a resolved URL)."""
    return Franchise(
        id=d.get('id', ''),
        name=d.get('name', ''),
        name_english=d.get('name_english'),
        image=d.get('image'),
        first_year=d.get('first_year'),
        last_year=d.get('last_year'),
        total_releases=d.get('total_releases'),
    )


class FranchisesView(ItemsGridView):

    @property
    def current_franchise_name(self) -> str:
        return self.current_item_name

    def _load_items(self):
        self._grid.set_spinner_visible(True)
        cached = search_index.get_franchises()
        if cached is not None:
            franchises = [_franchise_from_index(f) for f in cached]
            self._on_items_loaded(franchises, None)
        else:
            self._client.get_franchises(callback=self._on_franchises_fetched)

    def _on_franchises_fetched(self, franchises, error):
        if not error and franchises:
            search_index.update_franchises(franchises)
        self._on_items_loaded(franchises, error)

    def _create_card(self, item):
        return FranchiseCard(item)

    def _get_item_from_card(self, card):
        if isinstance(card, FranchiseCard):
            return card.franchise
        return None

    def _show_item_releases(self, item):
        releases_view = FranchiseReleasesView(
            franchise=item, client=self._client,
        )
        self._show_releases(item, releases_view)
