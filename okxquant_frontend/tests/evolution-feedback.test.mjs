import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import { observedNumber } from '../src/utils/observationDisplay.ts'
const source=readFileSync(new URL('../src/components/EvolutionEvidencePanel.vue',import.meta.url),'utf8')
const {descriptor}=parse(source)
const script=compileScript(descriptor,{id:'evolution-feedback',inlineTemplate:true})
const compiled=ts.transpileModule(script.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const exports={}
new Function('require','exports',compiled)(name=>{
  if(name==='vue')return Vue
  if(name.includes('observationDisplay'))return {observedNumber}
  throw Error(name)
},exports)
const render=feedback=>renderToString(Vue.createSSRApp(exports.default,{feedback}))
test('legacy review does not pretend it already contains forward validation',async()=>{
 const html=await render(undefined)
 assert.ok(html.includes('旧复盘未保存结构化证据反馈'))
 assert.ok(!html.includes('0 U'))
})
test('feedback distinguishes actual zero, signed outcomes, unknown costs and precision',async()=>{
 const html=await render({settled_samples:2,entry_snapshot_samples:1,partial_snapshot_samples:1,fee_cost:null,rebates:0,friction_reversed_trades:1})
 assert.ok(html.includes('未完整核对'));assert.ok(html.includes('0 U'));assert.ok(html.includes('1 / 2 笔'))
 const precise=await render({fee_cost:.00001234,rebates:.0025})
 assert.ok(precise.includes('0.00001234 U'));assert.ok(precise.includes('0.0025 U'))
 for(const value of [true,NaN,Infinity,''])assert.ok((await render({fee_cost:value})).includes('未完整核对'))
})
test('memory outcome cohorts keep artifact identity and explicit non-causal authority',async()=>{
 const html=await render({memory_cohorts:[{prompt_hash:'a'.repeat(64),strategy_version:'code-version',execution_profile_signature:'b'.repeat(64),samples:3,net_pnl:-.12345678}]})
 assert.ok(html.includes('-0.12345678 U'))
 assert.ok(html.includes('代码 code-ver'))
 assert.ok(html.includes('分组盈亏不是对照实验'))
 assert.ok(html.includes('不会因短期盈利自动推广心法'))
 assert.ok(!html.includes('button'))
})
