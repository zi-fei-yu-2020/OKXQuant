<script setup lang="ts">
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'

import { useFeedback } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Zap, RefreshCw, RotateCcw, Server, Clock, AlertTriangle } from 'lucide-vue-next'

const { api } = useApi()
const gw = ref<any>(null)
const loading = ref(true)
const bannerMsg = useFeedback()

const deliveredCount = computed(
  () => (gw.value?.stats?.delivered ?? 0) + (gw.value?.stats?.accepted ?? 0),
)
const deliveryTotal = computed(() =>
  Object.values(gw.value?.stats || {}).reduce((a: number, b: any) => a + Number(b || 0), 0),
)
const overdueCount = computed(
  () => (gw.value?.scheduler?.jobs || []).filter((j: any) => j.overdue).length,
)

async function load() {
  loading.value = true
  try {
    gw.value = await api('/api/v1/admin/gateway?limit=50')
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

async function replayDelivery(id: number) {
  const phrase = await prompt(`重放投递 #${id} 需精确输入确认短语：REPLAY ${id}`)
  if (!phrase) return
  try {
    await api(`/api/v1/admin/gateway/deliveries/${id}/replay`, {
      method: 'POST',
      body: JSON.stringify({ confirmation: phrase.trim().toUpperCase() }),
    })
    bannerMsg.value = { text: `投递 #${id} 已重新入队`, type: 'ok' }
    await load()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `重放失败：${e.message}`, type: 'err' }
  }
}

function statusColor(s: string) {
  if (s === 'success' || s === 'delivered' || s === 'ok') return 'text-emerald-400'
  if (s === 'dead' || s === 'failed' || s === 'error') return 'text-rose-400'
  if (s === 'pending' || s === 'retrying') return 'text-amber-400'
  return 'text-zinc-300'
}

onMounted(load)

const { prompt } = useDialogs()
</script>

<template>
  <div class="space-y-4">
    <LoadingState v-if="loading" />

    <template v-else-if="gw">
      <AppCard v-if="gw.runtime_note" class="p-4 flex items-start gap-3" role="status">
        <AlertTriangle class="w-5 h-5 shrink-0 text-amber-500" aria-hidden="true" />
        <div class="space-y-1">
          <h2 class="text-sm font-semibold" style="color: var(--text-main)">运行环境说明</h2>
          <p class="text-sm leading-relaxed" style="color: var(--text-muted)">{{ gw.runtime_note }}</p>
        </div>
      </AppCard>
      <!-- Worker & Stats Cards -->
      <div class="grid grid-cols-2 lg:grid-cols-4 gap-3">
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="flex items-center space-x-2 text-xs font-sans mb-2"
            style="color: var(--text-muted)"
          >
            <Server class="w-4 h-4 text-emerald-500" /><span>Gateway 进程</span>
          </div>
          <div
            class="text-lg font-black font-sans"
            :class="gw.running ? 'text-emerald-500' : 'text-rose-500'"
          >
            {{ gw.running ? 'ONLINE' : 'OFFLINE' }}
          </div>
          <div class="text-xs font-sans mt-1" style="color: var(--text-faint)">
            PID {{ gw.pid || '--' }} · v{{ gw.version }}
          </div>
        </AppCard>
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="flex items-center space-x-2 text-xs font-sans mb-2"
            style="color: var(--text-muted)"
          >
            <Zap class="w-4 h-4 text-blue-500" /><span>投递队列</span>
          </div>
          <div class="text-lg font-black font-sans num-tabular" style="color: var(--text-main)">
            {{ deliveredCount
            }}<span class="text-sm" style="color: var(--text-muted)"> / {{ deliveryTotal }}</span>
          </div>
          <div class="text-xs font-sans mt-1" style="color: var(--text-faint)">
            待处理 {{ gw.stats?.pending ?? 0 }} · 重试 {{ gw.stats?.retry ?? 0 }}
          </div>
        </AppCard>
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="flex items-center space-x-2 text-xs font-sans mb-2"
            style="color: var(--text-muted)"
          >
            <AlertTriangle class="w-4 h-4 text-amber-500" /><span>死信 / 关键事件</span>
          </div>
          <div
            class="text-lg font-black font-sans num-tabular"
            :class="(gw.stats?.dead ?? 0) > 0 ? 'text-rose-500' : 'text-emerald-500'"
          >
            {{ gw.stats?.dead ?? 0
            }}<span class="text-sm" style="color: var(--text-muted)">
              / {{ gw.event_health?.critical_total ?? 0 }}</span
            >
          </div>
          <div class="text-xs font-sans mt-1" style="color: var(--text-faint)">
            关键未达 {{ gw.event_health?.critical_unmet ?? 0 }} · 失败
            {{ gw.event_health?.critical_failed ?? 0 }}
          </div>
        </AppCard>
        <AppCard
          class="rounded-xl border p-4 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="flex items-center space-x-2 text-xs font-sans mb-2"
            style="color: var(--text-muted)"
          >
            <Clock class="w-4 h-4 text-purple-500" /><span>调度任务</span>
          </div>
          <div class="text-lg font-black font-sans num-tabular" style="color: var(--text-main)">
            {{ gw.scheduler?.jobs?.length ?? 0 }}
          </div>
          <div
            class="text-xs font-sans mt-1"
            :class="overdueCount > 0 ? 'text-rose-500' : 'text-emerald-500'"
          >
            {{ overdueCount > 0 ? overdueCount + ' 个任务逾期!' : '无逾期任务' }}
          </div>
        </AppCard>
      </div>

      <!-- Scheduler Jobs -->
      <AppCard
        v-if="gw.scheduler?.jobs?.length"
        class="rounded-xl border overflow-hidden shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="px-4 py-3 border-b flex items-center justify-between"
          style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
        >
          <h2
            class="text-sm font-black font-sans uppercase tracking-wide"
            style="color: var(--text-main)"
          >
            本地调度计划（北京时间）
          </h2>
          <span class="text-xs font-sans" style="color: var(--text-faint)"
            >{{ gw.scheduler.jobs.length }} 个受管定时作业</span
          >
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
                <th class="py-2.5 px-4">任务</th>
                <th class="py-2.5 px-3">脚本</th>
                <th class="py-2.5 px-3">触发</th>
                <th class="py-2.5 px-3">最近调度</th>
                <th class="py-2.5 px-4 text-right">状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="j in gw.scheduler.jobs"
                :key="j.name"
                class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                style="border-color: var(--border-subtle)"
              >
                <td class="py-2.5 px-4 font-bold" style="color: var(--text-main)">{{ j.name }}</td>
                <td class="py-2.5 px-3 text-xs" style="color: var(--text-muted)">{{ j.script }}</td>
                <td class="py-2.5 px-3 font-medium" style="color: var(--text-main)">
                  {{ j.schedule }}
                </td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint)">
                  {{ j.last_scheduled_at || '尚未调度' }}
                </td>
                <td
                  class="py-2.5 px-4 text-right font-bold"
                  :class="j.overdue ? 'text-rose-400' : 'text-emerald-400'"
                >
                  {{ j.overdue ? '⚠ 逾期' : '正常' }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </AppCard>

      <!-- Deliveries -->
      <AppCard
        class="rounded-xl border overflow-hidden shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="px-4 py-3 border-b flex items-center justify-between"
          style="border-color: var(--border-subtle); background-color: var(--bg-card-subtle)"
        >
          <div class="flex items-center space-x-2">
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              事件投递队列 (最近 50 条)
            </h2>
            <span
              class="text-xs font-sans px-2 py-0.2 rounded border font-bold"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
            >
              {{ gw.deliveries?.length || 0 }} 记录
            </span>
          </div>
          <button
            @click="load"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs font-sans cursor-pointer transition-all shadow-xs"
            style="
              background-color: var(--bg-card);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <RefreshCw class="w-3 h-3" />
            <span>刷新队列</span>
          </button>
        </div>
        <div class="table-scroll-container max-h-[420px] overflow-y-auto">
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
                <th class="py-2.5 px-4">#</th>
                <th class="py-2.5 px-3">事件类型</th>
                <th class="py-2.5 px-3">投递通道</th>
                <th class="py-2.5 px-3">状态</th>
                <th class="py-2.5 px-3">尝试</th>
                <th class="py-2.5 px-3">时间</th>
                <th class="py-2.5 px-4 text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="d in gw.deliveries"
                :key="d.id"
                class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                style="border-color: var(--border-subtle)"
              >
                <td class="py-2.5 px-4 num-tabular" style="color: var(--text-faint)">{{ d.id }}</td>
                <td class="py-2.5 px-3 font-bold" style="color: var(--text-main)">
                  {{ d.event_type || d.topic || '--' }}
                </td>
                <td class="py-2.5 px-3" style="color: var(--text-muted)">
                  {{ d.channel || '--' }}
                </td>
                <td class="py-2.5 px-3 font-bold" :class="statusColor(d.status)">{{ d.status }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-muted)">
                  {{ d.attempts ?? d.attempt_count ?? 1 }}
                </td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint)">
                  {{ d.created_at || d.time || '--' }}
                </td>
                <td class="py-2.5 px-4 text-right">
                  <button
                    v-if="d.status === 'dead'"
                    @click="replayDelivery(d.id)"
                    class="flex items-center space-x-1 ml-auto px-2 py-1 rounded-md border text-xs font-sans cursor-pointer transition-colors"
                    style="
                      background-color: var(--color-warn-bg);
                      border-color: var(--color-warn-border);
                      color: var(--color-warn);
                    "
                  >
                    <RotateCcw class="w-3 h-3" /><span>重放</span>
                  </button>
                  <span v-else class="text-xs" style="color: var(--text-faint)">--</span>
                </td>
              </tr>
              <tr v-if="!gw.deliveries || gw.deliveries.length === 0">
                <td colspan="7" class="py-8 text-center" style="color: var(--text-faint)">
                  暂无投递记录
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </AppCard>
    </template>
  </div>
</template>
