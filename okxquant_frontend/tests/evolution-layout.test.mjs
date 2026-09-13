import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import {renderToString} from 'vue/server-renderer'
import {parse,compileScript} from '@vue/compiler-sfc'
import ts from 'typescript'
import * as display from '../src/utils/evolutionDisplay.ts'
import * as observations from '../src/utils/observationDisplay.ts'

const card={setup:(_,{slots})=>()=>Vue.h('section',slots.default?.())}
function component(name){
 const source=readFileSync(new URL('../src/components/'+name,import.meta.url),'utf8')
 const {descriptor}=parse(source)
 const script=compileScript(descriptor,{id:name,inlineTemplate:true})
 const code=ts.transpileModule(script.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={}
 new Function('require','exports',code)(name=>{
  if(name==='vue')return Vue
  if(name.includes('evolutionDisplay'))return display
  if(name.includes('observationDisplay'))return observations
  if(name.includes('/ui/'))return {__esModule:true,default:card}
  if(name.endsWith('.vue'))return {__esModule:true,default:component(name.split('/').pop())}
  throw Error(name)
 },exports)
 return exports.default
}
const Review=component('EvolutionReviewPanel.vue'),Memory=component('PublishedMemoryPanel.vue')
const render=(comp,props)=>renderToString(Vue.createSSRApp(comp,props))
const insights=['【已验证事实】累计19笔，净亏损21.48 U。','【已验证事实】手续费7.50 U。','【待验证假设】试错成本可能偏高。','【数理快照不可观测】缺少历史快照。','未分类原文 <script>unsafe()</script>']
const review={status:'success',last_attempt_at:'2026-09-08 20:00:02',last_success_at:'2026-09-08 20:00:08',active_memory_updated_at:'2026-09-06 20:00:05',review_change_status:'NO_CHANGE',sample_size:19,win_rate:26.3,pending_candidates:2,rejected_candidates:2,insights,recommendations:['建议原文，不得自动执行'],review_markdown:'# 原始报告\n【已验证事实】保留正文。'}
const publication={status:'ok',managed:true,scope:'demo-test',revision:1,active_version:1,published_at:'2026-09-08 10:47:00',effective_updated_at:'2026-09-06 20:00:05',format:'legacy_snapshot',rules:[],legacy_context:'兼容上下文原文',prompt_text:'完整模型输入原文',prompt_hash:'abcdef0123456789'.repeat(4)}

test('grouping preserves every original insight without independently verifying model claims',()=>{
 const before=structuredClone(insights),groups=display.insightGroups(insights)
 assert.deepEqual(groups.map(g=>[g.id,g.items.length]),[['observations',2],['hypotheses',1],['gaps',1],['other',1]])
 assert.equal(groups[0].label,'报告观察')
 assert.deepEqual(groups.flatMap(g=>g.items).sort((a,b)=>a.index-b.index).map(item=>item.original),insights)
 assert.deepEqual(insights,before)
})
test('excerpt is a literal bounded excerpt and never breaks Unicode characters',()=>{
 assert.equal(display.insightExcerpt('🚀证据原文',2),'🚀证…')
 assert.equal(display.insightExcerpt('短句'),'短句')
 assert.equal(display.changeProposalLabel('NO_CHANGE'),'无需变更')
 assert.equal(display.changeProposalLabel('UNKNOWN_STATUS'),'UNKNOWN_STATUS')
 assert.equal(display.changeProposalLabel(null),'—')
})
test('review keeps report and memory timestamps separate, with complete source in disclosures',async()=>{
 const before=structuredClone(review),html=await render(Review,{review})
 for(const token of [review.last_attempt_at,review.last_success_at,review.active_memory_updated_at,'无需变更','NO_CHANGE','26.3%','报告观察','数据缺口','data-review-task','data-review-original','建议原文，不得自动执行'])assert.ok(html.includes(token),token)
 assert.equal((html.match(/data-insight-original/g)||[]).length,insights.length)
 assert.ok(html.includes('&lt;script&gt;unsafe()&lt;/script&gt;'));assert.ok(!html.includes('<script>unsafe()'))
 assert.ok(html.includes('不等于系统已完成独立核验'))
 assert.deepEqual(review,before)
})
test('failure, timeout, no-new-evidence and account mismatch stay explicit',async()=>{
 for(const status of ['failed','timeout','running','not_run','no_new_evidence','other_scope_report']){
  const html=await render(Review,{review:{...review,status}})
  assert.ok(html.includes(display.reviewStatusLabel(status)))
  if(status!=='success')assert.ok(!html.includes('复盘已完成'))
  if(['failed','timeout'].includes(status))assert.ok(html.includes('下方保留上次成功报告'))
 }
 const html=await render(Review,{review:{...review,report_scope_verified:false}})
 assert.ok(html.includes('data-review-scope-warning'))
})
test('missing rates and feedback are unknown, real zero remains zero',async()=>{
 for(const value of [null,undefined,NaN,Infinity,-1,101]){
  const html=await render(Review,{review:{...review,win_rate:value}})
  assert.ok(html.includes('<dd>—</dd>'))
 }
 assert.ok((await render(Review,{review:{...review,win_rate:0}})).includes('0%'))
 const html=await render(Review,{review})
 assert.ok(html.includes('旧报告未记录'));assert.ok(html.includes('旧复盘未保存结构化证据反馈'))
})
test('actual evidence panel remains available including fee and cohort data',async()=>{
 const feedback={entry_snapshot_samples:4,settled_samples:19,partial_snapshot_samples:1,fee_cost:7.5,rebates:0,friction_reversed_trades:2,memory_cohorts:[{prompt_hash:'abc',strategy_version:'commit',execution_profile_signature:'signature',samples:19,net_pnl:-21.48}]}
 const html=await render(Review,{review:{...review,evidence_feedback:feedback}})
 for(const token of ['4 / 19 笔','7.5 U','0 U','-21.48 U','描述性统计'])assert.ok(html.includes(token),token)
})
test('memory version, effective content, legacy provenance and full fingerprint remain separate',async()=>{
 const before=structuredClone(publication),html=await render(Memory,{publication})
 for(const token of ['版本 v1',publication.published_at,publication.effective_updated_at,publication.prompt_hash,publication.legacy_context,publication.prompt_text,'不是新审批规则','暂无审核启用规则','data-memory-metadata'])assert.ok(html.includes(token),token)
 assert.equal((html.match(/data-memory-disclosure=/g)||[]).length,2)
 assert.deepEqual(publication,before)
})
test('only true enabled rules are shown, unavailable memory does not imply zero rules',async()=>{
 const html=await render(Memory,{publication:{...publication,format:'rules',rules:[{id:'a',text:'APPROVED_RULE',enabled:true},{id:'b',text:'DISABLED_RULE',enabled:false}]}})
 assert.ok(html.includes('APPROVED_RULE'));assert.ok(!html.includes('DISABLED_RULE'))
 const failed=await render(Memory,{publication:{...publication,status:'unavailable',message:'Account scope unreadable'}})
 assert.ok(failed.includes('Account scope unreadable'));assert.ok(!failed.includes('条已审核启用规则'))
 const missing=await render(Memory,{})
 assert.ok(missing.includes('尚未取得运行记忆状态'));assert.ok(!missing.includes('条已审核启用规则'))
})
