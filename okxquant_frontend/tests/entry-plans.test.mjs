import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const panel=readFileSync(new URL('../src/components/DecisionAuditPanel.vue',import.meta.url),'utf8')
test('program plans distinguish drafts, model selection and final execution',()=>{
 for(const token of ['data-entry-plan-review','不等于订单','candidate_id === plan.id','仍需执行核验','candidate_reviews','entry_price','stop_loss_price','take_profit_price'])assert.ok(panel.includes(token),token)
 assert.ok(panel.includes('numberText(plan.net_rr)'))
 assert.ok(panel.includes('Number.isFinite(value)'))
})
test('candidate diagnostics remain contained and independently expandable on mobile',()=>{
 assert.ok(panel.includes('<details v-for="row in rows"'))
 assert.ok(panel.includes('audit-row__summary:focus-visible'))
 assert.ok(panel.includes('container-type: inline-size'))
 assert.ok(panel.includes('overflow-wrap: anywhere'))
 assert.ok(!panel.includes('v-html'))
})
