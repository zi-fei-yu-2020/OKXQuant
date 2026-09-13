import { shallowRef } from 'vue'

export interface DialogOptions {
  title?: string
  confirmLabel?: string
  danger?: boolean
}
export interface PromptOptions extends DialogOptions {
  defaultValue?: string
  label?: string
  placeholder?: string
  requiredText?: string
}
export interface DialogRequest {
  id: number
  kind: 'confirm' | 'prompt'
  message: string
  options: PromptOptions
  resolve: (value: string | boolean | null) => void
}
const active = shallowRef<DialogRequest | null>(null)
const queue: DialogRequest[] = []
let sequence = 0
function finish(value: string | boolean | null) {
  const previous = active.value
  active.value = null
  previous?.resolve(value)
  // Let the current native dialog close and return focus before the next opens.
  setTimeout(() => {
    if (!active.value) active.value = queue.shift() || null
  }, 0)
}
function cancelAll() {
  const requests = [...(active.value ? [active.value] : []), ...queue.splice(0)]
  active.value = null
  for (const request of requests) request.resolve(request.kind === 'prompt' ? null : false)
}
function ask(kind: DialogRequest['kind'], message: string, options: PromptOptions) {
  return new Promise<string | boolean | null>((resolve) => {
    const phrase = message.match(/确认短语[：:]\s*([A-Z][A-Z0-9 _-]+)$/)?.[1]
    const normalized = {
      danger: kind === 'confirm' || !!phrase || /实盘|LIVE|删除|恢复|备份|强制|重放|解锁|安装/.test(message),
      requiredText: phrase,
      ...options,
    }
    const request = { id: ++sequence, kind, message, options: normalized, resolve }
    if (active.value) queue.push(request)
    else active.value = request
  })
}
export function useDialogs() {
  return {
    active,
    finish,
    cancelAll,
    confirm: async (message: string, options: DialogOptions = {}) =>
      (await ask('confirm', message, options)) === true,
    prompt: async (message: string, options: string | PromptOptions = {}) => {
      const value = await ask(
        'prompt',
        message,
        typeof options === 'string' ? { defaultValue: options } : options,
      )
      return typeof value === 'string' ? value : null
    },
  }
}
