<script setup lang="ts">
import PublishedMemoryPanel from './PublishedMemoryPanel.vue'
import EvolutionReviewPanel from './EvolutionReviewPanel.vue'
import DecisionAuditPanel from './DecisionAuditPanel.vue'
import { useDashboardStore } from '../stores/dashboard'
import { Sparkles } from 'lucide-vue-next'
const store = useDashboardStore()
</script>
<template>
  <div class="evolution-page min-w-0" data-evolution-page>
    <header class="evolution-toolbar">
      <span class="evolution-toolbar__scope"><Sparkles class="size-4 shrink-0" aria-hidden="true" />复盘结果与运行记忆</span>
      <span class="evolution-toolbar__model">复盘模型 <strong>{{ store.llmRuntime.model }}</strong></span>
    </header>
    <div class="evolution-layout">
      <EvolutionReviewPanel :review="store.data?.evolution_review" />
      <PublishedMemoryPanel :publication="store.data?.memory_publication" />
    </div>
    <section class="evolution-audit"><DecisionAuditPanel :audit="store.data?.wait_audit" :cycle="store.data?.decision_cycle" /></section>
  </div>
</template>
<style scoped>
.evolution-page { container-type: inline-size; }
.evolution-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .5rem 1rem; margin-bottom: 1rem; color: var(--text-muted); font-size: .8125rem; }
.evolution-toolbar__scope { display: flex; align-items: center; gap: .5rem; }
.evolution-toolbar__model { display: flex; flex-wrap: wrap; gap: .375rem; min-width: 0; overflow-wrap: anywhere; font-size: .75rem; }
.evolution-toolbar__model strong { color: var(--text-main); font-weight: 500; }
.evolution-audit { margin-top: 1rem; }
.evolution-layout { display: grid; grid-template-columns: minmax(0,1fr); gap: 1rem; align-items: start; }
@container (min-width: 1040px) { .evolution-layout { grid-template-columns: minmax(0,1.6fr) minmax(0,1fr); } }
</style>
