<script setup lang="ts">
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'

import { useErrorFeedback } from '../../composables/useFeedback'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Package, Cpu, KeyRound, RefreshCw } from 'lucide-vue-next'

const { api } = useApi()
const data = ref<any>(null)
const loading = ref(true)
const loadFailed = ref(false)
const errText = ref('')
const phaseLabel = (phase: string) => ({connect:'连接',headers:'等待响应头',first_byte:'等待首包',idle:'流空闲',body:'读取响应',total:'总时限',protocol:'协议完整性',http:'HTTP 错误',configuration:'配置',network:'网络',worker:'传输进程',complete:'完成'}[phase] || phase || '无')
const transportMetric = (value: any, suffix = '') => typeof value === 'number' && Number.isFinite(value) ? `${value}${suffix}` : '未记录'

async function load() {
  loadFailed.value = false
  loading.value = true
  try {
    data.value = await api('/api/v1/admin/agents')
    errText.value = ''
  } catch (e: any) {
    if (e?.silent) return
    loadFailed.value = true
    errText.value = e.message
  } finally {
    loading.value = false
  }
}

function statusColor(s: string) {
  if (s === 'disabled') return 'text-[var(--text-muted)]'
  if (['success', 'running', 'online', 'idle'].includes(s)) return 'text-emerald-400'
  if (['failed', 'error', 'offline'].includes(s)) return 'text-rose-400'
  return 'text-amber-400'
}

function agentHealthLabel(a: any): string {
  if (a.health === 'disabled' || a.automatic_enabled === false) {
    return (a.id?.includes('evolution') || a.role?.includes('evolution')) ? '未启用（可手动复盘）' : '未启用'
  }
  return a.health || '--'
}

function agentOutputLabel(a: any): string {
  if (a.output_age_seconds != null) return Math.round(a.output_age_seconds / 60) + ' 分钟前'
  if (a.health === 'disabled' || a.automatic_enabled === false) return a.output ? '历史产物' : '未启用'
  return a.output ? '冷启动' : '无产物'
}

onMounted(load)

useErrorFeedback(errText)
</script>

<template>
  <div class="space-y-4">
    <div v-if="loadFailed" role="alert" class="flex items-center justify-between gap-3 rounded-lg border p-3" style="border-color:var(--color-down-border);color:var(--text-main)"><span>页面加载失败，请重试。</span><button class="ui-button ui-button--secondary ui-button--sm" :disabled="loading" @click="load()">重试</button></div>
    <LoadingState v-if="loading" />

    <template v-else-if="data">
      <!-- Agents -->
      <AppCard class="overflow-hidden"
      >
        <div
          class="px-4 py-3 border-b flex items-center justify-between"
          style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
        >
          <div class="flex items-center space-x-2">
            <Package class="w-4 h-4 text-blue-400" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              受管 Worker 单元清单
            </h2>
          </div>
          <button
            @click="load"
            class="ui-action ui-action--sm border"
            style="
              background-color: var(--bg-card);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <RefreshCw class="w-3 h-3" />
            <span>刷新</span>
          </button>
        </div>
        <div class="table-scroll-container">
          <table class="w-full text-left text-sm font-sans whitespace-nowrap">
            <thead>
              <tr
                class="border-b text-xs uppercase tracking-wider font-bold"
                style="
                  border-color: var(--border-subtle);
                  background-color: var(--bg-card-subtle);
                  color: var(--text-muted);
                "
              >
                <th class="py-2.5 px-4">Worker 单元</th>
                <th class="py-2.5 px-3">核心职责</th>
                <th class="py-2.5 px-3">健康状态</th>
                <th class="py-2.5 px-3">最近执行时间</th>
                <th class="py-2.5 px-3">运行结果</th>
                <th class="py-2.5 px-4 text-right">产物时效</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="a in data.agents"
                :key="a.id"
                class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                style="border-color: var(--border-subtle)"
              >
                <td class="py-2.5 px-4 font-bold" style="color: var(--text-main)">{{ a.name }}</td>
                <td class="py-2.5 px-3" style="color: var(--text-muted)">{{ a.role }}</td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(a.health)">{{ agentHealthLabel(a) }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint)">
                  {{ a.last_run_at || '尚未调度' }}
                </td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(a.last_run_status)">
                  {{ a.last_run_status }}
                </td>
                <td class="py-2.5 px-4 text-right" style="color: var(--text-muted)">
                  {{ agentOutputLabel(a) }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </AppCard>

      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <!-- Model Telemetry -->
        <AppCard class="overflow-hidden p-4"
        >
          <div class="flex items-center space-x-2 mb-3">
            <Cpu class="w-4 h-4 text-purple-400" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              模型调用遥测 (最近 50 次)
            </h2>
          </div>
          <div
            class="text-xs font-sans mb-3 p-2.5 rounded-lg border leading-relaxed"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-subtle);
              color: var(--text-muted);
            "
          >
            {{ data.prompt_policy }}
          </div>
          <div class="grid grid-cols-3 gap-2.5 mb-3 text-center">
            <div
              class="rounded-lg border p-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <div class="text-xs font-sans" style="color: var(--text-faint)">总调用量</div>
              <div
                class="text-sm font-bold font-sans num-tabular mt-0.5"
                style="color: var(--text-main)"
              >
                {{ data.model_stats?.total_calls ?? '--' }}
              </div>
            </div>
            <div
              class="rounded-lg border p-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <div class="text-xs font-sans" style="color: var(--text-faint)">调用成功率</div>
              <div
                class="text-sm font-bold num-tabular mt-0.5"
                :class="
                  (data.model_stats?.total_calls ?? 0) > 0 &&
                  (data.model_stats?.successful_calls ?? 0) < (data.model_stats?.total_calls ?? 0)
                    ? 'text-amber-500'
                    : 'text-emerald-500'
                "
              >
                {{
                  (data.model_stats?.total_calls ?? 0) > 0
                    ? Math.round(
                        (100 * (data.model_stats?.successful_calls ?? 0)) /
                          data.model_stats.total_calls,
                      ) + '%'
                    : '--'
                }}
              </div>
            </div>
            <div
              class="rounded-lg border p-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <div class="text-xs font-sans" style="color: var(--text-faint)">平均时延</div>
              <div
                class="text-sm font-bold font-sans num-tabular mt-0.5"
                style="color: var(--text-main)"
              >
                {{
                  data.model_stats?.avg_duration_ms
                    ? Math.round(data.model_stats.avg_duration_ms) + 'ms'
                    : '--'
                }}
              </div>
            </div>
          </div>
          <div
            class="table-scroll-container max-h-60 overflow-y-auto rounded-lg border"
            style="border-color: var(--border-subtle)"
          >
            <table class="w-full text-left text-sm font-sans whitespace-nowrap">
              <thead class="sticky top-0 z-10">
                <tr
                  class="border-b text-xs uppercase tracking-wider font-bold"
                  style="
                    border-color: var(--border-subtle);
                    background-color: var(--bg-card-subtle);
                    color: var(--text-muted);
                  "
                >
                  <th class="py-2 px-3">调用方</th>
                  <th class="py-2 px-2">模型</th>
                  <th class="py-2 px-2">状态</th>
                  <th class="py-2 px-2">Tokens</th>
                  <th class="py-2 px-3 text-right">耗时</th>
                  <th class="py-2 px-3">传输</th>
                </tr>
              </thead>
              <tbody>
                <tr
                  v-for="c in (data.model_calls || []).slice(0, 30)"
                  :key="c.id"
                  class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                  style="border-color: var(--border-subtle)"
                >
                  <td class="py-1.5 px-3" style="color: var(--text-muted)">
                    {{ c.caller || '--' }}
                  </td>
                  <td class="py-1.5 px-2 num-tabular" style="color: var(--text-faint)">
                    {{ c.model || '--' }}
                  </td>
                  <td class="py-1.5 px-2 font-bold" :class="statusColor(c.status)">
                    {{ c.status }}
                  </td>
                  <td class="py-1.5 px-2 num-tabular" style="color: var(--text-muted)">
                    {{ c.total_tokens ?? '--' }}
                  </td>
                  <td class="py-1.5 px-3 text-right num-tabular" style="color: var(--text-muted)">
                    {{ c.duration_ms === null || c.duration_ms === undefined ? '--' : Math.round(c.duration_ms) + 'ms' }}
                  </td>
                  <td class="py-2 px-3 whitespace-normal">
                    <details v-if="c.transport" class="action-disclosure text-xs min-w-[180px]" data-model-transport>
                      <summary>{{ c.transport.transport_mode === 'stream' ? '流式' : '非流式' }} · 传输详情</summary>
                      <dl class="space-y-1 pt-2 max-w-[300px] break-words">
                        <div>HTTP {{ c.transport.http_status ?? '--' }} · 阶段 {{ phaseLabel(c.transport.failure_phase) }}</div>
                        <div>首包 {{ transportMetric(c.transport.first_byte_ms, ' ms') }}</div>
                        <div>首个模型内容 {{ transportMetric(c.transport.first_content_ms, ' ms') }}</div>
                        <div>最长间隔 {{ transportMetric(c.transport.max_gap_ms, ' ms') }}</div>
                        <div>心跳 {{ transportMetric(c.transport.heartbeat_count) }} · 尝试 {{ transportMetric(c.transport.attempts) }}</div>
                        <div>完整结束 {{ c.transport.completion_seen === true ? '是' : c.transport.completion_seen === false ? '否' : '未记录' }}</div>
                        <div v-if="c.transport.request_id" class="break-all">请求 ID {{ c.transport.request_id }}</div>
                        <div v-if="c.transport.cf_ray" class="break-all">CF-Ray {{ c.transport.cf_ray }}</div>
                      </dl>
                    </details>
                    <span v-else class="text-xs" style="color:var(--text-faint)">历史记录未采集</span>
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </AppCard>

        <!-- Secret Store -->
        <AppCard class="p-4"
        >
          <div class="flex items-center space-x-2 mb-3">
            <KeyRound class="w-4 h-4 text-amber-500" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              本机加密密文库
            </h2>
          </div>
          <div class="space-y-1.5 text-sm font-sans">
            <div
              class="flex items-center justify-between border rounded-lg px-3 py-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span style="color: var(--text-muted)">加密库状态</span>
              <span
                :class="
                  data.secret_store?.initialized
                    ? 'text-emerald-500 font-bold'
                    : 'text-rose-500 font-bold'
                "
                >{{ data.secret_store?.initialized ? '已初始化 ✓' : '未初始化' }} ·
                {{ data.secret_store?.count ?? 0 }} 项密文 · 文件权限
                {{ data.secret_store?.store_mode || '--' }}</span
              >
            </div>
            <div
              class="flex items-center justify-between border rounded-lg px-3 py-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span style="color: var(--text-muted)">读取优先级</span
              ><span style="color: var(--text-main)">{{
                data.secret_store?.source_priority || 'encrypted-store-over-env'
              }}</span>
            </div>
            <div
              v-for="k in data.secret_store?.keys || []"
              :key="k"
              class="flex items-center justify-between border rounded-lg px-3 py-2"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span style="color: var(--text-muted)">{{ k }}</span>
              <span class="text-emerald-500 font-bold">已配置 ✓</span>
            </div>
          </div>
        </AppCard>
      </div>
    </template>
  </div>
</template>
