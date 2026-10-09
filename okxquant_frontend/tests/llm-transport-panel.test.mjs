import test from 'node:test'
import assert from 'node:assert/strict'
import {readFileSync} from 'node:fs'
import * as Vue from 'vue'
import ts from 'typescript'
import {parse,compileScript} from '@vue/compiler-sfc'
const source=readFileSync(new URL('../src/components/LlmTransportPanel.vue',import.meta.url),'utf8')
const state=()=>({policy:{mode:'auto',connect_timeout_seconds:10,first_byte_timeout_seconds:150,idle_timeout_seconds:90,total_timeout_seconds:180,max_response_bytes:8388608},effective_mode:'json',protocol:'openai_chat',capabilities:{stream:{status:'unknown'},json:{status:'unknown'}},warnings:['fixture warning']})
const flush=async()=>{for(let i=0;i<20;i++)await Promise.resolve();await Vue.nextTick()}
function harness(t,{admin=true,provider='p',models=[{id:'m'}],request,confirm=async()=>true}={}){
 const props=Vue.reactive({providerId:provider,models,activeModelId:'m'}),auth=Vue.reactive({token:'session-a',isSuperadmin:admin}),calls=[],unmounted=[]
 let code=ts.transpileModule(source.split('<script setup lang="ts">')[1].split('</script>')[0],{compilerOptions:{target:ts.ScriptTarget.ES2022,module:ts.ModuleKind.ESNext}}).outputText
 const ast=ts.createSourceFile('panel.js',code,ts.ScriptTarget.Latest,true),imports=ast.statements.filter(ts.isImportDeclaration)
 for(const node of [...imports].reverse())code=code.slice(0,node.getStart(ast))+code.slice(node.end)
 const env={...Vue,onUnmounted:fn=>unmounted.push(fn),defineProps:()=>props,useAuthStore:()=>auth,useApi:()=>({api:async(path,options={})=>{calls.push({path,options});return request?request(path,options):state()}}),useDialogs:()=>({confirm})}
 const names=Object.keys(env).filter(key=>/^[A-Za-z_$][\w$]*$/.test(key)&&key!=='default')
 const scope=Vue.effectScope();const panel=scope.run(()=>new Function(...names,code+'\nreturn {selectedModel,state,policy,busy,error,notice,lastResult,save,verify,load,metric,capabilityLabel}')( ...names.map(k=>env[k])))
 t.after(()=>{for(const fn of unmounted)fn();scope.stop()})
 return {panel,props,auth,calls,unmounted}
}
test('transport panel compiles using existing accessible UI primitives',()=>{
 const {descriptor,errors}=parse(source);assert.deepEqual(errors,[]);compileScript(descriptor,{id:'LlmTransportPanel',inlineTemplate:true})
 assert.ok(source.includes('data-llm-transport'));assert.doesNotMatch(source,/v-html/)
})
test('mount and model selection only read state, never run a billable verification',async t=>{
 const h=harness(t);await flush();assert.ok(h.calls.length>=1);assert.ok(h.calls.every(c=>!c.options.method));assert.equal(h.panel.state.value.effective_mode,'json')
})
test('unsaved provider or missing model makes no requests',async t=>{
 const a=harness(t,{provider:''}),b=harness(t,{models:[]});await flush();assert.equal(a.calls.length,0);assert.equal(b.calls.length,0)
})
test('ordinary admin cannot save or verify even by calling handlers',async t=>{
 const h=harness(t,{admin:false});await flush();await h.panel.save();await h.panel.verify('stream');assert.ok(h.calls.every(c=>!c.options.method))
})
test('verification cancellation sends no POST',async t=>{
 const h=harness(t,{confirm:async()=>false});await flush();await h.panel.verify('stream');assert.ok(h.calls.every(c=>!c.options.method))
})
test('successful explicit verification uses one POST and current bound receipt',async t=>{
 const verified={...state(),effective_mode:'stream',capabilities:{stream:{status:'verified'},json:{status:'unknown'}}}
 const h=harness(t,{request:async(path,options)=>options.method==='POST'?{status:'completed',ok:true,state:verified,diagnostics:{heartbeat_count:0,total_ms:0,completion_seen:true}}:state()})
 await flush();await h.panel.verify('stream');assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);assert.equal(h.panel.state.value.effective_mode,'stream')
 assert.equal(h.panel.metric(0,' ms'),'0 ms');assert.equal(h.panel.metric(null),'未记录')
})
test('late state read cannot overwrite another provider',async t=>{
 let release;const wait=new Promise(r=>release=r)
 const h=harness(t,{request:async path=>path.includes('provider_id=p&')?wait:{...state(),protocol:'claude_messages'}})
 await flush();h.props.providerId='other';await flush();release({...state(),protocol:'old-private-provider'});await flush();assert.equal(h.panel.state.value.protocol,'claude_messages')
})
test('switching context during confirmation cannot submit to the new model',async t=>{
 let release;const h=harness(t,{confirm:()=>new Promise(r=>release=r)});await flush();const work=h.panel.verify('stream');h.props.providerId='other';await flush();release(true);await work;assert.equal(h.calls.filter(c=>c.options.method==='POST').length,0)
})
test('unknown polling outcome stops without resubmitting the model request',async t=>{
 t.mock.timers.enable({apis:['setTimeout','Date'],now:100000})
 const h=harness(t,{request:async(path,options)=>options.method==='POST'?{job_id:'job',status:'queued'}:path.includes('/verification/')?Promise.reject(new Error('Verification job expired')):state()})
 await flush();const work=h.panel.verify('stream');await flush();t.mock.timers.tick(2000);await flush();await work
 assert.equal(h.calls.filter(c=>c.options.method==='POST').length,1);assert.match(h.panel.error.value,/未自动重发/);assert.equal(h.panel.busy.value,'')
})
test('failed verification is not labeled as unsupported',async t=>{
 const h=harness(t);await flush();assert.equal(h.panel.capabilityLabel('failed'),'验证未通过');assert.equal(h.panel.capabilityLabel('unsupported'),'明确不支持')
})
