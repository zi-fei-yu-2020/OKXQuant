import { ref } from 'vue'
import { useAuthStore } from '../stores/auth'
import { HandledSessionError } from '../utils/sessionResponse'
import { createSingleFlight } from '../utils/singleFlight'

const sharedReads = createSingleFlight<any>()
let readEpoch = 0

export function useApi() {
  const loading = ref(false)
  const error = ref<string | null>(null)
  let pending = 0

  async function api<T = any>(path: string, options: RequestInit = {}): Promise<T> {
    const auth = useAuthStore()
    const session = auth.token
    const isRead = (options.method || 'GET').toUpperCase() === 'GET'
    if (!isRead) readEpoch += 1
    pending += 1
    loading.value = true
    error.value = null
    try {
      const headers = new Headers({ 'Content-Type': 'application/json' })
      if (session) headers.set('X-OKXQuant-Session', session)
      new Headers(options.headers).forEach((value, key) => headers.set(key, value))
      const request = async () => {
        const resp = await fetch(path, { ...options, headers })
        auth.checkResponse(resp, session)
        let data: any = {}
        try { data = await resp.json() } catch { /* empty response */ }
        auth.checkResponse(resp, session)
        if (!resp.ok) {
          const error = data?.error
          const detail = Array.isArray(data)
            ? data.map((x: any) => typeof x === 'string' ? x : x?.msg || x?.detail || JSON.stringify(x)).join('；')
            : Array.isArray(data.detail)
            ? data.detail.map((x: any) => `${(x.loc || []).slice(1).join('.') || '请求'}：${x.msg}`).join('；')
            : typeof data.detail === 'string'
              ? data.detail
              : typeof error?.message === 'string'
                ? error.message
                : typeof data.message === 'string'
                  ? data.message
                  : `HTTP ${resp.status}`
          const requestId = resp.headers.get('x-request-id') || data?.request_id || error?.request_id
          const suffix = requestId ? `（request_id: ${requestId}）` : ''
          const failure = new Error(`${detail}${suffix}`)
          ;(failure as any).code = error?.code || data?.code
          ;(failure as any).requestId = requestId
          throw failure
        }
        return data
      }
      const canShare = isRead && Object.keys(options).every(key => key === 'method' || key === 'headers')
      const key = JSON.stringify([session, readEpoch, path, [...headers.entries()]])
      return await (canShare ? sharedReads(key, request) : request()) as T
    } catch (e: any) {
      if (auth.token !== session) throw new HandledSessionError()
      error.value = e.silent ? null : e.message || String(e)
      throw e
    } finally {
      // A post-save refresh must not join a read started before the mutation.
      if (!isRead) readEpoch += 1
      pending -= 1
      loading.value = pending > 0
    }
  }

  return { loading, error, api }
}
