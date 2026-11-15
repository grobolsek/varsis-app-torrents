import { defineStore } from 'pinia'
import { ref } from 'vue'
import { api } from '@/api'

// Whether torrent search/download works (Prowlarr and qBittorrent both reachable).
// Starts false so torrent buttons don't flash in before the check finishes.
export const useTorrents = defineStore('torrents', () => {
  const available = ref(false)
  async function check() {
    try {
      available.value = (await api('/torrents/status')).available
    } catch {
      available.value = false
    }
  }
  return { available, check }
})
