<script setup>
import { LoaderCircle, Magnet, X } from 'lucide-vue-next'
import { ref } from 'vue'
import { api, fmtSize } from '@/api'
import { useTorrents } from '../store'
import { infoRows, openInfo } from '../torrentInfo'
import InfoButton from './InfoButton.vue'

const props = defineProps({ q: { type: String, required: true } })
const torrents = useTorrents()
const FORMATS = [
  { id: 'flac', label: 'FLAC' },
  { id: 'wav', label: 'WAV' },
  { id: 'm4a', label: 'ALAC / M4A' },
  { id: 'mp3', label: 'MP3' },
  { id: 'ogg', label: 'OGG' },
  { id: 'opus', label: 'Opus' },
]
const open = ref(false)
const title = ref(props.q)
const artists = ref([])
const artistInput = ref('')
const formats = ref(new Set(['flac', 'wav', 'm4a'])) // lossless by default
const results = ref(null)
const busy = ref(false)
const error = ref('')
const started = ref(new Map()) // url -> job message

function addArtist() {
  const name = artistInput.value.trim().replace(/,$/, '')
  if (name && !artists.value.includes(name)) artists.value.push(name)
  artistInput.value = ''
}
// Enter or comma turns the typed name into a chip; Backspace on an empty input removes the last one.
function onArtistKey(e) {
  if (e.key === 'Enter' || e.key === ',') {
    e.preventDefault()
    addArtist()
  } else if (e.key === 'Backspace' && !artistInput.value) artists.value.pop()
}
function toggleFormat(id) {
  const next = new Set(formats.value)
  next.has(id) ? next.delete(id) : next.add(id)
  formats.value = next
}

async function search() {
  addArtist()
  error.value = ''
  if (!title.value.trim() && !artists.value.length) return (error.value = 'Enter an artist or a title.')
  const params = new URLSearchParams({ q: title.value.trim() })
  artists.value.forEach((a) => params.append('artists', a))
  formats.value.forEach((f) => params.append('formats', f))
  busy.value = true
  try {
    results.value = await api(`/torrents/search?${params}`)
  } catch (e) {
    error.value = e.message
  } finally {
    busy.value = false
  }
}
async function download(r) {
  error.value = ''
  try {
    const job = await api('/torrents', { method: 'POST', body: { title: r.title, url: r.url, info: r } })
    started.value = new Map(started.value).set(r.url, job.message)
  } catch (e) {
    error.value = `${r.title}: ${e.message}`
  }
}
const label = (id) => FORMATS.find((f) => f.id === id)?.label ?? id
</script>

<template>
  <section v-if="torrents.available" class="rounded-lg bg-white/5 p-5">
    <div class="flex flex-wrap items-center gap-4">
      <Magnet class="h-8 w-8 text-muted" />
      <div class="flex-1">
        <p class="font-bold">Not in your library?</p>
        <p class="text-sm text-muted">Search torrents and the server will download and import it.</p>
      </div>
      <button v-if="!open" class="rounded-full border border-white/30 px-5 py-2 text-sm font-bold hover:scale-105 hover:border-white" @click="open = true">
        Search torrents
      </button>
    </div>

    <form v-if="open" class="mt-5 grid gap-4 md:grid-cols-2" @submit.prevent="search">
      <label class="text-sm font-bold">
        Artists
        <div class="mt-2 flex min-h-11 flex-wrap items-center gap-2 rounded-md bg-white/10 px-2 py-1.5 focus-within:ring-2 focus-within:ring-white">
          <span v-for="(a, i) in artists" :key="a" class="flex items-center gap-1 rounded-full bg-white/15 py-0.5 pr-1 pl-3 font-normal">
            {{ a }}
            <button type="button" class="rounded-full p-0.5 text-muted hover:text-white" :title="`Remove ${a}`" @click="artists.splice(i, 1)"><X class="h-3.5 w-3.5" /></button>
          </span>
          <input
            v-model="artistInput"
            :placeholder="artists.length ? 'Add another' : 'Artist name, Enter to add'"
            class="min-w-32 flex-1 bg-transparent py-1 font-normal outline-none placeholder:text-muted"
            @keydown="onArtistKey"
          />
        </div>
      </label>
      <label class="text-sm font-bold">
        Album or song
        <input v-model="title" placeholder="Optional" class="mt-2 h-11 w-full rounded-md bg-white/10 px-3 font-normal outline-none placeholder:text-muted focus:ring-2 focus:ring-white" />
      </label>
      <div class="text-sm font-bold md:col-span-2">
        File types
        <div class="mt-2 flex flex-wrap gap-2">
          <button
            v-for="f in FORMATS"
            :key="f.id"
            type="button"
            class="rounded-full px-3 py-1.5 font-normal transition-colors"
            :class="formats.has(f.id) ? 'bg-white text-black' : 'bg-white/10 hover:bg-white/20'"
            @click="toggleFormat(f.id)"
          >
            {{ f.label }}
          </button>
        </div>
        <p class="mt-2 font-normal text-muted">Results named with another format are hidden. Unsupported files are skipped when downloading.</p>
      </div>
      <div class="md:col-span-2">
        <button class="flex items-center gap-2 rounded-full bg-accent px-6 py-2.5 font-bold text-black hover:scale-105 disabled:opacity-50" :disabled="busy">
          <LoaderCircle v-if="busy" class="h-4 w-4 animate-spin" /> {{ busy ? 'Searching…' : 'Search' }}
        </button>
      </div>
    </form>

    <p v-if="busy" class="mt-4 text-sm text-muted">Searching your indexers… this can take up to 2 minutes (Internet Archive is slow).</p>
    <p v-if="error" class="mt-4 text-sm text-red-400">{{ error }}</p>
    <p v-if="results && !results.length" class="mt-4 text-sm text-muted">No matching torrents. Try fewer words, another artist spelling, or more file types.</p>
    <div v-if="results?.length" class="mt-4 divide-y divide-white/5">
      <div v-for="r in results" :key="r.url" class="flex items-center gap-4 py-3">
        <div class="min-w-0 flex-1">
          <p class="text-sm break-words">{{ r.title }}</p>
          <p class="mt-1 flex flex-wrap items-center gap-x-1 gap-y-1 text-xs text-muted">
            <span v-for="f in r.formats" :key="f" class="rounded-sm bg-white/15 px-1 text-[10px] font-bold text-neutral-200 uppercase">{{ label(f) }}</span>
            <span v-if="!r.formats.length" class="rounded-sm border border-white/20 px-1 text-[10px] uppercase" title="The title doesn't say which format">unknown</span>
            <span>· {{ fmtSize(r.size) }} · <span class="text-accent">{{ r.seeders }} seeders</span> · {{ r.leechers }} leechers</span>
            <span v-if="r.indexer">· {{ r.indexer }}</span>
            <span v-if="r.magnet_only" title="Magnet links can't be checked before downloading">· magnet, not checked</span>
          </p>
        </div>
        <InfoButton :title="r.title" :rows="infoRows(r)" @open="openInfo(r)" />
        <RouterLink v-if="started.has(r.url)" to="/torrents" class="shrink-0 text-right text-sm font-bold text-accent hover:underline">
          Downloading →<span v-if="started.get(r.url)" class="block text-xs font-normal text-muted">{{ started.get(r.url) }}</span>
        </RouterLink>
        <button v-else class="shrink-0 rounded-full bg-white px-4 py-1.5 text-sm font-bold text-black hover:scale-105" @click="download(r)">Download</button>
      </div>
    </div>
  </section>
</template>
