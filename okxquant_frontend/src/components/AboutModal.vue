<script setup lang="ts">
import AppDialog from './ui/AppDialog.vue'

import { ref } from 'vue'
import { Code, BookOpen, ExternalLink, Copy, Check } from 'lucide-vue-next'

defineProps<{
  visible: boolean
}>()

const emit = defineEmits<{
  (e: 'close'): void
}>()

const copiedTarget = ref<string | null>(null)

async function copyToClipboard(text: string, targetName: string) {
  try {
    await navigator.clipboard.writeText(text)
    copiedTarget.value = targetName
    setTimeout(() => {
      if (copiedTarget.value === targetName) {
        copiedTarget.value = null
      }
    }, 2000)
  } catch (err) {
    console.error('Copy failed:', err)
  }
}
</script>

<template>
  <Teleport to="body">
    <AppDialog
      v-if="visible"
      :open="!!visible"
      title="关于 OKXQuant"
      size="md"
      @update:open="
        (open) => {
          if (!open) {
            emit('close')
          }
        }
      "
      ><div
        class="dialog-content p-5 sm:p-6 space-y-4 font-mono text-xs animate-scale-up my-auto"
        style="
          background-color: var(--bg-card);
          border-color: var(--border-subtle);
          color: var(--text-main);
        "
      >
        <!-- Modal Header -->
        <div
          class="flex items-center justify-between pb-3 border-b"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2.5">
            <div
              class="w-7 h-7 rounded-lg flex items-center justify-center font-bold border"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-medium);
                color: var(--text-main);
              "
            >
              <Code class="w-4 h-4" />
            </div>
            <div>
              <h3
                class="text-sm font-bold uppercase tracking-wide flex items-center gap-2"
                style="color: var(--text-main)"
              >
                <span>OKXQuant</span>
                <span
                  class="px-1.5 py-0.2 rounded text-[10px] font-mono font-bold border"
                  style="
                    background-color: var(--color-brand-bg);
                    color: var(--color-brand);
                    border-color: var(--color-brand-border);
                  "
                >
                  v0.1.0
                </span>
              </h3>
            </div>
          </div>
        </div>

        <!-- Description -->
        <p class="text-xs font-sans leading-relaxed" style="color: var(--text-muted)">
          面向 OKX 永续合约的 LLM
          辅助量化交易监控系统。提供市场信号、多模型决策审计、订单与保护状态查询，以及策略复盘。
          任务时间与风控参数以当前配置为准；展示不保证保护已生效，也不保证盈利。
        </p>

        <!-- Links Grid -->
        <div class="space-y-2">
          <!-- System Documentation Link -->
          <a
            href="/docs"
            class="p-3 rounded-xl border flex items-center justify-between transition-all group cursor-pointer"
            style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            @click="emit('close')"
          >
            <div class="flex items-center space-x-2.5 min-w-0">
              <div
                class="w-6 h-6 rounded-lg border flex items-center justify-center shrink-0"
                style="
                  background-color: var(--bg-card);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              >
                <BookOpen class="w-3.5 h-3.5" />
              </div>
              <div class="truncate">
                <span class="font-bold block truncate" style="color: var(--text-main)">
                  系统开发与使用文档 (Docs)
                </span>
                <span class="text-[10px] truncate block" style="color: var(--text-faint)">
                  架构说明 · 提示词变量插槽 · 物理拦截插件规范
                </span>
              </div>
            </div>
            <div
              class="flex items-center space-x-1 shrink-0 font-medium"
              style="color: var(--color-brand)"
            >
              <span class="text-[11px]">查看文档</span>
              <ExternalLink class="w-3 h-3" />
            </div>
          </a>
        </div>

        <!-- Quick Commands -->
        <div class="space-y-2 pt-2 border-t" style="border-color: var(--border-subtle)">
          <div class="text-[11px] font-bold uppercase" style="color: var(--text-faint)">
            常用运维命令
          </div>

          <div class="space-y-1.5">
            <div
              class="flex items-center justify-between p-2 rounded-lg border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span class="truncate pr-2 font-mono text-[11px]" style="color: var(--text-muted)">
                cd okxquant_frontend && npm run build
              </span>
              <button
                @click="copyToClipboard('cd okxquant_frontend && npm run build', 'build')"
                class="px-2 py-1 rounded border text-[10px] font-mono cursor-pointer transition-colors shrink-0 flex items-center space-x-1"
                style="
                  background-color: var(--bg-card);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              >
                <Check v-if="copiedTarget === 'build'" class="w-3 h-3 text-emerald-500" />
                <Copy v-else class="w-3 h-3" />
                <span>{{ copiedTarget === 'build' ? '已复制' : '复制' }}</span>
              </button>
            </div>

            <div
              class="flex items-center justify-between p-2 rounded-lg border"
              style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
            >
              <span class="truncate pr-2 font-mono text-[11px]" style="color: var(--text-muted)">
                /app/venv/bin/python3 -m unittest discover -s tests
              </span>
              <button
                @click="
                  copyToClipboard(
                    '/app/venv/bin/python3 -m unittest discover -s tests -p &quot;test_*.py&quot;',
                    'test',
                  )
                "
                class="px-2 py-1 rounded border text-[10px] font-mono cursor-pointer transition-colors shrink-0 flex items-center space-x-1"
                style="
                  background-color: var(--bg-card);
                  border-color: var(--border-subtle);
                  color: var(--text-main);
                "
              >
                <Check v-if="copiedTarget === 'test'" class="w-3 h-3 text-emerald-500" />
                <Copy v-else class="w-3 h-3" />
                <span>{{ copiedTarget === 'test' ? '已复制' : '复制' }}</span>
              </button>
            </div>
          </div>
        </div>

        <!-- Footer -->
        <div class="pt-2 text-center text-[10px]" style="color: var(--text-faint)">
          OKXQuant · ENTERPRISE QUANTITATIVE FRAMEWORK
        </div>
      </div></AppDialog
    >
  </Teleport>
</template>
