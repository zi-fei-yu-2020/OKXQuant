import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { poolStatusLabel, poolMoney } from '../src/utils/capitalPool.ts'

test('unknown allocation values are not fake zeros', () => {
  assert.equal(poolMoney(undefined), '--')
  assert.equal(poolMoney(NaN), '--')
  assert.equal(poolMoney(true), '--')
  assert.equal(poolMoney(300), '300.00')
  assert.equal(poolMoney(297), '297.00')
})
test('disabled, blocked and initialization are distinct', () => {
  assert.notEqual(poolStatusLabel('blocked'), poolStatusLabel('active'))
  assert.notEqual(poolStatusLabel('awaiting_flat_initialization'), poolStatusLabel('active'))
})
test('pool view is optional and distinguishes allocation from exchange cash', () => {
  const text=readFileSync(new URL('../src/components/CapitalPoolPanel.vue',import.meta.url),'utf8')
  assert.ok(text.includes('v-if="pool?.enabled"'))
  assert.ok(text.includes('交易所USDT权益'))
  assert.ok(text.includes('不是额外现金'))
  assert.ok(text.includes('min-w-0'))
})
