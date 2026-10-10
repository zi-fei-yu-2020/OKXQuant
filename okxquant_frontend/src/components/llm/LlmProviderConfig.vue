<script setup lang="ts">
import { ref } from 'vue'
import { Eye, EyeOff } from 'lucide-vue-next'
import AppField from '../ui/AppField.vue'
import AppSwitch from '../ui/AppSwitch.vue'
import { resultStyle } from './llmDisplay'

const props = defineProps<{
  provider: any
  busy: boolean
  canManage: boolean
  testLoading: boolean
  testResult: any
}>()
const form = defineModel<any>('form', { required: true })
const testModel = defineModel<string>('testModel', { required: true })
const emit = defineEmits<{ save: []; saveAndFetch: []; test: [] }>()
const showApiKey = ref(false)

const DEFAULT_PATHS: Record<string, string> = {
  openai_chat: '/chat/completions',
  claude_messages: '/messages',
  openai_responses: '/responses',
}

// Switching protocol only replaces a path that is still another protocol's default.
function onApiFormatChange() {
  const fmt = form.value.api_format
  const defaults = Object.values(DEFAULT_PATHS)
  if (!form.value.api_path || defaults.includes(form.value.api_path)) {
    form.value.api_path = DEFAULT_PATHS[fmt] || DEFAULT_PATHS.openai_chat
  }
  form.value.response_api_enabled = fmt === 'openai_responses'
}

const editable = () => !props.busy && props.canManage
</script>

<template>
  <section class="ui-card space-y-5 p-5">
    <div>
      <h3 class="mb-2 text-xs font-bold tracking-wider" style="color: var(--text-muted)">管理</h3>
      <div class="llm-settings divide-y">
        <div class="llm-setting">
          <div>
            <label for="provider-type" class="font-medium">供应商类型</label>
            <p class="llm-setting__hint">用于后台分类展示，不改变 API 协议</p>
          </div>
          <input id="provider-type" v-model="form.type" list="provider-type-options" :disabled="!editable()" placeholder="例如：OpenAI 兼容" />
          <datalist id="provider-type-options">
            <option value="OpenAI 兼容" /><option value="OpenAI" /><option value="Anthropic" />
            <option value="Gemini" /><option value="聚合网关" />
          </datalist>
        </div>

        <div class="llm-setting">
          <div>
            <label for="provider-format" class="font-medium">API 交互协议</label>
            <p class="llm-setting__hint">选择该端点底层支持的通信协议标准</p>
          </div>
          <select id="provider-format" v-model="form.api_format" :disabled="!editable()" @change="onApiFormatChange">
            <option value="openai_chat">OpenAI Chat (/chat/completions)</option>
            <option value="claude_messages">Claude Messages (/messages)</option>
            <option value="openai_responses">OpenAI Responses (/responses)</option>
          </select>
        </div>

        <div class="llm-setting">
          <div>
            <label for="provider-group" class="font-medium">分组</label>
            <p class="llm-setting__hint">可选择常用分组，也可直接输入自定义名称</p>
          </div>
          <input id="provider-group" v-model="form.group" list="provider-group-options" :disabled="!editable()" placeholder="例如：自定义" />
          <datalist id="provider-group-options">
            <option value="基础供应" /><option value="自定义" />
            <option value="聚合网关" /><option value="其他" />
          </datalist>
        </div>

        <div class="llm-setting llm-setting--switch">
          <span class="font-medium">是否启用</span>
          <AppSwitch v-model="form.enabled" label="启用供应商" :disabled="!editable()" />
        </div>
        <div class="llm-setting llm-setting--switch">
          <span class="font-medium">多Key模式</span>
          <AppSwitch v-model="form.multi_key_enabled" label="多 Key 模式" :disabled="!editable()" />
        </div>
      </div>
    </div>

    <div class="space-y-4">
      <AppField v-if="provider.is_new" label="供应商唯一标识 (ID)" v-slot="field">
        <input :id="field.id" v-model="form.id" :disabled="!editable()" placeholder="例如: openrouter 或 my-proxy" />
      </AppField>
      <AppField label="名称" v-slot="field">
        <input :id="field.id" v-model="form.name" :disabled="!editable()" placeholder="OpenAI" />
      </AppField>
      <AppField>
        <template #label>
          <span class="flex items-center justify-between gap-2">
            <span>API Key</span>
            <span v-if="provider.has_key" class="text-xs font-bold" style="color: var(--color-up)">✓ 密钥已就绪</span>
          </span>
        </template>
        <template #default="field">
        <div class="relative">
          <input :id="field.id" v-model="form.api_key" :type="showApiKey ? 'text' : 'password'" autocomplete="new-password"
            :disabled="!editable()" placeholder="••••••••••••••••••••••••" class="pr-11" />
          <button type="button" class="ui-icon-button absolute right-1 top-1/2 -translate-y-1/2" :disabled="busy"
            :aria-label="showApiKey ? '隐藏 API Key' : '显示 API Key'" @click="showApiKey = !showApiKey">
            <EyeOff v-if="showApiKey" class="size-4" aria-hidden="true" />
            <Eye v-else class="size-4" aria-hidden="true" />
          </button>
        </div>
        </template>
      </AppField>
      <AppField label="API Base URL" v-slot="field">
        <input :id="field.id" v-model="form.base_url" :disabled="!editable()" placeholder="https://api.openai.com/v1" />
      </AppField>
      <AppField label="API 路径" v-slot="field">
        <input :id="field.id" v-model="form.api_path" :disabled="!editable()" placeholder="/chat/completions" />
      </AppField>
      <AppField label="说明" v-slot="field">
        <textarea :id="field.id" v-model="form.description" :disabled="!editable()" rows="2" class="llm-textarea"
          placeholder="记录供应商用途、线路或计费备注（不要填写密钥）"></textarea>
      </AppField>
    </div>

    <div class="ui-panel space-y-3 border p-4" style="background-color: var(--bg-card-subtle)">
      <div>
        <h3 class="text-sm font-bold">供应商可用性测试</h3>
        <p class="mt-1 text-xs" style="color: var(--text-faint)">填写一个真实模型 ID，使用当前表单中的 Base URL、协议与临时 API Key 发起最小 PING 请求；测试不会保存表单。</p>
      </div>
      <div class="flex flex-col gap-2 sm:flex-row">
        <input v-model="testModel" :disabled="busy" list="provider-known-models" aria-label="测试模型 ID"
          placeholder="例如：gpt-5-mini 或供应商返回的模型 ID" class="flex-1" />
        <datalist id="provider-known-models"><option v-for="model in provider.models || []" :key="model.id" :value="model.id" /></datalist>
        <button type="button" class="ui-button ui-button--secondary shrink-0" :disabled="!canManage || busy || testLoading || !testModel.trim()" @click="emit('test')">
          {{ testLoading ? '测试中...' : '测试可用性' }}
        </button>
      </div>
      <div v-if="testResult" role="status" class="rounded-lg border px-3 py-2 text-sm break-words" :style="resultStyle(testResult.ok)">
        <span v-if="testResult.ok">可用 · HTTP {{ testResult.status_code }} · {{ testResult.latency_ms }}ms · {{ testResult.api_format_name || testResult.api_format }}</span>
        <span v-else>{{ testResult.error || '模型测试失败' }}</span>
      </div>
    </div>

    <div class="ui-actions justify-end">
      <button type="button" class="ui-button ui-button--secondary" :disabled="!editable()" @click="emit('save')">保存供应商配置</button>
      <button type="button" class="ui-button ui-button--primary" :disabled="!editable()" @click="emit('saveAndFetch')">保存并获取模型</button>
    </div>
  </section>
</template>

<style scoped>
.llm-settings {
  overflow: hidden;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-card);
  background: var(--bg-card-subtle);
  font-size: 14px;
}
.llm-setting {
  display: grid;
  gap: var(--space-2);
  padding: 14px;
}
@media (min-width: 640px) {
  .llm-setting {
    grid-template-columns: minmax(0, 1fr) minmax(180px, 280px);
    align-items: center;
  }
}
.llm-setting--switch {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: var(--space-3);
}
.llm-setting__hint {
  font-size: 12px;
  color: var(--text-faint);
}
.llm-textarea {
  min-height: 72px;
}
</style>
