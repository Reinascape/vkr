# Third-party symbolic icons

A subset of the SVG icons bundled in this directory are sourced from the
[GNOME Adwaita icon theme](https://www.gnome.org), used under the terms
of the GNU Lesser General Public License, version 3
(SPDX: `LGPL-3.0-only`). Upstream offers the work under either LGPL-3.0
or CC-BY-SA-3.0; this project incorporates them under the LGPL-3.0 arm
of that dual-license to remain compatible with our GPL-3.0-or-later
license.

Attribution: **The GNOME Project**, https://www.gnome.org

## Byte-identical copies (no modification)

The following files are byte-identical to the upstream
`icon-theme-adwaita` shipped at
`/usr/share/icons/Adwaita/symbolic/<category>/<name>.svg`. They are
renamed with the `net.armatik.Kitsune.` prefix to keep the GResource
namespace isolated, but the file content is unchanged from upstream.

| Bundled name | Upstream name |
|---|---|
| `net.armatik.Kitsune.applications-graphics-symbolic.svg` | `applications-graphics-symbolic` |
| `net.armatik.Kitsune.audio-volume-high-symbolic.svg` | `audio-volume-high-symbolic` |
| `net.armatik.Kitsune.drive-harddisk-symbolic.svg` | `drive-harddisk-symbolic` |
| `net.armatik.Kitsune.go-previous-symbolic.svg` | `go-previous-symbolic` |
| `net.armatik.Kitsune.go-up-symbolic.svg` | `go-up-symbolic` |
| `net.armatik.Kitsune.media-playback-pause-symbolic.svg` | `media-playback-pause-symbolic` |
| `net.armatik.Kitsune.media-playback-start-symbolic.svg` | `media-playback-start-symbolic` |
| `net.armatik.Kitsune.media-skip-backward-symbolic.svg` | `media-skip-backward-symbolic` |
| `net.armatik.Kitsune.media-skip-forward-symbolic.svg` | `media-skip-forward-symbolic` |
| `net.armatik.Kitsune.non-starred-symbolic.svg` | `non-starred-symbolic` |
| `net.armatik.Kitsune.object-select-symbolic.svg` | `object-select-symbolic` |
| `net.armatik.Kitsune.open-menu-symbolic.svg` | `open-menu-symbolic` |
| `net.armatik.Kitsune.settings-symbolic.svg` | `applications-system-symbolic` |
| `net.armatik.Kitsune.starred-symbolic.svg` | `starred-symbolic` |
| `net.armatik.Kitsune.system-search-symbolic.svg` | `system-search-symbolic` |
| `net.armatik.Kitsune.update-symbolic.svg` | `view-refresh-symbolic` |
| `net.armatik.Kitsune.user-trash-symbolic.svg` | `user-trash-symbolic` |
| `net.armatik.Kitsune.view-fullscreen-symbolic.svg` | `view-fullscreen-symbolic` |
| `net.armatik.Kitsune.view-grid-symbolic.svg` | `view-grid-symbolic` |
| `net.armatik.Kitsune.view-list-bullet-symbolic.svg` | `view-list-bullet-symbolic` |
| `net.armatik.Kitsune.view-list-symbolic.svg` | `view-list-symbolic` |
| `net.armatik.Kitsune.window-close-symbolic.svg` | `window-close-symbolic` |

## Modified derivatives

| Bundled name | Upstream name | Modification |
|---|---|---|
| `net.armatik.Kitsune.image-missing-symbolic.svg` | `image-missing-symbolic` | `fill` changed from hardcoded `#2e3434` to `currentColor` so the icon recolors via GTK's symbolic theming. |

## Custom and other-origin icons

The remaining SVGs in this directory (`funnel-symbolic`, `genres-symbolic`,
`franchises-symbolic`, `magnet-symbolic`, `cross-large-symbolic`,
`seek-backward-10-symbolic`, `seek-forward-10-symbolic`,
`sidebar-collapse-right-symbolic`, `plus-circle-symbolic`,
`home-symbolic`, `angled-arrows-symbolic`) are either project-original
or sourced from libadwaita upstream; they are not part of this notice.

## LGPL-3.0 reference

The full text of LGPL-3.0 is reproduced under `LICENSE` at the project
root (GPL-3.0+ includes LGPL-3.0 by reference).
