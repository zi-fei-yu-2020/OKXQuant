import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as display from '../src/utils/observationDisplay.ts'

const { observedNumber, observedText, observedPercent, observationColor, smartMoneyDisplay } = display
const unknowns = [undefined, null, '', '  ', 'UNKNOWN', '--', NaN, Infinity, -Infinity, 'NaN', 'Infinity', true, false, {}, [], '0x10', '12oops']

test('missing/malformed observations never become zero, signed percentages, or directional colors', () => {
  for (const value of unknowns) {
    assert.equal(observedNumber(value), null)
    assert.equal(observedText(value), '--')
    assert.equal(observedPercent(value), '--')
    assert.equal(observedPercent(value, true), '--')
    assert.equal(observationColor(value), 'var(--text-muted)')
  }
})

test('real zero, numeric strings, signed changes and percentage bounds are preserved', () => {
  for (const zero of [0, '0', '0.0', -0]) {
    assert.equal(observedText(zero), '0')
    assert.equal(observedPercent(zero), '0%')
    assert.equal(observedPercent(zero, true), '+0%')
  }
  assert.equal(observedPercent('1.25', true), '+1.25%')
  assert.equal(observedPercent(-2.5, true), '-2.5%')
  assert.equal(observedPercent(150, true), '+150%')
  assert.equal(observedPercent(100), '100%')
  for (const value of [-1, 101]) assert.equal(observedPercent(value), '--')
})

test('smart money strictly requires boolean valid=true, even when defaults look plausible', () => {
  for (const valid of [undefined, false, null, 1, 'true']) {
    assert.deepEqual(smartMoneyDisplay({ valid, weighted_long_pct: 50, net_flow_usdt: '0 U' }), {
      long: '--', flow: '--', unavailable: true,
    })
  }
  assert.equal(smartMoneyDisplay({ valid: true, weighted_long_pct: 50 }, false).long, '--')
})

test('valid partial smart-money observations stay independent and actual zero is retained', () => {
  assert.deepEqual(smartMoneyDisplay({ valid: true, weighted_long_pct: 0, net_flow_usdt: 0 }), {
    long: '0%多', flow: '0 U', unavailable: false,
  })
  assert.deepEqual(smartMoneyDisplay({ valid: true, weighted_long_pct: 'UNKNOWN', net_flow_usdt: '0.0 U' }), {
    long: '--', flow: '0.0 U', unavailable: false,
  })
  assert.deepEqual(smartMoneyDisplay({ valid: true, weighted_long_pct: 65.4, net_flow_usdt: 'NaN U' }), {
    long: '65.4%多', flow: '--', unavailable: false,
  })
  for (const value of unknowns) {
    assert.equal(smartMoneyDisplay({ valid: true, weighted_long_pct: value, net_flow_usdt: value }).unavailable, true)
  }
  for (const flow of ['-2.0万 U', '1,234,567.89 U', '+1.2M USDT', '0 U']) {
    assert.equal(smartMoneyDisplay({ valid: true, net_flow_usdt: flow }).flow, flow)
  }
  for (const flow of ['NaN U', 'Infinity U', '-- U', '1,2 U', 'UNKNOWN U']) {
    assert.equal(smartMoneyDisplay({ valid: true, net_flow_usdt: flow }).flow, '--')
  }
})

// Render the actual SFC with an in-memory store; no DOM, server, or API calls.
const source = readFileSync(new URL('../src/components/InstrumentMatrix.vue', import.meta.url), 'utf8')
const { descriptor } = parse(source)
const script = compileScript(descriptor, { id: 'instrument-display-test', inlineTemplate: true })
const compiled = ts.transpileModule(script.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
const Card = { setup: (_, { slots }) => () => Vue.h('section', slots.default?.()) }
const Empty = { render: () => null }
async function renderCard(item, rawMoney, supported = true) {
  const store = {
    factors: [{ instId: 'BTC-USDT-SWAP', name: 'BTC', ...item }],
    data: { factors: [{ instId: 'OTHER', smart_money: { valid: true, weighted_long_pct: 99 } }, { instId: 'BTC-USDT-SWAP', smart_money: rawMoney }] },
    macroAnalysis: {}, macroLabel: '', macroAssessment: '',
  }
  const exports = {}
  const require = name => {
    if (name === 'vue') return Vue
    if (name.includes('observationDisplay')) return display
    if (name.includes('stores/dashboard')) return { useDashboardStore: () => store }
    if (name.includes('instrumentSupport')) return { canOpen: () => supported }
    if (name.includes('waitAudit')) return { auditLabel: () => '等待' }
    if (name.includes('AppCard.vue')) return { __esModule: true, default: Card }
    if (name === 'lucide-vue-next') return Object.fromEntries(['TrendingUp', 'TrendingDown', 'ArrowUpRight', 'Compass', 'Activity'].map(name => [name, { render: () => Vue.h('i', { 'data-icon': name }) }]))
    if (name.endsWith('.vue')) return { __esModule: true, default: Empty }
    throw new Error(`Unexpected import ${name}`)
  }
  new Function('require', 'exports', compiled)(require, exports)
  return renderToString(Vue.createSSRApp(exports.default))
}

test('actual matrix uses same-instrument raw validity, not merged factor-library defaults', async () => {
  const html = await renderCard({ smart_money: { weighted_long_pct: 50, net_flow_usdt: '0 U' } }, { valid: false })
  assert.match(html, /聪明钱:/)
  assert.match(html, /净敞口:/)
  assert.match(html, /未取得数据/)
  assert.doesNotMatch(html, /50%多|99%多|0 U|0%|NaN|\+%|data-icon="Trending(?:Up|Down)"/)
  assert.match(html, /置信:/)
})

test('actual matrix preserves nested confidence=0 and valid zero observations', async () => {
  const html = await renderCard({ price: 0, chg24h: 0, confidence: 88, decision: { confidence: 0 } }, { valid: true, weighted_long_pct: 0, net_flow_usdt: '0 U' })
  assert.match(html, /\$0/)
  assert.match(html, /\+0%/)
  assert.match(html, /0%多/)
  assert.match(html, /0 U/)
  assert.match(html, /置信:<\/span>\s*<span[^>]*>0%<\/span>/)
  assert.doesNotMatch(html, /88%|未取得数据/)
})

test('actual matrix rejects non-finite fields and keeps unsupported instruments observation-only', async () => {
  const invalid = await renderCard({ price: NaN, chg24h: NaN, decision: { confidence: NaN }, calculus: { velocity_1h: NaN } }, { valid: true, weighted_long_pct: NaN, net_flow_usdt: 'NaN U' })
  assert.doesNotMatch(invalid, /NaN|\+%|data-icon="Trending(?:Up|Down)"/)
  const unsupported = await renderCard({ confidence: 75 }, { valid: true, weighted_long_pct: 65, net_flow_usdt: '20 U' }, false)
  assert.doesNotMatch(unsupported, /65%多|20 U|75%/)
  assert.match(unsupported, /仅观察/)
})
