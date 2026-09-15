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
const pct = (v: unknown) => v == null ? '--' : (Number(v) * 100).toFixed(1) + '%'
const money = (v: unknown) => v == null ? '--' : Number(v).toFixed(2) + ' U'
const profileLabel = computed(() => { const id = execution.value.id; return id === 'standard' ? '标准风控' : id === 'small300' ? '300U 小资金' : execution.value.label || '未设置' })
const statusLabel = computed(() => {
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
const statusTone = computed(() => ['不可用', '待补全', '已熔断', '未通过'].includes(statusLabel.value) ? 'warning' : statusLabel.value === '有候选' ? 'brand' : 'neutral')
const dailyLossPct = computed(() => {
  const pnl = Number(store.data?.today_stats?.total_pnl)
  const initial = Number(store.data?.account?.initial_capital)
  if (!Number.isFinite(pnl) || !Number.isFinite(initial) || initial <= 0) return null
  return (pnl / initial) * 100
})
const dailyLimitPct = computed(() => {
  const value = Number(execution.value.daily_drawdown_pct)
  return Number.isFinite(value) ? value * 100 : null
})
const dailyCircuitTriggered = computed(() => dailyLossPct.value !== null && dailyLimitPct.value !== null && dailyLossPct.value <= -dailyLimitPct.value)
const signedPct = (value: number | null) => value === null ? '--' : `${value >= 0 ? '+' : ''}${value.toFixed(2)}%`
</script>
<template>
  <div class="strategy-telemetry" data-strategy-telemetry>
    <AppCard class="telemetry-card telemetry-card--profile"><div class="telemetry-card__heading"><div><p class="telemetry-card__eyebrow">执行模式</p><h3>{{ profileLabel }}</h3></div><AppBadge tone="brand">{{ execution.id === 'standard' ? '标准风控' : execution.id === 'small300' ? '300U 小资金' : execution.id || '标准风控' }}</AppBadge></div><div class="telemetry-card__body telemetry-card__params"><div><span>单笔风险</span><strong>{{ pct(execution.per_trade_equity_pct) }}</strong></div><div><span>最大杠杆</span><strong>波段 5x · 短线最高 20x</strong></div><div><span>持仓上限</span><strong>{{ execution.max_active_instruments ?? '--' }} 个</strong></div><div><span>保证金上限</span><strong>{{ execution.total_margin_usdt ?? '--' }}U</strong></div></div></AppCard>
    <AppCard class="telemetry-card telemetry-card--stats"><div class="telemetry-card__heading"><div><p class="telemetry-card__eyebrow">策略统计</p><h3>短线 / 波段</h3></div><AppBadge tone="neutral">实时更新</AppBadge></div><div class="telemetry-card__body telemetry-card__stats"><div class="telemetry-stat"><span>短线净盈亏</span><strong :style="{color:Number(stat('scalp').net_pnl || 0)>=0?'var(--color-up)':'var(--color-down)'}">{{ money(stat('scalp').net_pnl) }}</strong><small>{{ stat('scalp').closed || 0 }} 笔 · {{ stat('scalp').wins || 0 }} 胜</small></div><div class="telemetry-stat"><span>波段净盈亏</span><strong :style="{color:Number(stat('swing').net_pnl || 0)>=0?'var(--color-up)':'var(--color-down)'}">{{ money(stat('swing').net_pnl) }}</strong><small>{{ stat('swing').closed || 0 }} 笔 · {{ stat('swing').wins || 0 }} 胜</small></div></div></AppCard>
    <AppCard class="telemetry-card telemetry-card--decision"><div class="telemetry-card__heading"><div><p class="telemetry-card__eyebrow">决策状态</p><h3>当前状态</h3></div><AppBadge :tone="statusTone" data-decision-status>{{ statusLabel }}</AppBadge></div><div class="telemetry-card__body telemetry-card__decision"><div class="telemetry-decision__meta"><span>已审查 <strong>{{ wait?.evaluated_count ?? '--' }}</strong></span><span>候选 <strong>{{ wait?.counts?.entry_candidate ?? 0 }}</strong></span><span>日内亏损 <strong>{{ signedPct(dailyLossPct) }}</strong></span><span>熔断阈值 <strong>{{ dailyLimitPct === null ? '--' : dailyLimitPct.toFixed(1) + '%' }}</strong></span><span>状态 <strong :style="{ color: dailyCircuitTriggered ? 'var(--color-down)' : 'var(--color-up)' }">{{ dailyCircuitTriggered ? '已触发' : '未触发' }}</strong></span></div></div></AppCard>
  </div>
</template>
<style scoped>
.strategy-telemetry{display:grid;gap:.75rem}.telemetry-card{min-width:0;padding:1rem 1.15rem;display:grid;grid-template-columns:minmax(145px,190px) minmax(0,1fr);align-items:center;gap:1rem 1.5rem}.telemetry-card__heading{min-width:0;display:flex;align-items:center;justify-content:space-between;gap:.75rem}.telemetry-card__eyebrow{margin:0 0 .2rem;color:var(--text-faint);font-size:.7rem;letter-spacing:.08em}.telemetry-card h3{margin:0;color:var(--text-main);font-size:.95rem;font-weight:650}.telemetry-card__body{min-width:0}.telemetry-card__params,.telemetry-card__stats,.telemetry-card__decision{display:flex;align-items:center;min-width:0}.telemetry-card__params{justify-content:space-between;gap:1.25rem}.telemetry-card__params div{display:grid;gap:.2rem;min-width:0}.telemetry-card__params span,.telemetry-stat span,.telemetry-decision__meta span{color:var(--text-muted);font-size:.72rem;white-space:nowrap}.telemetry-card__params strong{color:var(--text-main);font-size:.88rem;font-variant-numeric:tabular-nums;white-space:nowrap}.telemetry-card__stats{gap:.75rem}.telemetry-stat{flex:1 1 0;min-width:0;padding:.65rem .85rem;border-radius:.6rem;background:var(--bg-card-subtle);display:grid;gap:.18rem}.telemetry-stat strong{font-size:1.1rem;font-variant-numeric:tabular-nums}.telemetry-stat small,.telemetry-decision__copy small{color:var(--text-faint);font-size:.7rem}.telemetry-card__decision{gap:1.5rem;justify-content:flex-end}.telemetry-decision__copy{flex:1 1 auto;min-width:0}.telemetry-decision__copy p{margin:0;color:var(--text-muted);font-size:.82rem;line-height:1.55;overflow-wrap:anywhere}.telemetry-decision__status{font-weight:750;color:var(--text-main)!important;font-size:1rem!important}.telemetry-decision__copy small{display:block;margin-top:.35rem;line-height:1.45;overflow-wrap:anywhere}.telemetry-decision__meta{flex:0 0 auto;display:flex;flex-wrap:wrap;justify-content:flex-end;gap:.55rem 1rem}.telemetry-decision__meta span{display:grid;gap:.15rem}.telemetry-decision__meta strong{color:var(--text-main);font-size:.85rem;font-variant-numeric:tabular-nums}@media (max-width:900px){.telemetry-card{grid-template-columns:1fr;gap:.75rem}.telemetry-card__params,.telemetry-card__decision{align-items:flex-start}}@media (max-width:560px){.telemetry-card__params{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:.7rem}.telemetry-card__stats,.telemetry-card__decision{display:grid;grid-template-columns:1fr;gap:.8rem}.telemetry-decision__meta{justify-content:flex-start}}
</style>
