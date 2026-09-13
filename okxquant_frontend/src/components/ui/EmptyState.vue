<script setup lang="ts">
import { Inbox } from 'lucide-vue-next'
defineProps<{ title: string; description?: string; compact?: boolean }>()
</script>
<template>
  <div class="ui-empty" :class="{ 'ui-empty--compact': compact }">
    <div class="ui-empty__icon">
      <slot name="icon"><Inbox class="size-6" aria-hidden="true" /></slot>
    </div>
    <h3>{{ title }}</h3>
    <p v-if="description">{{ description }}</p>
    <div v-if="$slots.action" class="mt-4"><slot name="action" /></div>
  </div>
</template>
