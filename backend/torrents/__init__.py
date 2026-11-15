"""Varsis torrents plugin: search torrents with Prowlarr, download with qBittorrent, import into the library.

This folder is `app/plugins/torrents/` in the Varsis backend. The names below are the hooks the app looks for
(see `app/plugins/__init__.py` there).
"""

import asyncio

from . import torrents

router = torrents.router
STARTUP_SQL = [
    "ALTER TABLE torrent_jobs ADD COLUMN IF NOT EXISTS details JSON",  # column added after the first release
    # A restart in the middle of an import left these behind; the poller finishes them (see _import_job).
    "UPDATE torrent_jobs SET status = 'downloading' WHERE status = 'importing'",
]
delete_user = torrents.remove_user_torrents
_poller: asyncio.Task | None = None


async def startup():
    """Start the task that follows the running downloads."""
    global _poller
    _poller = asyncio.create_task(torrents.poll_forever())


async def shutdown():
    """Stop the poller."""
    if _poller:
        _poller.cancel()
