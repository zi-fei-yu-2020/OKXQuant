<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import AppDialog from './ui/AppDialog.vue'
import AppButton from './ui/AppButton.vue'
import AppTable from './ui/AppTable.vue'
import { isSettlementPending } from '../utils/tradeSettlement'
import { tradeDuration } from '../utils/tradeDuration'
import { observedNumber } from '../utils/observationDisplay'
import { feeAccounting, feeText, ledgerValue, ledgerNumberText, ledgerNumberColor } from '../utils/feeAccounting'

import { ref, computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { Receipt, Search } from 'lucide-vue-next'

const store = useDashboardStore()
const filter = ref<'all' | 'active' | 'closed'>('all')
const keyword = ref('')
const feeTrade = ref<any>(null)
const feeDialogOpen = ref(false)
function showFees(trade: any) { feeTrade.value = trade; feeDialogOpen.value = true }

const trades = computed(() => {
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

const holdingCount = computed(
  () => (store.data?.trades || []).filter((t: any) => t.status === 'holding').length,
)
const closedCount = computed(
  () => (store.data?.trades || []).filter((t: any) => t.status !== 'holding').length,
)

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

function horizonLabel(v: unknown): string {
  return v === 'scalp' ? '短线' : v === 'swing' ? '波段' : '未知'
}
</script>

<template>
  <div class="trade-ledger space-y-3.5 min-w-0 max-w-full">
    <!-- Header -->
    <AppCard
      class="rounded-xl border p-4 sm:p-5 flex flex-wrap items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
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
            完整成交台账与生命周期履历
          </h2>
          <p class="text-xs font-mono mt-0.5" style="color: var(--text-muted)">
            以交易所已结算净额为准；缺失金额保留未知，费用可单独核对
          </p>
        </div>
      </div>
      <div class="text-xs font-mono" style="color: var(--text-muted)">
        持仓 <strong style="color: var(--color-brand)">{{ holdingCount }}</strong> · 已平仓
        <strong style="color: var(--text-main)">{{ closedCount }}</strong>
      </div>
    </AppCard>

    <!-- Filter bar -->
    <AppCard
      class="rounded-xl border p-2.5 sm:p-3 flex flex-wrap items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        class="flex rounded-lg border p-0.5 font-mono text-xs"
        style="background-color: var(--bg-badge); border-color: var(--border-subtle)"
      >
        <button
          @click="filter = 'all'"
          class="px-3 py-1.5 rounded-md cursor-pointer transition font-medium"
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
          class="px-3 py-1.5 rounded-md cursor-pointer transition font-medium"
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
          class="px-3 py-1.5 rounded-md cursor-pointer transition font-medium"
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
        class="flex items-center space-x-1.5 flex-1 min-w-[180px] max-w-[320px] rounded-lg border px-3 py-1.5"
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
    <AppCard
      class="rounded-xl border shadow-xs transition-colors overflow-hidden flex flex-col"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        v-if="trades.length === 0"
        class="py-12 text-center text-xs font-mono"
        style="color: var(--text-muted)"
      >
        无匹配交易台账记录
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
          <tbody>
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
                  {{ t.side }} {{ t.lever || '3x' }}
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
                  {{ clean(t.strategy, '观望') }}
                </span>
              </td>
              <td class="py-3 px-3 text-xs" style="color: var(--text-muted)">
                <span class="px-2 py-0.5 rounded border" style="border-color: var(--border-subtle); background: var(--bg-badge)">{{ horizonLabel(t.horizon) }}</span>
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

      <!-- Table Footer Summary -->
      <div
        class="px-4 py-2.5 border-t flex items-center justify-between text-[11px] font-mono shrink-0"
        style="
          border-color: var(--border-subtle);
          background-color: var(--bg-card-subtle);
          color: var(--text-faint);
        "
      >
        <span>已载入 {{ trades.length }} 笔真实撮合成交记录</span>
        <span class="hidden sm:inline">OKX 当前账户历史履历</span>
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
      <template #footer><AppButton @click="feeDialogOpen=false">关闭</AppButton></template>
    </AppDialog>
  </div>
</template>

<style scoped>
.trade-ledger__fee-column { width: 7.5rem; white-space: nowrap; }
.trade-ledger__fee-button { min-height: 2.75rem; margin-inline: auto; gap: 0.375rem; color: var(--text-muted); border-color: var(--border-subtle); background: var(--bg-card-subtle); font-family: inherit; font-weight: 500; }
.trade-ledger__fee-button:hover { color: var(--text-main); border-color: var(--border-medium); background: var(--bg-card-hover); }
.trade-ledger__strategy { display: inline-block; max-width: 15rem; white-space: normal; overflow-wrap: anywhere; }
.trade-ledger__reason { display: inline-block; max-width: 22rem; white-space: normal; overflow-wrap: anywhere; vertical-align: middle; }
</style>
