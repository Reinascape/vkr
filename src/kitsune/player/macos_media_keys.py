# SPDX-License-Identifier: GPL-3.0-or-later
"""macOS media key integration via MPRemoteCommandCenter / MPNowPlayingInfoCenter.

Uses PyObjC to talk directly to MediaPlayer.framework.  All callbacks from
MPRemoteCommandCenter run on a background queue, so every command is
forwarded into the GLib main loop via GLib.idle_add.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

log = logging.getLogger('kitsune.player.macos_media_keys')

# Conditional import so Linux builds don't break
try:
    import gi
    gi.require_version('GLib', '2.0')
    from gi.repository import GLib
    from Foundation import (
        NSData, NSMutableDictionary, NSNumber, NSString,
    )
    from AppKit import NSImage
    from MediaPlayer import (
        MPChangePlaybackPositionCommandEvent,
        MPMediaItemPropertyAlbumTitle,
        MPMediaItemPropertyArtist,
        MPMediaItemPropertyArtwork,
        MPMediaItemPropertyPlaybackDuration,
        MPMediaItemPropertyTitle,
        MPMediaItemArtwork,
        MPNowPlayingInfoCenter,
        MPNowPlayingInfoPropertyElapsedPlaybackTime,
        MPNowPlayingInfoPropertyPlaybackRate,
        MPRemoteCommandCenter,
        MPRemoteCommandHandlerStatusSuccess,
    )
    _AVAILABLE = True
except Exception as exc:
    log.debug('macOS media keys unavailable: %s', exc)
    _AVAILABLE = False


# -- internal state ----------------------------------------------------------

_g_on_play: callable = None
_g_on_pause: callable = None
_g_on_toggle: callable = None
_g_on_next: callable = None
_g_on_prev: callable = None
_g_on_seek: callable = None
_g_last_artwork_obj = None
_g_last_artwork_url: str | None = None
_g_handlers: list = []  # registered handler references for cleanup


# -- helpers -----------------------------------------------------------------


def _idle_cmd(data):
    fn = data
    if fn is not None:
        try:
            fn()
        except Exception:
            log.exception('macOS media key callback error')
    return GLib.SOURCE_REMOVE


def _dispatch_cmd(fn):
    """Schedule a callback on the GLib main loop."""
    if fn is not None:
        GLib.idle_add(_idle_cmd, fn)


# debounce state per command type
_g_last_toggle: float = 0.0
_g_last_play_pause: float = 0.0


def _dispatch_toggle(fn):
    """Toggle gets 300 ms debounce (macOS may spam it)."""
    global _g_last_toggle
    now = time.monotonic()
    if now - _g_last_toggle < 0.3:
        return
    _g_last_toggle = now
    _dispatch_cmd(fn)


def _dispatch_play_pause(fn):
    """Play/Pause get 100 ms debounce."""
    global _g_last_play_pause
    now = time.monotonic()
    if now - _g_last_play_pause < 0.1:
        return
    _g_last_play_pause = now
    _dispatch_cmd(fn)


# -- artwork helpers ---------------------------------------------------------


def _thumb_path(url: str) -> str:
    h = hashlib.sha256(url.encode()).hexdigest()[:16]
    return os.path.join(
        os.path.expanduser('~'), '.cache', 'kitsune', 'thumbnails',
        f'{h}_square.png',
    )


def _load_square_artwork(url: str):
    """Return NSImage from pre-generated square thumbnail PNG, or None."""
    path = _thumb_path(url)
    if os.path.exists(path):
        img = NSImage.alloc().initWithContentsOfFile_(path)
        if img:
            return img
    return None


# -- public API --------------------------------------------------------------


def init(on_play, on_pause, on_toggle, on_next, on_prev, on_seek):
    """Register MPRemoteCommandCenter handlers.

    Must be called on the GLib main thread (e.g. from PlayerView.__init__).
    """
    if not _AVAILABLE:
        return

    global _g_on_play, _g_on_pause, _g_on_toggle, _g_on_next, _g_on_prev, _g_on_seek
    _g_on_play = on_play
    _g_on_pause = on_pause
    _g_on_toggle = on_toggle
    _g_on_next = on_next
    _g_on_prev = on_prev
    _g_on_seek = on_seek

    cc = MPRemoteCommandCenter.sharedCommandCenter()

    def _play_handler(_event):
        _dispatch_play_pause(_g_on_play)
        return MPRemoteCommandHandlerStatusSuccess

    def _pause_handler(_event):
        _dispatch_play_pause(_g_on_pause)
        return MPRemoteCommandHandlerStatusSuccess

    def _toggle_handler(_event):
        _dispatch_toggle(_g_on_toggle)
        return MPRemoteCommandHandlerStatusSuccess

    def _next_handler(_event):
        _dispatch_cmd(_g_on_next)
        return MPRemoteCommandHandlerStatusSuccess

    def _prev_handler(_event):
        _dispatch_cmd(_g_on_prev)
        return MPRemoteCommandHandlerStatusSuccess

    def _seek_handler(event):
        if hasattr(event, 'positionTime'):
            pos = float(event.positionTime())
        else:
            return MPRemoteCommandHandlerStatusSuccess
        if _g_on_seek is not None:
            def _do_seek():
                _g_on_seek(pos)
                return GLib.SOURCE_REMOVE
            GLib.idle_add(_do_seek)
        return MPRemoteCommandHandlerStatusSuccess

    # Keep references so we can remove them later
    _g_handlers[:] = [
        (cc.playCommand(), _play_handler),
        (cc.pauseCommand(), _pause_handler),
        (cc.togglePlayPauseCommand(), _toggle_handler),
        (cc.nextTrackCommand(), _next_handler),
        (cc.previousTrackCommand(), _prev_handler),
    ]
    for cmd, handler in _g_handlers:
        cmd.addTargetWithHandler_(handler)

    cc.changePlaybackPositionCommand().setEnabled_(True)
    cc.changePlaybackPositionCommand().addTargetWithHandler_(_seek_handler)
    _g_handlers.append((cc.changePlaybackPositionCommand(), _seek_handler))

    log.debug('macOS media keys registered')


def update(title, artist, album, duration_sec, elapsed_sec, is_playing, artwork_url=None):
    """Push current track metadata to MPNowPlayingInfoCenter."""
    if not _AVAILABLE:
        return

    global _g_last_artwork_obj, _g_last_artwork_url

    # Only reload artwork when the track (and thus URL) actually changes.
    if artwork_url and artwork_url != _g_last_artwork_url:
        _g_last_artwork_url = artwork_url
        _g_last_artwork_obj = None
        img = _load_square_artwork(artwork_url)
        if img:
            try:
                _g_last_artwork_obj = MPMediaItemArtwork.alloc().initWithBoundsSize_requestHandler_(
                    img.size(),
                    lambda s: img,
                )
            except Exception:
                log.debug('MPMediaItemArtwork init failed')

    info = NSMutableDictionary.dictionary()
    info[MPMediaItemPropertyTitle] = NSString.stringWithUTF8String_((title or '').encode('utf-8'))
    info[MPMediaItemPropertyArtist] = NSString.stringWithUTF8String_((artist or '').encode('utf-8'))
    info[MPMediaItemPropertyAlbumTitle] = NSString.stringWithUTF8String_((album or '').encode('utf-8'))
    info[MPMediaItemPropertyPlaybackDuration] = NSNumber.numberWithDouble_(float(duration_sec))
    info[MPNowPlayingInfoPropertyElapsedPlaybackTime] = NSNumber.numberWithDouble_(float(elapsed_sec))
    info[MPNowPlayingInfoPropertyPlaybackRate] = NSNumber.numberWithDouble_(1.0 if is_playing else 0.0)

    if _g_last_artwork_obj is not None:
        info[MPMediaItemPropertyArtwork] = _g_last_artwork_obj

    MPNowPlayingInfoCenter.defaultCenter().setNowPlayingInfo_(info)


def update_state(elapsed_sec, is_playing):
    """Lightweight update of elapsed time and play state only."""
    if not _AVAILABLE:
        return

    cur = MPNowPlayingInfoCenter.defaultCenter().nowPlayingInfo()
    if not cur:
        return
    updated = NSMutableDictionary.dictionaryWithDictionary_(cur)
    updated[MPNowPlayingInfoPropertyElapsedPlaybackTime] = NSNumber.numberWithDouble_(float(elapsed_sec))
    updated[MPNowPlayingInfoPropertyPlaybackRate] = NSNumber.numberWithDouble_(1.0 if is_playing else 0.0)
    # Re-attach artwork if it disappeared (macOS sometimes drops it)
    global _g_last_artwork_obj
    if _g_last_artwork_obj is not None and MPMediaItemPropertyArtwork not in updated:
        updated[MPMediaItemPropertyArtwork] = _g_last_artwork_obj
    MPNowPlayingInfoCenter.defaultCenter().setNowPlayingInfo_(updated)


def clear():
    """Clear the Now Playing info and remove command targets."""
    if not _AVAILABLE:
        return

    global _g_last_artwork_obj, _g_last_artwork_url
    global _g_on_play, _g_on_pause, _g_on_toggle, _g_on_next, _g_on_prev, _g_on_seek
    _g_last_artwork_obj = None
    _g_last_artwork_url = None
    MPNowPlayingInfoCenter.defaultCenter().setNowPlayingInfo_(None)

    cc = MPRemoteCommandCenter.sharedCommandCenter()
    for cmd, handler in _g_handlers:
        try:
            cmd.removeTarget_(handler)
        except Exception:
            pass
    _g_handlers.clear()

    _g_on_play = None
    _g_on_pause = None
    _g_on_toggle = None
    _g_on_next = None
    _g_on_prev = None
    _g_on_seek = None
