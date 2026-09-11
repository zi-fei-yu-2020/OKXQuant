<script setup lang="ts">
import { readableLog } from '../../utils/logText'
import { useToast } from '../../composables/useFeedback'
const toast = useToast()
import AppCard from '../../components/ui/AppCard.vue'
import DecisionAuditPanel from '../../components/DecisionAuditPanel.vue'
import CapitalPoolPanel from '../../components/CapitalPoolPanel.vue'
import ScenarioShadowPanel from '../../components/ScenarioShadowPanel.vue'
import type { ScenarioShadowStatus } from '../../utils/scenarioShadow'
import type { CapitalPoolStatus } from '../../utils/capitalPool'
import type { WaitAuditState, DecisionCycle } from '../../utils/waitAudit'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Terminal, RefreshCw } from 'lucide-vue-next'

const { api } = useApi()
const executionCycles = ref<Array<{ timestamp: string; actions: string[] }>>([])
const capital = ref<CapitalPoolStatus>()
const shadow = ref<ScenarioShadowStatus>()
const audit = ref<WaitAuditState>()
const cycle = ref<DecisionCycle>()
const loading = ref(true)
const logs = ref<string[]>([])
const activeLogTab = ref<'trader' | 'backend' | 'scheduler'>('trader')
const logContent = ref<string>('')
const logLoading = ref(false)

async function loadDecisions() {
  loading.value = true
  try {
    const res = await api('/api/v1/admin/runtime')
    logs.value = res.recent_logs || []
    executionCycles.value = res.recent_execution_cycles || []
    capital.value = res.capital_pool
    shadow.value = res.scenario_shadow
    audit.value = res.wait_audit
    cycle.value = res.decision_cycle
    await fetchLogStream('trader')
  } catch (e: any) {
    if (e?.silent) return
    toast.error(e.message)
  } finally {
    loading.value = false
  }
}

async function fetchLogStream(type: 'trader' | 'backend' | 'scheduler') {
  activeLogTab.value = type
  logLoading.value = true
  try {
    const res = await api(`/api/v1/admin/logs?source=${type}&lines=100`)
    logContent.value = readableLog(res.content || res.lines?.join('\n') || '无实时日志')
  } catch (e: any) {
    if (e?.silent) return
    logContent.value = `获取日志失败: ${e.message}`
  } finally {
    logLoading.value = false
  }
}

onMounted(() => {
  loadDecisions()
})
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
    <div class="flex items-center justify-between">
      <p class="text-sm font-sans" style="color: var(--text-muted)">
        核对 AI 宏观基调与逐币动作，并审查交易、后台与任务调度三路实时日志流。
      </p>
      <span
        class="text-xs font-sans px-2 py-1 rounded border font-bold"
        style="
          background-color: var(--color-brand-bg);
          color: var(--color-brand);
          border-color: var(--color-brand-border);
        "
      >
        日常运行 · 决策与审计
      </span>
    </div>

    <CapitalPoolPanel :pool="capital" />
    <ScenarioShadowPanel :shadow="shadow" />
    <DecisionAuditPanel :audit="audit" :cycle="cycle" />
    <AppCard v-if="executionCycles.length" class="min-w-0 rounded-xl border p-4 space-y-2" style="border-color: var(--border-subtle); background: var(--bg-card)">
      <h3 class="text-sm font-semibold" style="color: var(--text-main)">近期完整执行记录</h3>
      <details v-for="entry in executionCycles" :key="entry.timestamp" class="min-w-0 border-t pt-2 text-xs" style="border-color: var(--border-subtle)">
        <summary class="cursor-pointer" style="color: var(--text-muted)">{{ entry.timestamp }} · {{ entry.actions.length }} 项</summary>
        <ul class="mt-2 space-y-1 leading-relaxed" style="color: var(--text-main); overflow-wrap: anywhere">
          <li v-for="(action, index) in entry.actions" :key="index">{{ readableLog(action) }}</li>
          <li v-if="!entry.actions.length">无开平仓操作</li>
        </ul>
      </details>
    </AppCard>
    <!-- 3-Way Log Streams -->
    <AppCard
      class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 mb-3 border-b"
        style="border-color: var(--border-subtle)"
      >
        <div class="flex items-center space-x-2">
          <Terminal class="w-4 h-4 text-purple-400" />
          <h2
            class="text-sm font-black font-sans uppercase tracking-wide"
            style="color: var(--text-main)"
          >
            系统实时日志流
          </h2>
        </div>
        <!-- Log Selector Tabs -->
        <div
          class="flex flex-wrap gap-1 p-1 rounded-lg border"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
        >
          <button
            @click="fetchLogStream('trader')"
            class="px-2.5 py-1 rounded text-sm font-sans font-bold cursor-pointer transition-colors"
            :style="
              activeLogTab === 'trader'
                ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
                : { color: 'var(--text-muted)' }
            "
          >
            交易巡检 (Trader)
          </button>
          <button
            @click="fetchLogStream('backend')"
            class="px-2.5 py-1 rounded text-sm font-sans font-bold cursor-pointer transition-colors"
            :style="
              activeLogTab === 'backend'
                ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
                : { color: 'var(--text-muted)' }
            "
          >
            控制面服务 (Backend)
          </button>
          <button
            @click="fetchLogStream('scheduler')"
            class="px-2.5 py-1 rounded text-sm font-sans font-bold cursor-pointer transition-colors"
            :style="
              activeLogTab === 'scheduler'
                ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
                : { color: 'var(--text-muted)' }
            "
          >
            任务调度器 (Scheduler)
          </button>
        </div>
      </div>

      <div class="relative">
        <div
          v-if="logLoading"
          class="absolute inset-0 bg-black/40 backdrop-blur-xs flex items-center justify-center text-sm font-sans"
          style="color: var(--color-brand)"
        >
          <RefreshCw class="w-4 h-4 animate-spin mr-1.5" />
          <span>正在拉取最新日志流...</span>
        </div>
        <pre
          class="border rounded-lg p-3 text-sm font-sans max-h-[520px] overflow-y-auto whitespace-pre-wrap leading-relaxed select-text"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
          >{{ logContent }}</pre
        >
      </div>
    </AppCard>
  </div>
</template>
