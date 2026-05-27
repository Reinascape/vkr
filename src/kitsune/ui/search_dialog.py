# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from kitsune.api import AniLibriaClient
from kitsune.storage import search_index, tags_store, watch_positions, release_cache
from kitsune.storage.watch_positions import is_completed
from kitsune.models import Release
from kitsune.models.release import Genre, ReleaseName
from kitsune.models.franchise import Franchise
from kitsune import ADW_TRANSITION
from kitsune.ui import register_css

log = logging.getLogger('kitsune.search_dialog')

_T = ADW_TRANSITION

_SEARCH_CSS = (
    '.search-tab { padding: 4px 10px; min-height: 0; min-width: 0;'
    ' font-size: 12px; border-radius: 99px;'
    ' transition: background ' + _T + ', color ' + _T + '; }'
    ' .search-tab:checked { background: @accent_bg_color;'
    ' color: @accent_fg_color; }'
    ' .search-result { background: alpha(currentColor, 0.04);'
    ' border-radius: 12px; padding: 10px; margin: 3px 6px;'
    ' transition: background ' + _T + '; }'
    ' .search-poster { border-radius: 8px; }'
    ' .search-section-header { margin: 8px 12px 2px; }'
    ' .search-episode-bar { background: @accent_bg_color;'
    ' border-radius: 8px; padding: 6px 10px; margin-top: 4px;'
    ' transition: background ' + _T + '; }'
    ' .search-episode-bar label { color: @accent_fg_color; }'
    ' .search-episode-bar image { color: @accent_fg_color; }'
    ' .search-episode-btn { margin: 0; padding: 0;'
    ' background: none; outline: none; border: none;'
    ' box-shadow: none; }'
    ' .search-episode-btn:hover { background: none; }'
    ' .search-episode-btn:focus { outline: none; background: none; }'
    ' .search-episode-btn:active { background: none; }'
    ' .search-episode-btn:hover .search-episode-bar {'
    ' background: alpha(@accent_bg_color, 0.85); }'
    ' .search-episode-btn:active .search-episode-bar {'
    ' background: alpha(@accent_bg_color, 0.7); }'
    ' .search-dialog-list row { background: none; padding: 0;'
    ' transition: background ' + _T + '; }'
    ' .search-dialog-list row:hover .search-result {'
    ' background: alpha(currentColor, 0.07); }'
    ' .search-dialog-list row:selected .search-result,'
    ' .search-dialog-list row:focus .search-result {'
    ' background: alpha(@accent_bg_color, 0.12); }'
)


# xgettext: these must be plain _() calls, not lambdas
_ALL_CATEGORY_NAMES = {
    'anime': 'Anime',
    'genres': 'Genres',
    'franchises': 'Franchises',
    'tags': 'Tags',
}
# Ensure xgettext sees these strings:
_CATEGORY_GETTEXT = [_('Anime'), _('Genres'), _('Franchises'), _('Tags')]

_DEFAULT_ORDER = ['anime', 'genres', 'franchises', 'tags']


def _categories(settings=None):
    """Return category list ordered by GSettings preference."""
    order = _DEFAULT_ORDER
    if settings:
        import json
        try:
            raw = settings.get_string('search-category-order')
            parsed = json.loads(raw)
            if isinstance(parsed, list):
                order = parsed
        except (json.JSONDecodeError, ValueError):
            pass
    return [(cid, _(_ALL_CATEGORY_NAMES[cid])) for cid in order if cid in _ALL_CATEGORY_NAMES]


@Gtk.Template(resource_path='/net/armatik/Kitsune/search_dialog.ui')
class SearchDialog(Adw.Dialog):
    __gtype_name__ = 'KitsuneSearchDialog'

    search_entry = Gtk.Template.Child()
    tabs_box = Gtk.Template.Child()
    separator = Gtk.Template.Child()
    stack = Gtk.Template.Child()
    scrolled = Gtk.Template.Child()
    listbox = Gtk.Template.Child()
    empty_subtitle = Gtk.Template.Child()

    def __init__(self, client: AniLibriaClient, **kwargs):
        super().__init__(**kwargs)
        register_css(_SEARCH_CSS)
        self._client = client
        self._settings = Gio.Settings(schema_id='net.armatik.Kitsune')
        self._cancellable = None
        self._debounce_id = 0
        self._on_release_activated = None
        self._on_episode_play = None
        self._on_genre_activated = None
        self._on_franchise_activated = None
        self._on_tag_activated = None
        self._tab_buttons: dict[str, Gtk.ToggleButton] = {}
        self._section_indices: dict[str, int] = {}
        self._results: dict[str, list] = {}
        self._suppress_tab_toggle = False
        self._scroll_handler_id = 0
        self._closed_by_navigation = False
        self._refreshed_ids: set[int] = set()

        self.empty_subtitle.set_label(
            _('Search anime, genres, franchises and tags')
        )
        self._build_tabs()
        self._setup_keyboard()
        self.connect('closed', self._on_closed)
        self._ensure_index_populated()
        # Hide tabs and set correct spacing on initial open
        self.tabs_box.set_visible(False)
        search_row = self.search_entry.get_parent()
        if search_row:
            search_row.set_margin_bottom(10)

    # --- Public setters ---

    def set_on_release_activated(self, cb):
        self._on_release_activated = cb

    def set_on_episode_play(self, cb):
        self._on_episode_play = cb

    def set_on_genre_activated(self, cb):
        self._on_genre_activated = cb

    def set_on_franchise_activated(self, cb):
        self._on_franchise_activated = cb

    def set_on_tag_activated(self, cb):
        self._on_tag_activated = cb

    # --- Preload index ---

    def _ensure_index_populated(self):
        """Load genres/franchises into index if not cached yet."""
        if search_index.get_genres() is None:
            self._client.get_genres(callback=self._on_preload_genres)
        if search_index.get_franchises() is None:
            self._client.get_franchises(callback=self._on_preload_franchises)

    def _on_preload_genres(self, genres, error):
        if not error and genres:
            search_index.update_genres(genres)

    def _on_preload_franchises(self, franchises, error):
        if not error and franchises:
            search_index.update_franchises(franchises)

    # --- Tabs ---

    def _build_tabs(self):
        for cat_id, label in _categories(self._settings):
            btn = Gtk.ToggleButton(
                label=label,
                css_classes=['pill', 'search-tab'],
            )
            btn.connect('toggled', self._on_tab_toggled, cat_id)
            self.tabs_box.append(btn)
            self._tab_buttons[cat_id] = btn
            btn.set_visible(False)

    def _on_tab_toggled(self, btn, cat_id):
        if self._suppress_tab_toggle:
            return
        if not btn.get_active():
            return
        self._set_active_tab(cat_id)
        idx = self._section_indices.get(cat_id)
        if idx is not None:
            row = self.listbox.get_row_at_index(idx)
            if row:
                self._smooth_scroll_to_row(row)

    def _smooth_scroll_to_row(self, row):
        """Animate scroll so that row appears at the top."""
        adj = self.scrolled.get_vadjustment()
        if adj is None:
            return
        try:
            coords = row.translate_coordinates(self.listbox, 0, 0)
        except Exception:
            return
        if not coords:
            return
        target_y = coords[-1]
        self._animate_scroll(adj, adj.get_value(), target_y)

    def _animate_scroll(self, adj, start, end, duration_ms=200):
        """Smooth scroll animation using GLib.timeout_add."""
        if abs(end - start) < 1:
            adj.set_value(end)
            return
        steps = max(1, duration_ms // 16)  # ~60fps
        step_i = [0]

        def _tick():
            step_i[0] += 1
            t = min(step_i[0] / steps, 1.0)
            # Ease-out cubic
            t_ease = 1 - (1 - t) ** 3
            adj.set_value(start + (end - start) * t_ease)
            if t >= 1.0:
                self._scroll_anim_id = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._scroll_anim_id = GLib.timeout_add(16, _tick)

    def _setup_scroll_tracking(self):
        """Track scroll position to highlight the active category tab."""
        adj = self.scrolled.get_vadjustment()
        if not adj:
            log.debug('scroll_tracking: no vadjustment')
            return
        if self._scroll_handler_id:
            adj.disconnect(self._scroll_handler_id)
        self._scroll_handler_id = adj.connect(
            'value-changed', self._on_scroll_changed)

    def _on_scroll_changed(self, adj):
        """Highlight tab whose section occupies the bottom of visible area."""
        if not self._section_indices:
            return
        scroll_bottom = adj.get_value() + adj.get_page_size()
        active_cat = None
        for cat_id, idx in self._section_indices.items():
            row = self.listbox.get_row_at_index(idx)
            if row:
                try:
                    coords = row.translate_coordinates(self.listbox, 0, 0)
                except Exception:
                    continue
                if coords and coords[-1] < scroll_bottom:
                    active_cat = cat_id
        if active_cat:
            self._set_active_tab(active_cat)

    def _set_active_tab(self, cat_id):
        """Highlight a tab without triggering scroll."""
        self._suppress_tab_toggle = True
        for cid, btn in self._tab_buttons.items():
            btn.set_active(cid == cat_id)
        self._suppress_tab_toggle = False

    def _update_tabs(self):
        any_visible = False
        first_visible = None
        for cat_id, btn in self._tab_buttons.items():
            visible = bool(self._results.get(cat_id))
            btn.set_visible(visible)
            if visible:
                any_visible = True
                if first_visible is None:
                    first_visible = cat_id
        self.tabs_box.set_visible(any_visible)
        # Highlight first tab
        if first_visible:
            self._set_active_tab(first_visible)
        # When tabs hidden: search row needs bottom margin before separator
        search_row = self.search_entry.get_parent()
        if search_row:
            search_row.set_margin_bottom(0 if any_visible else 10)

    # --- Search ---

    @Gtk.Template.Callback()
    def on_cancel_clicked(self, _button):
        self.close()

    @Gtk.Template.Callback()
    def on_search_changed(self, entry):
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0

        query = entry.get_text().strip()
        if len(query) < 2:
            self.stack.set_visible_child_name('empty')
            self._clear_results()
            return

        self._do_local_search(query)
        self._render_results()
        self._debounce_id = GLib.timeout_add(300, self._do_api_search, query)

    def _do_local_search(self, query):
        self._results['anime'] = search_index.search_releases(query)
        self._results['genres'] = search_index.search_genres(query)
        self._results['franchises'] = search_index.search_franchises(query)

        all_tags = tags_store.get_all_tags()
        q = query.casefold()
        self._results['tags'] = [
            t for t in all_tags if q in t['name'].casefold()
        ]

        # Smart tag filter: words in query that match tag names
        # find releases at the intersection of matched tags
        self._enrich_anime_from_tags(query, all_tags)

    def _enrich_anime_from_tags(self, query, all_tags):
        """If query words match tag names, add tagged releases to anime results.

        Single word matching a tag: add all releases from that tag.
        Multiple words each matching a tag: add releases at intersection.
        """
        words = query.casefold().split()
        if not words:
            return

        # Find tags matching each word (substring match)
        matched_tag_sets = []
        for word in words:
            if len(word) < 2:
                continue
            word_release_ids = set()
            for tag in all_tags:
                if word in tag['name'].casefold():
                    word_release_ids.update(tag.get('releases', []))
            if word_release_ids:
                matched_tag_sets.append(word_release_ids)

        if not matched_tag_sets:
            return

        # Intersect all matched sets (releases must match ALL tag-words)
        tag_release_ids = matched_tag_sets[0]
        for s in matched_tag_sets[1:]:
            tag_release_ids &= s

        if not tag_release_ids:
            return

        # Add releases not already in anime results
        existing_ids = {r['id'] for r in self._results.get('anime', [])}
        idx = search_index.load()
        for rid in tag_release_ids:
            if rid in existing_ids:
                continue
            meta = idx['releases'].get(str(rid))
            if meta:
                self._results.setdefault('anime', []).append(
                    {**meta, 'id': rid})

    def _do_api_search(self, query):
        self._debounce_id = 0
        if getattr(self._client, '_offline', False):
            return GLib.SOURCE_REMOVE

        if self._cancellable:
            self._cancellable.cancel()
        self._cancellable = Gio.Cancellable()

        self._client.search_releases(
            query=query,
            callback=self._on_api_results,
            cancellable=self._cancellable,
        )
        return GLib.SOURCE_REMOVE

    def _on_api_results(self, releases, error):
        if error or not releases:
            return
        if not self.get_visible():
            return

        # Build lookup from current local/index entries
        local_map = {r['id']: r for r in self._results.get('anime', [])}

        api_entries = []
        for release in releases:
            genres = [g.id for g in release.genres]
            # API search may return releases without genres — preserve from index
            if not genres:
                local = local_map.get(release.id)
                if local:
                    genres = local.get('genres', [])
                else:
                    meta = search_index.get_release_meta(release.id)
                    if meta:
                        genres = meta.get('genres', [])
            entry = {
                'id': release.id,
                'main': release.name.main,
                'english': release.name.english,
                'alternative': release.name.alternative,
                'description': release.description,
                'poster_preview': release.poster_preview,
                'type': release.type,
                'year': release.year,
                'is_ongoing': release.is_ongoing,
                'is_adult': release.is_adult,
                'episodes_total': release.episodes_total,
                'genres': genres,
            }
            api_entries.append(entry)

        api_ids = {e['id'] for e in api_entries}
        local_only = [
            r for r in self._results.get('anime', [])
            if r['id'] not in api_ids
        ]
        self._results['anime'] = api_entries + local_only
        self._render_results()

        # Lazily cache releases not in index (fetches full data with genres)
        uncached = [e['id'] for e in api_entries
                    if search_index.get_release_meta(e['id']) is None]
        if uncached:
            self._lazy_pending = len(uncached)
            for rid in uncached:
                self._client.get_release_raw(
                    str(rid),
                    callback=lambda data, err, r=rid:
                        self._on_lazy_cached(r, data, err),
                    cancellable=self._cancellable,
                )

    def _on_lazy_cached(self, release_id, data, error):
        if not error and data:
            release_cache.save(release_id, data)
        self._lazy_pending = getattr(self, '_lazy_pending', 1) - 1
        if not self.get_visible() or self._lazy_pending > 0:
            return
        # All lazy fetches done — update genres in one batch re-render
        changed = False
        for entry in self._results.get('anime', []):
            if not entry.get('genres'):
                meta = search_index.get_release_meta(entry['id'])
                if meta and meta.get('genres'):
                    entry['genres'] = meta['genres']
                    changed = True
        if changed:
            self._render_results()

    def _clear_results(self):
        self._results = {}
        while row := self.listbox.get_first_child():
            self.listbox.remove(row)
        self._section_indices = {}
        self._update_tabs()

    # --- Rendering ---

    def _render_results(self):
        while row := self.listbox.get_first_child():
            self.listbox.remove(row)
        self._section_indices = {}

        # Pre-load shared data once per render (avoid N+1 disk reads)
        cached_genres = search_index.get_genres()
        self._genre_map = {g['id']: g['name'] for g in cached_genres} if cached_genres else {}
        self._all_tags = tags_store.get_all_tags()

        total = 0
        idx = 0
        for cat_id, label in _categories(self._settings):
            items = self._results.get(cat_id, [])
            if not items:
                continue
            header = self._make_section_header(label)
            self.listbox.append(header)
            self._section_indices[cat_id] = idx
            idx += 1
            for item in items:
                row = self._make_row(cat_id, item)
                self.listbox.append(row)
                idx += 1
                total += 1

        self._update_tabs()
        if total > 0:
            self.stack.set_visible_child_name('results')
            log.debug('render: %d results across %d categories',
                      total, len(self._section_indices))
            self._setup_scroll_tracking()
            if log.isEnabledFor(logging.DEBUG):
                GLib.idle_add(self._debug_log_sizes)
            # Check if ongoing releases need API refresh
            self._check_ongoing_refresh()
        else:
            self.stack.set_visible_child_name('no-results')

    def _check_ongoing_refresh(self):
        """For ongoing releases where all episodes are watched, refresh from API."""
        anime = self._results.get('anime', [])
        for entry in anime:
            rid = entry.get('id')
            if not rid or not entry.get('is_ongoing'):
                continue
            if rid in self._refreshed_ids:
                continue
            positions = watch_positions.get_all_for_release(rid)
            if not positions:
                continue
            episodes = search_index.get_episodes(rid)
            if not episodes:
                continue
            all_completed = True
            for ep in episodes:
                ordinal = ep.get('ordinal', 0)
                pos = positions.get(ordinal, 0)
                if pos == 0:
                    all_completed = False
                    break
                if not is_completed(pos, ep.get('duration')):
                    all_completed = False
                    break
            if all_completed:
                self._refreshed_ids.add(rid)
                log.debug('ongoing refresh: fetching release %d', rid)
                self._client.get_release_raw(
                    str(rid),
                    callback=lambda data, err, r=rid:
                        self._on_ongoing_refreshed(r, data, err),
                    cancellable=self._cancellable,
                )

    def _on_ongoing_refreshed(self, release_id, data, error):
        if error or not data:
            return
        release_cache.save(release_id, data)
        if not self.get_visible():
            return
        meta = search_index.get_release_meta(release_id)
        if meta:
            for i, entry in enumerate(self._results.get('anime', [])):
                if entry.get('id') == release_id:
                    self._results['anime'][i] = {**meta, 'id': release_id}
                    break
        self._render_results()

    def _debug_log_sizes(self):
        idx = 0
        row = self.listbox.get_row_at_index(idx)
        while row:
            child = row.get_child()
            stype = getattr(row, '_search_type', 'header')
            log.debug('row[%d] type=%s row_alloc=(%d,%d) child_alloc=(%d,%d)',
                      idx, stype,
                      row.get_allocated_width(), row.get_allocated_height(),
                      child.get_allocated_width() if child else 0,
                      child.get_allocated_height() if child else 0)
            # Find thumbnails in the row
            if child:
                c = child.get_first_child() if hasattr(child, 'get_first_child') else None
                while c:
                    if isinstance(c, Gtk.Fixed):
                        log.debug('  Fixed: req=(%d,%d) alloc=(%d,%d)',
                                  c.get_size_request()[0], c.get_size_request()[1],
                                  c.get_allocated_width(), c.get_allocated_height())
                        pic = c.get_first_child()
                        if pic:
                            log.debug('  Picture: req=(%d,%d) alloc=(%d,%d) type=%s',
                                      pic.get_size_request()[0], pic.get_size_request()[1],
                                      pic.get_allocated_width(), pic.get_allocated_height(),
                                      type(pic).__name__)
                    c = c.get_next_sibling() if hasattr(c, 'get_next_sibling') else None
            idx += 1
            row = self.listbox.get_row_at_index(idx)
        return GLib.SOURCE_REMOVE

    def _make_section_header(self, label):
        lbl = Gtk.Label(
            label=label, xalign=0,
            css_classes=['heading', 'dim-label', 'search-section-header'],
        )
        row = Gtk.ListBoxRow(child=lbl, activatable=False, selectable=False)
        row.set_can_focus(False)
        return row

    def _make_row(self, cat_id, item):
        if cat_id == 'anime':
            return self._make_anime_row(item)
        elif cat_id == 'genres':
            return self._make_genre_row(item)
        elif cat_id == 'franchises':
            return self._make_franchise_row(item)
        elif cat_id == 'tags':
            return self._make_tag_row(item)
        return Gtk.ListBoxRow()

    # --- Anime row ---

    def _make_anime_row(self, entry):
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        outer.add_css_class('search-result')

        # --- Top: poster + info ---
        top = Gtk.Box(spacing=12)

        top.append(self._make_fixed_thumbnail(
            entry.get('poster_preview'), 56, 80,
            is_adult=entry.get('is_adult', False)))

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2,
                        hexpand=True, valign=Gtk.Align.CENTER)

        info.append(Gtk.Label(
            label=entry.get('main', ''), xalign=0,
            ellipsize=3, lines=2, wrap=True,
            css_classes=['heading'],
        ))

        parts = []
        if entry.get('type'):
            parts.append(entry['type'])
        if entry.get('year'):
            parts.append(str(entry['year']))
        ep_total = entry.get('episodes_total')
        if ep_total:
            parts.append(f'{ep_total} ' + _('ep.'))
        if entry.get('is_ongoing'):
            parts.append(_('ongoing'))
        if parts:
            info.append(Gtk.Label(
                label=' · '.join(parts), xalign=0,
                ellipsize=3,
                css_classes=['dim-label', 'caption'],
            ))

        # Genre label (single ellipsized line)
        genre_ids = entry.get('genres', [])
        if genre_ids and self._genre_map:
            names = [self._genre_map[gid] for gid in genre_ids[:3]
                     if gid in self._genre_map]
            if names:
                info.append(Gtk.Label(
                    label=' · '.join(names), xalign=0,
                    ellipsize=3,
                    css_classes=['caption', 'dim-label'],
                    margin_top=2,
                ))

        # Tag badges (use pre-loaded all_tags)
        release_id = entry.get('id')
        if release_id and self._all_tags:
            tags = [t for t in self._all_tags
                    if release_id in t.get('releases', [])]
            if tags:
                tags_box = Gtk.Box(spacing=4, margin_top=2)
                for tag in tags[:4]:
                    if tag['icon_type'] == 'emoji':
                        tags_box.append(Gtk.Label(label=tag['icon_value']))
                    elif tag['icon_type'] == 'symbolic':
                        img = Gtk.Image.new_from_icon_name(tag['icon_value'])
                        img.set_pixel_size(16)
                        img.set_valign(Gtk.Align.CENTER)
                        if tag.get('color'):
                            css = Gtk.CssProvider()
                            css.load_from_string(
                                f"image {{ color: {tag['color']}; }}"
                            )
                            img.get_style_context().add_provider(
                                css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                            )
                        tags_box.append(img)
                    else:
                        from kitsune.ui.widgets.tag_card import create_color_circle
                        tags_box.append(create_color_circle(
                            tag['icon_value'], 16))
                if len(tags) > 4:
                    tags_box.append(Gtk.Label(
                        label=f'+{len(tags) - 4}',
                        css_classes=['dim-label', 'caption'],
                    ))
                info.append(tags_box)

        top.append(info)

        # Load positions and episodes once per row
        positions = {}
        idx_eps = []
        if release_id:
            positions = watch_positions.get_all_for_release(release_id)
            idx_eps = search_index.get_episodes(release_id) or []

        # Progress label (right-aligned)
        if release_id and positions and ep_total and ep_total > 0:
            dur_map = {e['ordinal']: e.get('duration') for e in idx_eps}
            completed = sum(1 for o, p in positions.items()
                            if is_completed(p, dur_map.get(o, 0)))
            all_done = completed >= ep_total
            progress_lbl = Gtk.Label(
                label=f'{completed} / {ep_total}' + (' ✓' if all_done else ''),
                css_classes=['caption', 'dim-label'],
                valign=Gtk.Align.START,
            )
            top.append(progress_lbl)

        outer.append(top)

        # --- Bottom: episode block (below poster+info) ---
        if release_id:
            self._add_episode_block(outer, release_id, ep_total, entry,
                                     positions, idx_eps)

        row = Gtk.ListBoxRow(child=outer)
        row._search_type = 'anime'
        row._search_data = entry
        return row

    def _add_episode_block(self, outer, release_id, ep_total, entry,
                            positions=None, episodes=None):
        """Add episode continue/new block below the main card content."""
        if positions is None:
            positions = watch_positions.get_all_for_release(release_id)
        if not positions:
            return
        if episodes is None:
            episodes = search_index.get_episodes(release_id) or []

        ep_durations = {}
        for ep in episodes:
            d = ep.get('duration')
            if d:
                ep_durations[ep.get('ordinal', 0)] = d

        completed_ordinals = set()
        watching_ordinal = None
        watching_position = 0

        for ordinal, pos in positions.items():
            duration = ep_durations.get(ordinal, 0)
            if is_completed(pos, duration):
                completed_ordinals.add(ordinal)
            elif pos > 0:
                if watching_ordinal is None or ordinal > watching_ordinal:
                    watching_ordinal = ordinal
                    watching_position = pos

        max_completed = max(completed_ordinals, default=0)

        new_ep = self._find_new_episode(
            episodes, max_completed, positions, completed_ordinals)

        # Priority: continue > new episode
        if watching_ordinal is not None:
            ep_data = next((e for e in episodes
                            if e.get('ordinal') == watching_ordinal), None)
            self._add_continue_block(outer, release_id, watching_ordinal,
                                     watching_position, ep_data, entry)
        elif new_ep:
            self._add_new_episode_block(outer, release_id, new_ep, entry)

    @staticmethod
    def _find_new_episode(episodes, max_completed, positions,
                           completed_ordinals):
        """Find first unwatched episode after max completed ordinal."""
        sorted_eps = sorted(episodes, key=lambda e: e.get('sort_order', 0))
        for ep in sorted_eps:
            ordinal = ep.get('ordinal', 0)
            if ordinal <= max_completed:
                continue
            if ordinal in completed_ordinals:
                continue
            if ordinal in positions:
                continue
            return ep
        return None

    def _add_continue_block(self, outer, release_id, ordinal,
                             position, ep_data, entry):
        duration = ep_data.get('duration') if ep_data else None

        bar = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        bar.add_css_class('search-episode-bar')

        ordinal_str = int(ordinal) if ordinal == int(ordinal) else ordinal
        bar.append(Gtk.Label(
            label=_('Episode') + f' {ordinal_str}',
            css_classes=['caption'], hexpand=True, xalign=0,
        ))
        if duration and duration > 0:
            pos_str = f'{int(position) // 60}:{int(position) % 60:02d}'
            dur_str = f'{duration // 60}:{duration % 60:02d}'
            bar.append(Gtk.Label(
                label=f'{pos_str} / {dur_str}',
                css_classes=['caption'],
            ))
        bar.append(Gtk.Image(
            icon_name='go-next-symbolic', pixel_size=16,
        ))

        btn = Gtk.Button(css_classes=['flat', 'search-episode-btn'], child=bar)
        btn.set_overflow(Gtk.Overflow.HIDDEN)
        btn.connect('clicked', self._on_episode_clicked,
                     release_id, ordinal, entry)
        outer.append(btn)

    def _add_new_episode_block(self, outer, release_id, ep_data, entry):
        ordinal = ep_data.get('ordinal', 0)

        bar = Gtk.Box(spacing=8, valign=Gtk.Align.CENTER)
        bar.add_css_class('search-episode-bar')

        ordinal_str = int(ordinal) if ordinal == int(ordinal) else ordinal
        bar.append(Gtk.Label(
            label=_('New episode') + f' {ordinal_str}',
            css_classes=['caption'], hexpand=True, xalign=0,
        ))
        bar.append(Gtk.Image(
            icon_name='go-next-symbolic', pixel_size=16,
        ))

        btn = Gtk.Button(css_classes=['flat', 'search-episode-btn'], child=bar)
        btn.set_overflow(Gtk.Overflow.HIDDEN)
        btn.connect('clicked', self._on_episode_clicked,
                     release_id, ordinal, entry)
        outer.append(btn)

    # --- Helpers ---

    def _make_fixed_thumbnail(self, url, w, h=None, is_adult=False):
        """Create a fixed-size thumbnail with crop-to-fill.

        Uses Adw.Clamp for width and explicit height on inner box.
        Adult releases get a CSS blur on the picture; the surrounding
        `inner` box (overflow: hidden + rounded corners) clips the
        bloom so the blur stays within the visible thumbnail.
        """
        if h is None:
            h = w

        # Inner box: holds the picture, has overflow hidden for crop + border-radius
        inner = Gtk.Box(
            hexpand=True, vexpand=True,
        )
        inner.set_size_request(w, h)
        inner.set_overflow(Gtk.Overflow.HIDDEN)
        inner.add_css_class('search-poster')

        if url:
            from kitsune.ui.image_cache import load_image
            from kitsune.ui import apply_adult_blur
            picture = Gtk.Picture(
                content_fit=Gtk.ContentFit.COVER,
                can_shrink=True,
                hexpand=True, vexpand=True,
            )
            apply_adult_blur(picture, is_adult)
            inner.append(picture)
            load_image(url, lambda tex, err, p=picture:
                       p.set_paintable(tex) if tex else None,
                       category='posters')
        else:
            inner.append(Gtk.Image(
                icon_name='net.armatik.Kitsune.image-missing-symbolic',
                pixel_size=int(min(w, h) * 0.45), opacity=0.3,
                halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                hexpand=True, vexpand=True,
            ))

        # Adw.Clamp constrains maximum width — this actually works in GTK4
        clamp = Adw.Clamp(
            maximum_size=w,
            tightening_threshold=w,
            child=inner,
            hexpand=False, vexpand=False,
            halign=Gtk.Align.START, valign=Gtk.Align.START,
        )
        clamp.set_size_request(w, h)
        return clamp

    # --- Genre / Franchise / Tag rows ---

    def _make_genre_row(self, item):
        box = Gtk.Box(spacing=12)
        box.add_css_class('search-result')
        box.append(self._make_fixed_thumbnail(item.get('image'), 56, 80))
        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             hexpand=True, valign=Gtk.Align.CENTER)
        label_box.append(Gtk.Label(
            label=item.get('name', ''), xalign=0,
            ellipsize=3, lines=2, wrap=True,
        ))
        box.append(label_box)
        count = item.get('total_releases', 0)
        if count:
            box.append(Gtk.Label(
                label=str(count), css_classes=['dim-label', 'caption'],
                valign=Gtk.Align.CENTER,
            ))
        row = Gtk.ListBoxRow(child=box)
        row._search_type = 'genre'
        row._search_data = item
        return row

    def _make_franchise_row(self, item):
        box = Gtk.Box(spacing=12)
        box.add_css_class('search-result')
        box.append(self._make_fixed_thumbnail(item.get('image'), 56, 80))
        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             hexpand=True, valign=Gtk.Align.CENTER)
        label_box.append(Gtk.Label(
            label=item.get('name', ''), xalign=0,
            ellipsize=3, lines=2, wrap=True,
        ))
        parts = []
        fy = item.get('first_year')
        ly = item.get('last_year')
        if fy and ly:
            parts.append(f'{fy}–{ly}')
        tr = item.get('total_releases')
        if tr:
            parts.append(f'{tr} ' + _('titles'))
        if parts:
            label_box.append(Gtk.Label(
                label=' · '.join(parts), xalign=0,
                css_classes=['dim-label', 'caption'],
            ))
        box.append(label_box)
        row = Gtk.ListBoxRow(child=box)
        row._search_type = 'franchise'
        row._search_data = item
        return row

    def _make_tag_row(self, item):
        box = Gtk.Box(spacing=10)
        box.add_css_class('search-result')
        if item.get('icon_type') == 'emoji':
            box.append(Gtk.Label(label=item.get('icon_value', ''),
                                  width_request=28))
        elif item.get('icon_type') == 'symbolic':
            img = Gtk.Image.new_from_icon_name(item.get('icon_value', ''))
            img.set_pixel_size(20)
            img.set_size_request(28, -1)
            img.set_valign(Gtk.Align.CENTER)
            if item.get('color'):
                css = Gtk.CssProvider()
                css.load_from_string(
                    f"image {{ color: {item['color']}; }}"
                )
                img.get_style_context().add_provider(
                    css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
                )
            box.append(img)
        else:
            from kitsune.ui.widgets.tag_card import create_color_circle
            box.append(create_color_circle(item.get('icon_value', 'blue'), 28))
        label_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL,
                             hexpand=True, valign=Gtk.Align.CENTER)
        label_box.append(Gtk.Label(
            label=item.get('name', ''), xalign=0,
            ellipsize=3, lines=2, wrap=True,
        ))
        box.append(label_box)
        count = len(item.get('releases', []))
        if count:
            box.append(Gtk.Label(
                label=str(count), css_classes=['dim-label', 'caption'],
            ))
        row = Gtk.ListBoxRow(child=box)
        row._search_type = 'tag'
        row._search_data = item
        return row

    # --- Activation ---

    @Gtk.Template.Callback()
    def on_row_activated(self, _listbox, row):
        if not hasattr(row, '_search_type'):
            return
        self._closed_by_navigation = True
        self.close()
        stype = row._search_type
        data = row._search_data
        if stype == 'anime':
            self._activate_anime(data)
        elif stype == 'genre':
            self._activate_genre(data)
        elif stype == 'franchise':
            self._activate_franchise(data)
        elif stype == 'tag':
            self._activate_tag(data)

    def _activate_anime(self, entry):
        release = Release(
            id=entry['id'],
            name=ReleaseName(
                main=entry.get('main', ''),
                english=entry.get('english'),
                alternative=entry.get('alternative'),
            ),
            alias='',
            type=entry.get('type', ''),
            year=entry.get('year', 0),
            is_ongoing=entry.get('is_ongoing', False),
        )
        if self._on_release_activated:
            self._on_release_activated(release)

    def _activate_genre(self, item):
        genre = Genre(
            id=item['id'], name=item['name'],
            image=item.get('image'),
            total_releases=item.get('total_releases', 0),
        )
        if self._on_genre_activated:
            self._on_genre_activated(genre)

    def _activate_franchise(self, item):
        franchise = Franchise(
            id=item['id'], name=item['name'],
            name_english=item.get('name_english'),
            image=item.get('image'),
            first_year=item.get('first_year'),
            last_year=item.get('last_year'),
            total_releases=item.get('total_releases'),
        )
        if self._on_franchise_activated:
            self._on_franchise_activated(franchise)

    def _activate_tag(self, item):
        if self._on_tag_activated:
            self._on_tag_activated(item)

    def _on_episode_clicked(self, _btn, release_id, ordinal, entry):
        self._closed_by_navigation = True
        self.close()
        if not self._on_episode_play:
            return
        raw = release_cache.get(release_id)
        if not raw:
            self._activate_anime(entry)
            return
        release = Release.from_dict(raw)
        # First push release page, then player on top —
        # so "back" from player lands on release page
        if self._on_release_activated:
            self._on_release_activated(release)
        for ep in release.episodes:
            if ep.ordinal == ordinal:
                self._on_episode_play(release, ep)
                return

    # --- Keyboard ---

    def _setup_keyboard(self):
        ctrl = Gtk.EventControllerKey()
        ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        ctrl.connect('key-pressed', self._on_key_pressed)
        self.add_controller(ctrl)

    def _on_key_pressed(self, ctrl, keyval, keycode, state):
        if keyval == Gdk.KEY_Escape:
            self.close()
            return True

        focus = self.get_focus()
        in_entry = (focus == self.search_entry
                    or (focus is not None and focus.is_ancestor(self.search_entry)))

        if not in_entry:
            if keyval == Gdk.KEY_Left:
                self._switch_tab(-1)
                return True
            elif keyval == Gdk.KEY_Right:
                self._switch_tab(1)
                return True

            if keyval == Gdk.KEY_BackSpace:
                text = self.search_entry.get_text()
                if text:
                    self.search_entry.set_text(text[:-1])
                    self.search_entry.set_position(-1)
                self.search_entry.grab_focus()
                return True

            if keyval >= 32 and not (state & (Gdk.ModifierType.CONTROL_MASK |
                                               Gdk.ModifierType.ALT_MASK)):
                self.search_entry.grab_focus()
                return False

        return False

    def _switch_tab(self, direction):
        visible = [cid for cid, btn in self._tab_buttons.items()
                   if btn.get_visible()]
        if not visible:
            return
        current = None
        for cid, btn in self._tab_buttons.items():
            if btn.get_active():
                current = cid
                break
        if current is None:
            target = visible[0]
        else:
            try:
                i = visible.index(current)
                i = (i + direction) % len(visible)
                target = visible[i]
            except ValueError:
                target = visible[0]
        self._tab_buttons[target].set_active(True)

    # --- Cleanup ---

    def _on_closed(self, _dialog):
        if self._debounce_id:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = 0
        if self._cancellable:
            self._cancellable.cancel()
            self._cancellable = None
        if getattr(self, '_scroll_anim_id', 0):
            GLib.source_remove(self._scroll_anim_id)
            self._scroll_anim_id = 0
        if self._scroll_handler_id:
            adj = self.scrolled.get_vadjustment()
            if adj:
                adj.disconnect(self._scroll_handler_id)
            self._scroll_handler_id = 0
        if not self._closed_by_navigation:
            self.search_entry.set_text('')
            self._clear_results()
            self._refreshed_ids.clear()
