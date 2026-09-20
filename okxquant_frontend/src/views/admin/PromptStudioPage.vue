<script setup lang="ts">
import { useApiAction } from '../../composables/useApiAction'
const { action, actionBusy, canManage } = useApiAction(() => loading.value || loadFailed.value)

import AppField from '../../components/ui/AppField.vue'
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'

import AppDialog from '../../components/ui/AppDialog.vue'

import { useFeedback } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
import { onBeforeRouteLeave } from 'vue-router'
import { executionBinding, executionSummary, newProfilePackage, type ExecutionProfile } from '../../api/adminPrompt'
import { useApi } from '../../composables/useApi'
import { useAuthStore } from '../../stores/auth'
import {
  Plus,
  ArrowUp,
  ArrowDown,
  Eye,
  CheckCircle2,
  Save,
  ToggleLeft,
  ToggleRight,
  History,
  RotateCcw,
  Trash2,
  Copy,
  Download,
  Upload,
  FileUp,
  Sparkles,
  BookOpen,
  Layers,
} from 'lucide-vue-next'

const { api } = useApi()
const auth = useAuthStore()

const lib = ref<any>(null)
const loading = ref(true)
const loadFailed = ref(false)
const bannerMsg = useFeedback()

const selectedProfileId = ref<string>('')
const activePipeline = ref<'trading_system' | 'trading_user'>('trading_system')
const workingModules = ref<any[]>([])
const dirty = ref(false)
const historyVisible = ref(false)
const historyList = ref<any[]>([])
const createVisible = ref(false)
const newProfileName = ref('我的策略')
const newExecutionProfile = ref<ExecutionProfile>('standard')
const selectedExecution = ref<ExecutionProfile>('standard')
const bindingDirty = computed(() => selectedExecution.value !== executionBinding(selectedProfile.value))
const executionSettings = computed(() => (lib.value?.profiles || []).find((p: any) => p.execution_settings?.id === selectedExecution.value)?.execution_settings)
const hasDraft = computed(() => dirty.value || bindingDirty.value)
const historyLoading = ref(false)
const historyError = ref('')
const engineStatus = ref<{ engine_version: string; environment: string; enabled: boolean; authorized: boolean; status: string } | null>(null)
const engineLoading = ref(false)
const engineError = ref('')
let engineGeneration = 0
const engineStatusText = computed(() => ({ ready: '已就绪（不代表已成交）', disabled: '未启用', confirmation_required: '需要单独确认', binding_changed: '绑定已变化，请重新确认' }[engineStatus.value?.status || ''] || '状态待核验'))

// Import Modal State
const importVisible = ref(false)
const importRawJson = ref('')
const importNameOverride = ref('')
const importFileError = ref('')

// Template Variables & Guide State
const variableGuideVisible = ref(false)
const showVarRibbon = ref(false)
const activeEditingIdx = ref<number>(0)
const previewMode = ref<'rendered' | 'template'>('rendered')

const pipelines = [
  { id: 'trading_system', label: '交易 System', desc: '发给交易主脑的规则与决策纪律' },
  { id: 'trading_user', label: '交易 User', desc: '每轮拼装实时行情、动力学与决策任务' },
] as const

const selectedProfile = computed(
  () => (lib.value?.profiles || []).find((p: any) => p.id === selectedProfileId.value) || null,
)
const templateVariables = computed(() => lib.value?.template_variables || [])

const compiledPreview = computed(() => {
  if (previewMode.value === 'template') {
    return workingModules.value
      .filter((m) => m.enabled && String(m.content || '').trim())
      .map(
        (m) =>
          `======================= 【${m.title}】 =======================\n${String(m.content).trim()}`,
      )
      .join('\n\n')
  }
  if (!selectedProfile.value?.pipeline_views)
    return lib.value?.effective_templates?.[activePipeline.value] || ''
  return compileLocal()
})

function compileLocal(): string {
  return workingModules.value
    .filter((m) => m.enabled && String(m.content || '').trim())
    .map((m) => String(m.content).trim())
    .join('\n\n')
}

async function loadLib(preserveDraft = false) {
  loadFailed.value = false
  const hadProfile = !!selectedProfile.value
  loading.value = true
  try {
    lib.value = await api('/api/v1/admin/prompt-library')
    if (
      !selectedProfileId.value ||
      !(lib.value.profiles || []).some((p: any) => p.id === selectedProfileId.value)
    ) {
      selectedProfileId.value = lib.value.active_profile_id || lib.value.profiles?.[0]?.id || ''
    }
    if (!preserveDraft || !hadProfile) loadWorkingModules()
    void loadEngineStatus()
  } catch (e: any) {
    if (e?.silent) return
    loadFailed.value = true
    bannerMsg.value = { text: `加载失败：${e.message}`, type: 'err' }
  } finally {
    loading.value = false
  }
}

function loadWorkingModules() {
  const views = selectedProfile.value?.pipeline_views?.[activePipeline.value] || []
  workingModules.value = JSON.parse(JSON.stringify(views)).map((m: any) => ({
    ...m,
    locked: false, // 全量解锁，支持自由修改
  }))
  dirty.value = false
  selectedExecution.value = executionBinding(selectedProfile.value)
}

async function selectProfile(id: string) {
  if (id === selectedProfileId.value) return
  if (hasDraft.value && !(await confirm('当前修改尚未保存，切换方案将丢失修改。继续？'))) return
  selectedProfileId.value = id
  loadWorkingModules()
}

async function switchPipeline(id: any) {
  if (id === activePipeline.value) return
  if (hasDraft.value && !(await confirm('当前修改尚未保存，切换管线将丢失修改。继续？'))) return
  activePipeline.value = id
  loadWorkingModules()
}

function moveModule(idx: number, dir: -1 | 1) {
  const target = idx + dir
  if (target < 0 || target >= workingModules.value.length) return
  const arr = workingModules.value
  ;[arr[idx], arr[target]] = [arr[target], arr[idx]]
  activeEditingIdx.value = target
  dirty.value = true
}

function toggleModule(m: any) {
  m.enabled = !m.enabled
  dirty.value = true
}

// 在当前激活模块中一键插入变量占位符
function insertVarIntoActiveModule(key: string) {
  if (workingModules.value.length === 0) return
  const idx = Math.min(Math.max(0, activeEditingIdx.value), workingModules.value.length - 1)
  const m = workingModules.value[idx]
  const tag = `{{${key}}}`
  if (m.content && m.content.includes(tag)) {
    bannerMsg.value = { text: `模块「${m.title}」已包含变量 ${tag}`, type: 'warn' }
    return
  }
  m.content = m.content ? `${m.content.trim()}\n\n${tag}` : tag
  dirty.value = true
  bannerMsg.value = { text: `✅ 已插入变量插槽 ${tag} 到模块「${m.title}」`, type: 'ok' }
}

const saveProfile = action(async () => {
  try {
    const pipelinesMap: Record<string, any[]> = JSON.parse(JSON.stringify(selectedProfile.value.pipeline_views || selectedProfile.value.pipelines || {}))
    for (const p of pipelines) {
      pipelinesMap[p.id] =
        p.id === activePipeline.value
          ? workingModules.value
          : JSON.parse(JSON.stringify(selectedProfile.value.pipeline_views?.[p.id] || [])).map(
              (m: any) => ({ ...m, locked: false }),
            )
    }
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}`, {
      method: 'PUT',
      body: JSON.stringify({
        name: selectedProfile.value.name,
        description: selectedProfile.value.description || '',
        enabled: selectedProfile.value.enabled !== false,
        editor_mode: 'modules',
        pipelines: pipelinesMap,
      }),
    })
    bannerMsg.value = {
      text: selectedProfileId.value === lib.value.active_profile_id ? '提示词已保存，下一次新决策生效；旧仓保护保持。' : '提示词已保存，设为当前策略后用于下一次新决策。',
      type: 'ok',
    }
    dirty.value = false
    const pendingExecution = selectedExecution.value
    await loadLib()
    selectedExecution.value = pendingExecution
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `保存失败：${e.message}`, type: 'err' }
  }
})

const activateProfile = action(async () => {
  if (hasDraft.value) { bannerMsg.value = { text: '请先保存提示词和执行模式，再设为当前策略。', type: 'warn' }; return }
  if (!(await confirm(`设为当前策略？基础上限：${executionSummary(selectedProfile.value?.execution_settings)}。实际按当前环境的短线 / 波段模式执行，不代表实盘与模拟盘一致。下一次新决策生效，不触发立即推演；旧仓保护保持。`))) return
  try {
    await api(
      `/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/activate`,
      { method: 'POST', body: '{}' },
    )
    bannerMsg.value = { text: `已激活「${selectedProfile.value?.name}」，下一次新决策生效；旧仓保护保持。`, type: 'ok' }
    await loadLib()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `激活失败：${e.message}`, type: 'err' }
  }
})

const duplicateProfile = action(async () => {
  if (hasDraft.value && !(await confirm('复制已保存版本并切换方案？当前未保存修改会丢失。'))) return
  const name = await prompt('新方案名称：', `${selectedProfile.value?.name || ''} 副本`)
  if (!name) return
  try {
    const res = await api('/api/v1/admin/prompt-profiles', {
      method: 'POST',
      body: JSON.stringify({ name, description: '', source_id: selectedProfileId.value }),
    })
    bannerMsg.value = { text: `已复制为可编辑方案「${res.profile.name}」`, type: 'ok' }
    selectedProfileId.value = res.profile.id
    await loadLib()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `复制失败：${e.message}`, type: 'err' }
  }
})

const createProfile = action(async () => {
  if (!newProfileName.value.trim() || newProfileName.value.trim().length > 60) {
    bannerMsg.value = { text: '请输入 1–60 字的策略名称', type: 'err' }; return
  }
  if (hasDraft.value && !(await confirm('操作后将切换方案，当前未保存修改会丢失。继续？'))) return
  try {
    // The existing atomic import endpoint binds execution in the same write as creation.
    const source = await api('/api/v1/admin/prompt-profiles/stable/export')
    const res = await api('/api/v1/admin/prompt-profiles/import', {
      method: 'POST',
      body: JSON.stringify({ payload: newProfilePackage(source, newProfileName.value, newExecutionProfile.value), name_override: newProfileName.value.trim() }),
    })
    selectedProfileId.value = res.profile.id
    createVisible.value = false
    await loadLib()
    bannerMsg.value = { text: `已新建「${res.profile.name}」并绑定执行模式，尚未激活。`, type: 'ok' }
  } catch (e: any) {
    if (!e?.silent) bannerMsg.value = { text: `保存失败：${e.message}`, type: 'err' }
  }
})

const saveExecutionProfile = action(async () => {
  if (!selectedProfile.value || !bindingDirty.value) return
  if (!(await confirm(`保存执行模式？基础上限：${executionSummary(executionSettings.value)}。实际按当前环境的短线 / 波段模式执行，不代表实盘与模拟盘一致。不改写提示词；当前策略从下一次新决策生效，旧仓保护保持。`))) return
  try {
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}`, {
      method: 'PUT',
      body: JSON.stringify({ name: selectedProfile.value.name, execution_profile: selectedExecution.value }),
    })
    // Keep unsaved prompt modules while updating the independent execution binding.
    const settings = executionSettings.value
    selectedProfile.value.execution_profile = selectedExecution.value
    selectedProfile.value.execution_settings = settings
    await loadLib(true)
    bannerMsg.value = { text: selectedProfileId.value === lib.value.active_profile_id ? '执行模式已保存，下一次新决策生效；旧仓保护保持。' : '执行模式已保存，激活此策略后生效；提示词未改写。', type: 'ok' }
  } catch (e: any) {
    if (!e?.silent) bannerMsg.value = { text: `保存失败：${e.message}`, type: 'err' }
  }
})

async function loadEngineStatus() {
  if (!auth.isSuperadmin) return
  const generation = ++engineGeneration
  engineLoading.value = true
  engineError.value = ''
  try {
    const result = await api('/api/v1/admin/strategy/engine')
    if (generation === engineGeneration) engineStatus.value = result
  } catch (e: any) {
    if (generation !== engineGeneration) return
    engineStatus.value = null
    if (!e?.silent) engineError.value = e.message
  } finally { if (generation === engineGeneration) engineLoading.value = false }
}

const authorizeLiveEngine = action(async (enabled: boolean) => {
  if (engineLoading.value || engineStatus.value?.environment !== 'live') return
  const expectedBinding = { ...engineStatus.value }
  const expected = enabled ? 'ENABLE LIVE SCALP' : 'DISABLE LIVE SCALP'
  const phrase = await prompt(`${enabled ? '授权' : '撤销授权'}当前实盘账户的分钟策略。授权绑定当前账户、策略与风控，变更后需重新确认；不切换账户，不立即提交订单，不改变模拟盘或旧仓保护。实盘可能产生真实损益。请输入：${expected}`)
  if (!phrase) return
  if (phrase.trim() !== expected) { bannerMsg.value = { text: `确认词必须为：${expected}`, type: 'warn' }; return }
  try {
    await api('/api/v1/admin/strategy/engine/live-authorization', { method: 'POST', body: JSON.stringify({ enabled, confirmation: expected, expected_binding: expectedBinding }) })
    await loadEngineStatus()
    bannerMsg.value = { text: '实盘分钟策略授权设置已保存；未切换账户或立即提交订单，请核对运行状态。', type: 'ok' }
  } catch (e: any) {
    if (!e?.silent) bannerMsg.value = { text: `授权更新失败：${e.message}`, type: 'err' }
  }
})

function addModule() {
  workingModules.value.push({
    id: `module-${Date.now().toString(36)}`,
    title: `自定义规则模块 ${workingModules.value.length + 1}`,
    content: '',
    enabled: true,
    locked: false,
    source: 'custom',
  })
  activeEditingIdx.value = workingModules.value.length - 1
  dirty.value = true
}

async function removeModule(idx: number) {
  if (!(await confirm('确定删除该模块？'))) return
  workingModules.value.splice(idx, 1)
  if (activeEditingIdx.value >= workingModules.value.length) {
    activeEditingIdx.value = Math.max(0, workingModules.value.length - 1)
  }
  dirty.value = true
}

function duplicateModule(idx: number) {
  const m = workingModules.value[idx]
  if (!m) return
  workingModules.value.splice(idx + 1, 0, {
    ...JSON.parse(JSON.stringify(m)),
    id: `module-${Date.now().toString(36)}`,
    title: `${m.title} 副本`,
    locked: false,
    source: 'custom',
  })
  activeEditingIdx.value = idx + 1
  dirty.value = true
}

const deleteProfile = action(async () => {
  if (!(await confirm(`确定删除方案「${selectedProfile.value?.name}」？`))) return
  try {
    await api(`/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}`, {
      method: 'DELETE',
    })
    selectedProfileId.value = ''
    await loadLib()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `删除失败：${e.message}`, type: 'err' }
  }
})

async function showHistory() {
  historyVisible.value = true
  historyLoading.value = true
  historyError.value = ''
  historyList.value = []
  try {
    const res = await api(
      `/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/history`,
    )
    historyList.value = res.history || []
  } catch (e: any) {
    if (e?.silent) return
    historyError.value = e.message
    bannerMsg.value = { text: `历史加载失败：${e.message}`, type: 'err' }
  } finally {
    historyLoading.value = false
  }
}

const rollback = action(async (revId: string) => {
  if (!(await confirm('回滚将覆盖已保存方案及当前草稿，包括历史执行模式绑定。当前策略从下一次新决策生效；旧仓保护保持。继续？'))) return
  try {
    await api(
      `/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/rollback`,
      {
        method: 'POST',
        body: JSON.stringify({ revision_id: revId }),
      },
    )
    bannerMsg.value = { text: '已回滚到所选历史版本', type: 'ok' }
    historyVisible.value = false
    await loadLib()
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `回滚失败：${e.message}`, type: 'err' }
  }
})

// 导出策略方案为 JSON
async function exportProfile() {
  if (!selectedProfileId.value) return
  try {
    const res = await api(
      `/api/v1/admin/prompt-profiles/${encodeURIComponent(selectedProfileId.value)}/export`,
    )
    const blob = new Blob([JSON.stringify(res, null, 2)], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `okxquant-strategy-${selectedProfile.value?.name || 'profile'}-${new Date().toISOString().slice(0, 10)}.json`
    a.click()
    URL.revokeObjectURL(a.href)
    bannerMsg.value = {
      text: `✅ 方案「${selectedProfile.value?.name}」已成功导出为 JSON 策略包`,
      type: 'ok',
    }
  } catch (e: any) {
    if (e?.silent) return
    bannerMsg.value = { text: `导出失败：${e.message}`, type: 'err' }
  }
}

// 处理导入文件选择
function handleFileSelect(event: Event) {
  const file = (event.target as HTMLInputElement).files?.[0]
  if (!file) return
  importRawJson.value = ''
  importFileError.value = ''
  const reader = new FileReader()
  reader.onload = (e) => {
    try {
      const text = e.target?.result as string
      JSON.parse(text)
      importRawJson.value = text
      importFileError.value = ''
      if (!importNameOverride.value && file.name) {
        importNameOverride.value = file.name.replace(/\.json$/i, '').replace(/^okxquant-strategy-/, '')
      }
    } catch {
      importFileError.value = '文件内容不是合法的 JSON 格式'
    }
  }
  reader.onerror = () => { importFileError.value = '读取文件失败，请重新选择文件' }
  reader.readAsText(file)
}

// 提交导入
const submitImport = action(async () => {
  if (hasDraft.value && !(await confirm('操作后将切换方案，当前未保存修改会丢失。继续？'))) return
  importFileError.value = ''
  if (!importRawJson.value.trim()) {
    importFileError.value = '请先选择 JSON 策略文件或粘贴 JSON 内容'
    return
  }
  try {
    const payload = JSON.parse(importRawJson.value.trim())
    const res = await api('/api/v1/admin/prompt-profiles/import', {
      method: 'POST',
      body: JSON.stringify({
        payload,
        name_override: importNameOverride.value.trim() || undefined,
      }),
    })
    bannerMsg.value = { text: `🎉 成功导入策略方案「${res.profile.name}」！`, type: 'ok' }
    importVisible.value = false
    importRawJson.value = ''
    importNameOverride.value = ''
    selectedProfileId.value = res.profile.id
    await loadLib()
  } catch (e: any) {
    if (e?.silent) return
    importFileError.value = `导入失败：${e.message}`
  }
})

async function copyPreview() {
  try {
    await navigator.clipboard.writeText(compiledPreview.value)
    bannerMsg.value = { text: '提示词预览已复制', type: 'ok' }
  } catch {
    bannerMsg.value = { text: '复制失败，请手动选择预览文本复制。', type: 'err' }
  }
}
onBeforeRouteLeave(async () => !hasDraft.value || await confirm('当前修改未保存，离开页面将丢失修改。继续？'))

onMounted(() => { void loadLib(); void loadEngineStatus() })

const { confirm, prompt } = useDialogs()
</script>

<template>
  <div class="space-y-4 font-sans text-sm">
    <div v-if="loadFailed" role="alert" class="flex items-center justify-between gap-3 rounded-lg border p-3" style="border-color:var(--color-down-border);color:var(--text-main)"><span>页面加载失败，请重试。</span><button class="ui-button ui-button--secondary ui-button--sm" :disabled="loading || actionBusy" @click="loadLib(hasDraft)">重试</button></div>
    <AppDialog :busy="actionBusy" v-model:open="createVisible" title="新建策略" size="sm">
      <div class="space-y-4">
        <AppField label="策略名称" v-slot="field"><input :disabled="actionBusy" :id="field.id" v-model="newProfileName" maxlength="60" class="ui-input w-full" /></AppField>
        <AppField label="执行模式" v-slot="field"><select :disabled="actionBusy" :id="field.id" v-model="newExecutionProfile" class="ui-input w-full"><option value="standard">标准风控</option><option value="small300">300U 风险预算</option></select></AppField>
        <p class="text-sm" style="color:var(--text-muted)">基于稳健模板新建，明确绑定执行模式，不改写模板规则。新建后不会自动激活。</p>
      </div>
      <template #footer><button :disabled="actionBusy || !canManage" class="ui-button ui-button--primary" @click="createProfile">新建策略</button></template>
    </AppDialog>
    <!-- Header Summary & Plaza Gateway -->
    <div class="flex flex-col sm:flex-row sm:items-center justify-between gap-2.5 pb-1">
      <div class="flex items-center space-x-2">
        <Sparkles class="w-4 h-4 text-blue-400 shrink-0" />
        <p class="text-sm text-[var(--text-muted)] font-sans">
          编排交易提示词模块，右侧预览本地拼接结果；实时变量在下一次新决策时填入。
        </p>
      </div>
      <div class="flex items-center space-x-1.5 shrink-0">
        <button :disabled="actionBusy"
          @click="showVarRibbon = !showVarRibbon"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-sm font-sans transition-all cursor-pointer shadow-xs"
          :style="
            showVarRibbon
              ? {
                  backgroundColor: 'var(--color-brand-bg)',
                  borderColor: 'var(--color-brand-border)',
                  color: 'var(--color-brand)',
                  fontWeight: 'bold',
                }
              : {
                  backgroundColor: 'var(--bg-card-subtle)',
                  borderColor: 'var(--border-subtle)',
                  color: 'var(--text-muted)',
                }
          "
          title="展开/收起快捷变量标签条"
        >
          <Layers class="w-3.5 h-3.5" />
          <span>{{ showVarRibbon ? '收起变量条' : '插入变量' }}</span>
        </button>
        <button :disabled="actionBusy"
          @click="variableGuideVisible = true"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-sm font-sans transition-all cursor-pointer shadow-xs"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--color-brand);
          "
          title="查看所有可用数据插槽与变量字典"
        >
          <BookOpen class="w-3.5 h-3.5" />
          <span>变量字典</span>
        </button>
        <button :disabled="actionBusy"
          v-if="auth.isSuperadmin"
          @click="importVisible = true"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-sm font-sans transition-all cursor-pointer shadow-xs"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
          title="从本地文件或文本导入策略方案"
        >
          <Upload class="w-3.5 h-3.5" />
          <span>导入方案</span>
        </button>
        <button :disabled="actionBusy"
          @click="exportProfile"
          class="flex items-center space-x-1 px-2.5 py-1.5 rounded-lg border text-sm font-sans transition-all cursor-pointer shadow-xs"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
          title="将当前方案导出为 JSON 策略包"
        >
          <Download class="w-3.5 h-3.5" />
          <span>导出策略</span>
        </button>
      </div>
    </div>

    <!-- Collapsible Quick Variable Inserter Ribbon -->
    <AppCard
      v-if="showVarRibbon"
      class="rounded-xl border p-3 flex flex-wrap items-center gap-2 shadow-xs transition-colors"
      style="background-color: var(--bg-card); border-color: var(--border-subtle)"
    >
      <div
        class="flex items-center space-x-1.5 text-xs font-bold mr-1"
        style="color: var(--text-muted)"
      >
        <Layers class="w-3.5 h-3.5" style="color: var(--color-brand)" />
        <span>快捷变量插槽:</span>
      </div>
      <button :disabled="actionBusy"
        v-for="v in templateVariables"
        :key="v.key"
        @click="insertVarIntoActiveModule(v.key)"
        class="flex items-center space-x-1 px-2 py-1 rounded-lg border text-xs transition-all cursor-pointer shadow-xs hover:border-[var(--color-brand)]"
        style="
          background-color: var(--bg-card-subtle);
          border-color: var(--border-subtle);
          color: var(--text-main);
        "
        :title="`${v.description}\n点击插入到正在编辑的模块 #${activeEditingIdx + 1}`"
      >
        <span class="font-bold" style="color: var(--color-brand)">+</span>
        <span class="font-sans font-medium">{{ v.label }}</span>
        <code class="text-xs ml-0.5 font-sans opacity-60">&#123;&#123;{{ v.key }}&#125;&#125;</code>
      </button>
    </AppCard>

    <!-- Alert / Banner Message -->

    <AppCard class="rounded-xl border p-4 space-y-2" style="border-color:var(--border-subtle);background:var(--bg-card)">
      <h2 class="text-sm font-bold">分钟策略运行范围</h2>
      <p v-if="!auth.isSuperadmin" class="text-sm" style="color:var(--text-muted)">仅超级管理员可查看和调整运行授权。</p>
      <template v-else>
        <p v-if="engineLoading" class="text-sm">正在读取运行范围…</p>
        <div v-else-if="engineError" class="flex items-center gap-3 text-sm" role="alert"><span>运行范围加载失败：{{ engineError }}</span><button class="ui-button ui-button--secondary ui-button--sm" :disabled="actionBusy" @click="loadEngineStatus">重试</button></div>
        <template v-else-if="engineStatus">
          <p v-if="engineStatus.environment === 'demo'" class="text-sm">模拟盘{{ engineStatus.enabled ? '已启用' : '未启用' }}；实盘需切换账户后单独确认。{{ engineStatusText }}</p>
          <p v-else-if="engineStatus.environment === 'live'" class="text-sm">当前实盘 · {{ engineStatusText }} · {{ engineStatus.authorized ? '已有授权记录' : '尚未授权' }}</p>
          <p v-else class="text-sm">环境尚未核验，不能操作实盘授权。</p>
          <p class="text-xs" style="color:var(--text-muted)">引擎 {{ engineStatus.engine_version || '待核验' }}。授权绑定当前账户、策略与风控；实盘绑定变更后需重新确认。就绪不代表已成交，不改变模拟盘或旧仓保护。</p>
          <div v-if="engineStatus.environment === 'live'" class="flex flex-wrap gap-2">
            <button class="ui-button ui-button--secondary ui-button--sm" :disabled="actionBusy || engineLoading" @click="authorizeLiveEngine(true)">确认实盘授权</button>
            <button v-if="engineStatus.authorized" class="ui-button ui-button--secondary ui-button--sm" :disabled="actionBusy || engineLoading" @click="authorizeLiveEngine(false)">撤销实盘授权</button>
          </div>
        </template>
      </template>
    </AppCard>

    <!-- Loading State -->
    <LoadingState v-if="loading" />

    <!-- Main Workspace Grid -->
    <div
      v-else-if="lib"
      class="grid grid-cols-1 xl:grid-cols-[240px_minmax(0,1fr)_400px] gap-3.5 items-start"
    >
      <!-- Left: Profile List -->
      <AppCard
        class="rounded-xl border p-3 space-y-2 h-fit shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between px-1 pb-2 border-b"
          style="border-color: var(--border-subtle)"
        >
          <span class="text-xs font-bold uppercase tracking-wider" style="color: var(--text-faint)"
            >策略方案列表</span
          >
          <button :disabled="actionBusy"
            v-if="auth.isSuperadmin"
            @click="createVisible = true"
            class="flex items-center space-x-1 px-2 py-1 rounded-lg text-xs font-bold cursor-pointer shadow-xs transition-colors"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            <Plus class="w-3 h-3" />
            <span>新建方案</span>
          </button>
        </div>
        <div class="space-y-1.5 max-h-[calc(100vh-220px)] overflow-y-auto pr-0.5">
          <button :disabled="actionBusy"
            v-for="p in lib.profiles"
            :key="p.id"
            @click="selectProfile(p.id)"
            class="w-full text-left p-2.5 rounded-xl border transition-all cursor-pointer group shadow-xs"
            :style="
              selectedProfileId === p.id
                ? {
                    borderColor: 'var(--color-brand-border)',
                    backgroundColor: 'var(--color-brand-bg)',
                  }
                : { borderColor: 'var(--border-subtle)', backgroundColor: 'var(--bg-card-subtle)' }
            "
          >
            <div class="flex items-center justify-between">
              <span class="text-sm font-bold transition-colors" style="color: var(--text-main)">{{
                p.name
              }}</span>
              <span
                v-if="p.id === lib.active_profile_id"
                class="text-xs font-bold px-1.5 py-0.2 rounded border"
                style="
                  background-color: var(--color-up-bg);
                  color: var(--color-up);
                  border-color: var(--color-up-border);
                "
              >
                当前生效
              </span>
            </div>
            <div class="text-xs mt-1 line-clamp-1" style="color: var(--text-muted)">
              {{ p.description || '无详细描述' }}
            </div>
          </button>
        </div>
      </AppCard>

      <!-- Center: Modules Editor (100% Unlocked) -->
      <AppCard
        class="rounded-xl border p-4 min-w-0 shadow-xs space-y-3 transition-colors flex flex-col"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <section class="rounded-lg border p-3 text-sm space-y-2" style="border-color:var(--color-brand-border);background:var(--color-brand-bg)" data-execution-binding>
          <AppField label="执行模式（与提示词独立）" v-slot="field">
            <select :id="field.id" v-model="selectedExecution" class="ui-input w-full" :disabled="(!auth.isSuperadmin || selectedProfileId === 'small300') || actionBusy">
              <option value="standard">标准风控</option><option value="small300">300U 风险预算</option>
            </select>
          </AppField>
          <p>基础上限：{{ executionSummary(executionSettings) }}</p>
          <p class="text-xs">执行模式只绑定基础风控预算，不改写提示词。实际额度按当前环境的短线 / 波段模式执行，不代表实盘与模拟盘一致；交易周期以实际新决策及执行结果为准。下一次新决策生效，不立即触发推演；旧仓保护保持。</p>
          <p v-if="selectedProfileId === 'small300'" class="text-xs">内置 300U 方案固定绑定此模式；需切换模式请复制副本。</p>
          <button v-if="auth.isSuperadmin" class="ui-button ui-button--secondary ui-button--sm" :disabled="(!bindingDirty) || actionBusy || !canManage" @click="saveExecutionProfile">保存执行模式</button>
        </section>
        <!-- Pipeline Navigation Tabs -->
        <div
          class="flex items-center justify-between border-b pb-2"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex space-x-1.5">
            <button :disabled="actionBusy"
              v-for="p in pipelines"
              :key="p.id"
              @click="switchPipeline(p.id)"
              class="px-3.5 py-1.5 text-sm font-bold rounded-lg cursor-pointer transition-all"
              :style="
                activePipeline === p.id
                  ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
                  : { color: 'var(--text-muted)' }
              "
            >
              {{ p.label }}
            </button>
          </div>
          <span class="text-xs font-bold" style="color: var(--text-muted)">
            当前方案：<span style="color: var(--text-main)">{{ selectedProfile?.name }}</span>
          </span>
        </div>

        <!-- Module Cards List -->
        <div class="space-y-3 max-h-[calc(100vh-320px)] overflow-y-auto pr-1">
          <div
            v-for="(m, idx) in workingModules"
            :key="m.id"
            @click="activeEditingIdx = idx"
            class="border rounded-xl p-3.5 transition-all shadow-xs"
            :style="{
              backgroundColor: m.enabled ? 'var(--bg-card-subtle)' : 'var(--bg-card)',
              borderColor:
                activeEditingIdx === idx ? 'var(--color-brand-border)' : 'var(--border-subtle)',
              opacity: m.enabled ? '1' : '0.5',
            }"
            :class="activeEditingIdx === idx ? 'ring-1 ring-blue-500/30' : ''"
          >
            <div class="flex items-center justify-between mb-2 gap-2">
              <!-- Title & Ordering -->
              <div class="flex items-center space-x-2 min-w-0 flex-1">
                <span
                  class="w-5 h-5 rounded font-bold text-xs flex items-center justify-center shrink-0 border"
                  style="
                    background-color: var(--bg-card);
                    border-color: var(--border-subtle);
                    color: var(--text-muted);
                  "
                >
                  #{{ idx + 1 }}
                </span>
                <button
                  @click.stop="moveModule(idx, -1)"
                  :disabled="(idx === 0) || actionBusy"
                  class="p-1 rounded disabled:opacity-20 cursor-pointer transition-colors"
                  style="color: var(--text-muted)"
                  title="上移模块"
                >
                  <ArrowUp class="w-3.5 h-3.5" />
                </button>
                <button
                  @click.stop="moveModule(idx, 1)"
                  :disabled="(idx === workingModules.length - 1) || actionBusy"
                  class="p-1 rounded disabled:opacity-20 cursor-pointer transition-colors"
                  style="color: var(--text-muted)"
                  title="下移模块"
                >
                  <ArrowDown class="w-3.5 h-3.5" />
                </button>
                <input :disabled="actionBusy" aria-label="模块标题"
                  v-model="m.title"
                  class="bg-transparent border-b border-transparent focus:border-blue-500 text-sm font-bold font-sans outline-none flex-1 min-w-[120px] transition-colors"
                  style="color: var(--text-main)"
                  placeholder="模块标题"
                  @input="dirty = true"
                />
              </div>

              <!-- Controls: Copy, Delete, Toggle -->
              <div class="flex items-center space-x-1.5 shrink-0">
                <button :disabled="actionBusy"
                  @click.stop="duplicateModule(idx)"
                  class="p-1.5 rounded-lg cursor-pointer transition-colors"
                  style="color: var(--text-muted)"
                  title="复制模块"
                >
                  <Copy class="w-3.5 h-3.5" />
                </button>
                <button :disabled="actionBusy"
                  @click.stop="removeModule(idx)"
                  class="p-1.5 rounded-lg text-rose-400 hover:opacity-80 cursor-pointer transition-opacity"
                  title="删除模块"
                >
                  <Trash2 class="w-3.5 h-3.5" />
                </button>
                <button :disabled="actionBusy"
                  @click.stop="toggleModule(m)"
                  class="cursor-pointer transition-colors p-1"
                  :class="m.enabled ? 'text-emerald-500' : 'text-[var(--text-muted)]'"
                  :title="m.enabled ? '已启用该模块 (点击禁用)' : '已禁用该模块 (点击启用)'"
                >
                  <ToggleRight v-if="m.enabled" class="w-5 h-5" />
                  <ToggleLeft v-else class="w-5 h-5" />
                </button>
              </div>
            </div>

            <!-- Content Area (100% Editable) -->
            <textarea :disabled="actionBusy" aria-label="模块提示词内容"
              v-model="m.content"
              @focus="activeEditingIdx = idx"
              rows="5"
              class="w-full rounded-lg px-3 py-2 text-sm outline-none border resize-y leading-relaxed transition-colors select-text font-sans"
              style="
                background-color: var(--bg-input);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
              placeholder="编写该模块的提示词或插入 {{variable}} 数据插槽..."
              @input="dirty = true"
            ></textarea>
          </div>

          <!-- Add Module Button -->
          <button :disabled="actionBusy"
            @click="addModule"
            class="w-full py-2.5 rounded-xl border border-dashed text-sm cursor-pointer flex items-center justify-center space-x-1.5 transition-all shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-medium);
              color: var(--text-muted);
            "
          >
            <Plus class="w-4 h-4" style="color: var(--color-brand)" />
            <span>新增自定义规则模块</span>
          </button>
        </div>

        <!-- Action Bar -->
        <div
          class="flex flex-wrap items-center justify-between gap-2 pt-3 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex flex-wrap items-center gap-2">
            <button
              @click="saveProfile"
              :disabled="(!dirty || !auth.isSuperadmin) || actionBusy || !canManage"
              class="btn-primary-text flex items-center space-x-1.5 px-4 py-2 rounded-lg font-bold transition-all shadow-xs"
              :class="
                dirty
                  ? 'cursor-pointer hover:bg-blue-600 active:scale-95'
                  : 'opacity-40 cursor-not-allowed'
              "
              style="background-color: #2563eb; color: #ffffff !important"
            >
              <Save class="w-4 h-4" style="color: #ffffff" />
              <span style="color: #ffffff">保存当前方案{{ dirty ? ' *' : '' }}</span>
            </button>
            <button :disabled="actionBusy || !canManage"
              v-if="selectedProfileId !== lib.active_profile_id && auth.isSuperadmin"
              @click="activateProfile"
              class="btn-primary-text flex items-center space-x-1.5 px-3.5 py-2 rounded-lg font-bold cursor-pointer hover:bg-emerald-600 transition-all shadow-xs"
              style="background-color: #067647; color: #ffffff !important"
            >
              <CheckCircle2 class="w-4 h-4" style="color: #ffffff" />
              <span style="color: #ffffff">设为当前策略</span>
            </button>
          </div>

          <div class="flex flex-wrap items-center gap-2">
            <button :disabled="actionBusy || !canManage"
              v-if="auth.isSuperadmin"
              @click="duplicateProfile"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-sm font-sans font-bold cursor-pointer transition-all shadow-xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              <Copy class="w-3.5 h-3.5" />
              <span>复制副本</span>
            </button>
            <button :disabled="actionBusy"
              @click="showHistory"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-sm font-sans font-bold cursor-pointer transition-all shadow-xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              <History class="w-3.5 h-3.5" />
              <span>历史版本</span>
            </button>
            <button :disabled="actionBusy || !canManage"
              v-if="selectedProfileId !== lib.active_profile_id && auth.isSuperadmin"
              @click="deleteProfile"
              class="flex items-center space-x-1 px-3 py-2 rounded-lg border text-sm font-sans font-bold cursor-pointer transition-all shadow-xs"
              style="
                background-color: var(--color-down-bg);
                border-color: var(--color-down-border);
                color: var(--color-down);
              "
            >
              <Trash2 class="w-3.5 h-3.5" />
              <span>删除</span>
            </button>
          </div>
        </div>
      </AppCard>

      <!-- Right: Compiled Live Preview with Dual-Mode Toggle -->
      <AppCard
        class="rounded-xl border p-4 h-fit shadow-xs space-y-3 transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between pb-2 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <Eye class="w-4 h-4 text-cyan-400" />
            <h3
              class="text-sm font-bold uppercase tracking-wider font-sans"
              style="color: var(--text-main)"
            >
              提示词拼接预览
            </h3>
          </div>
          <div class="flex items-center space-x-1.5">
            <!-- Mode Switch -->
            <div
              class="flex p-0.5 rounded-lg border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <button :disabled="actionBusy"
                @click="previewMode = 'rendered'"
                class="px-2 py-0.5 rounded text-xs font-bold cursor-pointer transition-all"
                :style="
                  previewMode === 'rendered'
                    ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
                    : { color: 'var(--text-muted)' }
                "
              >
                拼接正文
              </button>
              <button :disabled="actionBusy"
                @click="previewMode = 'template'"
                class="px-2 py-0.5 rounded text-xs font-bold cursor-pointer transition-all"
                :style="
                  previewMode === 'template'
                    ? { backgroundColor: 'var(--text-main)', color: 'var(--bg-card)' }
                    : { color: 'var(--text-muted)' }
                "
              >
                模块与插槽
              </button>
            </div>
            <button :disabled="actionBusy"
              @click="copyPreview"
              class="px-2 py-1 rounded-lg border text-xs font-sans cursor-pointer transition-all shadow-xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              复制
            </button>
          </div>
        </div>
        <div
          class="text-xs flex items-center justify-between font-sans"
          style="color: var(--text-faint)"
        >
          <span>{{
            previewMode === 'rendered'
              ? '未填入实时行情，不代表历史实发内容'
              : '显示模块包含的原始模版语法与插槽'
          }}</span>
          <span class="num-tabular font-bold" style="color: var(--color-brand)"
            >{{ compiledPreview.length }} 字符</span
          >
        </div>
        <pre
          class="border rounded-xl p-3 text-xs font-sans whitespace-pre-wrap leading-relaxed max-h-[calc(100vh-280px)] overflow-y-auto select-text"
          style="
            background-color: var(--bg-card-subtle);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
          >{{ compiledPreview || '（空）' }}</pre
        >
      </AppCard>
    </div>

    <!-- Template Variables Guide Modal -->
    <AppDialog :busy="actionBusy"
      v-if="variableGuideVisible"
      :open="!!variableGuideVisible"
      title="提示词变量参考"
      size="xl"
      @update:open="
        (open) => {
          if (!open) {
            variableGuideVisible = false
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 space-y-4 transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2.5">
            <div
              class="w-7 h-7 rounded-lg flex items-center justify-center border"
              style="
                background-color: var(--color-brand-bg);
                border-color: var(--color-brand-border);
                color: var(--color-brand);
              "
            >
              <BookOpen class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-sm font-bold" style="color: var(--text-main)">
                系统数据插槽与变量字典
              </h3>
              <p class="text-xs" style="color: var(--text-muted)">
                可以在任意提示词模块中自由引用，系统推演时将自动替换为最新真实数据
              </p>
            </div>
          </div>

        </div>

        <div class="space-y-3">
          <div
            v-for="v in templateVariables"
            :key="v.key"
            class="p-3.5 rounded-xl border space-y-2 transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <div class="flex items-center justify-between">
              <div class="flex items-center space-x-2">
                <span
                  class="px-2 py-0.5 rounded text-xs font-bold border"
                  style="
                    background-color: var(--color-brand-bg);
                    border-color: var(--color-brand-border);
                    color: var(--color-brand);
                  "
                >
                  {{ v.category }}
                </span>
                <span class="text-sm font-bold" style="color: var(--text-main)">{{ v.label }}</span>
                <code
                  class="px-2 py-0.5 rounded border font-sans text-xs"
                  style="
                    background-color: var(--bg-badge);
                    border-color: var(--border-subtle);
                    color: var(--color-warn);
                  "
                >
                  &#123;&#123;{{ v.key }}&#125;&#125;
                </code>
              </div>
              <button :disabled="actionBusy"
                @click="() => {
                  insertVarIntoActiveModule(v.key)
                  variableGuideVisible = false
                 }"
                class="btn-primary-text px-2.5 py-1 rounded-lg font-bold text-xs cursor-pointer shadow-xs hover:bg-blue-600 transition-colors"
                style="background-color: #2563eb; color: #ffffff !important"
              >
                <span style="color: #ffffff">插入到当前模块</span>
              </button>
            </div>
            <p class="text-xs font-sans" style="color: var(--text-muted)">{{ v.description }}</p>
            <div
              v-if="v.sample"
              class="border rounded-lg p-2.5 text-xs font-sans whitespace-pre-wrap max-h-24 overflow-y-auto"
              style="
                background-color: var(--bg-card);
                border-color: var(--border-subtle);
                color: var(--text-muted);
              "
            >
              {{ v.sample }}
            </div>
          </div>
        </div>


      </div></AppDialog
    >

    <!-- Import Modal -->
    <AppDialog :busy="actionBusy"
      v-if="importVisible"
      :open="!!importVisible"
      title="导入策略方案"
      size="md"
      @update:open="
        (open) => {
          if (!open) {
            importVisible = false
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 space-y-4 transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2.5">
            <div
              class="w-7 h-7 rounded-lg flex items-center justify-center border"
              style="
                background-color: var(--color-brand-bg);
                border-color: var(--color-brand-border);
                color: var(--color-brand);
              "
            >
              <Upload class="w-4 h-4" />
            </div>
            <div>
              <h3 class="text-sm font-bold" style="color: var(--text-main)">导入策略方案包</h3>
              <p class="text-xs" style="color: var(--text-muted)">
                支持导入从本系统导出或从策略广场获取的 .json 策略方案
              </p>
            </div>
          </div>

        </div>

        <div
          v-if="importFileError"
          class="p-3 rounded-lg text-sm border"
          style="
            background-color: var(--color-down-bg);
            border-color: var(--color-down-border);
            color: var(--color-down);
          "
        >
          {{ importFileError }}
        </div>

        <div>
          <label class="block text-sm font-bold mb-1.5" style="color: var(--text-main)"
            >方式一：选择本地 .json 策略文件</label
          >
          <div class="flex items-center space-x-3">
            <label
              class="flex items-center space-x-2 px-3 py-2 rounded-xl border border-dashed text-sm cursor-pointer transition-all shadow-xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--color-brand);
              "
            >
              <FileUp class="w-4 h-4" />
              <span>选择策略文件 (.json)</span>
              <input :disabled="actionBusy" type="file" accept=".json" class="hidden" @change="handleFileSelect" />
            </label>
          </div>
        </div>

        <div>
          <AppField class="w-full min-w-0"><template #label><span class="block text-sm font-bold mb-1.5" style="color: var(--text-main)"
            >方式二：或直接粘贴策略 JSON 文本</span></template><template #default="{ id: fieldId }"><textarea :disabled="actionBusy" :id="fieldId"
            v-model="importRawJson"
            rows="6"
            class="w-full border rounded-xl px-3 py-2 text-sm outline-none resize-y font-sans transition-colors"
            style="
              background-color: var(--bg-input);
              border-color: var(--border-subtle);
              color: var(--text-main);
            "
            placeholder='{"format": "okxquant-prompt-profile", "version": 3, "profile": { ... }}'
          ></textarea></template></AppField>
        </div>

        <div>
          <AppField class="w-full min-w-0"><template #label><span class="block text-sm font-bold mb-1.5" style="color: var(--text-main)"
            >自定义导入方案名称（可选）</span></template><template #default="{ id: fieldId }"><input :disabled="actionBusy" :id="fieldId"
            v-model="importNameOverride"
            type="text"
            class="w-full border rounded-xl px-3 py-2 text-sm outline-none transition-colors"
            style="
              background-color: var(--bg-input);
              border-color: var(--border-subtle);
              color: var(--text-main);
            "
            placeholder="留空则自动采用策略包内部的原始名称"
          /></template></AppField>
        </div>

        <div
          class="flex items-center justify-end space-x-2 pt-3 border-t"
          style="border-color: var(--border-subtle)"
        >

          <button :disabled="actionBusy || !canManage"
            @click="submitImport"
            class="px-5 py-2 rounded-xl font-bold text-sm cursor-pointer transition-all shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            确认导入并载入方案
          </button>
        </div>
      </div></AppDialog
    >

    <!-- History Modal -->
    <AppDialog :busy="actionBusy"
      v-if="historyVisible"
      :open="!!historyVisible"
      title="方案版本历史"
      size="xl"
      @update:open="
        (open) => {
          if (!open) {
            historyVisible = false
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between mb-4 pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <h3 class="text-sm font-bold" style="color: var(--text-main)">
            版本历史 · {{ selectedProfile?.name }}
          </h3>

        </div>
        <LoadingState v-if="historyLoading" />
        <button :disabled="actionBusy" v-if="historyError" class="ui-button ui-button--secondary" @click="showHistory">加载失败，重试</button>
        <div
          v-if="!historyLoading && !historyError && historyList.length === 0"
          class="text-sm py-8 text-center"
          style="color: var(--text-muted)"
        >
          暂无历史版本
        </div>
        <div
          v-for="h in historyList"
          :key="h.id || h.revision_id"
          class="flex items-center justify-between py-2.5 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div>
            <div class="text-sm font-bold" style="color: var(--text-main)">
              {{ h.note || h.summary || h.id || h.revision_id }}
            </div>
            <div class="text-xs num-tabular" style="color: var(--text-faint)">
              {{ h.created_at || h.time }} · {{ h.actor || 'system' }}
            </div>
          </div>
          <button :disabled="actionBusy || !canManage"
            v-if="auth.isSuperadmin"
            @click="rollback(h.id || h.revision_id)"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs cursor-pointer transition-all shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <RotateCcw class="w-3 h-3" />
            <span>回滚</span>
          </button>
        </div>
      </div></AppDialog
    >
  </div>
</template>
