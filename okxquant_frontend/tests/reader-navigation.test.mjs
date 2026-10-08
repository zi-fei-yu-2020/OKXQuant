import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

test('ordinary administrators never receive an account-center shortcut', () => {
  const security = readFileSync(new URL('../src/views/admin/SecurityPage.vue', import.meta.url), 'utf8')
  const overview = readFileSync(new URL('../src/views/admin/OverviewPage.vue', import.meta.url), 'utf8')
  assert.match(security, /<router-link v-if="auth\.isSuperadmin" to="\/admin\/accounts"/)
  assert.match(overview, /auth\.isSuperadmin \? '\/admin\/accounts' : '\/admin\/security'/)
})
