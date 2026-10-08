<script setup lang="ts">
import { computed, ref, onMounted, onUnmounted, watch } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { useDashboardStore } from '../stores/dashboard'
import { useAuthStore } from '../stores/auth'
import { monitorConnectionLabel } from '../utils/dashboardHealth'
import { frontTabs, findFrontTab } from '../config/navigation'
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
const auth = useAuthStore()
const layoutMode = ref<'dual' | 'stacked'>('dual')

const currentTab = computed(() => {
  const meta = route.meta.tab as string | undefined
  if (meta === 'decisions' || meta === 'factors' || route.path === '/decisions' || route.path === '/factors') return 'decisions'
  if (meta === 'market-intelligence' || meta === 'news' || route.path === '/market-intelligence' || route.path === '/news') return 'market-intelligence'
  if (meta === 'reviews' || meta === 'lab' || route.path === '/reviews' || route.path === '/lab') return 'reviews'
  if (meta === 'trades' || meta === 'history' || route.path === '/trades' || route.path === '/history') return 'trades'
  return 'overview'
})

const activeTabMeta = computed(() => findFrontTab(currentTab.value) || frontTabs[0]!)
const heading = computed(() => [activeTabMeta.value.label, activeTabMeta.value.description] as const)

const monitorLabel = computed(() => monitorConnectionLabel(store.data, store.error, store.isStale))
const monitorTone = computed(() => monitorLabel.value === '数据已更新' ? 'success' : 'warning')

function syncTabFromRoute() {
  const tab = currentTab.value
  if (tab === 'overview') store.activeTab = 'trading'
  else if (tab === 'decisions') store.activeTab = 'factors'
  else if (tab === 'market-intelligence') store.activeTab = 'news'
  else if (tab === 'reviews') store.activeTab = 'lab'
  else if (tab === 'trades') store.activeTab = 'history'
}

watch(() => route.path, syncTabFromRoute)

watch(
  () => store.activeTab,
  (tab) => {
    let targetPath = '/'
    if (tab === 'factors' || tab === ('decisions' as any)) targetPath = '/decisions'
    else if (tab === 'news' || tab === ('market-intelligence' as any)) targetPath = '/market-intelligence'
    else if (tab === 'lab' || tab === ('reviews' as any)) targetPath = '/reviews'
    else if (tab === 'history' || tab === ('trades' as any)) targetPath = '/trades'

    if (route.path !== targetPath && !route.path.startsWith('/admin') && !route.path.startsWith('/docs')) {
      router.replace(targetPath)
    }
  },
)

onMounted(() => {
  syncTabFromRoute()
  store.startPolling(10000)
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
            :tone="monitorTone"
            dot
            >{{ monitorLabel }}</AppBadge
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
            v-if="currentTab === 'overview'"
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

      <!-- Overview tab (lazy mount: unmounted when other tabs active) -->
      <div
        v-if="currentTab === 'overview'"
        class="terminal-overview"
        :class="{ 'terminal-overview--dual': layoutMode === 'dual' }"
      >
        <div class="terminal-overview__left"><TopHudRibbon /><MarketCandles :active="currentTab === 'overview' && (route.path === '/' || route.path === '/trading')" /><TacticalDesk /></div>
        <InstrumentMatrix />
      </div>

      <!-- AI Decisions tab (lazy mount) -->
      <div v-if="currentTab === 'decisions'" class="terminal-grid">
        <AiBrainHistory />
      </div>

      <!-- Market Intelligence tab (lazy mount) -->
      <div v-if="currentTab === 'market-intelligence'" class="terminal-grid">
        <NewsIntelligence />
      </div>

      <!-- Strategy Review tab (lazy mount) -->
      <div v-if="currentTab === 'reviews'" class="terminal-grid">
        <SelfEvolutionLab />
      </div>

      <!-- Trades tab (lazy mount) -->
      <div v-if="currentTab === 'trades'" class="terminal-grid">
        <TradesLedger />
        <details v-if="auth.isAuthenticated" class="action-disclosure rounded-xl border p-4 text-xs font-mono" style="background:var(--bg-card);border-color:var(--border-subtle)">
          <summary class="cursor-pointer font-bold select-none" style="color:var(--text-main)">
            高级运行诊断日志 (已认证管理员可见)
          </summary>
          <div class="mt-3 pt-3 border-t" style="border-color:var(--border-subtle)">
            <LedgerLogs />
          </div>
        </details>
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
