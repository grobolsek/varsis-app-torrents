"""Prowlarr search, qBittorrent download jobs, and the poller that imports finished downloads."""

import asyncio
import contextlib
import hashlib
import logging
import re
import shutil
import time
from pathlib import Path, PurePosixPath
from typing import Annotated
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import httpx
import mutagen
import qbittorrentapi
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError

from ... import sidecars
from ...auth import CurrentUser, storage_used
from ...db import SessionDep, SessionLocal
from ...library import MIME, gb, import_error, import_file, user_lock
from ...models import GB, User
from ...ratelimit import Limiter
from .config import settings
from .models import TorrentJob

router = APIRouter(prefix="/api/torrents", tags=["torrents"])
log = logging.getLogger("varsis.torrents")
POLL_SECONDS = 15
STALL_SECONDS = 600  # no progress for this long -> import what finished
_last_progress: dict[int, tuple[float, float]] = {}  # job id -> (progress, when it last changed)
SWEEP_SECONDS = 600  # how often qBittorrent is checked for torrents no running job owns
ORPHAN_GRACE = 600  # seconds; a torrent newer than this may belong to a job that is just being saved
_last_sweep = 0.0  # time.monotonic() of the last sweep
AUDIO_CATEGORY = 3000  # Newznab/Torznab "Audio"
ACTIVE = ("downloading", "importing")
MAX_ACTIVE = 5  # downloads one user can have running at once
MAX_ARTISTS = 5  # per search: each artist is its own search on every indexer
MAX_TORRENT = 25 * 1024**2  # a .torrent file is a few MB at most
# A download may be this much bigger than the user's free space: magnet links also fetch the non-audio files,
# which are thrown away. Anything bigger is stopped so it can't fill the disk.
SIZE_SLACK = 2 * GB
MAX_FAILURES = 5  # unexpected errors in a row before a job is given up
_failures: dict[int, int] = {}
search_limit = Limiter(30, 10 * 60)  # torrent searches per user


def _qbit() -> qbittorrentapi.Client:
    """Make a client for the configured qBittorrent. Its calls block, so they are run in a thread."""
    return qbittorrentapi.Client(
        host=settings.qbit_url,
        username=settings.qbit_user,
        password=settings.qbit_pass,
        REQUESTS_ARGS={"timeout": 15},
    )


def _tag(job_id: int) -> str:
    """Name the qBittorrent tag that marks a torrent as belonging to a job."""
    return f"varsis-{job_id}"


def job_out(j: TorrentJob):
    """Describe a download job the way the frontend gets it."""
    return {
        "id": j.id,
        "title": j.title,
        "status": j.status,
        "progress": j.progress,
        "message": j.message,
        "created_at": j.created_at,
        "details": j.details,
    }


# ---------- search & add ----------


# Format words in release titles -> file extension Varsis can play. AAC lives in .m4a files.
FORMAT_PATTERNS = {
    "flac": r"\bflac\b",
    "wav": r"\bwav\b",
    "m4a": r"\b(alac|m4a|aac)\b",
    "mp3": r"\bmp3\b|\b320\s?kbps\b|\bv0\b",
    "ogg": r"\b(ogg|vorbis)\b",
    "opus": r"\bopus\b(?!\s*(no\.?\s*)?\d)",  # not classical "Opus 27"
}
UNSUPPORTED_PATTERN = r"\b(ape|wv|wavpack|dsd\d*|dsf|dff|sacd|aiff?|wma)\b"
MP3_CATEGORY = 3010


def detect_formats(title: str, categories: list[int]) -> tuple[list[str], bool]:
    """Guess the audio formats of a release from its title (or its category).

    Args:
        title: The release title, e.g. "Artist - Album (2020) [FLAC]".
        categories: The ids of the indexer categories the release is in.

    Returns:
        The supported formats the title names, as file extensions (empty when it names none), and whether it
        names a format Varsis can't play.
    """
    t = title.lower()
    found = [fmt for fmt, pattern in FORMAT_PATTERNS.items() if re.search(pattern, t)]
    if not found and MP3_CATEGORY in categories:
        found = ["mp3"]
    return found, bool(re.search(UNSUPPORTED_PATTERN, t))


def _norm(text: str) -> str:
    """Reduce text to lower-case letters and digits with single spaces between, for comparing names."""
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


async def _prowlarr(c: httpx.AsyncClient, query: str) -> list[dict]:
    """Run one audio search on Prowlarr, which asks every indexer it knows.

    Args:
        c: The HTTP client to use.
        query: The search text.

    Returns:
        The results as Prowlarr reports them, at most 100.

    Raises:
        httpx.HTTPError: If Prowlarr can't be reached or answers with an error.
        ValueError: If its answer isn't JSON.
    """
    r = await c.get(
        f"{settings.prowlarr_url}/api/v1/search",
        params={"query": query, "categories": AUDIO_CATEGORY, "type": "search", "limit": 100},
        headers={"X-Api-Key": settings.prowlarr_api_key},
    )
    r.raise_for_status()
    return r.json()


PRIVATE_FIELDS = {"downloadUrl", "magnetUrl"}  # Prowlarr links carry its API key


def _without_key(url: str) -> str:
    """Drop the `apikey` parameter from a Prowlarr download link before it goes to the browser.

    Args:
        url: A download link or a magnet link.

    Returns:
        The link without the key. Magnet links have none and come back unchanged.
    """
    p = urlparse(url)
    if p.scheme == "magnet":
        return url
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True) if k.lower() != "apikey"]
    return urlunparse(p._replace(query=urlencode(query)))


def _with_key(url: str) -> str:
    """Put the server's Prowlarr API key into a download link, replacing any key the link came with."""
    p = urlparse(_without_key(url))
    query = [*parse_qsl(p.query, keep_blank_values=True), ("apikey", settings.prowlarr_api_key)]
    return urlunparse(p._replace(query=urlencode(query)))


def _qbit_up() -> bool:
    """Whether qBittorrent answers and accepts the login, within 3 seconds. Blocks, so run it in a thread."""
    try:
        qbittorrentapi.Client(
            host=settings.qbit_url,
            username=settings.qbit_user,
            password=settings.qbit_pass,
            REQUESTS_ARGS={"timeout": 3},
        ).app_version()
        return True
    except qbittorrentapi.APIError:  # includes connection and login errors
        return False


async def _prowlarr_up() -> bool:
    """Whether Prowlarr is configured and answers its health check, within 3 seconds."""
    if not settings.prowlarr_api_key:
        return False
    try:
        async with httpx.AsyncClient(timeout=3) as c:
            r = await c.get(f"{settings.prowlarr_url}/api/v1/health", headers={"X-Api-Key": settings.prowlarr_api_key})
        return r.is_success
    except httpx.HTTPError:
        return False


_status_cache: tuple[float, dict] | None = None
STATUS_TTL = 20  # seconds


@router.get("/status")
async def status(_: CurrentUser):
    """Whether torrent search/download is usable: Prowlarr and qBittorrent both reachable.

    The answer is remembered for STATUS_TTL seconds, so repeated calls don't each wait on both services.

    Returns:
        `prowlarr` and `qbittorrent`, whether each one is reachable, and `available`, whether both are.
    """
    global _status_cache
    if _status_cache and time.monotonic() - _status_cache[0] < STATUS_TTL:
        return _status_cache[1]
    prowlarr, qbit = await asyncio.gather(_prowlarr_up(), asyncio.to_thread(_qbit_up))
    result = {"prowlarr": prowlarr, "qbittorrent": qbit, "available": prowlarr and qbit}
    _status_cache = (time.monotonic(), result)
    return result


@router.get("/search")
async def search(
    user: CurrentUser,
    q: Annotated[str, Query(max_length=200)] = "",
    artists: Annotated[list[str], Query()] = [],  # noqa: B006 (FastAPI copies defaults)
    formats: Annotated[list[str], Query()] = [],  # noqa: B006
):
    """Search the torrent indexers through Prowlarr.

    Args:
        user: The logged-in user, whose searches are rate limited.
        q: A title to look for.
        artists: Artist names. Each one is searched on its own (together with `q`), and only releases whose title
            names one of them are kept.
        formats: File extensions the user wants, e.g. "flac". Releases whose title names only other formats are
            dropped; ones that name no format are kept.

    Returns:
        The releases with the most seeders first. Each has its title, size, seeders, leechers, indexer, the
        formats its title names, `magnet_only` (its contents can't be checked before downloading), `url` (the
        link to pass to add(), without Prowlarr's key) and `details`, everything else the indexer reported.

    Raises:
        HTTPException: 503 when Prowlarr isn't configured. 422 when neither a title nor an artist was given, or
            more than MAX_ARTISTS artists. 429 after too many searches. 502 when Prowlarr fails.
    """
    if not settings.prowlarr_api_key:
        raise HTTPException(503, "Prowlarr is not configured (PROWLARR_API_KEY)")
    artists = [a.strip() for a in artists if a.strip()]
    if not q.strip() and not artists:
        raise HTTPException(422, "Enter an artist or a title")
    if len(artists) > MAX_ARTISTS or any(len(a) > 200 for a in artists):
        raise HTTPException(422, f"At most {MAX_ARTISTS} artists per search")
    search_limit.hit(str(user.id))  # every search goes out to all indexers; too many gets the server banned there
    queries = [f"{a} {q}".strip() for a in artists] or [q.strip()]
    try:
        # Some indexers are slow (archive.org throttles Prowlarr: 30-90 s per search).
        async with httpx.AsyncClient(timeout=180) as c:
            batches = await asyncio.gather(*(_prowlarr(c, query) for query in queries))
    except (httpx.HTTPError, ValueError) as e:  # ValueError: the answer wasn't JSON
        log.warning("Prowlarr search failed: %s", e)
        raise HTTPException(502, f"Prowlarr error: {type(e).__name__}")

    wanted = {f for f in formats if f in MIME}
    seen, results = set(), []
    for x in (x for batch in batches for x in batch):
        # Prefer the .torrent link: its file list can be checked before downloading (magnets can't).
        url = x.get("downloadUrl") or x.get("magnetUrl")
        url = url and _without_key(url)
        if x.get("protocol") != "torrent" or not url or url in seen:
            continue
        seen.add(url)
        title = x.get("title", "")
        if artists and not any(_norm(a) in _norm(title) for a in artists):
            continue
        found, unsupported = detect_formats(title, [cat.get("id") for cat in x.get("categories") or []])
        if wanted and found and not wanted & set(found):  # names formats, but none of the wanted ones
            continue
        if unsupported and not found:  # names only formats Varsis can't play
            continue
        results.append(
            {
                "title": title,
                "size": x.get("size", 0),
                "seeders": x.get("seeders") or 0,
                "leechers": x.get("leechers") or 0,
                "indexer": x.get("indexer"),
                "formats": found,  # empty = not stated in the title
                "magnet_only": url.startswith("magnet:"),
                "url": url,
                # Everything else the indexer reported, for the info popup/page (download links left out).
                "details": {
                    k: v
                    for k, v in x.items()
                    if k not in PRIVATE_FIELDS
                    and v not in (None, "", [], {})
                    and settings.prowlarr_api_key not in str(v)
                },
            }
        )
    return sorted(results, key=lambda x: x["seeders"], reverse=True)


class AddTorrent(BaseModel):
    """A search result to download."""

    title: str = Field(min_length=1, max_length=500)
    url: str = Field(max_length=10_000)  # magnet link or a Prowlarr download link from /search
    info: dict | None = None  # the search result, kept for the job's info page


class TorrentUrl(BaseModel):
    """The link of a search result."""

    url: str = Field(max_length=10_000)


DOWNLOAD_PATH = re.compile(r"/\d+/download/?$")  # Prowlarr's link for one search result: /<indexer id>/download


async def _resolve(url: str) -> tuple[str | None, bytes | None]:
    """Turn a search result's link into something qBittorrent can take.

    Args:
        url: A magnet link, or a download link of this server's Prowlarr (without the API key).

    Returns:
        Either (the magnet link, None) or (None, the content of the .torrent file).

    Raises:
        HTTPException: 400 when the link is neither of the two. 502 when the .torrent can't be fetched or is
            bigger than MAX_TORRENT.
    """
    if url.startswith("magnet:"):
        return url, None
    # Only follow download links of our Prowlarr, so users can't make the server fetch arbitrary URLs
    # (or call the rest of Prowlarr's API with the server's key).
    p, ours = urlparse(url), urlparse(settings.prowlarr_url)
    if p.scheme not in ("http", "https") or p.netloc != ours.netloc or not DOWNLOAD_PATH.search(p.path):
        raise HTTPException(400, "URL must be a magnet link or a Prowlarr download link")
    url = _with_key(url)  # search results leave the key out
    try:
        async with httpx.AsyncClient(timeout=60) as c:
            for _ in range(5):  # Prowlarr may redirect to a magnet link or to the indexer's .torrent
                async with c.stream("GET", url) as r:
                    if r.is_redirect:
                        url = urljoin(url, r.headers.get("location", ""))
                        if url.startswith("magnet:"):
                            return url, None
                        if not url.startswith(("http://", "https://")):
                            break
                        continue
                    if r.status_code != 200:
                        raise HTTPException(502, f"Could not fetch torrent (HTTP {r.status_code})")
                    data = b""
                    async for chunk in r.aiter_bytes():
                        data += chunk
                        if len(data) > MAX_TORRENT:  # not a .torrent; don't read it all into memory
                            raise HTTPException(502, "Could not fetch torrent (the file is too big)")
                    return None, data
    except httpx.HTTPError as e:  # the message can contain the link with Prowlarr's key: log only its kind
        log.warning("fetching a torrent failed: %s", type(e).__name__)
        raise HTTPException(502, "Could not fetch torrent (Prowlarr or the indexer didn't answer)")
    raise HTTPException(502, "Could not fetch torrent (too many redirects)")


# What a broken or hostile .torrent can raise in the functions below.
BAD_TORRENT = (ValueError, KeyError, IndexError, TypeError, AttributeError, RecursionError)


def _bdecode(data: bytes, i: int = 0, depth: int = 0):
    """Decode one bencoded value. A minimal decoder: enough to read a .torrent's file list.

    Bencode is the format of .torrent files. It has integers, byte strings, lists and dicts.

    Args:
        data: The bencoded bytes.
        i: The index the value starts at.
        depth: How deeply nested this value is. The caller leaves it at 0.

    Returns:
        The value (an int, bytes, a list or a dict with bytes keys) and the index right after it.

    Raises:
        ValueError: If the data is nested more than 100 levels deep or isn't valid bencode. Broken data can also
            raise the other errors in BAD_TORRENT.
    """
    if depth > 100:
        raise ValueError("nested too deeply")
    c = data[i : i + 1]
    if c == b"i":  # integer: i<digits>e
        end = data.index(b"e", i)
        return int(data[i + 1 : end]), end + 1
    if c in (b"l", b"d"):  # list: l<values>e, dict: d<key><value>…e
        items, i = [], i + 1
        while data[i : i + 1] != b"e":
            value, i = _bdecode(data, i, depth + 1)
            items.append(value)
        return (dict(zip(items[::2], items[1::2])) if c == b"d" else items), i + 1
    colon = data.index(b":", i)  # byte string: <length>:<bytes>
    n = int(data[i:colon])
    # A negative length would move the position backwards: an endless loop that eats all memory.
    if n < 0 or colon + 1 + n > len(data):
        raise ValueError("bad string length")
    return data[colon + 1 : colon + 1 + n], colon + 1 + n


def torrent_files(torrent: bytes) -> list[str]:
    """List the file paths inside a .torrent, in qBittorrent's file order.

    Args:
        torrent: The content of the .torrent file.

    Returns:
        One path per file, with "/" between folders. A single-file torrent gives just its name.

    Raises:
        HTTPException: 502 when the .torrent can't be read.
    """
    try:
        info = _bdecode(torrent)[0][b"info"]
        if b"files" not in info:  # single-file torrent
            return [info[b"name"].decode(errors="replace")]
        return ["/".join(part.decode(errors="replace") for part in f[b"path"]) for f in info[b"files"]]
    except BAD_TORRENT:
        raise HTTPException(502, "Could not read the .torrent file")


def torrent_file_sizes(torrent: bytes) -> list[int]:
    """List the file sizes inside a .torrent, in the same order as torrent_files().

    Args:
        torrent: The content of the .torrent file.

    Returns:
        One size in bytes per file.

    Raises:
        HTTPException: 502 when the .torrent can't be read or a size isn't a whole number from 0 up.
    """
    try:
        info = _bdecode(torrent)[0][b"info"]
        sizes = [f[b"length"] for f in info[b"files"]] if b"files" in info else [info[b"length"]]
        if not all(isinstance(s, int) and s >= 0 for s in sizes):
            raise ValueError("bad file size")
        return sizes
    except BAD_TORRENT:
        raise HTTPException(502, "Could not read the .torrent file")


def info_hash(torrent: bytes) -> str:
    """Compute qBittorrent's id for a .torrent: SHA-1 of the raw bencoded `info` dict.

    Args:
        torrent: The content of the .torrent file.

    Returns:
        The hash as 40 hex digits.

    Raises:
        HTTPException: 502 when the .torrent can't be read or has no `info`.
    """
    try:
        # Walk the key/value pairs of the outer dict. The hash is over the bytes exactly as they are in the file,
        # so what's needed is where the `info` value starts and ends, not its decoded content.
        i = 1  # skip the "d" that opens the outer dict
        while torrent[i : i + 1] != b"e":
            key, i = _bdecode(torrent, i)
            start = i
            _, i = _bdecode(torrent, i)
            if key == b"info":
                return hashlib.sha1(torrent[start:i]).hexdigest()
    except BAD_TORRENT:
        pass
    raise HTTPException(502, "Could not read the .torrent file")


MAGNET_HASH = re.compile(r"\bbtih:([0-9a-fA-F]{40})\b")


def _qbit_add(magnet: str | None, torrent: bytes | None, job_id: int, skip: list[int]):
    """Add a torrent to qBittorrent. Blocks, so run it in a thread.

    qBittorrent rejects file priorities on upload, so a torrent with files to skip is added stopped, gets its
    priorities set and is then started.

    Args:
        magnet: The magnet link, or None when `torrent` is given.
        torrent: The content of the .torrent file, or None when `magnet` is given.
        job_id: The job this download belongs to. It names the tag and the download folder.
        skip: Indexes of the files that must not be downloaded (priority 0). Empty for a magnet link.

    Raises:
        HTTPException: 409 when qBittorrent already has this torrent. 502 when it refuses the torrent or the
            file selection.
        qbittorrentapi.APIError: If qBittorrent can't be reached or the login fails.
    """
    q = _qbit()
    found = MAGNET_HASH.search(magnet) if magnet else None
    h = info_hash(torrent) if torrent else found and found.group(1).lower()
    # qBittorrent keeps one copy per torrent. A second job for it would never get its own download (and would
    # change the file selection of the first one).
    if h and q.torrents_info(torrent_hashes=h):
        raise HTTPException(409, "This torrent is already being downloaded. Try again when that download is done.")
    result = q.torrents_add(
        urls=magnet,
        torrent_files=torrent,
        save_path=f"{settings.downloads_dir}/{job_id}",
        tags=_tag(job_id),
        category="varsis",
        is_stopped=bool(skip),
    )
    if result == "Fails.":  # older API returns "Ok."/"Fails.", v2.14+ returns JSON metadata
        raise HTTPException(502, "qBittorrent refused the torrent")
    if not skip:
        return
    try:
        for _ in range(20):  # the torrent can take a moment to appear
            try:
                q.torrents_file_priority(torrent_hash=h, file_ids=skip, priority=0)
                break
            except (qbittorrentapi.NotFound404Error, qbittorrentapi.Conflict409Error):
                time.sleep(0.5)
        else:  # never start it with every file selected
            raise HTTPException(502, "qBittorrent didn't accept the file selection")
        q.torrents_start(torrent_hashes=h)
    except BaseException:
        with contextlib.suppress(qbittorrentapi.APIError):  # don't leave a half set up torrent behind
            q.torrents_delete(delete_files=True, torrent_hashes=h)
        raise


def _supported(path: str) -> bool:
    """Whether a file is audio Varsis can play, going by its extension."""
    return Path(path).suffix.lower().lstrip(".") in MIME


QUALITY = ["flac", "wav", "m4a", "mp3", "ogg", "opus"]  # best first
LOW_QUALITY_COPY = re.compile(r"_(64kb|vbr|128kb|256kb|320kb)$", re.IGNORECASE)  # archive.org derivatives


def song_key(path: str) -> tuple[str, str]:
    """Make the key that tells which files are versions of the same song (Song.flac, Song.mp3, Song_64kb.mp3).

    Args:
        path: A file's path inside the torrent.

    Returns:
        Its folder, and its name without the extension and without a low-quality marker, both in lower case.
    """
    p = Path(path)
    return str(p.parent).lower(), LOW_QUALITY_COPY.sub("", p.stem).lower()


def best_versions(paths: list[str]) -> list[int]:
    """Choose one file per song: the best supported format.

    A song is the files that share a folder and a name (see song_key()). Formats rank as in QUALITY, and
    archive.org's re-encoded copies lose against any original.

    Args:
        paths: The paths of all files in the torrent.

    Returns:
        The indexes of the files to keep, in ascending order.
    """
    best = {}  # (folder, name) -> (rank, index)
    for i, path in enumerate(paths):
        p = Path(path)
        ext = p.suffix.lower().lstrip(".")
        if ext not in MIME:
            continue
        # Lower is better. A re-encoded copy is pushed behind every original, whatever its format.
        rank = QUALITY.index(ext) + (len(QUALITY) if LOW_QUALITY_COPY.search(p.stem) else 0)
        key = song_key(path)
        if key not in best or rank < best[key][0]:
            best[key] = (rank, i)
    return sorted(i for _, i in best.values())


def wanted_files(paths: list[str], sizes: list[int]) -> list[int]:
    """Choose the files of a torrent to download.

    That is best_versions(), plus the small cover pictures and XML/NFO files in the songs' folders (or folders above
    them, or a cover in their Artwork/Scans subfolder) for album covers and missing tags.

    Args:
        paths: The paths of all files in the torrent.
        sizes: Their sizes in bytes, in the same order.

    Returns:
        The indexes of the files to download, in ascending order.
    """
    songs = best_versions(paths)
    dirs = {d for i in songs for d in PurePosixPath(paths[i]).parents}  # every folder with a song in or below it

    def near_songs(p: PurePosixPath) -> bool:
        """Whether a sidecar is where find() would use it: beside or above a song, or a cover in an art folder."""
        if p.parent in dirs:
            return True
        rank = sidecars.cover_rank(p)
        art_dir = p.parent.name.lower() in sidecars.ART_DIRS and p.parent.parent in dirs
        return art_dir and rank is not None and rank < sidecars.PLAIN

    extras = [
        i
        for i, (path, size) in enumerate(zip(paths, sizes))
        if size <= sidecars.MAX_SIDECAR and sidecars.useful(path) and near_songs(PurePosixPath(path))
    ]
    return sorted({*songs, *extras})


@router.post("", status_code=201)
async def add(data: AddTorrent, db: SessionDep, user: CurrentUser):
    """Start downloading a search result.

    With a .torrent link the contents are checked first: only the best version of each song is downloaded (plus
    covers and metadata), and the songs must fit in the user's storage. A magnet link's contents aren't known yet,
    so the poller stops it later if it turns out too big.

    Args:
        data: The search result.
        db: Database session.
        user: The logged-in user, who owns the download and whose storage it counts against.

    Returns:
        The new job (see job_out()).

    Raises:
        HTTPException: 403 when the user's storage is full or too small for the songs. 429 when they already
            have MAX_ACTIVE downloads running. 413 when `info` is too big. 400 when the link isn't allowed or
            the torrent has no supported audio. 409 when the torrent is already downloading. 502 when Prowlarr,
            the indexer or qBittorrent fails.
    """
    free = None if user.quota is None else user.quota - await storage_used(db, user.id)
    if free is not None and free <= 0:
        raise HTTPException(403, "Your storage is full. Ask an admin for more space.")
    running = await db.scalar(
        select(func.count()).select_from(TorrentJob).where(TorrentJob.user_id == user.id, TorrentJob.status.in_(ACTIVE))
    )
    if running >= MAX_ACTIVE:
        raise HTTPException(429, f"You already have {running} downloads running. Wait for one of them to finish.")
    if len(str(data.info or "")) > 100_000:
        raise HTTPException(413, "Too much torrent info")
    magnet, torrent = await _resolve(data.url)
    skip, details = [], {k: v for k, v in (data.info or {}).items() if k != "url"}
    if torrent:  # check the contents; download only supported audio files (+ their covers and metadata)
        files, sizes = torrent_files(torrent), torrent_file_sizes(torrent)
        songs, keep = best_versions(files), set(wanted_files(files, sizes))
        details["info_hash"] = info_hash(torrent)
        details["contents"] = [
            {"path": p, "size": s, "download": i in keep} for i, (p, s) in enumerate(zip(files, sizes))
        ]
        skip = [i for i in range(len(files)) if i not in keep]
        if not songs:
            exts = sorted({Path(f).suffix.lower() or "(none)" for f in files})[:8]
            raise HTTPException(400, f"No supported audio files in this torrent (contains: {', '.join(exts)})")
        need = sum(sizes[i] for i in songs)
        if free is not None and need > free:  # torrents count against the storage limit like uploads do
            raise HTTPException(
                403,
                f"Not enough storage left: these songs take {gb(need)} and you have {gb(free)} free. "
                "Ask an admin for more space.",
            )
    job = TorrentJob(user_id=user.id, title=data.title, details=details or None)
    db.add(job)
    await db.flush()  # gives the job its id, which names the tag and folder; nothing is committed if adding fails
    try:
        await asyncio.to_thread(_qbit_add, magnet, torrent, job.id, skip)
    except qbittorrentapi.APIError as e:
        raise HTTPException(502, f"qBittorrent error: {e}")
    if skip:
        extra = ", plus cover art and metadata" if len(keep) > len(songs) else ""
        job.message = f"Downloading {len(keep)} of {len(files)} files (best version of each song{extra})"
    await db.commit()
    return job_out(job)


@router.post("/files")
async def files(data: TorrentUrl, _: CurrentUser):
    """List the files inside a search result's .torrent, and which ones a download would fetch.

    Args:
        data: The link of the search result.

    Returns:
        `files`, each with its path, size and `download`; `info_hash`; and `magnet`, which is true for a magnet
        link, whose files can't be listed.

    Raises:
        HTTPException: 400 when the link isn't allowed, 502 when the .torrent can't be fetched or read.
    """
    _, torrent = await _resolve(data.url)
    if not torrent:
        return {"magnet": True, "info_hash": None, "files": []}
    paths, sizes = torrent_files(torrent), torrent_file_sizes(torrent)
    keep = set(wanted_files(paths, sizes))
    return {
        "magnet": False,
        "info_hash": info_hash(torrent),
        "files": [{"path": p, "size": s, "download": i in keep} for i, (p, s) in enumerate(zip(paths, sizes))],
    }


@router.get("")
async def jobs(db: SessionDep, user: CurrentUser):
    """List the user's downloads.

    Args:
        db: Database session.
        user: The logged-in user.

    Returns:
        Their 100 most recent jobs, newest first (see job_out()).
    """
    rows = await db.scalars(
        select(TorrentJob).where(TorrentJob.user_id == user.id).order_by(TorrentJob.id.desc()).limit(100)
    )
    return [job_out(j) for j in rows]


LIVE_FIELDS = [  # qBittorrent torrent properties shown on a download's info page
    "state",
    "dlspeed",
    "upspeed",
    "eta",
    "num_seeds",
    "num_complete",
    "num_leechs",
    "num_incomplete",
    "size",
    "downloaded",
    "uploaded",
    "ratio",
    "availability",
    "added_on",
    "hash",
    "tracker",
]


def _live(job_id: int):
    """Ask qBittorrent how a job's torrent is doing right now. Blocks, so run it in a thread.

    Args:
        job_id: The job.

    Returns:
        The torrent's LIVE_FIELDS plus `files` with each file's progress. None when qBittorrent doesn't have the
        torrent (any more).

    Raises:
        qbittorrentapi.APIError: If qBittorrent can't be reached.
    """
    q = _qbit()
    t = next(iter(q.torrents_info(tag=_tag(job_id))), None)
    if t is None:
        return None
    files = q.torrents_files(torrent_hash=t.hash)
    return {
        **{k: t.get(k) for k in LIVE_FIELDS},
        "files": [
            {"path": f.name, "size": f.size, "progress": f.progress, "download": bool(f.priority)} for f in files
        ],
    }


@router.get("/{job_id}")
async def job_info(job_id: int, db: SessionDep, user: CurrentUser):
    """Get one download, plus live qBittorrent stats while it's still in qBittorrent.

    Args:
        job_id: The job.
        db: Database session.
        user: The logged-in user.

    Returns:
        The job (see job_out()) plus `live` (see _live()), which is None once the download is over or when
        qBittorrent doesn't answer.

    Raises:
        HTTPException: 404 when the job doesn't exist or is someone else's.
    """
    job = await db.get(TorrentJob, job_id)
    if job is None or job.user_id != user.id:
        raise HTTPException(404, "Download not found")
    live = None
    if job.status in ("downloading", "importing"):
        try:
            live = await asyncio.to_thread(_live, job_id)
        except qbittorrentapi.APIError:
            pass  # show the stored info only
    return {**job_out(job), "live": live}


# ---------- poller ----------


async def _discard(qbit: qbittorrentapi.Client, job_id: int, torrent_hash: str | None):
    """Best effort: remove a job's torrent from qBittorrent, and its download folder.

    Args:
        qbit: The qBittorrent client.
        job_id: The job, which names the download folder.
        torrent_hash: The torrent's id in qBittorrent. None when qBittorrent doesn't have it.
    """
    if torrent_hash:
        with contextlib.suppress(qbittorrentapi.APIError):
            await asyncio.to_thread(qbit.torrents_delete, delete_files=True, torrent_hashes=torrent_hash)
    await asyncio.to_thread(shutil.rmtree, Path(settings.downloads_dir) / str(job_id), True)


async def remove_user_torrents(db, user_id: int):
    """Stop a user's running downloads before their account (and with it their jobs) is deleted.

    If qBittorrent can't be reached, that is logged and the account is deleted anyway; the poller removes the
    torrents later (see _sweep).

    Args:
        db: Database session.
        user_id: The user.
    """
    ids = list(
        await db.scalars(select(TorrentJob.id).where(TorrentJob.user_id == user_id, TorrentJob.status.in_(ACTIVE)))
    )
    if not ids:
        return
    try:
        qbit = _qbit()
        tags = {_tag(i) for i in ids}
        torrents = await asyncio.to_thread(qbit.torrents_info, category="varsis")
        for t in torrents:
            if t.tags in tags:
                await _discard(qbit, int(t.tags.removeprefix("varsis-")), t.hash)
    except qbittorrentapi.APIError:
        log.warning("could not remove the torrents of deleted user %s from qBittorrent", user_id)


async def _import_job(db, job: TorrentJob, qbit: qbittorrentapi.Client, torrent_hash: str, stalled=False):
    """Import the finished audio files of a job (best version of each song), then remove the torrent.

    Files that can't be imported are skipped and named in the job's message. The job's final status and message
    are set here; the caller commits them.

    Args:
        db: Database session.
        job: The job, already marked "importing".
        qbit: The qBittorrent client.
        torrent_hash: The torrent's id in qBittorrent.
        stalled: The torrent stopped progressing, so import what finished and report the rest.
    """
    job_id, user_id = job.id, job.user_id  # rollback() expires ORM objects; keep plain values
    user = await db.get(User, user_id)
    quota = user.quota if user else None
    folder = (Path(settings.downloads_dir) / str(job_id)).resolve()
    files = await asyncio.to_thread(qbit.torrents_files, torrent_hash=torrent_hash)
    done = [f.name for f in files if f.priority and f.progress >= 1]
    got = {song_key(name) for name in done if _supported(name)}
    missing = len({song_key(f.name) for f in files if f.priority and _supported(f.name)} - got)  # songs, not files

    def inside(p: Path) -> bool:
        """A real file of this download: file names come from the torrent, so don't trust ".." or links."""
        return not p.is_symlink() and p.resolve().is_relative_to(folder)

    imported, earlier, skipped = 0, 0, []
    audio = [folder / done[i] for i in best_versions(done)]
    extras = sidecars.find(folder, audio, [p for name in done if sidecars.useful(name) and inside(p := folder / name)])
    for f in audio:
        try:
            if not inside(f):
                raise ValueError("not a file inside the download")
            if not f.exists():  # a restart interrupted this import: the file is already in the library
                earlier += 1
                continue
            async with user_lock(user_id):
                if quota is not None and await storage_used(db, user_id) + f.stat().st_size > quota:
                    raise ValueError("not enough storage left")
                await import_file(db, f, f.name, user_id, extras[f])
            imported += 1
        except (ValueError, OSError, mutagen.MutagenError) as e:
            await db.rollback()
            skipped.append(f"{f.name}: {import_error(e)}")
        except SQLAlchemyError:
            await db.rollback()
            log.exception("import of %s (job %s) failed", f.name, job_id)
            skipped.append(f"{f.name}: database error")
    await asyncio.to_thread(qbit.torrents_delete, delete_files=True, torrent_hashes=torrent_hash)
    await asyncio.to_thread(shutil.rmtree, folder, True)
    job = await db.get(TorrentJob, job_id)
    job.status = "done" if imported or earlier else "failed"
    job.message = f"Imported {imported + earlier} track(s)" + (f"; skipped: {'; '.join(skipped)}" if skipped else "")
    if stalled and missing:
        job.message += f"; {missing} song(s) couldn't be downloaded (no source had them)"
    if not imported and not earlier and not skipped:
        job.message = "No supported audio files (flac, wav, m4a, mp3, ogg, opus) in this torrent"


async def _stalled_with_files(qbit: qbittorrentapi.Client, job_id: int, t) -> bool:
    """Whether the torrent hasn't progressed for STALL_SECONDS and some wanted audio file is complete.

    Args:
        qbit: The qBittorrent client.
        job_id: The job.
        t: The torrent as qBittorrent reports it.

    Returns:
        True when it is time to stop waiting and import what there is.
    """
    last = _last_progress.get(job_id)
    if last is None or t.progress > last[0]:  # first look, or it moved: restart the clock
        _last_progress[job_id] = (t.progress, time.monotonic())
        return False
    if time.monotonic() - last[1] < STALL_SECONDS:
        return False
    files = await asyncio.to_thread(qbit.torrents_files, torrent_hash=t.hash)
    return any(f.priority and f.progress >= 1 and _supported(f.name) for f in files)


async def _too_big(db, job: TorrentJob, t) -> bool:
    """Whether the download is far bigger than the space its user has left.

    The size of a magnet link is only known once qBittorrent has its file list, so this is checked while it
    downloads, not when it's added.

    Args:
        db: Database session.
        job: The job.
        t: The torrent as qBittorrent reports it.

    Returns:
        True when its size is more than the user's free space plus SIZE_SLACK. Always False for a user without
        a limit, and while the size isn't known.
    """
    user = await db.get(User, job.user_id)
    if not user or user.quota is None or (t.size or 0) <= 0:
        return False
    return t.size > max(0, user.quota - await storage_used(db, user.id)) + SIZE_SLACK


async def _poll_job(db, qbit: qbittorrentapi.Client, job_id: int, t):
    """Move one downloading job forward, going by the state of its torrent.

    The job fails when the torrent is gone, broken or too big. It is imported when the torrent is complete or
    has stalled with finished songs. Otherwise only its progress is updated.

    Args:
        db: Database session.
        qbit: The qBittorrent client.
        job_id: The job.
        t: The torrent as qBittorrent reports it, None when qBittorrent doesn't have it.
    """
    job = await db.get(TorrentJob, job_id)
    if job is None:  # its user was deleted meanwhile
        return
    if t is None:
        job.status, job.message = "failed", "Torrent disappeared from qBittorrent"
        await _discard(qbit, job_id, None)
    elif t.state in ("error", "missingFiles"):
        job.status, job.message = "failed", f"qBittorrent state: {t.state}"
        await _discard(qbit, job_id, t.hash)
    elif t.progress >= 1 or await _stalled_with_files(qbit, job_id, t):
        stalled = t.progress < 1
        job.status, job.progress = "importing", t.progress
        await db.commit()
        await _import_job(db, job, qbit, t.hash, stalled)
    elif await _too_big(db, job, t):
        job.status = "failed"
        job.message = f"Stopped: this torrent is {gb(t.size)}, more than the storage you have left"
        await _discard(qbit, job_id, t.hash)
    else:
        job.progress = t.progress
        await db.commit()
        return
    # Every other branch ended the job: forget what was remembered about it and save its final state.
    _last_progress.pop(job_id, None)
    _failures.pop(job_id, None)
    await db.commit()


async def _sweep(db, qbit: qbittorrentapi.Client, torrents):
    """Remove our torrents that no running job owns, with their files.

    That is what's left when qBittorrent couldn't be reached at the moment a job failed or its user was deleted.
    Without this they would download and seed forever, outside every storage limit.

    Args:
        db: Database session.
        qbit: The qBittorrent client.
        torrents: The torrents in our category, as qBittorrent lists them.
    """
    running = set(await db.scalars(select(TorrentJob.id).where(TorrentJob.status.in_(ACTIVE))))
    for t in torrents:
        tag = re.fullmatch(r"varsis-(\d+)", t.tags or "")
        if not tag:  # not added by us: leave it alone
            continue
        job_id = int(tag.group(1))
        # add() puts the torrent into qBittorrent a moment before it saves the job, hence the grace period.
        if job_id in running or time.time() - (t.added_on or time.time()) < ORPHAN_GRACE:
            continue
        log.warning("removing torrent %s: its job %s is finished or gone", t.hash, job_id)
        await _discard(qbit, job_id, t.hash)


async def poll_once():
    """Check every downloading job against qBittorrent, one after the other.

    A job that raises an unexpected error is tried again on the next poll, and given up after MAX_FAILURES errors
    in a row. Every SWEEP_SECONDS, torrents that no running job owns are removed first (see _sweep).

    Raises:
        qbittorrentapi.APIError: If qBittorrent can't be reached while jobs are downloading.
    """
    global _last_sweep
    async with SessionLocal() as db:
        ids = list(await db.scalars(select(TorrentJob.id).where(TorrentJob.status == "downloading")))
        sweep = time.monotonic() - _last_sweep >= SWEEP_SECONDS
        if not ids and not sweep:
            return
        qbit = _qbit()
        try:
            torrents = await asyncio.to_thread(qbit.torrents_info, category="varsis")
        except qbittorrentapi.APIError:
            if ids:
                raise
            _last_sweep = time.monotonic()  # nothing is downloading and qBittorrent is away: look again later
            return
        if sweep:
            _last_sweep = time.monotonic()
            await _sweep(db, qbit, torrents)
        by_tag = {t.tags: t for t in torrents}
        for job_id in ids:
            t = by_tag.get(_tag(job_id))
            try:
                await _poll_job(db, qbit, job_id, t)
            except Exception:  # one broken job must not stop the others (or stay "importing" forever)
                log.exception("torrent job %s failed", job_id)
                await db.rollback()
                job = await db.get(TorrentJob, job_id)
                if job is None:
                    continue
                _failures[job_id] = _failures.get(job_id, 0) + 1
                if _failures[job_id] < MAX_FAILURES:
                    job.status = "downloading"  # try again on the next poll; finished imports aren't repeated
                else:
                    job.status, job.message = "failed", "Gave up after repeated errors (details are in the server log)"
                    await _discard(qbit, job_id, t.hash if t else None)
                    _failures.pop(job_id, None)
                await db.commit()


async def poll_forever():
    """Run poll_once() every POLL_SECONDS until the task is cancelled. Started at app startup (see __init__.py)."""
    while True:
        try:
            await poll_once()
        except Exception:  # keep polling even if qBittorrent/DB hiccups
            log.exception("torrent poll failed")
        await asyncio.sleep(POLL_SECONDS)
