<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import { poolStatusLabel, poolMoney } from '../utils/capitalPool'
import type { CapitalPoolStatus } from '../utils/capitalPool'
defineProps<{ pool?: CapitalPoolStatus }>()
</script>

<template>
  <AppCard v-if="pool?.enabled" class="min-w-0 max-w-full rounded-xl border p-3 sm:p-4 space-y-2"
    style="background: var(--bg-card); border-color: var(--border-subtle)">
    <header class="flex flex-wrap items-center justify-between gap-2">
      <h3 class="text-sm font-semibold" style="color: var(--text-main)">虚拟策略资金池</h3>
      <span class="text-xs" style="color: var(--text-muted)">{{ poolStatusLabel(pool.status) }}{{ pool.stale ? ' · 额度核验记录较旧' : '' }}</span>
    </header>
    <dl class="grid min-w-0 grid-cols-2 gap-3 lg:grid-cols-4 text-xs">
      <div><dt style="color: var(--text-muted)">配置额度上限</dt><dd class="mt-1 font-semibold" style="color: var(--text-main)">{{ poolMoney(pool.configured_cap) }} U</dd></div>
      <div><dt style="color: var(--text-muted)">资金池盯市净值</dt><dd class="mt-1 font-semibold" style="color: var(--text-main)">{{ poolMoney(pool.pool_nav) }} U</dd></div>
      <div><dt style="color: var(--text-muted)">本轮风控基数</dt><dd class="mt-1 font-semibold" style="color: var(--text-main)">{{ poolMoney(pool.risk_equity) }} U</dd></div>
      <div><dt style="color: var(--text-muted)">交易所USDT权益</dt><dd class="mt-1 font-semibold" style="color: var(--text-main)">{{ poolMoney(pool.account_equity) }} U</dd></div>
    </dl>
    <p v-if="pool.message" role="status" class="text-xs break-words" style="color: var(--text-main)">{{ pool.message }}</p>
    <p class="text-[11px] leading-relaxed break-words" style="color: var(--text-muted)">
      这不是额外现金或交易所隔离钱包，也不是最大亏损保证。持仓和入场挂单共用额度；全仓模式仍可能影响池外权益。
      <span v-if="pool.max_active_instruments">最多同时占用 {{ pool.max_active_instruments }} 个标的名额。</span>
    </p>
  </AppCard>
</template>
