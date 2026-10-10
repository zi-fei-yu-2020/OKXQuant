<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import AppDialog from './ui/AppDialog.vue'
import AppButton from './ui/AppButton.vue'
import AppTable from './ui/AppTable.vue'
import { isSettlementPending } from '../utils/tradeSettlement'
import { tradeDuration } from '../utils/tradeDuration'
import { observedNumber } from '../utils/observationDisplay'
import { getSessionToken, buildAuthHeaders, handleSessionResponse } from '../utils/sessionResponse'
import { feeAccounting, feeText, ledgerValue, ledgerNumberText, ledgerNumberColor } from '../utils/feeAccounting'

import { ref, computed, watch, nextTick, onMounted, onUnmounted } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Receipt, Search } from 'lucide-vue-next'

const store = useDashboardStore()
const filter = ref<'all' | 'active' | 'closed'>('all')
const keyword = ref('')
const feeTrade = ref<any>(null)
const feeDialogOpen = ref(false)

function showFees(trade: any) {
  feeTrade.value = trade
  feeDialogOpen.value = true
  if (trade?.id && !trade.fee_details_loaded) {
    void fetchTradeDetail(trade)
  }
}

// Server pagination and filter state
const serverTrades = ref<any[] | null>(null)
const serverTotal = ref<number | null>(null)
const serverCounts = ref<{ all?: number; active?: number; closed?: number; pending?: number } | null>(null)
const offset = ref(0) // Page currently displayed; commit only after a valid response.
const requestedOffset = ref(0)
const pageRevision = ref(0)
const ledgerFrame = ref<HTMLElement | null>(null)
const frameMinHeight = ref(0)
const pageSize = 30
const loadingTrades = ref(false)
const tradesError = ref<string | null>(null)

let activeController: AbortController | null = null
let fetchGeneration = 0
let disposed = false
let searchDebounceTimer: ReturnType<typeof setTimeout> | undefined
let quietTimer: ReturnType<typeof setInterval> | undefined



async function fetchTradeDetail(trade: any) {
  const scope = store.data?.account_source_id
  const statisticsId = store.data?.statistics_epoch?.id
  try {
    const session = getSessionToken()
    const resp = await fetch(`/api/trades/${encodeURIComponent(trade.id)}`, {
      headers: buildAuthHeaders(session),
    })
    if (disposed || session !== getSessionToken() || scope !== store.data?.account_source_id || statisticsId !== store.data?.statistics_epoch?.id) return
    if (resp.status === 401 || resp.status === 403) {
      handleSessionResponse(resp.status, session)
      return
    }
    if (resp.ok) {
      const data = await resp.json()
      if (disposed || scope !== store.data?.account_source_id) return
      Object.assign(trade, data, { fee_details_loaded: true })
      if (feeTrade.value && feeTrade.value.id === trade.id) {
        Object.assign(feeTrade.value, data)
      }
    }
  } catch {}
}

async function fetchPage(newOffset = 0, options: { silent?: boolean; redirect?: boolean; preserveHeight?: boolean } = {}) {
  const { silent = false, redirect = false, preserveHeight = true } = options
  if (disposed || silent && !redirect && (activeController || loadingTrades.value || tradesError.value || serverTrades.value === null)) return
  const scope = store.data?.account_source_id
  const statisticsId = store.data?.statistics_epoch?.id
  const session = getSessionToken()
  const targetOffset = Math.max(0, newOffset)
  if (!silent) {
    requestedOffset.value = targetOffset
    if (preserveHeight) frameMinHeight.value = Math.max(frameMinHeight.value, Math.ceil(ledgerFrame.value?.getBoundingClientRect().height ?? 0))
  }
  const gen = ++fetchGeneration

  if (activeController) {
    activeController.abort()
  }
  const controller = new AbortController()
  activeController = controller

  if (!silent) { loadingTrades.value = true; tradesError.value = null }
  let timedOut = false
  const timeout = setTimeout(() => { timedOut = true; controller.abort() }, 12000)

  try {
    const params = new URLSearchParams()
    params.set('limit', String(pageSize))
    params.set('offset', String(targetOffset))
    params.set('state', filter.value)
    if (keyword.value.trim()) {
      params.set('q', keyword.value.trim())
    }

    const resp = await fetch(`/api/trades?${params.toString()}`, {
      signal: controller.signal,
      headers: buildAuthHeaders(session),
      cache: 'no-store',
    })
    if (disposed || gen !== fetchGeneration || session !== getSessionToken() || scope !== store.data?.account_source_id || statisticsId !== store.data?.statistics_epoch?.id) return

    if (resp.status === 401 || resp.status === 403) {
      handleSessionResponse(resp.status, session)
      throw new Error('会话已失效，请重新登录')
    }
    if (!resp.ok) {
      throw new Error(`HTTP ${resp.status}`)
    }

    const json = await resp.json()
    if (disposed || gen !== fetchGeneration || session !== getSessionToken() || scope !== store.data?.account_source_id || statisticsId !== store.data?.statistics_epoch?.id) return
    if (!Array.isArray(json?.items) || !Number.isSafeInteger(json?.total) || json.total < json.items.length
      || json.items.some((item: unknown) => !item || typeof item !== 'object' || Array.isArray(item))) {
      throw new Error('交易记录返回格式无效，请重试')
    }
    if (json.account_source_id && json.account_source_id !== scope) return
    if ((json.statistics_epoch?.id || undefined) !== statisticsId) return
    const lastOffset = json.total > 0 ? Math.floor((json.total - 1) / pageSize) * pageSize : 0
    if (json.total > 0 && targetOffset > lastOffset) {
      await fetchPage(lastOffset, { silent, redirect: true, preserveHeight })
      return
    }
    serverTrades.value = json.items
    serverTotal.value = json.total
    offset.value = json.total === 0 ? 0 : targetOffset
    if (json?.counts && typeof json.counts === 'object') serverCounts.value = json.counts
    if (!silent) pageRevision.value++
    else requestedOffset.value = offset.value
    await nextTick()
    if (disposed || gen !== fetchGeneration || session !== getSessionToken() || scope !== store.data?.account_source_id || statisticsId !== store.data?.statistics_epoch?.id) return
    const scroller = ledgerFrame.value?.querySelector<HTMLElement>('.table-scroll-container')
    if (scroller && !silent) scroller.scrollTop = 0
  } catch (e: any) {
    if (disposed || gen !== fetchGeneration || session !== getSessionToken() || scope !== store.data?.account_source_id || statisticsId !== store.data?.statistics_epoch?.id || controller.signal.aborted && !timedOut) return
    if (!silent) tradesError.value = timedOut ? '请求超时，已保留上次记录，请重试' : e.message || '加载成交记录失败'
  } finally {
    clearTimeout(timeout)
    if (gen === fetchGeneration) {
      loadingTrades.value = false
      if (activeController === controller) activeController = null
    }
  }
}

watch(filter, () => {
  clearTimeout(searchDebounceTimer)
  void fetchPage(0)
})

watch(keyword, () => {
  clearTimeout(searchDebounceTimer)
  ++fetchGeneration
  activeController?.abort()
  requestedOffset.value = 0
  loadingTrades.value = true
  tradesError.value = null
  searchDebounceTimer = setTimeout(() => {
    void fetchPage(0)
  }, 250)
})

watch(
  () => JSON.stringify([store.data?.account_source_id, store.data?.statistics_epoch?.id]),
  (newScope, oldScope) => {
    if (newScope !== oldScope) {
      offset.value = 0
      clearTimeout(searchDebounceTimer)
      serverTrades.value = []
      serverTotal.value = null
      serverCounts.value = null
      frameMinHeight.value = 0
      feeDialogOpen.value = false
      feeTrade.value = null
      void fetchPage(0, { preserveHeight: false })
    }
  },
)

function refreshQuietly() {
  if (typeof document === 'undefined' || document.visibilityState === 'visible') void fetchPage(offset.value, { silent: true })
}
watch(() => store.data?.ledger_sync?.last_success, (current, prior) => { if (current !== prior) refreshQuietly() })

onMounted(() => {
  if (typeof window !== 'undefined') {
    void fetchPage(0)
    document.addEventListener('visibilitychange', refreshQuietly)
    quietTimer = setInterval(refreshQuietly, 30000)
  }
})

onUnmounted(() => {
  if (quietTimer) clearInterval(quietTimer)
  if (typeof document !== 'undefined') document.removeEventListener('visibilitychange', refreshQuietly)
  disposed = true
  ++fetchGeneration
  clearTimeout(searchDebounceTimer)
  if (activeController) {
    activeController.abort()
    activeController = null
  }
})

const trades = computed(() => {
  if (serverTrades.value !== null) {
    return serverTrades.value
  }
  const all: any[] = store.data?.trades || []
  return all.filter((t) => {
    if (filter.value === 'active' && t.status !== 'holding') return false
    if (filter.value === 'closed' && t.status === 'holding') return false
    if (keyword.value) {
      const q = keyword.value.toLowerCase()
      const matchInst = (t.inst || '').toLowerCase().includes(q)
      const matchStrat = (t.strategy || '').toLowerCase().includes(q)
      const matchReason = (t.exit_reason || '').toLowerCase().includes(q)
      if (!matchInst && !matchStrat && !matchReason) return false
    }
    return true
  })
})

function pageCount(key: 'active' | 'closed'): number | null {
  const value = serverCounts.value?.[key]
  return typeof value === 'number' && Number.isSafeInteger(value) && value >= 0 ? value : null
}
const holdingCount = computed(() => pageCount('active'))
const closedCount = computed(() => pageCount('closed'))


function marginText(value: unknown): string {
  const n = observedNumber(value)
  return n === null ? '--' : n.toFixed(2) + ' U'
}

function getPnl(t: any): number | null {
  return ledgerValue(t, ['net_pnl', 'pnl'])
}

function getRoi(t: any): number | null {
  return ledgerValue(t, ['roi_pct', 'roi'])
}

function formatPx(v: any): string {
  const n = observedNumber(v)
  if (n === null) return '--'
  return n >= 100 ? n.toFixed(2) : n >= 1 ? n.toFixed(4) : n.toFixed(6)
}

function clean(v: any, fallback = '--'): string {
  return v || fallback
}

function sourcePendingLabel(t: any): string {
  if (t?.source_status === 'automatic_unlinked' || t?.strategy_evidence === 'automatic_entry_unlinked') return '系统自动开仓（决策证据缺失）'
  if (t?.source_status === 'external_or_unlinked' || /来源未关联|未关联/.test(String(t?.strategy || ''))) return '持仓来源待确认'
  return ''
}

function strategyLabel(t: any): string {
  return sourcePendingLabel(t) || clean(t.strategy, '观望')
}

function horizonLabel(v: unknown, t?: any): string {
  if (sourcePendingLabel(t)) return '待确认'
  return v === 'scalp' ? '短线' : v === 'swing' ? '波段' : '未知'
}</script>

<template>
  <div class="trade-ledger space-y-3.5 min-w-0 max-w-full">
    <!-- Header -->
    <AppCard class="p-4 sm:p-5 flex flex-wrap items-center justify-between gap-3"
    >
      <div class="flex items-center space-x-3 min-w-0">
        <div
          class="w-9 h-9 rounded-lg flex items-center justify-center border shrink-0"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-medium);
            color: var(--text-main);
          "
        >
          <Receipt class="w-4 h-4" />
        </div>
        <div>
          <h2
            class="text-xs sm:text-sm font-black font-mono uppercase tracking-wide"
            style="color: var(--text-main)"
          >
            成交台账与生命周期履历
          </h2>
          <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted)">
            以交易所已结算净额为准；缺失金额保留未知，费用可单独核对
          </p>
        </div>
      </div>
      <div class="text-xs font-mono" style="color: var(--text-muted)">
        持仓 <strong style="color: var(--color-brand)">{{ holdingCount ?? '—' }}</strong> · 已平仓（含待结算） <strong style="color: var(--text-main)">{{ closedCount ?? '—' }}</strong>
      </div>
    </AppCard>

    <!-- Filter bar -->
    <AppCard class="p-2.5 sm:p-3 flex flex-wrap items-center justify-between gap-3"
    >
      <div
        class="flex rounded-lg border p-0.5 font-mono text-xs"
        style="background-color: var(--bg-badge); border-color: var(--border-subtle)"
      >
        <button
          @click="filter = 'all'"
          class="ui-tab-control"
          :style="
            filter === 'all'
              ? {
                  backgroundColor: 'var(--bg-card)',
                  color: 'var(--text-main)',
                  borderColor: 'var(--border-medium)',
                  boxShadow: 'var(--shadow-card)',
                }
              : { color: 'var(--text-muted)' }
          "
          :class="filter === 'all' ? 'border font-bold' : ''"
        >
          全部
        </button>
        <button
          @click="filter = 'active'"
          class="ui-tab-control"
          :style="
            filter === 'active'
              ? {
                  backgroundColor: 'var(--bg-card)',
                  color: 'var(--text-main)',
                  borderColor: 'var(--border-medium)',
                  boxShadow: 'var(--shadow-card)',
                }
              : { color: 'var(--text-muted)' }
          "
          :class="filter === 'active' ? 'border font-bold' : ''"
        >
          持仓中
        </button>
        <button
          @click="filter = 'closed'"
          class="ui-tab-control"
          :style="
            filter === 'closed'
              ? {
                  backgroundColor: 'var(--bg-card)',
                  color: 'var(--text-main)',
                  borderColor: 'var(--border-medium)',
                  boxShadow: 'var(--shadow-card)',
                }
              : { color: 'var(--text-muted)' }
          "
          :class="filter === 'closed' ? 'border font-bold' : ''"
        >
          已平仓
        </button>
      </div>

      <div
        class="flex items-center space-x-1.5 flex-1 min-w-0 sm:max-w-[320px] rounded-lg border px-3 py-1.5"
        style="background-color: var(--bg-input); border-color: var(--border-subtle)"
      >
        <Search class="w-3.5 h-3.5 shrink-0" style="color: var(--text-faint)" />
        <input aria-label="搜索交易记录"
          v-model="keyword"
          placeholder="搜索币种 / 策略 / 平仓原因..."
          class="flex-1 bg-transparent text-xs font-mono outline-none min-w-0"
          style="color: var(--text-main)"
        />
      </div>
    </AppCard>

    <!-- Trades Table with Fixed Max-Height (No infinite page stretching) -->
    <AppCard class="overflow-hidden flex flex-col"
    >
      <div ref="ledgerFrame" class="trade-ledger__frame" :class="{ 'is-loading': loadingTrades }"
        :style="frameMinHeight ? { minHeight: frameMinHeight + 'px' } : undefined">
        <Transition name="ledger-loading">
          <div v-if="loadingTrades" class="trade-ledger__loading" role="status" aria-live="polite">
            <div class="trade-ledger__progress" aria-hidden="true"><span /></div>
            <span class="trade-ledger__loading-label"><span class="trade-ledger__spinner" aria-hidden="true" />
              正在载入第 {{ Math.floor(requestedOffset / pageSize) + 1 }} 页
            </span>
          </div>
        </Transition>
        <div class="trade-ledger__contents" :inert="loadingTrades" :aria-busy="loadingTrades">
      <div v-if="loadingTrades && trades.length === 0" class="trade-ledger__skeleton" aria-hidden="true">
        <div v-for="row in 5" :key="row"><i /><i /><i /></div>
      </div>
      <div
        v-else-if="trades.length === 0"
        class="py-12 text-center text-xs font-mono"
        style="color: var(--text-muted)"
      >
        {{ tradesError ? '交易记录暂不可用' : '无匹配交易台账记录' }}
      </div>
      <template v-else>
        <p id="trade-ledger-scroll-hint" class="px-4 py-2 text-xs sm:hidden" style="color: var(--text-muted)">左右滑动表格查看完整交易字段</p>
        <AppTable label="交易记录明细" aria-describedby="trade-ledger-scroll-hint" class="overflow-y-auto max-h-[580px]">
        <table class="w-full text-left text-xs font-mono whitespace-nowrap">
          <thead class="sticky top-0 z-10" style="background-color: var(--bg-card)">
            <tr
              class="border-b text-[11px] uppercase tracking-wider"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <th class="py-3 px-4 font-bold">标的 / 方向</th>
              <th class="py-3 px-3 font-bold">策略来源</th>
              <th class="py-3 px-3 font-bold">周期</th>
              <th class="py-3 px-3 font-bold">保证金</th>
              <th class="py-3 px-3 font-bold">开仓价 / 时间</th>
              <th class="py-3 px-3 font-bold">平仓价 / 时间</th>
              <th class="py-3 px-3 text-right font-bold">净盈亏 / ROI</th>
              <th class="py-3 px-3 text-center font-bold">时长</th>
              <th class="py-3 px-4 font-bold">状态 / 平仓原因</th>
              <th scope="col" class="trade-ledger__fee-column py-3 px-3 text-center font-bold">费用明细</th>
            </tr>
          </thead>
          <tbody :key="pageRevision" class="trade-ledger__rows">
            <tr
              v-for="(t, idx) in trades"
              :key="t.id || idx"
              class="border-b last:border-b-0 transition-colors hover:bg-[var(--bg-card-hover)]"
              style="border-color: var(--border-subtle)"
            >
              <td class="py-3 px-4">
                <span class="font-bold text-sm" style="color: var(--text-main)">{{ t.inst }}</span>
                <span
                  class="ml-1.5 px-1.5 py-0.5 rounded text-[10px] font-bold border"
                  :style="{
                    backgroundColor:
                      t.side === '多' ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                    borderColor:
                      t.side === '多' ? 'var(--color-up-border)' : 'var(--color-down-border)',
                    color: t.side === '多' ? 'var(--color-up)' : 'var(--color-down)',
                  }"
                >
                  {{ t.side }} {{ t.lever || '杠杆未记录' }}
                </span>
              </td>
              <td class="py-3 px-3">
                <span
                  class="trade-ledger__strategy px-2 py-0.5 rounded border text-[11px]"
                  style="
                    background-color: var(--bg-badge);
                    border-color: var(--border-subtle);
                    color: var(--text-muted);
                  "
                >
                  {{ strategyLabel(t) }}
                </span>
              </td>
              <td class="py-3 px-3 text-xs" style="color: var(--text-muted)">
                <span class="px-2 py-0.5 rounded border" style="border-color: var(--border-subtle); background: var(--bg-badge)">{{ horizonLabel(t.horizon, t) }}</span><span v-if="t.duration_bucket && t.duration_bucket.endsWith('overdue')" class="ml-1 text-[10px]" style="color:var(--color-warn)">持仓超计划</span>
              </td>
              <td class="py-3 px-3 font-bold num-tabular" style="color: var(--text-main)">
                {{ marginText(t.margin) }}
              </td>
              <td class="py-3 px-3">
                <span class="num-tabular" style="color: var(--text-main)">{{
                  formatPx(t.open_px)
                }}</span>
                <span class="text-[10px] ml-1 num-tabular" style="color: var(--text-faint)"
                  >({{ (t.open_time || '--').substring(5, 19) }})</span
                >
              </td>
              <td class="py-3 px-3">
                <span
                  class="num-tabular"
                  :style="{
                    color: t.status === 'holding' ? 'var(--color-brand)' : 'var(--text-main)',
                  }"
                >
                  {{ t.status === 'holding' ? '盯盘中' : formatPx(t.close_px) }}
                </span>
                <span class="text-[10px] ml-1 num-tabular" style="color: var(--text-faint)">
                  ({{ isSettlementPending(t) ? '等待结算时间' : t.status === 'holding' ? '--' : (t.close_time || '--').substring(5, 19) }})
                </span>
              </td>
              <td class="py-3 px-3 text-right">
                <span v-if="isSettlementPending(t)" class="text-xs" style="color: var(--text-muted)">-- · 结算同步中</span>
                <template v-else>
                <span
                  class="font-bold text-sm num-tabular"
                  :style="{ color: ledgerNumberColor(getPnl(t)) }"
                >
                  {{ ledgerNumberText(getPnl(t), 2, ' U') }}
                </span>
                <span
                  class="text-[10px] ml-1 num-tabular"
                  :style="{ color: ledgerNumberColor(getRoi(t)) }"
                >
                  ({{ ledgerNumberText(getRoi(t), 2, '%') }})
                </span>
                </template>
              </td>
              <td class="py-3 px-3 text-center num-tabular" style="color: var(--text-muted)">
                {{ tradeDuration(t) }}
              </td>
              <td class="py-3 px-4 text-xs" style="color: var(--text-muted)">
                <span
                  class="px-2 py-0.5 rounded text-[10px] font-bold border mr-1"
                  :style="{
                    backgroundColor:
                      t.status === 'holding' ? 'var(--color-brand-bg)' : 'var(--bg-badge)',
                    borderColor:
                      t.status === 'holding' ? 'var(--color-brand-border)' : 'var(--border-subtle)',
                    color: t.status === 'holding' ? 'var(--color-brand)' : 'var(--text-muted)',
                  }"
                >
                  {{ t.status === 'holding' ? '在途' : isSettlementPending(t) ? '已平·待结算' : '已平' }}
                </span>
                <span class="trade-ledger__reason" :title="t.attribution_note || t.exit_evidence || ''">{{ clean(t.exit_reason, '持仓中') }}</span>
              </td>
              <td class="trade-ledger__fee-column py-2 px-3 text-center align-middle">
                <AppButton v-if="t.status === 'closed'" variant="ghost" size="sm" class="trade-ledger__fee-button" :aria-label="t.inst + '费用明细'" aria-haspopup="dialog" @click="showFees(t)">
                  <Receipt class="size-3.5 shrink-0" aria-hidden="true" />
                  <span>费用明细</span>
                </AppButton>
                <span v-else class="text-xs" style="color: var(--text-faint)" aria-label="平仓后可查看费用明细">—</span>
              </td>
            </tr>
          </tbody>
        </table>
        </AppTable>
      </template>

        </div>
      </div>

      <!-- Table Footer Summary -->
      <div v-if="tradesError" class="px-4 py-2 text-xs border-t flex items-center justify-between" style="border-color: var(--border-subtle); color: var(--color-down)">
        <span role="alert">{{ tradesError }}；{{ trades.length ? '页面未切换，仍显示上次成功载入的记录。' : '暂未取得可用记录，请重试。' }}</span>
        <button type="button" class="ui-button ui-button--secondary ui-button--sm py-0.5 px-2 text-xs" @click="fetchPage(requestedOffset)" :disabled="loadingTrades">重试加载</button>
      </div>
      <div
        class="px-4 py-2.5 border-t flex flex-wrap items-center justify-between gap-2 text-[11px] font-mono shrink-0"
        style="
          border-color: var(--border-subtle);
          background-color: var(--bg-card-subtle);
          color: var(--text-faint);
        "
      >
        <span v-if="serverTotal === null">{{ loadingTrades ? '正在读取交易记录…' : '尚未取得交易记录' }}</span>
        <span v-else-if="serverTotal > pageSize">
          已载入第 {{ offset + 1 }} - {{ Math.min(offset + trades.length, serverTotal) }} 笔，全账户共 {{ serverTotal }} 笔已保留的交易生命周期记录（含待结算，分页查阅）
        </span>
        <span v-else>已载入 {{ trades.length }} 笔已保留的交易生命周期记录（含待结算）</span>

        <div v-if="serverTotal !== null && serverTotal > pageSize" class="flex items-center gap-2">
          <button
            type="button"
            class="ui-action ui-action--sm border"
            style="border-color: var(--border-subtle); background: var(--bg-card)"
            :disabled="offset === 0 || loadingTrades"
            @click="fetchPage(offset - pageSize)"
          >
            上一页
          </button>
          <span class="num-tabular">{{ Math.floor(offset / pageSize) + 1 }} / {{ Math.ceil(serverTotal / pageSize) }}</span>
          <span class="trade-ledger__pager-loading" :class="{ 'is-visible': loadingTrades }" aria-hidden="true">
            <span v-if="loadingTrades" class="trade-ledger__spinner" />载入中…
          </span>
          <button
            type="button"
            class="ui-action ui-action--sm border"
            style="border-color: var(--border-subtle); background: var(--bg-card)"
            :disabled="offset + pageSize >= serverTotal || loadingTrades"
            @click="fetchPage(offset + pageSize)"
          >
            下一页
          </button>
        </div>
        <span v-else class="hidden sm:inline">OKX 当前账户历史履历</span>
      </div>
    </AppCard>
    <AppDialog v-model:open="feeDialogOpen" :title="(feeTrade?.inst || '') + ' 费用明细'" description="已结算交易的手续费证据；与订单操作无关。">
      <div v-if="feeTrade" class="space-y-3 text-sm" data-fee-reconciliation>
        <p class="font-semibold" style="color:var(--text-main)">{{ feeAccounting(feeTrade).label }}</p>
        <dl class="grid grid-cols-2 gap-3"><dt>开仓手续费</dt><dd class="text-right break-all">{{ feeText(feeAccounting(feeTrade).opening) }}</dd>
          <dt>平仓手续费</dt><dd class="text-right break-all">{{ feeText(feeAccounting(feeTrade).closing) }}</dd></dl>
        <p v-if="feeAccounting(feeTrade).verified" style="color:var(--text-muted)">按本账户成交量及官方总手续费核对，负值为扣费、正值为返佣；不改写官方净盈亏。</p>
        <p v-else style="color:var(--text-muted)">成交证据尚不完整或不一致，不按比例猜测费用。</p>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.trade-ledger__frame { position: relative; min-width: 0; max-width: 100%; min-height: 16rem; }
.trade-ledger__contents { min-width: 0; max-width: 100%; transition: opacity 140ms ease; }
.trade-ledger__frame.is-loading .trade-ledger__contents { opacity: .4; pointer-events: none; }
.trade-ledger__loading { position: absolute; inset: 0; z-index: 20; pointer-events: none; }
.trade-ledger__loading-label { position: absolute; top: 3.5rem; left: 50%; transform: translateX(-50%); display: flex; align-items: center; gap: .5rem; white-space: nowrap; padding: .65rem .9rem; border: 1px solid var(--border-medium); border-radius: .65rem; background: var(--bg-card); color: var(--text-main); box-shadow: var(--shadow-card); font-size: .75rem; }
.trade-ledger__spinner { width: .875rem; height: .875rem; border: 2px solid var(--border-medium); border-top-color: var(--color-brand); border-radius: 50%; animation: ledger-spin .75s linear infinite; }
.trade-ledger__progress { position: absolute; inset: 0 0 auto; height: 2px; overflow: hidden; background: var(--border-subtle); }
.trade-ledger__progress span { display: block; width: 35%; height: 100%; background: var(--color-brand); animation: ledger-progress 1.1s ease-in-out infinite; }
.trade-ledger__pager-loading { display: inline-flex; align-items: center; gap: .3rem; min-width: 4.5rem; visibility: hidden; font-size: .65rem; color: var(--text-muted); }
.trade-ledger__pager-loading.is-visible { visibility: visible; }
.trade-ledger__rows { animation: ledger-page-in 180ms ease-out; }
.trade-ledger__skeleton { padding: 4.5rem 1rem 1rem; }
.trade-ledger__skeleton > div { display: grid; grid-template-columns: 1fr 1.6fr 1fr; gap: 1rem; padding: 1rem 0; border-bottom: 1px solid var(--border-subtle); }
.trade-ledger__skeleton i { height: .6rem; border-radius: .25rem; background: var(--border-medium); animation: ledger-pulse 1.2s ease-in-out infinite alternate; }
.ledger-loading-enter-active, .ledger-loading-leave-active { transition: opacity 120ms ease; }
.ledger-loading-enter-from, .ledger-loading-leave-to { opacity: 0; }
@keyframes ledger-spin { to { transform: rotate(360deg); } }
@keyframes ledger-progress { from { transform: translateX(-110%); } to { transform: translateX(390%); } }
@keyframes ledger-page-in { from { opacity: .35; transform: translateY(3px); } to { opacity: 1; transform: translateY(0); } }
@keyframes ledger-pulse { to { opacity: .4; } }
@media (prefers-reduced-motion: reduce) {
  .trade-ledger__contents, .ledger-loading-enter-active, .ledger-loading-leave-active { transition: none; }
  .trade-ledger__rows, .trade-ledger__spinner, .trade-ledger__progress span, .trade-ledger__skeleton i { animation: none; }
}

.trade-ledger__fee-column { width: 7.5rem; white-space: nowrap; }
.trade-ledger__fee-button { min-height: 2.75rem; margin-inline: auto; gap: 0.375rem; color: var(--text-muted); border-color: var(--border-subtle); background: var(--bg-card-subtle); font-family: inherit; font-weight: 500; }
.trade-ledger__fee-button:hover { color: var(--text-main); border-color: var(--border-medium); background: var(--bg-card-hover); }
.trade-ledger__strategy { display: inline-block; max-width: 15rem; white-space: normal; overflow-wrap: anywhere; }
.trade-ledger__reason { display: inline-block; max-width: 22rem; white-space: normal; overflow-wrap: anywhere; vertical-align: middle; }
</style>
