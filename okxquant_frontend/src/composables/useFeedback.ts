import { ref, watch } from 'vue'

export type ToastTone = 'success' | 'error' | 'warning' | 'info'
export interface ToastItem {
  id: number
  tone: ToastTone
  message: string
  count: number
}
export interface FeedbackMessage {
  text: string
  type: 'ok' | 'err' | 'warn' | 'error'
}
const items = ref<ToastItem[]>([])
const timers = new Map<number, ReturnType<typeof setTimeout>>()
export const TOAST_DURATION_MS = 3000
let sequence = 0

function dismiss(id: number) {
  clearTimeout(timers.get(id))
  timers.delete(id)
  items.value = items.value.filter((item) => item.id !== id)
}
function notify(message: string, tone: ToastTone = 'info') {
  if (!message) return
  const text = String(message)
    .trim()
    .replace(/^[✅⚠️🛡️]+\s*/u, '')
  if (!text) return
  const existing = items.value.find((item) => item.message === text && item.tone === tone)
  if (existing) {
    existing.count++
    return
  }
  const id = ++sequence
  items.value.push({ id, tone, message: text, count: 1 })
  if (items.value.length > 4) dismiss(items.value[0]!.id)
  timers.set(id, setTimeout(() => dismiss(id), TOAST_DURATION_MS))
}
export function useToast() {
  return {
    items,
    dismiss,
    info: (text: string) => notify(text),
    success: (text: string) => notify(text, 'success'),
    error: (text: string) => notify(text, 'error'),
    warning: (text: string) => notify(text, 'warning'),
  }
}
/** Adapter for existing request handlers. Business logic and confirmation phrases stay unchanged. */
export function useFeedback() {
  const message = ref<FeedbackMessage | null>(null)
  watch(
    message,
    (value) => {
      if (value)
        notify(
          value.text,
          ({ ok: 'success', err: 'error', error: 'error', warn: 'warning' } as const)[value.type],
        )
    },
    { flush: 'sync' },
  )
  return message
}

export function useErrorFeedback(message: { value: string }) {
  watch(
    () => message.value,
    (text) => {
      if (text) notify(text, 'error')
    },
  )
}
