<script setup lang="ts">
// Authenticated advanced prompt audit action; render only a verified receipt.
import { ref, watch, onUnmounted } from 'vue'
import { Terminal, Copy } from 'lucide-vue-next'
import AppDialog from './ui/AppDialog.vue'
import AppButton from './ui/AppButton.vue'
import { useDashboardStore } from '../stores/dashboard'
import { useClipboard } from '../composables/useClipboard'
import { useAuthStore } from '../stores/auth'

const store = useDashboardStore()
const auth = useAuthStore()
const { copyText } = useClipboard()
const promptModalOpen = ref(false)
const promptCopied = ref(false)
const promptLoading = ref(false)

const prompt = ref(''), promptScope = ref('')
let promptSession = '', generation = 0
let copiedTimer: ReturnType<typeof setTimeout> | undefined
watch([() => auth.token, () => store.data?.account_source_id], () => {
  if (prompt.value && (promptSession !== auth.token || promptScope.value !== store.data?.account_source_id)) {
    generation += 1
    prompt.value = ''
    promptScope.value = ''
  }
}, { flush: 'sync' })
async function openPrompt() {
  promptModalOpen.value = true
  if (promptLoading.value) return
  const epoch = ++generation
  const session = auth.token
  prompt.value = ''
  promptScope.value = ''
  promptLoading.value = true
  try {
    const value = await store.fetchLatestPrompt()
    const verified = store.getVerifiedPrompt()
    if (epoch === generation && session === auth.token && value && verified?.prompt === value) {
      promptSession = session
      promptScope.value = verified.scope
      prompt.value = value
    }
  } catch { /* An unavailable/new account never displays a prior private prompt. */ }
  finally { promptLoading.value = false }
}
async function copyPrompt() {
  if (!prompt.value || !(await copyText(prompt.value))) return
  promptCopied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { promptCopied.value = false }, 1500)
}
onUnmounted(() => { generation += 1; clearTimeout(copiedTimer) })
</script>
<template>
  <button class="ui-button ui-button--secondary ui-button--sm" aria-label="查看实时提示词（最近保存记录）" aria-haspopup="dialog" data-header-prompt @click="openPrompt">
    <Terminal class="size-4" aria-hidden="true" /><span class="hidden sm:inline">实时提示词</span>
  </button>
  <AppDialog v-model:open="promptModalOpen" title="最近保存的决策提示词" size="xl" description="只读审计记录；不代表当前正在执行的决策。">
    <div class="space-y-3 min-w-0" data-prompt-audit>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-xs leading-relaxed flex-1 min-w-0" style="color:var(--text-muted)">
          {{ promptLoading ? '正在加载提示词…' : !prompt ? '暂无已保存提示词。待决策任务写入记录后可在此查阅。' : store.error || store.isStale ? '监控数据更新延迟，以下保留最近取得的提示词记录。' : '已核验账户归属的保存记录，不代表当前执行或模型调用成功' }}
        </p>
        <AppButton v-if="prompt" size="sm" @click="copyPrompt"><Copy class="size-4" aria-hidden="true" />{{ promptCopied ? '已复制' : '复制全文' }}</AppButton>
      </div>
      <pre v-if="prompt" tabindex="0" aria-label="最近保存的提示词原文" class="text-xs font-mono whitespace-pre-wrap break-words leading-relaxed select-text p-4 rounded-lg border max-h-[65vh] overflow-auto" style="background:var(--bg-card-subtle);border-color:var(--border-subtle);color:var(--text-main)">{{ prompt }}</pre>
    </div>
  </AppDialog>
</template>
