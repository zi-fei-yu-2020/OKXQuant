import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'

const source=readFileSync(new URL('../src/components/MacroDataSources.vue',import.meta.url),'utf8')
const {descriptor,errors}=parse(source)
assert.deepEqual(errors,[])
const js=ts.transpileModule(compileScript(descriptor,{id:'macro-data-test'}).content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const status=(revision=1)=>({configuration:{revision,official_enabled:true},snapshot:{sources:{},rates:{},events:[]}})
function harness(){
 const requests=[];const unmount=[];const exports={}
 new Function('require','exports',js)(name=>{
  if(name==='vue')return {...Vue,onMounted:()=>{},onBeforeUnmount:fn=>unmount.push(fn)}
  if(name.includes('useApi'))return {useApi:()=>({api:(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject}))})}
  if(name.endsWith('.vue'))return {__esModule:true,default:{}}
  throw Error(name)
 },exports)
 const scope=Vue.effectScope();const state=scope.run(()=>exports.default.setup({}, {expose:()=>{}}))
 return {state,requests,cleanup(){unmount.forEach(fn=>fn());scope.stop()}}
}
async function load(h){const task=h.state.load();h.requests.at(-1).resolve(status());await task}


test('only official controls and sources are exposed',async()=>{
 const h=harness();try{await load(h);assert.deepEqual(Object.keys(h.state.form),['official_enabled']);assert.deepEqual(Object.keys(h.state.sources),['treasury','bea','bls'])
 assert.doesNotMatch(source,/FMP|fmp_|financialmodelingprep|type="password"|form\.api_key|calendar_timezone/)
 }finally{h.cleanup()}
})
test('save submits only official flag and revision; repeated clicks coalesce',async()=>{
 const h=harness();try{await load(h);h.state.form.official_enabled=false;const p=h.state.save();await h.state.save();assert.equal(h.requests.length,2)
 assert.deepEqual(JSON.parse(h.requests.at(-1).options.body),{official_enabled:false,expected_revision:1})
 h.requests.at(-1).resolve(status(2));await p;assert.equal(h.state.state.value.configuration.revision,2)
 }finally{h.cleanup()}
})
test('failed save preserves edits and does not claim success',async()=>{
 const h=harness();try{await load(h);h.state.form.official_enabled=false;const p=h.state.save();h.requests.at(-1).reject(Error('failed'));await p
 assert.equal(h.state.error.value,'failed');assert.equal(h.state.form.official_enabled,false);assert.equal(h.state.notice.value,'')
 }finally{h.cleanup()}
})
test('refresh preserves unsaved state and revision rather than silently overwriting settings',async()=>{
 const h=harness();try{await load(h);h.state.form.official_enabled=false;const p=h.state.refresh();const req=h.requests.at(-1)
 assert.equal(req.url,'/api/v1/admin/macro-data/refresh');assert.equal(req.options.body,undefined);req.resolve(status(2));await p
 assert.equal(h.state.form.official_enabled,false);assert.equal(h.state.state.value.configuration.revision,1)
 }finally{h.cleanup()}
})
test('unmount cancels pending UI reads without late error feedback',async()=>{
 const h=harness();const p=h.state.load();h.cleanup();assert.equal(h.requests[0].options.signal.aborted,true)
 h.requests[0].reject(Error('aborted'));await p;assert.equal(h.state.error.value,'')
})
test('zero and missing official observations remain distinct',()=>{
 const h=harness();try{assert.equal(h.state.number(0),'0');assert.equal(h.state.number(null),'—');assert.equal(h.state.number(NaN),'—')
 assert.match(source,/role="alert"/);assert.match(source,/last_success_at/);assert.doesNotMatch(source,/event\.actual|event\.estimate|state\.snapshot\.quotes/)
 }finally{h.cleanup()}
})
