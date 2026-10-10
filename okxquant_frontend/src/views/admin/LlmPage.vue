<script setup lang="ts">
import { useApiAction } from '../../composables/useApiAction'
const { action, actionBusy, canManage } = useApiAction(() => loading.value || loadFailed.value)

import LlmTransportPanel from '../../components/LlmTransportPanel.vue'
import LlmProviderList from '../../components/llm/LlmProviderList.vue'
import LlmProviderConfig from '../../components/llm/LlmProviderConfig.vue'
import LlmModelList from '../../components/llm/LlmModelList.vue'
import LlmFetchModelsDialog from '../../components/llm/LlmFetchModelsDialog.vue'
import LlmModelDialog from '../../components/llm/LlmModelDialog.vue'
import { providerGlyph } from '../../components/llm/llmDisplay'

import { useToast } from '../../composables/useFeedback'
import { useDialogs } from '../../composables/useDialogs'

import { ref, computed, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { ArrowLeft, Settings, Layers } from 'lucide-vue-next'

const { api } = useApi()
const { confirm } = useDialogs()
const toast = useToast()

// State
const cfg = ref<any>(null)
const transportRevision = ref(0)
const loading = ref(true)
const loadFailed = ref(false)
const searchQuery = ref('')

// Navigation: 'list' (一级：供应商列表) | 'detail' (二级：供应商配置与模型详情)
const currentView = ref<'list' | 'detail'>('list')
const selectedProvider = ref<any>(null)
const detailTab = ref<'config' | 'models'>('config')

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
const providerTestModel = ref('')

// Remote Fetch State & Modal
const fetchModalVisible = ref(false)
const fetchingRemote = ref(false)
const remoteFetchResult = ref<any>(null)
const remoteSearch = ref('')
const customFetchUrl = ref('')
const customFetchKey = ref('')
const remoteTestingModelId = ref<string | null>(null)
const remoteTestResults = ref<Record<string, any>>({})

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
  loadFailed.value = false
  loading.value = true
  try {
    cfg.value = await api('/api/v1/admin/llm/models')
    transportRevision.value += 1
    if (selectedProvider.value) {
      const updated = cfg.value.providers?.find((p: any) => p.id === selectedProvider.value.id)
      if (updated) {
        selectedProvider.value = updated
      }
    }
  } catch (e: any) {
    if (e?.silent) return
    loadFailed.value = true
    toast.error(e.message)
  } finally {
    loading.value = false
  }
}

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
  providerTestModel.value = ''
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
  providerTestModel.value = p.models?.[0]?.id || ''
}

function goBackToList() {
  currentView.value = 'list'
  selectedProvider.value = null
  testResult.value = null
}

const toggleProviderQuick = action(async (p: any) => {
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
})

const saveProviderConfig = action(async () => {
  try {
    const payload = { ...providerForm.value }
    if (!String(payload.name || '').trim()) { toast.error('请输入供应商名称'); return false }
    if (!String(payload.group || '').trim()) payload.group = '其他'
    if (!String(payload.type || '').trim()) payload.type = payload.name
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
    providerForm.value.api_key = ''
    toast.success('供应商配置已保存')
    await loadConfig()
    if (selectedProvider.value?.is_new) {
      const created = cfg.value.providers?.find((p: any) => p.id === payload.id)
      if (created) {
        selectedProvider.value = created
      }
    }
    return true
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
    return false
  }
})

async function saveProviderAndOpenModels() {
  const saved = await saveProviderConfig()
  if (!saved || !selectedProvider.value || selectedProvider.value.is_new) return
  detailTab.value = 'models'
  openFetchDialog()
}

const clearCurrentProviderModels = action(async () => {
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
})

// ----------------- Remote Fetch -----------------
function openFetchDialog() {
  if (!selectedProvider.value) return
  customFetchUrl.value = providerForm.value.base_url || selectedProvider.value.base_url || ''
  customFetchKey.value = providerForm.value.api_key || ''
  remoteFetchResult.value = null
  remoteTestResults.value = {}
  remoteTestingModelId.value = null
  remoteSearch.value = ''
  fetchModalVisible.value = true
}

function savedProviderId() {
  return selectedProvider.value?.is_new ? '' : String(selectedProvider.value?.id || '')
}

function currentProviderPayload(modelId: string, model: any = {}) {
  const payload: any = {
    model: modelId,
    base_url: String(providerForm.value.base_url || selectedProvider.value?.base_url || '').trim(),
    api_format: providerForm.value.api_format || model.api_format || 'openai_chat',
    reasoning_effort: model.reasoning_effort || model.default_effort || 'auto',
    reasoning_type: model.reasoning_type || 'auto',
  }
  const providerId = savedProviderId()
  if (providerId) payload.provider_id = providerId
  const temporaryKey = String(providerForm.value.api_key || '').trim()
  if (temporaryKey) payload.api_key = temporaryKey
  return payload
}

const executeRemoteFetch = action(async () => {
  if (!canManage.value) return
  if (!selectedProvider.value) return
  fetchingRemote.value = true
  remoteFetchResult.value = null
  try {
    const baseUrl = customFetchUrl.value.trim() || String(providerForm.value.base_url || '').trim()
    if (!baseUrl.startsWith('http://') && !baseUrl.startsWith('https://')) {
      remoteFetchResult.value = { ok: false, error: '请先填写以 http:// 或 https:// 开头的 Base URL' }
      return
    }
    const payload: any = { base_url: baseUrl }
    const providerId = savedProviderId()
    if (providerId) payload.provider_id = providerId
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
}, false)

const runRemoteModelTest = action(async (m: any) => {
  if (!canManage.value) return
  remoteTestingModelId.value = m.id
  remoteTestResults.value = { ...remoteTestResults.value, [m.id]: null }
  try {
    const payload = currentProviderPayload(m.id, m)
    payload.base_url = customFetchUrl.value.trim() || payload.base_url
    const temporaryKey = customFetchKey.value.trim()
    if (temporaryKey) payload.api_key = temporaryKey
    remoteTestResults.value = {
      ...remoteTestResults.value,
      [m.id]: await api('/api/v1/admin/llm/test', { method: 'POST', body: JSON.stringify(payload) }),
    }
  } catch (err: any) {
    if (err?.silent) return
    remoteTestResults.value = { ...remoteTestResults.value, [m.id]: { ok: false, error: err.message } }
  } finally {
    remoteTestingModelId.value = null
  }
}, false)

const filteredRemoteModels = computed(() => {
  if (!remoteFetchResult.value?.models) return []
  const q = remoteSearch.value.trim().toLowerCase()
  if (!q) return remoteFetchResult.value.models
  return remoteFetchResult.value.models.filter(
    (m: any) => m.id.toLowerCase().includes(q) || (m.name && m.name.toLowerCase().includes(q)),
  )
})

function remoteModelPayload(m: any) {
  return {
    id: m.id,
    name: m.name || m.id,
    provider_id: savedProviderId(),
    provider_name: providerForm.value.name || selectedProvider.value.name,
    base_url: customFetchUrl.value.trim() || providerForm.value.base_url || selectedProvider.value.base_url,
    api_format: m.api_format || providerForm.value.api_format || selectedProvider.value.api_format || 'openai_chat',
    reasoning_type: m.reasoning_type || 'auto',
    reasoning_effort: m.default_effort || 'high',
    capabilities: m.capabilities || ['chat'],
    context_length: m.context_length,
    description: m.description ? m.description.slice(0, 100) : '从远端一键自动收录',
  }
}

const importRemoteModel = action(async (m: any, autoActivate = false) => {
  if (!selectedProvider.value) return
  if (!savedProviderId()) { toast.warning('请先保存供应商配置，再添加模型'); return }
  try {
    const payload = remoteModelPayload(m)
    await api('/api/v1/admin/llm/models', {
      method: 'POST',
      body: JSON.stringify(payload),
    })
    if (autoActivate) {
      await api('/api/v1/admin/llm/activate', {
        method: 'POST',
        body: JSON.stringify({ model_id: m.id, provider_id: selectedProvider.value.id, reasoning_effort: payload.reasoning_effort }),
      })
    }
    await loadConfig()
    toast.success(autoActivate ? `已收录并激活主脑为 ${m.id}！` : `已成功添加 ${m.id} 到模型列表！`)
  } catch (err: any) {
    if (err?.silent) return
    toast.error(err.message)
  }
})

const importAllFilteredRemoteModels = action(async () => {
  if (!selectedProvider.value || !filteredRemoteModels.value.length) return
  if (!savedProviderId()) { toast.warning('请先保存供应商配置，再批量添加模型'); return }
  const list = [...filteredRemoteModels.value]
  let successCount = 0
  const failedIds: string[] = []
  for (const m of list) {
    try {
      await api('/api/v1/admin/llm/models', {
        method: 'POST',
        body: JSON.stringify(remoteModelPayload(m)),
      })
      successCount++
    } catch (e: any) {
      if (e?.silent) return
      failedIds.push(m.id)
    }
  }
  await loadConfig()
  if (failedIds.length) toast.warning(`已收录 ${successCount} 个模型；以下 ${failedIds.length} 个失败，请重试：${failedIds.join('，')}`)
  else toast.success(`已收录 ${successCount} 个模型`)
})

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

const saveModelForm = action(async () => {
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
})

const activateModel = action(async (m: any) => {
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
})

const deleteSingleModel = action(async (modelId: string) => {
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
})

// ----------------- Test Connection -----------------
async function executeModelTest(modelId: string, model: any = {}) {
  if (!canManage.value) return
  const cleanModelId = String(modelId || '').trim()
  if (!cleanModelId) {
    testResult.value = { ok: false, error: '请先填写要测试的模型 ID' }
    return
  }
  testLoading.value = true
  testingModelId.value = cleanModelId
  testResult.value = null
  try {
    testResult.value = await api('/api/v1/admin/llm/test', {
      method: 'POST',
      body: JSON.stringify(currentProviderPayload(cleanModelId, model)),
    })
  } catch (e: any) {
    if (e?.silent) return
    testResult.value = { ok: false, error: e.message }
  } finally {
    testLoading.value = false
    testingModelId.value = null
  }
}

const runTestModel = action(async (m: any) => executeModelTest(m.id, m))
const runProviderTest = action(async () => executeModelTest(providerTestModel.value))

onMounted(() => {
  loadConfig()
})
</script>

<template>
  <div class="mx-auto max-w-4xl space-y-4 text-sm">
    <div v-if="loadFailed" role="alert" class="flex items-center justify-between gap-3 rounded-lg border p-3" style="border-color: var(--color-down-border)">
      <span>页面加载失败，请重试。</span>
      <button type="button" class="ui-button ui-button--secondary ui-button--sm" :disabled="loading || actionBusy" @click="loadConfig()">重试</button>
    </div>

    <LlmProviderList v-if="currentView === 'list'" v-model:search="searchQuery" :providers="filteredProviders"
      :active-provider-id="cfg?.active_provider_id" :active-model-id="cfg?.active_model_id" :busy="actionBusy" :can-manage="canManage"
      @add="openAddProviderModal" @select="selectProvider" @toggle="toggleProviderQuick" />

    <template v-else-if="currentView === 'detail' && selectedProvider">
      <header class="ui-card flex flex-wrap items-center gap-3 p-3 sm:p-4">
        <button type="button" class="ui-button ui-button--secondary ui-button--sm" :disabled="actionBusy" @click="goBackToList">
          <ArrowLeft class="size-4" aria-hidden="true" /><span>返回</span>
        </button>
        <div class="flex min-w-0 flex-1 items-center gap-2">
          <span class="text-base font-bold" :style="{ color: providerGlyph(selectedProvider.id).tone }" aria-hidden="true">{{ providerGlyph(selectedProvider.id).glyph }}</span>
          <h2 class="truncate text-sm font-bold sm:text-base">{{ selectedProvider.name }}</h2>
        </div>
        <div role="tablist" aria-label="供应商详情" class="llm-tabs">
          <button type="button" role="tab" class="ui-tab-control" :aria-selected="detailTab === 'config'" :disabled="actionBusy" @click="detailTab = 'config'">
            <Settings class="size-4" aria-hidden="true" /><span>配置</span>
          </button>
          <button type="button" role="tab" class="ui-tab-control" :aria-selected="detailTab === 'models'" :disabled="actionBusy || selectedProvider.is_new" @click="detailTab = 'models'">
            <Layers class="size-4" aria-hidden="true" /><span>模型 ({{ selectedProvider.models?.length || 0 }})</span>
          </button>
        </div>
      </header>

      <LlmTransportPanel v-if="!selectedProvider.is_new" :key="transportRevision" :provider-id="selectedProvider.id" :models="selectedProvider.models || []"
        :active-model-id="cfg?.active_provider_id === selectedProvider.id ? cfg?.active_model_id : undefined" />

      <LlmProviderConfig v-if="detailTab === 'config'" v-model:form="providerForm" v-model:test-model="providerTestModel" :provider="selectedProvider"
        :busy="actionBusy" :can-manage="canManage" :test-loading="testLoading" :test-result="testResult"
        @save="saveProviderConfig" @save-and-fetch="saveProviderAndOpenModels" @test="runProviderTest" />

      <LlmModelList v-else :provider="selectedProvider" :active-provider-id="cfg?.active_provider_id" :active-model-id="cfg?.active_model_id"
        :busy="actionBusy" :can-manage="canManage" :test-loading="testLoading" :testing-model-id="testingModelId" :test-result="testResult"
        @activate="activateModel" @test="runTestModel" @edit="openEditModelModal" @remove="deleteSingleModel"
        @fetch="openFetchDialog" @add="openAddModelModal" @clear="clearCurrentProviderModels" />
    </template>

    <LlmFetchModelsDialog v-if="fetchModalVisible" v-model:open="fetchModalVisible" v-model:url="customFetchUrl" v-model:api-key="customFetchKey"
      v-model:search="remoteSearch" :provider="selectedProvider" :busy="actionBusy" :can-manage="canManage" :can-import="!!savedProviderId()"
      :fetching="fetchingRemote" :result="remoteFetchResult" :models="filteredRemoteModels" :testing-model-id="remoteTestingModelId"
      :test-results="remoteTestResults" @fetch="executeRemoteFetch" @test="runRemoteModelTest" @import="importRemoteModel"
      @import-all="importAllFilteredRemoteModels" />

    <LlmModelDialog v-if="modelModalVisible" v-model:open="modelModalVisible" v-model:form="modelForm" :editing="!!editingModel"
      :provider-name="selectedProvider?.name" :busy="actionBusy" :can-manage="canManage" @save="saveModelForm" />
  </div>
</template>

<style scoped>
.llm-tabs {
  display: inline-flex;
  gap: 4px;
  padding: 3px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-control);
  background: var(--bg-card-subtle);
}
.llm-tabs .ui-tab-control {
  color: var(--text-muted);
}
.llm-tabs .ui-tab-control[aria-selected='true'] {
  background: var(--bg-card);
  color: var(--color-brand);
  box-shadow: var(--shadow-card);
}
</style>
