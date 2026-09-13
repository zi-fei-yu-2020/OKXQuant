<script setup lang="ts">
import AppCard from './ui/AppCard.vue'
import StrategyTelemetryPanel from './StrategyTelemetryPanel.vue'
import CapitalPoolPanel from './CapitalPoolPanel.vue'
import ScenarioShadowPanel from './ScenarioShadowPanel.vue'
import { auditLabel } from '../utils/waitAudit'
import InstrumentSupportNotice from './InstrumentSupportNotice.vue'
import { canOpen } from '../utils/instrumentSupport'
import { observedNumber, observedText, observedPercent, observationColor, smartMoneyDisplay } from '../utils/observationDisplay'
import EmptyState from './ui/EmptyState.vue'

import { ref, computed } from 'vue'
import { useDashboardStore } from '../stores/dashboard'
import { TrendingUp, TrendingDown, ArrowUpRight, Compass, Activity } from 'lucide-vue-next'
import FactorDetailModal from './FactorDetailModal.vue'

const store = useDashboardStore()
// The store's factor-library merge drops valid and can replace observations with defaults.
// Read smart money from the same instrument in the raw response, not the merged fallback.
const displayFactors = computed(() => {
  const raw = new Map((store.data?.factors || []).map(item => [item.instId, item.smart_money]))
  return store.factors.map(item => ({
    ...item,
    smartMoneyDisplay: smartMoneyDisplay(raw.get(item.instId), canOpen(item.environment_support)),
  }))
})
const selectedInstrument = ref<any | null>(null)
const drawerVisible = ref(false)
const detailInstrument = computed(() => store.factors.find(item => item.instId === selectedInstrument.value?.instId) || selectedInstrument.value)

function openDetail(item: any) {
  selectedInstrument.value = item
  drawerVisible.value = true
}

function getActionStyle(action?: string) {
  if (action === 'BUY_LONG') {
    return {
      backgroundColor: 'var(--color-up-bg)',
      borderColor: 'var(--color-up-border)',
      color: 'var(--color-up)',
    }
  }
  if (action === 'SELL_SHORT') {
    return {
      backgroundColor: 'var(--color-down-bg)',
      borderColor: 'var(--color-down-border)',
      color: 'var(--color-down)',
    }
  }
  return {
    backgroundColor: 'var(--bg-badge)',
    borderColor: 'var(--border-subtle)',
    color: 'var(--text-muted)',
  }
}

function getActionLabel(action?: string, status?: string) {
  if (action === 'BUY_LONG') return '开多候选 BUY'
  if (action === 'SELL_SHORT') return '顺势做空 SELL'
  return status ? auditLabel(status) : '等待 · 未审计'
}
</script>

<template>
  <div class="space-y-3">
    <CapitalPoolPanel :pool="store.data?.capital_pool" />
    <ScenarioShadowPanel :shadow="store.data?.scenario_shadow" />
    <StrategyTelemetryPanel />
    <!-- Macro Summary Telemetry Strip -->
    <AppCard
      class="rounded-xl border p-3 sm:p-3.5 flex items-start space-x-2.5 transition-colors shadow-xs"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        class="w-6 h-6 rounded-md flex items-center justify-center border shrink-0 mt-0.5"
        style="
          background-color: var(--color-brand-bg);
          border-color: var(--color-brand-border);
          color: var(--color-brand);
        "
      >
        <Compass class="w-3.5 h-3.5" />
      </div>
      <div class="flex-1 min-w-0">
        <div class="flex flex-wrap items-center justify-between gap-x-2 gap-y-1">
          <span
            class="text-[11px] font-bold font-mono uppercase tracking-wider"
            style="color: var(--color-brand)"
          >
            宏观多周期推演基调
          </span>
          <span class="text-[10px] font-mono" style="color: var(--text-faint)">
            {{ store.macroLabel }} · {{ store.macroAnalysis.analyzed_at ? store.macroAnalysis.analyzed_at.slice(11, 19) : '尚未生成' }}
          </span>
        </div>
        <p
          class="text-[11px] font-sans mt-0.5 leading-relaxed break-words"
          role="status" aria-live="polite" aria-atomic="true"
          style="color: var(--text-muted)"
          :title="store.macroAssessment"
        >
          {{ store.macroAssessment }}
        </p>
        <p v-if="store.macroAnalysis.text && store.macroAnalysis.message" class="text-[10px] mt-1 break-words" style="color: var(--text-faint)">
          {{ store.macroAnalysis.message }}
        </p>
      </div>
    </AppCard>

    <!-- Section Title -->
    <div class="flex items-center justify-between px-1">
      <div class="flex items-center space-x-2">
        <Activity class="w-3.5 h-3.5" style="color: var(--color-brand)" />
        <h2
          class="text-xs font-mono font-black uppercase tracking-wider"
          style="color: var(--text-main)"
        >
          {{
            store.factors.length
              ? `${store.factors.length} 标的因果动力学与微结构雷达`
              : '动态资产池因果动力学与微结构雷达'
          }}
        </h2>
      </div>
      <span class="text-[10px] font-mono" style="color: var(--text-faint)">
        点击卡片下钻微积分推演
      </span>
    </div>

    <AppCard v-if="!store.factors.length"
      ><EmptyState
        title="暂无市场信号"
        description="行情采集与策略决策就绪后，标的卡片会在这里自动更新。"
    /></AppCard>

    <!-- 6-Asset Quantitative Ticker Cards Grid (adaptive 1 col on mobile, 2 cols on tablet, 3 cols on desktop, 6 cols on ultra-wide) -->
    <div v-else class="factor-grid grid grid-cols-1 sm:grid-cols-2 xl:grid-cols-3 gap-4">
      <AppCard
        v-for="item in displayFactors"
        :key="item.instId"
        @click="openDetail(item)"
        role="button"
        tabindex="0"
        :aria-label="`查看 ${item.name} 的市场信号`"
        @keydown.enter="openDetail(item)"
        @keydown.space.prevent="openDetail(item)"
        class="rounded-xl border p-3.5 transition-all duration-150 flex flex-col justify-between cursor-pointer group shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        :class="'hover:border-[var(--border-medium)] hover:bg-[var(--bg-card-hover)]'"
      >
        <!-- Top: Header Info -->
        <div>
          <div
            class="flex items-center justify-between pb-2 border-b"
            style="border-color: var(--border-subtle)"
          >
            <div class="flex items-center space-x-1.5">
              <span
                class="font-mono font-black text-sm tracking-wide"
                style="color: var(--text-main)"
              >
                {{ item.name }}
              </span>
              <span
                class="text-[9px] font-mono px-1 py-0.2 rounded border"
                style="
                  background-color: var(--bg-badge);
                  border-color: var(--border-subtle);
                  color: var(--text-faint);
                "
              >
                SWAP
              </span>
            </div>
            <div class="text-right font-mono">
              <div class="text-xs font-black num-tabular" style="color: var(--text-main)">
                {{ observedNumber(item.price) === null ? '--' : `$${observedText(item.price)}` }}
              </div>
              <div
                class="text-[10px] font-bold font-mono flex items-center justify-end space-x-0.5 num-tabular"
                :style="{ color: observationColor(item.chg24h) }"
              >
                <TrendingUp v-if="observedNumber(item.chg24h) !== null && observedNumber(item.chg24h)! >= 0" class="w-2.5 h-2.5" />
                <TrendingDown v-else-if="observedNumber(item.chg24h) !== null" class="w-2.5 h-2.5" />
                <span>{{ observedPercent(item.chg24h, true) }}</span>
              </div>
            </div>
          </div>

          <InstrumentSupportNotice class="mt-2" :support="item.environment_support" compact />
          <!-- Calculus Telemetry Grid -->
          <div
            class="grid grid-cols-4 gap-1 my-2 py-1.5 px-2 rounded-lg border text-[10px] font-mono"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint)">速度 v</div>
              <div
                class="font-bold num-tabular truncate"
                :style="{ color: observationColor(item.calculus?.velocity_1h) }"
              >
                {{ observedText(item.calculus?.velocity_1h) }}
              </div>
            </div>
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint)">加速 a</div>
              <div class="font-bold num-tabular truncate" style="color: var(--text-main)">
                {{ observedText(item.calculus?.accel_1h) }}
              </div>
            </div>
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint)">冲击 j</div>
              <div class="font-bold num-tabular truncate" style="color: var(--text-muted)">
                {{ observedText(item.calculus?.jerk_1h) }}
              </div>
            </div>
            <div>
              <div class="text-[8px] uppercase" style="color: var(--text-faint)">ADX</div>
              <div class="font-bold num-tabular truncate" style="color: var(--color-brand)">
                {{ canOpen(item.environment_support) ? observedText(item.adx_1h) : '--' }}
              </div>
            </div>
          </div>

          <!-- Microstructure Flow -->
          <div
            class="flex flex-wrap gap-x-2 gap-y-1 items-center justify-between text-[10px] font-mono mb-2 px-0.5"
            :title="item.smartMoneyDisplay.unavailable ? '未取得数据：本轮资讯源未返回可用观测，横线不代表中性或零敞口' : '官方聪明钱聚合；净敞口为多头名义金额减空头名义金额，不是24小时资金净流入'"
            style="color: var(--text-muted)"
          >
            <span
              >聪明钱:
              <strong class="num-tabular" style="color: var(--text-main)"
                >{{ item.smartMoneyDisplay.long }}</strong
              ></span
            >
            <span
              >净敞口:
              <strong class="num-tabular" style="color: var(--text-main)">{{
                item.smartMoneyDisplay.flow
              }}</strong></span
            >
          </div>
        </div>

        <!-- Bottom: Decision Status & Drawer Trigger -->
        <div class="pt-2 border-t" style="border-color: var(--border-subtle)">
          <div class="flex items-center justify-between">
            <span
              class="px-2 py-0.5 rounded text-[10px] font-bold font-mono border"
              :style="getActionStyle(canOpen(item.environment_support) ? (item.decision?.action || item.action) : 'WAIT')"
            >
              {{ canOpen(item.environment_support) ? getActionLabel(item.decision?.action || item.action, item.decision?.decision_status || item.decision_status) : '仅观察 · 不参与交易' }}
            </span>
            <div
              class="flex items-center space-x-1 text-xs font-mono font-bold"
              style="color: var(--text-muted)"
            >
              <span class="text-[10px]" style="color: var(--text-faint)">置信:</span>
              <span class="num-tabular" style="color: var(--text-main)"
                >{{ canOpen(item.environment_support) ? observedPercent(item.decision?.confidence ?? item.confidence) : '--' }}</span
              >
              <ArrowUpRight
                class="w-3.5 h-3.5 opacity-60 group-hover:opacity-100 transition-opacity"
              />
            </div>
          </div>
        </div>
      </AppCard>
    </div>

    <!-- Factor Detail Modal (Drawer) -->
    <FactorDetailModal
      v-if="drawerVisible && selectedInstrument"
      :visible="drawerVisible"
      :instrument="detailInstrument"
      :full-prompt-text="store.data?.ai_last_prompt || ''"
      @close="drawerVisible = false"
    />
  </div>
</template>
