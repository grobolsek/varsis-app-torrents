<script setup>
// "i" icon: hover/focus shows a popup with labelled rows, click opens the full info page in a new tab.
import { Info } from 'lucide-vue-next'

defineProps({
  title: { type: String, required: true },
  rows: { type: Array, required: true }, // [{ label, value, href? }]
  href: { type: String, default: null }, // page to open in a new tab
})
const emit = defineEmits(['open'])
</script>

<template>
  <div class="group relative shrink-0">
    <a
      v-if="href"
      :href="href"
      target="_blank"
      class="block rounded-full p-1.5 text-muted hover:bg-white/10 hover:text-white focus-visible:text-white"
      aria-label="Torrent info (opens in a new tab)"
    >
      <Info class="h-5 w-5" />
    </a>
    <button v-else class="rounded-full p-1.5 text-muted hover:bg-white/10 hover:text-white focus-visible:text-white" aria-label="Torrent info (opens in a new tab)" @click="emit('open')">
      <Info class="h-5 w-5" />
    </button>
    <div
      class="pointer-events-none invisible absolute top-full right-0 z-40 mt-1 w-[min(24rem,calc(100vw-2rem))] rounded-md bg-highlight p-4 text-xs opacity-0 shadow-2xl shadow-black/60 transition group-focus-within:visible group-focus-within:opacity-100 group-hover:visible group-hover:opacity-100"
    >
      <p class="mb-3 text-sm font-bold break-words">{{ title }}</p>
      <dl class="grid grid-cols-[auto_minmax(0,1fr)] gap-x-4 gap-y-1.5">
        <template v-for="row in rows" :key="row.label">
          <dt class="text-muted">{{ row.label }}</dt>
          <dd class="break-all" :class="{ 'text-accent': row.href }">{{ row.value }}</dd>
        </template>
      </dl>
      <p class="mt-3 text-muted">Click for the file list and links.</p>
    </div>
  </div>
</template>
