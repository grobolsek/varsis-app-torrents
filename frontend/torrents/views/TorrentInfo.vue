<script setup>
// Full info for a torrent: a search result (/torrent-info/:id, handed over in localStorage)
// or one of your downloads (/torrents/:jobId, loaded from the server and refreshed while it runs).
import { Check, ExternalLink, LoaderCircle, Minus } from 'lucide-vue-next'
import { computed, onMounted, onUnmounted, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { api, fmtSize } from '@/api'
import { infoRows, jobRows, liveRows, loadInfo } from '../torrentInfo'

const route = useRoute()
const jobId = route.params.jobId
const result = jobId ? null : loadInfo(route.params.id)
const job = ref(null)
const contents = ref(null) // { files: [{ path, size, download, progress? }], info_hash }
const loading = ref(false)
const error = ref('')

const title = computed(() => job.value?.title ?? result?.title)
const missing = computed(() => (jobId ? error.value && !job.value : !result))
const rows = computed(() => (job.value ? jobRows(job.value) : result ? infoRows(result) : []))
const live = computed(() => (job.value?.live ? liveRows(job.value.live) : []))
const magnetOnly = computed(() => (job.value ? job.value.details?.magnet_only : result?.magnet_only))
const wanted = computed(() => contents.value?.files.filter((f) => f.download) ?? [])
const wantedSize = computed(() => wanted.value.reduce((sum, f) => sum + f.size, 0))
const extraHash = computed(() => contents.value?.info_hash && !rows.value.some((r) => r.label === 'Info hash'))

async function loadJob() {
  try {
    job.value = await api(`/torrents/${jobId}`)
    const files = job.value.live?.files ?? job.value.details?.contents
    contents.value = files ? { files, info_hash: job.value.details?.info_hash } : null
  } catch (e) {
    error.value = e.message
  }
}

let timer
onMounted(async () => {
  if (jobId) {
    loading.value = true
    await loadJob()
    loading.value = false
    timer = setInterval(() => ['downloading', 'importing'].includes(job.value?.status) && loadJob(), 3000)
  } else if (result && !result.magnet_only) {
    // The file list is in the .torrent itself, so fetch it (through the server).
    loading.value = true
    try {
      contents.value = await api('/torrents/files', { method: 'POST', body: { url: result.url } })
    } catch (e) {
      error.value = e.message
    } finally {
      loading.value = false
    }
  }
})
onUnmounted(() => clearInterval(timer))
watch(title, (t) => t && (document.title = `${t} · Varsis`), { immediate: true })
</script>

<template>
  <div class="px-4 pt-16 pb-10 md:px-6">
    <p v-if="missing" class="mt-10 text-center text-muted">
      {{ jobId ? error : "This torrent's info isn't available here any more. Open it again from the search results." }}
    </p>
    <p v-else-if="jobId && !job" class="mt-10 flex justify-center"><LoaderCircle class="h-6 w-6 animate-spin text-muted" /></p>
    <template v-else>
      <p class="mb-2 text-sm font-bold text-muted">{{ jobId ? 'Download' : 'Torrent' }}</p>
      <h1 class="mb-6 text-2xl font-bold break-words md:text-3xl">{{ title }}</h1>
      <div v-if="job?.status === 'downloading'" class="mb-6 h-1.5 rounded-full bg-white/10">
        <div class="h-1.5 rounded-full bg-accent transition-all" :style="{ width: `${Math.round(job.progress * 100)}%` }" />
      </div>

      <div class="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <div class="space-y-6">
          <section v-if="live.length" class="rounded-lg bg-white/5 p-5">
            <h2 class="mb-3 font-bold">Live from qBittorrent</h2>
            <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
              <template v-for="row in live" :key="row.label">
                <dt class="text-muted">{{ row.label }}</dt>
                <dd class="break-words">{{ row.value }}</dd>
              </template>
            </dl>
          </section>

          <section class="rounded-lg bg-white/5 p-5">
            <h2 class="mb-3 font-bold">Details</h2>
            <img v-if="(job?.details ?? result)?.details?.posterUrl" :src="(job?.details ?? result).details.posterUrl" alt="" class="mb-4 max-h-60 rounded-md" />
            <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-6 gap-y-2 text-sm">
              <template v-for="row in rows" :key="row.label">
                <dt class="text-muted">{{ row.label }}</dt>
                <dd class="break-words">
                  <a v-if="row.href" :href="row.href" target="_blank" rel="noopener noreferrer" class="inline-flex items-center gap-1 text-accent hover:underline">
                    <span class="break-all">{{ row.value }}</span> <ExternalLink class="h-3.5 w-3.5 shrink-0" />
                  </a>
                  <span v-else :class="{ 'font-mono text-xs break-all': row.label === 'Info hash' }">{{ row.value }}</span>
                </dd>
              </template>
              <template v-if="extraHash">
                <dt class="text-muted">Info hash</dt>
                <dd class="font-mono text-xs break-all">{{ contents.info_hash }}</dd>
              </template>
            </dl>
            <p v-if="job && !job.details" class="mt-4 text-sm text-muted">This download started before torrent info was saved, so only its status is known.</p>
          </section>
        </div>

        <section class="rounded-lg bg-white/5 p-5">
          <h2 class="mb-1 font-bold">Files</h2>
          <p v-if="loading" class="flex items-center gap-2 text-sm text-muted"><LoaderCircle class="h-4 w-4 animate-spin" /> Reading the .torrent file…</p>
          <p v-else-if="error && !jobId" class="text-sm text-red-400">{{ error }}</p>
          <template v-else-if="contents">
            <p class="mb-3 text-sm text-muted">
              {{ contents.files.length }} files. {{ jobId ? 'Downloading' : 'Download would fetch' }} {{ wanted.length }} of them ({{ fmtSize(wantedSize) }}): the best supported version of each song.
            </p>
            <ul class="divide-y divide-white/5 text-sm">
              <li v-for="f in contents.files" :key="f.path" class="flex items-start gap-3 py-2" :class="{ 'text-muted': !f.download }">
                <Check v-if="f.download" class="mt-0.5 h-4 w-4 shrink-0 text-accent" />
                <Minus v-else class="mt-0.5 h-4 w-4 shrink-0" />
                <span class="min-w-0 flex-1 break-all">{{ f.path }}</span>
                <span v-if="f.download && f.progress !== undefined" class="shrink-0 tabular-nums">{{ Math.floor(f.progress * 100) }}%</span>
                <span class="w-16 shrink-0 text-right tabular-nums">{{ fmtSize(f.size) }}</span>
              </li>
            </ul>
          </template>
          <p v-else-if="magnetOnly" class="text-sm text-muted">This is a magnet link, so the file list is only known once qBittorrent starts downloading it.</p>
          <p v-else class="text-sm text-muted">No file list saved for this download.</p>
        </section>
      </div>
    </template>
  </div>
</template>
