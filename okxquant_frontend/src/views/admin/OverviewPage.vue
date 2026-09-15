<script setup lang="ts">
import AppTable from '../../components/ui/AppTable.vue'
import { useToast } from '../../composables/useFeedback'
const toast = useToast()
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'
import EmptyState from '../../components/ui/EmptyState.vue'

import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useApi } from '../../composables/useApi'
import { overviewConnection } from '../../utils/accountConnections'
import { dataHealthSource, dataHealthLabel } from '../../utils/dataHealthDisplay'
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
  Layers,
  Clock,
  CheckCircle2,
  AlertCircle,
} from 'lucide-vue-next'

const router = useRouter()
const { api } = useApi()
const runtime = ref<any>(null)
const loading = ref(true)
const connection = computed(() => overviewConnection(runtime.value))

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
  if (!dh) return []
  if (Array.isArray(dh)) return dh
  if (Array.isArray(dh.files)) return dh.files
  return []
})

const dataHealthOverall = computed(() => {
  const dh = runtime.value?.data_health
  if (!dh) return 'UNKNOWN'
  if (typeof dh === 'object' && dh.overall) return dh.overall
  if (Array.isArray(dh)) {
    return dh.every((f: any) => f.fresh) ? 'LIVE' : 'STALE'
  }
  return 'UNKNOWN'
})

async function loadRuntime() {
  loading.value = true
  try {
    // Runtime already includes the configuration summary used on this page.
    const rt = await api('/api/v1/admin/runtime')
    if (rt) {
      runtime.value = rt
    }
  } catch (e: any) {
    if (e?.silent) return
    toast.error(e.message)
  } finally {
    loading.value = false
  }
}

onMounted(() => {
  loadRuntime()
})

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
    <AppCard v-if="runtime?.runtime_controls" class="p-4 text-sm leading-relaxed" data-runtime-controls>
      <strong style="color:var(--text-main)">{{ runtime.runtime_controls.automatic_trader ? '自动决策调度已启用' : '新的自动决策周期已暂停' }}</strong>
      <p style="color:var(--text-muted)">
        {{ runtime.runtime_controls.gateway_autostart ? '服务启动时允许拉起任务网关。' : '当前服务未启用任务网关自动拉起。' }}
        暂停不会撤销已有订单或中断已运行周期；网关运行时独立持仓保护继续。
        调度配置不等于交易所连接正常，也不表示已获准开仓。
      </p>
    </AppCard>
    <!-- Top Executive Header Strip -->
    <AppCard
      class="rounded-xl border p-4 sm:p-5 flex flex-col md:flex-row md:items-center justify-between gap-3 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div>
        <div class="flex items-center space-x-2">
          <span class="w-2 h-2 rounded-full bg-emerald-500 animate-pulse"></span>
          <h2
            class="text-sm sm:text-base font-black font-sans tracking-wide"
            style="color: var(--text-main)"
          >
            OKXQuant 控制中心
          </h2>
          <span
            class="px-2 py-0.2 rounded text-xs font-sans font-bold border"
            style="
              background-color: var(--color-brand-bg);
              color: var(--color-brand);
              border-color: var(--color-brand-border);
            "
          >
            v0.1.0
          </span>
        </div>
        <p class="text-sm font-sans mt-1" style="color: var(--text-muted)">
          交易引擎、微积分决策链路、数据健康与物理拦截插件全景监控。
        </p>
      </div>

      <div class="flex items-center space-x-2">
        <button
          @click="loadRuntime"
          :disabled="loading"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg border text-sm font-sans font-bold transition-all cursor-pointer shadow-xs disabled:opacity-50"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
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
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
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
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
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
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors cursor-pointer group"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
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
            class="text-xs font-sans mt-1 flex items-center space-x-1.5"
            style="color: var(--text-faint)"
          >
            <span>推理思考: {{ runtime.llm_runtime?.active_reasoning_effort || 'HIGH' }}</span>
            <span>·</span>
            <span class="text-indigo-400 group-hover:underline">配置通道 →</span>
          </div>
        </AppCard>

        <!-- 4. 交易所环境与授权 -->
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors cursor-pointer group"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          @click="router.push('/admin/accounts')"
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
            class="text-xs font-sans mt-1 flex items-center space-x-1"
            style="color: var(--text-faint)"
          >
            <span
              :class="connection.configured ? 'text-emerald-400' : 'text-amber-400'"
            >
              ● {{ connection.configured ? 'Key 已配置' : connection.status === 'unbound' ? '当前用途未绑定' : '尚未配置凭证' }}
            </span>
            <span>·</span>
            <span class="text-indigo-400 group-hover:underline">账户管理 →</span>
          </div>
        </AppCard>
      </div>

      <!-- Quick Nav Action Deck -->
      <div class="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-3">
        <button
          v-for="nav in quickNav"
          :key="nav.route"
          @click="router.push(nav.route)"
          class="p-3.5 rounded-xl border flex items-center justify-between transition-all cursor-pointer text-left group"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-3">
            <div
              class="w-8 h-8 rounded-lg flex items-center justify-center border shrink-0 transition-transform group-hover:scale-105"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              <component :is="nav.icon" class="w-4 h-4" />
            </div>
            <div>
              <div
                class="text-sm font-black font-sans group-hover:text-blue-500 transition-colors"
                style="color: var(--text-main)"
              >
                {{ nav.label }}
              </div>
              <div class="text-xs font-sans truncate" style="color: var(--text-faint)">
                {{ nav.desc }}
              </div>
            </div>
          </div>
          <ArrowRight
            class="w-3.5 h-3.5 shrink-0 transition-transform group-hover:translate-x-0.5"
            style="color: var(--text-faint)"
          />
        </button>
      </div>

      <!-- Main Dual Panel: LLM Decision Audit & Data Freshness -->
      <div class="overview-panels grid gap-4">
        <!-- Left: Full Decisions Audit -->
        <AppCard
          class="min-w-0 rounded-xl border p-4 sm:p-5 shadow-xs transition-colors flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div>
            <div
              class="flex items-center justify-between pb-3 mb-3 border-b"
              style="border-color: var(--border-subtle)"
            >
              <div class="flex items-center space-x-2">
                <Layers class="w-4 h-4" style="color: var(--color-brand)" />
                <h2
                  class="text-sm font-black font-sans uppercase tracking-wider"
                  style="color: var(--text-main)"
                >
                  大模型决策态势全景 (Realtime Brain Status)
                </h2>
              </div>
              <button
                @click="router.push('/admin/decisions')"
                class="text-sm font-sans flex items-center space-x-1 cursor-pointer transition-colors"
                style="color: var(--color-brand)"
              >
                <span>完整推演日志</span>
                <ArrowRight class="w-3 h-3" />
              </button>
            </div>

            <!-- Decisions List -->
            <div v-if="formattedDecisions.length" class="space-y-2">
              <div
                v-for="d in formattedDecisions"
                :key="d.instId"
                class="p-3 rounded-lg border font-sans text-sm transition-colors"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
              >
                <div class="flex items-center justify-between">
                  <div class="flex items-center space-x-2">
                    <span class="font-black text-sm" style="color: var(--text-main)">{{
                      d.instId
                    }}</span>
                    <span
                      class="px-2 py-0.5 rounded text-xs font-bold border"
                      :style="{
                        backgroundColor: d.action?.includes('BUY')
                          ? 'var(--color-up-bg)'
                          : d.action?.includes('SELL')
                            ? 'var(--color-down-bg)'
                            : 'var(--bg-badge)',
                        borderColor: d.action?.includes('BUY')
                          ? 'var(--color-up-border)'
                          : d.action?.includes('SELL')
                            ? 'var(--color-down-border)'
                            : 'var(--border-subtle)',
                        color: d.action?.includes('BUY')
                          ? 'var(--color-up)'
                          : d.action?.includes('SELL')
                            ? 'var(--color-down)'
                            : 'var(--text-muted)',
                      }"
                    >
                      {{ d.action || '观望 HOLD' }}
                    </span>
                    <span v-if="d.confidence" class="text-xs" style="color: var(--text-faint)">
                      置信度 {{ Math.round(d.confidence * 100) }}%
                    </span>
                  </div>
                  <div class="text-xs" style="color: var(--text-faint)">
                    {{ d.timestamp ? d.timestamp.substring(11, 19) : '--' }}
                  </div>
                </div>
                <div
                  class="mt-2 text-sm font-sans line-clamp-2 leading-relaxed"
                  style="color: var(--text-muted)"
                >
                  {{ d.reason || '大模型评估当前动能结构未达到击穿阈值，顺势风控保持被动防御。' }}
                </div>
              </div>
            </div>
            <div
              v-else
              class="py-12 text-center text-sm font-sans"
              style="color: var(--text-muted)"
            >
              暂无巡检决策记录，等待下轮 15M 定时任务...
            </div>
          </div>
        </AppCard>

        <!-- Right: Data Health Monitor -->
        <AppCard
          class="data-health min-w-0 rounded-xl border p-4 sm:p-5 shadow-xs transition-colors flex flex-col justify-between"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div>
            <div
              class="flex flex-wrap gap-2 items-center justify-between pb-3 mb-3 border-b"
              style="border-color: var(--border-subtle)"
            >
              <div class="flex items-center space-x-2">
                <Clock class="w-4 h-4 shrink-0" style="color: var(--color-brand)" />
                <h2
                  class="text-sm font-black font-sans uppercase tracking-wider"
                  style="color: var(--text-main)"
                >
                  关键数据管道时效 (Data Health)
                </h2>
              </div>
              <span
                class="shrink-0 whitespace-nowrap px-2 py-0.5 rounded text-xs font-sans font-bold border"
                :style="{
                  backgroundColor:
                    dataHealthOverall === 'LIVE' ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                  borderColor:
                    dataHealthOverall === 'LIVE'
                      ? 'var(--color-up-border)'
                      : 'var(--color-down-border)',
                  color: dataHealthOverall === 'LIVE' ? 'var(--color-up)' : 'var(--color-down)',
                }"
              >
                {{ dataHealthOverall }}
              </span>
            </div>

            <AppTable label="运行数据" class="data-health-scroll"
              ><table class="data-health-table w-full text-left font-sans text-sm border-collapse">
                <colgroup>
                  <col style="width: 28%" />
                  <col style="width: 28%" />
                  <col style="width: 24%" />
                  <col style="width: 20%" />
                </colgroup>
                <thead>
                  <tr
                    class="border-b text-xs uppercase"
                    style="border-color: var(--border-subtle); color: var(--text-faint)"
                  >
                    <th class="pb-2 font-medium">通道来源</th>
                    <th class="pb-2 font-medium">状态</th>
                    <th class="pb-2 font-medium">更新延时</th>
                    <th class="pb-2 text-right font-medium">字节数</th>
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
                          backgroundColor: x.fresh ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
                          borderColor: x.fresh
                            ? 'var(--color-up-border)'
                            : 'var(--color-down-border)',
                          color: x.fresh ? 'var(--color-up)' : 'var(--color-down)',
                        }"
                      >
                        <CheckCircle2 v-if="x.fresh" class="w-2.5 h-2.5 shrink-0" />
                        <AlertCircle v-else class="w-2.5 h-2.5 shrink-0" />
                        <span>{{ dataHealthLabel(x) }}</span>
                      </span>
                    </td>
                    <td class="py-2.5 num-tabular" style="color: var(--text-muted)">
                      {{ duration(x.age_seconds) }}
                    </td>
                    <td
                      class="py-2.5 text-right font-sans num-tabular"
                      style="color: var(--text-muted)"
                    >
                      {{ x.bytes ? Math.round(x.bytes / 1024) + ' KB' : '--' }}
                    </td>
                  </tr>
                </tbody>
              </table></AppTable
            >
          </div>
        </AppCard>
      </div>

      <!-- Security & Config Cards -->
      <AppCard
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
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
