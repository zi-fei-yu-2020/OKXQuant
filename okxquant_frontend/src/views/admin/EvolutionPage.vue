<script setup lang="ts">
import AppCard from '../../components/ui/AppCard.vue'
import MemoryManagementPanel from '../../components/MemoryManagementPanel.vue'
import type { MemoryPublication } from '../../utils/memory'
import EvolutionReviewPanel from '../../components/EvolutionReviewPanel.vue'
import type { EvolutionReview } from '../../components/EvolutionReviewPanel.vue'

import { useFeedback, useToast } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
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
const busy = ref<'save' | 'run' | 'add' | 'delete' | 'toggle' | 'rollback' | ''>('')
const bannerMsg = useFeedback()

// Pipelines state (evolution_system & evolution_user)
const activeTab = ref<'settings' | 'evolution_system' | 'evolution_user'>('settings')
const lib = ref<any>(null)
const selectedProfileId = ref('stable')
const workingModules = ref<any[]>([])

// Structured White-Box Memory state
const memoryPublication = ref<MemoryPublication>()
const memorySchedule = ref<string | string[]>()
const evolutionReview = ref<EvolutionReview>()


const selectedProfile = computed(
  () => (lib.value?.profiles || []).find((p: any) => p.id === selectedProfileId.value) || null,
)

async function loadData() {
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
    bannerMsg.value = { text: `加载失败: ${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function memoryUpdated(res: any) { memoryPublication.value=res.publication; memorySchedule.value=res.self_improvement_schedule; evolutionReview.value=res.evolution_review }

function syncWorkingModules() {
  if (activeTab.value === 'settings') return
  const views = selectedProfile.value?.pipeline_views?.[activeTab.value] || []
  workingModules.value = JSON.parse(JSON.stringify(views))
}

function switchTab(tab: 'settings' | 'evolution_system' | 'evolution_user') {
  activeTab.value = tab
  syncWorkingModules()
}

async function savePipelineModules() {
  if (!selectedProfile.value) return
  busy.value = 'save'
  bannerMsg.value = null
  try {
    const pipelinesMap: Record<string, any[]> = {}
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
}

async function triggerEvolutionNow() {
  const phrase = await prompt(
    '立即强制执行自进化复盘任务（对全天战绩穿透提炼并生成最新复盘心法），请输入确认短语：RUN EVOLUTION',
  )
  if (!phrase) return
  if (phrase.trim().toUpperCase() !== 'RUN EVOLUTION') {
    toast.success('确认短语错误，已取消执行')
    return
  }
  busy.value = 'run'
  bannerMsg.value = null
  try {
    const res = await api('/api/v1/admin/gateway/jobs/self_improvement/run', {
      method: 'POST',
      body: JSON.stringify({ confirmation: 'RUN JOB' }),
    })
    bannerMsg.value = {
      text: `复盘任务请求已处理，请查看最近任务与报告状态；建议不会自动应用。${res.detail || ''}`,
      type: 'ok',
    }
    await loadData()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `执行复盘失败: ${e.message}`, type: 'err' }
  } finally {
    busy.value = ''
  }
}

onMounted(loadData)

const { prompt } = useDialogs()

const toast = useToast()
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
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

    <EvolutionReviewPanel :review="evolutionReview" />

    <!-- Banner -->

    <!-- Navigation Tabs -->
    <AppCard
      class="flex flex-wrap items-center justify-between gap-3 p-1.5 rounded-xl border"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div class="flex flex-wrap gap-1">
        <button
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
        <button
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
        <button
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
          :disabled="busy !== ''"
          class="flex items-center space-x-1 px-3 py-1.5 rounded-lg text-sm font-sans font-bold cursor-pointer disabled:opacity-40 transition-all shadow-xs"
          style="
            background-color: var(--color-brand-bg);
            border-color: var(--color-brand-border);
            color: var(--color-brand);
          "
        >
          <PlayCircle class="w-3.5 h-3.5" />
          <span>{{ busy === 'run' ? '正在执行复盘提炼...' : '防污染立即复盘' }}</span>
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
            :disabled="busy !== ''"
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
                  type="checkbox"
                  class="accent-blue-500 w-3.5 h-3.5"
                  :disabled="!auth.isSuperadmin"
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
              :disabled="!auth.isSuperadmin || mod.locked"
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
