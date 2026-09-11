<script setup lang="ts">
import AppSwitch from '../../components/ui/AppSwitch.vue'
import AppField from '../../components/ui/AppField.vue'
import AppDialog from '../../components/ui/AppDialog.vue'

import { useToast } from '../../composables/useFeedback'

import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import {
  Cpu,
  Plus,
  Trash2,
  CheckCircle2,
  AlertCircle,
  RefreshCw,
  Search,
  ArrowLeft,
  Settings,
  Layers,
  Eye,
  EyeOff,
  DownloadCloud,
  Wrench,
  Sparkles,
} from 'lucide-vue-next'

const { api } = useApi()

// State
const cfg = ref<any>(null)
const loading = ref(true)
const searchQuery = ref('')

// Navigation: 'list' (一级：供应商列表) | 'detail' (二级：供应商配置与模型详情)
const currentView = ref<'list' | 'detail'>('list')
const selectedProvider = ref<any>(null)
const detailTab = ref<'config' | 'models'>('config')

// Password visibility toggles
const showApiKey = ref(false)

// Edit / Add Provider Form
const providerForm = ref<any>({
  id: '',
  name: '',
  type: 'OpenAI',
  group: '其他',
  enabled: true,
  multi_key_enabled: false,
  response_api_enabled: false,
  base_url: '',
  api_key: '',
  api_path: '/chat/completions',
  description: '',
})

// Test Connection State
const testResult = ref<any>(null)
const testLoading = ref(false)
const testingModelId = ref<string | null>(null)

// Remote Fetch State & Modal
const fetchModalVisible = ref(false)
const fetchingRemote = ref(false)
const remoteFetchResult = ref<any>(null)
const remoteSearch = ref('')
const customFetchUrl = ref('')
const customFetchKey = ref('')

// Add / Edit Single Model Modal
const modelModalVisible = ref(false)
const editingModel = ref<any>(null)
const modelForm = ref<any>({
  id: '',
  name: '',
  provider_id: '',
  capabilities: ['chat'],
  reasoning_effort: 'high',
  context_length: 128000,
  description: '',
})

// ----------------- Data Loading -----------------
async function loadConfig() {
  loading.value = true
  try {
    cfg.value = await api('/api/v1/admin/llm/models')
    if (selectedProvider.value) {
      const updated = cfg.value.providers?.find((p: any) => p.id === selectedProvider.value.id)
      if (updated) {
        selectedProvider.value = updated
      }
    }
  } catch (e: any) {
    if (e?.silent) return
    toast.error(e.message)
  } finally {
    loading.value = false
  }
}

// Model Effort Options depending on model family
const availableEffortOptions = computed(() => {
  const mid = (modelForm.value.id || '').toLowerCase()
  // 智能自适应：支持 GPT-6、GPT-5 以及未来全系前沿具备极值推演能力的旗舰模型
  const supportsExtreme =
    mid.includes('gpt-6') ||
    mid.includes('gpt-5') ||
    mid.includes('o3') ||
    mid.includes('o4') ||
    mid.includes('ultra') ||
    mid.includes('max')

  const options = [
    { value: 'high', label: '高 (high)' },
    { value: 'medium', label: '中 (medium)' },
    { value: 'low', label: '低 (low)' },
    { value: 'none', label: '关闭 (none)' },
  ]
  if (supportsExtreme) {
    options.unshift(
      { value: 'max', label: '极值 (max)' },
      { value: 'xhigh', label: '超高 (xhigh)' },
    )
  }
  return options
})

// ----------------- Filtered Providers -----------------
const filteredProviders = computed(() => {
  if (!cfg.value?.providers) return []
  const q = searchQuery.value.trim().toLowerCase()
  if (!q) return cfg.value.providers
  return cfg.value.providers.filter(
    (p: any) =>
      p.name.toLowerCase().includes(q) ||
      (p.type && p.type.toLowerCase().includes(q)) ||
      (p.group && p.group.toLowerCase().includes(q)) ||
      (p.id && p.id.toLowerCase().includes(q)),
  )
})

// ----------------- Provider Actions -----------------
function openAddProviderModal() {
  selectedProvider.value = { id: '', name: '新建自定义供应商', is_new: true }
  providerForm.value = {
    id: '',
    name: '',
    type: 'OpenAI 兼容',
    group: '自定义',
    enabled: true,
    multi_key_enabled: false,
    response_api_enabled: false,
    api_format: 'openai_chat',
    base_url: '',
    api_key: '',
    api_path: '/chat/completions',
    description: '',
  }
  detailTab.value = 'config'
  currentView.value = 'detail'
  testResult.value = null
  showApiKey.value = false
}

function selectProvider(p: any) {
  selectedProvider.value = p
  const format = p.api_format || (p.id === 'claude' ? 'claude_messages' : 'openai_chat')
  providerForm.value = {
    id: p.id,
    name: p.name,
    type: p.type || p.name,
    group: p.group || '其他',
    enabled: !!p.enabled,
    multi_key_enabled: !!p.multi_key_enabled,
    response_api_enabled: !!p.response_api_enabled,
    api_format: format,
    base_url: p.base_url || '',
    api_key: '',
    api_path:
      p.api_path ||
      (format === 'claude_messages'
        ? '/messages'
        : format === 'openai_responses'
          ? '/responses'
          : '/chat/completions'),
    description: p.description || '',
  }
  detailTab.value = 'config'
  currentView.value = 'detail'
  testResult.value = null
  showApiKey.value = false
}

function onApiFormatChange() {
  const fmt = providerForm.value.api_format
  if (fmt === 'claude_messages') {
    if (
      !providerForm.value.api_path ||
      providerForm.value.api_path === '/chat/completions' ||
      providerForm.value.api_path === '/responses'
    ) {
      providerForm.value.api_path = '/messages'
    }
    providerForm.value.response_api_enabled = false
  } else if (fmt === 'openai_responses') {
    if (
      !providerForm.value.api_path ||
      providerForm.value.api_path === '/chat/completions' ||
      providerForm.value.api_path === '/messages'
    ) {
      providerForm.value.api_path = '/responses'
    }
    providerForm.value.response_api_enabled = true
  } else {
    if (
      !providerForm.value.api_path ||
      providerForm.value.api_path === '/messages' ||
      providerForm.value.api_path === '/responses'
    ) {
      providerForm.value.api_path = '/chat/completions'
    }
    providerForm.value.response_api_enabled = false
  }
}

function goBackToList() {
  currentView.value = 'list'
  selectedProvider.value = null
  testResult.value = null
}

async function toggleProviderQuick(p: any, e: Event) {
  e.stopPropagation()
  try {
    const res = await api(`/api/v1/admin/llm/providers/${encodeURIComponent(p.id)}/toggle`, {
      method: 'POST',
      body: JSON.stringify({ enabled: !p.enabled }),
    })
    p.enabled = res.enabled
    await loadConfig()
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

async function saveProviderConfig() {
  try {
    const payload = { ...providerForm.value }
    if (!payload.id) {
      payload.id = payload.name
        .trim()
        .toLowerCase()
        .replace(/[^a-z0-9_-]/g, '_')
    }
    if (!payload.api_key) delete payload.api_key
    await api('/api/v1/admin/llm/providers', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    toast.success('供应商配置已成功保存！')
    await loadConfig()
    if (selectedProvider.value?.is_new) {
      const created = cfg.value.providers?.find((p: any) => p.id === payload.id)
      if (created) {
        selectedProvider.value = created
      }
    }
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

async function clearCurrentProviderModels() {
  if (!selectedProvider.value) return
  if (!(await confirm(`确定要清空 ${selectedProvider.value.name} 旗下的全部模型吗？`))) return
  try {
    await api(
      `/api/v1/admin/llm/providers/${encodeURIComponent(selectedProvider.value.id)}/models`,
      {
        method: 'DELETE',
      },
    )
    toast.success('已清空该供应商所有模型！')
    await loadConfig()
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

// ----------------- Remote Fetch -----------------
function openFetchDialog() {
  if (!selectedProvider.value) return
  customFetchUrl.value = selectedProvider.value.base_url || ''
  customFetchKey.value = ''
  remoteFetchResult.value = null
  remoteSearch.value = ''
  fetchModalVisible.value = true
  // 优化：若当前供应商已配置好 Base URL，弹窗打开时自动发起探测拉取，免除重复输入与多次点击
  executeRemoteFetch()
}

async function executeRemoteFetch() {
  if (!selectedProvider.value) return
  fetchingRemote.value = true
  remoteFetchResult.value = null
  try {
    const payload: any = {
      provider_id: selectedProvider.value.id,
      base_url: customFetchUrl.value.trim() || selectedProvider.value.base_url,
    }
    if (customFetchKey.value.trim()) {
      payload.api_key = customFetchKey.value.trim()
    }
    const res = await api('/api/v1/admin/llm/fetch-models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    remoteFetchResult.value = res
  } catch (err: any) {
    if (err?.silent) return
    remoteFetchResult.value = { ok: false, error: err.message }
  } finally {
    fetchingRemote.value = false
  }
}

const filteredRemoteModels = computed(() => {
  if (!remoteFetchResult.value?.models) return []
  const q = remoteSearch.value.trim().toLowerCase()
  if (!q) return remoteFetchResult.value.models
  return remoteFetchResult.value.models.filter(
    (m: any) => m.id.toLowerCase().includes(q) || (m.name && m.name.toLowerCase().includes(q)),
  )
})

async function importRemoteModel(m: any, autoActivate = false) {
  if (!selectedProvider.value) return
  try {
    const payload = {
      id: m.id,
      name: m.name || m.id,
      provider_id: selectedProvider.value.id,
      provider_name: selectedProvider.value.name,
      base_url: selectedProvider.value.base_url,
      api_format: m.api_format || selectedProvider.value.api_format || 'openai_chat',
      reasoning_type: m.reasoning_type || 'auto',
      reasoning_effort: m.default_effort || 'high',
      capabilities: m.capabilities || ['chat'],
      context_length: m.context_length,
      description: m.description ? m.description.slice(0, 100) : '从远端一键自动收录',
    }
    await api('/api/v1/admin/llm/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (autoActivate) {
      await api('/api/v1/admin/llm/activate', {
        method: 'POST',
        body: JSON.stringify({ model_id: m.id, reasoning_effort: payload.reasoning_effort }),
      })
    }
    await loadConfig()
    toast.success(autoActivate ? `已收录并激活主脑为 ${m.id}！` : `已成功添加 ${m.id} 到模型列表！`)
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

async function importAllFilteredRemoteModels() {
  if (!selectedProvider.value || !filteredRemoteModels.value.length) return
  const list = filteredRemoteModels.value
  let successCount = 0
  for (const m of list) {
    try {
      const payload = {
        id: m.id,
        name: m.name || m.id,
        provider_id: selectedProvider.value.id,
        provider_name: selectedProvider.value.name,
        base_url: selectedProvider.value.base_url,
        api_format: m.api_format || selectedProvider.value.api_format || 'openai_chat',
        reasoning_type: m.reasoning_type || 'auto',
        reasoning_effort: m.default_effort || 'high',
        capabilities: m.capabilities || ['chat'],
        context_length: m.context_length,
        description: m.description ? m.description.slice(0, 100) : '从远端一键自动收录',
      }
      await api('/api/v1/admin/llm/models', {
        method: 'POST',
        body: JSON.stringify(payload),
      })
      successCount++
    } catch (e: any) {
      if (e?.silent) return
      console.warn('Import model failed:', m.id, e)
    }
  }
  await loadConfig()
  toast.success(`成功批量收录 ${successCount} 个模型到 ${selectedProvider.value.name}！`)
}

// ----------------- Model Management -----------------
function openAddModelModal() {
  if (!selectedProvider.value) return
  editingModel.value = null
  modelForm.value = {
    id: '',
    name: '',
    provider_id: selectedProvider.value.id,
    capabilities: ['chat'],
    reasoning_effort: 'high',
    context_length: 128000,
    description: '',
  }
  modelModalVisible.value = true
}

function openEditModelModal(m: any) {
  editingModel.value = m
  modelForm.value = {
    id: m.id,
    name: m.name || m.id,
    provider_id: selectedProvider.value?.id || m.provider_id,
    capabilities: m.capabilities || ['chat'],
    reasoning_effort: m.reasoning_effort || 'high',
    context_length: m.context_length || 128000,
    description: m.description || '',
  }
  modelModalVisible.value = true
}

async function saveModelForm() {
  if (!selectedProvider.value) return
  try {
    const payload = {
      ...modelForm.value,
      provider_id: selectedProvider.value.id,
      provider_name: selectedProvider.value.name,
      base_url: selectedProvider.value.base_url,
      api_format: selectedProvider.value.api_format || 'openai_chat',
    }
    await api('/api/v1/admin/llm/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    modelModalVisible.value = false
    await loadConfig()
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

async function activateModel(m: any) {
  try {
    await api('/api/v1/admin/llm/activate', {
      method: 'POST',
      body: JSON.stringify({
        model_id: m.id,
        provider_id: selectedProvider.value?.id || m.provider_id,
        reasoning_effort: m.reasoning_effort || 'high',
      }),
    })
    await loadConfig()
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

async function deleteSingleModel(modelId: string) {
  if (!(await confirm(`确定删除模型 ${modelId} 吗？`))) return
  try {
    const providerId = selectedProvider.value?.id
    const modelPath = providerId
      ? `/api/v1/admin/llm/providers/${encodeURIComponent(providerId)}/models/${encodeURIComponent(modelId)}`
      : `/api/v1/admin/llm/models/${encodeURIComponent(modelId)}`
    await api(modelPath, {
      method: 'DELETE',
    })
    await loadConfig()
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
}

// ----------------- Test Connection -----------------
async function runTestModel(m: any) {
  testLoading.value = true
  testingModelId.value = m.id
  testResult.value = null
  try {
    const prov =
      selectedProvider.value || cfg.value?.providers?.find((p: any) => p.id === m.provider_id)
    testResult.value = await api('/api/v1/admin/llm/test', {
      method: 'POST',
      body: JSON.stringify({
        model: m.id,
        provider_id: prov?.id || m.provider_id,
        base_url: prov?.base_url || m.base_url,
        api_format: prov?.api_format || m.api_format || 'openai_chat',
        reasoning_effort: m.reasoning_effort || 'auto',
      }),
    })
  } catch (e: any) {
    if (e?.silent) return
    testResult.value = { ok: false, error: e.message }
  } finally {
    testLoading.value = false
    testingModelId.value = null
  }
}

function toggleCapability(cap: string) {
  const caps = modelForm.value.capabilities
  const idx = caps.indexOf(cap)
  if (idx > -1) {
    caps.splice(idx, 1)
  } else {
    caps.push(cap)
  }
}

onMounted(() => {
  loadConfig()
})

const { confirm } = useDialogs()

const toast = useToast()
</script>

<template>
  <div class="space-y-4 max-w-4xl mx-auto font-sans text-sm">
    <!-- VIEW 1: 供应商列表页 (对应截图 1) -->
    <template v-if="currentView === 'list'">
      <!-- Top Title & Navigation Bar -->
      <div
        class="rounded-2xl border p-4 sm:p-5 flex items-center justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div class="flex items-center space-x-3">
          <div
            class="w-10 h-10 rounded-xl flex items-center justify-center border shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-subtle);
              color: var(--color-brand);
            "
          >
            <Cpu class="w-5 h-5" />
          </div>
          <div>
            <h2
              class="text-base sm:text-lg font-bold tracking-tight"
              style="color: var(--text-main)"
            >
              供应商
            </h2>
            <p class="text-xs" style="color: var(--text-muted)">
              管理 AI 模型渠道矩阵与 API 密钥直连配置
            </p>
          </div>
        </div>

        <!-- Right Quick Actions -->
        <div class="flex items-center space-x-2">
          <button
            @click="openAddProviderModal"
            class="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl border text-sm font-bold cursor-pointer transition-all hover:opacity-90 btn-primary-text"
            style="background-color: #2563eb; color: #ffffff"
            title="添加自定义供应商"
          >
            <Plus class="w-4 h-4" />
            <span>添加供应商</span>
          </button>

          <button
            @click="loadConfig"
            class="p-2 rounded-xl border text-sm cursor-pointer transition-all hover:opacity-80"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-subtle);
              color: var(--text-muted);
            "
            title="刷新状态"
          >
            <RefreshCw class="w-4 h-4" :class="loading ? 'animate-spin' : ''" />
          </button>
        </div>
      </div>

      <!-- Search Box (对应截图 1 顶部的搜索栏) -->
      <div class="relative">
        <input
          aria-label="搜索供应商或分组"
          v-model="searchQuery"
          placeholder="搜索供应商或分组"
          class="w-full rounded-2xl px-4 py-3 pl-11 text-sm outline-none border transition-colors shadow-xs"
          style="
            background-color: var(--bg-card);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
        />
        <Search
          class="w-4 h-4 absolute left-4 top-3.5 text-[var(--text-muted)] pointer-events-none"
        />
      </div>

      <!-- Providers List Container -->
      <div
        class="rounded-2xl border overflow-hidden shadow-xs divide-y transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          v-for="prov in filteredProviders"
          :key="prov.id"
          @click="selectProvider(prov)"
          class="p-4 flex items-center justify-between hover:bg-[var(--bg-card-subtle)] transition-colors cursor-pointer group"
          style="border-color: var(--border-subtle)"
        >
          <!-- Left: Provider Logo / Icon & Name -->
          <div class="flex items-center space-x-3.5">
            <!-- Icon Avatar -->
            <div
              class="w-10 h-10 rounded-xl flex items-center justify-center border font-bold text-sm shrink-0 transition-transform group-hover:scale-105"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span v-if="prov.id === 'openai'" class="text-emerald-500">❖</span>
              <span v-else-if="prov.id === 'siliconflow'" class="text-purple-500">⚡</span>
              <span v-else-if="prov.id === 'gemini'" class="text-blue-500">✦</span>
              <span v-else-if="prov.id === 'openrouter'" class="text-indigo-500">◈</span>
              <span v-else-if="prov.id === 'deepseek'" class="text-sky-500">🐳</span>
              <span v-else-if="prov.id === 'claude'" class="text-amber-500">✳</span>
              <span v-else-if="prov.id === 'grok'" class="text-neutral-300">Ø</span>
              <span v-else-if="prov.id === 'volcengine'" class="text-cyan-500">📶</span>
              <span v-else-if="prov.id === 'dashscope'" class="text-orange-500">[-]</span>
              <span v-else-if="prov.id === 'zhipu'" class="text-violet-500">◆</span>
              <span v-else class="text-blue-400">❖</span>
            </div>

            <!-- Provider Name & Subtitle -->
            <div>
              <div class="flex items-center space-x-2">
                <span class="font-bold text-sm" style="color: var(--text-main)">{{
                  prov.name
                }}</span>
                <span
                  v-if="prov.id === cfg?.active_provider_id && prov.models?.some((m: any) => m.id === cfg?.active_model_id)"
                  class="px-1.5 py-0.2 rounded text-xs font-bold border"
                  style="
                    background-color: var(--color-up-bg);
                    border-color: var(--color-up-border);
                    color: var(--color-up);
                  "
                >
                  主脑活跃
                </span>
              </div>
              <div class="text-xs mt-0.5" style="color: var(--text-faint)">
                {{ prov.models_count || 0 }} 个模型 · {{ prov.group || '其他' }}
              </div>
            </div>
          </div>

          <!-- Right: Enable / Disable Badge & Chevron Arrow (对齐截图 1) -->
          <div class="flex items-center space-x-2.5">
            <!-- Capsule Status Button -->
            <button
              @click="toggleProviderQuick(prov, $event)"
              class="px-3 py-1 rounded-full text-sm font-semibold border transition-all cursor-pointer shadow-2xs"
              :style="
                prov.enabled
                  ? {
                      backgroundColor: 'var(--color-up-bg)',
                      borderColor: 'var(--color-up-border)',
                      color: 'var(--color-up)',
                    }
                  : {
                      backgroundColor: 'var(--color-down-bg)',
                      borderColor: 'var(--color-down-border)',
                      color: 'var(--color-down)',
                    }
              "
            >
              {{ prov.enabled ? '启用' : '禁用' }}
            </button>

            <!-- Arrow Right -->
            <span class="text-[var(--text-muted)] font-bold text-base select-none">›</span>
          </div>
        </div>
      </div>
    </template>

    <!-- VIEW 2: 供应商详情管理 (对应截图 2 配置 & 截图 3 模型) -->
    <template v-else-if="currentView === 'detail' && selectedProvider">
      <!-- Detail Top Navigation Bar -->
      <div
        class="rounded-2xl border p-4 flex items-center justify-between shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <button
          @click="goBackToList"
          class="flex items-center space-x-1.5 px-3 py-1.5 rounded-xl border text-sm font-bold cursor-pointer transition-all hover:bg-[var(--bg-card-subtle)]"
          style="
            background-color: var(--bg-card);
            border-color: var(--border-subtle);
            color: var(--text-main);
          "
        >
          <ArrowLeft class="w-4 h-4" />
          <span>返回</span>
        </button>

        <div class="flex items-center space-x-2">
          <div
            class="w-7 h-7 rounded-lg flex items-center justify-center font-bold text-sm"
            style="background-color: var(--bg-card-subtle); color: var(--color-brand)"
          >
            ❖
          </div>
          <span class="font-bold text-sm sm:text-base" style="color: var(--text-main)">
            {{ selectedProvider.name }}
          </span>
        </div>

        <div class="w-16"></div>
      </div>

      <!-- SUB-VIEW A: 「配置」Tab (对齐截图 2) -->
      <div
        v-if="detailTab === 'config'"
        class="space-y-4 rounded-2xl border p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <!-- Section 1: 管理设置项列表 -->
        <div class="space-y-1">
          <div
            class="text-xs font-bold uppercase tracking-wider mb-2"
            style="color: var(--text-muted)"
          >
            管理
          </div>

          <div
            class="rounded-xl border divide-y overflow-hidden text-sm"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <!-- 供应商类型 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main)">供应商类型</span>
              <div class="flex items-center space-x-1" style="color: var(--text-muted)">
                <span>{{ providerForm.type }}</span>
                <span class="text-[var(--text-muted)]">›</span>
              </div>
            </div>

            <!-- API 交互协议类型 (下拉选择) -->
            <div class="p-3.5 flex items-center justify-between">
              <div>
                <span class="font-medium" style="color: var(--text-main)">API 交互协议</span>
                <div class="text-xs" style="color: var(--text-faint)">
                  选择该端点底层支持的通信协议标准
                </div>
              </div>
              <select
                aria-label="API 交互协议"
                v-model="providerForm.api_format"
                @change="onApiFormatChange"
                class="rounded-lg px-2.5 py-1.5 text-sm font-sans outline-none border cursor-pointer max-w-[200px]"
                style="
                  background-color: var(--bg-card);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              >
                <option value="openai_chat">OpenAI Chat (/chat/completions)</option>
                <option value="claude_messages">Claude Messages (/messages)</option>
                <option value="openai_responses">OpenAI Responses (/responses)</option>
              </select>
            </div>

            <!-- 分组 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main)">分组</span>
              <div class="flex items-center space-x-1" style="color: var(--text-muted)">
                <span>{{ providerForm.group }}</span>
                <span class="text-[var(--text-muted)]">›</span>
              </div>
            </div>

            <!-- 是否启用开关 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main)">是否启用</span>
              <AppSwitch v-model="providerForm.enabled" label="启用供应商" />
            </div>

            <!-- 多Key模式开关 -->
            <div class="p-3.5 flex items-center justify-between">
              <span class="font-medium" style="color: var(--text-main)">多Key模式</span>
              <AppSwitch v-model="providerForm.multi_key_enabled" label="多 Key 模式" />
            </div>
          </div>
        </div>

        <!-- Section 2: 凭据与输入表单区 (对应截图 2 底部字段) -->
        <div class="space-y-3 pt-2">
          <!-- 供应商唯一标识 ID (仅新建自定义供应商时展示) -->
          <div v-if="selectedProvider.is_new">
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-sm font-bold mb-1.5" style="color: var(--text-muted)"
                  >供应商唯一标识 (ID)</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="providerForm.id"
                  placeholder="例如: openrouter 或 my-proxy"
                  class="w-full rounded-xl px-4 py-2.5 text-sm outline-none border transition-colors font-sans"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>

          <!-- 名称 -->
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-sm font-bold mb-1.5" style="color: var(--text-muted)"
                  >名称</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="providerForm.name"
                  placeholder="OpenAI"
                  class="w-full rounded-xl px-4 py-2.5 text-sm outline-none border transition-colors"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>

          <!-- API Key -->
          <div>
            <div class="flex items-center justify-between mb-1.5">
              <label
                class="text-sm font-bold"
                style="color: var(--text-muted)"
                for="provider-api-key"
                >API Key</label
              >
              <span v-if="selectedProvider.has_key" class="text-xs text-emerald-500 font-bold">
                ✓ 密钥已就绪
              </span>
            </div>
            <div class="relative">
              <input
                aria-label="供应商 API Key"
                id="provider-api-key"
                v-model="providerForm.api_key"
                :type="showApiKey ? 'text' : 'password'"
                placeholder="••••••••••••••••••••••••"
                class="w-full rounded-xl px-4 py-2.5 pr-10 text-sm outline-none border transition-colors font-sans"
                style="
                  background-color: var(--bg-card-subtle);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              />
              <button
                type="button"
                @click="showApiKey = !showApiKey"
                :aria-label="showApiKey ? '隐藏 API Key' : '显示 API Key'"
                class="absolute right-3 top-2.5 text-[var(--text-muted)] hover:text-[var(--text-main)] cursor-pointer"
              >
                <EyeOff v-if="showApiKey" class="w-4 h-4" />
                <Eye v-else class="w-4 h-4" />
              </button>
            </div>
          </div>

          <!-- API Base URL -->
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-sm font-bold mb-1.5" style="color: var(--text-muted)"
                  >API Base URL</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="providerForm.base_url"
                  placeholder="https://api.openai.com/v1"
                  class="w-full rounded-xl px-4 py-2.5 text-sm outline-none border transition-colors font-sans"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>

          <!-- API 路径 -->
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-sm font-bold mb-1.5" style="color: var(--text-muted)"
                  >API 路径</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="providerForm.api_path"
                  placeholder="/chat/completions"
                  class="w-full rounded-xl px-4 py-2.5 text-sm outline-none border transition-colors font-sans"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>
        </div>

        <!-- Save Button -->
        <div class="pt-3 pb-16 flex justify-end">
          <button
            @click="saveProviderConfig"
            class="px-6 py-2 rounded-xl text-sm font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
            style="background-color: #2563eb; color: #ffffff"
          >
            保存供应商配置
          </button>
        </div>
      </div>

      <!-- SUB-VIEW B: 「模型」Tab (完美还原原生截图排版) -->
      <div v-else-if="detailTab === 'models'" class="space-y-4">
        <!-- Models List Container -->
        <div
          class="rounded-3xl border divide-y overflow-hidden shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            v-for="m in selectedProvider.models"
            :key="m.id"
            class="p-4 sm:p-5 flex items-center justify-between hover:bg-[var(--bg-card-subtle)] transition-colors group"
            style="border-color: var(--border-subtle)"
          >
            <!-- Left: Sparkle Avatar + Model Title + Badges -->
            <div class="flex items-start sm:items-center space-x-3.5 min-w-0 pr-3">
              <!-- Avatar: 经典彩色四角星 Sparkle 图标 -->
              <div
                class="w-10 h-10 rounded-full flex items-center justify-center shrink-0 shadow-2xs border"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
              >
                <Sparkles class="w-5 h-5 text-indigo-400" />
              </div>

              <!-- Content Area -->
              <div class="min-w-0">
                <!-- Model ID & Status Badge -->
                <div class="flex flex-wrap items-center gap-2">
                  <span
                    class="font-bold text-sm tracking-tight truncate max-w-[200px] sm:max-w-md font-sans"
                    style="color: var(--text-main)"
                  >
                    {{ m.id }}
                  </span>
                  <span
                    v-if="m.id === cfg?.active_model_id && selectedProvider?.id === cfg?.active_provider_id"
                    class="px-2 py-0.5 rounded-full text-xs font-bold border"
                    style="
                      background-color: var(--color-up-bg);
                      border-color: var(--color-up-border);
                      color: var(--color-up);
                    "
                  >
                    主脑生效
                  </span>
                </div>

                <!-- Capability Badges (对齐截图 3: 聊天、T图 > T、工具锤子、CoT思考) -->
                <div class="flex flex-wrap items-center gap-1.5 mt-2">
                  <span
                    v-if="m.capabilities?.includes('chat')"
                    class="px-2.5 py-0.5 rounded-full text-xs font-medium border"
                    style="
                      background-color: var(--color-purple-bg);
                      border-color: var(--color-purple-border);
                      color: var(--color-purple);
                    "
                  >
                    聊天
                  </span>
                  <span
                    v-if="m.capabilities?.includes('vision')"
                    class="px-2.5 py-0.5 rounded-full text-xs font-medium border"
                    style="
                      background-color: var(--color-pink-bg);
                      border-color: var(--color-pink-border);
                      color: var(--color-pink);
                    "
                  >
                    图像理解
                  </span>
                  <span
                    v-if="m.capabilities?.includes('tools')"
                    class="p-1 rounded-full border flex items-center justify-center"
                    style="
                      background-color: var(--color-blue-bg);
                      border-color: var(--color-blue-border);
                      color: var(--color-blue);
                    "
                    title="支持工具调用"
                  >
                    <Wrench class="w-3 h-3" />
                  </span>
                  <span
                    v-if="m.capabilities?.includes('reasoning') || m.reasoning_type !== 'none'"
                    class="px-2 py-0.5 rounded-full text-xs border flex items-center gap-1 font-bold text-amber-400"
                    style="
                      background-color: var(--color-warn-bg);
                      border-color: var(--color-warn-border);
                    "
                    title="支持长链推演"
                  >
                    🧠 思考
                  </span>
                  <span
                    v-if="m.context_length"
                    class="text-xs font-sans text-[var(--text-muted)] ml-1"
                  >
                    {{ (m.context_length / 1000).toFixed(0) }}k
                  </span>
                </div>
              </div>
            </div>

            <!-- Right: Minimalist Action Controls -->
            <div class="flex items-center space-x-1.5 sm:space-x-2 shrink-0">
              <button
                v-if="m.id !== cfg?.active_model_id || selectedProvider?.id !== cfg?.active_provider_id"
                @click="activateModel(m)"
                class="px-3 py-1 rounded-xl text-sm font-bold border transition-all cursor-pointer shadow-xs btn-primary-text"
                style="background-color: #2563eb; color: #ffffff"
                title="一键设为主脑"
              >
                启用
              </button>

              <button
                @click="runTestModel(m)"
                :disabled="testLoading && testingModelId === m.id"
                class="p-2 rounded-xl border text-sm cursor-pointer hover:bg-[var(--bg-card)] transition-colors"
                style="
                  background-color: var(--bg-card-subtle);
                  border-color: var(--border-subtle);
                  color: var(--text-muted);
                "
                title="测试连通性"
              >
                <RefreshCw
                  class="w-3.5 h-3.5"
                  :class="testLoading && testingModelId === m.id ? 'animate-spin' : ''"
                />
              </button>

              <button
                @click="openEditModelModal(m)"
                class="p-2 rounded-xl border text-sm cursor-pointer hover:bg-[var(--bg-card)] transition-colors"
                style="
                  background-color: var(--bg-card-subtle);
                  border-color: var(--border-subtle);
                  color: var(--text-muted);
                "
                title="编辑参数"
              >
                <Settings class="w-3.5 h-3.5" />
              </button>

              <button
                @click="deleteSingleModel(m.id)"
                class="p-2 rounded-xl border text-sm cursor-pointer hover:bg-red-500/10 transition-colors text-red-400"
                style="border-color: var(--border-subtle)"
                title="删除该模型"
              >
                <Trash2 class="w-3.5 h-3.5" />
              </button>
            </div>
          </div>

          <div
            v-if="!selectedProvider.models?.length"
            class="py-16 text-center text-sm"
            style="color: var(--text-muted)"
          >
            该供应商名下暂未配置模型，点击下方「获取」可一键从远端自动拉取。
          </div>
        </div>

        <!-- Diagnostic Response Box -->
        <div
          v-if="testResult"
          class="rounded-2xl border p-4 transition-all shadow-xs text-sm"
          :style="{
            backgroundColor: testResult.ok ? 'var(--color-up-bg)' : 'var(--color-down-bg)',
            borderColor: testResult.ok ? 'var(--color-up-border)' : 'var(--color-down-border)',
            color: testResult.ok ? 'var(--color-up)' : 'var(--color-down)',
          }"
        >
          <div class="flex items-center justify-between mb-1.5">
            <div class="flex items-center space-x-2 font-bold text-sm">
              <CheckCircle2 v-if="testResult.ok" class="w-4 h-4" />
              <AlertCircle v-else class="w-4 h-4" />
              <span>{{
                testResult.ok
                  ? `模型测试通过 (耗时: ${testResult.latency_ms}ms)`
                  : '连通性测试未通过'
              }}</span>
            </div>
            <span class="text-xs font-sans"
              >状态: {{ testResult.status_code || 0 }}</span
            >
          </div>

          <div v-if="testResult.ok" class="space-y-1 text-sm" style="color: var(--text-main)">
            <div>
              输出预览: <span class="font-bold">{{ testResult.response_preview }}</span>
            </div>
            <div v-if="testResult.reasoning_detected" class="text-emerald-500 font-bold">
              🧠 成功识别原生长思维链输出
            </div>
          </div>
          <div v-else class="text-sm break-all" style="color: var(--color-down)">
            {{ testResult.error || '连通性测试超时或未收到有效响应' }}
          </div>
        </div>

        <!-- Floating Bottom Operation Bar (完美对齐截图 3 椭圆气泡底栏: [获取] [+ 添加新模型] [清空]) -->
        <div class="flex items-center justify-center pt-2 pb-20">
          <div
            class="flex items-center space-x-3 p-1.5 rounded-full border shadow-2xl backdrop-blur-md"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <!-- 获取 (带方块立方体图标的大圆角按钮) -->
            <button
              @click="openFetchDialog"
              class="flex items-center space-x-2 px-5 py-2.5 rounded-full font-bold text-sm cursor-pointer border transition-all hover:opacity-90 shadow-2xs"
              style="
                background-color: var(--color-purple-bg);
                border-color: var(--color-purple-border);
                color: var(--color-purple);
              "
            >
              <DownloadCloud class="w-4 h-4" />
              <span>获取</span>
            </button>

            <!-- + 添加新模型 -->
            <button
              @click="openAddModelModal"
              class="flex items-center space-x-2 px-5 py-2.5 rounded-full font-bold text-sm cursor-pointer border transition-all hover:opacity-90 shadow-2xs"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            >
              <Plus class="w-4 h-4" />
              <span>添加新模型</span>
            </button>

            <!-- 清空删除图标 (带红晕气泡) -->
            <button
              @click="clearCurrentProviderModels"
              class="p-2.5 rounded-full border cursor-pointer hover:bg-red-500/10 transition-colors text-red-400"
              style="border-color: var(--color-down-border); background-color: var(--color-down-bg)"
              title="清空该供应商所有模型"
            >
              <Trash2 class="w-4 h-4" />
            </button>
          </div>
        </div>
      </div>

      <!-- Detail Bottom Tab Bar (对齐截图 2 & 截图 3 的底部「配置」与「模型」双Tab) -->
      <div
        class="fixed bottom-4 left-1/2 -translate-x-1/2 z-40 flex items-center rounded-2xl border p-1 shadow-2xl backdrop-blur-md"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <button
          @click="detailTab = 'config'"
          class="flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-sm cursor-pointer transition-all border"
          :style="
            detailTab === 'config'
              ? {
                  backgroundColor: '#2563EB',
                  borderColor: '#1D4ED8',
                  color: '#FFFFFF',
                  boxShadow: '0 2px 10px rgba(37,99,235,0.35)',
                }
              : {
                  backgroundColor: 'transparent',
                  borderColor: 'transparent',
                  color: 'var(--text-muted)',
                }
          "
        >
          <Settings class="w-4 h-4" />
          <span>配置</span>
        </button>

        <button
          @click="detailTab = 'models'"
          class="flex items-center space-x-2 px-6 py-2.5 rounded-xl font-bold text-sm cursor-pointer transition-all border"
          :style="
            detailTab === 'models'
              ? {
                  backgroundColor: '#2563EB',
                  borderColor: '#1D4ED8',
                  color: '#FFFFFF',
                  boxShadow: '0 2px 10px rgba(37,99,235,0.35)',
                }
              : {
                  backgroundColor: 'transparent',
                  borderColor: 'transparent',
                  color: 'var(--text-muted)',
                }
          "
        >
          <Layers class="w-4 h-4" />
          <span>模型 ({{ selectedProvider.models?.length || 0 }})</span>
        </button>
      </div>
    </template>

    <!-- MODAL A: 远端一键获取模型抽屉/弹窗 -->
    <AppDialog
      v-if="fetchModalVisible"
      :open="!!fetchModalVisible"
      title="发现远程模型"
      size="xl"
      @update:open="
        (open) => {
          if (!open) {
            fetchModalVisible = false
          }
        }
      "
      ><div
        class="dialog-content p-5 space-y-4 text-sm flex flex-col"
        style="
          background-color: var(--bg-card);
          border-color: var(--border-subtle);
          color: var(--text-main);
        "
      >
        <div
          class="flex items-center justify-between pb-3 border-b shrink-0"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <DownloadCloud class="w-4 h-4 text-blue-500" />
            <h3 class="text-sm font-bold uppercase" style="color: var(--text-main)">
              获取 {{ selectedProvider?.name }} 远端可用模型
            </h3>
          </div>
          <span class="text-xs" style="color: var(--text-faint)">探测 /models 兼容端点</span>
        </div>

        <!-- Probe Configuration (仅当需要微调或端点无预存 Key 时作为高级选项展开) -->
        <div
          class="p-3 rounded-xl border space-y-2 shrink-0 text-sm"
          style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
        >
          <div class="flex items-center justify-between">
            <div class="flex items-center space-x-2">
              <span class="font-bold text-xs" style="color: var(--text-main)">探测端点:</span>
              <span class="font-sans text-xs text-blue-400">{{
                customFetchUrl || selectedProvider?.base_url
              }}</span>
            </div>
            <div class="flex items-center space-x-1.5">
              <span v-if="selectedProvider?.has_key" class="text-xs text-emerald-400 font-bold">
                ✓ 使用已存凭证
              </span>
              <button
                @click="executeRemoteFetch"
                :disabled="fetchingRemote"
                class="flex items-center space-x-1.5 px-3 py-1 rounded-lg text-sm font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
                style="background-color: #2563eb; color: #ffffff"
              >
                <RefreshCw class="w-3.5 h-3.5" :class="fetchingRemote ? 'animate-spin' : ''" />
                <span>{{ fetchingRemote ? '正在探测...' : '重新探测' }}</span>
              </button>
            </div>
          </div>
        </div>

        <!-- Status Banner -->
        <div v-if="remoteFetchResult" class="shrink-0">
          <div
            v-if="remoteFetchResult.ok"
            class="p-2.5 rounded-xl border text-sm flex items-center justify-between"
            style="
              background-color: var(--color-up-bg);
              border-color: var(--color-up-border);
              color: var(--color-up);
            "
          >
            <span class="font-bold">✓ 成功探测到 {{ remoteFetchResult.total }} 个可用模型</span>
            <span class="text-xs opacity-80 font-sans">{{ remoteFetchResult.endpoint_used }}</span>
          </div>
          <div
            v-else
            class="p-2.5 rounded-xl border text-sm"
            style="
              background-color: var(--color-down-bg);
              border-color: var(--color-down-border);
              color: var(--color-down);
            "
          >
            {{ remoteFetchResult.error }}
          </div>
        </div>

        <!-- Remote Search Box -->
        <div v-if="remoteFetchResult?.ok" class="relative shrink-0">
          <input
            aria-label="搜索远程模型"
            v-model="remoteSearch"
            placeholder="过滤搜索模型 ID..."
            class="w-full rounded-xl px-3.5 py-1.5 pl-9 text-sm outline-none border font-sans"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-subtle);
              color: var(--text-main);
            "
          />
          <Search
            class="w-3.5 h-3.5 absolute left-3 top-2.5 text-[var(--text-muted)] pointer-events-none"
          />
        </div>

        <!-- Remote Scroll List -->
        <div
          v-if="remoteFetchResult?.ok"
          class="flex-1 overflow-y-auto space-y-2 pr-1 min-h-[200px]"
        >
          <div
            v-for="rm in filteredRemoteModels"
            :key="rm.id"
            class="p-3 rounded-xl border flex items-center justify-between hover:border-[var(--border-strong)] transition-colors"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
          >
            <div>
              <div class="font-bold text-sm" style="color: var(--text-main)">{{ rm.name }}</div>
              <div class="text-xs font-sans text-blue-400">{{ rm.id }}</div>
            </div>

            <div class="flex items-center space-x-2 shrink-0">
              <button
                @click="importRemoteModel(rm, false)"
                class="px-2.5 py-1 rounded-lg text-sm font-medium border cursor-pointer hover:bg-[var(--bg-card)] transition-colors"
                style="
                  background-color: var(--bg-card);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              >
                + 添加
              </button>
              <button
                @click="importRemoteModel(rm, true)"
                class="px-3 py-1 rounded-lg text-sm font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
                style="background-color: #2563eb; color: #ffffff"
              >
                添加并启用
              </button>
            </div>
          </div>
        </div>

        <div
          class="flex items-center justify-between pt-3 border-t shrink-0"
          style="border-color: var(--border-subtle)"
        >
          <div class="text-xs" style="color: var(--text-muted)">
            <span v-if="filteredRemoteModels.length"
              >当前显示 {{ filteredRemoteModels.length }} 个模型</span
            >
          </div>
          <div class="flex items-center space-x-2">
            <button
              v-if="filteredRemoteModels.length"
              @click="importAllFilteredRemoteModels"
              class="px-3 py-1.5 rounded-xl border text-sm font-bold cursor-pointer transition-all hover:opacity-90"
              style="
                background-color: var(--color-purple-bg);
                border-color: var(--color-purple-border);
                color: var(--color-purple);
              "
            >
              一键添加当前全部 ({{ filteredRemoteModels.length }})
            </button>
            <button
              @click="fetchModalVisible = false"
              class="px-4 py-1.5 rounded-xl border text-sm cursor-pointer"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-subtle);
                color: var(--text-main);
              "
            >
              完成
            </button>
          </div>
        </div>
      </div></AppDialog
    >

    <!-- MODAL B: 手动添加 / 编辑单模型弹窗 -->
    <AppDialog
      v-if="modelModalVisible"
      :open="!!modelModalVisible"
      title="模型配置"
      size="md"
      @update:open="
        (open) => {
          if (!open) {
            modelModalVisible = false
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 space-y-4 text-sm"
        style="
          background-color: var(--bg-card);
          border-color: var(--border-subtle);
          color: var(--text-main);
        "
      >
        <div
          class="flex items-center justify-between pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <h3 class="text-sm font-bold uppercase" style="color: var(--text-main)">
            {{ editingModel ? '编辑模型' : '添加新模型' }}
          </h3>
          <span class="text-xs" style="color: var(--text-faint)"
            >所属: {{ selectedProvider?.name }}</span
          >
        </div>

        <div class="space-y-3">
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs font-bold mb-1" style="color: var(--text-muted)"
                  >模型 ID</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="modelForm.id"
                  :readonly="!!editingModel"
                  placeholder="gemini-3.8-flash-high"
                  class="w-full rounded-xl px-3.5 py-2 text-sm outline-none border font-sans"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>

          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs font-bold mb-1" style="color: var(--text-muted)"
                  >展示名称</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model="modelForm.name"
                  placeholder="Gemini 3.8 Flash (高推演)"
                  class="w-full rounded-xl px-3.5 py-2 text-sm outline-none border"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>

          <!-- 模型能力标签选择 -->
          <div>
            <label class="block text-xs font-bold mb-1" style="color: var(--text-muted)"
              >能力标签徽标</label
            >
            <div class="flex flex-wrap gap-2 pt-1">
              <button
                type="button"
                @click="toggleCapability('chat')"
                :aria-pressed="modelForm.capabilities.includes('chat')"
                class="px-2.5 py-1 rounded-lg border text-sm font-medium cursor-pointer transition-all"
                :style="
                  modelForm.capabilities.includes('chat')
                    ? {
                        backgroundColor: 'var(--color-purple-bg)',
                        borderColor: 'var(--color-purple)',
                        color: 'var(--color-purple)',
                      }
                    : {
                        backgroundColor: 'var(--bg-card-subtle)',
                        borderColor: 'var(--border-subtle)',
                        color: 'var(--text-muted)',
                      }
                "
              >
                聊天 (chat)
              </button>
              <button
                type="button"
                @click="toggleCapability('vision')"
                :aria-pressed="modelForm.capabilities.includes('vision')"
                class="px-2.5 py-1 rounded-lg border text-sm font-medium cursor-pointer transition-all"
                :style="
                  modelForm.capabilities.includes('vision')
                    ? {
                        backgroundColor: 'var(--color-pink-bg)',
                        borderColor: 'var(--color-pink)',
                        color: 'var(--color-pink)',
                      }
                    : {
                        backgroundColor: 'var(--bg-card-subtle)',
                        borderColor: 'var(--border-subtle)',
                        color: 'var(--text-muted)',
                      }
                "
              >
                图像理解 (vision)
              </button>
              <button
                type="button"
                @click="toggleCapability('tools')"
                :aria-pressed="modelForm.capabilities.includes('tools')"
                class="px-2.5 py-1 rounded-lg border text-sm font-medium cursor-pointer transition-all"
                :style="
                  modelForm.capabilities.includes('tools')
                    ? {
                        backgroundColor: 'var(--color-blue-bg)',
                        borderColor: 'var(--color-blue)',
                        color: 'var(--color-blue)',
                      }
                    : {
                        backgroundColor: 'var(--bg-card-subtle)',
                        borderColor: 'var(--border-subtle)',
                        color: 'var(--text-muted)',
                      }
                "
              >
                工具调用 (tools)
              </button>
              <button
                type="button"
                @click="toggleCapability('reasoning')"
                :aria-pressed="modelForm.capabilities.includes('reasoning')"
                class="px-2.5 py-1 rounded-lg border text-sm font-medium cursor-pointer transition-all"
                :style="
                  modelForm.capabilities.includes('reasoning')
                    ? {
                        backgroundColor: 'var(--color-warn-bg)',
                        borderColor: 'var(--color-warn)',
                        color: 'var(--color-warn)',
                      }
                    : {
                        backgroundColor: 'var(--bg-card-subtle)',
                        borderColor: 'var(--border-subtle)',
                        color: 'var(--text-muted)',
                      }
                "
              >
                🧠 链式思考 (CoT)
              </button>
            </div>
          </div>

          <!-- 思考强度配置 (动态精简与自适应展示) -->
          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs font-bold mb-1" style="color: var(--text-muted)"
                  >思考推演强度</span
                ></template
              ><template #default="{ id: fieldId }"
                ><select
                  :id="fieldId"
                  v-model="modelForm.reasoning_effort"
                  class="w-full rounded-xl px-3.5 py-2 text-sm outline-none border cursor-pointer font-sans"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  "
                >
                  <option v-for="opt in availableEffortOptions" :key="opt.value" :value="opt.value">
                    {{ opt.label }}
                  </option>
                </select></template
              ></AppField
            >
          </div>

          <div>
            <AppField class="w-full min-w-0"
              ><template #label
                ><span class="block text-xs font-bold mb-1" style="color: var(--text-muted)"
                  >上下文上限长度 (Tokens)</span
                ></template
              ><template #default="{ id: fieldId }"
                ><input
                  :id="fieldId"
                  v-model.number="modelForm.context_length"
                  type="number"
                  placeholder="1048576"
                  class="w-full rounded-xl px-3.5 py-2 text-sm outline-none border font-sans"
                  style="
                    background-color: var(--bg-card-subtle);
                    border-color: var(--border-subtle);
                    color: var(--text-main);
                  " /></template
            ></AppField>
          </div>
        </div>

        <div
          class="flex justify-end space-x-2 pt-3 border-t"
          style="border-color: var(--border-subtle)"
        >
          <button
            @click="modelModalVisible = false"
            class="px-4 py-1.5 rounded-xl border text-sm cursor-pointer"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-subtle);
              color: var(--text-muted);
            "
          >
            取消
          </button>
          <button
            @click="saveModelForm"
            class="px-5 py-1.5 rounded-xl text-sm font-bold transition-all cursor-pointer shadow-xs btn-primary-text"
            style="background-color: #2563eb; color: #ffffff"
          >
            保存模型
          </button>
        </div>
      </div></AppDialog
    >
  </div>
</template>
