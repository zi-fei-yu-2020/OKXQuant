import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { useToast, TOAST_DURATION_MS } from '../src/composables/useFeedback.ts'
import { checkSessionResponse, HandledSessionError } from '../src/utils/sessionResponse.ts'

const clear = toast => [...toast.items.value].forEach(item => toast.dismiss(item.id))
test('all notification tones disappear after 3 seconds, including errors', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const toast = useToast(); clear(toast)
  try {
    assert.equal(TOAST_DURATION_MS, 3000)
    for (const tone of ['error', 'success', 'warning', 'info']) toast[tone](tone)
    assert.equal(toast.items.value.length, 4)
    t.mock.timers.tick(2999)
    assert.equal(toast.items.value.length, 4)
    t.mock.timers.tick(1)
    assert.equal(toast.items.value.length, 0)
  } finally { clear(toast) }
})
test('repeated concurrent errors do not extend the notification lifetime', t => {
  t.mock.timers.enable({ apis: ['setTimeout'] })
  const toast = useToast(); clear(toast)
  try {
    toast.error('expired')
    t.mock.timers.tick(2500)
    toast.error('expired')
    assert.equal(toast.items.value.length, 1)
    assert.equal(toast.items.value[0].count, 2)
    t.mock.timers.tick(500)
    assert.equal(toast.items.value.length, 0)
    toast.error(''); toast.error(undefined)
    assert.equal(toast.items.value.length, 0)
  } finally { clear(toast) }
})
test('401 expires once; other concurrent responses become silent after logout', () => {
  let token = 'old'; let calls = 0
  const expire = () => { calls++; token = '' }
  assert.throws(() => checkSessionResponse(401, 'old', token, expire), HandledSessionError)
  for (const status of [401, 200, 500]) {
    assert.throws(() => checkSessionResponse(status, 'old', token, expire), error => error.silent && !error.message)
  }
  assert.equal(calls, 1)
})
test('old responses never expire a newly logged-in session; 403/network status never logs out', () => {
  let calls = 0
  const expire = () => calls++
  assert.throws(() => checkSessionResponse(401, 'old', 'new', expire), HandledSessionError)
  for (const status of [200, 403, 429, 500, 503]) checkSessionResponse(status, 'new', 'new', expire)
  assert.equal(calls, 0)
  assert.throws(() => checkSessionResponse(401, '', '', expire), HandledSessionError)
  assert.equal(calls, 1)
})
test('all admin request transports share session checks, and expired routes use replace', () => {
  const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
  assert.ok(read('composables/useApi.ts').includes('auth.checkResponse(resp, session)'))
  assert.ok(read('views/admin/BackupPage.vue').includes('auth.checkResponse(resp, checkedToken)'))
  assert.ok(read('stores/auth.ts').includes('checkResponse(resp, checkedToken)'))
  const app = read('App.vue')
  assert.ok(app.includes('auth.isAuthenticated') && app.includes('route.meta.requiresAuth'))
  assert.ok(app.includes("router.replace({ name: 'admin-login' })"))
})
test('account result messages use transient notifications; confirmation and OAuth dialogs stay explicit', () => {
  const source = readFileSync(new URL('../src/views/admin/AccountsPage.vue', import.meta.url), 'utf8')
  assert.ok(!source.includes(':open="!!error"') && !source.includes(':open="!!notice"'))
  assert.ok(source.includes('toast.error(text)') && source.includes('toast.info(text)'))
  for (const name of ['confirmOpen', 'authorizeOpen', 'addOpen']) assert.ok(source.includes(`v-model:open="${name}"`))
  const dialog = readFileSync(new URL('../src/components/ui/AppDialog.vue', import.meta.url), 'utf8')
  assert.ok(!dialog.includes('setTimeout'))
})
