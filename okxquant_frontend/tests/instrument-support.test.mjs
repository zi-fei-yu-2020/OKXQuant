import test from 'node:test'
import assert from 'node:assert/strict'
import { canOpen, instrumentSupport, supportTone } from '../src/utils/instrumentSupport.ts'
const row = { instId: 'WLD-USDT-SWAP', environment: 'demo', status: 'unsupported', can_open: false, label: '模拟盘不支持', message: '仅观察', checked_at: 1 }
test('unsupported instruments are observation-only, not neutral trading signals', () => {
  assert.equal(canOpen(row), false)
  assert.equal(supportTone(row), 'warning')
  assert.equal(instrumentSupport(row.instId, { items: { [row.instId]: row } }, 'demo').label, '模拟盘不支持')
})
test('mode changes and missing status cannot reuse a supported badge', () => {
  const supported = { ...row, status: 'supported', can_open: true }
  assert.equal(canOpen(supported), true)
  const result = instrumentSupport(row.instId, { items: { [row.instId]: supported } }, 'live')
  assert.equal(result.status, 'unknown')
  assert.equal(canOpen(result), false)
  assert.equal(canOpen(undefined), false)
})
test('unknown/unavailable statuses never permit opening even with inconsistent can_open', () => {
  for (const status of ['unknown','unsupported','unavailable']) assert.equal(canOpen({ ...row, status, can_open: true }), false)
})
