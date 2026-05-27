# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import random

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gtk

from kitsune import tags_store
from kitsune.storage import watch_positions
from kitsune.ui import register_css, resolved_tag_color

log = logging.getLogger('kitsune.profile_view')

_COLLECTION_TAGS = [
    ('favorites', 'Favorites', 'net.armatik.Kitsune.starred-symbolic'),
    ('watching', 'Watching', 'net.armatik.Kitsune.media-playback-start-symbolic'),
    ('watched', 'Watched', 'net.armatik.Kitsune.object-select-symbolic'),
    ('planned', 'Planned', 'net.armatik.Kitsune.view-list-bullet-symbolic'),
    ('postponed', 'Postponed', 'net.armatik.Kitsune.media-playback-pause-symbolic'),
    ('abandoned', 'Abandoned', 'net.armatik.Kitsune.cross-large-symbolic'),
]

_HERO_IMAGES = [
    'SD01.BK-zeZze.jpg', 'SD02.CFb2ug4g.jpg', 'SD03.Big5uXdC.jpg',
    'SD04.iDB17XuA.jpg', 'SD05.xSIf8EKO.jpg', 'SD06.DzSKOZiB.jpg',
    'SD07.PUUEzIZh.jpg', 'SD08.D_N8GfOl.jpg', 'SD09.DdbN9Lh3.jpg',
    'SD10.CT9UJk16.jpg', 'SD11.CoQVRJh3.jpg', 'SD12.Py78cBAE.jpg',
    'SD13.6iC3GxuS.jpg', 'SD14.1vbAt3XX.jpg', 'SD15.DqhS0xIL.jpg',
    'SD16.Di6S4bdo.jpg', 'SD17.B1bzHyBh.jpg', 'SD18.COgz20JF.jpg',
    'SD19.DpTeEfyb.jpg', 'SD20.De4OAhxk.jpg', 'REG01.CktUHpfc.jpg',
    'REG02.CQ9KlpWV.jpg', 'REG03.CTS9TmSc.jpg', 'REG04.BDVfGboN.jpg',
]

# Animation constants
_HERO_HEIGHT = 520
_HERO_MARGIN_START = -340   # hero hidden above card
_HERO_MARGIN_END = 0        # hero fully visible
_CONTENT_MARGIN_START = 16  # content at top with padding
_CONTENT_MARGIN_END = 220   # content pushed down (overlaps hero by 520-220=300px)
_ANIM_DURATION_MS = 900

_PROFILE_CSS = (
    # Card wrapper — uses window_bg_color to match gradient exactly
    ' .profile-card { background: @window_bg_color;'
    ' border-radius: 16px;'
    ' border: 1px solid alpha(currentColor, 0.1);'
    ' box-shadow: 0 2px 12px alpha(black, 0.1); }'
    # Narrow / mobile: strip the card chrome so the profile flows
    # edge-to-edge of the window instead of sitting inside a virtual
    # rounded card. Hero stays — just loses its top rounding.
    ' .profile-card.profile-card-narrow { background: none;'
    ' border: none; box-shadow: none; border-radius: 0; }'
    ' .profile-card.profile-card-narrow .profile-hero-image {'
    ' border-radius: 0; }'
    # Hero image rounded top
    ' .profile-hero-image { border-radius: 15px 15px 0 0; }'
    # Hero gradient — matches card bg (@window_bg_color)
    ' .profile-hero-gradient { background:'
    ' linear-gradient(to bottom, transparent 0%,'
    ' transparent 15%,'
    ' alpha(@window_bg_color, 0.3) 35%,'
    ' alpha(@window_bg_color, 0.65) 50%,'
    ' alpha(@window_bg_color, 0.92) 72%,'
    ' @window_bg_color 91%); }'
    # Collection card
    ' .collection-card { border-radius: 14px; padding: 14px 8px;'
    ' border: 1px solid alpha(currentColor, 0.06);'
    ' transition: border-color 200ms, box-shadow 200ms; }'
    ' .collection-card:hover { border-color: alpha(currentColor, 0.15);'
    ' box-shadow: 0 2px 8px alpha(black, 0.1); }'
    ' .collection-card:active { box-shadow: none; opacity: 0.85; }'
    # Total card
    ' .total-card { border-radius: 14px; padding: 20px 16px;'
    ' background: alpha(@accent_bg_color, 0.08);'
    ' border: 1px solid alpha(@accent_bg_color, 0.10); }'
    # FlowBox — system Adwaita has `flowbox > flowboxchild { padding: 3px }`
    # which is more specific than a plain `flowboxchild` selector, so our
    # zero-padding override must match that specificity or higher, else
    # each cell steals 6px horizontal and the row overflows the parent.
    ' .profile-card flowbox > flowboxchild { padding: 0; background: none; }'
    ' .profile-card flowbox > flowboxchild:hover { background: none; }'
    ' .profile-card flowbox > flowboxchild:active { background: none; }'
    # Button.flat ships with its own internal padding (~5×10px) and a
    # min-width/min-height baseline; without zeroing them, the visible
    # .collection-card paint sits inset from the flowboxchild edges,
    # making the 6-card row appear narrower than the action row below
    # and inflating the visible column gap past the declared 8px.
    ' .profile-card flowboxchild button.flat {'
    ' padding: 0; min-width: 0; min-height: 0;'
    ' background: none; box-shadow: none; }'
    ' .profile-card flowboxchild button.flat:hover { background: none; }'
    ' .profile-card flowboxchild button.flat:active { background: none; }'
    # Icon-only pill next to logout — large and square so it reads as a
    # peer of the text pill rather than a tiny icon button
    ' button.profile-settings-pill {'
    ' min-width: 56px; min-height: 40px; padding: 8px 14px; }'
    ' button.profile-settings-pill > image { -gtk-icon-size: 20px; }'
)


def _hex_to_rgba(hex_color, alpha):
    r = int(hex_color[1:3], 16)
    g = int(hex_color[3:5], 16)
    b = int(hex_color[5:7], 16)
    return f'rgba({r},{g},{b},{alpha})'


@Gtk.Template(resource_path='/net/armatik/Kitsune/profile_view.ui')
class ProfileView(Gtk.Box):
    __gtype_name__ = 'KitsuneProfileView'

    clamp = Gtk.Template.Child()
    card_box = Gtk.Template.Child()
    profile_overlay = Gtk.Template.Child()
    content_box = Gtk.Template.Child()
    hero_box = Gtk.Template.Child()
    hero_picture = Gtk.Template.Child()
    avatar = Gtk.Template.Child()
    nickname_label = Gtk.Template.Child()
    email_label = Gtk.Template.Child()
    member_since_label = Gtk.Template.Child()
    sync_time_label = Gtk.Template.Child()
    sync_button = Gtk.Template.Child()
    pending_row = Gtk.Template.Child()
    pending_label = Gtk.Template.Child()
    error_row = Gtk.Template.Child()
    error_text_label = Gtk.Template.Child()
    retry_button = Gtk.Template.Child()
    collections_flow = Gtk.Template.Child()
    totals_box = Gtk.Template.Child()
    settings_button = Gtk.Template.Child()
    logout_button = Gtk.Template.Child()

    def __init__(self, session_manager, on_navigate_tag, sync_manager=None, **kwargs):
        super().__init__(**kwargs)
        register_css(_PROFILE_CSS)
        self._session = session_manager
        self._on_navigate_tag = on_navigate_tag
        self._sync_manager = sync_manager
        self._cards = {}
        self._anim = None
        self._narrow = False
        self._hero_session = None

        # Clip card content
        self.card_box.set_overflow(Gtk.Overflow.HIDDEN)

        # Content overlay determines card sizing (not hero)
        self.profile_overlay.set_measure_overlay(self.content_box, True)

        # Initial state: hero hidden, content at top
        self.hero_box.set_margin_top(_HERO_MARGIN_START)
        self.content_box.set_margin_top(_CONTENT_MARGIN_START)

        self._setup_collection_cards()
        self._setup_total_cards()
        self._load_hero_image()

        # Subscribe to sync events for pending-ops indicator
        if self._sync_manager:
            self._sync_manager.connect_queue_changed(self._on_queue_changed)
            self._sync_manager.connect_sync_complete(self._on_sync_complete)
            self._refresh_indicator()

    def _setup_collection_cards(self):
        for tag_id, label, icon_name in _COLLECTION_TAGS:
            color = resolved_tag_color({'id': tag_id})
            count = len(tags_store.get_release_ids_for_tag(tag_id))

            card_btn = Gtk.Button(css_classes=['flat'])
            card_btn.connect('clicked', self._on_collection_clicked, tag_id)

            bg_start = _hex_to_rgba(color, 0.10)
            bg_end = _hex_to_rgba(color, 0.03)
            border = _hex_to_rgba(color, 0.12)

            card_box = Gtk.Box(
                orientation=Gtk.Orientation.VERTICAL,
                spacing=4,
                css_classes=['collection-card'],
            )
            # Color is scoped to .collection-icon (not the whole card) so
            # title_lbl's dim-label keeps its neutral grey instead of
            # picking up the tag tint via currentColor inheritance.
            css_provider = Gtk.CssProvider()
            css_provider.load_from_string(
                f'.collection-card {{ background:'
                f' linear-gradient(135deg, {bg_start}, {bg_end});'
                f' border-color: {border}; }}'
                f'.collection-card > .collection-icon {{ color: {color}; }}'
            )
            card_box.get_style_context().add_provider(
                css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

            icon = Gtk.Image.new_from_icon_name(icon_name)
            icon.set_pixel_size(28)
            icon.add_css_class('collection-icon')
            card_box.append(icon)

            count_lbl = Gtk.Label()
            count_lbl.set_markup(
                f'<span size="x-large" weight="bold" color="{color}">'
                f'{count}</span>')
            card_box.append(count_lbl)

            title_lbl = Gtk.Label(
                label=_(label),
                css_classes=['caption', 'dim-label'],
                # Allow narrow shrink: without this the label's natural
                # width ("Просмотренные") drives FlowBox natural request
                # past the parent, causing the row to overflow.
                wrap=True,
                max_width_chars=10,
                justify=Gtk.Justification.CENTER,
            )
            card_box.append(title_lbl)

            card_btn.set_child(card_box)
            self.collections_flow.append(card_btn)
            self._cards[tag_id] = count_lbl

    def _setup_total_cards(self):
        total = sum(
            len(tags_store.get_release_ids_for_tag(tid))
            for tid, _, _ in _COLLECTION_TAGS
        )

        box1 = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=['total-card'],
        )
        self._total_label = Gtk.Label()
        self._total_label.set_markup(
            f'<span size="x-large" weight="bold">{total}</span>')
        box1.append(self._total_label)
        box1.append(Gtk.Label(
            label=_('Total titles'),
            css_classes=['caption', 'dim-label'],
            wrap=True,
            max_width_chars=12,
            justify=Gtk.Justification.CENTER,
        ))
        self.totals_box.append(box1)

        box2 = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL,
            css_classes=['total-card'],
        )
        self._episodes_label = Gtk.Label()
        self._episodes_label.set_markup(
            '<span size="x-large" weight="bold">—</span>')
        box2.append(self._episodes_label)
        box2.append(Gtk.Label(
            label=_('Episodes watched'),
            css_classes=['caption', 'dim-label'],
            wrap=True,
            max_width_chars=12,
            justify=Gtk.Justification.CENTER,
        ))
        self.totals_box.append(box2)

    def _load_hero_image(self):
        from gi.repository import Soup

        name = random.choice(_HERO_IMAGES)
        url = f'https://cdn.anilibria.top/static/{name}'
        log.debug('Profile hero: loading %s', url)

        # Hold on the view so the session GC'd with us; navigating away
        # drops the in-flight request as soon as Python collects.
        self._hero_session = Soup.Session()
        self._hero_session.set_timeout(10)
        msg = Soup.Message.new('GET', url)

        def on_image(_session, result):
            try:
                gbytes = _session.send_and_read_finish(result)
                if not gbytes or gbytes.get_size() == 0:
                    return
                # View may have been unmapped/destroyed while the request
                # was in flight; bail before touching template children.
                if not self.get_realized():
                    return
                texture = Gdk.Texture.new_from_bytes(gbytes)
                self.hero_picture.set_paintable(texture)
                self._start_parallax()
            except Exception as e:
                log.debug('Profile hero: failed: %s', e)

        self._hero_session.send_and_read_async(
            msg, GLib.PRIORITY_DEFAULT, None, on_image)

    def _start_parallax(self):
        """Parallax animation: hero slides more, content slides less."""
        def on_progress(t, _=None):
            # Hero: -340 → 0 (moves 340px)
            hero_margin = int(
                _HERO_MARGIN_START +
                (_HERO_MARGIN_END - _HERO_MARGIN_START) * t)
            self.hero_box.set_margin_top(hero_margin)

            # Content: 16 → 220 (moves 204px — less than hero)
            content_margin = int(
                _CONTENT_MARGIN_START +
                (_CONTENT_MARGIN_END - _CONTENT_MARGIN_START) * t)
            self.content_box.set_margin_top(content_margin)

            # Fade in image (delayed start at 15%)
            if t > 0.15:
                self.hero_picture.set_opacity(
                    min(1.0, (t - 0.15) / 0.6))
            else:
                self.hero_picture.set_opacity(0.0)

        target = Adw.CallbackAnimationTarget.new(on_progress)
        self._anim = Adw.TimedAnimation.new(
            self.card_box, 0.0, 1.0, _ANIM_DURATION_MS, target)
        self._anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        self._anim.play()

    def _on_collection_clicked(self, _button, tag_id):
        if self._on_navigate_tag:
            data = tags_store._load()
            tag = tags_store._find_tag(data, tag_id)
            if tag:
                self._on_navigate_tag(tag)

    def refresh_hero(self):
        """Reset to initial state, load new image."""
        if self._anim:
            self._anim.skip()
            self._anim = None
        self.hero_box.set_margin_top(_HERO_MARGIN_START)
        self.content_box.set_margin_top(_CONTENT_MARGIN_START)
        self.hero_picture.set_opacity(0)
        self._load_hero_image()

    def set_narrow(self, narrow: bool):
        if self._narrow == narrow:
            return
        self._narrow = narrow
        if narrow:
            self.card_box.add_css_class('profile-card-narrow')
            self.clamp.set_margin_start(0)
            self.clamp.set_margin_end(0)
            self.clamp.set_margin_top(0)
            # Reserve space for the BottomSheet bottom-bar (separator +
            # tab buttons ≈ 56-64px) so the logout/settings row can
            # scroll fully into view instead of being hidden behind it.
            self.clamp.set_margin_bottom(72)
            # Disable Adw.Clamp width cap so content reaches the window
            # edges instead of staying inside a 520px column.
            self.clamp.set_maximum_size(2**30)
        else:
            self.card_box.remove_css_class('profile-card-narrow')
            self.clamp.set_margin_start(16)
            self.clamp.set_margin_end(16)
            self.clamp.set_margin_top(20)
            self.clamp.set_margin_bottom(20)
            self.clamp.set_maximum_size(520)

    def update_profile(self, user):
        if user is None:
            self.nickname_label.set_label('')
            self.email_label.set_label('')
            self.member_since_label.set_label('')
            self.avatar.set_text('')
            self.avatar.set_custom_image(None)
            return
        nickname = user.nickname or ''
        self.nickname_label.set_label(nickname)
        self.avatar.set_text(nickname)
        # Adw.Avatar shows initials by default; load the server-side
        # avatar (already resolved to a full URL by User.from_dict) and
        # set it as the custom image when it arrives. If the fetch
        # fails, the initials fallback stays visible.
        if user.avatar:
            from kitsune.ui.image_cache import load_image
            load_image(user.avatar, lambda tex, err:
                       self.avatar.set_custom_image(tex) if tex else None,
                       category='avatars')
        else:
            self.avatar.set_custom_image(None)
        self.email_label.set_label(user.email or '')
        if user.created_at:
            try:
                from datetime import datetime
                dt = datetime.fromisoformat(
                    user.created_at.replace('Z', '+00:00'))
                self.member_since_label.set_label(
                    _('Member since %s') % dt.strftime('%B %Y'))
            except Exception:
                self.member_since_label.set_label('')

    def refresh_counts(self):
        """Refresh all counters with count-up animation."""
        targets = {}
        total = 0
        for tag_id, _label, _icon_name in _COLLECTION_TAGS:
            count = len(tags_store.get_release_ids_for_tag(tag_id))
            total += count
            lbl = self._cards.get(tag_id)
            if lbl:
                targets[lbl] = (count, resolved_tag_color({'id': tag_id}))

        targets[self._total_label] = (total, None)
        completed = watch_positions.get_completed_count()
        targets[self._episodes_label] = (completed, None)

        self._animate_counters(targets)

    def _animate_counters(self, targets):
        """Animate labels from 0 to target with ease-out."""
        if not targets:
            return

        def on_progress(t, _=None):
            for lbl, (target, color) in targets.items():
                val = int(target * t)
                if color:
                    lbl.set_markup(
                        f'<span size="x-large" weight="bold"'
                        f' color="{color}">{val}</span>')
                else:
                    lbl.set_markup(
                        f'<span size="x-large" weight="bold">'
                        f'{val}</span>')

        target = Adw.CallbackAnimationTarget.new(on_progress)
        anim = Adw.TimedAnimation.new(
            self, 0.0, 1.0, 2000, target)
        anim.set_easing(Adw.Easing.EASE_OUT_CUBIC)
        anim.play()

    def set_sync_time(self, time_str):
        self.sync_time_label.set_label(
            _('Synced at %s') % time_str if time_str else '')

    @Gtk.Template.Callback()
    def on_sync_clicked(self, _button):
        if self._sync_manager:
            self._sync_manager.sync_now(self._on_sync_done)

    def _on_sync_done(self, ok, error):
        import datetime
        self.set_sync_time(datetime.datetime.now().strftime('%H:%M'))
        self.refresh_counts()

    def _refresh_indicator(self):
        """Update pending-row, error-row, retry-button visibility + text
        based on current queue state."""
        if not self._sync_manager:
            self.pending_row.set_visible(False)
            self.error_row.set_visible(False)
            self.retry_button.set_visible(False)
            return
        size = self._sync_manager.queue_size()
        has_errors = self._sync_manager.queue_has_errors()
        last_error = self._sync_manager.last_queue_error()
        self.pending_row.set_visible(size > 0)
        if size > 0:
            if size == 1:
                self.pending_label.set_label(
                    _('1 operation waiting to sync'))
            else:
                self.pending_label.set_label(
                    _('{n} operations waiting to sync').format(n=size))
        # Keep error_row and retry_button visibility in sync: if any op
        # has failed, the user sees both the explanation AND the retry
        # button. Fall back to a generic label if last_error is missing
        # (rare — queue ops always record their error message on failure,
        # but defend against a None/'' leak).
        self.error_row.set_visible(has_errors)
        if has_errors:
            self.error_text_label.set_label(
                (last_error or _('Unknown error'))[:80])
        self.retry_button.set_visible(has_errors)

    def _on_queue_changed(self, _size):
        self._refresh_indicator()

    def _on_sync_complete(self, success):
        if success:
            import datetime
            self.set_sync_time(datetime.datetime.now().strftime('%H:%M'))
            self.refresh_counts()
        self._refresh_indicator()

    @Gtk.Template.Callback()
    def on_retry_clicked(self, _button):
        """User hit 'Retry now' — reset backoffs and drain."""
        if self._sync_manager:
            self._sync_manager.force_drain()

    @Gtk.Template.Callback()
    def on_settings_site_clicked(self, _button):
        launcher = Gtk.UriLauncher(uri='https://anilibria.top/app/settings/')
        launcher.launch(self.get_root(), None, None)

    @Gtk.Template.Callback()
    def on_logout_clicked(self, _button):
        if self._session:
            self._session.logout()
