<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDashboardStore } from '../stores/dashboard'
import { observedNumber } from '../utils/observationDisplay'
import HeaderBar from '../components/HeaderBar.vue'
import TopHudRibbon from '../components/TopHudRibbon.vue'
import TacticalDesk from '../components/TacticalDesk.vue'
import MarketCandles from '../components/MarketCandles.vue'
import InstrumentMatrix from '../components/InstrumentMatrix.vue'
import LedgerLogs from '../components/LedgerLogs.vue'
import NewsIntelligence from '../components/NewsIntelligence.vue'
import SelfEvolutionLab from '../components/SelfEvolutionLab.vue'
import TradesLedger from '../components/TradesLedger.vue'
import AiBrainHistory from '../components/AiBrainHistory.vue'
import AboutModal from '../components/AboutModal.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import AppBadge from '../components/ui/AppBadge.vue'
import { Columns2, Rows2, RefreshCw } from 'lucide-vue-next'
const router = useRouter()
const route = useRoute()
const store = useDashboardStore()
const layoutMode = ref<'dual' | 'stacked'>('dual')
const descriptions = {
  trading: ['交易概览', '账户、持仓与市场信号，在一个视图中保持同步。'],
  factors: ['AI 决策', '回溯模型判断与投委会讨论，了解每一轮策略的依据。'],
  news: ['市场情报', '关注影响市场的新闻、情绪与资金动向。'],
  lab: ['策略复盘', '查看交易复盘与长期记忆，追踪策略的持续演进。'],
  history: ['交易记录', '查阅订单生命周期、历史成交与执行日志。'],
} as const
const heading = computed(() => descriptions[store.activeTab])
const hasAccount = computed(() => observedNumber(store.data?.account?.total_eq) !== null)
function syncTabFromRoute() {
  const tab = route.meta.tab
  if (typeof tab === 'string' && tab in descriptions)
    store.activeTab = tab as keyof typeof descriptions
  else if (route.path === '/') store.activeTab = 'trading'
}
watch(() => route.path, syncTabFromRoute)
watch(
  () => store.activeTab,
  (tab) => {
    const path = tab === 'trading' ? '/' : `/${tab}`
    if (route.path !== path && !route.path.startsWith('/admin') && !route.path.startsWith('/docs'))
      router.replace(path)
  },
)
onMounted(() => {
  syncTabFromRoute()
  store.startPolling(3000)
  try {
    layoutMode.value = localStorage.getItem('okxquant_dashboard_layout_v2') === 'stacked' ? 'stacked' : 'dual'
  } catch {
    /* optional preference */
  }
})
onUnmounted(() => store.stopPolling())
function setLayout(mode: 'dual' | 'stacked') {
  layoutMode.value = mode
  try {
    localStorage.setItem('okxquant_dashboard_layout_v2', mode)
  } catch {
    /* optional preference */
  }
}
</script>
<template>
  <div class="terminal-shell">
    <HeaderBar />
    <main class="terminal-main">
      <PageHeader :title="heading[0]" :description="heading[1]" eyebrow="工作空间 / 监控终端"
        ><template #actions>
          <AppBadge v-if="store.data?.okx_environment" :tone="store.data.okx_environment === 'demo' ? 'neutral' : 'warning'">
            {{ store.data.okx_environment === 'demo' ? 'OKX 模拟盘' : 'OKX 实盘' }}
          </AppBadge>
          <AppBadge
            data-monitor-connection
            class="min-w-[7.5rem] justify-center"
            :tone="store.error || !hasAccount || store.isStale ? 'warning' : 'success'"
            dot
            >{{ store.error || !hasAccount || store.isStale ? '数据更新延迟' : '数据已更新' }}</AppBadge
          ><button
            v-if="store.error || (store.data && store.isStale)"
            class="ui-icon-button"
            :disabled="store.isRefreshing"
            aria-label="重试获取监控数据"
            title="重试获取监控数据"
            @click="store.fetchDashboard()"
          >
            <RefreshCw class="size-4" :class="{ 'animate-spin': store.isRefreshing }" />
          </button>
          <div
            v-if="store.activeTab === 'trading'"
            class="hidden lg:flex border rounded-lg p-0.5 bg-[var(--bg-card)]"
          >
            <button
              class="ui-icon-button"
              :style="{ color: layoutMode === 'stacked' ? 'var(--color-brand)' : undefined }"
              :aria-pressed="layoutMode === 'stacked'"
              aria-label="切换纵向布局"
              @click="setLayout('stacked')"
            >
              <Rows2 class="size-4" /></button
            ><button
              class="ui-icon-button"
              :style="{ color: layoutMode === 'dual' ? 'var(--color-brand)' : undefined }"
              :aria-pressed="layoutMode === 'dual'"
              aria-label="切换分栏布局"
              @click="setLayout('dual')"
            >
              <Columns2 class="size-4" />
            </button></div></template
      ></PageHeader>
      <div
        v-show="store.activeTab === 'trading'"
        class="terminal-overview"
        :class="{ 'terminal-overview--dual': layoutMode === 'dual' }"
      >
        <div class="terminal-overview__left"><TopHudRibbon /><MarketCandles :active="store.activeTab === 'trading' && (route.path === '/' || route.path === '/trading')" /><TacticalDesk /></div>
        <InstrumentMatrix />
      </div>
      <div v-show="store.activeTab === 'factors'" class="terminal-grid"><AiBrainHistory /></div>
      <div v-show="store.activeTab === 'news'" class="terminal-grid"><NewsIntelligence /></div>
      <div v-show="store.activeTab === 'lab'" class="terminal-grid"><SelfEvolutionLab /></div>
      <div v-show="store.activeTab === 'history'" class="terminal-grid">
        <TradesLedger /><LedgerLogs />
      </div>
    </main>
    <footer class="terminal-footer">
      <button @click="store.showAboutModal = true">OKXQuant · v0.1.0</button
      ><span class="ml-4 hidden sm:inline">只读监控 · 交易有风险，决策需审慎</span>
    </footer>
    <AboutModal
      :visible="store.showAboutModal"
      @close="store.showAboutModal = false"
    />
  </div>
</template>
