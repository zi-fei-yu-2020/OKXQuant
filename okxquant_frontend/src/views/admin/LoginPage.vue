<script setup lang="ts">
import { ref } from 'vue'
import { useRouter } from 'vue-router'
import { useAuthStore } from '../../stores/auth'
import { useTheme } from '../../composables/useTheme'
import {
  Activity,
  ArrowLeft,
  ArrowRight,
  Eye,
  EyeOff,
  LockKeyhole,
  Moon,
  Sun,
  ShieldCheck,
} from 'lucide-vue-next'
import AppButton from '../../components/ui/AppButton.vue'
import AppField from '../../components/ui/AppField.vue'
const auth = useAuthStore()
const router = useRouter()
const { theme, toggleTheme } = useTheme()
const username = ref('admin')
const password = ref('')
const showPassword = ref(false)
const loading = ref(false)
async function handleLogin() {
  if (loading.value) return
  loading.value = true
  try {
    if (await auth.login(username.value.trim(), password.value)) {
      password.value = ''
      await router.push('/admin/overview')
    }
  } finally {
    loading.value = false
  }
}
</script>
<template>
  <div class="login-page">
    <header class="login-topbar">
      <RouterLink to="/" class="terminal-brand"
        ><span class="brand-mark"><Activity class="size-5" /></span
        ><span>OKXQuant</span></RouterLink
      ><button
        class="ui-icon-button"
        :aria-label="theme === 'dark' ? '切换浅色主题' : '切换深色主题'"
        @click="toggleTheme"
      >
        <Sun v-if="theme === 'dark'" class="size-5" /><Moon v-else class="size-5" />
      </button>
    </header>
    <main class="login-layout">
      <section class="login-intro">
        <span class="ui-badge ui-badge--brand">量化策略工作空间</span>
        <h1>让每一次决策，<br />都有据可循。</h1>
        <p>连接模型、管理策略、观察风险。<br />在一个清晰的工作空间里，掌握系统的每一步。</p>
        <div class="login-feature">
          <ShieldCheck class="size-5" />
          <div>
            <strong>权限与操作隔离</strong><span>关键操作保留密码验证、确认短语和审计记录。</span>
          </div>
        </div>
        <div class="login-feature">
          <Activity class="size-5" />
          <div>
            <strong>从信号到执行</strong><span>集中查看模型决策、任务状态和账户信息。</span>
          </div>
        </div>
      </section>
      <section class="login-card">
        <div class="login-card__icon"><LockKeyhole class="size-6" aria-hidden="true" /></div>
        <h2>登录控制台</h2>
        <p class="text-[var(--text-muted)] mt-2 mb-7">使用管理员账户访问你的工作空间。</p>
        <form @submit.prevent="handleLogin" class="space-y-5">
          <AppField label="管理员账号" for="login-username"
            ><input
              id="login-username"
              v-model="username"
              name="username"
              autocomplete="username"
              required
              autofocus
              :disabled="loading" /></AppField
          ><AppField label="密码" for="login-password" :error="auth.error"
            ><div class="input-with-action">
              <input
                id="login-password"
                v-model="password"
                name="password"
                :type="showPassword ? 'text' : 'password'"
                autocomplete="current-password"
                required
                :disabled="loading"
                :aria-invalid="!!auth.error"
                :aria-describedby="auth.error ? 'login-password-error' : undefined"
                placeholder="输入管理员密码"
              /><button
                type="button"
                class="ui-icon-button"
                :aria-label="showPassword ? '隐藏密码' : '显示密码'"
                @click="showPassword = !showPassword"
              >
                <EyeOff v-if="showPassword" class="size-[18px]" /><Eye v-else class="size-[18px]" />
              </button></div></AppField
          ><AppButton type="submit" variant="primary" :loading="loading" class="w-full"
            >{{ loading ? '正在登录' : '登录工作空间' }}<ArrowRight v-if="!loading" class="size-4"
          /></AppButton>
        </form>
        <p class="login-security-note">
          <ShieldCheck class="size-4 shrink-0" />连续登录失败会触发临时锁定，请确认账号及密码。
        </p>
        <RouterLink to="/" class="login-back"><ArrowLeft class="size-4" />返回监控终端</RouterLink>
      </section>
    </main>
    <footer class="login-footer">OKXQuant <span>·</span> 安全访问 · 操作可追溯</footer>
  </div>
</template>
