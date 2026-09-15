<script setup lang="ts">
import { ref, onMounted, onUnmounted } from 'vue'
import { useRoute } from 'vue-router'
const route = useRoute()
const clock = ref('')
let timer: ReturnType<typeof setInterval> | undefined
const updateClock = () => {
  clock.value = new Date().toLocaleTimeString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
}
onMounted(() => {
  updateClock()
  timer = setInterval(updateClock, 1000)
})
onUnmounted(() => clearInterval(timer))
import {
  LayoutDashboard,
  Brain,
  Newspaper,
  Sparkles,
  Receipt,
  Moon,
  Sun,
  Settings2,
  BookOpen,
} from 'lucide-vue-next'
import { useTheme } from '../composables/useTheme'
const { theme, toggleTheme } = useTheme()
const tabs = [
  { to: '/', label: '交易概览', icon: LayoutDashboard },
  { to: '/factors', label: 'AI 决策', icon: Brain },
  { to: '/news', label: '市场情报', icon: Newspaper },
  { to: '/lab', label: '策略复盘', icon: Sparkles },
  { to: '/history', label: '交易记录', icon: Receipt },
]
</script>
<template>
  <header class="terminal-header">
    <div class="terminal-header__inner">
      <RouterLink to="/" class="terminal-brand"
        ><span class="brand-mark"><img src="/favicon.svg?v=okxquant-20260915" alt="OKXQuant" class="brand-mark__image" /></span><span
          >OKXQuant</span
        ></RouterLink
      >
      <nav class="terminal-nav" aria-label="监控导航">
        <RouterLink
          v-for="tab in tabs"
          :key="tab.to"
          :to="tab.to"
          class="terminal-nav__item"
          exact-active-class="is-active"
          :class="{ 'is-active': tab.to === '/' && route.meta.tab === 'trading' }"
          ><component :is="tab.icon" class="size-4" aria-hidden="true" /><span>{{
            tab.label
          }}</span></RouterLink
        >
      </nav>
      <div class="flex items-center gap-1.5">
        <span
          class="hidden xl:inline text-xs text-[var(--text-faint)] num-tabular mr-2"
          title="北京时间 UTC+8"
          >{{ clock }}</span
        >
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
        ><RouterLink to="/docs" class="ui-icon-button hidden sm:inline-flex" aria-label="使用文档"
          ><BookOpen class="size-[18px]" aria-hidden="true" /></RouterLink
        ><RouterLink to="/admin" class="ui-button ui-button--secondary ui-button--sm"
          ><Settings2 class="size-4" aria-hidden="true" /><span>控制台</span></RouterLink
        >
      </div>
    </div>
  </header>
  <nav class="terminal-mobile-nav" aria-label="移动端监控导航">
    <RouterLink
      v-for="tab in tabs"
      :key="tab.to"
      :to="tab.to"
      exact-active-class="is-active"
      :class="{ 'is-active': tab.to === '/' && route.meta.tab === 'trading' }"
      ><component :is="tab.icon" class="size-5" aria-hidden="true" /><span>{{
        tab.label
      }}</span></RouterLink
    >
  </nav>
</template>
