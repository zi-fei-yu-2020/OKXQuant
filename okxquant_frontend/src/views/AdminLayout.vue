<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTheme } from '../composables/useTheme'
import { adminPages } from '../config/navigation'
import {
  Activity,
  ArrowUpRight,
  ChevronRight,
  LogOut,
  Menu,
  Moon,
  Sun,
  BookOpen,
} from 'lucide-vue-next'
import SidebarNav from '../components/ui/SidebarNav.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import AppDialog from '../components/ui/AppDialog.vue'
const route = useRoute()
const router = useRouter()
const auth = useAuthStore()
const { theme, toggleTheme } = useTheme()
const drawerOpen = ref(false)
const page = computed(
  () => adminPages.find((item) => route.path === `/admin/${item.id}`) || adminPages[0]!,
)
watch(
  () => route.path,
  () => {
    drawerOpen.value = false
  },
)
function logout() {
  auth.logout()
  router.push('/admin/login')
}
</script>
<template>
  <div class="workspace-shell admin-shell">
    <a class="skip-link" href="#workspace-content">跳到页面内容</a>
    <aside class="workspace-sidebar">
      <RouterLink to="/admin/overview" class="workspace-brand"
        ><span class="brand-mark"><Activity class="size-5" aria-hidden="true" /></span
        ><span>OKXQuant<span class="workspace-brand__sub">Quantum workspace</span></span
        ><span class="workspace-version">7.3</span></RouterLink
      >
      <SidebarNav />
      <div class="workspace-sidebar__footer">
        <RouterLink to="/docs"
          ><BookOpen class="size-4" aria-hidden="true" />使用文档<ArrowUpRight
            class="size-3.5 ml-auto"
            aria-hidden="true"
        /></RouterLink>
        <div class="workspace-profile">
          <span class="workspace-avatar">{{
            (auth.user?.username || 'A').slice(0, 1).toUpperCase()
          }}</span>
          <div class="min-w-0">
            <strong>{{ auth.user?.username || '管理员' }}</strong>
            <p>{{ auth.isSuperadmin ? '超级管理员' : '管理员' }}</p>
          </div>
          <button class="ui-icon-button ml-auto" aria-label="退出登录" @click="logout">
            <LogOut class="size-4" aria-hidden="true" />
          </button>
        </div>
      </div>
    </aside>
    <div class="workspace-main">
      <header class="workspace-topbar">
        <div class="flex items-center gap-3 min-w-0">
          <button
            class="ui-icon-button mobile-menu"
            aria-label="打开管理导航"
            @click="drawerOpen = true"
          >
            <Menu class="size-5" aria-hidden="true" /></button
          ><span class="text-[var(--text-faint)] hidden sm:inline">工作空间</span
          ><ChevronRight
            class="size-3.5 text-[var(--text-faint)] hidden sm:inline"
            aria-hidden="true"
          /><span class="font-medium truncate">{{ page.label }}</span>
        </div>
        <div class="flex items-center gap-2">
          <button
            class="ui-icon-button"
            :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
            @click="toggleTheme"
          >
            <Sun v-if="theme === 'dark'" class="size-[18px]" aria-hidden="true" /><Moon
              v-else
              class="size-[18px]"
              aria-hidden="true"
            /></button
          ><RouterLink to="/" class="ui-button ui-button--secondary ui-button--sm"
            >监控终端<ArrowUpRight class="size-4" aria-hidden="true"
          /></RouterLink>
        </div>
      </header>
      <main id="workspace-content" class="workspace-content" tabindex="-1">
        <PageHeader :title="page.label" :description="page.description" />
        <div class="admin-content"><RouterView /></div>
        <footer class="workspace-footer">
          <span>OKXQuant</span><span>策略与账户操作均保留原有权限校验</span>
        </footer>
      </main>
    </div>
    <AppDialog v-model:open="drawerOpen" title="工作空间导航" size="sm"
      ><SidebarNav @navigate="drawerOpen = false" /><template #footer
        ><button class="ui-button ui-button--ghost" @click="logout">
          <LogOut class="size-4" />退出登录
        </button></template
      ></AppDialog
    >
  </div>
</template>
