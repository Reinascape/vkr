# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from kitsune import ADW_TRANSITION
from kitsune.ui import register_css

_T = ADW_TRANSITION
_FILTER_CSS = (
    '.filter-chip { padding: 4px 10px; min-height: 0; min-width: 0; font-size: 13px;'
    ' transition: background ' + _T + ', color ' + _T + '; }'
    ' .filter-chip:checked { background: @accent_bg_color; color: @accent_fg_color; }'
    ' .filter-panel { background: @window_bg_color;'
    ' border-left: 1px solid alpha(currentColor, 0.15); }'
)


def _types():
    return [
        ('TV', _('TV')),
        ('ONA', 'ONA'),
        ('WEB', 'WEB'),
        ('OVA', 'OVA'),
        ('OAD', 'OAD'),
        ('MOVIE', _('Movie')),
        ('DORAMA', _('Dorama')),
        ('SPECIAL', _('Special')),
    ]


def _seasons():
    return [
        ('winter', _('Winter')),
        ('spring', _('Spring')),
        ('summer', _('Summer')),
        ('autumn', _('Autumn')),
    ]


def _age_ratings():
    return [
        ('R0_PLUS', '0+'),
        ('R6_PLUS', '6+'),
        ('R12_PLUS', '12+'),
        ('R16_PLUS', '16+'),
        ('R18_PLUS', '18+'),
    ]


def _sorting():
    return [
        (None, _('By default')),
        ('FRESH_AT_DESC', _('Recently updated')),
        ('YEAR_DESC', _('Year (new ones first)')),
        ('YEAR_ASC', _('Year (old ones first)')),
        ('RATING_DESC', _('By rating')),
    ]


def _publish_statuses():
    return [
        ('IS_ONGOING', _('Ongoing')),
        ('IS_NOT_ONGOING', _('Not ongoing')),
    ]


def _production_statuses():
    return [
        ('IS_IN_PRODUCTION', _('Now dubbing')),
        ('IS_NOT_IN_PRODUCTION', _('Dubbing completed')),
    ]


@Gtk.Template(resource_path='/net/armatik/Kitsune/filter_dialog.ui')
class FilterPanel(Gtk.Box):
    __gtype_name__ = 'KitsuneFilterPanel'

    content_box = Gtk.Template.Child()
    sorting_btn = Gtk.Template.Child()
    year_from = Gtk.Template.Child()
    year_to = Gtk.Template.Child()
    reset_btn = Gtk.Template.Child()

    def __init__(self, genres: list | None = None,
                 year_range: tuple[int, int] | None = None, **kwargs):
        super().__init__(**kwargs)
        register_css(_FILTER_CSS)
        self.add_css_class('filter-panel')

        self._genres_data = genres or []
        self._year_min, self._year_max = year_range or (1990, 2026)
        self._on_apply = None
        self._on_close = None
        self._auto_apply_id = 0
        self._buttons: dict[str, dict] = {}
        self._selected_sorting: str | None = None
        self._sorting_items = _sorting()

        self._setup_year_range()
        self._setup_sorting()
        self._build_chip_sections()

    def _setup_year_range(self):
        self.year_from.set_range(self._year_min, self._year_max)
        self.year_from.set_increments(1, 5)
        self.year_from.set_value(self._year_min)
        self.year_to.set_range(self._year_min, self._year_max)
        self.year_to.set_increments(1, 5)
        self.year_to.set_value(self._year_max)

    def _setup_sorting(self):
        sorting_menu = Gio.Menu()
        for val, lbl in self._sorting_items:
            item = Gio.MenuItem.new(lbl, None)
            item.set_action_and_target_value(
                'filter.set-sorting',
                GLib.Variant.new_string(val or ''),
            )
            sorting_menu.append_item(item)

        self.sorting_btn.set_menu_model(sorting_menu)

        action_group = Gio.SimpleActionGroup()
        sorting_action = Gio.SimpleAction.new('set-sorting', GLib.VariantType.new('s'))
        sorting_action.connect('activate', self._on_sorting_selected)
        action_group.add_action(sorting_action)
        self.sorting_btn.insert_action_group('filter', action_group)

        self._update_sorting_label()

    def _build_chip_sections(self):
        categories = [
            ('types', _('Type'), _types()),
            ('seasons', _('Season'), _seasons()),
            ('age_ratings', _('Age Rating'), _age_ratings()),
            ('publish_statuses', _('Release status'), _publish_statuses()),
            ('production_statuses', _('Dubbing status'), _production_statuses()),
        ]
        if self._genres_data:
            categories.append(
                ('genres', _('Genres'), [(g['id'], g['name']) for g in self._genres_data])
            )

        for cat, title, items in categories:
            if items:
                self._add_chip_section(cat, title, items)

    def update_genres(self, genres: list):
        self._genres_data = genres
        if 'genres' not in self._buttons and genres:
            self._add_chip_section(
                'genres', _('Genres'),
                [(g['id'], g['name']) for g in genres],
            )

    def update_year_range(self, year_range: tuple[int, int]):
        self._year_min, self._year_max = year_range
        self._setup_year_range()

    def set_on_apply(self, callback):
        self._on_apply = callback

    def set_on_close(self, callback):
        self._on_close = callback

    def set_filters(self, filters: dict):
        self._suppress_auto = True
        for cat, btns in self._buttons.items():
            selected = set(filters.get(cat, []))
            for val, btn in btns.items():
                btn.set_active(val in selected)

        years = filters.get('years', {})
        self.year_from.set_value(years.get('from_year', self._year_min))
        self.year_to.set_value(years.get('to_year', self._year_max))

        self._selected_sorting = filters.get('sorting')
        self._update_sorting_label()
        self._suppress_auto = False
        self._update_reset_sensitivity()

    def get_filters(self) -> dict:
        filters = {}
        for cat, btns in self._buttons.items():
            selected = [v for v, b in btns.items() if b.get_active()]
            if selected:
                filters[cat] = selected

        if self._selected_sorting:
            filters['sorting'] = self._selected_sorting

        year_from = int(self.year_from.get_value())
        year_to = int(self.year_to.get_value())
        if year_from != self._year_min or year_to != self._year_max:
            filters['years'] = {'from_year': year_from, 'to_year': year_to}
        return filters

    def _has_any_selection(self) -> bool:
        for btns in self._buttons.values():
            for btn in btns.values():
                if btn.get_active():
                    return True
        if self._selected_sorting is not None:
            return True
        if int(self.year_from.get_value()) != self._year_min:
            return True
        if int(self.year_to.get_value()) != self._year_max:
            return True
        return False

    def _update_reset_sensitivity(self):
        self.reset_btn.set_sensitive(self._has_any_selection())

    def _update_sorting_label(self):
        label = self._sorting_items[0][1]
        for val, lbl in self._sorting_items:
            if val == self._selected_sorting:
                label = lbl
                break
        self.sorting_btn.set_label(label)

    def _add_chip_section(self, cat: str, title: str, items: list):
        self._buttons[cat] = {}

        label = Gtk.Label(
            label=title, xalign=0, css_classes=['heading'], margin_top=2,
        )
        self.content_box.append(label)

        wrap = Adw.WrapBox(line_spacing=6, child_spacing=6)
        for value, item_label in items:
            btn = Gtk.ToggleButton(
                label=item_label,
                css_classes=['pill', 'filter-chip'],
            )
            btn.connect('toggled', self._on_any_changed)
            wrap.append(btn)
            self._buttons[cat][value] = btn
        self.content_box.append(wrap)

    def _schedule_auto_apply(self):
        if getattr(self, '_suppress_auto', False):
            return
        if self._auto_apply_id:
            GLib.source_remove(self._auto_apply_id)
        self._auto_apply_id = GLib.timeout_add(1000, self._do_auto_apply)

    def _do_auto_apply(self):
        self._auto_apply_id = 0
        if self._on_apply:
            self._on_apply(self.get_filters())
        return GLib.SOURCE_REMOVE

    def _on_sorting_selected(self, action, variant):
        val = variant.get_string()
        self._selected_sorting = val if val else None
        self._update_sorting_label()
        self._update_reset_sensitivity()
        self._schedule_auto_apply()

    @Gtk.Template.Callback()
    def on_year_changed(self, spin):
        year_from = int(self.year_from.get_value())
        year_to = int(self.year_to.get_value())
        if spin == self.year_from and year_from > year_to:
            self.year_to.set_value(year_from)
        elif spin == self.year_to and year_to < year_from:
            self.year_from.set_value(year_to)
        self._update_reset_sensitivity()
        self._schedule_auto_apply()

    def _on_any_changed(self, *args):
        self._update_reset_sensitivity()
        self._schedule_auto_apply()

    @Gtk.Template.Callback()
    def on_close_clicked(self, _button):
        if self._on_close:
            self._on_close()

    @Gtk.Template.Callback()
    def on_reset(self, _button):
        for btns in self._buttons.values():
            for btn in btns.values():
                btn.set_active(False)
        self._selected_sorting = None
        self._update_sorting_label()
        self.year_from.set_value(self._year_min)
        self.year_to.set_value(self._year_max)
        # Apply reset immediately
        if self._auto_apply_id:
            GLib.source_remove(self._auto_apply_id)
            self._auto_apply_id = 0
        if self._on_apply:
            self._on_apply(self.get_filters())
