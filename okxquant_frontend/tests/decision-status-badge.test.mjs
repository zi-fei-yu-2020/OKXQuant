import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
const source=readFileSync(new URL('../src/components/StrategyTelemetryPanel.vue',import.meta.url),'utf8')
async function render(status,code,counts={}) {
 const {descriptor,errors}=parse(source); assert.equal(errors.length,0)
 const compiled=compileScript(descriptor,{id:'telemetry-badge',inlineTemplate:true})
 const js=ts.transpileModule(compiled.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={}
 new Function('require','exports',js)(name=>{
  if(name==='vue')return Vue
  if(name.includes('stores/dashboard'))return {useDashboardStore:()=>({data:{decision_cycle:{status,counts},wait_state:{code,detail:'SHOULD_NOT_RENDER_LONG_COPY'},execution_profile:{execution:{id:'standard'}}}})}
  if(name.endsWith('.vue'))return {__esModule:true,default:{setup:(_,{slots})=>()=>Vue.h('section',slots.default?.())}}
  throw Error(name)
 },exports)
 return renderToString(Vue.createSSRApp(exports.default))
}
test('decision status lives in one Chinese badge, never a duplicate explanatory body',async()=>{
 for(const [status,code,label] of [['reviewed','NO_PROGRAM_CANDIDATE','等待'],['incomplete','AUDIT_INCOMPLETE','待补全'],['unavailable','NO_PROGRAM_CANDIDATE','不可用'],['running',null,'处理中']]){
  const html=await render(status,code)
  assert.equal((html.match(new RegExp(label,'g'))||[]).length,1)
  assert.ok(html.includes('data-decision-status'))
  assert.ok(!html.includes('SHOULD_NOT_RENDER_LONG_COPY'))
  assert.ok(!html.includes('WAIT 当前状态'))
  assert.ok(!html.includes('telemetry-decision__copy'))
 }
 const html=await render('reviewed','CANDIDATE_REVIEW',{entry_candidate:1})
 assert.ok(html.includes('有候选'))
})
