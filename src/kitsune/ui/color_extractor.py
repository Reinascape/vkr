# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import io
import random

import cairo

import gi

gi.require_version('Gtk', '4.0')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gdk, GdkPixbuf, GLib, Gio



def extract_colors(texture: Gdk.Texture, count: int = 6) -> list[tuple[int, int, int]]:
    """Extract dominant colors from a Gdk.Texture using median cut.
    NOTE: Must be called from the main thread (accesses GDK objects).
    For background threads, use extract_colors_from_bytes() instead.
    """
    png_bytes = texture.save_to_png_bytes()
    return extract_colors_from_bytes(png_bytes, count)


def extract_colors_from_bytes(png_bytes, count: int = 6) -> list[tuple[int, int, int]]:
    """Extract dominant colors from PNG bytes (GLib.Bytes). Thread-safe."""
    stream = Gio.MemoryInputStream.new_from_bytes(png_bytes)
    pixbuf = GdkPixbuf.Pixbuf.new_from_stream(stream, None)
    small = pixbuf.scale_simple(32, 32, GdkPixbuf.InterpType.BILINEAR)

    pixels_data = small.get_pixels()
    n_channels = small.get_n_channels()
    rowstride = small.get_rowstride()
    w = small.get_width()
    h = small.get_height()
    pixels = []
    for y in range(h):
        for x in range(w):
            offset = y * rowstride + x * n_channels
            r, g, b = pixels_data[offset], pixels_data[offset + 1], pixels_data[offset + 2]
            if _is_interesting(r, g, b):
                pixels.append((r, g, b))

    if not pixels:
        return [(80, 80, 120)]

    return _median_cut(pixels, count)


def create_gradient_bytes(colors: list[tuple[int, int, int]], n_points: int = 3,
                          size: int = 64, noise: bool = False) -> bytes:
    """Create gradient PNG bytes. Thread-safe (no GDK access)."""
    render_size = 512 if noise else size
    n_points = max(2, min(n_points, len(colors)))
    chosen = random.sample(colors, n_points)

    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, render_size, render_size)
    ctx = cairo.Context(surface)

    for r, g, b in chosen:
        cx = random.uniform(0.1, 0.9) * render_size
        cy = random.uniform(0.05, 0.55) * render_size
        radius = random.uniform(0.25, 0.5) * render_size
        alpha = random.uniform(0.6, 0.95)

        pattern = cairo.RadialGradient(cx, cy, 0, cx, cy, radius)
        pattern.add_color_stop_rgba(0, r / 255, g / 255, b / 255, alpha)
        pattern.add_color_stop_rgba(1, r / 255, g / 255, b / 255, 0.0)

        ctx.set_source(pattern)
        ctx.paint()

    if noise:
        _apply_noise(surface, render_size)

    buf = io.BytesIO()
    surface.write_to_png(buf)
    return buf.getvalue()


def create_gradient_texture(colors: list[tuple[int, int, int]], n_points: int = 3,
                            size: int = 64, noise: bool = False) -> Gdk.Texture:
    """Create gradient texture. Must be called from the main thread."""
    png_data = create_gradient_bytes(colors, n_points, size, noise)
    gbytes = GLib.Bytes.new(png_data)
    return Gdk.Texture.new_from_bytes(gbytes)


def _apply_noise(surface: cairo.ImageSurface, size: int):
    """Overlay fine uniform monochrome noise for a frosted glass effect."""
    noise_surf = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    data = noise_surf.get_data()
    for i in range(0, len(data), 4):
        val = random.randint(0, 255)
        alpha = random.randint(4, 12)
        pval = val * alpha // 255
        data[i] = pval        # B
        data[i + 1] = pval    # G
        data[i + 2] = pval    # R
        data[i + 3] = alpha   # A
    noise_surf.mark_dirty()

    ctx = cairo.Context(surface)
    ctx.set_operator(cairo.OPERATOR_OVER)
    ctx.set_source_surface(noise_surf, 0, 0)
    ctx.paint()


def _is_interesting(r: int, g: int, b: int) -> bool:
    brightness = (r + g + b) / 3
    if brightness < 20 or brightness > 240:
        return False
    saturation = max(r, g, b) - min(r, g, b)
    return saturation > 15


def _median_cut(pixels: list[tuple[int, int, int]], depth: int) -> list[tuple[int, int, int]]:
    if depth <= 1 or len(pixels) <= 1:
        r = sum(p[0] for p in pixels) // len(pixels)
        g = sum(p[1] for p in pixels) // len(pixels)
        b = sum(p[2] for p in pixels) // len(pixels)
        return [(r, g, b)]

    ranges = []
    for ch in range(3):
        vals = [p[ch] for p in pixels]
        ranges.append(max(vals) - min(vals))

    split_ch = ranges.index(max(ranges))
    pixels.sort(key=lambda p: p[split_ch])
    mid = len(pixels) // 2

    return _median_cut(pixels[:mid], depth // 2) + _median_cut(pixels[mid:], depth - depth // 2)
