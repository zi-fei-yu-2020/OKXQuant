<script setup lang="ts">
import type { Component } from 'vue'
defineProps<{ sections: Array<{ id: string; title: string; icon: Component }>; active: string }>()
defineEmits<{ select: [id: string] }>()
</script>
<template>
  <nav class="docs-contents" aria-label="文档章节目录">
    <button v-for="section in sections" :key="section.id" type="button" :data-doc-section="section.id"
      :aria-current="active === section.id ? 'location' : undefined" :class="{ 'is-active': active === section.id }"
      @click="$emit('select', section.id)">
      <component :is="section.icon" class="size-4 shrink-0" aria-hidden="true" />
      <span>{{ section.title }}</span>
    </button>
  </nav>
</template>
<style scoped>
.docs-contents { display: grid; gap: .25rem; min-width: 0; }
.docs-contents button { display: flex; align-items: flex-start; gap: .625rem; min-width: 0; min-height: 44px; width: 100%; padding: .75rem; text-align: left; border: 1px solid transparent; border-radius: .5rem; color: var(--text-muted); background: transparent; font-size: .8125rem; line-height: 1.6; cursor: pointer; }
.docs-contents button svg { margin-top: .125rem; }
.docs-contents button span { min-width: 0; overflow-wrap: anywhere; }
.docs-contents button:hover { color: var(--text-main); background: var(--bg-card-hover); }
.docs-contents button.is-active { color: var(--color-brand); background: var(--color-brand-bg); border-color: var(--color-brand-border); font-weight: 600; }
.docs-contents button:focus-visible { outline: 2px solid var(--color-brand); outline-offset: -2px; }
</style>
