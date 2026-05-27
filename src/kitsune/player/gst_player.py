# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import logging
import sys

import gi

gi.require_version('Gst', '1.0')
gi.require_version('Gtk', '4.0')

from gi.repository import GLib, GObject, Gst


log = logging.getLogger('kitsune.player')


class GstPlayer(GObject.Object):

    __gsignals__ = {
        'state-changed': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'position-updated': (GObject.SignalFlags.RUN_LAST, None, (int, int)),
        'error': (GObject.SignalFlags.RUN_LAST, None, (str,)),
        'eos': (GObject.SignalFlags.RUN_LAST, None, ()),
        'buffering': (GObject.SignalFlags.RUN_LAST, None, (int,)),
    }

    def __init__(self):
        super().__init__()
        if not Gst.is_initialized():
            log.warning('Gst not initialized, calling Gst.init()')
            Gst.init(None)
        self._playbin = Gst.ElementFactory.make('playbin3', 'playbin')
        if self._playbin:
            log.debug('created playbin3')
        else:
            self._playbin = Gst.ElementFactory.make('playbin', 'playbin')
            if self._playbin:
                log.debug('created playbin (fallback)')
            else:
                raise RuntimeError(
                    'GStreamer playbin not available. '
                    'Install gstreamer1.0-plugins-base.'
                )

        self._paintable = None
        self._setup_video_sink()
        self._setup_audio_filter()
        self._target_state = Gst.State.NULL
        self._is_buffering = False
        self._rate = 1.0
        self._target_rate = 1.0

        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        self._bus_handler_ids = [
            bus.connect('message::error', self._on_error),
            bus.connect('message::eos', self._on_eos),
            bus.connect('message::state-changed', self._on_state_changed),
            bus.connect('message::buffering', self._on_buffering),
        ]

        self._position_timer = 0
        self._cleaned_up = False

    def _setup_video_sink(self):
        sink = Gst.ElementFactory.make('gtk4paintablesink', 'gtksink')
        if sink:
            self._paintable = sink.get_property('paintable')
            # macOS: avoid glsinkbin because GstGL can't wrap the native
            # GL/Metal context that GTK4 creates, causing rendering failures.
            if sys.platform == 'darwin':
                self._playbin.set_property('video-sink', sink)
                log.debug('video sink: gtk4paintablesink (macOS, no GL)')
            else:
                gl_sink = Gst.ElementFactory.make('glsinkbin', 'glsink')
                if gl_sink:
                    gl_sink.set_property('sink', sink)
                    self._playbin.set_property('video-sink', gl_sink)
                    log.debug('video sink: glsinkbin → gtk4paintablesink')
                else:
                    self._playbin.set_property('video-sink', sink)
                    log.debug('video sink: gtk4paintablesink (no GL)')
        else:
            log.warning('gtk4paintablesink not available')

    def _setup_audio_filter(self):
        elements = [
            "audioconvert", "audioresample", "scaletempo",
            "audioconvert", "audioresample"
        ]
        bins = [Gst.ElementFactory.make(n, None) for n in elements]
        if None in bins:
            log.warning("scaletempo pipeline unavailable, pitch may distort")
            return

        filter_bin = Gst.Bin.new("audiofilterbin")
        for e in bins:
            filter_bin.add(e)

        for i in range(len(bins) - 1):
            bins[i].link(bins[i + 1])

        filter_bin.add_pad(Gst.GhostPad.new("sink", bins[0].get_static_pad("sink")))
        filter_bin.add_pad(Gst.GhostPad.new("src", bins[-1].get_static_pad("src")))

        self._playbin.set_property("audio-filter", filter_bin)
        log.debug("audio filter: audioconvert ! audioresample ! scaletempo ! audioconvert ! audioresample")

    @property
    def paintable(self):
        return self._paintable

    def reset_pipeline(self):
        """Fully recreate the pipeline (needed after EOS / episode switch)."""
        if self._playbin:
            bus = self._playbin.get_bus()
            if bus:
                for hid in self._bus_handler_ids:
                    bus.disconnect(hid)
                bus.remove_signal_watch()
            self._playbin.set_state(Gst.State.NULL)
        self._bus_handler_ids = []
        self._playbin = Gst.ElementFactory.make('playbin3', 'playbin')
        if self._playbin:
            log.debug('created playbin3')
        else:
            self._playbin = Gst.ElementFactory.make('playbin', 'playbin')
            if self._playbin:
                log.debug('created playbin (fallback)')
            else:
                raise RuntimeError(
                    'GStreamer playbin not available. '
                    'Install gstreamer1.0-plugins-base.'
                )
        self._setup_video_sink()
        self._setup_audio_filter()
        self._target_state = Gst.State.NULL
        self._is_buffering = False
        self._rate = 1.0
        self._target_rate = 1.0
        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        self._bus_handler_ids = [
            bus.connect('message::error', self._on_error),
            bus.connect('message::eos', self._on_eos),
            bus.connect('message::state-changed', self._on_state_changed),
            bus.connect('message::buffering', self._on_buffering),
        ]

    def play_uri(self, uri: str):
        if not uri or not uri.startswith(('https://', 'http://')):
            log.error('refusing non-HTTP URI: %s', uri)
            self.emit('error', 'Invalid stream URL')
            return
        log.debug('play_uri: %s', uri)
        self._playbin.set_state(Gst.State.NULL)
        self._playbin.set_property('uri', uri)
        self._target_state = Gst.State.PLAYING
        self._is_buffering = False
        self._rate = 1.0
        self._playbin.set_state(Gst.State.PLAYING)
        self._start_position_timer()

    def play(self):
        log.debug('play')
        self._target_state = Gst.State.PLAYING
        self._playbin.set_state(Gst.State.PLAYING)
        self._start_position_timer()

    def pause(self):
        log.debug('pause')
        self._target_state = Gst.State.PAUSED
        self._playbin.set_state(Gst.State.PAUSED)
        self._stop_position_timer()

    def stop(self):
        log.debug('stop')
        self._stop_position_timer()
        self._target_state = Gst.State.NULL
        self._is_buffering = False
        self._rate = 1.0
        self._playbin.set_state(Gst.State.NULL)

    def toggle_play_pause(self):
        _, state, _ = self._playbin.get_state(0)
        if state == Gst.State.PLAYING:
            self.pause()
        else:
            self.play()

    def seek(self, position_seconds: float):
        position_seconds = max(0.0, position_seconds)
        log.debug('seek → %.1fs (rate=%.2f)', position_seconds, self._target_rate)
        self._playbin.seek(
            self._target_rate,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH | Gst.SeekFlags.ACCURATE,
            Gst.SeekType.SET, int(position_seconds * Gst.SECOND),
            Gst.SeekType.NONE, -1,
        )
        self._rate = self._target_rate

    def get_position(self) -> float:
        ok, pos = self._playbin.query_position(Gst.Format.TIME)
        return pos / Gst.SECOND if ok else 0

    def get_duration(self) -> float:
        ok, dur = self._playbin.query_duration(Gst.Format.TIME)
        return dur / Gst.SECOND if ok else 0

    @property
    def is_playing(self) -> bool:
        _, state, _ = self._playbin.get_state(0)
        return state == Gst.State.PLAYING

    def _start_position_timer(self):
        if not self._position_timer:
            self._position_timer = GLib.timeout_add(500, self._update_position)

    def _stop_position_timer(self):
        if self._position_timer:
            GLib.source_remove(self._position_timer)
            self._position_timer = 0

    def _update_position(self):
        pos = self.get_position()
        dur = self.get_duration()
        self.emit('position-updated', int(pos), int(dur))
        return GLib.SOURCE_CONTINUE

    def _on_error(self, _bus, msg):
        err, debug = msg.parse_error()
        log.error('pipeline error: %s (debug: %s)', err.message, debug)
        self.emit('error', err.message)
        self.stop()

    def _on_eos(self, _bus, _msg):
        log.debug('eos')
        self.emit('eos')
        self.stop()

    def _on_buffering(self, _bus, msg):
        percent = msg.parse_buffering()
        log.debug('buffering %d%%', percent)
        self.emit('buffering', percent)
        if percent < 100:
            if not self._is_buffering:
                self._is_buffering = True
                log.debug('buffering: pausing pipeline')
                self._playbin.set_state(Gst.State.PAUSED)
        else:
            self._is_buffering = False
            if self._target_state == Gst.State.PLAYING:
                log.debug('buffering done: resuming pipeline')
                self._playbin.set_state(Gst.State.PLAYING)

    def _on_state_changed(self, _bus, msg):
        if msg.src != self._playbin:
            return
        old, new, pending = msg.parse_state_changed()
        state_names = {
            Gst.State.NULL: 'stopped',
            Gst.State.PAUSED: 'paused',
            Gst.State.PLAYING: 'playing',
        }
        sn = state_names.get
        log.debug('state: %s → %s (pending: %s)',
                  sn(old, '?'), sn(new, '?'), sn(pending, 'none'))
        if self._rate != self._target_rate and new in (Gst.State.PLAYING, Gst.State.PAUSED):
            log.debug('applying pending rate → %.2f', self._target_rate)
            self._apply_rate()
        self.emit('state-changed', state_names.get(new, 'unknown'))

    def _apply_rate(self):
        log.debug('rate: %.2f → %.2f', self._rate, self._target_rate)
        self._playbin.seek(
            self._target_rate,
            Gst.Format.TIME,
            Gst.SeekFlags.FLUSH,
            Gst.SeekType.NONE, 0,
            Gst.SeekType.NONE, -1,
        )
        self._rate = self._target_rate

    def get_buffered_end(self) -> float:
        try:
            query = Gst.Query.new_buffering(Gst.Format.TIME)
            if not self._playbin.query(query):
                return -1
            n = query.get_n_buffering_ranges()
            max_end = 0
            for i in range(n):
                ok, start, stop = query.parse_nth_buffering_range(i)
                if stop > max_end:
                    max_end = stop
            return max_end / Gst.SECOND if max_end > 0 else -1
        except Exception:
            return -1

    def get_volume(self) -> float:
        return self._playbin.get_property('volume')

    def set_volume(self, volume: float):
        self._playbin.set_property('volume', max(0.0, min(1.0, volume)))

    def set_rate(self, rate: float):
        self._target_rate = max(0.25, min(3.0, rate))
        if self._playbin.get_state(0)[1] in (Gst.State.PLAYING, Gst.State.PAUSED):
            self._apply_rate()

    def cleanup(self):
        if self._cleaned_up:
            return
        self._cleaned_up = True
        log.debug('cleanup')
        bus = self._playbin.get_bus()
        if bus:
            for hid in self._bus_handler_ids:
                bus.disconnect(hid)
            self._bus_handler_ids.clear()
            bus.remove_signal_watch()
        self.stop()
        self._playbin.set_state(Gst.State.NULL)
