<script setup>
import { Check, CircleX, LoaderCircle } from 'lucide-vue-next'
import { onMounted, onUnmounted, ref } from 'vue'
import { api } from '@/api'
import InfoButton from '../components/InfoButton.vue'
import { jobRows } from '../torrentInfo'

const jobs = ref(null)
const load = async () => (jobs.value = await api('/torrents'))
const active = () => jobs.value?.some((j) => ['downloading', 'importing'].includes(j.status))
let timer
onMounted(() => {
  load()
  timer = setInterval(() => active() && load(), 5000)
})
onUnmounted(() => clearInterval(timer))
</script>

<template>
  <div class="px-4 pt-16 pb-10 md:px-6">
    <h1 class="mb-1 text-3xl font-bold">Torrent downloads</h1>
    <p class="mb-6 text-muted">Finished downloads are imported into your library automatically.</p>
    <p v-if="jobs && !jobs.length" class="mt-10 text-center text-muted">
      No downloads yet. <RouterLink to="/search" class="font-bold text-white hover:underline">Search</RouterLink> for music and use “Search torrents”.
    </p>
    <div class="space-y-2">
      <div v-for="j in jobs" :key="j.id" class="flex items-center gap-4 rounded-lg bg-white/5 p-4">
        <LoaderCircle v-if="j.status === 'downloading' || j.status === 'importing'" class="h-6 w-6 shrink-0 animate-spin text-muted" />
        <Check v-else-if="j.status === 'done'" class="h-6 w-6 shrink-0 text-accent" />
        <CircleX v-else class="h-6 w-6 shrink-0 text-red-400" />
        <div class="min-w-0 flex-1">
          <div class="flex items-baseline justify-between gap-4">
            <span class="truncate font-medium">{{ j.title }}</span>
            <span class="shrink-0 text-sm text-muted">
              {{ j.status === 'downloading' ? `${Math.round(j.progress * 100)}%` : j.status }}
            </span>
          </div>
          <div v-if="j.status === 'downloading'" class="mt-2 h-1 rounded-full bg-white/10">
            <div class="h-1 rounded-full bg-accent transition-all" :style="{ width: `${Math.round(j.progress * 100)}%` }" />
          </div>
          <p v-if="j.message" class="mt-1 text-sm text-muted">{{ j.message }}</p>
        </div>
        <InfoButton :title="j.title" :rows="jobRows(j)" :href="`/torrents/${j.id}`" />
      </div>
    </div>
  </div>
</template>
