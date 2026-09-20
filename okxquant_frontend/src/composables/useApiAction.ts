import { computed, ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { useToast } from './useFeedback'

/** One page-level write/confirmation at a time; the server remains the authority. */
export function useApiAction(blocked: () => boolean = () => false) {
  const auth = useAuthStore()
  const actionBusy = ref(false)
  const canManage = computed(() => auth.isSuperadmin)
  function action<Args extends unknown[], Result>(handler: (...args: Args) => Promise<Result>, superadmin = true) {
    return async (...args: Args): Promise<Result | undefined> => {
      if (actionBusy.value) return
      if (blocked()) { useToast().warning('请先完成页面加载，加载失败时请重试'); return }
      if (superadmin && !auth.isSuperadmin) {
        useToast().warning('仅超级管理员可执行此操作')
        return
      }
      actionBusy.value = true
      try { return await handler(...args) }
      finally { actionBusy.value = false }
    }
  }
  return { action, actionBusy, canManage }
}
