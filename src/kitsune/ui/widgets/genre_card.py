# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune.models import Genre
from kitsune.ui.image_cache import load_image


@Gtk.Template(resource_path='/net/armatik/Kitsune/genre_card.ui')
class GenreCard(Gtk.FlowBoxChild):
    __gtype_name__ = 'KitsuneGenreCard'

    picture = Gtk.Template.Child()
    placeholder = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    subtitle_label = Gtk.Template.Child()

    def __init__(self, genre: Genre, **kwargs):
        super().__init__(**kwargs)
        self.genre = genre

        self.title_label.set_label(genre.name)

        if genre.total_releases:
            self.subtitle_label.set_label(
                f'{genre.total_releases} ' + _('titles'),
            )
            self.subtitle_label.set_visible(True)

        if genre.image:
            load_image(genre.image, self._on_image_loaded)
        else:
            self.spinner.set_visible(False)
            self.placeholder.set_visible(True)

    def _on_image_loaded(self, texture, error):
        self.spinner.set_visible(False)
        if texture:
            self.picture.set_paintable(texture)
        else:
            self.placeholder.set_visible(True)
