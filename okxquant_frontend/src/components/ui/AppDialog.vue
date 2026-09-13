<script setup lang="ts">
import { nextTick, onBeforeUnmount, ref, useId, watch } from 'vue'
import { X } from 'lucide-vue-next'
import ToastViewport from './ToastViewport.vue'
const props = withDefaults(
  defineProps<{
    open: boolean
    title: string
    description?: string
    size?: 'sm' | 'md' | 'lg' | 'xl'
    busy?: boolean
  }>(),
  { size: 'md' },
)
const emit = defineEmits<{ 'update:open': [value: boolean] }>()
const dialog = ref<HTMLDialogElement | null>(null)
const titleId = useId()
const descriptionId = useId()
let previousFocus: HTMLElement | null = null
function requestClose() {
  if (!props.busy) emit('update:open', false)
}
watch(
  () => props.open,
  async (open) => {
    await nextTick()
    if (open && dialog.value && !dialog.value.open) {
      previousFocus = document.activeElement instanceof HTMLElement ? document.activeElement : null
      dialog.value.showModal()
    } else if (!open && dialog.value?.open) {
      dialog.value.close()
      if (previousFocus?.isConnected) previousFocus.focus()
    }
  },
  { immediate: true, flush: 'post' },
)
onBeforeUnmount(() => {
  dialog.value?.close()
  if (previousFocus?.isConnected) previousFocus.focus()
})
function trapFocus(event: KeyboardEvent) {
  if (event.key !== 'Tab' || !dialog.value) return
  const focusable = Array.from(
    dialog.value.querySelectorAll<HTMLElement>(
      'button:not([disabled]), a[href], input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])',
    ),
  ).filter((element) => element.getClientRects().length > 0)
  const first = focusable[0]
  const last = focusable[focusable.length - 1]
  if (!first || !last) {
    event.preventDefault()
    dialog.value.focus()
    return
  }
  if (
    event.shiftKey &&
    (document.activeElement === first || document.activeElement === dialog.value)
  ) {
    event.preventDefault()
    last.focus()
  } else if (
    !event.shiftKey &&
    (document.activeElement === last || document.activeElement === dialog.value)
  ) {
    event.preventDefault()
    first.focus()
  }
}
function backdrop(event: MouseEvent) {
  if (!dialog.value || event.target !== dialog.value) return
  const rect = dialog.value.getBoundingClientRect()
  if (
    event.clientX < rect.left ||
    event.clientX > rect.right ||
    event.clientY < rect.top ||
    event.clientY > rect.bottom
  )
    requestClose()
}
</script>
<template>
  <Teleport to="body">
    <dialog
      ref="dialog"
      class="ui-dialog"
      :class="`ui-dialog--${size}`"
      :aria-labelledby="titleId"
      :aria-describedby="description ? descriptionId : undefined"
      @cancel.prevent="requestClose"
      @click="backdrop"
      @keydown="trapFocus"
    >
      <div class="ui-dialog__header">
        <div class="min-w-0">
          <h2 :id="titleId">{{ title }}</h2>
          <p v-if="description" :id="descriptionId">{{ description }}</p>
        </div>
        <button
          type="button"
          class="ui-icon-button shrink-0"
          aria-label="关闭弹窗"
          :disabled="busy"
          @click="requestClose"
        >
          <X class="size-4" aria-hidden="true" />
        </button>
      </div>
      <ToastViewport v-if="open" inline />
      <div class="ui-dialog__body"><slot /></div>
      <div v-if="$slots.footer" class="ui-dialog__footer"><slot name="footer" /></div>
    </dialog>
  </Teleport>
</template>
