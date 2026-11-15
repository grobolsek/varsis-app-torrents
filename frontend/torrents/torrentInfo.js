// Turns a torrent search result into labelled rows, shared by the hover popup and the info page.
import { fmtSize } from '@/api'

const LINK_FIELDS = { infoUrl: 'Indexer page', commentUrl: 'Comments', posterUrl: 'Poster' }
// Shown elsewhere, folded into another row, or internal ids.
const SKIP = new Set([
  'title', 'sortTitle', 'size', 'seeders', 'leechers', 'indexer', 'protocol', 'categories', 'publishDate',
  'age', 'ageHours', 'ageMinutes', 'files', 'grabs', 'infoHash', 'subGroup', 'fileName', 'indexerFlags',
  'indexerId', 'downloadClientId', 'guid', ...Object.keys(LINK_FIELDS),
])
const FORMAT_LABELS = { m4a: 'ALAC / M4A' }

const fmtDate = (iso) => {
  const d = new Date(iso)
  if (isNaN(d)) return iso
  const days = Math.floor((Date.now() - d) / 864e5)
  const ago = days < 1 ? 'today' : days < 60 ? `${days} days ago` : days < 730 ? `${Math.round(days / 30)} months ago` : `${Math.round(days / 365)} years ago`
  return `${d.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' })} (${ago})`
}
const humanize = (key) => key.replace(/([a-z])([A-Z])/g, '$1 $2').replace(/^./, (c) => c.toUpperCase())
const show = (v) => (typeof v === 'object' ? JSON.stringify(v) : String(v))
const isUrl = (v) => typeof v === 'string' && /^https?:\/\//.test(v)

/** [{ label, value, href? }] for everything known about a search result. */
export function infoRows(r) {
  const d = r.details ?? {}
  const rows = [
    { label: 'Indexer', value: r.indexer },
    { label: 'Size', value: fmtSize(r.size) },
    { label: 'Files', value: d.files },
    { label: 'Seeders', value: r.seeders },
    { label: 'Leechers', value: r.leechers },
    { label: 'Downloads', value: d.grabs },
    { label: 'Published', value: d.publishDate && fmtDate(d.publishDate) },
    { label: 'Formats', value: r.formats.length ? r.formats.map((f) => FORMAT_LABELS[f] ?? f.toUpperCase()).join(', ') : 'Not stated in the title' },
    { label: 'Categories', value: d.categories?.map((c) => c.name).join(', ') },
    { label: 'Release group', value: d.subGroup },
    { label: 'File name', value: d.fileName },
    { label: 'Indexer flags', value: d.indexerFlags?.length ? d.indexerFlags.join(', ') : null },
    { label: 'Info hash', value: d.infoHash },
    { label: 'Link type', value: r.magnet_only ? 'Magnet (contents can’t be checked before downloading)' : '.torrent file' },
    { label: 'GUID', value: d.guid, href: isUrl(d.guid) ? d.guid : undefined },
    // Only http(s) links become clickable: an indexer could send "javascript:…" here.
    ...Object.entries(LINK_FIELDS).map(([k, label]) => ({ label, value: d[k], href: isUrl(d[k]) ? d[k] : undefined })),
    // Anything else the indexer sent that we don't know by name.
    ...Object.entries(d)
      .filter(([k]) => !SKIP.has(k))
      .map(([k, v]) => ({ label: humanize(k), value: show(v), href: isUrl(v) ? v : undefined })),
  ]
  return rows.filter((row) => row.value !== undefined && row.value !== null && row.value !== '')
}

// The info page opens in a new tab, which can't see this tab's memory, so hand the result over in localStorage.
const KEY = 'varsis-torrent-info'
const MAX_SAVED = 30

export function openInfo(r) {
  const id = Math.random().toString(36).slice(2, 10)
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) || '{}')
    saved[id] = r
    const ids = Object.keys(saved)
    ids.slice(0, Math.max(0, ids.length - MAX_SAVED)).forEach((old) => delete saved[old])
    localStorage.setItem(KEY, JSON.stringify(saved))
  } catch {
    // storage blocked: the page will say the info isn't available
  }
  window.open(`/torrent-info/${id}`, '_blank')
}

export function loadInfo(id) {
  try {
    return JSON.parse(localStorage.getItem(KEY) || '{}')[id] ?? null
  } catch {
    return null
  }
}

const STATUS = { downloading: 'Downloading', importing: 'Importing into library', done: 'Done', failed: 'Failed' }
const dedupe = (rows) => rows.filter((row, i) => rows.findIndex((o) => o.label === row.label) === i)

/** Rows for a download job: its status, then everything saved from the search result. */
export function jobRows(j) {
  const d = j.details ?? {}
  return dedupe(
    [
      { label: 'Status', value: STATUS[j.status] ?? j.status },
      { label: 'Progress', value: j.status === 'downloading' ? `${Math.round(j.progress * 100)}%` : null },
      { label: 'Started', value: j.created_at && fmtDate(j.created_at) },
      { label: 'Result', value: j.message },
      { label: 'Files picked', value: d.contents && `${d.contents.filter((f) => f.download).length} of ${d.contents.length}` },
      ...(d.title ? infoRows(d) : []),
      { label: 'Info hash', value: d.info_hash },
    ].filter((row) => row.value !== undefined && row.value !== null && row.value !== ''),
  )
}

const speed = (b) => (b >= 1024 ** 2 ? `${(b / 1024 ** 2).toFixed(1)} MB/s` : `${Math.round(b / 1024)} KB/s`)
const eta = (s) => (s >= 8640000 ? '∞' : s >= 3600 ? `${Math.floor(s / 3600)} h ${Math.round((s % 3600) / 60)} min` : `${Math.ceil(s / 60)} min`)

/** Live qBittorrent stats for a download that's still running. */
export function liveRows(l) {
  return [
    { label: 'qBittorrent state', value: l.state },
    { label: 'Download speed', value: speed(l.dlspeed) },
    { label: 'Upload speed', value: speed(l.upspeed) },
    { label: 'Time left', value: eta(l.eta) },
    { label: 'Seeds', value: `${l.num_seeds} connected (${l.num_complete} in swarm)` },
    { label: 'Peers', value: `${l.num_leechs} connected (${l.num_incomplete} in swarm)` },
    { label: 'Downloaded', value: `${fmtSize(l.downloaded)} of ${fmtSize(l.size)}` },
    { label: 'Uploaded', value: fmtSize(l.uploaded) },
    { label: 'Ratio', value: l.ratio?.toFixed(2) },
    { label: 'Availability', value: l.availability >= 0 ? l.availability.toFixed(2) : null },
    { label: 'Added to qBittorrent', value: l.added_on && new Date(l.added_on * 1000).toLocaleString() },
    { label: 'Tracker', value: l.tracker },
  ].filter((row) => row.value !== undefined && row.value !== null && row.value !== '')
}
