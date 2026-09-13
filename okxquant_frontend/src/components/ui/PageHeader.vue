<script setup lang="ts">
defineProps<{ title: string; description?: string; eyebrow?: string }>()
</script>
<template>
  <div class="page-heading">
    <div class="min-w-0">
      <p v-if="eyebrow" class="page-heading__eyebrow">{{ eyebrow }}</p>
      <h1>{{ title }}</h1>
      <p v-if="description" class="page-heading__description">{{ description }}</p>
    </div>
    <div v-if="$slots.actions" class="page-heading__actions"><slot name="actions" /></div>
  </div>
</template>
