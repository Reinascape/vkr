# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

from kitsune.models.release import Genre
from kitsune.storage import search_index
from kitsune.ui.genre_releases_view import GenreReleasesView
from kitsune.ui.items_grid_view import ItemsGridView
from kitsune.ui.widgets.genre_card import GenreCard


def _genre_from_index(d: dict) -> Genre:
    """Construct Genre from flat index dict (image is already a resolved URL)."""
    return Genre(
        id=d.get('id', 0),
        name=d.get('name', ''),
        image=d.get('image'),
        total_releases=d.get('total_releases', 0),
    )


class GenresView(ItemsGridView):

    @property
    def current_genre_name(self) -> str:
        return self.current_item_name

    def _load_items(self):
        self._grid.set_spinner_visible(True)
        cached = search_index.get_genres()
        if cached is not None:
            genres = [_genre_from_index(g) for g in cached]
            self._on_items_loaded(genres, None)
        else:
            self._client.get_genres(callback=self._on_genres_fetched)

    def _on_genres_fetched(self, genres, error):
        if not error and genres:
            search_index.update_genres(genres)
        self._on_items_loaded(genres, error)

    def _create_card(self, item):
        return GenreCard(item)

    def _get_item_from_card(self, card):
        if isinstance(card, GenreCard):
            return card.genre
        return None

    def _show_item_releases(self, item):
        releases_view = GenreReleasesView(
            genre=item, client=self._client,
        )
        self._show_releases(item, releases_view)
