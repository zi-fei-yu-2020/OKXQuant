<script setup lang="ts">
import AppCard from './ui/AppCard.vue'

import { computed, ref, watch, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Brain, ChevronDown, Users } from 'lucide-vue-next'
import { getSessionToken, buildAuthHeaders, handleSessionResponse } from '../utils/sessionResponse'

const store = useDashboardStore()
const visibleCount = ref(24)
const expanded = ref<Set<string>>(new Set())
const loadingDetails = ref<Set<string>>(new Set())
const historyKey = (item: any) => item?.history_id || JSON.stringify(item)



const serverHistory = ref<any[] | null>(null)
const serverTotal = ref<number | null>(null)
const loadingHistory = ref(false)
let fetchEpoch = 0
let activeController: AbortController | null = null

const allHistory = computed(() => {
  if (serverHistory.value !== null) {
    return serverHistory.value
  }
  return store.data?.ai_brain_history || []
})

const history = computed(() => allHistory.value.slice(0, visibleCount.value))

async function toggle(item: any) {
  const key = historyKey(item)
  const next = new Set(expanded.value)
  if (next.has(key)) {
    next.delete(key)
    expanded.value = next
    return
  }
  next.add(key)
  expanded.value = next
  if (!item?.history_id || item?.details_loaded || loadingDetails.value.has(key)) return
  loadingDetails.value = new Set(loadingDetails.value).add(key)
  try {
    const session = getSessionToken()
    const resp = await fetch(`/api/ai/history/${encodeURIComponent(item.history_id)}`, {
      cache: 'no-store',
      headers: buildAuthHeaders(session),
    })
    if (resp.status === 401 || resp.status === 403) {
      handleSessionResponse(resp.status, session)
      return
    }
    if (resp.ok) Object.assign(item, await resp.json(), { details_loaded: true })
  } finally {
    const pending = new Set(loadingDetails.value)
    pending.delete(key)
    loadingDetails.value = pending
  }
}

async function fetchServerHistory(offset = 0) {
  if (activeController) {
    activeController.abort()
  }
  const controller = new AbortController()
  activeController = controller
  const epoch = ++fetchEpoch

  loadingHistory.value = true
  try {
    const session = getSessionToken()
    const resp = await fetch(`/api/ai/history?limit=25&offset=${offset}`, {
      signal: controller.signal,
      headers: buildAuthHeaders(session),
    })
    if (resp.status === 401 || resp.status === 403) {
      handleSessionResponse(resp.status, session)
      return
    }
    if (!resp.ok) return
    const json = await resp.json()
    if (epoch !== fetchEpoch) return

    if (Array.isArray(json?.items)) {
      const currentList = offset === 0 ? [] : (serverHistory.value || [...allHistory.value])
      const merged = [...currentList]
      const knownIds = new Set(merged.map((x: any) => historyKey(x)))
      for (const it of json.items) {
        if (!knownIds.has(historyKey(it))) {
          merged.push(it)
          knownIds.add(historyKey(it))
        }
      }
      serverHistory.value = merged
      if (typeof json?.total === 'number') serverTotal.value = json.total
    }
  } catch {
    // Aborted or offline
  } finally {
    if (epoch === fetchEpoch) {
      loadingHistory.value = false
      if (activeController === controller) activeController = null
    }
  }
}

async function handleLoadMore() {
  visibleCount.value += 24
  const currentLen = allHistory.value.length
  if (serverTotal.value !== null && currentLen >= serverTotal.value) return
  if (currentLen > 0) {
    await fetchServerHistory(currentLen)
  }
}

// Watch account scope changes
watch(
  () => store.data?.account_source_id,
  (newScope, oldScope) => {
    if (newScope !== oldScope) {
      if (activeController) {
        activeController.abort()
        activeController = null
      }
      serverHistory.value = null
      visibleCount.value = 24
      if (typeof window !== 'undefined') {
        void fetchServerHistory(0)
      }
    }
  },
)

onMounted(() => {
  if (typeof window !== 'undefined') {
    void fetchServerHistory(0)
  }
})

onUnmounted(() => {
  if (activeController) {
    activeController.abort()
    activeController = null
  }
})</script>

<template>
  <AppCard
    class="rounded-xl border p-4 sm:p-5 transition-all shadow-xs space-y-4"
    style="background-color: var(--bg-card); border-color: var(--border-subtle)"
  >
    <!-- Header -->
    <div
      class="flex items-center space-x-3 pb-3 border-b"
      style="border-color: var(--border-subtle)"
    >
      <div
        class="w-9 h-9 rounded-lg flex items-center justify-center border shrink-0"
        style="
          background-color: var(--bg-card-subtle);
          border-color: var(--border-medium);
          color: var(--text-main);
        "
      >
        <Brain class="w-4 h-4" />
      </div>
      <div>
        <h2
          class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide"
          style="color: var(--text-main)"
        >
          AI 宏观多周期推演基调与决策审计
        </h2>
        <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted)">
          按记录时间回溯宏观研判、多模型辩论与持仓管理建议；不代表当前执行状态
        </p>
      </div>
    </div>

    <!-- Empty State -->
    <div
      v-if="history.length === 0"
      class="py-16 text-center text-xs font-mono rounded-xl border border-dashed"
      style="
        background-color: var(--bg-card-subtle);
        border-color: var(--border-subtle);
        color: var(--text-muted);
      "
    >
      暂无历史决策记录；待决策任务写入后显示
    </div>

    <!-- History List -->
    <div v-else class="space-y-2.5 max-h-[720px] overflow-y-auto pr-1">
      <div
        v-for="item in history"
        :key="historyKey(item)"
        class="rounded-xl border p-3.5 transition-all"
        style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
      >
        <button
          @click="toggle(item)"
          :aria-expanded="expanded.has(historyKey(item))"
          class="w-full flex items-center justify-between text-left cursor-pointer gap-2"
        >
          <div class="flex items-center space-x-2.5 min-w-0">
            <span
              class="font-mono font-bold text-xs shrink-0 num-tabular"
              style="color: var(--text-main)"
            >
              {{ item.time }}
            </span>
            <span
              v-if="item.status === 'failed'"
              class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border shrink-0"
              style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border); color: var(--color-warn)"
            >
              推理失败
            </span>
            <span
              v-if="item.council_transcript"
              class="px-2 py-0.5 rounded text-[10px] font-mono font-bold border shrink-0"
              style="
                background-color: var(--bg-badge);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              🏛️ 委员会决策
            </span>
            <span class="text-xs font-sans truncate" style="color: var(--text-muted)">
              {{ item.failure_reason || item.macro_assessment || '该记录未提供宏观研判' }}
            </span>
          </div>
          <ChevronDown
            class="w-4 h-4 shrink-0 transition-transform"
            style="color: var(--text-faint)"
            :class="expanded.has(historyKey(item)) ? 'rotate-180' : ''"
          />
        </button>

        <div
          v-if="expanded.has(historyKey(item))"
          class="mt-3 space-y-3 border-t pt-3"
          style="border-color: var(--border-subtle)"
        >
          <AppCard
            v-if="item.status === 'failed'"
            class="p-3 rounded-xl border space-y-1.5"
            style="background-color: var(--color-warn-bg); border-color: var(--color-warn-border)"
          >
            <div class="text-[10px] font-bold font-mono uppercase" style="color: var(--color-warn)">
              本轮未生成有效决策
            </div>
            <p class="text-xs font-sans leading-relaxed" style="color: var(--text-main)">
              <strong>失败原因:</strong>{{ item.failure_reason || item.model_failure?.message || item.macro_assessment }}
            </p>
          </AppCard>

          <AppCard v-if="item.market_context_receipt" class="p-3 space-y-1" data-market-context-receipt>
            <p class="text-xs font-semibold" style="color:var(--text-main)">市场情报输入与引用</p>
            <p class="text-xs" style="color:var(--text-muted)">
              已提供 {{ item.market_context_receipt.provided_article_count ?? '—' }} 条新闻 ·
              明确引用 {{ item.market_context_receipt.cited_article_count ?? '—' }} 条 ·
              情绪引用 {{ item.market_context_receipt.sentiment_cited_by?.length ?? '—' }} 个标的
            </p>
            <template v-if="item.market_context_receipt.macro_source_statuses">
              <p class="text-xs" style="color:var(--text-muted)">跨资产与日历：提供 {{ item.market_context_receipt.provided_macro_fact_count ?? '—' }} 条可核验字段 · 引用 {{ item.market_context_receipt.cited_macro_fact_count ?? '—' }} 条。</p>
              <p class="text-xs" style="color:var(--text-muted)">财政部 {{ item.market_context_receipt.macro_source_statuses.treasury?.usable ? '日频参考可用' : '不可用' }} · 官方日历 {{ item.market_context_receipt.macro_source_statuses.bea?.usable || item.market_context_receipt.macro_source_statuses.bls?.usable ? '至少部分来源可用' : '无有效来源' }}。仅描述该轮官方数据，不代表当前状态或完整覆盖。</p>
              </template>
              <p v-else class="text-xs" style="color:var(--text-muted)">该历史记录未提供独立跨资产数据接入回执，不能把新闻标题当实时行情。</p>
            <p class="text-xs" style="color:var(--text-faint)">只统计通过字段核验的引用；未明确引用不等于没有阅读，也不代表内部权重或真实胜率。</p>
          </AppCard>

          <!-- Macro Summary -->
          <div v-if="item.status !== 'failed'">
            <div
              class="text-[10px] font-bold font-mono uppercase mb-1"
              style="color: var(--text-faint)"
            >
              宏观研判总结:
            </div>
            <p class="text-xs font-sans leading-relaxed" style="color: var(--text-main)">
              {{ item.macro_assessment || '该记录未提供宏观研判' }}
            </p>
          </div>

          <!-- Multi-Agent Council Transcript -->
          <AppCard
            v-if="item.council_transcript"
            class="p-3.5 rounded-xl border space-y-2.5 font-mono"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="flex items-center justify-between border-b pb-2"
              style="border-color: var(--border-subtle)"
            >
              <div
                class="flex items-center space-x-2 text-xs font-bold"
                style="color: var(--text-main)"
              >
                <Users class="w-4 h-4" />
                <span>【多角色模型现场辩论纪要】</span>
              </div>
              <span class="text-[10px] font-mono" style="color: var(--text-faint)">
                协作总时延: {{ item.council_transcript.total_duration_ms }}ms
              </span>
            </div>

            <!-- Advisors viewpoints -->
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 pt-1">
              <div
                v-for="(adv, advKey) in item.council_transcript.advisors || {}"
                :key="advKey"
                class="p-2.5 rounded-lg border space-y-1 text-xs"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
              >
                <div class="flex items-center justify-between font-bold">
                  <span style="color: var(--text-main)">{{ adv.role_name }}</span>
                  <span class="text-[10px]" style="color: var(--text-faint)">{{
                    adv.model_used
                  }}</span>
                </div>
                <p
                  class="text-[11px] leading-relaxed whitespace-pre-wrap max-h-36 overflow-y-auto pr-0.5 select-text"
                  style="color: var(--text-muted)"
                >
                  {{ adv.content }}
                </p>
              </div>
            </div>

            <!-- Arbitrator summary -->
            <div
              class="mt-1 pt-2 border-t text-xs font-bold flex items-center justify-between"
              style="border-color: var(--border-subtle); color: var(--color-up)"
            >
              <span>⚖️ 首席仲裁官裁决收口: 采纳专家参谋核心论点，生成统一发单指令</span>
              <span class="text-[10px] font-normal" style="color: var(--text-faint)">
                终审模型: {{ item.council_transcript.arbitrator?.model_used }}
              </span>
            </div>
          </AppCard>

          <!-- In-flight Position Management Instructions -->
          <AppCard
            v-if="item.position_management?.length"
            class="p-3 rounded-xl border space-y-1.5 font-mono text-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <span class="text-[10px] font-bold block uppercase" style="color: var(--text-faint)"
              >在途持仓管理指令</span
            >
            <div
              v-for="(p, j) in item.position_management"
              :key="j"
              class="flex flex-wrap items-center gap-x-2 gap-y-0.5"
              style="color: var(--text-muted)"
            >
              <strong style="color: var(--text-main)">{{ p.instId }}</strong>
              <span
                class="px-2 py-0.5 rounded font-bold border text-[10px]"
                :style="{
                  backgroundColor: p.action?.includes('HOLD')
                    ? 'var(--bg-badge)'
                    : 'var(--color-warn-bg)',
                  borderColor: p.action?.includes('HOLD')
                    ? 'var(--border-subtle)'
                    : 'var(--color-warn-border)',
                  color: p.action?.includes('HOLD') ? 'var(--text-main)' : 'var(--color-warn)',
                }"
              >
                {{ p.action }}
              </span>
              <span v-if="p.reason" class="text-[11px]" style="color: var(--text-muted)">{{
                p.reason
              }}</span>
            </div>
          </AppCard>
        </div>
      </div>
    </div>
    <button v-if="history.length < allHistory.length" class="ui-button ui-button--secondary ui-button--sm" :disabled="loadingHistory" @click="handleLoadMore()">加载更多历史记录（剩余 {{ allHistory.length - history.length }} 条）</button>
  </AppCard>
</template>
