<script setup lang="ts">
import { positionQuotaLabel } from '../utils/entryQuota'
import { computed, ref } from 'vue'
import AppCard from './ui/AppCard.vue'
import AppBadge from './ui/AppBadge.vue'
import { useDashboardStore } from '../stores/dashboard'

const store = useDashboardStore()
const activePeriod = ref<'today' | 'all'>('today')

const stats = computed(() => store.data?.horizon_stats || {})
const profile = computed(() => store.data?.execution_profile)
const wait = computed(() => store.data?.decision_cycle)
const waitState = computed(() => store.data?.wait_state)
const execution = computed(() => profile.value?.execution || {})

const periodsData = computed(() => ((stats.value as any)?.periods as Record<string, any>) || null)
const activePeriodData = computed(() => {
  if (periodsData.value) {
    return periodsData.value[activePeriod.value] || null
  }
  return null
})

const isUnavailable = computed(() => stats.value?.error === 'statistics_unavailable')

function stat(key: string): any {
  if (isUnavailable.value || !activePeriodData.value) return {}
  return activePeriodData.value[key] || {}
}

const number = (v: unknown): number | null => {
  if (typeof v !== 'number' && (typeof v !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(v.trim()))) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}

const pct = (v: unknown) => {
  const n = number(v)
  return n === null || n < 0 ? '--' : Number((n * 100).toFixed(4)) + '%'
}

const money = (v: unknown) => {
  const n = number(v)
  return n === null ? '--' : n.toFixed(2) + ' U'
}

const count = (v: unknown) => {
  const n = number(v)
  return n !== null && Number.isSafeInteger(n) && n >= 0 ? n : '--'
}

function horizonNetPnlText(h: any): string {
  const val = number(h?.net_pnl)
  if (val !== null) return money(val)
  const obs = number(h?.observed?.net_pnl)
  if (obs !== null) return money(obs) + ' (部分)'
  return '--'
}

function horizonWinRateText(h: any): string {
  const closed = number(h?.closed)
  if (closed === null || closed <= 0) return '--'
  const rate = number(h?.win_rate)
  if (rate !== null && rate >= 0 && rate <= 1) {
    return (rate * 100).toFixed(1) + '%'
  }
  const wins = number(h?.wins)
  if (wins !== null && closed > 0) {
    return ((wins / closed) * 100).toFixed(1) + '%'
  }
  return '--'
}

const leverage = computed(() => {
  const limits = profile.value?.mode_limits
  const scalp = number(limits?.scalp?.max_leverage)
  const swing = number(limits?.swing?.max_leverage)
  if (scalp !== null && swing !== null) return `波段 ${swing}x · 短线最高 ${scalp}x`
  const n = number(execution.value.max_leverage)
  return n !== null && n > 0 ? `${n}x（基础上限）` : '未提供'
})

const riskPerTrade = computed(() => {
  const limits = profile.value?.mode_limits
  return limits ? `短线 ${pct(limits.scalp?.per_trade_equity_pct)} · 波段 ${pct(limits.swing?.per_trade_equity_pct)}` : pct(execution.value.per_trade_equity_pct)
})

const hasStats = computed(() => {
  if (isUnavailable.value || !activePeriodData.value) return false
  return ['scalp', 'swing'].some((key) => number(activePeriodData.value[key]?.closed) !== null)
})

const statsStatusLabel = computed(() => {
  if (isUnavailable.value) return '统计暂不可用'
  if (store.data?.risk_status?.daily_blocked === true) return '日内熔断期间'
  if (periodsData.value !== null && !activePeriodData.value) return '周期暂无数据'
  return '已结算样本'
})

const profitTone = (v: unknown) => {
  const n = number(v)
  return n === null ? 'var(--text-muted)' : n >= 0 ? 'var(--color-up)' : 'var(--color-down)'
}

const profileLabel = computed(() => {
  const id = execution.value.id
  return id === 'standard' ? '标准风控' : id === 'small300' ? '300U 小资金' : execution.value.label || '未设置'
})

const statusLabel = computed(() => {
  if ((store.data?.risk_status?.unresolved_entries ?? 0) > 0) return '待核对'
  if (!wait.value && !waitState.value) return '尚未取得'
  const code = waitState.value?.code
  const status = wait.value?.status
  if (status === 'unavailable' || status === 'failed' || wait.value?.unavailable_reason || code === 'AI_UNAVAILABLE' || code === 'DATA_UNAVAILABLE') return '不可用'
  if (status === 'incomplete' || code === 'AUDIT_INCOMPLETE') return '待补全'
  if (status === 'circuit_breaker') return '已熔断'
  if (code === 'EXECUTION_REJECTED' || (wait.value?.counts?.execution_rejected || 0) > 0) return '未通过'
  if (code === 'CANDIDATE_REVIEW' || (wait.value?.counts?.entry_candidate || 0) > 0) return '有候选'
  if (status === 'running' || status === 'pending') return '处理中'
  return '等待'
})

const statusTone = computed(() =>
  ['不可用', '待补全', '已熔断', '未通过', '待核对'].includes(statusLabel.value)
    ? 'warning'
    : statusLabel.value === '有候选'
      ? 'brand'
      : 'neutral'
)

const dailyLossPct = computed(() => {
  const value = number(store.data?.risk_status?.daily_drawdown)
  return value === null ? null : -value * 100
})

const dailyLimitPct = computed(() => {
  const value = number(store.data?.risk_status?.daily_threshold ?? execution.value.daily_drawdown_pct)
  return value !== null && value > 0 ? value * 100 : null
})

const dailyCircuitTriggered = computed(() => store.data?.risk_status?.daily_blocked)
const signedPct = (value: number | null) => (value === null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`)

// Unknown stats
const unknownStat = computed(() => stat('unknown'))
const hasUnknownOrders = computed(() => {
  const u = unknownStat.value
  const c = number(u?.closed) || 0
  const o = number(u?.opened) || 0
  const p = number(u?.pending_settlements) || 0
  return c > 0 || o > 0 || p > 0
})

// Floating PnL from positions
const floatingPnl = computed(() => {
  const items = store.positions || []
  let sum = 0
  let hasValid = false
  for (const it of items) {
    const p = number(it?.upl || (it as any)?.floating_pnl)
    if (p !== null) {
      sum += p
      hasValid = true
    }
  }
  return hasValid ? sum : null
})
</script>
<template>
  <div class="strategy-telemetry" data-strategy-telemetry>
    <!-- Card 1: Execution Profile -->
    <AppCard class="telemetry-card telemetry-card--profile">
      <div class="telemetry-card__heading">
        <div class="telemetry-heading__top">
          <span class="telemetry-card__eyebrow">执行模式</span>
          <AppBadge :tone="profile?.error ? 'warning' : execution.id ? 'brand' : 'neutral'">
            {{ profile?.error ? '配置不可用' : execution.id === 'standard' ? '标准风控' : execution.id === 'small300' ? '300U 小资金' : execution.id || '尚未取得' }}
          </AppBadge>
        </div>
        <div class="telemetry-heading__title-row">
          <h3>{{ profileLabel }}</h3>
        </div>
      </div>
      <div class="telemetry-card__body telemetry-card__params">
        <div><span>单笔风险</span><strong>{{ riskPerTrade }}</strong></div>
        <div><span>最大杠杆</span><strong>{{ leverage }}</strong></div>
        <div><span>数量控制</span><strong>{{ positionQuotaLabel(execution) }}</strong></div>
        <div><span>保证金上限</span><strong>{{ number(execution.total_margin_usdt) === null && execution.id === 'standard' ? '按账户可用资金' : money(execution.total_margin_usdt) }}</strong></div>
      </div>
    </AppCard>

    <!-- Card 2: Strategy Telemetry with TODAY / ALL tabs -->
    <AppCard class="telemetry-card telemetry-card--stats" data-strategy-stats>
      <div class="telemetry-card__heading telemetry-card__heading--stats">
        <div class="telemetry-heading__top">
          <span class="telemetry-card__eyebrow">策略统计</span>
          <AppBadge :tone="isUnavailable ? 'warning' : 'neutral'" :title="store.error || store.isStale ? '后台同步中，当前为最近一次已结算样本' : '已核验的结算样本'">
            {{ !hasStats ? '暂无统计' : statsStatusLabel }}
          </AppBadge>
        </div>
        <div class="telemetry-heading__title-row">
          <h3>短线 / 波段</h3>
          <!-- Responsive accessible tabs for TODAY / ALL -->
          <div
            v-if="periodsData"
            class="telemetry-tabs"
            role="tablist"
            aria-label="策略统计周期"
          >
            <button
              type="button"
              role="tab"
              :aria-selected="activePeriod === 'today'"
              class="ui-tab-control telemetry-tab-btn"
              :class="{ 'telemetry-tab-btn--active': activePeriod === 'today' }"
              @click="activePeriod = 'today'"
            >
              当日统计
            </button>
            <button
              type="button"
              role="tab"
              :aria-selected="activePeriod === 'all'"
              class="ui-tab-control telemetry-tab-btn"
              :class="{ 'telemetry-tab-btn--active': activePeriod === 'all' }"
              @click="activePeriod = 'all'"
            >
              全局统计
            </button>
          </div>
        </div>
        <div v-if="isUnavailable" class="telemetry-hint text-amber-500 font-medium">
          统计提示：当前账户历史分段统计尚未完成聚合
        </div>
        <div v-else-if="periodsData !== null && !activePeriodData" class="telemetry-hint text-amber-500 font-medium">
          周期提示：当前所选周期 ({{ activePeriod === 'today' ? '当日统计' : '全局统计' }}) 暂无分段统计
        </div>
      </div>

      <div class="telemetry-card__body telemetry-card__stats">
        <!-- Scalp Stats -->
        <div class="telemetry-stat">
          <div class="telemetry-stat__top">
            <span>短线净盈亏</span>
            <span v-if="horizonWinRateText(stat('scalp')) !== '--'" class="telemetry-stat__winrate">
              胜率 {{ horizonWinRateText(stat('scalp')) }}
            </span>
          </div>
          <strong :style="{ color: profitTone(stat('scalp').net_pnl) }">
            {{ horizonNetPnlText(stat('scalp')) }}
          </strong>
          <small>
            {{ count(stat('scalp').closed) }} 笔 · {{ count(stat('scalp').wins) }} 胜
            <template v-if="number(stat('scalp').pending_settlements)">
              · {{ stat('scalp').pending_settlements }} 待结
            </template>
          </small>
        </div>

        <!-- Swing Stats -->
        <div class="telemetry-stat">
          <div class="telemetry-stat__top">
            <span>波段净盈亏</span>
            <span v-if="horizonWinRateText(stat('swing')) !== '--'" class="telemetry-stat__winrate">
              胜率 {{ horizonWinRateText(stat('swing')) }}
            </span>
          </div>
          <strong :style="{ color: profitTone(stat('swing').net_pnl) }">
            {{ horizonNetPnlText(stat('swing')) }}
          </strong>
          <small>
            {{ count(stat('swing').closed) }} 笔 · {{ count(stat('swing').wins) }} 胜
            <template v-if="number(stat('swing').pending_settlements)">
              · {{ stat('swing').pending_settlements }} 待结
            </template>
          </small>
        </div>
      </div>

      <p v-if="store.data?.statistics_epoch" class="text-xs px-4 pb-3" style="color:var(--text-muted)" data-statistics-start>统计起点 {{ store.data.statistics_epoch.reset_time }} · 旧样本不计入当前周期</p>
      <!-- Additional Stats Info Row: Unclassified & Floating PnL -->
      <div
        v-if="hasUnknownOrders || floatingPnl !== null"
        class="telemetry-card__footer col-span-full pt-2 border-t flex flex-wrap items-center gap-2 text-[10px] font-mono text-[var(--text-faint)]"
        style="border-color: var(--border-subtle)"
      >
        <div class="flex items-center flex-wrap gap-2">
          <span v-if="hasUnknownOrders" class="text-amber-500 font-medium">
            未分类订单: {{ count(unknownStat.closed) }} 笔平仓 · {{ count(unknownStat.opened) }} 笔在途 (不计入胜率)
          </span>
          <span v-if="floatingPnl !== null">
            持仓浮动盈亏: <strong :style="{ color: profitTone(floatingPnl) }">{{ money(floatingPnl) }}</strong>
          </span>
        </div>
      </div>
    </AppCard>

    <!-- Card 3: Decision State -->
    <AppCard class="telemetry-card telemetry-card--decision">
      <div class="telemetry-card__heading">
        <div class="telemetry-heading__top">
          <h3>当前状态</h3>
          <AppBadge :tone="statusTone" data-decision-status>{{ statusLabel }}</AppBadge>
        </div>
      </div>
      <div class="telemetry-card__body telemetry-card__decision">
        <div class="telemetry-decision__meta">
          <span>已审查 <strong>{{ wait?.evaluated_count ?? '--' }}</strong></span>
          <span>候选 <strong>{{ wait?.counts?.entry_candidate ?? '--' }}</strong></span>
          <span title="执行层账户与资金分配的日内权益回撤，含已结账本检查">日内权益回撤 <strong>{{ signedPct(dailyLossPct) }}</strong></span>
          <span>熔断阈值 <strong>{{ dailyLimitPct === null ? '--' : dailyLimitPct.toFixed(1) + '%' }}</strong></span>
          <span>状态 <strong :style="{ color: dailyCircuitTriggered ? 'var(--color-down)' : 'var(--text-muted)' }">{{ dailyCircuitTriggered === true ? '已触发' : dailyCircuitTriggered === false ? '未触发' : '待核验' }}</strong></span>
        </div>
      </div>
    </AppCard>
  </div>
</template>
<style scoped>
.strategy-telemetry {
  display: grid;
  gap: 0.75rem;
  container-type: inline-size;
  min-width: 0;
  max-width: 100%;
}
.telemetry-card {
  min-width: 0;
  max-width: 100%;
  padding: 0.75rem 0.875rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
  box-sizing: border-box;
}
.telemetry-card__heading {
  min-width: 0;
  width: 100%;
  display: flex;
  flex-direction: column;
  gap: 0.35rem;
}
.telemetry-heading__top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 0.5rem;
  width: 100%;
  min-width: 0;
}
.telemetry-heading__title-row {
  display: flex;
  align-items: center;
  justify-content: space-between;
  flex-wrap: wrap;
  gap: 0.5rem 0.75rem;
  width: 100%;
  min-width: 0;
}
.telemetry-card__eyebrow {
  margin: 0;
  color: var(--text-faint);
  font-size: 0.7rem;
  letter-spacing: 0.06em;
  text-transform: uppercase;
  white-space: nowrap;
}
.telemetry-card h3 {
  margin: 0;
  color: var(--text-main);
  font-size: 0.9375rem;
  font-weight: 650;
  white-space: nowrap;
}
.telemetry-tabs {
  display: inline-flex;
  align-items: center;
  padding: 2px;
  border-radius: 6px;
  border: 1px solid var(--border-subtle);
  background: var(--bg-card-subtle);
  gap: 2px;
  max-width: 100%;
}
.telemetry-tab-btn {
  min-width: 60px;
  border: none;
  color: var(--text-muted);
  background: transparent;
}
.telemetry-tab-btn--active {
  background: var(--bg-card);
  font-weight: 700;
  color: var(--color-brand);
  box-shadow: 0 1px 2px rgba(0, 0, 0, 0.05);
}
.telemetry-hint {
  font-size: 0.7rem;
  line-height: 1.4;
  margin-top: 0.2rem;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.telemetry-card__body {
  min-width: 0;
  width: 100%;
}
.telemetry-card__params {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 0.5rem 0.75rem;
  min-width: 0;
  width: 100%;
}
.telemetry-card__params div {
  display: grid;
  gap: 0.15rem;
  min-width: 0;
}
.telemetry-card__params span {
  color: var(--text-muted);
  font-size: 0.72rem;
  white-space: nowrap;
}
.telemetry-card__params strong {
  color: var(--text-main);
  font-size: 0.85rem;
  font-variant-numeric: tabular-nums;
  overflow-wrap: anywhere;
  line-height: 1.4;
}
.telemetry-card__stats {
  display: grid;
  grid-template-columns: minmax(0, 1fr);
  gap: 0.65rem;
  min-width: 0;
  width: 100%;
}
.telemetry-stat {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
  padding: 0.55rem 0.75rem;
  border-radius: 0.5rem;
  background: var(--bg-card-subtle);
  border: 1px solid var(--border-subtle);
  display: grid;
  gap: 0.2rem;
}
.telemetry-stat__top {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  gap: 0.35rem;
  flex-wrap: wrap;
  width: 100%;
  min-width: 0;
}
.telemetry-stat__top span:first-child {
  color: var(--text-muted);
  font-size: 0.72rem;
  white-space: nowrap;
}
.telemetry-stat__winrate {
  font-size: 0.68rem;
  color: var(--text-faint);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.telemetry-stat strong {
  font-size: 1.05rem;
  font-variant-numeric: tabular-nums;
  line-height: 1.25;
  overflow-wrap: anywhere;
  word-break: break-all;
  width: 100%;
}
.telemetry-stat small {
  color: var(--text-faint);
  font-size: 0.68rem;
  font-variant-numeric: tabular-nums;
  line-height: 1.35;
  overflow-wrap: anywhere;
  word-break: break-word;
}
.telemetry-card__decision {
  display: flex;
  justify-content: flex-start;
  min-width: 0;
  width: 100%;
}
.telemetry-decision__meta {
  display: flex;
  flex-wrap: wrap;
  justify-content: flex-start;
  gap: 0.45rem 0.9rem;
  min-width: 0;
  width: 100%;
}
.telemetry-decision__meta span {
  display: inline-flex;
  align-items: baseline;
  gap: 0.25rem;
  font-size: 0.72rem;
  white-space: nowrap;
  color: var(--text-muted);
}
.telemetry-decision__meta strong {
  color: var(--text-main);
  font-size: 0.82rem;
  font-variant-numeric: tabular-nums;
}

@container (min-width: 500px) {
  .telemetry-card__stats {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@container (min-width: 640px) {
  .telemetry-card__params {
    grid-template-columns: repeat(4, minmax(0, 1fr));
  }
}

@container (min-width: 768px) {
  .telemetry-card {
    display: grid;
    grid-template-columns: minmax(180px, 230px) minmax(0, 1fr);
    align-items: center;
    gap: 1rem 1.5rem;
    padding: 1rem 1.15rem;
  }
  .telemetry-card__decision {
    justify-content: flex-end;
  }
  .telemetry-decision__meta {
    justify-content: flex-end;
  }
}
</style>
