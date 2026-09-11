<script setup lang="ts">
import { useToast } from '../../composables/useFeedback'
const toast = useToast()
import AppCard from '../../components/ui/AppCard.vue'
import LoadingState from '../../components/ui/LoadingState.vue'
import EmptyState from '../../components/ui/EmptyState.vue'

import { ref, onMounted } from 'vue'
import { useApi } from '../../composables/useApi'
import { Info, GitBranch, Download } from 'lucide-vue-next'

const { api } = useApi()
const about = ref<any>(null)
const loading = ref(true)
const updateChecking = ref(false)
const updateResult = ref<any>(null)

async function loadAbout() {
  loading.value = true
  try {
    about.value = await api('/api/v1/admin/about')
  } catch (e: any) {
    if (e?.silent) return
    toast.error(e.message)
  } finally {
    loading.value = false
  }
}

async function checkUpdate() {
  updateChecking.value = true
  try {
    const result = await api('/api/v1/admin/update-status')
    updateResult.value = result
    if (result.error) toast.error(result.error)
    else
      toast.success(
        result.managed_externally
          ? '此部署由宿主机管理，请在宿主机更新镜像。'
          : Number(result.behind) > 0
            ? `有 ${result.behind} 个上游提交可更新。`
            : '当前分支没有待同步的上游提交。',
      )
  } catch (e: any) {
    if (e?.silent) return
    updateResult.value = null
    toast.error(e.message)
  } finally {
    updateChecking.value = false
  }
}

onMounted(() => {
  loadAbout()
})
</script>

<template>
  <div class="space-y-4">
    <LoadingState v-if="loading" />

    <template v-else-if="about">
      <!-- About Cards -->
      <div class="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <AppCard
          class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="flex items-center justify-between pb-3 mb-3 border-b"
            style="border-color: var(--border-subtle)"
          >
            <div class="flex items-center space-x-2">
              <Info class="w-4 h-4" style="color: var(--color-brand)" />
              <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">关于 OKXQuant</h2>
            </div>
            <span
              class="text-xs font-sans px-2 py-0.5 rounded border font-bold"
              style="
                background-color: var(--color-up-bg);
                color: var(--color-up);
                border-color: var(--color-up-border);
              "
              >OPEN SOURCE</span
            >
          </div>
          <div class="text-sm font-sans space-y-1.5" style="color: var(--text-muted)">
            <div>
              产品: <strong style="color: var(--text-main)">{{ about.product?.name }}</strong>
            </div>
            <div>
              版本: <strong style="color: var(--color-brand)">{{ about.product?.version }}</strong>
            </div>
            <div>
              控制面:
              <span style="color: var(--text-main)">{{ about.product?.control_plane }}</span>
            </div>
          </div>
          <a
            href="https://github.com/zi-fei-yu-2020/OKXQuant"
            target="_blank"
            class="inline-flex items-center space-x-1.5 mt-4 px-3 py-1.5 rounded-lg border text-sm font-sans font-bold transition-all cursor-pointer shadow-xs"
            style="background-color: var(--text-main); color: var(--bg-card)"
          >
            <GitBranch class="w-3.5 h-3.5" />
            <span>GitHub 仓库</span>
          </a>
        </AppCard>

        <AppCard
          class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
          style="background-color: var(--bg-card); border-color: var(--border-subtle)"
        >
          <div
            class="flex items-center justify-between pb-3 mb-3 border-b"
            style="border-color: var(--border-subtle)"
          >
            <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">组件版本</h2>
            <span class="text-xs font-sans" style="color: var(--text-faint)">生产运行栈</span>
          </div>
          <div class="table-scroll-container">
            <table class="w-full text-left text-sm font-sans whitespace-nowrap">
              <tbody>
                <tr
                  v-for="c in about.components"
                  :key="c.name"
                  class="border-b last:border-b-0 hover:bg-[var(--bg-card-hover)] transition-colors"
                  style="border-color: var(--border-subtle)"
                >
                  <td class="py-2.5" style="color: var(--text-muted)">{{ c.name }}</td>
                  <td class="py-2.5 font-bold num-tabular" style="color: var(--text-main)">
                    {{ c.version }}
                  </td>
                </tr>
              </tbody>
            </table>
          </div>
        </AppCard>
      </div>

      <!-- Update -->
      <AppCard
        class="rounded-xl border p-4 sm:p-5 shadow-xs transition-colors"
        style="background-color: var(--bg-card); border-color: var(--border-subtle)"
      >
        <div
          class="flex items-center justify-between pb-3 mb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <h2 class="text-sm font-bold font-sans" style="color: var(--text-main)">安全更新</h2>
          <span
            class="text-xs font-sans px-2 py-0.5 rounded border font-bold"
            style="
              background-color: var(--color-brand-bg);
              color: var(--color-brand);
              border-color: var(--color-brand-border);
            "
            >GIT</span
          >
        </div>
        <div class="flex space-x-2">
          <button
            @click="checkUpdate"
            :disabled="updateChecking"
            class="flex items-center space-x-1 px-3 py-1.5 rounded-lg border text-sm font-sans font-bold transition-all cursor-pointer shadow-xs"
            style="
              background-color: var(--bg-card-subtle);
              border-color: var(--border-medium);
              color: var(--text-main);
            "
          >
            <Download v-if="!updateChecking" class="w-3.5 h-3.5" />
            <span>{{ updateChecking ? '检查中...' : '检查远端更新' }}</span>
          </button>
        </div>
        <div
          v-if="updateResult"
          class="mt-3 text-sm font-sans p-2.5 rounded-lg border"
          :style="
            updateResult.error
              ? {
                  backgroundColor: 'var(--color-down-bg)',
                  borderColor: 'var(--color-down-border)',
                  color: 'var(--color-down)',
                }
              : {
                  backgroundColor: 'var(--color-up-bg)',
                  borderColor: 'var(--color-up-border)',
                  color: 'var(--color-up)',
                }
          "
        >
          {{ updateResult.error || updateResult.message || JSON.stringify(updateResult) }}
        </div>
        <p class="mt-3 text-xs font-sans" style="color: var(--text-faint)">
          执行更新必须输入确认短语 UPDATE OKXQuant；工作区不干净、远端不可达或无法快进时自动拒绝。
        </p>
      </AppCard>
    </template>
    <EmptyState v-else title="版本信息暂不可用" description="请检查服务状态后重新尝试。"
      ><template #action
        ><button class="ui-button ui-button--secondary" @click="loadAbout">
          重新加载
        </button></template
      ></EmptyState
    >
  </div>
</template>
