import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { useToast } from '../composables/useFeedback'
import {
  checkSessionResponse,
  SESSION_EXPIRED_MESSAGE,
  SESSION_TOKEN_KEY,
  SESSION_USER_KEY,
  registerSessionExpireCallback,
} from '../utils/sessionResponse'

export interface AdminUser {
  id?: number
  username: string
  role: string
}

export const useAuthStore = defineStore('auth', () => {
  const token = ref<string>('')
  const user = ref<AdminUser | null>(null)
  const error = ref<string>('')
  let expiryNotified = false
  let verifiedToken = ''
  let verifiedAt = 0
  const navigationVerificationTtlMs = 15_000

  function expireSession(checkedToken: string) {
    if (token.value !== checkedToken || expiryNotified) return
    expiryNotified = true
    logout(false)
    useToast().error(SESSION_EXPIRED_MESSAGE)
  }

  registerSessionExpireCallback(expireSession)

  function checkResponse(response: Response, checkedToken: string) {
    checkSessionResponse(response.status, checkedToken, token.value, expireSession)
  }

  const isAuthenticated = computed(() => !!token.value)
  const isSuperadmin = computed(() => user.value?.role === 'superadmin')

  async function login(username: string, password: string): Promise<boolean> {
    error.value = ''
    try {
      const resp = await fetch('/api/v1/admin/auth/login', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      })
      const data = await resp.json()
      if (!resp.ok) {
        error.value = data.detail || `登录失败 (HTTP ${resp.status})`
        return false
      }
      expiryNotified = false
      token.value = data.session_token
      user.value = { id: data.user?.id, username: data.user?.username || username, role: data.user?.role || 'admin' }
      localStorage.setItem(SESSION_TOKEN_KEY, token.value)
      localStorage.setItem(SESSION_USER_KEY, JSON.stringify(user.value))
      return true
    } catch (e: any) {
      error.value = e.message || '网络错误'
      return false
    }
  }

  async function validateSession(): Promise<boolean> {
    if (!token.value) return false
    const checkedToken = token.value
    const controller = new AbortController()
    const timeout = setTimeout(() => controller.abort(), 6000)
    try {
      const resp = await fetch('/api/v1/admin/auth/me', {
        headers: { 'X-OKXQuant-Session': checkedToken },
        signal: controller.signal,
        cache: 'no-store',
      })
      if (token.value !== checkedToken) return false
      checkResponse(resp, checkedToken)
      if (!resp.ok) return false
      const data = await resp.json()
      if (token.value !== checkedToken) return false
      if (!data.user?.id || !data.user?.username || !data.user?.role) return false
      if (data.user) {
        user.value = { id: data.user.id, username: data.user.username, role: data.user.role }
        localStorage.setItem(SESSION_USER_KEY, JSON.stringify(user.value))
      }
      verifiedToken = checkedToken
      verifiedAt = Date.now()
      return true
    } catch {
      return false
    } finally {
      clearTimeout(timeout)
    }
  }

  let restorePromise: Promise<boolean> | null = null

  async function restoreSession(): Promise<boolean> {
    // Only a recent server-verified session can skip the navigation round trip.
    // Every protected data/mutation API continues to authenticate independently.
    const saved = localStorage.getItem(SESSION_TOKEN_KEY) || ''
    if (token.value && saved !== token.value) {
      verifiedToken = ''; verifiedAt = 0
      token.value = ''; user.value = null
    }
    const age = Date.now() - verifiedAt
    if (token.value && verifiedToken === token.value && age >= 0 && age < navigationVerificationTtlMs) return true
    if (restorePromise) return restorePromise
    restorePromise = (async () => {
      if (!token.value) {
        const savedToken = localStorage.getItem(SESSION_TOKEN_KEY)
        const savedUser = localStorage.getItem(SESSION_USER_KEY)
        if (savedToken) {
          token.value = savedToken
          try {
            user.value = savedUser ? JSON.parse(savedUser) : null
          } catch {
            user.value = null
          }
        }
      }
      if (!token.value) return false
      return validateSession()
    })().finally(() => {
      restorePromise = null
    })
    return restorePromise
  }

  function logout(revoke = true) {
    // Do not send another request when the server already rejected the session.
    if (revoke && token.value) {
      fetch('/api/v1/admin/auth/logout', {
        method: 'POST',
        headers: { 'X-OKXQuant-Session': token.value },
      }).catch(() => {})
    }
    verifiedToken = ''; verifiedAt = 0
    token.value = ''
    user.value = null
    localStorage.removeItem(SESSION_TOKEN_KEY)
    localStorage.removeItem(SESSION_USER_KEY)
  }

  return {
    token,
    user,
    error,
    isAuthenticated,
    isSuperadmin,
    login,
    logout,
    restoreSession,
    checkResponse,
  }
})
