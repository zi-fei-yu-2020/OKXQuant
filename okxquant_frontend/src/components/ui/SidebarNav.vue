<script setup lang="ts">
import { adminNavigation } from '../../config/navigation'
import { useRouter } from 'vue-router'
const router = useRouter()
const prefetched = new Set<string>()
function preload(id: string) {
  if (prefetched.has(id)) return
  prefetched.add(id)
  for (const record of router.resolve(`/admin/${id}`).matched) {
    const component = record.components?.default
    if (typeof component === 'function') {
      // Fetch code on navigation intent, never account/configuration data.
      Promise.resolve((component as () => Promise<unknown>)()).catch(() => prefetched.delete(id))
    }
  }
}
defineEmits<{ navigate: [] }>()
</script>
<template>
  <nav class="workspace-nav" aria-label="管理导航">
    <section v-for="group in adminNavigation" :key="group.label" class="workspace-nav__group">
      <h2>{{ group.label }}</h2>
      <RouterLink
        v-for="item in group.items"
        :key="item.id"
        :to="`/admin/${item.id}`"
        class="workspace-nav__item"
        active-class="is-active"
        @mouseenter="preload(item.id)"
        @focus="preload(item.id)"
        @click="$emit('navigate')"
        ><component :is="item.icon" class="size-[18px] shrink-0" aria-hidden="true" /><span>{{
          item.label
        }}</span></RouterLink
      >
    </section>
  </nav>
</template>
