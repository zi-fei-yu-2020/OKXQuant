import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as display from '../src/utils/backupDisplay.ts'

const { backupConfiguration, backupLatest, backupTime } = display

test('local configuration needs no credential status and is not a write verification', () => {
  const configured = backupConfiguration({ destination: 'local', configured: true })
  assert.equal(configured.configured, true)
  assert.match(configured.label, /无需凭据/)
  assert.match(configured.note, /尚未验证目录可写/)
  assert.doesNotMatch(configured.className, /emerald|green/)
  assert.match(backupConfiguration({ destination: 'local', configured: true, directory_write_verified: true }).note, /目录可写已验证/)
  for (const value of [false, undefined, null, 'true', 1]) {
    assert.equal(backupConfiguration({ destination: 'local', configured: value }).configured, false)
  }
})

test('remote presentation trusts backend completeness, not absent/stale okxquant_frontend credential fields', () => {
  for (const destination of ['s3', 'oss', 'webdav', 'baidu_oauth']) {
    assert.equal(backupConfiguration({ destination, configured: true }).configured, true)
    assert.match(backupConfiguration({ destination, configured: true }).note, /尚未验证远端连接和写入/)
    assert.equal(backupConfiguration({ destination, configured: false, target: { credential_status: { configured: true } } }).configured, false)
  }
})

test('manifest uses started_at/finished_at and preserves Beijing timestamp semantics', () => {
  const latest = backupLatest({ started_at: '2026-09-08 02:00:00', finished_at: '2026-09-08 02:01:30', status: 'success' })
  assert.match(latest.startedAt, /2026.*09.*08.*02:00:00/)
  assert.match(latest.finishedAt, /02:01:30/)
  assert.equal(backupTime('2026-09-07T18:00:00Z'), latest.startedAt)
  assert.equal(backupTime('2026-09-08T02:00:00+08:00'), latest.startedAt)
  assert.equal(latest.status, 'success')
})

test('invalid/missing timestamps never leak JSON or fake dates', () => {
  for (const value of [undefined, null, {}, [], 123, '', 'UNKNOWN', '{"job_id":"test"}', '2026-02-30 02:00:00', '2026-13-08 02:00:00', '2026-09-08 24:00:00', '2026-09-08 02:99:00', '2026-09-08']) {
    assert.equal(backupTime(value), '--', JSON.stringify(value))
  }
  const latest = backupLatest({ created_at: '2026-09-08 02:00:00', time: 'fake' })
  assert.equal(latest.startedAt, '--')
  assert.equal(latest.finishedAt, '--')
  assert.equal(latest.status, 'unknown')
})

test('only explicit success is green; failed/partial/unknown/running/skipped are not', () => {
  assert.equal(backupLatest({ status: 'success' }).className, 'text-emerald-500')
  for (const value of ['failed', 'partial', 'unknown', 'running', 'skipped', undefined, null, '', 'SUCCESS', 'garbage', {}, [], 'constructor', '__proto__']) {
    const result = backupLatest({ status: value })
    assert.doesNotMatch(result.className, /emerald|green/)
    assert.notEqual(result.status, 'success')
    assert.notEqual(result.label, '成功')
  }
})

// Compile and render the ACTUAL page; all stores/side effects are in-memory fakes.
// No browser, fetch, network, onMounted load or backend/gateway service is needed.
const source = readFileSync(new URL('../src/views/admin/BackupPage.vue', import.meta.url), 'utf8')
const { descriptor, errors } = parse(source)
assert.deepEqual(errors, [])
const script = compileScript(descriptor, { id: 'backup-status-offline', inlineTemplate: true })
const compiled = ts.transpileModule(script.content, {
  compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
}).outputText
const Card = { setup: (_, { slots }) => () => Vue.h('section', slots.default?.()) }
const Empty = { render: () => null }
async function renderPage(simple, superadmin = false) {
  let index = 0
  const exports = {}
  const require = name => {
    if (name === 'vue') return { ...Vue, onMounted: () => {}, ref: value => {
      index++
      // First/third/fifth refs are loading/simple/status in the actual setup.
      if (index === 1) return Vue.ref(false)
      if (index === 3) return Vue.ref(simple)
      if (index === 5) return Vue.ref({ local_archives: [] })
      return Vue.ref(value)
    } }
    if (name.includes('backupDisplay')) return display
    if (name.includes('useApi')) return { useApi: () => ({ api: () => { throw new Error('offline: API prohibited') } }) }
    if (name.includes('stores/auth')) return { useAuthStore: () => ({ isSuperadmin: superadmin }) }
    if (name.includes('useFeedback')) return { useFeedback: () => Vue.ref(null), useToast: () => ({ success: () => { throw new Error('unexpected toast') } }) }
    if (name.includes('useDialogs')) return { useDialogs: () => ({ prompt: () => { throw new Error('unexpected prompt') } }) }
    if (name.endsWith('AppCard.vue')) return { __esModule: true, default: Card }
    if (name.endsWith('.vue')) return { __esModule: true, default: Empty }
    if (name === 'lucide-vue-next') return Object.fromEntries(['HardDrive', 'PlugZap', 'Save', 'PlayCircle', 'Archive', 'Download', 'Upload', 'RotateCcw'].map(key => [key, Empty]))
    throw new Error('Unexpected import ' + name)
  }
  new Function('require', 'exports', compiled)(require, exports)
  return renderToString(Vue.createSSRApp(exports.default))
}
const fixture = latest => ({ configured: true, destination: 'local', target: { type: 'local' }, enabled: false, latest })

test('actual BackupPage local badge and latest use manifest fields, not JSON fragments', async () => {
  const html = await renderPage(fixture({ job_id: 'SENTINEL_JSON_FRAGMENT', status: 'success', started_at: '2026-09-08 02:00:00', finished_at: '2026-09-08 02:01:30' }))
  assert.match(html, /本地目标已配置 · 无需凭据/)
  assert.match(html, /尚未验证目录可写/)
  assert.match(html, /开始时间（北京时间）/)
  assert.match(html, /完成时间（北京时间）/)
  assert.match(html, /02:00:00/)
  assert.match(html, /02:01:30/)
  assert.match(html, /text-emerald-500[^>]*>成功/)
  assert.doesNotMatch(html, /SENTINEL_JSON_FRAGMENT|job_id|created_at/)
})

test('actual BackupPage failed/partial/missing statuses never render a green success', async () => {
  for (const [status, label] of [['failed', '失败'], ['partial', '部分成功'], ['running', '执行中'], ['skipped', '已跳过'], [undefined, '未知'], ['garbage', '未知']]) {
    const html = await renderPage(fixture({ status }))
    assert.match(html, new RegExp('>' + label + '<'))
    assert.doesNotMatch(html, /text-emerald-500|JSON|stringify|undefined|NaN/)
    assert.ok(html.includes('>--</span>'))
  }
})

test('actual BackupPage configured remote works without credential_status field', async () => {
  const html = await renderPage({ ...fixture(null), destination: 's3', target: { type: 's3' } })
  assert.match(html, /远端目标字段已配置/)
  assert.match(html, /尚未验证远端连接和写入/)
  assert.match(html, /尚无匹配的灾备清单记录/)
  assert.doesNotMatch(html, /目标配置不完整/)
})

test('actual BackupPage incomplete remote is not configured and restore warning preserves protection', async () => {
  const html = await renderPage({ ...fixture(null), configured: false, destination: 'webdav', target: { type: 'webdav' } }, true)
  assert.match(html, /目标配置不完整/)
  assert.doesNotMatch(html, /远端目标字段已配置/)
  assert.match(source, /请先停止 gateway、trader 及自动拉起机制/)
  assert.match(source, /不会自动停机或修改云端保护订单/)
  assert.ok(!source.includes('JSON.stringify(simple.latest)'))
  assert.ok(!source.includes("simple.latest.status || 'success'"))
})
