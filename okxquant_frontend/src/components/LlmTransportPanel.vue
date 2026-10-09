<script setup lang="ts">
import { computed, onUnmounted, ref, watch } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useApi } from '../composables/useApi'
import { useDialogs } from '../composables/useDialogs'
import AppCard from './ui/AppCard.vue'
import AppButton from './ui/AppButton.vue'
import AppField from './ui/AppField.vue'
import AppBadge from './ui/AppBadge.vue'

const props = defineProps<{ providerId: string; models: Array<{ id: string; name?: string }>; activeModelId?: string }>()
const auth = useAuthStore()
const { api } = useApi()
const { confirm } = useDialogs()
const selectedModel = ref('')
const state = ref<any>(null)
const policy = ref<any>({ mode: 'auto', connect_timeout_seconds: 10, first_byte_timeout_seconds: 150, idle_timeout_seconds: 90, total_timeout_seconds: 180, max_response_bytes: 8388608 })
const busy = ref('')
const error = ref('')
const notice = ref('')
const lastResult = ref<any>(null)
let epoch = 0
let controller: AbortController | null = null
const identity = () => [props.providerId, selectedModel.value, auth.token].join('\0')
const available = computed(() => !!props.providerId && props.models.some(m => m.id === selectedModel.value))
const editable = computed(() => auth.isSuperadmin && available.value && !busy.value)
const modeLabel = (mode: string) => ({ auto: '自动选择', stream: '流式', json: '非流式兼容' }[mode] || '未知')
const capabilityLabel = (value: string) => ({ verified: '已验证', unsupported: '明确不支持', failed: '验证未通过', unknown: '未验证' }[value] || '未验证')
const metric = (v: any, suffix = '') => typeof v === 'number' && Number.isFinite(v) ? `${v}${suffix}` : '未记录'
const timestamps = (value: any) => value && Number.isFinite(Date.parse(value)) ? new Date(value).toLocaleString() : '尚无记录'
const valid = (generation: number, key: string) => generation === epoch && key === identity()
function apply(value: any) { state.value = value; policy.value = { ...value.policy } }
function pause(signal: AbortSignal) {
  return new Promise<boolean>(resolve => {
    if (signal.aborted) { resolve(false); return }
    const done = (ok: boolean) => { clearTimeout(timer); signal.removeEventListener('abort', abort); resolve(ok) }
    const abort = () => done(false)
    const timer = setTimeout(() => done(true), 2000)
    signal.addEventListener('abort', abort, { once: true })
  })
}
async function load() {
  epoch += 1; controller?.abort(); controller = new AbortController()
  const generation = epoch, key = identity(), signal = controller.signal
  state.value = null; lastResult.value = null; notice.value = ''; error.value = ''; busy.value = ''
  if (!available.value) return
  busy.value = 'loading'
  try {
    const query = new URLSearchParams({ provider_id: props.providerId, model_id: selectedModel.value })
    const result = await api('/api/v1/admin/llm/transport?' + query, { signal })
    if (valid(generation, key)) apply(result)
  } catch (e: any) { if (valid(generation, key) && !signal.aborted) error.value = e.message || '加载失败' }
  finally { if (valid(generation, key)) busy.value = '' }
}
async function save() {
  if (!editable.value || !state.value) return
  const generation = epoch, key = identity()
  busy.value = 'saving'; error.value = ''; notice.value = ''
  try {
    const result = await api('/api/v1/admin/llm/transport', { method: 'PUT', body: JSON.stringify({ provider_id: props.providerId, model_id: selectedModel.value, policy: policy.value }) })
    if (valid(generation, key)) { apply(result); notice.value = '配置已保存；下次请求使用，不会触发模型调用或交易。' }
  } catch (e: any) { if (valid(generation, key)) error.value = e.message || '保存未确认，请刷新核对' }
  finally { if (valid(generation, key)) busy.value = '' }
}
async function verify(mode: 'stream' | 'json') {
  if (!editable.value || !state.value) return
  const generation = epoch, key = identity(), providerId = props.providerId, modelId = selectedModel.value
  const approved = await confirm('验证会发起一次小型模型请求，可能产生费用。不下单、不更换模型、不降低思考强度；短探针不保证所有长请求均有心跳；验证通过后，自动模式会按记录选择流式。确认开始？', { title: '验证响应能力', danger: false })
  if (!approved || !valid(generation, key) || !editable.value) return
  busy.value = 'verifying'; error.value = ''; notice.value = ''; lastResult.value = null
  const signal = controller!.signal, deadline = Date.now() + 600000
  try {
    let result = await api<any>('/api/v1/admin/llm/transport/verify', { method: 'POST', body: JSON.stringify({ provider_id: providerId, model_id: modelId, mode }) })
    while (valid(generation, key) && result.status !== 'completed' && result.job_id) {
      if (Date.now() >= deadline) throw new Error('验证状态等待已达上限；结果未知，不会自动重发。')
      if (!await pause(signal)) return
      result = await api<any>('/api/v1/admin/llm/transport/verification/' + encodeURIComponent(result.job_id), { signal })
    }
    if (!valid(generation, key)) return
    if (result.status !== 'completed' && typeof result.ok !== 'boolean') throw new Error('验证任务状态未知，请手动核对；不自动重发。')
    lastResult.value = result
    if (result.state) apply(result.state)
    if (result.ok) notice.value = '本次协议与完整 JSON 验证通过。'
    else error.value = result.error?.message || '本次验证失败，不等于供应商不支持此能力。'
  } catch (e: any) { if (valid(generation, key) && !signal.aborted) error.value = (e.message || '无法查询验证状态') + '；未自动重发模型请求。' }
  finally { if (valid(generation, key)) busy.value = '' }
}
watch(() => [props.providerId, props.models.map(m => m.id).join('\0'), props.activeModelId], () => {
  if (!props.models.some(m => m.id === selectedModel.value)) selectedModel.value = props.models.find(m => m.id === props.activeModelId)?.id || props.models[0]?.id || ''
}, { immediate: true })
watch(() => [props.providerId, selectedModel.value, auth.token], load, { immediate: true })
onUnmounted(() => { epoch += 1; controller?.abort() })
</script>

<template>
  <AppCard title="响应方式与连接稳定性" description="使用已保存的供应商配置验证；能力记录按模型与连接隔离。" padded data-llm-transport>
    <p v-if="!models.length" class="text-sm" style="color:var(--text-muted)">请先保存供应商并添加模型。</p>
    <div v-else class="space-y-4">
      <AppField label="验证模型" v-slot="field"><select :id="field.id" v-model="selectedModel" :disabled="!!busy"><option v-for="model in models" :key="model.id" :value="model.id">{{ model.name || model.id }}</option></select></AppField>
      <p v-if="error" role="alert" class="text-sm break-words" style="color:var(--color-down)">{{ error }}</p>
      <p v-if="notice" role="status" class="text-sm" style="color:var(--color-up)">{{ notice }}</p>
      <p v-if="busy === 'loading'" role="status">正在加载传输配置…</p>
      <template v-if="state">
        <div class="flex flex-wrap items-center gap-2 text-xs"><AppBadge tone="neutral">{{ state.protocol }}</AppBadge><span>当前生效：{{ modeLabel(state.effective_mode) }}</span></div>
        <AppField label="响应方式" v-slot="field"><select :id="field.id" v-model="policy.mode" :disabled="!editable"><option value="auto">自动选择（依据已验证能力）</option><option value="stream">流式</option><option value="json">非流式兼容</option></select></AppField>
        <details class="action-disclosure"><summary>高级超时设置</summary><div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-3">
          <AppField v-for="field in [{key:'connect_timeout_seconds',label:'连接超时（秒）',max:60,min:1},{key:'first_byte_timeout_seconds',label:'首包超时（秒）',max:600,min:1},{key:'idle_timeout_seconds',label:'空闲超时（秒）',max:300,min:1},{key:'total_timeout_seconds',label:'总时限（秒）',max:600,min:10}]" :key="field.key" :label="field.label" v-slot="slot"><input :id="slot.id" type="number" v-model.number="policy[field.key]" :min="field.min" :max="field.max" :disabled="!editable" /></AppField>
        </div><p class="text-xs mt-3" style="color:var(--text-muted)">总时限还会受调用任务预算限制；心跳不会延长总时限。</p></details>
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3"><div v-for="mode in ['stream','json']" :key="mode" class="rounded-lg border p-3 min-w-0"><div class="flex flex-wrap justify-between gap-2 text-sm"><strong>{{ modeLabel(mode) }}</strong><AppBadge :tone="state.capabilities[mode]?.status === 'verified' ? 'success' : 'warning'">{{ capabilityLabel(state.capabilities[mode]?.status) }}</AppBadge></div><p class="text-xs mt-2" style="color:var(--text-muted)">{{ timestamps(state.capabilities[mode]?.checked_at) }}</p><p v-if="state.capabilities[mode]?.last_probe_status === 'failed'" class="text-xs" style="color:var(--color-warn)">能力曾验证，最近复检失败。</p></div></div>
        <ul class="text-xs space-y-1 break-words" style="color:var(--text-muted)"><li v-for="warning in state.warnings" :key="warning">{{ warning }}</li></ul>
        <div v-if="lastResult?.diagnostics" class="flex flex-wrap gap-x-4 gap-y-2 text-xs" data-transport-diagnostics><span>首包 {{ metric(lastResult.diagnostics.first_byte_ms,' ms') }}</span><span>首个模型内容 {{ metric(lastResult.diagnostics.first_content_ms,' ms') }}</span><span>总耗时 {{ metric(lastResult.diagnostics.total_ms,' ms') }}</span><span>最长间隔 {{ metric(lastResult.diagnostics.max_gap_ms,' ms') }}</span><span>心跳 {{ metric(lastResult.diagnostics.heartbeat_count) }}</span><span>完整结束 {{ lastResult.diagnostics.completion_seen === true ? '是' : lastResult.diagnostics.completion_seen === false ? '否' : '未记录' }}</span></div>
        <div v-if="auth.isSuperadmin" class="ui-actions"><AppButton :disabled="!editable" :loading="busy === 'saving'" @click="save">保存传输配置</AppButton><AppButton :disabled="!editable" @click="verify('stream')">验证流式</AppButton><AppButton :disabled="!editable" @click="verify('json')">验证非流式</AppButton></div>
        <p v-if="busy === 'verifying'" role="status" class="text-sm">正在验证，最多等待十分钟；不会重复发起请求。</p>
      </template>
      <AppButton v-if="error && !busy" @click="load">刷新配置</AppButton>
    </div>
  </AppCard>
</template>
