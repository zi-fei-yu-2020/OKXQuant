<script setup lang="ts">
import { computed, useId } from 'vue'
const props = defineProps<{
  label?: string
  for?: string
  hint?: string
  error?: string
  required?: boolean
}>()
const generatedId = useId()
const inputId = computed(() => props.for || `field-${generatedId}`)
</script>
<template>
  <div class="ui-field">
    <label :for="inputId"
      ><slot name="label">{{ label }}</slot
      ><span v-if="required" class="text-[var(--color-down)]" aria-hidden="true"> *</span></label
    >
    <slot
      :id="inputId"
      :description-id="error ? `${inputId}-error` : hint ? `${inputId}-hint` : undefined"
    />
    <p v-if="error" :id="`${inputId}-error`" class="ui-field__error" role="alert">{{ error }}</p>
    <p v-else-if="hint" :id="`${inputId}-hint`" class="ui-field__hint">{{ hint }}</p>
  </div>
</template>
