# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, GLib, Gtk

import random

from kitsune.ui import register_css

log = logging.getLogger('kitsune.auth_dialog')

# 24 hero images from AniLibria CDN with content hashes
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

_AUTH_CSS = (
    '.auth-hero-gradient { background:'
    ' linear-gradient(to bottom, alpha(@window_bg_color, 0), @window_bg_color); }'
    ' .auth-logo-circle { background: @card_bg_color;'
    ' border-radius: 999px; padding: 4px;'
    ' border: 2px solid alpha(currentColor, 0.15); }'
    ' .auth-form { margin-top: -200px; }'
    ' .auth-error-field { border-color: @error_color; }'
    ' .monospace { font-family: monospace; letter-spacing: 4px; }'
    ' .auth-title { color: #e01b24; }'
    # Translucent input fields. `@card_bg_color` is opaque white in
    # Adwaita's light theme but a subtle alpha-tint in dark, which
    # produced visually different inputs across themes. Switching to
    # an explicit alpha of the foreground color gives the same soft
    # translucency in both themes (8% black on light bg, 8% white on
    # dark bg), and lets the hero image bleed through the field bg.
    ' .auth-dialog entry, .auth-dialog password-entry'
    ' { background: alpha(@window_fg_color, 0.08); }'
    # Red accent for all interactive elements
    ' .auth-dialog .suggested-action'
    ' { background: #e01b24; color: white; }'
    ' .auth-dialog .suggested-action:hover'
    ' { background: #c01120; }'
    ' .auth-dialog entry:focus, .auth-dialog password-entry:focus'
    ' { outline-color: #e01b24; }'
    ' .auth-dialog .flat:hover { color: #e01b24; }'
    ' .auth-dialog linkbutton > label { color: #e01b24; }'
    ' .auth-dialog progressbar > trough > progress { background: #e01b24; }'
    ' .auth-dialog check:checked { background: #e01b24; }'
)

# Social login button attribute name → provider name mapping
_SOCIAL_MAP = {
    'vk_button': 'vk',
    'google_button': 'google',
    'discord_button': 'discord',
    'patreon_button': 'patreon',
}


@Gtk.Template(resource_path='/net/armatik/Kitsune/auth_dialog.ui')
class AuthDialog(Adw.Dialog):
    __gtype_name__ = 'AuthDialog'

    toast_overlay = Gtk.Template.Child()
    toolbar = Gtk.Template.Child()
    header_bar = Gtk.Template.Child()
    hero_picture = Gtk.Template.Child()
    logo_block = Gtk.Template.Child()
    logo_image = Gtk.Template.Child()
    login_entry = Gtk.Template.Child()
    password_entry = Gtk.Template.Child()
    login_button = Gtk.Template.Child()
    social_grid = Gtk.Template.Child()
    links_box = Gtk.Template.Child()
    register_link = Gtk.Template.Child()
    forgot_link = Gtk.Template.Child()
    vk_button = Gtk.Template.Child()
    google_button = Gtk.Template.Child()
    discord_button = Gtk.Template.Child()
    patreon_button = Gtk.Template.Child()
    otp_button = Gtk.Template.Child()
    otp_stack = Gtk.Template.Child()
    otp_code_label = Gtk.Template.Child()
    otp_timer_label = Gtk.Template.Child()

    def __init__(self, session_manager, sync_manager=None, **kwargs):
        super().__init__(**kwargs)
        register_css(_AUTH_CSS)
        self._session = session_manager
        self._sync = sync_manager
        self._otp_code = None
        self._otp_timer_id = 0
        self._otp_poll_id = 0
        self._otp_remaining = 0
        self._otp_total = 0
        self._social_poll_id = 0
        self._social_state = None
        self._social_attempts = 0
        self._fade_timer_id = 0
        self._hero_session = None

        # Transparent header over hero image
        self.toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
        self.toolbar.set_extend_content_to_top_edge(True)

        # Add CSS class for styling
        self.add_css_class('auth-dialog')

        # Breakpoint: narrow → social 2x2, links vertical
        bp = Adw.Breakpoint.new(
            Adw.BreakpointCondition.parse('max-width: 380sp'))
        bp.connect('apply', self._on_narrow_apply)
        bp.connect('unapply', self._on_narrow_unapply)
        self.add_breakpoint(bp)

        # Load AniLiberty logo from GResource
        try:
            self.logo_image.set_from_resource(
                '/net/armatik/Kitsune/aniliberty-logo.svg')
        except Exception as e:
            log.debug('Logo: failed to load: %s', e)

        self._load_hero_image()

        # Activate login on Enter in password field
        self.password_entry.connect('activate', lambda _w: self._do_login())
        self.login_entry.connect('activate',
                                 lambda _w: self.password_entry.grab_focus())

        self.connect('closed', self._on_closed)

    def _load_hero_image(self):
        """Load a random hero image directly from AniLibria CDN."""
        chosen = random.choice(_HERO_IMAGES)
        url = f'https://cdn.anilibria.top/static/{chosen}'
        log.debug('Hero: loading %s', url)
        self._fetch_hero(url)

    def _fetch_hero(self, url):
        """Download and display the hero image with fade-in."""
        from gi.repository import Soup

        log.debug('Hero: downloading %s', url)
        # Hold on the dialog so the session is GC'd with us, and a
        # dismissed dialog drops the in-flight request as soon as Python
        # collects it.
        self._hero_session = Soup.Session()
        # Without an explicit timeout the underlying GIO socket has no
        # per-request cap, so a flaky CDN can leave the auth dialog with
        # a blank hero indefinitely.
        self._hero_session.set_timeout(10)
        msg = Soup.Message.new('GET', url)

        def on_image(_session, result):
            try:
                gbytes = _session.send_and_read_finish(result)
                if not gbytes or gbytes.get_size() == 0:
                    log.debug('Hero: empty response')
                    return
                # Dialog may have been dismissed while the request was
                # in flight; touching template children after dispose
                # leaks GTK warnings and risks a use-after-free.
                if not self.get_realized():
                    return
                log.debug('Hero: %d bytes, creating texture...', gbytes.get_size())
                texture = Gdk.Texture.new_from_bytes(gbytes)
                self.hero_picture.set_paintable(texture)
                log.debug('Hero: set %dx%d', texture.get_width(), texture.get_height())
                self._fade_in_hero()
            except Exception as e:
                log.debug('Hero: failed: %s', e)

        self._hero_session.send_and_read_async(
            msg, GLib.PRIORITY_DEFAULT, None, on_image)

    def _fade_in_hero(self):
        """Fade in hero image, fade out logo block."""
        opacity = [0.0]
        step = 0.015  # 0 → 1 in ~67 frames ≈ 1.1s at 60fps

        def tick():
            if not self.get_realized():
                self._fade_timer_id = 0
                return GLib.SOURCE_REMOVE
            opacity[0] = min(1.0, opacity[0] + step)
            self.hero_picture.set_opacity(opacity[0])
            # Logo fades out 2x faster than image fades in
            self.logo_block.set_opacity(max(0.0, 1.0 - opacity[0] * 2))
            if opacity[0] >= 1.0:
                self._fade_timer_id = 0
                return GLib.SOURCE_REMOVE
            return GLib.SOURCE_CONTINUE

        self._fade_timer_id = GLib.timeout_add(16, tick)

    def _on_closed(self, _dialog):
        """Clean up timers when dialog is closed."""
        self._stop_otp_timers()
        self._stop_social_poll()
        if self._fade_timer_id:
            GLib.source_remove(self._fade_timer_id)
            self._fade_timer_id = 0

    # ------------------------------------------------------------------ Login

    @Gtk.Template.Callback()
    def on_login_clicked(self, _button):
        self._do_login()

    def _do_login(self):
        login = self.login_entry.get_text().strip()
        password = self.password_entry.get_text().strip()

        # Clear previous error styling
        self._clear_error_fields()

        if not login or not password:
            self._show_toast(_('Fill in all fields'))
            if not login:
                self.login_entry.add_css_class('auth-error-field')
            if not password:
                self.password_entry.add_css_class('auth-error-field')
            return

        self.login_button.set_sensitive(False)
        self._session.login_with_credentials(login, password,
                                             callback=self._on_login_result)

    def _on_login_result(self, success, error):
        self.login_button.set_sensitive(True)
        if not success:
            self._show_error(error)
            return
        self._finalize_login()

    def _apply_login_to_session(self, new_user, old_user_id, was_expired):
        """Decide between resume-same-user and cleanup-different-user.

        Pure state transition on session + sync; no UI side effects here
        (the caller handles closing the dialog).

        - was_expired=True and new.id != old.id → account switch: wipe
          the previous account's synced data and drop its pending queue
          ops, then clear_expired.
        - was_expired=True and new.id == old.id → same user resume:
          just clear_expired (emits session-restored → SyncManager
          resumes from Stage 6 wiring).
        - was_expired=False → fresh login: nothing to do here.

        Tags the sync queue with the new user's id immediately after
        cleanup — otherwise any write-through op enqueued between this
        point and the async profile-loaded callback in window.py would
        carry the previous (stored) user_id and then be wiped by
        clear_for_user on the next account switch.
        """
        if not was_expired:
            return
        if old_user_id is not None and new_user.id != old_user_id:
            self._session.force_logout_cleanup()
            if self._sync is not None:
                self._sync._queue.clear_for_user(old_user_id)
        if self._sync is not None and getattr(new_user, 'id', None):
            self._sync.set_user_id(new_user.id)
        self._session.clear_expired()

    def _finalize_login(self):
        """Post-login sequence shared by all three login flows.

        Captures was_expired and old_user_id before fetching the profile
        (since fetch_profile mutates self._session._user), then invokes
        the account-switch decision and closes the dialog.

        old_user_id falls back to the persisted `last-user-id` GSetting
        when `_user` is unset — that happens after a stale-token startup
        where validate_session got a 401 and `_user` was never populated.
        Without the fallback the account-switch branch in
        `_apply_login_to_session` is silently skipped, leaving the
        previous account's favorites / collections / watch positions to
        merge into the new account on first sync.
        """
        was_expired = self._session.is_expired() if self._session else False
        old_user = self._session.get_user() if self._session else None
        old_user_id = old_user.id if old_user else None
        if old_user_id is None:
            from gi.repository import Gio
            try:
                stored = Gio.Settings(
                    schema_id='net.armatik.Kitsune'
                ).get_int('last-user-id')
                if stored:
                    old_user_id = stored
            except Exception:
                pass

        def on_profile(new_user, err):
            if err or not new_user:
                # Profile fetch failed — keep dialog open, surface error
                self._show_error(err or 'no profile')
                return
            self._apply_login_to_session(new_user, old_user_id, was_expired)
            self.close()

        if self._session:
            self._session.fetch_profile(on_profile)
        else:
            self.close()

    def _show_error(self, error):
        """Show appropriate error toast based on error string from API client."""
        err_str = str(error).lower() if error else ''
        if 'unauthorized' in err_str or '401' in err_str:
            self._show_toast(_('Wrong login or password'))
            self.login_entry.add_css_class('auth-error-field')
            self.password_entry.add_css_class('auth-error-field')
        elif 'unprocessable' in err_str or '422' in err_str:
            self._show_toast(_('Fill in all fields'))
        else:
            self._show_toast(_('No connection to server'))

    def _clear_error_fields(self):
        self.login_entry.remove_css_class('auth-error-field')
        self.password_entry.remove_css_class('auth-error-field')

    # -------------------------------------------------------------- Social

    # NOTE: social buttons in the template are currently `sensitive: false` with no
    # `clicked =>` handler, so no @Gtk.Template.Callback — Gtk errors out if the
    # decorator is declared but the template has no matching handler. Keep the
    # method reachable for when social login is re-enabled.
    def on_social_clicked(self, button):
        provider = None
        for attr_name, prov in _SOCIAL_MAP.items():
            if getattr(self, attr_name, None) is button:
                provider = prov
                break
        if not provider:
            log.warning('Unknown social button clicked')
            return

        self._stop_social_poll()
        self._session.start_social_login(provider,
                                         callback=self._on_social_url)

    def _on_social_url(self, data, error):
        log.debug('Social: data=%s error=%s', data, error)
        if error or not data:
            self._show_toast(_('No connection to server'))
            return

        url = data.get('url', '') if isinstance(data, dict) else ''
        state = data.get('state', '') if isinstance(data, dict) else ''
        log.debug('Social: url=%s state=%s', url, state)

        if not url:
            self._show_toast(_('No connection to server'))
            return

        # Open browser
        launcher = Gtk.UriLauncher(uri=url)
        launcher.launch(None, None, None)

        # Start polling for completion
        self._social_state = state
        self._social_poll_id = GLib.timeout_add(3000, self._poll_social)

    def _poll_social(self):
        if not self._social_state:
            self._social_poll_id = 0
            return GLib.SOURCE_REMOVE

        self._social_attempts += 1
        if self._social_attempts > 100:  # 5 min timeout (100 * 3s)
            self._stop_social_poll()
            self._show_toast(_('Authorization timed out'))
            return GLib.SOURCE_REMOVE

        self._session.poll_social_login(self._social_state,
                                        callback=self._on_social_poll_result)
        return GLib.SOURCE_CONTINUE

    def _on_social_poll_result(self, success, error):
        if success:
            self._stop_social_poll()
            self._finalize_login()

    def _stop_social_poll(self):
        if self._social_poll_id:
            GLib.source_remove(self._social_poll_id)
            self._social_poll_id = 0
        self._social_state = None
        self._social_attempts = 0

    # ------------------------------------------------------------------- OTP

    @Gtk.Template.Callback()
    def on_otp_clicked(self, _button):
        current = self.otp_stack.get_visible_child_name()
        if current != 'idle':
            return

        self.otp_stack.set_visible_child_name('loading')
        self._session.start_otp(callback=self._on_otp_started)

    def _on_otp_started(self, data, error):
        if error or not data:
            self.otp_stack.set_visible_child_name('idle')
            self._show_toast(_('No connection to server'))
            return

        # API returns {otp: {code: "058701", ...}, remaining_time: 120}
        otp_data = data.get('otp', {}) if isinstance(data, dict) else {}
        code = otp_data.get('code', '------') if isinstance(otp_data, dict) else '------'
        remaining = data.get('remaining_time', 120) if isinstance(data, dict) else 120

        # Format code with space: "058701" → "058 701"
        code_str = str(code)
        if len(code_str) == 6:
            code_str = f'{code_str[:3]} {code_str[3:]}'

        self._otp_code = code
        self._otp_remaining = float(remaining)
        self._otp_total = float(remaining)

        self.otp_code_label.set_label(code_str)
        self.otp_stack.set_visible_child_name('code')

        # Update timer label
        self._update_otp_timer_label()

        # Timer every second for countdown
        self._otp_timer_id = GLib.timeout_add(1000, self._otp_tick)

        # Poll for OTP login every 3 seconds
        self._otp_poll_id = GLib.timeout_add(3000, self._poll_otp)

    def _otp_tick(self):
        self._otp_remaining -= 1
        if self._otp_remaining <= 0:
            self._reset_otp()
            return GLib.SOURCE_REMOVE

        self._update_otp_timer_label()
        return GLib.SOURCE_CONTINUE

    def _update_otp_timer_label(self):
        secs = int(self._otp_remaining)
        mins = secs // 60
        secs = secs % 60
        self.otp_timer_label.set_markup(
            f'{_("Remaining:")} <span font_family="monospace">{mins}:{secs:02d}</span>'
        )

    def _poll_otp(self):
        if not self._otp_code:
            self._otp_poll_id = 0
            return GLib.SOURCE_REMOVE

        device_id = self._session.get_device_id()
        self._session.login_with_otp(self._otp_code, device_id,
                                     callback=self._on_otp_poll_result)
        return GLib.SOURCE_CONTINUE

    def _on_otp_poll_result(self, success, error):
        if success:
            self._stop_otp_timers()
            self._finalize_login()

    def _reset_otp(self):
        """Reset OTP UI to idle state."""
        self._stop_otp_timers()
        self.otp_stack.set_visible_child_name('idle')
        self.otp_timer_label.set_label('')
        self.otp_code_label.set_label('------')

    def _stop_otp_timers(self):
        if self._otp_timer_id:
            GLib.source_remove(self._otp_timer_id)
            self._otp_timer_id = 0
        if self._otp_poll_id:
            GLib.source_remove(self._otp_poll_id)
            self._otp_poll_id = 0
        self._otp_code = None

    # -------------------------------------------------------------- Helpers

    def _on_narrow_apply(self, _bp):
        """Narrow: social buttons 2x2, links vertical."""
        lm = self.social_grid.get_layout_manager()
        lm.get_layout_child(self.discord_button).set_row(1)
        lm.get_layout_child(self.discord_button).set_column(0)
        lm.get_layout_child(self.patreon_button).set_row(1)
        lm.get_layout_child(self.patreon_button).set_column(1)
        self.links_box.set_orientation(Gtk.Orientation.VERTICAL)

    def _on_narrow_unapply(self, _bp):
        """Wide: social buttons 4 in row, links horizontal."""
        lm = self.social_grid.get_layout_manager()
        lm.get_layout_child(self.discord_button).set_row(0)
        lm.get_layout_child(self.discord_button).set_column(2)
        lm.get_layout_child(self.patreon_button).set_row(0)
        lm.get_layout_child(self.patreon_button).set_column(3)
        self.links_box.set_orientation(Gtk.Orientation.HORIZONTAL)

    def _show_toast(self, message):
        toast = Adw.Toast(title=message, timeout=3)
        self.toast_overlay.add_toast(toast)
