<script setup lang="ts">
import { CheckCircle2, CircleAlert, Info, TriangleAlert, X } from 'lucide-vue-next'
import { useToast } from '../../composables/useFeedback'
defineProps<{ inline?: boolean }>()
const { items, dismiss } = useToast()
const icons = { success: CheckCircle2, error: CircleAlert, warning: TriangleAlert, info: Info }
const titles = { success: '操作成功', error: '操作未完成', warning: '请注意', info: '提示' }
</script>
<template>
  <div
    v-if="items.length"
    :class="inline ? 'ui-dialog-notifications' : 'ui-toasts'"
    aria-label="操作通知"
  >
    <TransitionGroup name="toast"
      ><div
        v-for="item in items"
        :key="item.id"
        class="ui-toast"
        :class="`ui-toast--${item.tone}`"
        :role="item.tone === 'error' ? 'alert' : 'status'"
      >
        <component :is="icons[item.tone]" class="ui-toast__icon size-5" aria-hidden="true" />
        <div class="min-w-0 flex-1">
          <strong
            >{{ titles[item.tone]
            }}<span v-if="item.count > 1" class="ui-toast__count">{{ item.count }}</span></strong
          >
          <p>{{ item.message }}</p>
        </div>
        <button
          type="button"
          class="ui-icon-button"
          aria-label="关闭通知"
          @click="dismiss(item.id)"
        >
          <X class="size-4" aria-hidden="true" />
        </button></div
    ></TransitionGroup>
  </div>
</template>
