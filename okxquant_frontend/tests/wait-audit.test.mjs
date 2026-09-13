import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { auditLabel, conditionText, reviewLabel } from '../src/utils/waitAudit.ts'

test('incomplete and legacy WAIT never display as audited', () => {
  assert.equal(auditLabel('incomplete'), '决策不完整')
  assert.equal(auditLabel(), '待审计')
  assert.equal(auditLabel('audited_wait'), 'WAIT · 审计通过')
})
test('predicates are explicit and missing data is availability not invented zero', () => {
  assert.equal(conditionText({ref:'/price',op:'gte',value:100}), '/price ≥ 100')
  assert.equal(conditionText({ref:'/rsi_1h',op:'available',value:true}), '/rsi_1h 恢复可用')
})
test('audit details remain keyboard accessible and constrain mobile widths', () => {
  const source = readFileSync(new URL('../src/components/DecisionAuditPanel.vue', import.meta.url),'utf8')
  assert.ok(source.includes('<details'))
  assert.ok(source.includes('<summary'))
  assert.ok(source.includes('grid-cols-1'))
  assert.ok(source.includes('min-w-0'))
  assert.ok(source.includes('overflow-wrap: anywhere'))
  assert.ok(!source.includes('v-html'))
})
test('monitor and administrator reuse the same audit component', () => {
  for (const path of ['../src/components/SelfEvolutionLab.vue','../src/views/admin/DecisionsPage.vue']) {
    assert.ok(readFileSync(new URL(path, import.meta.url),'utf8').includes('<DecisionAuditPanel'))
  }
})

test('previous review label reflects completion instead of perpetually requiring review', () => {
  assert.equal(reviewLabel({status:'audited_wait', previous_check:{required:true}, audit:{previous_review:{reason:'checked'}}}), '\u524d\u8f6e\u6761\u4ef6\u5df2\u590d\u67e5')
  assert.equal(reviewLabel({status:'incomplete', previous_check:{required:true}}), '\u524d\u8f6e\u6761\u4ef6\u5f85\u590d\u67e5')
  assert.equal(reviewLabel({status:'audited_wait', previous_check:{required:false}}), '')
})
test('grid audit cards opt out of sibling stretch and expose complete actions', () => {
  const source = readFileSync(new URL('../src/components/DecisionAuditPanel.vue', import.meta.url),'utf8')
  assert.ok(source.includes('items-start'))
  assert.ok(source.includes('self-start'))
  assert.ok(source.includes(':data-wait-audit-card="row.instId"'))
  assert.ok(source.includes('cycle.executed_actions'))
})
