import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
const read = name => readFileSync(new URL('../src/' + name, import.meta.url), 'utf8')

test('monitor and administrator share separate review and active-memory timestamps', () => {
  assert.ok(read('components/SelfEvolutionLab.vue').includes('<EvolutionReviewPanel'))
  assert.ok(read('views/admin/EvolutionPage.vue').includes('<EvolutionReviewPanel'))
  const panel = read('components/EvolutionReviewPanel.vue')
  for (const field of ['last_attempt_at', 'last_success_at', 'active_memory_updated_at', 'pending_candidates', 'rejected_candidates']) assert.ok(panel.includes(field))
  assert.ok(panel.includes('NO_CHANGE'))
  assert.ok(panel.includes('no_new_evidence'))
})
test('review UI does not fabricate optimized weights or successful strategy improvements', () => {
  const source = read('components/SelfEvolutionLab.vue') + read('components/EvolutionReviewPanel.vue')
  for (const text of ['35%', '30%', '25%', '10%', '处于最优稳态区间', '锁死期望值优势', 'v-html']) assert.ok(!source.includes(text))
  assert.ok(source.includes('未自动执行'))
  assert.ok(source.includes('observedNumber(props.review?.win_rate)'))
  assert.ok(source.includes('value >= 0 && value <= 100'))
})
test('review text and long words are contained on mobile and disclosure remains accessible', () => {
  const panel = read('components/EvolutionReviewPanel.vue')
  const css=read('components/research-panels.css')
  assert.ok(css.includes('grid-template-columns: repeat(3,minmax(0,1fr))'))
  assert.ok(css.includes('overflow-wrap: anywhere'))
  assert.ok(css.includes('summary:focus-visible'))
  assert.ok(panel.includes('<summary'))
  assert.ok(panel.includes('role="status"'))
})
