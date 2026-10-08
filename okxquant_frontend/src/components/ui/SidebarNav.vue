<script setup lang="ts">
import { ref, watch } from 'vue'
import { adminNavigation, type AdminNavSection, type AdminNavItem } from '../../config/navigation'
import { useRouter, useRoute } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { ChevronDown, ChevronRight } from 'lucide-vue-next'

const router = useRouter()
const route = useRoute()
const auth = useAuthStore()

const prefetched = new Set<string>()
function preload(id: string) {
  if (prefetched.has(id)) return
  prefetched.add(id)
  for (const record of router.resolve(`/admin/${id}`).matched) {
    const component = record.components?.default
    if (typeof component === 'function') {
      Promise.resolve((component as () => Promise<unknown>)()).catch(() => prefetched.delete(id))
    }
  }
}

const emit = defineEmits<{ navigate: [] }>()

// Track manually toggled expanded sections, defaulting to expanding the active section
const expandedSections = ref<Record<string, boolean>>({})

function isSectionActive(section: AdminNavSection): boolean {
  return section.items.some((item) => route.path === `/admin/${item.id}`)
}

function isSectionExpanded(section: AdminNavSection): boolean {
  if (expandedSections.value[section.id] !== undefined) {
    return expandedSections.value[section.id]
  }
  return isSectionActive(section)
}

function toggleSection(section: AdminNavSection) {
  const current = isSectionExpanded(section)
  expandedSections.value = {
    ...expandedSections.value,
    [section.id]: !current,
  }
}

function handleSectionClick(section: AdminNavSection) {
  const visible = visibleChildren(section)
  if (visible.length === 1) {
    router.push(`/admin/${visible[0]!.id}`)
    emit('navigate')
  } else {
    toggleSection(section)
  }
}

function visibleChildren(section: AdminNavSection): AdminNavItem[] {
  return section.items.filter((item) => {
    if (item.superadminOnly && !auth.isSuperadmin) return false
    return true
  })
}

// Ensure active section expands when route changes
watch(
  () => route.path,
  () => {
    for (const section of adminNavigation) {
      if (isSectionActive(section)) {
        expandedSections.value[section.id] = true
      }
    }
  },
  { immediate: true },
)
</script>

<template>
  <nav class="workspace-nav" aria-label="管理导航">
    <div class="space-y-1">
      <div
        v-for="section in adminNavigation"
        :key="section.id"
        class="workspace-nav__section"
      >
        <!-- Single-item section button (e.g. 运行概览, 模型服务) -->
        <RouterLink
          v-if="visibleChildren(section).length === 1"
          :to="`/admin/${visibleChildren(section)[0]!.id}`"
          class="workspace-nav__item workspace-nav__section-header"
          active-class="is-active"
          @mouseenter="preload(visibleChildren(section)[0]!.id)"
          @focus="preload(visibleChildren(section)[0]!.id)"
          @click="emit('navigate')"
        >
          <component :is="section.icon" class="size-[18px] shrink-0" aria-hidden="true" />
          <span class="font-medium truncate">{{ section.label }}</span>
        </RouterLink>

        <!-- Multi-item section header (accordion / contextual children) -->
        <template v-else-if="visibleChildren(section).length > 1">
          <button
            type="button"
            class="workspace-nav__item workspace-nav__section-header w-full flex items-center justify-between text-left"
            :class="{ 'is-active-parent': isSectionActive(section) }"
            :aria-expanded="isSectionExpanded(section)"
            @click="handleSectionClick(section)"
          >
            <div class="flex items-center gap-2.5 min-w-0">
              <component :is="section.icon" class="size-[18px] shrink-0" aria-hidden="true" />
              <span class="font-medium truncate">{{ section.label }}</span>
            </div>
            <component
              :is="isSectionExpanded(section) ? ChevronDown : ChevronRight"
              class="size-4 shrink-0 opacity-60 ml-auto transition-transform"
              aria-hidden="true"
            />
          </button>

          <!-- Contextual child links -->
          <div
            v-show="isSectionExpanded(section)"
            class="workspace-nav__children pl-3 ml-2 border-l border-[var(--border-subtle)] my-0.5 space-y-0.5"
          >
            <RouterLink
              v-for="item in visibleChildren(section)"
              :key="item.id"
              :to="`/admin/${item.id}`"
              class="workspace-nav__item workspace-nav__child text-xs py-1.5"
              active-class="is-active"
              @mouseenter="preload(item.id)"
              @focus="preload(item.id)"
              @click="emit('navigate')"
            >
              <component :is="item.icon" class="size-3.5 shrink-0" aria-hidden="true" />
              <span class="truncate">{{ item.label }}</span>
              <span
                v-if="item.advanced"
                class="ml-auto text-[10px] px-1 py-0.2 rounded border uppercase font-mono tracking-tight"
                style="background: var(--bg-badge); border-color: var(--border-subtle); color: var(--color-brand)"
                title="核心风控拦截门禁"
              >
                风控
              </span>
            </RouterLink>
          </div>
        </template>
      </div>
    </div>
  </nav>
</template>

<style scoped>
.workspace-nav__section-header {
  font-size: 0.875rem;
}
.workspace-nav__section-header.is-active-parent {
  color: var(--color-brand);
}
.workspace-nav__child {
  font-size: 0.8125rem;
}
</style>
