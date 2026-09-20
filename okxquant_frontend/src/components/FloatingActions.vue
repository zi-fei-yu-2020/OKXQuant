<script setup lang="ts">
// Kept at the existing import path; this is now a header action, not a floating toolbar.
import { computed, ref, onUnmounted } from 'vue'
import { Terminal, Copy } from 'lucide-vue-next'
import AppDialog from './ui/AppDialog.vue'
import AppButton from './ui/AppButton.vue'
import { useDashboardStore } from '../stores/dashboard'
import { useClipboard } from '../composables/useClipboard'
const store = useDashboardStore()
const { copyText } = useClipboard()
const promptModalOpen = ref(false), promptCopied = ref(false)
const prompt = computed(() => store.data?.ai_last_prompt?.trim() ? store.data.ai_last_prompt : '')
let copiedTimer: ReturnType<typeof setTimeout> | undefined
async function copyPrompt() {
  if (!prompt.value || !(await copyText(prompt.value))) return
  promptCopied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { promptCopied.value = false }, 1500)
}
onUnmounted(() => clearTimeout(copiedTimer))
</script>
<template>
  <button class="ui-button ui-button--secondary ui-button--sm" aria-label="查看实时提示词（最近保存记录）" aria-haspopup="dialog" data-header-prompt @click="promptModalOpen = true">
    <Terminal class="size-4" aria-hidden="true" /><span class="hidden sm:inline">实时提示词</span>
  </button>
  <AppDialog v-model:open="promptModalOpen" title="最近保存的决策提示词" size="xl" description="只读审计记录；不代表当前正在执行的决策。">
    <div class="space-y-3 min-w-0" data-prompt-audit>
      <div class="flex flex-wrap items-center justify-between gap-3">
        <p class="text-xs leading-relaxed flex-1 min-w-0" style="color:var(--text-muted)">
          {{ !prompt ? '暂无已保存提示词。待决策任务写入记录后可在此查阅。' : store.error || store.isStale ? '监控数据更新延迟，以下保留最近取得的提示词记录。' : '展示后端最近保存的原文；接口未提供该记录的独立时间与轮次，无法核验它属于本轮。' }}
        </p>
        <AppButton v-if="prompt" size="sm" @click="copyPrompt"><Copy class="size-4" aria-hidden="true" />{{ promptCopied ? '已复制' : '复制全文' }}</AppButton>
      </div>
      <pre v-if="prompt" tabindex="0" aria-label="最近保存的提示词原文" class="text-xs font-mono whitespace-pre-wrap break-words leading-relaxed select-text p-4 rounded-lg border max-h-[65vh] overflow-auto" style="background:var(--bg-card-subtle);border-color:var(--border-subtle);color:var(--text-main)">{{ prompt }}</pre>
    </div>
  </AppDialog>
</template>
