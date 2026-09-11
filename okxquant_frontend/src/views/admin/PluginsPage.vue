<script setup lang="ts">
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'

import { useErrorFeedback } from '../../composables/useFeedback'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Blocks, ShieldAlert, RefreshCw } from 'lucide-vue-next'

const { api } = useApi()
const data = ref<any>(null)
const loading = ref(true)
const errText = ref('')

async function load() {
  loading.value = true
  try {
    data.value = await api('/api/v1/admin/plugins')
    errText.value = ''
  } catch (e: any) {
    if (e?.silent) return
    errText.value = e.message
  } finally {
    loading.value = false
  }
}

onMounted(load)

useErrorFeedback(errText)
</script>

<template>
  <div class="space-y-4 max-w-[2160px] mx-auto">
    <LoadingState v-if="loading" />

    <template v-else-if="data">
      <AppCard
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div class="flex items-center justify-between mb-3">
          <div class="flex items-center space-x-2">
            <Blocks class="w-4 h-4" style="color: var(--color-brand)" />
            <h2
              class="text-sm font-black font-sans uppercase tracking-wide"
              style="color: var(--text-main)"
            >
              插件清单
            </h2>
          </div>
          <button
            @click="load"
            class="flex items-center space-x-1 px-2.5 py-1 rounded-lg border text-xs font-sans cursor-pointer transition-all shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <RefreshCw class="w-3 h-3" />
            <span>刷新</span>
          </button>
        </div>

        <div
          class="table-scroll-container rounded-lg border my-2"
          style="border-color: var(--border-subtle)"
        >
          <table class="w-full text-left text-sm font-sans whitespace-nowrap">
            <thead>
              <tr
                class="border-b text-xs uppercase tracking-wider font-bold"
                style="
                  border-color: var(--border-subtle);
                  background-color: var(--bg-card-subtle);
                  color: var(--text-muted);
                "
              >
                <th class="py-2.5 px-3">插件</th>
                <th class="py-2.5 px-3">类型</th>
                <th class="py-2.5 px-3">版本</th>
                <th class="py-2.5 px-3">权限声明</th>
                <th class="py-2.5 px-3">启用开关</th>
                <th class="py-2.5 px-3">健康状态</th>
              </tr>
            </thead>
            <tbody>
              <tr
                v-for="p in data.plugins"
                :key="p.plugin_id"
                class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                style="border-color: var(--border-subtle)"
              >
                <td class="py-2.5 px-3 font-bold" style="color: var(--text-main)">
                  {{ p.name }}
                  <div class="text-xs font-normal" style="color: var(--text-faint)">
                    {{ p.plugin_id }}
                  </div>
                </td>
                <td class="py-2.5 px-3" style="color: var(--text-muted)">{{ p.plugin_type }}</td>
                <td class="py-2.5 px-3 num-tabular" style="color: var(--text-faint)">
                  {{ p.version }}
                </td>
                <td class="py-2.5 px-3 text-xs" style="color: var(--text-muted)">
                  {{ (p.permissions || []).join(', ') }}
                </td>
                <td class="py-2.5 px-3" style="color: var(--text-faint)">
                  {{ p.enabled_key || '默认启用' }}
                </td>
                <td
                  class="py-2.5 px-3 font-bold"
                  :class="p.health === 'healthy' ? 'text-emerald-500' : 'text-amber-500'"
                >
                  {{
                    p.health === 'healthy' ? '正常' : p.health === 'disabled' ? '已禁用' : p.health
                  }}
                </td>
              </tr>
            </tbody>
          </table>
        </div>
      </AppCard>

      <AppCard
        class="rounded-xl border p-4 flex items-start gap-3 shadow-xs"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <ShieldAlert class="w-5 h-5 text-amber-500 shrink-0 mt-0.5" />
        <div>
          <h3 class="text-sm font-bold text-amber-500 font-sans mb-1">
            安装策略：{{
              data.installation_policy === 'builtin-only' ? '仅内置插件' : data.installation_policy
            }}
          </h3>
          <p class="text-xs font-sans leading-relaxed" style="color: var(--text-muted)">
            {{ data.reason }}
          </p>
        </div>
      </AppCard>
    </template>
  </div>
</template>
