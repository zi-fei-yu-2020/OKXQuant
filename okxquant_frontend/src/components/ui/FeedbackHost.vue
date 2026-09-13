<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute } from 'vue-router'
import { useDialogs } from '../../composables/useDialogs'
import AppDialog from './AppDialog.vue'
import AppButton from './AppButton.vue'
import ToastViewport from './ToastViewport.vue'
const { active, finish, cancelAll } = useDialogs()
const route = useRoute()
watch(() => route.fullPath, cancelAll)
const input = ref('')
const matchesPhrase = computed(
  () =>
    !active.value?.options.requiredText ||
    input.value.trim().toUpperCase() === active.value.options.requiredText.trim().toUpperCase(),
)
watch(
  () => active.value?.id,
  () => {
    input.value = active.value?.options.defaultValue || ''
  },
)
function cancel() {
  finish(active.value?.kind === 'prompt' ? null : false)
}
function submit() {
  if (!matchesPhrase.value) return
  finish(active.value?.kind === 'prompt' ? input.value : true)
}
</script>
<template>
  <Teleport to="body"><ToastViewport /></Teleport>
  <AppDialog
    :open="!!active"
    :title="active?.options.title || (active?.kind === 'prompt' ? '填写确认信息' : '确认操作')"
    size="sm"
    @update:open="
      (value) => {
        if (!value) cancel()
      }
    "
  >
    <form v-if="active" id="global-confirm-form" @submit.prevent="submit">
      <p id="global-confirm-message" class="ui-confirm-message">{{ active.message }}</p>
      <div v-if="active.kind === 'prompt'" class="ui-field mt-5">
        <label for="global-confirm-input">{{ active.options.label || '输入内容' }}</label
        ><input
          id="global-confirm-input"
          aria-describedby="global-confirm-message global-confirm-hint"
          v-model="input"
          :placeholder="active.options.placeholder || '请按上方说明输入'"
          autocomplete="off"
          autofocus
        />
        <p id="global-confirm-hint" class="ui-field__hint">
          {{
            active.options.requiredText
              ? `请输入确认短语：${active.options.requiredText}`
              : '取消不会提交任何修改。'
          }}
        </p>
      </div>
    </form>
    <template #footer
      ><AppButton autofocus @click="cancel">取消</AppButton
      ><AppButton
        type="submit"
        form="global-confirm-form"
        :disabled="!matchesPhrase"
        :variant="active?.options.danger === false ? 'primary' : 'danger'"
        >{{ active?.options.confirmLabel || '确认并继续' }}</AppButton
      ></template
    >
  </AppDialog>
</template>
