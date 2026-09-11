<script setup lang="ts">
import { observedNumber } from '../utils/observationDisplay'
export interface EvolutionFeedback {
  settled_samples?: number
  entry_snapshot_samples?: number
  partial_snapshot_samples?: number
  fee_cost?: number | null
  rebates?: number | null
  known_fee_samples?: number
  friction_reversed_trades?: number
  memory_cohorts?: { prompt_hash: string; strategy_version: string; execution_profile_signature: string; samples: number; net_pnl: number }[]
  auto_promote?: boolean
  validation_status?: string
}
defineProps<{ feedback?: EvolutionFeedback }>()
function amount(value: unknown) {
  const n = observedNumber(value)
  return n === null ? '未完整核对' : n.toLocaleString('zh-CN', { maximumFractionDigits: 8 }) + ' U'
}
</script>
<template>
  <section class="min-w-0 rounded-lg border p-3 sm:p-4 space-y-3" style="border-color:var(--border-subtle);background:var(--bg-card-subtle)" data-evolution-evidence>
    <h4 class="font-semibold text-sm" style="color:var(--text-main)">自动证据反馈</h4>
    <template v-if="feedback">
      <dl class="grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-4 gap-3 text-xs">
        <div><dt style="color:var(--text-muted)">可关联开仓决策的样本</dt><dd class="mt-1">{{ feedback.entry_snapshot_samples ?? '--' }} / {{ feedback.settled_samples ?? '--' }} 笔</dd></div>
        <div><dt style="color:var(--text-muted)">已核对手续费支出</dt><dd class="mt-1 break-words">{{ amount(feedback.fee_cost) }}</dd></div>
        <div><dt style="color:var(--text-muted)">已核对返佣</dt><dd class="mt-1 break-words">{{ amount(feedback.rebates) }}</dd></div>
        <div><dt style="color:var(--text-muted)">毛利为正、净额不为正</dt><dd class="mt-1">{{ feedback.friction_reversed_trades ?? '--' }} 笔</dd></div>
      </dl>
      <p class="text-xs leading-relaxed" style="color:var(--text-muted)">由程序核对同账户成交与原始决策，不用当前行情补写历史指标。部分关联 {{ feedback.partial_snapshot_samples ?? '--' }} 笔；未关联样本仍可统计已结算盈亏，但不能据此归因开仓指标。</p>
      <details v-if="feedback.memory_cohorts?.length" class="action-disclosure min-w-0 text-xs">
        <summary class="min-h-11 flex items-center cursor-pointer">按实际记忆版本追踪结果（描述性统计）</summary>
        <div class="space-y-2">
          <div v-for="row in feedback.memory_cohorts" :key="row.prompt_hash + row.strategy_version + row.execution_profile_signature" class="flex flex-wrap justify-between gap-2 rounded-md border p-2" style="border-color:var(--border-subtle)">
            <span class="min-w-0 break-all" :title="'记忆 ' + row.prompt_hash + ' · 代码 ' + row.strategy_version + ' · 执行 ' + row.execution_profile_signature">记忆 {{ row.prompt_hash.slice(0, 10) }} · 代码 {{ row.strategy_version.slice(0, 8) }} · 执行 {{ row.execution_profile_signature.slice(0, 8) }}</span>
            <span>{{ row.samples }} 笔 · 净额 {{ amount(row.net_pnl) }}</span>
          </div>
        </div>
      </details>
      <p class="text-xs leading-relaxed" style="color:var(--text-muted)">下一步：候选单变量对照 → 前向样本验证 → 审核发布 → 持续监测与回退。这里的分组盈亏不是对照实验，不会因短期盈利自动推广心法，也不会自动增加风险预算。</p>
    </template>
    <p v-else class="text-xs leading-relaxed" style="color:var(--text-muted)">旧复盘未保存结构化证据反馈；下一次复盘将自动计算并记录，不把旧报告伪装成已验证的策略改进。</p>
  </section>
</template>
