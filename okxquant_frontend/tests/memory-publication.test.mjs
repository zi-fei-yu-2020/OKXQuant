import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { memoryActionLabel, memoryCandidateLabel } from '../src/utils/memory.ts'
const read = file => readFileSync(new URL('../src/' + file, import.meta.url), 'utf8')

test('published memory has shared model/public/admin source and explicit legacy provenance',()=>{
 assert.ok(read('components/SelfEvolutionLab.vue').includes('<PublishedMemoryPanel'))
 assert.ok(read('components/MemoryManagementPanel.vue').includes('<PublishedMemoryPanel'))
 const panel=read('components/PublishedMemoryPanel.vue')
 for(const key of ['prompt_hash','prompt_text','active_version','effective_updated_at','legacy_context'])assert.ok(panel.includes(key))
 assert.ok(panel.includes('不是新审批规则'))
 assert.ok(!panel.includes('v-html'))
})
test('publication requires confirmation, reviewed evidence and frozen scope/revision',()=>{
 const source=read('components/MemoryManagementPanel.vue')
 for(const value of ['PUBLISH MEMORY','INITIALIZE MEMORY','ROLLBACK MEMORY','baseRevision','baseScope','baseEvidenceHash','selectedTrades.value.length>=2','expected_revision','supporting_trade_ids'])assert.ok(source.includes(value))
 assert.ok(source.includes("kind.value==='reject'"))
 assert.ok(source.includes(':disabled="!canSubmit"'))
 assert.ok(!source.includes('setTimeout'))
})
test('legacy toggles, fake baseline restoration and invented schedule are absent from admin UI',()=>{
 const source=read('views/admin/EvolutionPage.vue')
 for(const value of ['rollbackToBaseline','toggleLessonStatus','structuredLessons.filter','每 6 小时 (4次/天)','health_score'])assert.ok(!source.includes(value))
 assert.ok(source.includes('MemoryManagementPanel'))
 assert.equal(memoryActionLabel('REVISE'),'修订规则')
 assert.equal(memoryCandidateLabel('pending'),'待人工审核')
 assert.equal(memoryCandidateLabel('blocked'),'静态检查未通过')
})
