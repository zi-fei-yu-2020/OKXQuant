<script setup lang="ts">
defineProps<{ title?: string; description?: string; padded?: boolean }>()
</script>
<template>
  <section class="ui-card">
    <div v-if="title || $slots.header" class="ui-card__header">
      <slot name="header"
        ><div>
          <h2>{{ title }}</h2>
          <p v-if="description">{{ description }}</p>
        </div></slot
      ><slot name="actions" />
    </div>
    <div v-if="padded" class="ui-card__body"><slot /></div>
    <slot v-else />
    <div v-if="$slots.footer" class="ui-card__footer"><slot name="footer" /></div>
  </section>
</template>
