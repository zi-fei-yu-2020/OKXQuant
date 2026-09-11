<script setup lang="ts">
import { onMounted, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from './stores/auth'
import FeedbackHost from './components/ui/FeedbackHost.vue'
import { useTheme } from './composables/useTheme'

const { initTheme } = useTheme()
const auth = useAuthStore()
const route = useRoute()
const router = useRouter()
// Navigation guards alone do not run when a request expires the current session.
watch(
  [() => auth.isAuthenticated, () => route.meta.requiresAuth],
  ([authenticated, protectedPage]) => {
    if (!authenticated && protectedPage) void router.replace({ name: 'admin-login' })
  },
  { immediate: true },
)
onMounted(() => {
  initTheme()
})
</script>

<template>
  <router-view />
  <FeedbackHost />
</template>
