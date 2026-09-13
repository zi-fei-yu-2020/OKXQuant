import test from 'node:test'
import assert from 'node:assert/strict'
import { dashboardIsStale } from '../src/utils/dashboardHealth.ts'

test('backend health is honored even without the legacy is_stale field', () => {
  for (const status of ['STALE', 'PARTIAL', 'OFFLINE']) {
    assert.equal(dashboardIsStale({ data_health: { status } }), true)
  }
})

test('partial or stale data is never shown as fresh', () => {
  assert.equal(dashboardIsStale({ is_stale: false, data_health: { status: 'STALE' } }), true)
  assert.equal(dashboardIsStale({ data_health: { status: 'LIVE', partial: true } }), true)
  assert.equal(dashboardIsStale({ is_stale: true }), true)
})

test('successful and absent snapshots do not invent a stale flag', () => {
  assert.equal(dashboardIsStale({ data_health: { status: 'LIVE', partial: false } }), false)
  assert.equal(dashboardIsStale(null), false)
})
