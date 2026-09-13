import test from 'node:test'
import assert from 'node:assert/strict'
import { effectScope } from 'vue'
import { useDialogs } from '../src/composables/useDialogs.ts'
import { useFeedback, useToast } from '../src/composables/useFeedback.ts'
const tick = () => new Promise((resolve) => setTimeout(resolve, 5))

test('confirmation is false on cancellation and true only on explicit acceptance', async () => {
  const dialogs = useDialogs()
  const cancelled = dialogs.confirm('Delete this item?')
  assert.equal(dialogs.active.value.kind, 'confirm')
  dialogs.finish(false)
  assert.equal(await cancelled, false)
  await tick()
  const accepted = dialogs.confirm('Delete this item?')
  dialogs.finish(true)
  assert.equal(await accepted, true)
  await tick()
})

test('prompt preserves exact text, defaults, and null cancellation semantics', async () => {
  const dialogs = useDialogs()
  const pending = dialogs.prompt('Type the exact phrase', 'default value')
  assert.equal(dialogs.active.value.options.defaultValue, 'default value')
  dialogs.finish('  RESTORE OKXQuant  ')
  assert.equal(await pending, '  RESTORE OKXQuant  ')
  await tick()
  const cancelled = dialogs.prompt('Type the exact phrase')
  dialogs.finish(null)
  assert.equal(await cancelled, null)
  await tick()
  const empty = dialogs.prompt('Type a name')
  dialogs.finish('')
  assert.equal(await empty, '')
  await tick()
})

test('multiple confirmation requests remain ordered and do not overwrite callbacks', async () => {
  const dialogs = useDialogs()
  const first = dialogs.confirm('First')
  const second = dialogs.confirm('Second')
  assert.equal(dialogs.active.value.message, 'First')
  dialogs.finish(true)
  assert.equal(await first, true)
  await tick()
  assert.equal(dialogs.active.value.message, 'Second')
  dialogs.finish(false)
  assert.equal(await second, false)
  await tick()
})

test('toast duplicates collapse, variants remain distinct, and dismissal clears them', () => {
  const toast = useToast()
  toast.error('A failed request')
  toast.error('A failed request')
  assert.equal(toast.items.value.length, 1)
  assert.equal(toast.items.value[0].count, 2)
  toast.success('Saved')
  assert.equal(toast.items.value.length, 2)
  for (const item of [...toast.items.value]) toast.dismiss(item.id)
  assert.equal(toast.items.value.length, 0)
})

test('legacy feedback handlers emit component notifications without changing their payload', () => {
  const toast = useToast()
  const scope = effectScope()
  scope.run(() => {
    const feedback = useFeedback()
    const message = { type: 'err', text: 'Confirmation phrase mismatch' }
    feedback.value = message
    assert.equal(feedback.value.text, message.text)
    assert.equal(toast.items.value[0].tone, 'error')
    feedback.value = null
    assert.equal(toast.items.value.length, 1)
  })
  scope.stop()
  for (const item of [...toast.items.value]) toast.dismiss(item.id)
})

test('destructive prompts expose the exact phrase and harmless name prompts use primary styling', async () => {
  const dialogs = useDialogs()
  const destructive = dialogs.prompt('请输入确认短语：BACKUP OKXQUANT')
  assert.equal(dialogs.active.value.options.requiredText, 'BACKUP OKXQUANT')
  assert.equal(dialogs.active.value.options.danger, true)
  dialogs.finish(null)
  await destructive
  await tick()
  const harmless = dialogs.prompt('新方案名称：', 'My strategy')
  assert.equal(dialogs.active.value.options.danger, false)
  assert.equal(dialogs.active.value.options.requiredText, undefined)
  dialogs.finish(null)
  await harmless
  await tick()
})

test('leaving a page cancels active and queued operations without accepting them', async () => {
  const dialogs = useDialogs()
  const first = dialogs.confirm('Delete the item?')
  const second = dialogs.prompt('Type a phrase')
  dialogs.cancelAll()
  assert.equal(await first, false)
  assert.equal(await second, null)
  await tick()
  assert.equal(dialogs.active.value, null)
})

test('notification queue remains bounded', () => {
  const toast = useToast()
  for (let n = 0; n < 8; n++) toast.error(`Error ${n}`)
  assert.equal(toast.items.value.length, 4)
  assert.equal(toast.items.value[0].message, 'Error 4')
  for (const item of [...toast.items.value]) toast.dismiss(item.id)
})
