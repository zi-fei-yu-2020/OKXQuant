import test from 'node:test'
import assert from 'node:assert/strict'
import { resolveMacroAnalysis, macroStatusLabel } from '../src/utils/macroAnalysis.ts'
import { isSettlementPending } from '../src/utils/tradeSettlement.ts'

test('macro uses actual history time, not dashboard time', () => {
  const result = resolveMacroAnalysis({ timestamp: '2026-09-06 23:00:00', ai_brain_history: [{time: '2026-09-06 22:45:08', macro_assessment: 'evidence'}] }, Date.parse('2026-09-06T23:00:00+08:00'))
  assert.equal(result.analyzed_at, '2026-09-06 22:45:08')
  assert.equal(result.status, 'ready')
})
test('macro unknown time is never current', () => {
  assert.equal(resolveMacroAnalysis({macro_assessment: 'old'}).status, 'stale')
  assert.equal(resolveMacroAnalysis(null).status, 'empty')
})
test('backend failure state is preserved', () => {
  const state = {text: 'old', analyzed_at: '', status: 'failed', message: '503'}
  assert.equal(resolveMacroAnalysis({macro_analysis: state}), state)
  assert.ok(macroStatusLabel('failed'))
})
test('pending settlement is distinct from settled and holding', () => {
  assert.equal(isSettlementPending({status: 'closed_pending'}), true)
  assert.equal(isSettlementPending({settlement_status: 'pending'}), true)
  assert.equal(isSettlementPending({status: 'closed'}), false)
  assert.equal(isSettlementPending({status: 'holding'}), false)
})
