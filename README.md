# Varsis torrents

An add-on for [Varsis](../varsis-app): search for music on torrent indexers (Prowlarr), download it with qBittorrent
behind a VPN, and import the finished files into the library. Varsis itself has no torrent code; this repository
plugs into it.

| Here | Becomes in Varsis | What it is |
|---|---|---|
| `backend/torrents/` | `backend/app/plugins/torrents/` | API (`/api/torrents`), download poller, `torrent_jobs` table |
| `frontend/torrents/` | `frontend/src/plugins/torrents/` | Torrents pages, sidebar item, "Search torrents" under the search results |
| `docker-compose.yml` | | Varsis built with the plugin, plus Prowlarr, FlareSolverr, gluetun (VPN) and qBittorrent |

It needs a Varsis version with plugin support (it has the folder `backend/app/plugins/`).

## Run with Docker
You need a checkout of `varsis-app` next to this folder (or set `VARSIS_CORE` to its path), and Docker with the
Buildx plugin (part of Docker Desktop; on Arch: `sudo pacman -S docker-buildx`). The images are built from the
Varsis Dockerfiles, with this plugin passed in as an extra build context.

Run these in this folder:
```bash
cat ../varsis-app/.env.example .env.example > .env   # then fill it in (see the comments in it)
cp vpn.env.example vpn.env                           # then put the VPN key in it
docker compose up --build
```
- **Varsis already running without torrents?** Stop it first (`docker compose down` in `varsis-app`): both use
  port 8080 and the same data folders.
- **Already have a filled-in `.env` in `varsis-app`?** Copy it here instead of the `cat` line and add the settings
  from this folder's `.env.example`. If it still has `WIREGUARD_PRIVATE_KEY`, move that line to `vpn.env`.
- Then do the one-time setup below; the Torrents page only appears once it's done.

| Service | URL |
|---|---|
| App | http://localhost:8080 |
| Mailpit (dev emails) | http://localhost:8025 |
| Prowlarr | http://localhost:9696 |
| qBittorrent | http://localhost:8081 |

It uses the same data folders as Varsis alone (`DATA_DIR`: `postgres`, `music`, `covers`) and adds `downloads`,
`prowlarr` and `qbittorrent`, so you can switch between the two without losing anything. Start one or the other,
not both: they use the same ports.

### One-time setup
1. **qBittorrent**: the temporary admin password is printed in `docker compose logs qbittorrent`. Log in, set a password under Options → Web UI, and put it in `.env` as `QBIT_PASS`.
2. **Prowlarr**: open the UI, add indexers (Indexers → Add), copy the API key from Settings → General into `.env` as `PROWLARR_API_KEY`.
3. `docker compose up -d backend` to reload the settings.

Torrent search and the Torrents page only show up in the app while both Prowlarr and qBittorrent can be reached.
Torrent downloads count against the user's storage limit, like uploads.

## Local development
```bash
./link.sh                                            # links the two folders into ../varsis-app (git ignores them there)
docker compose up -d db mailpit prowlarr qbittorrent
cd ../varsis-app/backend && uv run --with-requirements app/plugins/torrents/requirements.txt uvicorn app.main:app --reload
cd ../varsis-app/frontend && npm run dev
```
Edits made here are picked up by both (reload / hot reload). `./link.sh --remove` takes the links out again, and
Varsis runs without torrents. Docker builds of Varsis itself never include a linked plugin.

### Changing the plugin's Python packages
The Docker build only installs packages that `backend/torrents/requirements.txt` lists with an exact version and
hashes. Edit `requirements.in`, then make `requirements.txt` again (from `varsis-app/backend`; packages the app
already has are held at its versions, and `certifi` and `idna` are left to it):
```bash
uv export --frozen --no-dev --no-hashes --no-emit-project -o /tmp/varsis-core.txt
cd ../../varsis-app-torrents/backend/torrents
uv pip compile requirements.in -c /tmp/varsis-core.txt --generate-hashes --universal --python-version 3.13 \
  --no-emit-package certifi --no-emit-package idna --no-annotate \
  --custom-compile-command "see README.md: Changing the plugin's Python packages" -o requirements.txt
```

Lint the backend half with the Varsis backend's ruff: `uv run ruff check --config ../../varsis-app-torrents/ruff.toml ../../varsis-app-torrents/backend`
(from `varsis-app/backend`).

See [TODO.md](TODO.md) for what's missing and for known limitations.
