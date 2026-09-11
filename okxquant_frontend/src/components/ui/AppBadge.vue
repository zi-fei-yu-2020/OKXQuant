<script setup lang="ts">
withDefaults(
  defineProps<{ tone?: 'neutral' | 'success' | 'warning' | 'danger' | 'brand'; dot?: boolean }>(),
  { tone: 'neutral' },
)
</script>
<template>
  <span class="ui-badge" :class="`ui-badge--${tone}`"
    ><span v-if="dot" class="ui-badge__dot" aria-hidden="true"></span><slot
  /></span>
</template>
