<script setup lang="ts">
import { computed } from 'vue'
import { Wallet, TrendingUp, CalendarDays, Layers } from 'lucide-vue-next'
import { useDashboardStore } from '../stores/dashboard'
import AppCard from './ui/AppCard.vue'
const store = useDashboardStore()
const account = computed(() => store.data?.account)
const ready = computed(() => account.value?.total_eq != null)
const today = computed(() => store.data?.today_stats)
const fmt = (value: unknown) =>
  value == null || !Number.isFinite(Number(value))
    ? '—'
    : Number(value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
const signed = (value: unknown) =>
  value == null ? '—' : `${Number(value) > 0 ? '+' : ''}${fmt(value)}`
const tone = (value: unknown) =>
  !ready.value || value == null || Number(value) === 0
    ? 'var(--text-main)'
    : Number(value) > 0
      ? 'var(--color-up)'
      : 'var(--color-down)'
const positionMargin = computed(() =>
  store.positions.reduce((sum, p) => sum + Number(p.margin_usdt ?? p.margin ?? 0), 0),
)

</script>
<template>
  <div class="metric-grid">
    <AppCard class="metric-card"
      ><div class="metric-card__label">
        <span>账户总权益</span><Wallet class="size-4 text-[var(--text-faint)]" />
      </div>
      <div class="metric-card__value num-tabular">
        {{ ready ? fmt(account?.total_eq) : '—' }}
        <span class="text-xs font-normal tracking-normal text-[var(--text-faint)]">USDT</span>
      </div>
      <div class="metric-card__footer">
        可用 {{ ready ? fmt(account?.avail_eq) : '—'
        }}<span class="ml-auto">占用 {{ ready ? `${fmt(account?.margin_usage_pct)}%` : '—' }}</span>
      </div></AppCard
    >
    <AppCard class="metric-card"
      ><div class="metric-card__label">
        <span>累计净盈亏</span><TrendingUp class="size-4 text-[var(--text-faint)]" />
      </div>
      <div class="metric-card__value num-tabular" :style="{ color: tone(account?.cum_net_pnl) }">
        {{ ready ? signed(account?.cum_net_pnl) : '—' }}
        <span class="text-xs font-normal tracking-normal text-[var(--text-faint)]">USDT</span>
      </div>
      <div class="metric-card__footer">
        基准 {{ ready ? fmt(account?.initial_capital) : '—'
        }}<span class="ml-auto">{{ ready && account?.cum_roi_pct != null ? `${signed(account.cum_roi_pct)}%` : '—' }}</span>
      </div>
      <div v-if="ready && account?.baseline_configured === false" class="metric-card__footer mt-1">
        请在控制台确认本金基准后查看累计收益
      </div>
      <div
        class="metric-card__footer mt-1"
        :title="`已结净额 ${fmt(account?.cum_realized_pnl)} / 累计费用 ${fmt(account?.cum_total_fees)}`"
      >
        已结 {{ ready ? signed(account?.cum_realized_pnl) : '—' }} · 费用
        {{ ready ? fmt(account?.cum_total_fees) : '—' }}
      </div></AppCard
    >
    <AppCard class="metric-card"
      ><div class="metric-card__label">
        <span>今日已结盈亏</span><CalendarDays class="size-4 text-[var(--text-faint)]" />
      </div>
      <div
        class="metric-card__value num-tabular"
        :style="{ color: tone(today?.net_realized ?? today?.total_pnl) }"
      >
        {{ ready ? signed(today?.net_realized ?? today?.total_pnl) : '—' }}
        <span class="text-xs font-normal tracking-normal text-[var(--text-faint)]">USDT</span>
      </div>
      <div class="metric-card__footer">
        {{
          ready
            ? `${Number(today?.win_trades || 0) + Number(today?.loss_trades || 0)} 笔平仓`
            : '暂无成交数据'
        }}<span class="ml-auto">胜率 {{ ready && today ? `${fmt(today.win_rate)}%` : '—' }}</span>
      </div>
      <div class="metric-card__footer mt-1">
        UTC+8 · 手续费 {{ ready ? fmt(today?.fees_paid ?? today?.total_fees ?? today?.fees) : '—' }}
      </div></AppCard
    >
    <AppCard class="metric-card"
      ><div class="metric-card__label">
        <span>持仓浮动盈亏</span><Layers class="size-4 text-[var(--text-faint)]" />
      </div>
      <div
        class="metric-card__value num-tabular"
        :style="{ color: tone(account?.pos_upl_total ?? account?.upl) }"
      >
        {{ ready ? signed(account?.pos_upl_total ?? account?.upl) : '—' }}
        <span class="text-xs font-normal tracking-normal text-[var(--text-faint)]">USDT</span>
      </div>
      <div class="metric-card__footer">
        {{ store.positions.length }} 个持仓<span class="ml-auto"
          >保证金 {{ ready ? fmt(positionMargin) : '—' }}</span
        >
      </div>
      <div class="metric-card__footer mt-1">
        多 {{ store.positions.filter((p) => p.side === 'long').length }} / 空
        {{ store.positions.filter((p) => p.side === 'short').length }}
      </div></AppCard
    >
  </div>
</template>
