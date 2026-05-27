# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import sys
from time import monotonic

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from kitsune import ADW_TRANSITION, watch_positions
from kitsune.models import Episode, Release
from kitsune.player.gst_player import GstPlayer
from kitsune.ui import register_css
from kitsune.player.display_rotate import check_available, DisplayRotator
from kitsune.player import macos_media_keys

log = logging.getLogger('kitsune.ui.player')

_HIDE_DELAY = 2
_T = ADW_TRANSITION
_PLAYER_CSS = (
    '.player-bg { background: black; }'
    ' .player-shade {'
    '   background: linear-gradient(to bottom,'
    '     alpha(black, 0.5) 0%, alpha(black, 0.08) 18%,'
    '     transparent 30%, transparent 70%,'
    '     alpha(black, 0.08) 82%, alpha(black, 0.6) 100%);'
    ' }'
    ' .player-text { color: white;'
    '   text-shadow: 0 1px 3px alpha(black, 0.8); }'
    ' .player-play-btn { -gtk-icon-size: 32px;'
    '   color: white; min-width: 64px; min-height: 64px; padding: 0;'
    '   border-radius: 50%; background: alpha(white, 0.1);'
    '   transition: background ' + _T + '; }'
    ' .player-play-btn:hover { background: alpha(white, 0.2); }'
    ' .player-center-btn { -gtk-icon-size: 24px;'
    '   color: white; min-width: 48px; min-height: 48px; padding: 0;'
    '   border-radius: 50%; background: alpha(white, 0.1);'
    '   transition: background ' + _T + '; }'
    ' .player-center-btn:hover { background: alpha(white, 0.2); }'
    ' .player-shade scale { padding: 0; }'
    ' .player-shade scale trough {'
    '   background: alpha(white, 0.3); min-height: 4px;'
    '   margin: 0; padding: 0; }'
    ' .player-shade scale highlight {'
    '   background: white; min-height: 4px; }'
    ' .player-shade scale fill {'
    '   background: alpha(white, 0.5); min-height: 4px; }'
    ' .player-shade scale slider {'
    '   background: white; border: none;'
    '   min-width: 14px; min-height: 14px;'
    '   border-radius: 7px; margin: -5px; }'
    ' .player-shade dropdown button {'
    '   color: white; background: alpha(white, 0.15);'
    '   transition: background ' + _T + '; }'
    ' .player-shade dropdown button:hover {'
    '   background: alpha(white, 0.25); }'
    ' .player-shade dropdown button:checked {'
    '   background: alpha(white, 0.3); }'
    ' .player-rotate-btn {'
    '   color: white; background: alpha(white, 0.15);'
    '   border: none;'
    '   transition: background ' + _T + '; }'
    ' .player-rotate-btn:hover {'
    '   background: alpha(white, 0.25); }'
)


@Gtk.Template(resource_path='/net/armatik/Kitsune/player_view.ui')
class PlayerView(Adw.NavigationPage):
    __gtype_name__ = 'KitsunePlayerView'

    main_overlay = Gtk.Template.Child()
    picture = Gtk.Template.Child()
    controls_box = Gtk.Template.Child()
    top_bar = Gtk.Template.Child()
    title_label = Gtk.Template.Child()
    fullscreen_btn = Gtk.Template.Child()
    center_controls = Gtk.Template.Child()
    play_btn = Gtk.Template.Child()
    prev_btn = Gtk.Template.Child()
    next_btn = Gtk.Template.Child()
    bottom_box = Gtk.Template.Child()
    progress = Gtk.Template.Child()
    position_label = Gtk.Template.Child()
    duration_label = Gtk.Template.Child()
    volume_btn = Gtk.Template.Child()
    volume_scale = Gtk.Template.Child()
    speed_dropdown = Gtk.Template.Child()
    rotate_btn = Gtk.Template.Child()
    quality_dropdown = Gtk.Template.Child()
    seek_label = Gtk.Template.Child()
    skip_btn = Gtk.Template.Child()
    close_btn = Gtk.Template.Child()

    def __init__(self, release: Release, episode: Episode,
                 sync_manager=None, **kwargs):
        log.debug('init: %s ep %s', release.name.main, episode.ordinal)
        super().__init__(title=release.name.main, **kwargs)
        self._release = release
        self._episode = episode
        self._sync = sync_manager
        self._player = GstPlayer()
        self._seeking = False
        self._seek_reset_timer = 0
        self._skip_target = None
        self._settings = Gio.Settings(schema_id='net.armatik.Kitsune')
        self._last_motion = 0
        self._hide_timer = 0
        self._controls_visible = True
        self._last_mx = -1.0
        self._last_my = -1.0
        self._fullscreen = False
        self._fade_anim = None
        self._last_duration = 0
        self._muted_volume = 0.0
        self._ignore_quality_change = False
        self._save_counter = 0
        self._seek_accum = 0
        self._seek_base = 0
        self._seek_debounce = 0
        self._ignore_speed_change = False
        self._rotator = None
        self._last_known_position = 0
        self._restore_position = None
        self._buffering = False
        self._start_idle = 0
        self._spinner = Adw.Spinner()
        self._spinner.set_size_request(32, 32)
        register_css(_PLAYER_CSS)

        # Episode navigation
        self._episodes = list(release.episodes) if release.episodes else []
        self._current_idx = next(
            (i for i, ep in enumerate(self._episodes)
             if ep.ordinal == episode.ordinal), -1
        )

        self._setup_title()
        self._setup_paintable()
        self._setup_rotation()
        self._setup_speed()
        self._setup_quality()
        self._setup_volume()
        self._setup_nav_buttons()
        self._setup_input()
        self._connect_signals()
        self._setup_macos_media_keys()
        self.close_btn.set_visible(
            self._settings.get_boolean('player-show-close-button'))
        # Show spinner until stream is ready
        self._buffering = True
        self.play_btn.set_child(self._spinner)
        # Defer playback until widget is mapped (gtk4paintablesink needs GL context)
        self.connect('map', self._on_first_map)

    def _setup_title(self):
        ordinal = int(self._episode.ordinal) \
            if self._episode.ordinal == int(self._episode.ordinal) \
            else self._episode.ordinal
        self.title_label.set_label(
            f'{self._release.name.main} \u2014 {_("Episode")} {ordinal}'
        )

    def _setup_paintable(self):
        if self._player.paintable:
            self.picture.set_paintable(self._player.paintable)

    def _setup_rotation(self):
        # Always probe Mutter DisplayConfig availability first. On
        # non-Mutter compositors (Phosh, sway, KDE, X11+non-mutter)
        # the proxy is created without a name owner, the rotation call
        # silently fails, and the button sits visibly but inert.
        # GSettings 'player-show-rotate-button' is a user preference
        # layered on top of platform availability, not a replacement
        # for it.
        self._rotate_setting_on = self._settings.get_boolean(
            'player-show-rotate-button')
        check_available(self._on_rotate_available)

    def _on_rotate_available(self, available):
        if not available:
            return
        if self._rotate_setting_on:
            self.rotate_btn.set_visible(True)
        self._rotator = DisplayRotator()

    def _setup_speed(self):
        self._ignore_speed_change = True
        self._speeds = [0.5, 0.75, 1.0, 1.25, 1.5, 1.75, 2.0, 2.25, 2.5]
        model = Gtk.StringList.new([f'{s}x' for s in self._speeds])
        self.speed_dropdown.set_model(model)
        self.speed_dropdown.set_selected(self._speeds.index(1.0))
        self._ignore_speed_change = False

    def _setup_quality(self):
        self._ignore_quality_change = True
        quality_model = Gtk.StringList()
        available_qualities = []
        if self._episode.hls_1080:
            quality_model.append('1080p')
            available_qualities.append('1080')
        if self._episode.hls_720:
            quality_model.append('720p')
            available_qualities.append('720')
        if self._episode.hls_480:
            quality_model.append('480p')
            available_qualities.append('480')
        self._available_qualities = available_qualities
        if len(available_qualities) > 1:
            self.quality_dropdown.set_model(quality_model)
            self.quality_dropdown.set_visible(True)
            preferred = self._settings.get_string('preferred-quality')
            if preferred in available_qualities:
                self.quality_dropdown.set_selected(
                    available_qualities.index(preferred),
                )
        else:
            self.quality_dropdown.set_visible(False)
        self._ignore_quality_change = False

    def _setup_volume(self):
        saved_volume = self._settings.get_double('volume')
        self._player.set_volume(saved_volume)
        self._update_volume_icon(saved_volume)
        self.volume_scale.set_value(saved_volume)
        scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL,
        )
        scroll.connect('scroll', self._on_volume_scroll)
        self.volume_btn.add_controller(scroll)

    @Gtk.Template.Callback()
    def on_volume_toggle(self, _button):
        self._toggle_mute()

    @Gtk.Template.Callback()
    def on_volume_change(self, _scale, _scroll_type, value):
        vol = max(0.0, min(1.0, value))
        self._player.set_volume(vol)
        self._update_volume_icon(vol)
        if vol > 0:
            self._settings.set_double('volume', vol)
        return False

    def _on_seek_scroll(self, _ctrl, _dx, dy):
        self._accumulate_seek(-dy * 5)
        return True

    def _on_volume_scroll(self, _ctrl, _dx, dy):
        delta = -dy * 0.05
        vol = max(0.0, min(1.0, self._player.get_volume() + delta))
        self._player.set_volume(vol)
        self._update_volume_icon(vol)
        self.volume_scale.set_value(vol)
        if vol > 0:
            self._settings.set_double('volume', vol)
        return True

    def _update_volume_icon(self, volume):
        if volume <= 0:
            name = 'audio-volume-muted-symbolic'
        elif volume < 0.33:
            name = 'audio-volume-low-symbolic'
        elif volume < 0.66:
            name = 'audio-volume-medium-symbolic'
        else:
            name = 'net.armatik.Kitsune.audio-volume-high-symbolic'
        self.volume_btn.set_icon_name(name)

    def _setup_nav_buttons(self):
        self.prev_btn.set_sensitive(self._current_idx > 0)
        self.next_btn.set_sensitive(
            self._current_idx >= 0
            and self._current_idx < len(self._episodes) - 1
        )

    def _setup_input(self):
        # Motion on overlay — reveal controls on mouse movement
        motion = Gtk.EventControllerMotion()
        motion.connect('motion', self._on_motion)
        self.main_overlay.add_controller(motion)

        # Motion on controls — reset hide timer (controls_box covers
        # the whole overlay, so when can_target=True events go here
        # instead of main_overlay)
        ctrl_motion = Gtk.EventControllerMotion()
        ctrl_motion.connect('motion', self._on_motion)
        self.controls_box.add_controller(ctrl_motion)

        # Click on video — tap to reveal, double-click to fullscreen
        click = Gtk.GestureClick()
        click.connect('released', self._on_click_released)
        self.picture.add_controller(click)

        # Keyboard shortcuts (CAPTURE phase to intercept before buttons)
        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        key_ctrl.connect('key-pressed', self._on_key_pressed)
        self.main_overlay.add_controller(key_ctrl)
        self.main_overlay.set_focusable(True)
        self.connect('realize', lambda _w: self.main_overlay.grab_focus())

        # Scroll on bottom bar to seek (volume area has its own handler)
        seek_scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL,
        )
        seek_scroll.connect('scroll', self._on_seek_scroll)
        self.bottom_box.add_controller(seek_scroll)

        # Scroll on progress scale (CAPTURE to intercept before Scale)
        progress_scroll = Gtk.EventControllerScroll(
            flags=Gtk.EventControllerScrollFlags.VERTICAL,
        )
        progress_scroll.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        progress_scroll.connect('scroll', self._on_seek_scroll)
        self.progress.add_controller(progress_scroll)

    def _connect_signals(self):
        self._player.connect('position-updated', self._on_position_updated)
        self._player.connect('state-changed', self._on_state_changed)
        self._player.connect('eos', self._on_eos)
        self._player.connect('error', self._on_error)
        self._player.connect('buffering', self._on_buffering_signal)

    def _setup_macos_media_keys(self):
        if sys.platform != 'darwin':
            return
        macos_media_keys.init(
            on_play=self._player.play,
            on_pause=self._player.pause,
            on_toggle=self._player.toggle_play_pause,
            on_next=self._on_next_episode_media_key,
            on_prev=self._on_prev_episode_media_key,
            on_seek=self._player.seek,
        )

    def _on_next_episode_media_key(self):
        if self._current_idx >= 0 and self._current_idx < len(self._episodes) - 1:
            self._switch_episode(self._episodes[self._current_idx + 1])

    def _on_prev_episode_media_key(self):
        if self._current_idx > 0:
            self._switch_episode(self._episodes[self._current_idx - 1])

    def _update_macos_now_playing(self, position, duration, is_playing):
        if sys.platform != 'darwin':
            return
        title = self._release.name.main or ''
        episode_str = str(int(self._episode.ordinal) if self._episode.ordinal == int(self._episode.ordinal) else self._episode.ordinal)
        artist = f'{_("Episode")} {episode_str}'
        album = self._release.name.english or ''
        artwork = self._release.poster
        macos_media_keys.update(
            title=title,
            artist=artist,
            album=album,
            duration_sec=duration,
            elapsed_sec=position,
            is_playing=is_playing,
            artwork_url=artwork,
        )

    def _on_first_map(self, _widget):
        # Guard: NavigationView may map/unmap during push() animation,
        # so this handler can fire more than once.
        if self._start_idle:
            return
        self._start_idle = GLib.idle_add(self._start_playback)

    def _start_playback(self):
        self._start_idle = 0
        if not self.get_mapped():
            log.debug('_start_playback: not mapped, skip')
            return
        self._buffering = True
        self.play_btn.set_child(self._spinner)
        quality = self._settings.get_string('preferred-quality')
        url = self._episode.get_hls_url(quality)
        log.debug('start playback: quality=%s url=%s', quality, url)
        if url:
            saved = watch_positions.get_position(
                self._release.id, self._episode.ordinal,
            )
            if saved > 5:
                log.debug('restoring position: %.1fs', saved)
                self._restore_position = saved
                self._seeking = True
            self._player.play_uri(url)
            self._schedule_hide()
            # Push Now Playing immediately so the macOS widget shows up
            # right away, not 500 ms later on the first position tick.
            self._update_macos_now_playing(0, 0, True)

    # --- Controls visibility ---

    def _reveal_controls(self):
        self.main_overlay.set_cursor(None)
        if not self._controls_visible:
            self._controls_visible = True
            self._animate_controls(1.0)
            self.controls_box.set_can_target(True)
        self._schedule_hide()

    def _hide_controls(self):
        if not self._controls_visible or not self._player.is_playing:
            return
        self._controls_visible = False
        self._animate_controls(0.0)
        self.controls_box.set_can_target(False)
        self.main_overlay.set_cursor(Gdk.Cursor.new_from_name('none'))

    def _animate_controls(self, target: float):
        if self._fade_anim:
            self._fade_anim.skip()
        prop = Adw.PropertyAnimationTarget.new(self.controls_box, 'opacity')
        self._fade_anim = Adw.TimedAnimation.new(
            self.controls_box,
            self.controls_box.get_opacity(), target,
            250, prop,
        )
        self._fade_anim.play()

    def _schedule_hide(self):
        self._last_motion = monotonic()
        if self._hide_timer:
            GLib.source_remove(self._hide_timer)
        self._hide_timer = GLib.timeout_add_seconds(
            _HIDE_DELAY, self._on_hide_timeout, self._last_motion,
        )

    def _on_hide_timeout(self, scheduled_at):
        self._hide_timer = 0
        if scheduled_at != self._last_motion:
            return GLib.SOURCE_REMOVE
        self._hide_controls()
        return GLib.SOURCE_REMOVE

    # --- Input handlers ---

    def _on_motion(self, _ctrl, x, y):
        dx = x - self._last_mx
        dy = y - self._last_my
        if dx * dx + dy * dy < 1:
            return
        self._last_mx = x
        self._last_my = y
        self._reveal_controls()

    def _on_click_released(self, _gesture, n_press, _x, _y):
        if n_press == 2:
            self._toggle_fullscreen()
        else:
            self._reveal_controls()

    def _on_key_pressed(self, _ctrl, keyval, _keycode, _state):
        if keyval in (Gdk.KEY_space, Gdk.KEY_k, Gdk.KEY_K):
            self._player.toggle_play_pause()
            self._reveal_controls()
            return True
        if keyval == Gdk.KEY_Left:
            self._accumulate_seek(-10)
            self._reveal_controls()
            return True
        if keyval == Gdk.KEY_Right:
            self._accumulate_seek(10)
            self._reveal_controls()
            return True
        if keyval in (Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_F11):
            self._toggle_fullscreen()
            return True
        if keyval == Gdk.KEY_Escape:
            if self._fullscreen:
                self._toggle_fullscreen()
            else:
                self._do_back()
            return True
        if keyval == Gdk.KEY_Up:
            vol = min(1.0, self._player.get_volume() + 0.05)
            self._player.set_volume(vol)
            self._update_volume_icon(vol)
            self.volume_scale.set_value(vol)
            self._settings.set_double('volume', vol)
            self._reveal_controls()
            return True
        if keyval == Gdk.KEY_Down:
            vol = max(0.0, self._player.get_volume() - 0.05)
            self._player.set_volume(vol)
            self._update_volume_icon(vol)
            self.volume_scale.set_value(vol)
            if vol > 0:
                self._settings.set_double('volume', vol)
            self._reveal_controls()
            return True
        if keyval in (Gdk.KEY_m, Gdk.KEY_M):
            self._toggle_mute()
            self._reveal_controls()
            return True
        if keyval in (Gdk.KEY_greater, Gdk.KEY_bracketright):
            new_idx = max(0, min(len(self._speeds) - 1, self.speed_dropdown.get_selected() + 1))
            self.speed_dropdown.set_selected(new_idx)
            self._reveal_controls()
            return True
        if keyval in (Gdk.KEY_less, Gdk.KEY_bracketleft):
            new_idx = max(0, min(len(self._speeds) - 1, self.speed_dropdown.get_selected() - 1))
            self.speed_dropdown.set_selected(new_idx)
            self._reveal_controls()
            return True
        return False

    def _toggle_mute(self):
        vol = self._player.get_volume()
        if vol > 0:
            self._muted_volume = vol
            self._player.set_volume(0)
            self._update_volume_icon(0)
            self.volume_scale.set_value(0)
        else:
            restored = self._muted_volume if self._muted_volume > 0 \
                else self._settings.get_double('volume')
            if restored <= 0:
                restored = 0.5
            self._player.set_volume(restored)
            self._update_volume_icon(restored)
            self.volume_scale.set_value(restored)

    # --- Fullscreen ---

    def _toggle_fullscreen(self):
        root = self.get_root()
        if not root:
            return
        self._fullscreen = not self._fullscreen
        if self._fullscreen:
            root.fullscreen()
            self.fullscreen_btn.set_icon_name('view-restore-symbolic')
        else:
            root.unfullscreen()
            self.fullscreen_btn.set_icon_name('net.armatik.Kitsune.view-fullscreen-symbolic')

    # --- Player events ---

    def _on_position_updated(self, _player, position, duration):
        if self._buffering and self._player.is_playing:
            log.debug('buffering done (position update), pos=%d dur=%d', position, duration)
            self._buffering = False
            self.play_btn.set_icon_name('net.armatik.Kitsune.media-playback-pause-symbolic')
        if self._restore_position is not None and duration > 0:
            pos = self._restore_position
            self._restore_position = None
            log.debug('restore seek → %.1fs', pos)
            self._do_seek(pos)
            return
        if not self._seeking:
            if duration > 0:
                if duration != self._last_duration:
                    self.progress.set_range(0, duration)
                    self._last_duration = duration
                self.progress.set_value(position)
        if not self._seeking and not self._seek_debounce:
            self._last_known_position = position
            self.position_label.set_label(self._fmt_time(position))
        self.duration_label.set_label(self._fmt_time(duration))
        buffered = self._player.get_buffered_end()
        if buffered > 0:
            self.progress.set_fill_level(buffered)
        self._update_skip_button(position)
        # Save position every ~30s (60 ticks * 500ms)
        self._save_counter += 1
        if self._save_counter >= 60:
            self._save_counter = 0
            self._save_watch_position()
        # macOS Now Playing
        self._update_macos_now_playing(position, duration, self._player.is_playing)

    def _update_skip_button(self, position):
        op = self._episode.opening
        ed = self._episode.ending
        if op and op.start <= position < op.stop:
            self.skip_btn.set_label(_('Skip Intro'))
            self.skip_btn.set_visible(True)
            self._skip_target = op.stop
        elif ed and ed.start <= position < ed.stop:
            self.skip_btn.set_label(_('Skip Outro'))
            self.skip_btn.set_visible(True)
            self._skip_target = ed.stop
        else:
            self.skip_btn.set_visible(False)
            self._skip_target = None

    def _handle_auto_collections(self, pos):
        # Forward position-update to auto_collections; apply 'auto'
        # actions immediately (sync-aware) and forward 'suggest' actions
        # to the window for toast display. Gated by user setting so the
        # whole mechanism can be disabled without disconnecting the hook.
        if not self._sync:
            return
        try:
            settings = Gio.Settings(schema_id='net.armatik.Kitsune')
            if not settings.get_boolean('auto-collections-watch-events'):
                return
        except Exception:
            pass
        from kitsune.storage import auto_collections
        release_meta = {
            'episodes_total': self._release.episodes_total,
            'is_ongoing': self._release.is_ongoing,
            'episodes': [
                {'id': e.id, 'ordinal': e.ordinal}
                for e in self._release.episodes
            ],
        }
        actions = auto_collections.evaluate_position_change(
            self._release.id, pos, release_meta,
        )
        if not actions:
            return
        root = self.get_root()
        for action in actions:
            if action.type == 'auto':
                auto_collections.apply_action(action, self._sync)
                log.info(
                    'auto-collection %s release=%d → %s',
                    action.reason, action.release_id, action.to_tag,
                )
            elif action.type == 'suggest' and root is not None and \
                    hasattr(root, 'show_collection_suggestion'):
                root.show_collection_suggestion(action)

    def _save_watch_position(self):
        pos = self._player.get_position()
        dur = self._player.get_duration()
        ep_id = self._episode.id
        if dur > 0 and pos > 5 and (dur - pos) > 60:
            watch_positions.save_position(
                self._release.id, self._episode.ordinal, pos,
                episode_id=ep_id,
            )
            if self._sync and ep_id:
                self._sync.enqueue_timecode(
                    release_id=self._release.id,
                    episode_id=ep_id,
                    pos=pos,
                    is_watched=False,
                )
            self._handle_auto_collections(pos)
        elif dur > 0 and (dur - pos) <= 60:
            watch_positions.mark_completed(
                self._release.id, self._episode.ordinal,
                episode_id=ep_id,
            )
            if self._sync and ep_id:
                self._sync.enqueue_timecode(
                    release_id=self._release.id,
                    episode_id=ep_id,
                    pos=0,
                    is_watched=True,
                )
            self._handle_auto_collections(-1)

    def _on_state_changed(self, _player, state):
        log.debug('ui state: %s (buffering=%s)', state, self._buffering)
        if state == 'playing':
            self._buffering = False
            self.play_btn.set_icon_name('net.armatik.Kitsune.media-playback-pause-symbolic')
        elif not self._buffering:
            self.play_btn.set_icon_name('net.armatik.Kitsune.media-playback-start-symbolic')
            self._reveal_controls()
            if state == 'paused':
                self._save_watch_position()
        # macOS Now Playing state update
        if state in ('playing', 'paused'):
            macos_media_keys.update_state(
                self._player.get_position(),
                state == 'playing',
            )
        elif state == 'stopped':
            macos_media_keys.clear()

    def _on_eos(self, _player):
        macos_media_keys.clear()
        ep_id = self._episode.id
        watch_positions.mark_completed(
            self._release.id, self._episode.ordinal,
            episode_id=ep_id,
        )
        if self._sync and ep_id:
            self._sync.enqueue_timecode(
                release_id=self._release.id,
                episode_id=ep_id,
                pos=0,
                is_watched=True,
            )
        self._handle_auto_collections(-1)
        if self._current_idx >= 0 \
                and self._current_idx < len(self._episodes) - 1:
            self._switch_episode(self._episodes[self._current_idx + 1])
        else:
            self._do_back()

    def _on_error(self, _player, message):
        log.error('playback error: %s', message)
        safe_msg = GLib.markup_escape_text(message, -1)
        toast = Adw.Toast(title=_('Playback error: {}').format(safe_msg))
        root = self.get_root()
        if hasattr(root, 'add_toast'):
            root.add_toast(toast)

    def _on_buffering_signal(self, _player, percent):
        log.debug('ui buffering: %d%%', percent)
        if percent < 100:
            self._buffering = True
            self.play_btn.set_child(self._spinner)
        else:
            self._buffering = False
            if self._player.is_playing:
                self.play_btn.set_icon_name('net.armatik.Kitsune.media-playback-pause-symbolic')
            else:
                self.play_btn.set_icon_name('net.armatik.Kitsune.media-playback-start-symbolic')

    # --- Episode navigation ---

    def _switch_episode(self, episode):
        log.debug('switch episode → %s', episode.ordinal)
        self._save_watch_position()
        self._episode = episode
        self._current_idx = next(
            (i for i, ep in enumerate(self._episodes)
             if ep.ordinal == episode.ordinal), -1
        )
        self._player.stop()
        self._player.reset_pipeline()
        self._setup_paintable()
        self._last_duration = 0
        self._skip_target = None
        self.skip_btn.set_visible(False)
        self.progress.set_value(0)
        self.progress.set_fill_level(0)
        self.position_label.set_label('0:00')
        self.duration_label.set_label('0:00')
        self._setup_title()
        self._setup_quality()
        self._start_playback()
        self._setup_nav_buttons()

    # --- Callbacks ---

    def _do_back(self):
        log.debug('back (cleanup)')
        self._save_watch_position()
        if self._fullscreen:
            self._toggle_fullscreen()
        if self._rotator and self._rotator.is_rotated:
            self._rotator.restore()
        self._player.cleanup()
        nav = self.get_ancestor(Adw.NavigationView)
        if nav:
            nav.pop()

    @Gtk.Template.Callback()
    def on_back(self, _button):
        self._do_back()

    @Gtk.Template.Callback()
    def on_fullscreen(self, _button):
        self._toggle_fullscreen()

    @Gtk.Template.Callback()
    def on_close(self, _button):
        root = self.get_root()
        if root:
            root.close()

    @Gtk.Template.Callback()
    def on_play_pause(self, _button):
        self._player.toggle_play_pause()

    def _do_seek(self, position):
        log.debug('do_seek → %.1fs', position)
        self._seeking = True
        self._last_known_position = position
        self._player.seek(position)
        if self._seek_reset_timer:
            GLib.source_remove(self._seek_reset_timer)
        self._seek_reset_timer = GLib.timeout_add(1000, self._reset_seeking)

    def _reset_seeking(self):
        self._seeking = False
        self._seek_reset_timer = 0
        return GLib.SOURCE_REMOVE

    def _accumulate_seek(self, offset):
        if self._seek_debounce == 0:
            self._seek_base = self._last_known_position
            self._seek_accum = 0
        self._seek_accum += offset
        self._seeking = True
        target = max(0, self._seek_base + self._seek_accum)
        self.progress.set_value(target)
        self.position_label.set_label(self._fmt_time(target))
        # Show seek indicator
        accum = self._seek_accum
        if accum > 0:
            self.seek_label.set_label(f'\u25b6 +{self._fmt_time(accum)}')
        elif accum < 0:
            self.seek_label.set_label(f'\u25c0 -{self._fmt_time(-accum)}')
        self.seek_label.set_visible(True)
        if self._seek_debounce:
            GLib.source_remove(self._seek_debounce)
        self._seek_debounce = GLib.timeout_add(500, self._flush_accumulated_seek)

    def _flush_accumulated_seek(self):
        self._seek_debounce = 0
        self.seek_label.set_visible(False)
        target = max(0, self._seek_base + self._seek_accum)
        self._seek_accum = 0
        self._do_seek(target)
        return GLib.SOURCE_REMOVE

    @Gtk.Template.Callback()
    def on_rewind(self, _button):
        self._accumulate_seek(-10)

    @Gtk.Template.Callback()
    def on_forward(self, _button):
        self._accumulate_seek(10)

    @Gtk.Template.Callback()
    def on_seek(self, _scale, _scroll_type, value):
        self._do_seek(value)
        return False

    @Gtk.Template.Callback()
    def on_speed_changed(self, dropdown, _pspec):
        if self._ignore_speed_change:
            return
        idx = dropdown.get_selected()
        if idx < len(self._speeds):
            speed = self._speeds[idx]
            log.debug('speed changed → %s', speed)
            self._player.set_rate(speed)

    @Gtk.Template.Callback()
    def on_rotate_clicked(self, _button):
        if self._rotator:
            self._rotator.toggle()

    @Gtk.Template.Callback()
    def on_quality_changed(self, dropdown, _pspec):
        if self._ignore_quality_change:
            return
        idx = dropdown.get_selected()
        if idx < len(self._available_qualities):
            quality = self._available_qualities[idx]
            log.debug('quality changed → %s', quality)
            self._settings.set_string('preferred-quality', quality)
            self._restore_position = self._player.get_position()
            self._buffering = True
            self.play_btn.set_child(self._spinner)
            url = self._episode.get_hls_url(quality)
            if url:
                self._seeking = True
                self._player.play_uri(url)

    @Gtk.Template.Callback()
    def on_skip(self, _btn):
        if self._skip_target:
            target = self._skip_target
            self._skip_target = None
            self.skip_btn.set_visible(False)
            self._do_seek(target)

    @Gtk.Template.Callback()
    def on_prev_episode(self, _btn):
        if self._current_idx > 0:
            self._switch_episode(self._episodes[self._current_idx - 1])

    @Gtk.Template.Callback()
    def on_next_episode(self, _btn):
        if self._current_idx >= 0 \
                and self._current_idx < len(self._episodes) - 1:
            self._switch_episode(self._episodes[self._current_idx + 1])

    @staticmethod
    def _fmt_time(seconds) -> str:
        seconds = int(max(0, seconds))
        m, s = divmod(seconds, 60)
        h, m = divmod(m, 60)
        if h:
            return f'{h}:{m:02d}:{s:02d}'
        return f'{m}:{s:02d}'

    def do_unmap(self):
        log.debug('unmap')
        try:
            if self._fullscreen:
                root = self.get_root()
                if root:
                    root.unfullscreen()
            # _start_idle is NOT cancelled here: NavigationView may
            # map/unmap during push(), and _start_playback already
            # checks get_mapped() before starting the pipeline.
            if self._hide_timer:
                GLib.source_remove(self._hide_timer)
                self._hide_timer = 0
            if self._seek_reset_timer:
                GLib.source_remove(self._seek_reset_timer)
                self._seek_reset_timer = 0
            if self._seek_debounce:
                GLib.source_remove(self._seek_debounce)
                self._seek_debounce = 0
            if self._fade_anim:
                self._fade_anim.skip()
                self._fade_anim = None
            # Stage 5: flush pending timecode ops so the server gets our
            # latest position even if the user closes the player without
            # full app shutdown.
            if self._sync:
                self._sync.flush_timecodes(self._release.id)
            macos_media_keys.clear()
        finally:
            Adw.NavigationPage.do_unmap(self)
