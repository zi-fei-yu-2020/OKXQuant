import test from 'node:test'
import * as entryQuota from '../src/utils/entryQuota.ts'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { adminNavigation, adminPages, frontTabs, publicPages, pageTitle } from '../src/config/navigation.ts'
import * as sessionUtil from '../src/utils/sessionResponse.ts'

const read = (p) => readFileSync(new URL('../src/' + p, import.meta.url), 'utf8')

test('admin navigation defines exactly seven sections with contextual children and superadmin filter', () => {
  assert.equal(adminNavigation.length, 7)
  const labels = adminNavigation.map((s) => s.label)
  assert.deepEqual(labels, [
    '运行概览',
    '账户与交易',
    '策略与风控',
    '模型服务',
    '运行与日志',
    '通知与备份',
    '系统设置',
  ])

  // Total flat admin pages must remain 17
  assert.equal(adminPages.length, 17)

  // Accounts center must be marked superadminOnly so ordinary admins route to security
  const tradingSec = adminNavigation.find((s) => s.id === 'accounts-trading-section')
  assert.ok(tradingSec)
  const accountsItem = tradingSec.items.find((i) => i.id === 'accounts')
  const securityItem = tradingSec.items.find((i) => i.id === 'security')
  assert.ok(accountsItem?.superadminOnly === true)
  assert.ok(!securityItem?.superadminOnly)

  // Interceptors must be marked advanced
  const strategySec = adminNavigation.find((s) => s.id === 'strategy-risk-section')
  assert.ok(strategySec)
  const interceptorsItem = strategySec.items.find((i) => i.id === 'interceptors')
  assert.ok(interceptorsItem?.advanced === true)
})

test('canonical front routes and legacy redirects preserve titles and navigation consistency', () => {
  assert.equal(frontTabs.length, 5)
  const canonicalPaths = frontTabs.map((t) => t.path)
  assert.deepEqual(canonicalPaths, ['/', '/decisions', '/market-intelligence', '/reviews', '/trades'])

  const routerSrc = read('router/index.ts')
  for (const legacy of ['/trading', '/factors', '/news', '/lab', '/history']) {
    assert.ok(routerSrc.includes(`path: '${legacy}'`), `Legacy path ${legacy} missing`)
  }

  // Redirects must preserve query and hash
  assert.match(routerSrc, /query: to.query, hash: to.hash/)

  // Page titles must match for both canonical and legacy routes
  assert.equal(pageTitle('/'), '交易概览')
  assert.equal(pageTitle('/trading'), '交易概览')
  assert.equal(pageTitle('/decisions'), 'AI 决策')
  assert.equal(pageTitle('/factors'), 'AI 决策')
  assert.equal(pageTitle('/market-intelligence'), '市场情报')
  assert.equal(pageTitle('/news'), '市场情报')
  assert.equal(pageTitle('/reviews'), '策略复盘')
  assert.equal(pageTitle('/lab'), '策略复盘')
  assert.equal(pageTitle('/trades'), '交易记录')
  assert.equal(pageTitle('/history'), '交易记录')
})

test('public monitor routes are anonymous while admin and raw prompt tools remain private', () => {
  const routerSrc = read('router/index.ts')
  assert.match(routerSrc, /requiresAuth: true/)
  assert.equal((routerSrc.match(/isPublic: true, tab:/g) || []).length, 5)
  assert.doesNotMatch(routerSrc, /requiresAuth: true, tab:/)
  const display = read('views/DashboardView.vue')
  assert.doesNotMatch(display, /auth\.isAuthenticated/)
  assert.match(display, /<LedgerLogs/)
  assert.match(routerSrc, /query: { next: to.fullPath }/)

  const headerSrc = read('components/HeaderBar.vue')
  assert.doesNotMatch(headerSrc, /<FloatingActions/)
  assert.doesNotMatch(headerSrc, /data-header-prompt/)

  // Prompt audit belongs in authenticated advanced diagnostics (DecisionsPage)
  const decisionsSrc = read('views/admin/DecisionsPage.vue')
  assert.match(decisionsSrc, /<FloatingActions/)
})

test('TradesLedger pagination queries full canonical backend with state and keyword, avoiding unsupported claims', () => {
  const ledgerSrc = read('components/TradesLedger.vue')
  assert.match(ledgerSrc, /\/api\/trades\?\${params\.toString\(\)}/)
  assert.match(ledgerSrc, /params\.set\('state', filter\.value\)/)
  assert.match(ledgerSrc, /params\.set\('limit', String\(pageSize\)\)/)
  assert.match(ledgerSrc, /params\.set\('offset', String\(targetOffset\)\)/)

  // Disclaimers and footer must avoid unsupported '真实撮合' / '完整' claims
  assert.doesNotMatch(ledgerSrc, /真实撮合成交记录/)
  assert.doesNotMatch(ledgerSrc, /完整成交台账/)
  assert.match(ledgerSrc, /已保留的交易生命周期记录（含待结算）/)
  assert.match(ledgerSrc, /已平仓\s*[（(]含待结算[）)]/)

  // AbortController cancellation on search/scope change and unmount
  assert.match(ledgerSrc, /activeController(?:\?\.|\.)abort\(\)/)
  assert.match(ledgerSrc, /onUnmounted/)
  assert.match(ledgerSrc, /store\.data\?\.account_source_id/)
})

test('StrategyTelemetryPanel enforces strict period rendering with no legacy fallback', () => {
  const panelSrc = read('components/StrategyTelemetryPanel.vue')

  // stat(key) must NOT contain legacy stats.value[key] fallback
  assert.doesNotMatch(panelSrc, /stats\.value\[key\]/)
  assert.match(panelSrc, /activePeriodData\.value\[key\]/)

  // Tab labels must be exact '当日统计' and '全局统计'
  assert.match(panelSrc, /当日统计/)
  assert.match(panelSrc, /全局统计/)
  assert.match(panelSrc, /role="tab"/)

  // The operator requested a concise display, not a change to the accounting periods.
  const template = panelSrc.slice(panelSrc.indexOf('<template>'), panelSrc.lastIndexOf('</template>'))
  for (const redundant of ['coverageStart', '\u7cfb\u7edf\u4fdd\u7559\u8d26\u672c\u53e3\u5f84', '\u51b3\u7b56\u72b6\u6001']) {
    assert.ok(!template.includes(redundant), `Redundant display returned: ${redundant}`)
  }
})

test('StrategyTelemetryPanel renders unknown/no-stats when periods is empty or unavailable', async () => {
  const file = 'StrategyTelemetryPanel.vue'
  const source = read('components/' + file)
  const { descriptor, errors } = parse(source)
  assert.deepEqual(errors, [])
  const compiled = compileScript(descriptor, { id: file, inlineTemplate: true })
  const code = ts.transpileModule(compiled.content, {
    compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 },
  }).outputText

  const surface = { setup: (_, { slots }) => () => Vue.h('section', slots.default?.()) }
  const createComp = (dataState) => {
    const exports = {}
    new Function('require', 'exports', code)((name) => {
      if (name === 'vue') return Vue
    if (name.includes('entryQuota')) return entryQuota
      if (name.includes('stores/dashboard')) return { useDashboardStore: () => ({ data: dataState, error: null, isStale: false, positions: [] }) }
      if (name.includes('/ui/')) return { __esModule: true, default: surface }
      throw new Error('Unmocked: ' + name)
    }, exports)
    return exports.default
  }

  // 1. Backend unavailable result { scope: 'test', error: 'statistics_unavailable', periods: {} }
  const unavailHtml = await renderToString(Vue.createSSRApp(createComp({
    horizon_stats: { scope: 'test', error: 'statistics_unavailable', periods: {} },
    execution_profile: { execution: { id: 'standard' } },
  })))
  assert.match(unavailHtml, /暂无统计/)
  assert.match(unavailHtml, /统计提示：当前账户历史分段统计尚未完成聚合/)

  // 2. Closed == 0 must render '--' win rate, never 0.0%
  const zeroClosedHtml = await renderToString(Vue.createSSRApp(createComp({
    horizon_stats: {
      periods: {
        today: {
          scalp: { closed: 0, wins: 0, net_pnl: 0, win_rate: 0 },
          swing: { closed: 0, wins: 0, net_pnl: 0, win_rate: 0 },
          coverage: { start: '2026-09-01 00:00' },
        },
      },
      timezone: 'Asia/Shanghai',
      as_of: '2026-10-08 10:00',
    },
    execution_profile: { execution: { id: 'standard' } },
  })))
  assert.doesNotMatch(zeroClosedHtml, /胜率 0.0%/)
  assert.doesNotMatch(zeroClosedHtml, /2026-09-01|Asia\/Shanghai|2026-10-08 10:00/)
})

test('main dashboard fetch and prompt fetch send X-OKXQuant-Session and guard stale 401 vs new token', () => {
  const dashSrc = read('stores/dashboard.ts')

  // Both /api/all and /api/ai/last-prompt must send auth headers
  assert.match(dashSrc, /const session = getSessionToken\(\)/)
  assert.match(dashSrc, /const headers = buildAuthHeaders\(session\)/)
  assert.ok(dashSrc.includes('fetch(' + String.fromCharCode(96) + '/api/all?_t=' + '$' + '{Date.now()}' + String.fromCharCode(96)))
  assert.ok(dashSrc.includes("fetch('/api/ai/last-prompt', { headers, cache: 'no-store' })"))

  // Token-aware session response handling must be used
  assert.match(dashSrc, /handleSessionResponse\(resp\.status, session\)/)

  // Prompt must not be cached across account/scope change
  assert.match(dashSrc, /verifiedPromptCache\.value = null/)
  assert.match(dashSrc, /if \(previous && !json\.ai_last_prompt\)/)
})

test('handleSessionResponse protects new tokens from stale 401 and prevents false 403 logout', () => {

  // Mock localStorage
  const store = new Map()
  globalThis.localStorage = {
    getItem: (k) => store.get(k) || null,
    setItem: (k, v) => store.set(k, String(v)),
    removeItem: (k) => store.delete(k),
    clear: () => store.clear(),
  }

  try {
    // 1. Establish new active token
    globalThis.localStorage.setItem('okxquant.admin.session.id', 'new-token-active')

    // 2. Stale 401 arrives for old-token
    let expiredCalled = false
    assert.throws(
      () => sessionUtil.handleSessionResponse(401, 'old-stale-token', () => { expiredCalled = true }),
      (err) => err.silent === true
    )
    assert.equal(expiredCalled, false, 'Stale 401 must not trigger expire callback')
    assert.equal(globalThis.localStorage.getItem('okxquant.admin.session.id'), 'new-token-active', 'Stale 401 must not erase newly active token')

    // 3. 403 response must never log out
    let forbiddenLoggedOut = false
    sessionUtil.handleSessionResponse(403, 'new-token-active', () => { forbiddenLoggedOut = true })
    assert.equal(forbiddenLoggedOut, false, '403 must not trigger expire callback')
    assert.equal(globalThis.localStorage.getItem('okxquant.admin.session.id'), 'new-token-active', '403 must not erase session token')

    // 4. Current 401 for active token expires and clears
    let currentExpiredCalled = false
    assert.throws(
      () => sessionUtil.handleSessionResponse(401, 'new-token-active', () => { currentExpiredCalled = true }),
      (err) => err.silent === true
    )
    assert.equal(currentExpiredCalled, true, 'Current 401 must trigger expire callback')
    assert.equal(globalThis.localStorage.getItem('okxquant.admin.session.id'), null, 'Current 401 must clear session')
  } finally {
    delete globalThis.localStorage
  }
})
