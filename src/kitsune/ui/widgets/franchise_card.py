# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gtk

from kitsune.models import Franchise
from kitsune.ui.image_cache import load_image


@Gtk.Template(resource_path='/net/armatik/Kitsune/franchise_card.ui')
class FranchiseCard(Gtk.FlowBoxChild):
    __gtype_name__ = 'KitsuneFranchiseCard'

    picture = Gtk.Template.Child()
    placeholder = Gtk.Template.Child()
    spinner = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    subtitle_label = Gtk.Template.Child()

    def __init__(self, franchise: Franchise, **kwargs):
        super().__init__(**kwargs)
        self.franchise = franchise

        self.title_label.set_label(franchise.name)

        parts = []
        if franchise.first_year:
            if franchise.last_year and franchise.last_year != franchise.first_year:
                parts.append(f'{franchise.first_year}\u2013{franchise.last_year}')
            else:
                parts.append(str(franchise.first_year))
        if franchise.total_releases:
            parts.append(f'{franchise.total_releases} ' + _('titles'))
        if parts:
            self.subtitle_label.set_label(' / '.join(parts))
            self.subtitle_label.set_visible(True)

        if franchise.image:
            load_image(franchise.image, self._on_image_loaded)
        else:
            self.spinner.set_visible(False)
            self.placeholder.set_visible(True)

    def _on_image_loaded(self, texture, error):
        self.spinner.set_visible(False)
        if texture:
            self.picture.set_paintable(texture)
        else:
            self.placeholder.set_visible(True)
