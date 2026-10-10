<script setup lang="ts">
import { computed } from 'vue'
import AppDialog from '../ui/AppDialog.vue'
import AppField from '../ui/AppField.vue'
import { CAPABILITIES, toneStyle } from './llmDisplay'

defineProps<{ open: boolean; editing: boolean; providerName?: string; busy: boolean; canManage: boolean }>()
const form = defineModel<any>('form', { required: true })
const emit = defineEmits<{ 'update:open': [value: boolean]; save: [] }>()

// Flagship model families additionally expose max/xhigh reasoning effort.
const effortOptions = computed(() => {
  const id = String(form.value.id || '').toLowerCase()
  const extreme = ['gpt-6', 'gpt-5', 'o3', 'o4', 'ultra', 'max'].some((key) => id.includes(key))
  return [
    ...(extreme ? [{ value: 'max', label: '极值 (max)' }, { value: 'xhigh', label: '超高 (xhigh)' }] : []),
    { value: 'high', label: '高 (high)' },
    { value: 'medium', label: '中 (medium)' },
    { value: 'low', label: '低 (low)' },
    { value: 'none', label: '关闭 (none)' },
  ]
})

function toggleCapability(cap: string) {
  const caps: string[] = form.value.capabilities
  const index = caps.indexOf(cap)
  if (index > -1) caps.splice(index, 1)
  else caps.push(cap)
}
</script>

<template>
  <AppDialog :open="open" :busy="busy" size="md" :title="editing ? '编辑模型' : '添加新模型'"
    :description="providerName ? `所属供应商：${providerName}` : undefined" @update:open="emit('update:open', $event)">
    <div class="space-y-4 text-sm">
      <AppField label="模型 ID" :hint="editing ? '已收录模型的 ID 不可修改' : undefined" v-slot="field">
        <input :id="field.id" v-model="form.id" :disabled="busy" :readonly="editing" placeholder="gemini-3.8-flash-high" />
      </AppField>
      <AppField label="展示名称" v-slot="field">
        <input :id="field.id" v-model="form.name" :disabled="busy" placeholder="Gemini 3.8 Flash (高推演)" />
      </AppField>
      <fieldset class="space-y-2">
        <legend class="text-[13px] font-semibold">能力标签</legend>
        <div class="flex flex-wrap gap-2">
          <button v-for="cap in CAPABILITIES" :key="cap.id" type="button" class="ui-action ui-action--sm border"
            :aria-pressed="form.capabilities.includes(cap.id)" :disabled="busy"
            :style="form.capabilities.includes(cap.id) ? toneStyle(cap.tone) : { backgroundColor: 'var(--bg-card-subtle)', borderColor: 'var(--border-subtle)', color: 'var(--text-muted)' }"
            @click="toggleCapability(cap.id)">
            {{ cap.label }} ({{ cap.id }})
          </button>
        </div>
      </fieldset>
      <AppField label="思考推演强度" v-slot="field">
        <select :id="field.id" v-model="form.reasoning_effort" :disabled="busy">
          <option v-for="opt in effortOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
      </AppField>
      <AppField label="上下文上限长度 (Tokens)" v-slot="field">
        <input :id="field.id" v-model.number="form.context_length" :disabled="busy" type="number" min="1" placeholder="1048576" />
      </AppField>
    </div>
    <template #footer>
      <button type="button" class="ui-button ui-button--secondary ui-button--sm" :disabled="busy" @click="emit('update:open', false)">取消</button>
      <button type="button" class="ui-button ui-button--primary ui-button--sm" :disabled="busy || !canManage" @click="emit('save')">保存模型</button>
    </template>
  </AppDialog>
</template>
