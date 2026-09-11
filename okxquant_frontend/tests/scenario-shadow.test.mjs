import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { scenarioLabel, shadowStateLabel, shadowReason, shadowPrice } from '../src/utils/scenarioShadow.ts'

test('shadow trigger and expired review never claim authorized trades', () => {
  assert.equal(shadowStateLabel('triggered_research'), '条件已触发 · 尚未复核')
  assert.equal(shadowStateLabel('review_expired'), '复核窗口已过期')
  assert.equal(shadowStateLabel('unknown'), '状态待核验')
  assert.ok(shadowReason('closed_5m_trigger_requires_fresh_review').includes('不是下单授权'))
})
test('unknown shadow prices are not invented zeros', () => {
  for (const value of [undefined, null, NaN, Infinity, true]) assert.equal(shadowPrice(value), '--')
  assert.equal(shadowPrice(0.08953), '0.08953')
  assert.equal(scenarioLabel('trend_pullback'), '趋势回踩')
})
test('shadow card is opt-in, keyboard accessible, and does not stretch its neighbor', () => {
  const source = readFileSync(new URL('../src/components/ScenarioShadowPanel.vue', import.meta.url),'utf8')
  for (const token of ['v-if="shadow?.enabled"', '<details', '<summary', 'items-start', 'self-start', 'overflow-wrap: anywhere', '仅研究 · 不下单']) assert.ok(source.includes(token))
  assert.ok(!source.includes('v-html'))
})
test('front and administrator share the same shadow card', () => {
  for (const path of ['../src/components/InstrumentMatrix.vue','../src/views/admin/DecisionsPage.vue']) {
    assert.ok(readFileSync(new URL(path, import.meta.url),'utf8').includes('<ScenarioShadowPanel'))
  }
})
