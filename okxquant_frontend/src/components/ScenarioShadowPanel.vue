<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import { scenarioLabel, shadowStateLabel, shadowReason, shadowPrice } from '../utils/scenarioShadow'
import type { ScenarioShadowStatus } from '../utils/scenarioShadow'
defineProps<{ shadow?: ScenarioShadowStatus }>()
const timeLabel = (value: number) => new Date(value).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
</script>

<template>
  <AppCard v-if="shadow?.enabled" data-scenario-shadow class="min-w-0 max-w-full rounded-xl border p-3 sm:p-4 space-y-3" style="background: var(--bg-card); border-color: var(--border-subtle)">
    <header class="flex flex-wrap items-center justify-between gap-2">
      <h3 class="text-sm font-semibold" style="color: var(--text-main)">入场候选 · 影子研究</h3>
      <span class="rounded-md px-2 py-1 text-xs" style="background: var(--color-brand-bg); color: var(--color-brand)">仅研究 · 不下单</span>
    </header>
    <p class="text-xs leading-relaxed break-words" style="color: var(--text-muted)">{{ shadow.message || '候选触发不代表已获得模型认可、账户额度或交易授权。' }}</p>
    <p v-if="shadow.stale" role="status" class="text-xs" style="color: var(--color-warn)">监测记录较旧，不能将历史触发视为当前入场信号。</p>
    <div v-if="shadow.frames" class="flex flex-wrap gap-2 text-xs" style="color: var(--text-muted)">
      <span v-for="(frame, inst) in shadow.frames" :key="inst" class="min-w-0 break-words">{{ String(inst).split('-')[0] }} · {{ shadowStateLabel(frame.status) }}</span>
    </div>
    <div class="grid min-w-0 grid-cols-1 items-start gap-2 xl:grid-cols-2">
      <details v-for="row in shadow.candidates || []" :key="row.id" :data-scenario-candidate="row.id" class="min-w-0 self-start rounded-lg border p-2" style="border-color: var(--border-subtle); background: var(--bg-card-subtle)">
        <summary class="min-h-11 cursor-pointer text-xs leading-relaxed break-words" style="color: var(--text-main)">
          {{ row.definition.instrument.split('-')[0] }} · {{ scenarioLabel(row.definition.scenario) }} · {{ row.definition.side === 'long' ? '多' : '空' }} · {{ shadowStateLabel(row.display_status || row.status) }}
        </summary>
        <div class="mt-2 space-y-2 text-xs leading-relaxed" style="color: var(--text-muted); overflow-wrap: anywhere">
          <p>{{ shadowReason(row.reason) }}</p>
          <dl class="grid grid-cols-1 sm:grid-cols-3 gap-2">
            <div class="min-w-0"><dt>冻结触发位</dt><dd>{{ shadowPrice(row.definition.trigger) }}</dd></div>
            <div class="min-w-0"><dt>结构失效位</dt><dd>{{ shadowPrice(row.definition.stop) }}</dd></div>
            <div class="min-w-0"><dt>研究目标位</dt><dd>{{ shadowPrice(row.definition.target) }}</dd></div>
          </dl>
          <p>建立 {{ timeLabel(row.definition.created_at_ms) }} · 到期 {{ timeLabel(row.definition.expires_at_ms) }}</p>
          <p>候选编号：{{ row.id }}</p>
          <p>以上价格是研究假设，不是已提交订单或已部署的止损保护。</p>
        </div>
      </details>
    </div>
    <p v-if="!shadow.candidates?.length" class="text-xs" style="color: var(--text-muted)">当前没有可展示的候选；检查数据状态与环境限制，不强制生成交易。</p>
  </AppCard>
</template>
