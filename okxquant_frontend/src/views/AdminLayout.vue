<script setup lang="ts">
import { computed, ref, watch } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { useAuthStore } from '../stores/auth'
import { useTheme } from '../composables/useTheme'
import { adminPages, adminNavigation } from '../config/navigation'
import { APP_LOGO_SRC, APP_VERSION } from '../config/branding'
import {
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

const pages = computed(() => adminPages || [])
const sections = computed(() => adminNavigation || [])

const page = computed(
  () => pages.value.find((item) => route.path === `/admin/${item.id}`) || pages.value[0] || { id: 'overview', label: '控制台', description: '' },
)

const activeSection = computed(() => {
  return sections.value.find((sec) => sec.items && sec.items.some((item) => route.path === `/admin/${item.id}`))
})

const sectionSiblings = computed(() => {
  if (!activeSection.value || !activeSection.value.items || activeSection.value.items.length <= 1) return []
  return activeSection.value.items.filter((item) => {
    if (item.superadminOnly && !auth?.isSuperadmin) return false
    return true
  })
})

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
        ><span class="brand-mark"><img :src="APP_LOGO_SRC" alt="" aria-hidden="true" class="brand-mark__image" width="34" height="34" /></span
        ><span>OKXQuant<span class="workspace-brand__sub">工作台</span></span><span class="workspace-version">{{ APP_VERSION }}</span></RouterLink
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

        <!-- Subroute tabs bar for multi-item sections -->
        <nav
          v-if="sectionSiblings.length > 1"
          class="flex flex-wrap items-center gap-1.5 border-b pb-3 mb-4 text-xs font-medium"
          style="border-color: var(--border-subtle)"
          aria-label="分类子导航"
        >
          <RouterLink
            v-for="sub in sectionSiblings"
            :key="sub.id"
            :to="`/admin/${sub.id}`"
            class="px-3 py-1.5 rounded-lg transition-colors inline-flex items-center gap-1.5"
            :class="route.path === `/admin/${sub.id}` ? 'bg-[var(--bg-card)] text-[var(--color-brand)] font-bold shadow-xs border' : 'text-[var(--text-muted)] hover:text-[var(--text-main)]'"
            style="border-color: var(--border-subtle)"
          >
            <component :is="sub.icon" class="size-3.5" aria-hidden="true" />
            <span>{{ sub.label }}</span>
            <span
              v-if="sub.advanced"
              class="text-[10px] px-1 py-0.5 rounded border uppercase font-mono tracking-tight"
              style="background: var(--bg-badge); border-color: var(--border-subtle); color: var(--color-brand)"
            >
              高级
            </span>
          </RouterLink>
        </nav>

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
