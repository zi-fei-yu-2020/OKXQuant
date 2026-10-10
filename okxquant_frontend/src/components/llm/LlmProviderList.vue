<script setup lang="ts">
import { Cpu, Plus, Search, ChevronRight } from 'lucide-vue-next'
import { providerGlyph, toneStyle } from './llmDisplay'

defineProps<{
  providers: any[]
  activeProviderId?: string
  activeModelId?: string
  busy: boolean
  canManage: boolean
}>()
const search = defineModel<string>('search', { required: true })
const emit = defineEmits<{ add: []; select: [provider: any]; toggle: [provider: any, event: Event] }>()

function isActive(provider: any, activeProviderId?: string, activeModelId?: string) {
  return provider.id === activeProviderId && provider.models?.some((m: any) => m.id === activeModelId)
}
</script>

<template>
  <section class="ui-card flex flex-wrap items-center justify-between gap-3 p-4 sm:p-5">
    <div class="flex min-w-0 items-center gap-3">
      <div class="llm-avatar" style="color: var(--color-brand)"><Cpu class="size-5" aria-hidden="true" /></div>
      <div class="min-w-0">
        <h2 class="text-base font-bold sm:text-lg">供应商</h2>
        <p class="text-xs" style="color: var(--text-muted)">管理 AI 模型渠道矩阵与 API 密钥直连配置</p>
      </div>
    </div>
    <button type="button" class="ui-button ui-button--primary ui-button--sm" :disabled="busy" @click="emit('add')">
      <Plus class="size-4" aria-hidden="true" /><span>添加供应商</span>
    </button>
  </section>

  <div class="relative">
    <Search class="pointer-events-none absolute left-3.5 top-1/2 size-4 -translate-y-1/2" style="color: var(--text-muted)" aria-hidden="true" />
    <input v-model="search" :disabled="busy" aria-label="搜索供应商或分组" placeholder="搜索供应商或分组" class="pl-10" />
  </div>

  <ul class="ui-card divide-y overflow-hidden">
    <li v-for="prov in providers" :key="prov.id" class="flex items-center gap-2 pr-3 transition-colors hover:bg-[var(--bg-card-subtle)]">
      <button type="button" class="flex min-w-0 flex-1 items-center gap-3.5 p-4 text-left" :aria-label="`配置 ${prov.name}`" @click="emit('select', prov)">
        <span class="llm-avatar text-sm font-bold" :style="{ color: providerGlyph(prov.id).tone }" aria-hidden="true">{{ providerGlyph(prov.id).glyph }}</span>
        <span class="min-w-0">
          <span class="flex flex-wrap items-center gap-2">
            <span class="truncate text-sm font-bold">{{ prov.name }}</span>
            <span v-if="isActive(prov, activeProviderId, activeModelId)" class="ui-badge ui-badge--success">主脑活跃</span>
          </span>
          <span class="mt-0.5 block text-xs" style="color: var(--text-faint)">{{ prov.models_count || 0 }} 个模型 · {{ prov.group || '其他' }}</span>
        </span>
      </button>
      <button type="button" class="ui-action ui-action--sm shrink-0 rounded-full border" :style="toneStyle(prov.enabled ? 'up' : 'down')"
        :disabled="busy || !canManage" :aria-label="`${prov.enabled ? '禁用' : '启用'} ${prov.name}`" @click="emit('toggle', prov, $event)">
        {{ prov.enabled ? '启用' : '禁用' }}
      </button>
      <ChevronRight class="size-4 shrink-0" style="color: var(--text-muted)" aria-hidden="true" />
    </li>
    <li v-if="!providers.length" class="py-12 text-center text-sm" style="color: var(--text-muted)">没有匹配的供应商</li>
  </ul>
</template>

<style scoped>
.llm-avatar {
  display: grid;
  place-items: center;
  flex-shrink: 0;
  width: 40px;
  height: 40px;
  border: 1px solid var(--border-subtle);
  border-radius: var(--radius-card);
  background: var(--bg-card-subtle);
}
</style>
