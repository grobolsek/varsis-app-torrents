# TODO (torrents add-on)

The general list is in Varsis' own TODO.md. These are the points about torrents.

## Before anyone else gets the address

- [ ] **Decide the legal side** of offering torrent search to other people.
- [ ] **New qBittorrent password** for production. Don't reuse the one from the development `.env`.
- [ ] **Tests** for the poller (download finished, stalled, too big, repeated errors) and for choosing the files.

## Should do

- [ ] **Torrent traffic that doesn't go through the VPN.** Prowlarr's searches, and the `.torrent` files the
      backend fetches through it, use the server's real IP. Only qBittorrent is behind gluetun.
- [ ] **Pin the gluetun image.** `qmcgaw/gluetun:v3` is a moving tag.
- [ ] **The search rate limit and the "stalled download" timer live in memory.** They reset when the backend
      restarts and only work with one backend process.
- [ ] **Database index** on `torrent_jobs.status` for many jobs.

## Missing features

- [ ] Cancel or remove a download from the UI. A magnet link that never finds its file list stays "downloading"
      forever, and counts against the limit of 5 running downloads.
- [ ] For magnet links every file in the torrent is downloaded (the "best version of each song" choice only works
      for `.torrent` links). Pick the files once qBittorrent has the file list.
- [ ] Show Prowlarr indexer errors in the app (a failing indexer looks like "No matching torrents").

## Known limitations (not bugs)

- After import the torrent is removed from qBittorrent, so nothing is seeded. Private trackers may penalise that.
- Prowlarr's Internet Archive definition: sort "created" is rejected by archive.org and items without a
  `publicdate` pause the indexer. Use sort "downloads". archive.org also throttles Prowlarr (30–90 s per search).
- Local development: Prowlarr (http://localhost:9696) needs its login set up and indexers added. Until then
  torrent search finds nothing.
