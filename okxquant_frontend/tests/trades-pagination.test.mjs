import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as settlement from '../src/utils/tradeSettlement.ts'
import * as duration from '../src/utils/tradeDuration.ts'
import * as observation from '../src/utils/observationDisplay.ts'
import * as fees from '../src/utils/feeAccounting.ts'

const source=readFileSync(new URL('../src/components/TradesLedger.vue',import.meta.url),'utf8')
const {descriptor,errors}=parse(source)
assert.deepEqual(errors,[])
const script=compileScript(descriptor,{id:'ledger-pagination-test'})
const js=ts.transpileModule(script.content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
function harness() {
 const requests=[]; const authErrors=[]; const unmount=[]
 const store=Vue.reactive({data:{account_source_id:'scope-a',trades:[]}})
 const exports={}
 new Function('require','exports','fetch',js)(name=>{
  if(name==='vue')return {...Vue,onMounted:()=>{},onUnmounted:fn=>unmount.push(fn)}
  if(name.includes('stores/dashboard'))return {useDashboardStore:()=>store}
  if(name.includes('sessionResponse'))return {getSessionToken:()=> 'test-session',buildAuthHeaders:()=>({}),handleSessionResponse:status=>authErrors.push(status)}
  if(name.includes('tradeSettlement'))return settlement
  if(name.includes('tradeDuration'))return duration
  if(name.includes('observationDisplay'))return observation
  if(name.includes('feeAccounting'))return fees
  if(name==='lucide-vue-next')return {Receipt:{},Search:{}}
  if(name.endsWith('.vue'))return {__esModule:true,default:{}}
  throw Error('Unexpected dependency: '+name)
 },exports,(url,options)=>new Promise((resolve,reject)=>requests.push({url,options,resolve,reject})))
 const scope=Vue.effectScope()
 const state=scope.run(()=>exports.default.setup({}, {expose:()=>{}}))
 return {state,store,requests,authErrors,cleanup(){unmount.forEach(fn=>fn());scope.stop()}}
}
const ok=(items,total=65)=>({status:200,ok:true,json:async()=>({items,total,counts:{all:total,active:1,closed:total-1}})})
const row=id=>({id,inst:id,status:'closed',net_pnl:1})

test('page and rows commit together; pending pagination preserves prior rows and height',async()=>{
 const h=harness();try{
  const first=h.state.fetchPage(0);h.requests[0].resolve(ok([row('first')]));await first
  const scroller={scrollTop:320}
  h.state.ledgerFrame.value={getBoundingClientRect:()=>({height:580}),querySelector:()=>scroller}
  const next=h.state.fetchPage(30)
  assert.equal(h.state.loadingTrades.value,true);assert.equal(h.state.offset.value,0)
  assert.equal(h.state.requestedOffset.value,30);assert.equal(h.state.trades.value[0].id,'first')
  assert.equal(h.state.frameMinHeight.value,580)
  h.requests[1].resolve(ok([row('second')]));await next
  assert.equal(h.state.offset.value,30);assert.equal(h.state.trades.value[0].id,'second')
  assert.equal(h.state.loadingTrades.value,false);assert.equal(scroller.scrollTop,0)
 }finally{h.cleanup()}
})

test('failed next page leaves committed page unchanged and retry targets the failed page',async()=>{
 const h=harness();try{
  const first=h.state.fetchPage(0);h.requests[0].resolve(ok([row('first')]));await first
  const next=h.state.fetchPage(30);h.requests[1].reject(Error('offline'));await next
  assert.equal(h.state.offset.value,0);assert.equal(h.state.trades.value[0].id,'first')
  assert.equal(h.state.requestedOffset.value,30);assert.equal(h.state.loadingTrades.value,false)
  assert.match(h.state.tradesError.value,/offline/)
  const retry=h.state.fetchPage(h.state.requestedOffset.value)
  assert.match(h.requests[2].url,/offset=30/)
  h.requests[2].resolve(ok([row('second')]));await retry
  assert.equal(h.state.offset.value,30)
 }finally{h.cleanup()}
})

test('stale unauthorized response cannot invalidate the newer request or clear its loading state',async()=>{
 const h=harness();try{
  const older=h.state.fetchPage(0);const newer=h.state.fetchPage(30)
  assert.equal(h.requests[0].options.signal.aborted,true)
  h.requests[0].resolve({status:401,ok:false});await older
  assert.deepEqual(h.authErrors,[]);assert.equal(h.state.loadingTrades.value,true)
  h.requests[1].resolve(ok([row('new')]));await newer
  assert.equal(h.state.trades.value[0].id,'new')
 }finally{h.cleanup()}
})

test('account switch clears previous rows, counts and fee dialog before waiting for its response',async()=>{
 const h=harness();try{
  const first=h.state.fetchPage(0);h.requests[0].resolve(ok([row('private-a')]));await first
  const old=h.state.fetchPage(30)
  h.state.feeDialogOpen.value=true;h.state.feeTrade.value=row('private-a')
  h.store.data.account_source_id='scope-b';h.store.data.trades=[row('old-store-fallback')]
  await Vue.nextTick()
  assert.deepEqual(h.state.trades.value,[]);assert.equal(h.state.serverTotal.value,null)
  assert.equal(h.state.serverCounts.value,null);assert.equal(h.state.holdingCount.value,null);assert.equal(h.state.closedCount.value,null);assert.equal(h.state.feeDialogOpen.value,false)
  h.requests[1].resolve(ok([row('late-a')]));await old
  assert.deepEqual(h.state.trades.value,[])
  h.requests[2].resolve(ok([row('scope-b')]));await Promise.resolve();await Vue.nextTick();await Promise.resolve()
  assert.equal(h.state.trades.value[0].id,'scope-b')
 }finally{h.cleanup()}
})

test('malformed response is an error rather than a successful empty page',async()=>{
 const h=harness();try{
  const p=h.state.fetchPage(30)
  h.requests[0].resolve({status:200,ok:true,json:async()=>({items:null,total:50})});await p
  assert.equal(h.state.offset.value,0);assert.match(h.state.tradesError.value,/格式无效/)
 }finally{h.cleanup()}
})

test('late response after unmount cannot publish data',async()=>{
 const h=harness();const p=h.state.fetchPage(0);h.cleanup()
 h.requests[0].resolve(ok([row('late')]));await p
 assert.equal(h.state.serverTrades.value,null)
})

test('loading feedback is accessible, blocks stale row interactions and respects reduced motion',()=>{
 assert.match(source,/:aria-busy="loadingTrades"/)
 assert.match(source,/:inert="loadingTrades"/)
 assert.match(source,/role="status" aria-live="polite"/)
 assert.match(source,/prefers-reduced-motion: reduce/)
 assert.match(source,/仍显示上次成功载入的记录/)
 assert.match(source,/<tbody :key="pageRevision"/)
})

test('a page removed by concurrent reconciliation returns to a valid page without false ranges',async()=>{
 const h=harness();try{
  const first=h.state.fetchPage(0);h.requests[0].resolve(ok([row('first')]));await first
  const next=h.state.fetchPage(30);h.requests[1].resolve(ok([],10))
  await Promise.resolve();await Vue.nextTick();await Promise.resolve()
  assert.match(h.requests[2].url,/offset=0/)
  h.requests[2].resolve(ok([row('reconciled')],10));await next
  assert.equal(h.state.offset.value,0);assert.equal(h.state.serverTotal.value,10)
  assert.equal(h.state.trades.value[0].id,'reconciled')
 }finally{h.cleanup()}
})
