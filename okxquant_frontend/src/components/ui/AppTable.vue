<script setup lang="ts">
withDefaults(defineProps<{ label?: string }>(), { label: '数据表格' })
</script>
<template>
  <div class="table-scroll-container" role="region" :aria-label="label" tabindex="0"><slot /></div>
</template>
