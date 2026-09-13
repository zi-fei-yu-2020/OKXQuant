import { resolveMacroAnalysis, macroStatusLabel } from '../utils/macroAnalysis'
import { defineStore } from 'pinia'
import { dashboardIsStale } from '../utils/dashboardHealth'
import { instrumentSupport } from '../utils/instrumentSupport'
import { createSingleFlight } from '../utils/singleFlight'
import { ref, shallowRef, computed } from 'vue'
import { shareSnapshot } from '../utils/dashboardSnapshot'
import { observedNumber } from '../utils/observationDisplay'
import type { DashboardResponse, InstrumentFactor, PositionItem, PendingOrderItem } from '../types/dashboard'

export const useDashboardStore = defineStore('dashboard', () => {
  const activeTab = ref<'trading' | 'factors' | 'news' | 'lab' | 'history'>('trading')
  const data = shallowRef<DashboardResponse | null>(null)
  const loading = ref<boolean>(false)
  const isRefreshing = ref<boolean>(false)
  const error = ref<string | null>(null)
  const lastUpdated = ref<Date | null>(null)
  const isConnected = ref<boolean>(true)
  const pollingTimer = ref<any>(null)
  const showAboutModal = ref<boolean>(false)
  // Getters
  const account = computed(() => data.value?.account || null)
  const positions = computed<PositionItem[]>(() => data.value?.positions_summary?.items || [])
  const pendingOrders = computed<PendingOrderItem[]>(() => data.value?.pending_orders || [])
  const factors = computed<InstrumentFactor[]>(() => {
    const rawFactors = data.value?.factors || []
    const libInstruments: any[] = (data.value as any)?.factor_library?.instruments || (data.value as any)?.factor_library_snapshot?.instruments || []
    const libMap = new Map<string, any>()
    for (const li of libInstruments) {
      if (li?.instId) libMap.set(li.instId, li)
    }
    return rawFactors.map((f: any) => {
      const lib = libMap.get(f.instId) || {}
      const calc = lib.calculus_dynamics || {}
      const sm = lib.smart_money_derivatives || f.smart_money || {}
      const trend = lib.trend_momentum || {}
      return {
        ...f,
        environment_support: instrumentSupport(f.instId, data.value?.instrument_support, data.value?.okx_environment),
        adx_1h: f.adx_1h ?? trend.adx_1h,
        calculus: {
          velocity_1h: calc.velocity,
          accel_1h: calc.acceleration,
          jerk_1h: calc.jerk,
          impulse_1h: calc.impulse,
          energy_1h: (lib.definite_integrals || {}).energy_integral,
          action_area_1h: (lib.definite_integrals || {}).deviation_area_integral,
          state_1h: calc.regime,
        },
        smart_money: {
          weighted_long_pct: sm.weighted_long_pct ?? f.smart_money?.weighted_long_pct,
          net_flow_usdt: sm.smart_money_flow_usd ?? f.smart_money?.net_flow_usdt,
          top_win_rate: sm.top_win_rate,
        },
        decision: f.decision || {
          action: f.action,
          confidence: f.confidence,
          leverage: f.leverage,
          margin_usdt: f.margin_usdt,
          entry_price: f.entry_price,
          take_profit_price: f.take_profit_price,
          stop_loss_price: f.stop_loss_price,
          risk_reward_ratio: f.risk_reward_ratio || f.rr_ratio,
          summary_reason: f.decision?.summary_reason || f.reason,
        },
      }
    })
  })
  const macroAnalysis = computed(() => resolveMacroAnalysis(data.value))
  const macroAssessment = computed(() => macroAnalysis.value.text || macroAnalysis.value.message)
  const macroLabel = computed(() => macroStatusLabel(macroAnalysis.value.status))
  const llmRuntime = computed(() => data.value?.llm_runtime || {
    model: 'gemini-3.8-flash-high',
    provider_name: 'Google Gemini',
    reasoning_effort: 'high',
    api_format: 'openai_chat',
  })
  const logs = computed(() => data.value?.logs || [])
  const isStale = computed(() => !isConnected.value || dashboardIsStale(data.value))

  // Actions
  const sharedFetch = createSingleFlight<void>()
  let refreshGeneration = 0
  let activeController: AbortController | null = null
  let manualRequests = 0
  async function fetchDashboard(silent = false) {
    const generation = refreshGeneration
    if (!silent) { manualRequests += 1; isRefreshing.value = true }
    try { await sharedFetch('monitoring:' + generation, () => refreshDashboard(generation)) }
    finally { if (!silent) { manualRequests -= 1; isRefreshing.value = manualRequests > 0 } }
  }

  async function refreshDashboard(generation: number) {
    if (generation !== refreshGeneration) return
    const controller = new AbortController()
    activeController = controller
    const timeout = setTimeout(() => controller.abort(), 8000)
    try {
      const resp = await fetch(`/api/all?_t=${Date.now()}`, {
        signal: controller.signal,
        headers: {
          'Accept': 'application/json',
        },
      })
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}: ${resp.statusText}`)
      }
      const json: DashboardResponse = await resp.json()
      if (generation !== refreshGeneration) return
      if (!json || typeof json !== 'object' || Array.isArray(json) || !json.account || typeof json.account !== 'object' || Array.isArray(json.account)) {
        throw new Error('Invalid dashboard snapshot')
      }
      const previous = data.value
      const sameScope = !!json.account_source_id && previous?.account_source_id === json.account_source_id
      const retain = sameScope && observedNumber(previous?.account?.total_eq) !== null &&
        (json.initializing === true || observedNumber(json.account.total_eq) === null)
      const next = retain ? { ...previous!, initializing: true, is_stale: true,
        data_health: { ...json.data_health, status: 'STALE' as const, partial: true } } : json
      data.value = sameScope ? shareSnapshot(previous, next) : next
      if (!retain) lastUpdated.value = new Date()
      isConnected.value = true
      error.value = null
    } catch (err: any) {
      if (generation !== refreshGeneration) return
      console.error('[DashboardStore] fetch failed:', err)
      error.value = err.message || '获取数据失败'
      isConnected.value = false
    } finally {
      clearTimeout(timeout)
      if (generation === refreshGeneration) loading.value = false
      if (activeController === controller) activeController = null
    }
  }

  function onVisible() {
    if (document.visibilityState === 'visible') void fetchDashboard(true)
  }

  function startPolling(intervalMs = 3000) {
    stopPolling()
    void fetchDashboard(true)
    document.addEventListener('visibilitychange', onVisible)
    pollingTimer.value = setInterval(() => {
      if (document.visibilityState === 'visible') void fetchDashboard(true)
    }, intervalMs)
  }

  function stopPolling() {
    refreshGeneration += 1
    activeController?.abort()
    activeController = null
    document.removeEventListener('visibilitychange', onVisible)
    if (pollingTimer.value) {
      clearInterval(pollingTimer.value)
      pollingTimer.value = null
    }
  }

  return {
    activeTab,
    data,
    loading,
    isRefreshing,
    error,
    lastUpdated,
    isConnected,
    account,
    positions,
    pendingOrders,
    factors,
    macroAssessment,
    macroAnalysis,
    macroLabel,
    llmRuntime,
    logs,
    isStale,
    showAboutModal,
    fetchDashboard,
    startPolling,
    stopPolling,
  }
})
