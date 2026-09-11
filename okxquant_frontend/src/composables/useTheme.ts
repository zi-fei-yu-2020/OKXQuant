import { ref } from 'vue'

export type ThemeMode = 'dark' | 'light'

const currentTheme = ref<ThemeMode>('light')
let initialized = false

export function useTheme() {
  function applyTheme(theme: ThemeMode) {
    currentTheme.value = theme
    if (typeof document !== 'undefined') {
      const el = document.documentElement
      el.setAttribute('data-theme', theme)
      if (theme === 'dark') {
        el.classList.add('dark')
      } else {
        el.classList.remove('dark')
      }
      try {
        localStorage.setItem('okxquant_theme', theme)
      } catch {
        // ignore localStorage error in private mode
      }
    }
  }

  function toggleTheme() {
    applyTheme(currentTheme.value === 'dark' ? 'light' : 'dark')
  }

  function initTheme() {
    if (initialized) return
    initialized = true
    let saved: ThemeMode = 'light'
    try {
      const stored = localStorage.getItem('okxquant_theme')
      if (stored === 'light' || stored === 'dark') {
        saved = stored
      }
    } catch {
      // fallback
    }
    applyTheme(saved)
  }

  return {
    theme: currentTheme,
    toggleTheme,
    setTheme: applyTheme,
    initTheme,
  }
}
