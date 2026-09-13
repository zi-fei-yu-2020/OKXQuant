<script setup lang="ts">
defineProps<{ modelValue: boolean; label: string; disabled?: boolean }>()
const emit = defineEmits<{ 'update:modelValue': [value: boolean] }>()
</script>
<template>
  <button
    type="button"
    role="switch"
    class="ui-switch"
    :aria-label="label"
    :aria-checked="modelValue"
    :disabled="disabled"
    @click="emit('update:modelValue', !modelValue)"
  >
    <span class="ui-switch__track" aria-hidden="true"><span class="ui-switch__thumb"></span></span>
  </button>
</template>
<style scoped>
.ui-switch {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 44px;
  min-width: 44px;
  height: 44px;
  min-height: 44px;
  padding: 0;
  border: 0;
  background: transparent;
  flex-shrink: 0;
}
.ui-switch__track {
  position: relative;
  display: block;
  width: 42px;
  height: 24px;
  border-radius: 99px;
  background: var(--border-medium);
  transition: background-color 0.15s;
}
.ui-switch[aria-checked='true'] .ui-switch__track {
  background: var(--color-brand);
}
.ui-switch__thumb {
  position: absolute;
  top: 3px;
  left: 3px;
  width: 18px;
  height: 18px;
  border-radius: 50%;
  background: #fff;
  box-shadow: 0 1px 3px #10182830;
  transition: transform 0.15s;
}
.ui-switch[aria-checked='true'] .ui-switch__thumb {
  transform: translateX(18px);
}
</style>
