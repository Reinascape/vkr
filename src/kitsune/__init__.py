# SPDX-License-Identifier: GPL-3.0-or-later

SITE_URL = 'https://anilibria.top'
API_BASE_URL = SITE_URL + '/api/v1'

# Libadwaita canonical transition timing (stable across Adw 1.x)
ADW_TRANSITION = '200ms cubic-bezier(0.25, 0.46, 0.45, 0.94)'

# Re-export storage modules for backward compatibility
from kitsune.storage import release_cache, tags_store, watch_positions  # noqa: E402, F401
