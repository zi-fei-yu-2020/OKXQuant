<script setup lang="ts">
import { useApiAction } from '../../composables/useApiAction'
const { action, actionBusy, canManage } = useApiAction(() => loading.value || loadFailed.value)

import AppCard from '../../components/ui/AppCard.vue'
import MemoryManagementPanel from '../../components/MemoryManagementPanel.vue'
import type { MemoryPublication } from '../../utils/memory'
import EvolutionReviewPanel from '../../components/EvolutionReviewPanel.vue'
import type { EvolutionReview } from '../../components/EvolutionReviewPanel.vue'

import { useFeedback, useToast } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  Brain,
  Save,
  PlayCircle,
  BookOpen,
  Terminal,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const loading = ref(true)
const loadFailed = ref(false)
const busy = ref<'save' | 'run' | 'add' | 'delete' | 'toggle' | 'rollback' | ''>('')
const bannerMsg = useFeedback()

// Pipelines state (evolution_system & evolution_user)
const activeTab = ref<'settings' | 'evolution_system' | 'evolution_user'>('settings')
const lib = ref<any>(null)
const selectedProfileId = ref('stable')
const workingModules = ref<any[]>([])
const dirty = ref(false)

// Structured White-Box Memory state
const memoryPublication = ref<MemoryPublication>()
const memorySchedule = ref<string | string[]>()
const evolutionReview = ref<EvolutionReview>()
const evolutionRequest = ref<{request_id: string; status: string} | null>(null)
const statusLoading = ref(false)


const selectedProfile = computed(
  () => (lib.value?.profiles || []).find((p: any) => p.id === selectedProfileId.value) || null,
)

async function loadData() {
  loadFailed.value = false
  loading.value = true
  try {
    const [libRes, memRes] = await Promise.all([
      api('/api/v1/admin/prompt-library'),
      api('/api/v1/admin/memory'),
    ])
    lib.value = libRes
    selectedProfileId.value = libRes.active_profile_id || 'stable'
    memoryPublication.value = memRes.publication
    memorySchedule.value = memRes.self_improvement_schedule
    evolutionReview.value = memRes.evolution_review
    syncWorkingModules()
  } catch (e: any) {
    if (e?.silent) return
    loadFailed.value = true
    bannerMsg.value = { text: `加载失败: ${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function memoryUpdated(res: any) { memoryPublication.value=res.publication; memorySchedule.value=res.self_improvement_schedule; evolutionReview.value=res.evolution_review }

function syncWorkingModules() {
  dirty.value = false
  if (activeTab.value === 'settings') return
  const views = selectedProfile.value?.pipeline_views?.[activeTab.value] || []
  workingModules.value = JSON.parse(JSON.stringify(views))
}

async function switchTab(tab: 'settings' | 'evolution_system' | 'evolution_user') {
  if (tab === activeTab.value) return
  if (dirty.value && !(await confirm('当前复盘模板未保存，切换将丢失修改。继续？'))) return
  activeTab.value = tab
  syncWorkingModules()
}

const savePipelineModules = action(async () => {
  if (!selectedProfile.value) return
  busy.value = 'save'
  bannerMsg.value = null
  try {
    const pipelinesMap: Record<string, any[]> = JSON.parse(JSON.stringify(selectedProfile.value.pipeline_views || selectedProfile.value.pipelines || {}))
    pipelinesMap[activeTab.value] = workingModules.value.map((m) => ({
      id: m.id,
      title: m.title,
      content: m.content,
      enabled: m.enabled,
      locked: m.locked,
      source: m.source,
    }))

    await api(`/api/v1/admin/prompt-profiles/${selectedProfile.value.id}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: selectedProfile.value.name,
        description: selectedProfile.value.description,
        editor_mode: 'modules',
        pipelines: pipelinesMap,
      }),
    })
    bannerMsg.value = { text: `✅ 自进化模版布局已成功保存，下一轮复盘自动生效`, type: 'ok' }
    await loadData()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `保存失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
})

const triggerEvolutionNow = action(async () => {
  const phrase = await prompt(
    '将复盘任务加入队列，不自动发布运行记忆。请输入确认短语：RUN EVOLUTION',
  )
  if (!phrase) return
  if (phrase.trim().toUpperCase() !== 'RUN EVOLUTION') {
    toast.warning('确认短语错误，已取消执行')
    return
  }
  busy.value = 'run'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/gateway/jobs/self_improvement/run', {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'RUN EVOLUTION' }),
    })
    evolutionRequest.value = { request_id: res.request_id, status: res.status }
    bannerMsg.value = {
      text: res.status === 'running' ? '复盘任务运行中；不会自动发布运行记忆。' : '复盘已排队；不会自动发布运行记忆。',
      type: 'ok',
    }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `执行复盘失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
})

async function refreshEvolutionStatus() {
  if (statusLoading.value) return
  statusLoading.value = true
  try { memoryUpdated(await api('/api/v1/admin/memory')) }
  catch (e: any) { if (!e?.silent) toast.error(`查询失败：${e.message}`) }
  finally { statusLoading.value = false }
}

onMounted(loadData)

const { prompt, confirm } = useDialogs()
onBeforeRouteLeave(async () => !dirty.value || await confirm('当前复盘模板未保存，离开将丢失修改。继续？'))

const toast = useToast()
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
    <div v-if="loadFailed" role="alert" class="flex items-center justify-between gap-3 rounded-lg border p-3" style="border-color:var(--color-down-border);color:var(--text-main)"><span>页面加载失败，请重试。</span><button class="ui-button ui-button--secondary ui-button--sm" :disabled="loading || actionBusy" @click="loadData()">重试</button></div>
    <!-- Header -->
    <div class="flex items-center justify-between">
      <div>
        <h2
          class="text-sm sm:text-base font-black font-sans tracking-wide"
          style="color: var(--text-main)"
        >
          策略复盘与运行记忆版本管理
        </h2>
        <p class="text-sm font-sans mt-0.5" style="color: var(--text-muted)">
          复盘产生建议，审核后明确发布；模型与前后台读取同一版本，所有历史版本保留，不自动加载旧基准。
        </p>
      </div>
      <span
        class="text-xs font-sans px-2 py-1 rounded border font-bold"
        style="
          background-color: var(--color-brand-bg);
          color: var(--color-brand);
          border-color: var(--color-brand-border);
        "
      >
        可审计发布 · 同源读取
      </span>
    </div>

    <div class="flex flex-wrap items-center gap-3 text-sm">
      <p v-if="evolutionRequest" role="status">{{ evolutionRequest.status === 'running' ? '提交时任务运行中' : '提交时任务已排队' }} · 请求 {{ evolutionRequest.request_id }}。不会自动发布运行记忆。</p>
      <button class="ui-button ui-button--secondary ui-button--sm" :disabled="statusLoading || actionBusy" @click="refreshEvolutionStatus">{{ statusLoading ? '查询中…' : '查询复盘报告' }}</button>
      <RouterLink to="/admin/gateway" class="ui-button ui-button--ghost ui-button--sm">查看任务状态</RouterLink>
    </div>
    <EvolutionReviewPanel :review="evolutionReview" />

    <!-- Banner -->

    <!-- Navigation Tabs -->
    <AppCard
      class="flex flex-wrap items-center justify-between gap-3 p-1.5 rounded-xl border"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div class="flex flex-wrap gap-1">
        <button :disabled="actionBusy"
          @click="switchTab('settings')"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-sm font-sans font-bold cursor-pointer transition-colors"
          :style="
            activeTab === 'settings'
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { color: 'var(--text-muted)' }
          "
        >
          <Brain class="w-3.5 h-3.5" />
          <span>白盒心法与防污染总览</span>
        </button>
        <button :disabled="actionBusy"
          @click="switchTab('evolution_system')"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-sm font-sans font-bold cursor-pointer transition-colors"
          :style="
            activeTab === 'evolution_system'
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { color: 'var(--text-muted)' }
          "
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span>复盘官 System 模版</span>
        </button>
        <button :disabled="actionBusy"
          @click="switchTab('evolution_user')"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-lg text-sm font-sans font-bold cursor-pointer transition-colors"
          :style="
            activeTab === 'evolution_user'
              ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
              : { color: 'var(--text-muted)' }
          "
        >
          <Terminal class="w-3.5 h-3.5" />
          <span>战绩流水 User 模版</span>
        </button>
      </div>

      <div class="flex items-center space-x-2">
        <button
          v-if="auth.isSuperadmin"
          @click="triggerEvolutionNow"
          :disabled="(busy !== '') || actionBusy || !canManage"
          class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
          style="
            background-color: var(--color-brand-bg);
            border-color: var(--color-brand-border);
            color: var(--color-brand);
          "
        >
          <PlayCircle class="w-3.5 h-3.5" />
          <span>{{ busy === 'run' ? '正在提交...' : '提交复盘任务' }}</span>
        </button>
      </div>
    </AppCard>

    <!-- TAB 1: Settings & Structured White-Box Memory -->
    <div v-if="activeTab === 'settings'" class="space-y-4">
      <MemoryManagementPanel :publication="memoryPublication" :schedule="memorySchedule" @updated="memoryUpdated" />
    </div>

    <!-- TAB 2 & 3: Template Pipelines (Evolution System / User) -->
    <div v-else class="space-y-4">
      <AppCard
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors space-y-4"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div>
            <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">
              {{
                activeTab === 'evolution_system'
                  ? '自进化复盘官 System 提示词模版'
                  : '自进化战绩流水 User 提示词模版'
              }}
            </h2>
            <p class="text-sm font-sans mt-0.5" style="color: var(--text-muted)">
              {{
                activeTab === 'evolution_system'
                  ? '定义复盘官的角色定位、归因逻辑与心法沉淀标准'
                  : '配置按实际网关调度组装已平仓台账与可观察证据的模版语法'
              }}
            </p>
          </div>
          <button
            v-if="auth.isSuperadmin"
            @click="savePipelineModules"
            :disabled="(busy !== '') || actionBusy || !canManage"
            class="flex items-center space-x-1 px-4 py-2 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            <Save class="w-3.5 h-3.5" />
            <span>{{ busy === 'save' ? '保存中...' : '保存模版' }}</span>
          </button>
        </div>

        <!-- Modules List -->
        <div class="space-y-3">
          <div
            v-for="(mod, mIdx) in workingModules"
            :key="mod.id || mIdx"
            class="border rounded-xl p-4 transition-all"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <div class="flex items-center justify-between mb-2">
              <span class="text-sm font-bold font-sans" style="color: var(--text-main)">{{
                mod.title
              }}</span>
              <label class="flex items-center space-x-1.5 text-sm font-sans cursor-pointer">
                <input
                  v-model="mod.enabled"
                  @change="dirty = true"
                  type="checkbox"
                  class="accent-blue-500 w-3.5 h-3.5"
                  :disabled="(!auth.isSuperadmin) || actionBusy"
                />
                <span
                  :class="mod.enabled ? 'text-emerald-500 font-bold' : 'text-[var(--text-muted)]'"
                  >{{ mod.enabled ? '启用模块' : '已停用' }}</span
                >
              </label>
            </div>
            <textarea
              aria-label="复盘模块内容"
              v-model="mod.content"
              @input="dirty = true"
              :disabled="(!auth.isSuperadmin || mod.locked) || actionBusy"
              rows="6"
              class="w-full rounded-lg p-3 text-sm font-sans leading-relaxed outline-none border transition-colors resize-y"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            ></textarea>
          </div>
        </div>
      </AppCard>
    </div>
  </div>
</template>
