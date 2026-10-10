<script setup lang="ts">
import AppTable from '../../components/ui/AppTable.vue'
import { useToast } from '../../composables/useFeedback'
const toast = useToast()
import AppCard from '../../components/ui/AppCard.vue'
import AppBadge from '../../components/ui/AppBadge.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppDialog from '../../components/ui/AppDialog.vue'
import AppField from '../../components/ui/AppField.vue'
import LoadingState from '../../components/ui/LoadingState.vue'
import EmptyState from '../../components/ui/EmptyState.vue'
import { APP_VERSION } from '../../config/branding'

import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import { useDialogs } from '../../composables/useDialogs'
import { overviewConnection } from '../../utils/accountConnections'
import { dataHealthSource, dataHealthLabel, isDataHealthDisabled } from '../../utils/dataHealthDisplay'
import {
  Cpu,
  Database,
  Activity,
  Server,
  ShieldCheck,
  RefreshCw,
  ArrowRight,
  FileText,
  Users,
  CheckCircle2,
  AlertCircle,
  Sliders,
  Play,
  Pause,
} from 'lucide-vue-next'

const router = useRouter()
const auth = useAuthStore()
const dialogs = useDialogs()
const { api } = useApi()

const runtime = ref<any>(null)
const loading = ref(true)
const loadFailed = ref(false)
const connection = computed(() => overviewConnection(runtime.value))
const isAccountVerified = computed(() => {
  const tc = runtime.value?.trading_connection
  return !!tc?.configured && tc?.status === 'identity_verified' && !!tc?.capabilities?.[tc?.mode]?.read_ready
})


// Runtime Controls (Automatic Trader pause/resume)
const controlsModalOpen = ref(false)
const controlsTargetAction = ref<'enable' | 'pause'>('pause')
const controlsConfirmationInput = ref('')
const controlsSubmitting = ref(false)

// Light Profile & Runtime Features
const runtimeFeatures = ref<{
  profile: 'standard' | 'light'
  features: {
    factor_snapshots: boolean
    market_observations: boolean
    entry_research: boolean
    scalp_research: boolean
    automatic_review: boolean
  }
  source?: string
  version?: number
} | null>(null)
const featuresSubmitting = ref(false)

function duration(s: number | null): string {
  if (s == null) return '--'
  if (s < 60) return `${s}s`
  if (s < 3600) return `${Math.floor(s / 60)}m`
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`
}

const formattedDecisions = computed(() => {
  const d = runtime.value?.full_decisions
  if (!d) return []
  if (Array.isArray(d)) return d
  if (typeof d === 'object') {
    return Object.entries(d).map(([k, v]: [string, any]) => ({
      instId: v.instId || k,
      action: v.decision?.action || v.action || 'WAIT',
      confidence:
        (v.decision?.confidence ?? v.confidence ?? 0) > 1
          ? (v.decision?.confidence ?? v.confidence ?? 0) / 100
          : (v.decision?.confidence ?? v.confidence ?? 0),
      timestamp: v.time_str || (v.timestamp ? String(v.timestamp) : '--'),
      reason: v.decision?.summary_reason || v.thought_process?.market_structure || v.reason || '',
    }))
  }
  return []
})

const dataHealthFiles = computed(() => {
  const dh = runtime.value?.data_health
  let files: any[] = []
  if (Array.isArray(dh)) files = dh
  else if (Array.isArray(dh?.files)) files = dh.files

  const rf = runtimeFeatures.value || runtime.value?.runtime_features
  if (!rf?.features) return files

  return files.map((f: any) => {
    const filename = f.file || f.name || ''
    let isFeatureOff = false
    if (filename.includes('factor_library_snapshot') && rf.features.factor_snapshots === false) isFeatureOff = true
    if (filename.includes('market_observation') && rf.features.market_observations === false) isFeatureOff = true
    if (filename.includes('ai_brain_decisions') && rf.features.entry_research === false && rf.features.scalp_research === false) isFeatureOff = true

    if (isFeatureOff) {
      return { ...f, status: 'disabled', data_status: 'disabled', required: false, fresh: null }
    }
    return f
  })
})

const dataHealthOverall = computed(() => {
  const dh = runtime.value?.data_health
  if (!dh) return 'UNKNOWN'
  if (typeof dh === 'object' && dh.overall) return dh.overall
  if (Array.isArray(dh)) {
    return dh.every((f: any) => f.fresh || isDataHealthDisabled(f)) ? 'LIVE' : 'STALE'
  }
  return 'UNKNOWN'
})

async function loadRuntime() {
  loadFailed.value = false
  loading.value = true
  try {
    const rt = await api('/api/v1/admin/runtime')
    if (rt) {
      runtime.value = rt
    }
    try {
      const rf = await api('/api/v1/admin/runtime-features')
      if (rf && rf.profile) {
        runtimeFeatures.value = rf
      }
    } catch {
      // runtime-features optional read
    }
    try {
      const rc = await api('/api/v1/admin/runtime-controls')
      if (rc && runtime.value) {
        runtime.value.runtime_controls = rc
      }
    } catch {
      // runtime-controls optional read
    }
  } catch (e: any) {
    if (e?.silent) return
    loadFailed.value = true
    toast.error(e.message)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadRuntime()
})

// Expected confirmation phrase for pause/resume
const expectedConfirmation = computed(() => {
  if (controlsTargetAction.value === 'pause') return 'PAUSE AUTO'
  return connection.value.mode === 'live' ? 'ENABLE LIVE AUTO' : 'ENABLE DEMO AUTO'
})

function openControlsModal(action: 'enable' | 'pause') {
  if (!auth.isSuperadmin) {
    toast.warning('仅超级管理员有权修改调度状态')
    return
  }
  controlsTargetAction.value = action
  controlsConfirmationInput.value = ''
  controlsModalOpen.value = true
}

async function submitRuntimeControls() {
  if (!auth.isSuperadmin || controlsSubmitting.value) return
  if (controlsConfirmationInput.value.trim() !== expectedConfirmation.value) {
    toast.error(`请输入完全匹配的确认短语：${expectedConfirmation.value}`)
    return
  }
  controlsSubmitting.value = true
  try {
    const payload = {
      automatic_trader: controlsTargetAction.value === 'enable',
      confirmation: controlsConfirmationInput.value.trim(),
    }
    await api('/api/v1/admin/runtime-controls', {
      method: 'PUT',
      body: JSON.stringify(payload),
    })
    controlsModalOpen.value = false
    toast.success(controlsTargetAction.value === 'enable' ? '自动决策调度已启用' : '新的自动决策周期已暂停')
    if (runtime.value) {
      runtime.value.runtime_controls = {
        ...(runtime.value.runtime_controls || {}),
        automatic_trader: controlsTargetAction.value === 'enable',
      }
    }
    await loadRuntime()
  } catch (e: any) {
    toast.error(e.message || '更新调度状态失败')
  } finally {
    controlsSubmitting.value = false
  }
}

async function switchProfile(targetProfile: 'standard' | 'light') {
  if (!auth.isSuperadmin || featuresSubmitting.value) {
    if (!auth.isSuperadmin) toast.warning('仅超级管理员可调整运行模式')
    return
  }
  const confirmed = await dialogs.confirm(
    `确认将运行模式切换为【${targetProfile === 'light' ? '轻量模式 (Light)' : '标准模式 (Standard)'}】？\n\n说明：切换运行模式不会更改独立持仓保护、风控拦截门禁、新闻资讯与交易账本，也不会触发强制平仓。`
  )
  if (!confirmed) return
  featuresSubmitting.value = true
  try {
    const res = await api('/api/v1/admin/runtime-features', {
      method: 'PUT',
      body: JSON.stringify({
        profile: targetProfile,
        overrides: {},
      }),
    })
    if (res && res.profile) {
      runtimeFeatures.value = res
      toast.success(`运行模式已切换为【${targetProfile === 'light' ? '轻量模式' : '标准模式'}】`)
      await loadRuntime()
    }
  } catch (e: any) {
    toast.error(e.message || '切换运行模式失败')
  } finally {
    featuresSubmitting.value = false
  }
}

async function toggleFeatureOverride(featureKey: string, currentVal: boolean) {
  if (!auth.isSuperadmin || featuresSubmitting.value || !runtimeFeatures.value) {
    if (!auth.isSuperadmin) toast.warning('仅超级管理员可调整功能开关')
    return
  }
  const featureNames: Record<string, string> = {
    factor_snapshots: '因子快照',
    market_observations: '市场多维观测',
    entry_research: '开仓候选研究',
    scalp_research: '超短线快照研究',
    automatic_review: '自动复盘与经验积累',
  }
  const nextVal = !currentVal
  const label = featureNames[featureKey] || featureKey
  const confirmed = await dialogs.confirm(
    `确认${nextVal ? '开启' : '关闭'}【${label}】？\n\n说明：此操作仅影响可选后台研究计算，持仓保护、止损与风控门禁依然保持运行。`
  )
  if (!confirmed) return
  featuresSubmitting.value = true
  try {
    const res = await api('/api/v1/admin/runtime-features', {
      method: 'PUT',
      body: JSON.stringify({
        profile: runtimeFeatures.value.profile,
        overrides: { [featureKey]: nextVal },
      }),
    })
    if (res && res.profile) {
      runtimeFeatures.value = res
      toast.success(`【${label}】已${nextVal ? '开启' : '关闭'}`)
      await loadRuntime()
    }
  } catch (e: any) {
    toast.error(e.message || '更新功能开关失败')
  } finally {
    featuresSubmitting.value = false
  }
}

const quickNav = [
  {
    label: '提示词策略工作室',
    desc: '语义变量与预设方案',
    route: '/admin/promptlib',
    icon: FileText,
  },
  {
    label: '物理拦截插件',
    desc: 'Fail-Closed 风险拦截器',
    route: '/admin/interceptors',
    icon: ShieldCheck,
  },
  { label: '多模型决策委员会', desc: '博弈仲裁与思考链透视', route: '/admin/council', icon: Users },
  { label: '模型连接配置', desc: '供应商与思考强度', route: '/admin/llm', icon: Cpu },
]
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
    <div v-if="loadFailed" role="alert" class="flex items-center justify-between gap-3 rounded-lg border p-3" style="border-color:var(--color-down-border);color:var(--text-main)"><span>页面加载失败，请重试。</span><button class="ui-button ui-button--secondary ui-button--sm" :disabled="loading" @click="loadRuntime()">重试</button></div>

    <!-- Runtime Controls Card (Safety Switch for Automatic Trading) -->
    <AppCard v-if="runtime?.runtime_controls" class="p-4 text-sm leading-relaxed" data-runtime-controls>
      <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-2 border-b mb-2" style="border-color:var(--border-subtle)">
        <div class="flex items-center gap-2">
          <AppBadge :tone="runtime.runtime_controls.automatic_trader ? 'brand' : 'warning'" dot>
            {{ runtime.runtime_controls.automatic_trader ? '自动决策调度已启用' : '新的自动决策周期已暂停' }}
          </AppBadge>
          <span class="text-xs" style="color:var(--text-muted)">
            {{ connection.mode === 'live' ? '当前模式: OKX 实盘' : '当前模式: OKX 模拟盘' }}
          </span>
        </div>
        <div v-if="auth.isSuperadmin" class="flex items-center gap-2">
          <AppButton
            v-if="runtime.runtime_controls.automatic_trader"
            variant="secondary"
            size="sm"
            @click="openControlsModal('pause')"
          >
            <Pause class="size-3.5" aria-hidden="true" />
            <span>暂停调度</span>
          </AppButton>
          <AppButton
            v-else
            variant="primary"
            size="sm"
            :disabled="!connection.configured"
            @click="openControlsModal('enable')"
          >
            <Play class="size-3.5" aria-hidden="true" />
            <span>启用调度</span>
          </AppButton>
        </div>
      </div>
      <p style="color:var(--text-muted)">
        {{ runtime.runtime_controls.gateway_autostart ? '服务启动时允许拉起任务网关。' : '当前服务未启用任务网关自动拉起。' }}
        暂停不会撤销已有订单或中断已运行周期；网关运行时独立持仓保护继续。
        调度配置不等于交易所连接正常，也不表示已获准开仓。
      </p>
    </AppCard>

    <!-- Top Executive Header Strip -->
    <AppCard class="p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-3"
    >
      <div>
        <div class="flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full animate-pulse motion-reduce:animate-none" style="background: var(--color-up)" aria-hidden="true"></span>
          <h2
            class="text-sm sm:text-base font-black font-sans tracking-wide"
            style="color: var(--text-main)"
          >
            OKXQuant 控制中心
          </h2>
          <span
            class="px-2 py-0.5 rounded text-xs font-sans font-bold border"
            style="
              background-color: var(--color-brand-bg);
              color: var(--color-brand);
              border-color: var(--color-brand-border);
            "
          >
            {{ APP_VERSION }}
          </span>
        </div>
        <p class="text-sm font-sans mt-1" style="color: var(--text-muted)">
          交易状态、风险检查、任务与数据更新
        </p>
      </div>

      <div class="flex items-center space-x-2">
        <button
          @click="loadRuntime"
          :disabled="loading"
          class="ui-button ui-button--secondary ui-button--sm"
        >
          <RefreshCw class="w-3.5 h-3.5" :class="loading ? 'animate-spin' : ''" />
          <span>刷新状态</span>
        </button>
      </div>
    </AppCard>

    <!-- Loading State -->
    <LoadingState v-if="loading" />

    <!-- Runtime Data -->
    <template v-else-if="runtime">
      <!-- 4 High-Density Metric Bento Cards -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <!-- 1. 服务状态 -->
        <AppCard class="p-4"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-sans" style="color: var(--text-muted)">后台服务进程</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="
                background-color: var(--color-up-bg);
                border-color: var(--color-up-border);
                color: var(--color-up);
              "
            >
              <Server class="w-3.5 h-3.5" />
            </div>
          </div>
          <div
            class="text-xl sm:text-2xl font-black font-sans tracking-tight"
            style="color: var(--color-up)"
          >
            ONLINE
          </div>
          <div class="text-xs font-sans mt-1" style="color: var(--text-faint)">
            PID {{ runtime.service?.pid || '--' }} · FastAPI V5
          </div>
        </AppCard>

        <!-- 2. 运行时间 -->
        <AppCard class="p-4"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-sans" style="color: var(--text-muted)">引擎持续运行</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="
                background-color: var(--color-brand-bg);
                border-color: var(--color-brand-border);
                color: var(--color-brand);
              "
            >
              <Activity class="w-3.5 h-3.5" />
            </div>
          </div>
          <div
            class="text-xl sm:text-2xl font-black font-sans tracking-tight num-tabular"
            style="color: var(--text-main)"
          >
            {{ duration(runtime.service?.uptime_seconds) }}
          </div>
          <div class="text-xs font-sans mt-1" style="color: var(--text-faint)">
            已运行秒数 {{ runtime.service?.uptime_seconds || 0 }}s
          </div>
        </AppCard>

        <!-- 3. LLM 核心主脑 -->
        <AppCard class="p-4 cursor-pointer group"
          @click="router.push('/admin/llm')"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-sans" style="color: var(--text-muted)">决策主脑模型</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="
                background-color: var(--bg-badge);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            >
              <Cpu class="w-3.5 h-3.5" />
            </div>
          </div>
          <div
            class="text-sm sm:text-base font-black font-sans truncate"
            style="color: var(--text-main)"
          >
            {{ runtime.llm_runtime?.model || runtime.llm_runtime?.active_model || '未选择模型' }}
          </div>
          <div
            class="runtime-metric-meta text-xs font-sans mt-1"
            style="color: var(--text-faint)"
          >
            <span>推理思考: {{ runtime.llm_runtime?.active_reasoning_effort || 'HIGH' }}</span>
            <span class="text-indigo-400 group-hover:underline">配置通道 →</span>
          </div>
        </AppCard>

        <!-- 4. 交易所环境与授权 -->
        <AppCard class="p-4 cursor-pointer group"
          @click="router.push(auth.isSuperadmin ? '/admin/accounts' : '/admin/security')"
        >
          <div class="flex items-center justify-between mb-2">
            <span class="text-xs font-sans" style="color: var(--text-muted)">OKX 连接环境</span>
            <div
              class="w-6 h-6 rounded-md flex items-center justify-center border"
              style="
                background-color: var(--bg-badge);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            >
              <Database class="w-3.5 h-3.5" />
            </div>
          </div>
          <div
            class="text-xl sm:text-2xl font-black font-sans tracking-tight"
            style="color: var(--color-brand)"
          >
            {{
              connection.mode === 'demo' ? 'DEMO' : connection.mode === 'live' ? 'LIVE' : '环境待确认'
            }}
          </div>
          <div
            class="runtime-metric-meta text-xs font-sans mt-1"
            style="color: var(--text-faint)"
          >
            <span
              :class="connection.configured ? 'text-emerald-400' : 'text-amber-400'"
            >
              ● {{ connection.configured ? 'Key 已配置' : connection.status === 'unbound' ? '当前用途未绑定' : '尚未配置凭证' }}
            </span>
            <span class="text-indigo-400 group-hover:underline">账户管理 →</span>
          </div>
        </AppCard>
      </div>

      <!-- New Install Readiness Matrix UX (Truthful Status) -->
      <AppCard class="p-4 sm:p-5"
      >
        <div class="flex items-center justify-between pb-3 mb-3 border-b" style="border-color: var(--border-subtle)">
          <div class="flex items-center space-x-2">
            <ShieldCheck class="size-4 text-[var(--color-brand)]" />
            <h2 class="text-sm font-black font-sans uppercase tracking-wider" style="color: var(--text-main)">
              系统就绪与核验状态 (System Readiness)
            </h2>
          </div>
          <span class="text-xs font-sans" style="color: var(--text-faint)">
            服务在线不代表交易已连通或风控已放行 · 遵循实据核验原则
          </span>
        </div>

        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
          <!-- 1. API 凭证 (configured != connection verified) -->
          <div class="p-3 rounded-lg border flex flex-col justify-between gap-2" style="background: var(--bg-card-subtle); border-color: var(--border-subtle)">
            <span class="text-[var(--text-faint)] font-medium">1. API 凭证</span>
            <div class="flex items-center gap-1.5">
              <span
                class="size-2 rounded-full shrink-0"
                :style="{ background: connection.configured ? 'var(--color-warn)' : 'var(--border-strong)' }"
              ></span>
              <strong style="color: var(--text-main)">
                {{ connection.configured ? '静态 Key 已配置（待核验连通）' : '尚未配置凭证' }}
              </strong>
            </div>
            <span style="color: var(--text-muted)">
              {{ connection.configured ? '已保存静态凭据 · 不代表交易所连通' : '未检测到可用 API 凭据' }}
            </span>
          </div>

          <!-- 2. 交易账户 (environment neutral separate; ready only if verified binding/read capability) -->
          <div class="p-3 rounded-lg border flex flex-col justify-between gap-2" style="background: var(--bg-card-subtle); border-color: var(--border-subtle)">
            <div class="flex items-center justify-between">
              <span class="text-[var(--text-faint)] font-medium">2. 交易账户</span>
              <span class="text-[10px] px-1 py-0.5 rounded border font-mono" style="background: var(--bg-badge); border-color: var(--border-subtle); color: var(--text-muted)">
                {{ connection.mode === 'demo' ? '模拟盘 (DEMO)' : connection.mode === 'live' ? '实盘 (LIVE)' : '未选择环境' }}
              </span>
            </div>
            <div class="flex items-center gap-1.5">
              <span
                class="size-2 rounded-full shrink-0"
                :style="{ background: isAccountVerified ? 'var(--color-up)' : 'var(--color-warn)' }"
              ></span>
              <strong style="color: var(--text-main)">
                {{ isAccountVerified ? '账户连接已验证' : connection.configured ? '账户状态待核验' : '未配置账户' }}
              </strong>
            </div>
            <span style="color: var(--text-muted)">
              {{ isAccountVerified ? '已具备实际读取能力' : connection.status === 'unbound' ? '当前用途未绑定' : '需实际网络调用核验账户可用性' }}
            </span>
          </div>

          <!-- 3. 策略模板 (已选择方案/待运行验证 without fabricated greencheck) -->
          <div class="p-3 rounded-lg border flex flex-col justify-between gap-2" style="background: var(--bg-card-subtle); border-color: var(--border-subtle)">
            <span class="text-[var(--text-faint)] font-medium">3. 策略方案</span>
            <div class="flex items-center gap-1.5">
              <span class="size-2 rounded-full bg-indigo-400 shrink-0"></span>
              <strong style="color: var(--text-main)">
                {{ runtime.strategy_profile?.name ? `已选方案：${runtime.strategy_profile.name}` : '已选择方案 / 待运行验证' }}
              </strong>
            </div>
            <span style="color: var(--text-muted)">
              {{ runtime.llm_runtime?.model ? `${runtime.llm_runtime.model} · 待首周期运行验证` : '配置已加载 · 待首周期推演验证' }}
            </span>
          </div>

          <!-- 4. 风控门禁 (保护机制保留·执行状态待核验, no fabricated greencheck) -->
          <div class="p-3 rounded-lg border flex flex-col justify-between gap-2" style="background: var(--bg-card-subtle); border-color: var(--border-subtle)">
            <span class="text-[var(--text-faint)] font-medium">4. 风控门禁</span>
            <div class="flex items-center gap-1.5">
              <span class="size-2 rounded-full bg-indigo-400 shrink-0"></span>
              <strong style="color: var(--text-main)">
                保护机制保留 · 执行状态待核验
              </strong>
            </div>
            <span style="color: var(--text-muted)">
              拦截器静态配置存在 · 运行时放行需实盘证据
            </span>
          </div>

          <!-- 5. 自动调度 -->
          <div class="p-3 rounded-lg border flex flex-col justify-between gap-2" style="background: var(--bg-card-subtle); border-color: var(--border-subtle)">
            <span class="text-[var(--text-faint)] font-medium">5. 自动调度</span>
            <div class="flex items-center gap-1.5">
              <span
                class="size-2 rounded-full shrink-0"
                :style="{ background: runtime.runtime_controls?.automatic_trader ? 'var(--color-up)' : 'var(--border-strong)' }"
              ></span>
              <strong style="color: var(--text-main)">
                {{ runtime.runtime_controls?.automatic_trader ? '自动调度已启用' : '自动调度已暂停' }}
              </strong>
            </div>
            <span style="color: var(--text-muted)">
              {{ runtime.runtime_controls?.automatic_trader ? '按策略周期执行' : '无凭据时禁止开启' }}
            </span>
          </div>
        </div>
      </AppCard>

      <!-- Light Profile / Runtime Features Card -->
      <AppCard
        v-if="runtimeFeatures" class="p-4 sm:p-5"
      >
        <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-3 pb-3 mb-3 border-b" style="border-color: var(--border-subtle)">
          <div class="flex items-center space-x-2">
            <Sliders class="size-4 text-indigo-400" />
            <h2 class="text-sm font-black font-sans uppercase tracking-wider" style="color: var(--text-main)">
              算力与运行负载配置 (Runtime Profile)
            </h2>
            <AppBadge :tone="runtimeFeatures.profile === 'light' ? 'neutral' : 'brand'">
              {{ runtimeFeatures.profile === 'light' ? '轻量运行模式 (Light)' : '标准运行模式 (Standard)' }}
            </AppBadge>
          </div>
          <div v-if="auth.isSuperadmin" class="flex items-center gap-2">
            <AppButton
              v-if="runtimeFeatures.profile === 'standard'"
              variant="secondary"
              size="sm"
              :disabled="featuresSubmitting"
              @click="switchProfile('light')"
            >
              切换为轻量模式 (Light)
            </AppButton>
            <AppButton
              v-else
              variant="primary"
              size="sm"
              :disabled="featuresSubmitting"
              @click="switchProfile('standard')"
            >
              切换为标准模式 (Standard)
            </AppButton>
          </div>
          <span v-else class="text-xs" style="color:var(--text-muted)">仅超级管理员可调整运行模式</span>
        </div>

        <p class="text-xs mb-3 leading-relaxed" style="color: var(--text-muted)">
          轻量模式停用因子快照、多维观测、开仓研究与自动复盘等密集计算以降低负载与 Token 消耗。
          <strong>独立持仓保护、风控拦截门禁、新闻资讯与交易账本保持运行，不触发强制平仓。</strong>
        </p>

        <!-- Feature Switches List -->
        <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-5 gap-3 text-xs">
          <div
            v-for="(val, key) in runtimeFeatures.features"
            :key="key"
            class="p-2.5 rounded-lg border flex items-center justify-between"
            style="background: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <div>
              <strong class="block" style="color: var(--text-main)">
                {{ key === 'factor_snapshots' ? '因子快照' : key === 'market_observations' ? '市场多维观测' : key === 'entry_research' ? '开仓候选研究' : key === 'scalp_research' ? '超短线快照研究' : key === 'automatic_review' ? '自动复盘经验' : String(key) }}
              </strong>
              <span class="text-[10px]" style="color: var(--text-faint)">{{ String(key) }}</span>
            </div>
            <button
              v-if="auth.isSuperadmin"
              type="button"
              class="ui-button ui-button--sm px-2 py-1 text-xs"
              :class="val ? 'ui-button--secondary' : 'ui-button--ghost'"
              :disabled="featuresSubmitting"
              @click="toggleFeatureOverride(String(key), val)"
            >
              {{ val ? '启用中' : '已停用' }}
            </button>
            <span v-else class="text-xs px-2 py-0.5 rounded border" :style="{ color: val ? 'var(--color-up)' : 'var(--text-faint)' }">
              {{ val ? '已启用' : '已停用' }}
            </span>
          </div>
        </div>
      </AppCard>

      <!-- Interactive Quick Routing Bar -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <div
          v-for="item in quickNav"
          :key="item.route"
          @click="router.push(item.route)"
          class="flex items-center justify-between p-3.5 rounded-xl border font-sans cursor-pointer group transition-all shadow-xs"
          style="
            background-color: var(--bg-card);
            border-color: var(--border-subtle);
          "
        >
          <div class="flex items-center space-x-3">
            <div
              class="w-8 h-8 rounded-lg flex items-center justify-center border group-hover:scale-105 transition-transform"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            >
              <component :is="item.icon" class="w-4 h-4 text-indigo-400" />
            </div>
            <div>
              <div
                class="text-xs sm:text-sm font-black group-hover:text-indigo-400 transition-colors"
                style="color: var(--text-main)"
              >
                {{ item.label }}
              </div>
              <div class="text-xs mt-0.5 truncate max-w-[140px]" style="color: var(--text-muted)">
                {{ item.desc }}
              </div>
            </div>
          </div>
          <ArrowRight
            class="w-4 h-4 text-[var(--text-faint)] group-hover:translate-x-1 transition-transform shrink-0"
          />
        </div>
      </div>

      <!-- Main Operational Analytics Section -->
      <div class="overview-panels grid gap-4">
        <!-- Left: AI Decisions Table -->
        <AppCard class="p-4 sm:p-5 flex flex-col justify-between"
        >
          <div>
            <div
              class="flex items-center justify-between pb-3 mb-3 border-b"
              style="border-color: var(--border-subtle)"
            >
              <div class="flex items-center space-x-2">
                <Cpu class="w-4 h-4 text-indigo-400" />
                <h2
                  class="text-sm font-black font-sans uppercase tracking-wider"
                  style="color: var(--text-main)"
                >
                  大模型决策态势全景 (Realtime Brain Status)
                </h2>
              </div>
              <span
                class="text-xs font-sans font-bold cursor-pointer hover:underline text-indigo-400"
                @click="router.push('/admin/decisions')"
              >
                全部决策历史 →
              </span>
            </div>

            <div
              v-if="formattedDecisions.length === 0"
              class="py-12 text-center text-xs font-sans"
              style="color: var(--text-muted)"
            >
              暂无最新决策记录或大脑正在冷启动...
            </div>

            <div v-else class="table-scroll-container">
              <table class="w-full text-left font-sans text-xs">
                <thead>
                  <tr class="border-b" style="border-color: var(--border-subtle); color: var(--text-muted)">
                    <th class="pb-2.5 font-bold">标的代码</th>
                    <th class="pb-2.5 font-bold">动作信号</th>
                    <th class="pb-2.5 font-bold">信心指数</th>
                    <th class="pb-2.5 font-bold">决策时间</th>
                    <th class="pb-2.5 font-bold">推演核心逻辑</th>
                  </tr>
                </thead>
                <tbody class="divide-y" style="border-color: var(--border-subtle)">
                  <tr
                    v-for="(d, idx) in formattedDecisions"
                    :key="idx"
                    class="hover:bg-[var(--bg-card-subtle)] transition-colors"
                  >
                    <td class="py-2.5 font-bold" style="color: var(--text-main)">
                      {{ d.instId }}
                    </td>
                    <td class="py-2.5">
                      <span
                        class="px-2 py-0.5 rounded text-xs font-bold border"
                        :style="{
                          backgroundColor:
                            d.action === 'BUY'
                              ? 'var(--color-up-bg)'
                              : d.action === 'SELL'
                                ? 'var(--color-down-bg)'
                                : 'var(--bg-badge)',
                          borderColor:
                            d.action === 'BUY'
                              ? 'var(--color-up-border)'
                              : d.action === 'SELL'
                                ? 'var(--color-down-border)'
                                : 'var(--border-subtle)',
                          color:
                            d.action === 'BUY'
                              ? 'var(--color-up)'
                              : d.action === 'SELL'
                                ? 'var(--color-down)'
                                : 'var(--text-muted)',
                        }"
                      >
                        {{ d.action }}
                      </span>
                    </td>
                    <td class="py-2.5 num-tabular" style="color: var(--text-main)">
                      {{ (d.confidence * 100).toFixed(0) }}%
                    </td>
                    <td class="py-2.5 text-xs num-tabular" style="color: var(--text-muted)">
                      {{ d.timestamp }}
                    </td>
                    <td
                      class="py-2.5 text-xs truncate max-w-[220px]"
                      style="color: var(--text-muted)"
                      :title="d.reason"
                    >
                      {{ d.reason || '无核心摘要' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>
          </div>
        </AppCard>

        <!-- Right: Data Health Table -->
        <AppCard class="data-health min-w-0 p-4 sm:p-5 flex flex-col justify-between"
        >
          <div>
            <div
              class="flex items-center justify-between pb-3 mb-3 border-b"
              style="border-color: var(--border-subtle)"
            >
              <div class="flex items-center space-x-2">
                <Database class="w-4 h-4 text-emerald-400" />
                <h2
                  class="text-sm font-black font-sans uppercase tracking-wider"
                  style="color: var(--text-main)"
                >
                  关键数据管道时效 (Data Health)
                </h2>
              </div>
              <span
                class="px-2 py-0.5 rounded text-xs font-sans font-bold border"
                :style="{
                  backgroundColor:
                    dataHealthOverall === 'LIVE'
                      ? 'var(--color-up-bg)'
                      : 'var(--color-down-bg)',
                  borderColor:
                    dataHealthOverall === 'LIVE'
                      ? 'var(--color-up-border)'
                      : 'var(--color-down-border)',
                  color:
                    dataHealthOverall === 'LIVE' ? 'var(--color-up)' : 'var(--color-down)',
                }"
              >
                {{ dataHealthOverall }}
              </span>
            </div>

            <AppTable label="运行数据" class="data-health-scroll">
              <table class="data-health-table w-full text-left font-sans text-xs">
                <colgroup>
                  <col style="width: 42%" />
                  <col style="width: 24%" />
                  <col style="width: 18%" />
                  <col style="width: 16%" />
                </colgroup>
                <thead>
                  <tr class="border-b" style="border-color: var(--border-subtle); color: var(--text-muted)">
                    <th class="pb-2.5 font-bold">通道来源</th>
                    <th class="pb-2.5 font-bold">状态</th>
                    <th class="pb-2.5 font-bold">更新延时</th>
                    <th class="pb-2.5 font-bold text-right">字节数</th>
                  </tr>
                </thead>
                <tbody class="divide-y" style="border-color: var(--border-subtle)">
                  <tr
                    v-for="(x, idx) in dataHealthFiles"
                    :key="idx"
                    class="hover:bg-[var(--bg-card-subtle)] transition-colors"
                  >
                    <td class="py-2.5 font-bold" style="color: var(--text-main)">
                      <span class="data-health-source" :title="x.file || x.name" :aria-label="x.file || x.name">
                        {{ dataHealthSource(x.file || x.name) }}
                      </span>
                    </td>
                    <td class="py-2.5">
                      <span
                        class="px-2 py-0.5 rounded text-xs font-bold border inline-flex items-center space-x-1 whitespace-nowrap"
                        :style="{
                          backgroundColor: isDataHealthDisabled(x)
                            ? 'var(--bg-badge)'
                            : x.fresh ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                          borderColor: isDataHealthDisabled(x)
                            ? 'var(--border-subtle)'
                            : x.fresh ? 'var(--color-up-border)' : 'var(--color-down-border)',
                          color: isDataHealthDisabled(x)
                            ? 'var(--text-muted)'
                            : x.fresh ? 'var(--color-up)' : 'var(--color-down)',
                        }"
                      >
                        <CheckCircle2 v-if="x.fresh && !isDataHealthDisabled(x)" class="w-2.5 h-2.5 shrink-0" />
                        <AlertCircle v-else-if="!x.fresh && !isDataHealthDisabled(x)" class="w-2.5 h-2.5 shrink-0" />
                        <span>{{ dataHealthLabel(x) }}</span>
                      </span>
                    </td>
                    <td class="py-2.5 num-tabular" style="color: var(--text-muted)">
                      {{ isDataHealthDisabled(x) ? '--' : duration(x.age_seconds) }}
                    </td>
                    <td
                      class="py-2.5 text-right font-sans num-tabular"
                      style="color: var(--text-muted)"
                    >
                      {{ x.bytes ? Math.round(x.bytes / 1024) + ' KB' : '--' }}
                    </td>
                  </tr>
                </tbody>
              </table>
            </AppTable>
          </div>
        </AppCard>
      </div>

      <!-- Security & Config Cards -->
      <AppCard class="p-4 sm:p-5"
      >
        <div
          class="flex items-center justify-between pb-3 mb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <ShieldCheck class="w-4 h-4 text-emerald-500" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wider"
              style="color: var(--text-main)"
            >
              生产环境核心安全配置
            </h2>
          </div>
          <span class="text-xs font-sans" style="color: var(--text-faint)"
            >敏感 Key 已脱敏防泄露保护</span
          >
        </div>

        <div
          v-if="runtime.configuration && Object.keys(runtime.configuration).length"
          class="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-6 gap-3"
        >
          <div
            v-for="(v, k) in runtime.configuration"
            :key="k"
            class="rounded-lg border p-3 font-sans transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <div class="text-xs uppercase truncate font-medium" style="color: var(--text-faint)">
              {{ k }}
            </div>
            <div
              class="text-sm font-bold truncate mt-1.5"
              style="color: var(--text-main)"
              :title="String(v)"
            >
              {{ v || '未配置' }}
            </div>
          </div>
        </div>
        <div v-else class="py-6 text-center text-sm font-sans" style="color: var(--text-muted)">
          正在拉取核心安全配置...
        </div>
      </AppCard>
    </template>
    <EmptyState
      v-else
      title="运行数据暂不可用"
      description="请检查后端连接，或点击上方刷新状态重新加载。"
    />

    <!-- Runtime Controls Confirmation Dialog -->
    <AppDialog
      v-model:open="controlsModalOpen"
      :title="controlsTargetAction === 'enable' ? '确认启用自动决策调度' : '确认暂停自动决策调度'"
      size="md"
      description="此操作仅影响后续自动调度周期；关键安全风控与持仓保护保持有效。"
    >
      <div class="space-y-4 text-sm" data-controls-dialog>
        <div class="p-3 rounded-lg border text-xs leading-relaxed space-y-2" style="background:var(--bg-card-subtle);border-color:var(--border-subtle)">
          <p class="font-bold" style="color:var(--text-main)">
            {{ controlsTargetAction === 'enable' ? '⚠️ 启用自动开仓调度注意' : 'ℹ️ 暂停自动开仓调度说明' }}
          </p>
          <p style="color:var(--text-muted)">
            {{ controlsTargetAction === 'enable'
              ? '启用后，网关将在每个周期拉起 AI 模型推演并根据风控规则决定是否开仓。仅影响 FUTURE cycles；不会更改已存在的风控限额。启用不代表 LIVE 实盘免责，需自行确保各项就绪。'
              : '仅影响后续调度周期，不会撤销已有挂单，不会中断正在执行的周期，也不会停用独立持仓保护或风控拦截。' }}
          </p>
        </div>

        <AppField
          :label="`请输入确认短语：${expectedConfirmation}`"
          for="controls-confirmation-input"
        >
          <input
            id="controls-confirmation-input"
            v-model="controlsConfirmationInput"
            type="text"
            class="w-full font-mono text-sm"
            :placeholder="expectedConfirmation"
            :disabled="controlsSubmitting"
            autocomplete="off"
          />
        </AppField>

        <div class="flex items-center justify-end gap-2 pt-2">
          <AppButton
            variant="ghost"
            size="sm"
            :disabled="controlsSubmitting"
            @click="controlsModalOpen = false"
          >
            取消
          </AppButton>
          <AppButton
            :variant="controlsTargetAction === 'enable' ? 'primary' : 'secondary'"
            size="sm"
            :loading="controlsSubmitting"
            :disabled="controlsConfirmationInput.trim() !== expectedConfirmation"
            @click="submitRuntimeControls"
          >
            {{ controlsTargetAction === 'enable' ? '确认启用自动调度' : '确认暂停自动调度' }}
          </AppButton>
        </div>
      </div>
    </AppDialog>
  </div>
</template>

<style scoped>
.overview-panels {
  grid-template-columns: minmax(0, 1fr);
}

/* Reserve readable space for all four health columns at desktop widths. */
@media (min-width: 1200px) {
  .overview-panels {
    grid-template-columns: minmax(0, 1.4fr) minmax(460px, 1fr);
  }
}

.data-health-scroll {
  min-width: 0;
  max-width: 100%;
  overflow-x: auto;
}

.data-health-table {
  min-width: 420px;
  table-layout: fixed;
}

.data-health-table th,
.data-health-table td {
  padding-inline: 8px;
  white-space: nowrap;
  overflow-wrap: normal;
}

.data-health-source {
  display: block;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
</style>


<style scoped>
.runtime-metric-meta { display: flex; flex-wrap: wrap; align-items: center; gap: var(--space-1) var(--space-2); }
.runtime-metric-meta > span { white-space: nowrap; }
</style>
