import test from 'node:test'
import assert from 'node:assert/strict'
import { positionQuotaLabel } from '../src/utils/entryQuota.ts'
test('disabled quantity caps show daily breaker control rather than legacy counts', () => {
  assert.equal(positionQuotaLabel({quantity_limits_enabled: false, max_active_instruments: 2}), '日内熔断控制（无数量配额）')
})
test('unknown configuration never invents an enabled or disabled quota', () => {
  assert.equal(positionQuotaLabel({max_active_instruments: 6}), '待核验')
})
test('only an explicitly enabled valid quota displays a count', () => {
  assert.equal(positionQuotaLabel({quantity_limits_enabled: true, max_active_instruments: 2}), '2 个')
  assert.equal(positionQuotaLabel({quantity_limits_enabled: true, max_active_instruments: 0}), '配额不可用')
})
