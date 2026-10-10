<script setup lang="ts">
import { CheckCircle2, AlertCircle, RefreshCw, Settings, Trash2, Sparkles, Wrench, DownloadCloud, Plus, Brain } from 'lucide-vue-next'
import { resultStyle, toneStyle } from './llmDisplay'

const props = defineProps<{
  provider: any
  activeProviderId?: string
  activeModelId?: string
  busy: boolean
  canManage: boolean
  testLoading: boolean
  testingModelId: string | null
  testResult: any
}>()
const emit = defineEmits<{
  activate: [model: any]
  test: [model: any]
  edit: [model: any]
  remove: [modelId: string]
  fetch: []
  add: []
  clear: []
}>()

const isActive = (m: any) => m.id === props.activeModelId && props.provider?.id === props.activeProviderId
const hasReasoning = (m: any) => m.capabilities?.includes('reasoning') || (m.reasoning_type && m.reasoning_type !== 'none')
</script>

<template>
  <div class="space-y-4">
    <ul class="ui-card divide-y overflow-hidden">
      <li v-for="m in provider.models" :key="m.id" class="flex flex-wrap items-center justify-between gap-3 p-4 transition-colors hover:bg-[var(--bg-card-subtle)] sm:p-5">
        <div class="flex min-w-0 flex-1 items-start gap-3.5 sm:items-center">
          <span class="llm-model-avatar" aria-hidden="true"><Sparkles class="size-5" /></span>
          <div class="min-w-0">
            <div class="flex flex-wrap items-center gap-2">
              <span class="break-all text-sm font-bold">{{ m.id }}</span>
              <span v-if="isActive(m)" class="ui-badge ui-badge--success">主脑生效</span>
            </div>
            <div class="mt-2 flex flex-wrap items-center gap-1.5">
              <span v-if="m.capabilities?.includes('chat')" class="llm-chip" :style="toneStyle('purple')">聊天</span>
              <span v-if="m.capabilities?.includes('vision')" class="llm-chip" :style="toneStyle('pink')">图像理解</span>
              <span v-if="m.capabilities?.includes('tools')" class="llm-chip" :style="toneStyle('blue')" title="支持工具调用">
                <Wrench class="size-3" aria-hidden="true" />工具
              </span>
              <span v-if="hasReasoning(m)" class="llm-chip" :style="toneStyle('warn')" title="支持长链推演">
                <Brain class="size-3" aria-hidden="true" />思考
              </span>
              <span v-if="m.context_length" class="ml-1 text-xs" style="color: var(--text-muted)">{{ (m.context_length / 1000).toFixed(0) }}k</span>
            </div>
          </div>
        </div>

        <div class="flex shrink-0 items-center gap-1.5 sm:gap-2">
          <button v-if="!isActive(m)" type="button" class="ui-button ui-button--primary ui-button--sm"
            :disabled="busy || !canManage" title="一键设为主脑" @click="emit('activate', m)">启用</button>
          <button type="button" class="ui-icon-button border" title="测试连通性" :aria-label="`测试 ${m.id}`"
            :disabled="!canManage || (testLoading && testingModelId === m.id) || busy" @click="emit('test', m)">
            <RefreshCw class="size-3.5" :class="testLoading && testingModelId === m.id ? 'animate-spin' : ''" aria-hidden="true" />
          </button>
          <button type="button" class="ui-icon-button border" title="编辑参数" :aria-label="`编辑 ${m.id}`" :disabled="busy" @click="emit('edit', m)">
            <Settings class="size-3.5" aria-hidden="true" />
          </button>
          <button type="button" class="ui-icon-button ui-icon-button--danger border" title="删除该模型" :aria-label="`删除 ${m.id}`"
            :disabled="busy || !canManage" @click="emit('remove', m.id)">
            <Trash2 class="size-3.5" aria-hidden="true" />
          </button>
        </div>
      </li>
      <li v-if="!provider.models?.length" class="py-16 text-center text-sm" style="color: var(--text-muted)">
        该供应商名下暂未配置模型，点击下方「获取」可一键从远端自动拉取。
      </li>
    </ul>

    <div v-if="testResult" class="ui-card border p-4 text-sm" :style="resultStyle(testResult.ok)">
      <div class="mb-1.5 flex flex-wrap items-center justify-between gap-2">
        <div class="flex items-center gap-2 font-bold">
          <CheckCircle2 v-if="testResult.ok" class="size-4" aria-hidden="true" />
          <AlertCircle v-else class="size-4" aria-hidden="true" />
          <span>{{ testResult.ok ? `模型测试通过 (耗时: ${testResult.latency_ms}ms)` : '连通性测试未通过' }}</span>
        </div>
        <span class="text-xs">状态: {{ testResult.status_code || 0 }}</span>
      </div>
      <div v-if="testResult.ok" class="space-y-1" style="color: var(--text-main)">
        <div>输出预览: <span class="font-bold">{{ testResult.response_preview }}</span></div>
        <div v-if="testResult.reasoning_detected" class="font-bold" style="color: var(--color-up)">成功识别原生长思维链输出</div>
      </div>
      <div v-else class="break-all">{{ testResult.error || '连通性测试超时或未收到有效响应' }}</div>
    </div>

    <div class="ui-actions justify-center">
      <button type="button" class="ui-button ui-button--secondary" :style="toneStyle('purple')" :disabled="busy" @click="emit('fetch')">
        <DownloadCloud class="size-4" aria-hidden="true" /><span>获取</span>
      </button>
      <button type="button" class="ui-button ui-button--secondary" :disabled="busy" @click="emit('add')">
        <Plus class="size-4" aria-hidden="true" /><span>添加新模型</span>
      </button>
      <button type="button" class="ui-icon-button ui-icon-button--danger border" title="清空该供应商所有模型" aria-label="清空该供应商所有模型"
        :disabled="busy || !canManage" @click="emit('clear')">
        <Trash2 class="size-4" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<style scoped>
.llm-model-avatar {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border: 1px solid var(--border-subtle);
  border-radius: 999px;
  background: var(--bg-card-subtle);
  color: var(--color-brand);
}
.llm-chip {
  display: inline-flex;
  align-items: center;
  gap: 4px;
  border: 1px solid;
  border-radius: 999px;
  padding: 1px 10px;
  font-size: 12px;
  font-weight: 600;
  line-height: 20px;
}
</style>
