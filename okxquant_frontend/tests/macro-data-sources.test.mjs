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
const status=(revision=1)=>({configuration:{revision,official_enabled:true,fmp_enabled:false,calendar_timezone:'',api_key_configured:false},snapshot:{sources:{},quotes:{},rates:{},events:[]}})
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

test('status load never returns or populates a provider secret',async()=>{
 const h=harness();try{await load(h);assert.equal(h.state.form.api_key,'');assert.equal(h.state.form.fmp_enabled,false);assert.equal(h.state.busy.value,false)}finally{h.cleanup()}
})
test('blank key is omitted on save and configuration revision is sent',async()=>{
 const h=harness();try{await load(h);const p=h.state.save();const req=h.requests.at(-1);const body=JSON.parse(req.options.body)
 assert.equal(req.options.method,'PUT');assert.equal(Object.hasOwn(body,'api_key'),false);assert.equal(body.expected_revision,1)
 req.resolve(status(2));await p;assert.equal(h.state.state.value.configuration.revision,2)
 }finally{h.cleanup()}
})
test('successful save clears a typed key and duplicate submits are suppressed',async()=>{
 const h=harness();try{await load(h);h.state.form.api_key='test-private-key';const p=h.state.save();await h.state.save()
 assert.equal(h.requests.length,2);assert.equal(JSON.parse(h.requests.at(-1).options.body).api_key,'test-private-key')
 h.requests.at(-1).resolve(status(2));await p;assert.equal(h.state.form.api_key,'')
 }finally{h.cleanup()}
})
test('failed save is not reported as success and retains configuration',async()=>{
 const h=harness();try{await load(h);h.state.form.calendar_timezone='UTC';const p=h.state.save();h.requests.at(-1).reject(Error('quota-safe-error'));await p
 assert.equal(h.state.error.value,'quota-safe-error');assert.equal(h.state.form.calendar_timezone,'UTC');assert.equal(h.state.notice.value,'')
 }finally{h.cleanup()}
})
test('probing sources preserves unsaved input and cannot request an AI run',async()=>{
 const h=harness();try{await load(h);h.state.form.api_key='not-submitted';const p=h.state.refresh();const req=h.requests.at(-1)
 assert.equal(req.url,'/api/v1/admin/macro-data/refresh');assert.equal(req.options.body,undefined)
 req.resolve(status());await p;assert.equal(h.state.form.api_key,'not-submitted')
 }finally{h.cleanup()}
})
test('unmount aborts pending request and clears typed secret',async()=>{
 const h=harness();h.state.form.api_key='private';const p=h.state.load();h.cleanup();assert.equal(h.requests[0].options.signal.aborted,true)
 assert.equal(h.state.form.api_key,'');h.requests[0].reject(Error('aborted'));await p;assert.equal(h.state.error.value,'')
})
test('display distinguishes missing values from real zero and includes staleness caveats',()=>{
 const h=harness();try{assert.equal(h.state.number(0),'0');assert.equal(h.state.number(null),'—');assert.equal(h.state.number(NaN),'—')
 assert.match(source,/autocomplete="new-password"/);assert.match(source,/role="alert"/);assert.match(source,/quote\.usable/);assert.match(source,/last_success_at/)
 }finally{h.cleanup()}
})

test('probing never adopts a new revision for an unsaved older form',async()=>{
 const h=harness();try{await load(h);const p=h.state.refresh();h.requests.at(-1).resolve(status(2));await p
 assert.equal(h.state.state.value.configuration.revision,1)
 const save=h.state.save();assert.equal(JSON.parse(h.requests.at(-1).options.body).expected_revision,1)
 h.requests.at(-1).reject(Error('configuration changed'));await save;assert.equal(h.state.error.value,'configuration changed')
 }finally{h.cleanup()}
})
