import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import ts from 'typescript'
import * as Vue from 'vue'
import * as Pinia from 'pinia'
import * as macro from '../src/utils/macroAnalysis.ts'
import * as health from '../src/utils/dashboardHealth.ts'
import * as support from '../src/utils/instrumentSupport.ts'
import * as flight from '../src/utils/singleFlight.ts'
import * as snapshot from '../src/utils/dashboardSnapshot.ts'
import * as observation from '../src/utils/observationDisplay.ts'

const source=readFileSync(new URL('../src/stores/dashboard.ts',import.meta.url),'utf8')
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
const exports={}
new Function('require','exports',compiled)(name=>{
 if(name==='vue')return Vue
 if(name==='pinia')return Pinia
 for(const [key,value] of Object.entries({macroAnalysis:macro,dashboardHealth:health,instrumentSupport:support,singleFlight:flight,dashboardSnapshot:snapshot,observationDisplay:observation}))if(name.endsWith('/'+key))return value
 throw Error(name)
},exports)
const flush=async()=>{for(let i=0;i<15;i++)await Promise.resolve()}
const good=changes=>({account_source_id:'demo:a',timestamp:'2026-09-09 08:00:00',account:{total_eq:1000,avail_eq:900},positions_summary:{items:[]},pending_orders:[],factors:[],trades:[{id:'t1',inst:'BTC',net_pnl:1}],logs:['original'],ai_last_prompt:'complete prompt',ai_brain_history:[{time:'08:00',ai_last_prompt:'complete historic prompt'}],data_health:{status:'LIVE',partial:false},...changes})
const reply=value=>({ok:true,status:200,json:async()=>structuredClone(value)})
const deferred=()=>{let resolve,reject;const promise=new Promise((ok,no)=>{resolve=ok;reject=no});return {promise,resolve,reject}}
function harness(t){
 const old={document:globalThis.document,window:globalThis.window,fetch:globalThis.fetch}
 const doc=new EventTarget();doc.visibilityState='visible'
 globalThis.document=doc;globalThis.window=new EventTarget()
 t.mock.timers.enable({apis:['setTimeout','setInterval','Date'],now:100000})
 t.mock.method(console,'error',()=>{})
 let response=async()=>reply(good());const calls=[]
 globalThis.fetch=(url,options)=>{calls.push({url,options});return response(url,options)}
 Pinia.setActivePinia(Pinia.createPinia());const store=exports.useDashboardStore()
 t.after(()=>{store.stopPolling();store.$dispose();for(const [key,value]of Object.entries(old)){if(value===undefined)delete globalThis[key];else globalThis[key]=value}})
 return {store,calls,doc,setResponse:value=>{response=value},tick:async(ms)=>{t.mock.timers.tick(ms);await flush()}}
}

test('unchanged full sections are reused without losing prompts or history',()=>{
 const old=good();const newer=good({timestamp:'new',account:{total_eq:1001,avail_eq:901}})
 const merged=snapshot.shareSnapshot(old,newer)
 assert.equal(merged.trades,old.trades);assert.equal(merged.ai_brain_history,old.ai_brain_history)
 assert.notEqual(merged.account,old.account);assert.equal(merged.ai_last_prompt,'complete prompt')
 assert.equal(old.account.total_eq,1000);assert.equal(snapshot.shareSnapshot(old,structuredClone(old)),old)
 assert.deepEqual(snapshot.shareSnapshot({a:1,b:2},{a:1}),{a:1})
 const untrusted=JSON.parse('{"__proto__":{"polluted":true}}')
 assert.equal(Object.hasOwn(snapshot.shareSnapshot({},untrusted),'__proto__'),true)
 assert.equal({}.polluted,undefined)
})

test('automatic polling keeps the full API and exact three-second cadence without a spinner',async t=>{
 const h=harness(t);h.store.startPolling(3000);await flush()
 assert.equal(h.calls.length,1);assert.equal(h.store.isRefreshing,false)
 assert.ok(h.calls[0].url.startsWith('/api/all?_t='));assert.ok(!h.calls[0].url.includes('view='))
 assert.equal(h.store.data.ai_last_prompt,'complete prompt')
 const trades=h.store.data.trades
 await h.tick(2999);assert.equal(h.calls.length,1)
 await h.tick(1);assert.equal(h.calls.length,2);assert.equal(h.store.isRefreshing,false)
 assert.equal(h.store.data.trades,trades)
 await h.tick(3000);assert.equal(h.calls.length,3)
})

test('initial slow load does not set a manual refresh indicator or invent account values',async t=>{
 const h=harness(t),wait=deferred();h.setResponse(()=>wait.promise)
 h.store.startPolling(3000);await flush()
 assert.equal(h.store.data,null);assert.equal(h.store.isRefreshing,false)
 await h.tick(6000);assert.equal(h.calls.length,1)
 wait.resolve(reply(good()));await flush()
 assert.equal(h.store.account.total_eq,1000);assert.equal(h.store.isRefreshing,false)
})

test('transient and prolonged errors retain the last valid data; recovery restores health',async t=>{
 const h=harness(t);await h.store.fetchDashboard(true)
 const previous=h.store.data,updated=h.store.lastUpdated
 h.setResponse(async()=>({ok:false,status:503,statusText:'Unavailable'}))
 await h.store.fetchDashboard(true)
 assert.equal(h.store.data,previous);assert.equal(h.store.lastUpdated,updated)
 assert.equal(h.store.isConnected,false);assert.equal(h.store.isStale,true);assert.equal(h.store.isRefreshing,false)
 await h.tick(120000);await h.store.fetchDashboard(true)
 assert.equal(h.store.data,previous)
 h.setResponse(async()=>reply(good({account:{total_eq:1005,avail_eq:905}})))
 await h.store.fetchDashboard(true)
 assert.equal(h.store.account.total_eq,1005);assert.equal(h.store.error,null);assert.equal(h.store.isStale,false)
})

test('eight-second timeout clears pending work but leaves displayed data intact',async t=>{
 const h=harness(t);await h.store.fetchDashboard(true);const previous=h.store.data
 h.setResponse((_,{signal})=>new Promise((resolve,reject)=>signal.addEventListener('abort',()=>reject(new Error('timeout')),{once:true})))
 const waiting=h.store.fetchDashboard(true);await flush();await h.tick(8000);await waiting
 assert.equal(h.store.data,previous);assert.equal(h.store.isRefreshing,false);assert.ok(h.store.error)
 h.setResponse(async()=>reply(good()));await h.store.fetchDashboard(true);assert.equal(h.store.error,null)
})

test('same-account warmup preserves values, but a different account clears them',async t=>{
 const h=harness(t);await h.store.fetchDashboard(true);const old=h.store.data,updated=h.store.lastUpdated
 h.setResponse(async()=>reply(good({account:{},initializing:true,trades:[],positions_summary:{items:[]},data_health:{status:'OFFLINE',partial:true}})))
 await h.store.fetchDashboard(true)
 assert.equal(h.store.data.trades,old.trades);assert.equal(h.store.account.total_eq,1000)
 assert.equal(h.store.lastUpdated,updated);assert.equal(h.store.isStale,true)
 h.setResponse(async()=>reply(good({account_source_id:'demo:b',account:{},initializing:true,trades:[]})))
 await h.store.fetchDashboard(true)
 assert.equal(h.store.data.account_source_id,'demo:b');assert.deepEqual(h.store.data.trades,[])
 assert.equal(h.store.account.total_eq,undefined)
})

test('manual and automatic refresh share pending work without prematurely hiding the manual indicator',async t=>{
 const h=harness(t),wait=deferred();h.setResponse(()=>wait.promise)
 const one=h.store.fetchDashboard(),two=h.store.fetchDashboard(),silent=h.store.fetchDashboard(true)
 await flush();assert.equal(h.calls.length,1);assert.equal(h.store.isRefreshing,true)
 wait.resolve(reply(good()));await Promise.all([one,two,silent])
 assert.equal(h.store.isRefreshing,false)
})

test('late response after leaving the page cannot overwrite a newer generation',async t=>{
 const h=harness(t);await h.store.fetchDashboard(true)
 const wait=deferred();h.setResponse(()=>wait.promise)
 const old=h.store.fetchDashboard(true);await flush()
 h.store.stopPolling();assert.equal(h.calls.at(-1).options.signal.aborted,true)
 h.setResponse(async()=>reply(good({account_source_id:'demo:b',account:{total_eq:222,avail_eq:222}})))
 await h.store.fetchDashboard(true)
 wait.resolve(reply(good({account:{total_eq:999,avail_eq:999}})));await old
 assert.equal(h.store.data.account_source_id,'demo:b');assert.equal(h.store.account.total_eq,222)
})

test('hidden tabs do not poll and become visible with one shared refresh',async t=>{
 const h=harness(t);h.store.startPolling(3000);await flush()
 h.doc.visibilityState='hidden';await h.tick(9000);assert.equal(h.calls.length,1)
 h.doc.visibilityState='visible';h.doc.dispatchEvent(new Event('visibilitychange'));await flush()
 assert.equal(h.calls.length,2);assert.equal(h.store.isRefreshing,false)
})

test('malformed responses never clear the last complete snapshot',async t=>{
 const h=harness(t);await h.store.fetchDashboard(true);const previous=h.store.data
 for(const bad of [null,[],{}, {account:'bad'}, {account:[]}]){
  h.setResponse(async()=>reply(bad));await h.store.fetchDashboard(true)
  assert.equal(h.store.data,previous);assert.ok(h.store.error)
 }
})
