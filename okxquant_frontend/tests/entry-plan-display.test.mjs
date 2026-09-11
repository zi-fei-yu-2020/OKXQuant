import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as display from '../src/utils/entryPlanDisplay.ts'
import * as audit from '../src/utils/waitAudit.ts'

const checks=[
 {side:'long',setup:'pullback_reclaim',reason:'closed_candle_trigger_not_met'},
 {side:'long',setup:'closed_range_breakout',reason:'closed_candle_trigger_not_met'},
 {side:'short',setup:'closed_range_breakout',reason:'net_rr_below_policy',net_rr:.29},
]
const item={instId:'BTC-USDT-SWAP',action:'WAIT',status:'incomplete',reason:'WAIT审计不完整：声称盈亏比不足必须给出可计算方案',entry_plans:{plans:[],checks}}

test('duplicate reasons are grouped without deleting their individual checks or setups',()=>{
 const copy=structuredClone(checks),groups=display.groupedPlanChecks(checks)
 assert.equal(groups.length,2);assert.equal(groups[0].checks.length,2)
 assert.deepEqual(groups[0].setups,['pullback_reclaim','closed_range_breakout'])
 assert.deepEqual(groups[1].ratios,[.29]);assert.deepEqual(checks,copy)
})
test('different net RR observations remain available and missing is not converted to zero',()=>{
 const result=display.groupedPlanChecks([{...checks[2],net_rr:0},{...checks[2],net_rr:null},{...checks[2],net_rr:.29}])
 assert.deepEqual(result[0].ratios,[0,.29]);assert.equal(result[0].checks.length,3)
})
test('model audit failures are explicit and never presented as successful WAIT',()=>{
 assert.equal(display.decisionLabel(item),'审计未通过')
 assert.equal(display.incompleteReason(item),'声称盈亏比不足必须给出可计算方案')
 assert.equal(display.incompleteReason({...item,reason:undefined},'继续等待必须引用相对前轮变化的新证据'),'继续等待必须引用相对前轮变化的新证据')
 assert.equal(display.incompleteReason({...item,status:'audited_wait'}),'')
 assert.equal(display.decisionLabel({...item,status:'audited_wait'}),'等待 · 审计通过')
 assert.equal(display.decisionLabel({...item,status:'unknown'}),'等待 · 尚未审计')
})

const source=readFileSync(new URL('../src/components/DecisionAuditPanel.vue',import.meta.url),'utf8')
const {descriptor}=parse(source)
const script=compileScript(descriptor,{id:'entry-display',inlineTemplate:true})
const code=ts.transpileModule(script.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const exports={}
new Function('require','exports',code)(name=>{
 if(name==='vue')return Vue
 if(name.includes('entryPlanDisplay'))return display
 if(name.includes('waitAudit'))return audit
 if(name.endsWith('.vue'))return {__esModule:true,default:{setup:(_,{slots})=>()=>Vue.h('section',slots.default?.())}}
 throw Error(name)
},exports)

test('actual audit component separates validation reason from program trigger checks',async()=>{
 const html=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[item],counts:{program_plans:0,incomplete:1}},audit:{status:'ok',items:[]}}))
 assert.ok(html.includes('审计未通过：声称盈亏比不足必须给出可计算方案'))
 assert.ok(html.includes('有决策需要补全'))
 assert.ok(html.includes('回踩回收'))
 assert.ok(html.includes('区间突破'))
 assert.ok(html.includes('净 R:R 0.29'))
 assert.ok(html.includes('3 项 · 保留全部形态'))
 assert.equal((html.match(/data-wait-audit-card=/g)||[]).length,1)
 assert.ok(!html.includes('最终 WAIT · WAIT'))
 assert.ok(html.includes('data-plan-check-details'))
})
test('cycle and audit records merge into one instrument without changing source data',()=>{
 const cycle={items:[item]},state={status:'ok',items:[{instId:item.instId,status:'audited_wait',reason:'old result'},{instId:'ETH-USDT-SWAP',status:'audited_wait'}]}
 const before=structuredClone({cycle,state}),rows=display.mergedAuditRows(cycle,state)
 assert.equal(rows.length,2);assert.equal(rows[0].status,'incomplete');assert.equal(rows[1].historical,true)
 assert.equal(rows[0].record.reason,'old result')
 assert.deepEqual({cycle,state},before)
})
test('direction preview prioritizes blockers while retaining raw checks and missing ratios',()=>{
 const row=display.mergedAuditRows({items:[{...item,status:'audited_wait',entry_plans:{plans:[],checks:[...checks,{side:'long',setup:'pullback_reclaim',reason:'net_rr_below_policy',net_rr:0}]}}]})[0]
 assert.deepEqual(display.directionSummary(row,'long'),{text:'净盈亏比不足（0.00）',extra:1})
 assert.equal(row.item.entry_plans.checks.length,4)
 assert.equal(display.directionSummary(row,'short').text,'净盈亏比不足（0.29）')
 assert.equal(display.compactAuditStatus('unknown'),'待审计')
})
test('one visible summary replaces duplicate symbol grids and omits zero exceptions',async()=>{
 const cycle={evaluated_count:5,counts:{entry_candidate:0,audited_wait:5,incomplete:0,execution_rejected:0},items:['BTC','ETH','SOL','DOGE','SUI'].map(name=>({...item,instId:name+'-USDT-SWAP',status:'audited_wait',reason:'waiting'}))}
 const state={status:'ok',alert:true,no_entry_candidate_streak:14,items:cycle.items.map(row=>({...row}))}
 const html=await renderToString(Vue.createSSRApp(exports.default,{cycle,audit:state}))
 assert.equal((html.match(/data-wait-audit-card=/g)||[]).length,5)
 assert.equal((html.match(/<strong>BTC<\/strong>/g)||[]).length,1)
 assert.ok(html.includes('本轮暂无开仓候选'));assert.ok(html.includes('最终 WAIT 连续 14 轮'))
 assert.ok(!html.includes('连续 14 轮无候选'))
 assert.ok(!html.includes('<dt>待补全</dt>'));assert.ok(!html.includes('<dt>执行未通过</dt>'))
 assert.ok(!html.includes('WAIT ·'));assert.ok(html.includes('data-audit-explanation'))
})
test('selected and rejected candidates remain honest and retain prices and reviews',async()=>{
 const plan={id:'p1',action:'BUY_LONG',setup:'pullback_reclaim',entry_price:100,stop_loss_price:98,take_profit_price:110,net_rr:NaN}
 const row={...item,status:'execution_rejected',reason:'Risk budget exhausted',candidate_id:'p1',entry_plans:{plans:[plan],checks:[]},candidate_reviews:[{candidate_id:'p1',reason:'explicit evidence'}]}
 const html=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[row]}}))
 for(const value of ['Risk budget exhausted','模型已选择，但执行未通过','explicit evidence','<dd>100</dd>','<dd>98</dd>','<dd>110</dd>','<dd>—</dd>'])assert.ok(html.includes(value),value)
 assert.ok(!html.includes('NaN'));assert.ok(!html.includes('已成交'))
})
test('audit outage and raw evidence are retained rather than summarized as a successful wait',async()=>{
 const direction={reason:'full explanation',evidence:[{ref:'/price',value:100,interpretation:'source evidence'}],reconsider:{conditions:[{ref:'/price',op:'gt',value:101}],reason:'recheck once changed'}}
 const state={status:'error',message:'audit unavailable',items:[{instId:'BTC-USDT-SWAP',status:'incomplete',error:'required evidence missing',audit:{long:direction,short:direction}}]}
 const html=await renderToString(Vue.createSSRApp(exports.default,{audit:state,cycle:{executed_actions:['confirmed read-only fixture event'],environment_notices:['WLD demo unsupported']}}))
 for(const value of ['本轮数据待核验','audit unavailable','required evidence missing','source evidence','recheck once changed','confirmed read-only fixture event','WLD demo unsupported'])assert.ok(html.includes(value),value)
 assert.ok(!html.includes('本轮暂无开仓候选'))
})
test('no cycle counts means unknown not zero and program errors remain visible',async()=>{
 const empty=await renderToString(Vue.createSSRApp(exports.default,{}))
 assert.ok(empty.includes('等待本轮决策'));assert.ok(!empty.includes('<dd>0</dd>'))
 const broken=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[{...item,status:'audited_wait',entry_plans:{plans:[],checks:[],error:'entry_candle_gap_or_duplicate'}}]}}))
 assert.ok(broken.includes('entry_candle_gap_or_duplicate'))
})

test('new diagnostic streaks separate absent drafts and bad audits without relabeling history',async()=>{
 const diagnostics={version:'wait-diagnostics-v2',since:1788962400,observed_rounds:3,streaks:{no_program_plans:3,model_all_wait:3,audit_incomplete:2,audited_wait_with_plans:0}}
 const html=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[item]},audit:{status:'incomplete',items:[],no_entry_candidate_streak:14,legacy_final_wait_streak:14,diagnostics}}))
 assert.ok(html.includes('连续 3 轮无程序草案'));assert.ok(html.includes('审计异常连续 2 轮'))
 assert.ok(html.includes('历史最终 WAIT 连续 14 轮'));assert.ok(html.includes('不把旧数据推算成新口径'))
 assert.equal((html.match(/class="audit-streak(?:\s|")/g)||[]).length,2)
})
test('repair disclosure distinguishes original error, failed correction and still-WAIT success',async()=>{
 const corrected={...item,status:'audited_wait',reason:'已重新核验宏观限制',wait_repair:{status:'corrected',attempted:true,initial_error:'原始类别错误'}}
 let html=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[corrected]}}))
 assert.ok(html.includes('一次纠错后审计通过，动作仍为 WAIT，不授权交易。'));assert.ok(html.includes('原始类别错误'))
 const bad={...item,wait_repair:{status:'not_validated',attempted:true,initial_error:'原始类别错误',remaining_error:'仍缺少前轮复查'}}
 html=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[bad]}}))
 assert.ok(html.includes('审计未通过：仍缺少前轮复查'));assert.ok(html.includes('保留决策不完整'))
 html=await renderToString(Vue.createSSRApp(exports.default,{cycle:{items:[{...item,wait_repair:{status:'failed',attempted:true,initial_error:'原始错误',error_type:'LLMRequestError',http_status:503}}]}}))
 assert.ok(html.includes('纠错过程失败'));assert.ok(html.includes('HTTP 503'))
})

test('published memory disclosures have visible action styling and distinct content targets',()=>{
 const memory=readFileSync(new URL('../src/components/PublishedMemoryPanel.vue',import.meta.url),'utf8')
 assert.equal((memory.match(/class="action-disclosure text-xs min-w-0"/g)||[]).length,2)
 for(const value of ['data-memory-disclosure="legacy"','data-memory-disclosure="effective"','点击展开历史兼容内容','核对实际输入原文'])assert.ok(memory.includes(value))
 const css=readFileSync(new URL('../src/style.css',import.meta.url),'utf8')
 assert.ok(css.includes('.action-disclosure[open] > summary::after'))
 assert.ok(css.includes('.action-disclosure > summary:focus-visible'))
 assert.ok(!memory.includes('v-html'))
})

test('historical wait summaries stay compact and full reasons only occur inside details',async()=>{
 const reason='4H处于大级别区间震荡，1H动量速度处于负向承压状态，需要等待完整结构确认。'.repeat(5)
 const direction={code:'confirmation_pending',reason,evidence:[],reconsider:{conditions:[],reason:'保留完整重审条件'}}
 const state={status:'ok',items:[{instId:'BTC-USDT-SWAP',status:'audited_wait',audit:{long:direction,short:direction}}]}
 const html=await renderToString(Vue.createSSRApp(exports.default,{audit:state,cycle:{unavailable_reason:'模型响应未完整结束',items:[]}}))
 const summary=html.match(/<summary class="audit-row__summary">([\s\S]*?)<\/summary>/)[1]
 assert.ok(summary.includes('等待条件确认'));assert.ok(summary.includes('历史等待 · 已审计'))
 assert.ok(!summary.includes(reason));assert.ok(html.includes(reason));assert.ok(html.includes('不代表本轮决策成功'))
 assert.ok(!summary.includes(' open'))
})
test('unknown audit codes are bounded in preview but retain their complete source',()=>{
 const reason='unknown reason '.repeat(40)
 const row=display.mergedAuditRows(undefined,{items:[{instId:'X',status:'audited_wait',audit:{long:{code:'future-code',reason}}}]})[0]
 assert.ok(display.directionSummary(row,'long').text.length<=29)
 assert.equal(row.record.audit.long.reason,reason)
})
