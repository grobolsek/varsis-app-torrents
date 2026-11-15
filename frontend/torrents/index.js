// Varsis torrents plugin, web half: this folder is src/plugins/torrents/ in the Varsis frontend.
// The fields below are what the app looks for (see src/plugins.js there).
import { Download } from 'lucide-vue-next'
import TorrentSearch from './components/TorrentSearch.vue'
import { useTorrents } from './store'

export default {
  routes: [
    { path: '/torrents', component: () => import('./views/Torrents.vue') },
    { path: '/torrents/:jobId', component: () => import('./views/TorrentInfo.vue') },
    { path: '/torrent-info/:id', component: () => import('./views/TorrentInfo.vue') },
  ],
  // Hidden while Prowlarr or qBittorrent can't be reached.
  nav: () => (useTorrents().available ? [{ to: '/torrents', label: 'Torrents', icon: Download }] : []),
  searchSections: [TorrentSearch],
  // Re-check the torrent services on each page change (the server caches the answer for 20 s).
  pageChanged: () => useTorrents().check(),
}
