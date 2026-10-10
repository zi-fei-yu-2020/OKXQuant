<script setup lang="ts">
import { RefreshCw, Search } from 'lucide-vue-next'
import AppDialog from '../ui/AppDialog.vue'
import AppField from '../ui/AppField.vue'
import { resultStyle, toneStyle } from './llmDisplay'

defineProps<{
  open: boolean
  provider: any
  busy: boolean
  canManage: boolean
  canImport: boolean
  fetching: boolean
  result: any
  models: any[]
  testingModelId: string | null
  testResults: Record<string, any>
}>()
const url = defineModel<string>('url', { required: true })
const apiKey = defineModel<string>('apiKey', { required: true })
const search = defineModel<string>('search', { required: true })
const emit = defineEmits<{
  'update:open': [value: boolean]
  fetch: []
  test: [model: any]
  import: [model: any, activate: boolean]
  importAll: []
}>()
</script>

<template>
  <AppDialog :open="open" :busy="busy" size="xl" :title="`获取 ${provider?.name || ''} 远端可用模型`"
    description="探测标准 /models 兼容端点" @update:open="emit('update:open', $event)">
    <div class="space-y-4 text-sm">
      <div class="ui-panel space-y-3 border p-3" style="background-color: var(--bg-card-subtle)">
        <div class="grid grid-cols-1 gap-3 md:grid-cols-2">
          <AppField label="模型列表 Base URL" hint="探测标准 /models 兼容端点" v-slot="field">
            <input :id="field.id" v-model="url" :disabled="fetching || busy" placeholder="https://api.example.com/v1" />
          </AppField>
          <AppField label="临时 API Key" :hint="provider?.has_key ? '留空使用已保存凭证；填写则仅用于本次探测和测试' : '仅用于本次探测和测试，不会自动保存'" v-slot="field">
            <input :id="field.id" v-model="apiKey" type="password" autocomplete="new-password" :disabled="fetching || busy" placeholder="留空使用已保存凭证" />
          </AppField>
        </div>
        <div class="flex flex-wrap items-center justify-between gap-2">
          <span v-if="provider?.has_key && !apiKey" class="text-xs font-bold" style="color: var(--color-up)">✓ 将使用已保存凭证</span>
          <span v-else class="text-xs" style="color: var(--text-faint)">不会在结果、审计记录或页面中回显 API Key</span>
          <button type="button" class="ui-button ui-button--primary" :disabled="!canManage || fetching || busy || !url.trim()" @click="emit('fetch')">
            <RefreshCw class="size-3.5" :class="fetching ? 'animate-spin' : ''" aria-hidden="true" />
            <span>{{ fetching ? '正在获取...' : '获取模型列表' }}</span>
          </button>
        </div>
      </div>

      <div v-if="result" role="status" class="flex flex-wrap items-center justify-between gap-2 rounded-lg border p-2.5" :style="resultStyle(result.ok)">
        <template v-if="result.ok">
          <span class="font-bold">✓ 成功探测到 {{ result.total }} 个可用模型</span>
          <span class="break-all text-xs opacity-80">{{ result.endpoint_used }}</span>
        </template>
        <span v-else class="break-words">{{ result.error }}</span>
      </div>

      <template v-if="result?.ok">
        <div class="relative">
          <Search class="pointer-events-none absolute left-3 top-1/2 size-3.5 -translate-y-1/2" style="color: var(--text-muted)" aria-hidden="true" />
          <input v-model="search" :disabled="busy" aria-label="搜索远程模型" placeholder="过滤搜索模型 ID..." class="pl-9" />
        </div>

        <ul class="llm-remote-list space-y-2 pr-1">
          <li v-for="rm in models" :key="rm.id" class="ui-panel flex flex-wrap items-center justify-between gap-3 border p-3" style="background-color: var(--bg-card-subtle)">
            <div class="min-w-0 flex-1">
              <div class="truncate font-bold">{{ rm.name }}</div>
              <div class="break-all text-xs" style="color: var(--color-blue)">{{ rm.id }}</div>
              <div v-if="testResults[rm.id]" class="mt-1 text-xs" :style="{ color: testResults[rm.id].ok ? 'var(--color-up)' : 'var(--color-down)' }">
                <span v-if="testResults[rm.id].ok">可用 · {{ testResults[rm.id].latency_ms }}ms · HTTP {{ testResults[rm.id].status_code }}</span>
                <span v-else>{{ testResults[rm.id].error || '测试失败' }}</span>
              </div>
            </div>
            <div class="ui-actions shrink-0 justify-end">
              <button type="button" class="ui-button ui-button--secondary ui-button--sm" :disabled="!canManage || busy || testingModelId === rm.id" @click="emit('test', rm)">
                {{ testingModelId === rm.id ? '测试中...' : '测试' }}
              </button>
              <button type="button" class="ui-button ui-button--secondary ui-button--sm" :disabled="busy || !canManage || !canImport" @click="emit('import', rm, false)">+ 添加</button>
              <button type="button" class="ui-button ui-button--primary ui-button--sm" :disabled="busy || !canManage || !canImport" @click="emit('import', rm, true)">添加并启用</button>
            </div>
          </li>
        </ul>
      </template>
    </div>

    <template #footer>
      <span class="mr-auto self-center text-xs" style="color: var(--text-muted)">
        <template v-if="models.length">当前显示 {{ models.length }} 个模型</template>
      </span>
      <button v-if="models.length" type="button" class="ui-button ui-button--secondary ui-button--sm" :style="toneStyle('purple')"
        :disabled="busy || !canManage || !canImport" @click="emit('importAll')">一键添加当前全部 ({{ models.length }})</button>
      <button type="button" class="ui-button ui-button--secondary ui-button--sm" :disabled="busy" @click="emit('update:open', false)">完成</button>
    </template>
  </AppDialog>
</template>

<style scoped>
.llm-remote-list {
  max-height: min(52vh, 520px);
  overflow-y: auto;
}
</style>
