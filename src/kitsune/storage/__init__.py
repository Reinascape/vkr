# SPDX-License-Identifier: GPL-3.0-or-later

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


def _atomic_write_json(path: Path, data, *, ensure_ascii: bool = True):
    """Atomically write JSON data to *path* (mkstemp -> write -> replace).

    Handles the fd lifecycle correctly: if os.close succeeds but
    os.replace fails, the fd is not double-closed.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent)
    closed = False
    try:
        os.write(fd, json.dumps(data, ensure_ascii=ensure_ascii).encode())
        # fsync before close+rename so the data is durable on disk: without
        # this a SIGKILL or power loss between writeback and the next
        # commit interval (5-30s on default ext4) leaves a zero-byte file
        # at the destination, and JSON decode then silently drops the
        # pending queue / watch positions / tags store on next read.
        os.fsync(fd)
        os.close(fd)
        closed = True
        os.replace(tmp, path)
    except BaseException:
        if not closed:
            try:
                os.close(fd)
            except OSError:
                pass
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


from kitsune.storage import release_cache, search_index, tags_store, watch_positions  # noqa: E402, F401
