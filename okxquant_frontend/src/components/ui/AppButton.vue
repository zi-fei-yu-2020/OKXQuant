<script setup lang="ts">
import { LoaderCircle } from 'lucide-vue-next'
withDefaults(
  defineProps<{
    variant?: 'primary' | 'secondary' | 'ghost' | 'danger'
    size?: 'sm' | 'md'
    loading?: boolean
    disabled?: boolean
    type?: 'button' | 'submit' | 'reset'
  }>(),
  { variant: 'secondary', size: 'md', type: 'button' },
)
</script>
<template>
  <button
    :type="type"
    class="ui-button"
    :class="[`ui-button--${variant}`, `ui-button--${size}`]"
    :disabled="disabled || loading"
    :aria-busy="loading || undefined"
  >
    <LoaderCircle v-if="loading" class="size-4 animate-spin" aria-hidden="true" />
    <slot />
  </button>
</template>
