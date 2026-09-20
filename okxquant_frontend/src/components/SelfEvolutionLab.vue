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
      <span class="evolution-toolbar__model">当前配置模型 <strong>{{ store.data?.llm_runtime?.model || '尚未取得' }}</strong></span>
    </header>
    <div class="evolution-layout">
      <EvolutionReviewPanel :review="store.data?.evolution_review" />
      <PublishedMemoryPanel :publication="store.data?.memory_publication" />
    </div>
    <details class="evolution-audit action-disclosure" data-lab-audit>
      <summary><span>决策与等待审计</span><span class="evolution-audit__hint">按需查看 · 不属于已发布规则</span></summary>
      <div class="evolution-audit__body"><DecisionAuditPanel :audit="store.data?.wait_audit" :cycle="store.data?.decision_cycle" /></div>
    </details>
  </div>
</template>
<style scoped>
.evolution-page { container-type: inline-size; }
.evolution-toolbar { display: flex; flex-wrap: wrap; align-items: center; justify-content: space-between; gap: .5rem 1rem; margin-bottom: 1rem; color: var(--text-muted); font-size: .8125rem; }
.evolution-toolbar__scope { display: flex; align-items: center; gap: .5rem; }
.evolution-toolbar__model { display: flex; flex-wrap: wrap; gap: .375rem; min-width: 0; overflow-wrap: anywhere; font-size: .75rem; }
.evolution-toolbar__model strong { color: var(--text-main); font-weight: 500; }
.evolution-audit { margin-top: 1rem; min-width: 0; border: 1px solid var(--border-subtle); border-radius: .75rem; background: var(--bg-card); }
.evolution-audit > summary { display: flex; flex-wrap: wrap; align-items: center; gap: .5rem 1rem; padding: 1rem; min-height: 48px; font-size: .8125rem; font-weight: 600; }
.evolution-audit__hint { color: var(--text-muted); font-size: .75rem; font-weight: 400; }
.evolution-audit__body { padding: 0 1rem 1rem; min-width: 0; }
.evolution-layout { display: grid; grid-template-columns: minmax(0,1fr); gap: 1rem; align-items: start; }
@container (min-width: 1040px) { .evolution-layout { grid-template-columns: minmax(0,1.6fr) minmax(0,1fr); } }
</style>
