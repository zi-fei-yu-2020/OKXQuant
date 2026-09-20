<script setup lang="ts">
import { computed } from 'vue'
import AppCard from './ui/AppCard.vue'
import AppBadge from './ui/AppBadge.vue'
import { useDashboardStore } from '../stores/dashboard'
const store = useDashboardStore()
const stats = computed(() => store.data?.horizon_stats || {})
const profile = computed(() => store.data?.execution_profile)
const wait = computed(() => store.data?.decision_cycle)
const waitState = computed(() => store.data?.wait_state)
const execution = computed(() => profile.value?.execution || {})
const stat = (key: string) => stats.value[key] || {}
const number = (v: unknown): number | null => {
  if (typeof v !== 'number' && (typeof v !== 'string' || !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(v.trim()))) return null
  const n = Number(v)
  return Number.isFinite(n) ? n : null
}
const pct = (v: unknown) => { const n = number(v); return n === null || n < 0 ? '--' : Number((n * 100).toFixed(4)) + '%' }
const money = (v: unknown) => { const n = number(v); return n === null ? '--' : n.toFixed(2) + ' U' }
const count = (v: unknown) => { const n = number(v); return n !== null && Number.isSafeInteger(n) && n >= 0 ? n : '--' }
const leverage = computed(() => { const limits = profile.value?.mode_limits; const scalp = number(limits?.scalp?.max_leverage), swing = number(limits?.swing?.max_leverage); if (scalp !== null && swing !== null) return `波段 ${swing}x · 短线最高 ${scalp}x`; const n = number(execution.value.max_leverage); return n !== null && n > 0 ? `${n}x（基础上限）` : '未提供' })
const riskPerTrade = computed(() => { const limits = profile.value?.mode_limits; return limits ? `短线 ${pct(limits.scalp?.per_trade_equity_pct)} · 波段 ${pct(limits.swing?.per_trade_equity_pct)}` : pct(execution.value.per_trade_equity_pct) })
const hasStats = computed(() => ['scalp', 'swing'].some(key => number(stat(key).closed) !== null))
const profitTone = (v: unknown) => { const n = number(v); return n === null ? 'var(--text-muted)' : n >= 0 ? 'var(--color-up)' : 'var(--color-down)' }
const profileLabel = computed(() => { const id = execution.value.id; return id === 'standard' ? '标准风控' : id === 'small300' ? '300U 小资金' : execution.value.label || '未设置' })
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
const statusTone = computed(() => ['不可用', '待补全', '已熔断', '未通过', '待核对'].includes(statusLabel.value) ? 'warning' : statusLabel.value === '有候选' ? 'brand' : 'neutral')
const dailyLossPct = computed(() => {
  const value = number(store.data?.risk_status?.daily_drawdown)
  return value === null ? null : -value * 100
})
const dailyLimitPct = computed(() => {
  const value = number(store.data?.risk_status?.daily_threshold ?? execution.value.daily_drawdown_pct)
  return value !== null && value > 0 ? value * 100 : null
})
const dailyCircuitTriggered = computed(() => store.data?.risk_status?.daily_blocked)
const signedPct = (value: number | null) => value === null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
</script>
<template>
  <div class="strategy-telemetry" data-strategy-telemetry>
    <AppCard class="telemetry-card telemetry-card--profile"><div class="telemetry-card__heading"><div><p class="telemetry-card__eyebrow">执行模式</p><h3>{{ profileLabel }}</h3></div><AppBadge :tone="profile?.error ? 'warning' : execution.id ? 'brand' : 'neutral'">{{ profile?.error ? '配置不可用' : execution.id === 'standard' ? '标准风控' : execution.id === 'small300' ? '300U 小资金' : execution.id || '尚未取得' }}</AppBadge></div><div class="telemetry-card__body telemetry-card__params"><div><span>单笔风险</span><strong>{{ riskPerTrade }}</strong></div><div><span>最大杠杆</span><strong>{{ leverage }}</strong></div><div><span>持仓上限</span><strong>{{ count(execution.max_active_instruments) }} 个</strong></div><div><span>保证金上限</span><strong>{{ number(execution.total_margin_usdt) === null && execution.id === 'standard' ? '按账户可用资金' : money(execution.total_margin_usdt) }}</strong></div></div></AppCard>
    <AppCard class="telemetry-card telemetry-card--stats"><div class="telemetry-card__heading"><div><p class="telemetry-card__eyebrow">策略统计</p><h3>短线 / 波段</h3></div><AppBadge :tone="store.error || store.isStale ? 'warning' : 'neutral'">{{ !hasStats ? '暂无统计' : store.error || store.isStale ? '历史快照 · 更新延迟' : '已结算样本' }}</AppBadge></div><div class="telemetry-card__body telemetry-card__stats"><div class="telemetry-stat"><span>短线净盈亏</span><strong :style="{color:profitTone(stat('scalp').net_pnl)}">{{ money(stat('scalp').net_pnl) }}</strong><small>{{ count(stat('scalp').closed) }} 笔 · {{ count(stat('scalp').wins) }} 胜</small></div><div class="telemetry-stat"><span>波段净盈亏</span><strong :style="{color:profitTone(stat('swing').net_pnl)}">{{ money(stat('swing').net_pnl) }}</strong><small>{{ count(stat('swing').closed) }} 笔 · {{ count(stat('swing').wins) }} 胜</small></div></div></AppCard>
    <AppCard class="telemetry-card telemetry-card--decision"><div class="telemetry-card__heading"><div><p class="telemetry-card__eyebrow">决策状态</p><h3>当前状态</h3></div><AppBadge :tone="statusTone" data-decision-status>{{ statusLabel }}</AppBadge></div><div class="telemetry-card__body telemetry-card__decision"><div class="telemetry-decision__meta"><span>已审查 <strong>{{ wait?.evaluated_count ?? '--' }}</strong></span><span>候选 <strong>{{ wait?.counts?.entry_candidate ?? '--' }}</strong></span><span title="执行层账户与资金分配的日内回撤，含已结账本检查">日内亏损 <strong>{{ signedPct(dailyLossPct) }}</strong></span><span>熔断阈值 <strong>{{ dailyLimitPct === null ? '--' : dailyLimitPct.toFixed(1) + '%' }}</strong></span><span>状态 <strong :style="{ color: dailyCircuitTriggered ? 'var(--color-down)' : 'var(--text-muted)' }">{{ dailyCircuitTriggered === true ? '已触发' : dailyCircuitTriggered === false ? '未触发' : '待核验' }}</strong></span></div></div></AppCard>
  </div>
</template>
<style scoped>
.strategy-telemetry{display:grid;gap:.75rem;container-type:inline-size}.telemetry-card{min-width:0;padding:1rem 1.15rem;display:grid;grid-template-columns:minmax(145px,190px) minmax(0,1fr);align-items:center;gap:1rem 1.5rem}.telemetry-card__heading{min-width:0;display:flex;align-items:center;justify-content:space-between;gap:.75rem}.telemetry-card__eyebrow{margin:0 0 .2rem;color:var(--text-faint);font-size:.7rem;letter-spacing:.08em}.telemetry-card h3{margin:0;color:var(--text-main);font-size:.95rem;font-weight:650}.telemetry-card__body{min-width:0}.telemetry-card__params,.telemetry-card__stats,.telemetry-card__decision{display:flex;align-items:center;min-width:0}.telemetry-card__params{justify-content:space-between;gap:1.25rem}.telemetry-card__params div{display:grid;gap:.2rem;min-width:0}.telemetry-card__params span,.telemetry-stat span,.telemetry-decision__meta span{color:var(--text-muted);font-size:.72rem;white-space:nowrap}.telemetry-card__params strong{color:var(--text-main);font-size:.88rem;font-variant-numeric:tabular-nums;white-space:normal;overflow-wrap:anywhere;line-height:1.55}.telemetry-card__stats{gap:.75rem}.telemetry-stat{flex:1 1 0;min-width:0;padding:.65rem .85rem;border-radius:.6rem;background:var(--bg-card-subtle);display:grid;gap:.18rem}.telemetry-stat strong{font-size:1.1rem;font-variant-numeric:tabular-nums}.telemetry-stat small,.telemetry-decision__copy small{color:var(--text-faint);font-size:.7rem}.telemetry-card__decision{gap:1.5rem;justify-content:flex-end}.telemetry-decision__copy{flex:1 1 auto;min-width:0}.telemetry-decision__copy p{margin:0;color:var(--text-muted);font-size:.82rem;line-height:1.55;overflow-wrap:anywhere}.telemetry-decision__status{font-weight:750;color:var(--text-main)!important;font-size:1rem!important}.telemetry-decision__copy small{display:block;margin-top:.35rem;line-height:1.45;overflow-wrap:anywhere}.telemetry-decision__meta{flex:0 0 auto;display:flex;flex-wrap:wrap;justify-content:flex-end;gap:.55rem 1rem}.telemetry-decision__meta span{display:grid;gap:.15rem}.telemetry-decision__meta strong{color:var(--text-main);font-size:.85rem;font-variant-numeric:tabular-nums}@media (max-width:900px){.telemetry-card{grid-template-columns:1fr;gap:.75rem}.telemetry-card__params,.telemetry-card__decision{align-items:flex-start}}@media (max-width:560px){.telemetry-card__params{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem}.telemetry-card__stats,.telemetry-card__decision{display:grid;grid-template-columns:1fr;gap:.8rem}.telemetry-decision__meta{justify-content:flex-start}}
@container (max-width:720px){.telemetry-card{grid-template-columns:1fr;gap:.65rem;padding:.9rem 1rem}.telemetry-card__params{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.6rem 1.1rem}.telemetry-card__params div{gap:.1rem}.telemetry-card__decision{justify-content:flex-start}.telemetry-decision__meta{justify-content:flex-start;gap:.55rem 1.1rem}.telemetry-card__heading{justify-content:space-between}.telemetry-card__eyebrow{margin-bottom:.1rem}}
</style>
