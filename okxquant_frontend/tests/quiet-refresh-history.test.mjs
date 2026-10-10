import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import ts from 'typescript'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as dates from '../src/utils/aiHistoryToday.ts'
import { monitorPresentation, dashboardIsStale } from '../src/utils/dashboardHealth.ts'
import * as sessions from '../src/utils/sessionResponse.ts'
const read=p=>readFileSync(new URL('../src/'+p,import.meta.url),'utf8')
test('routine staleness stays quiet without claiming fresh data',()=>{
 const data={account:{total_eq:1000},data_health:{status:'STALE',partial:true,cache_age_seconds:20,errors:['ledger_sync: delayed']}}
 const out=monitorPresentation(data,null,true,0)
 assert.equal(out.label,'数据状态');assert.equal(out.tone,'neutral');assert.equal(out.critical,false)
 assert.equal(dashboardIsStale(data),true);assert.match(out.detail,/延迟/)
})
test('persistent outages and the real circuit remain visible',()=>{
 const data={account:{total_eq:1000},data_health:{cache_age_seconds:30}}
 assert.equal(monitorPresentation(data,Error('offline'),true,4).critical,true)
 assert.equal(monitorPresentation({...data,data_health:{cache_age_seconds:121}},null,true).critical,true)
 assert.equal(monitorPresentation({...data,risk_status:{daily_blocked:true}},null,false).label,'日内熔断已触发')
 assert.equal(monitorPresentation(null,null,false).critical,false)
 assert.equal(monitorPresentation({account:{total_eq:false}},null,true).critical,true)
})
test('history day is Shanghai, including UTC timestamps and midnight rollover',()=>{
 const now=Date.parse('2026-10-10T00:00:00Z')
 const rows=[{time:'2026-10-09 23:59:59',id:'old'},{time:'2026-10-09T16:00:01Z',id:'new'},{time:'2026-10-10 08:00:00',id:'now'},{time:'broken',id:'bad'}]
 assert.deepEqual(dates.todayHistory(rows,now).map(x=>x.id),['new','now'])
 assert.equal(dates.todayHistory(rows,Date.parse('2026-10-10T16:00:00Z')).length,0)
 assert.equal(dates.historyTime(rows[1]),'2026-10-10 00:00:01')
 assert.equal(dates.historyDay({time:Date.parse('2026-10-09T16:00:01Z')/1000}),'2026-10-10')
})
function authHarness(){
 let clock=1000;const requests=[];const saved=new Map([[sessions.SESSION_TOKEN_KEY,'session-a'],[sessions.SESSION_USER_KEY,JSON.stringify({id:999,role:'superadmin',username:'unverified'})]])
 const storage={getItem:k=>saved.get(k)||null,setItem:(k,v)=>saved.set(k,v),removeItem:k=>saved.delete(k)}
 const code=ts.transpileModule(read('stores/auth.ts'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={};new Function('require','exports','fetch','localStorage','Date',code)(name=>{
  if(name==='vue')return Vue
  if(name==='pinia')return {defineStore:(_id,setup)=>()=>setup()}
  if(name.includes('useFeedback'))return {useToast:()=>({error:()=>{}})}
  if(name.includes('sessionResponse'))return {...sessions,registerSessionExpireCallback:()=>{}}
  throw Error(name)
 },exports,(path,options)=>new Promise((resolve,reject)=>requests.push({path,options,resolve,reject})),storage,{now:()=>clock})
 return {state:exports.useAuthStore(),requests,saved,advance:ms=>{clock+=ms}}
}
const verified=()=>({ok:true,status:200,json:async()=>({user:{id:1,username:'verified',role:'admin'}})})
test('first navigation validates server identity; rapid subsequent navigation reuses only verified state',async()=>{
 const h=authHarness();const first=h.state.restoreSession();assert.equal(h.requests.length,1)
 h.requests[0].resolve(verified());assert.equal(await first,true);assert.equal(h.state.user.value.role,'admin')
 for(let i=0;i<5;i++)assert.equal(await h.state.restoreSession(),true)
 assert.equal(h.requests.length,1)
 h.advance(15001);const expired=h.state.restoreSession();assert.equal(h.requests.length,2);h.requests[1].resolve(verified());assert.equal(await expired,true)
})
test('session replacement and logout invalidate the navigation cache',async()=>{
 const h=authHarness();const first=h.state.restoreSession();h.requests[0].resolve(verified());await first
 h.saved.set(sessions.SESSION_TOKEN_KEY,'session-b');const changed=h.state.restoreSession();assert.equal(h.requests.length,2)
 assert.equal(h.requests[1].options.headers['X-OKXQuant-Session'],'session-b');h.requests[1].resolve(verified());await changed
 h.state.logout(false);assert.equal(await h.state.restoreSession(),false)
})
test('an expired server session is not cached as authenticated',async()=>{
 const h=authHarness();const first=h.state.restoreSession();h.requests[0].resolve({ok:false,status:401,json:async()=>({})})
 assert.equal(await first,false);assert.equal(h.state.isAuthenticated.value,false);assert.equal(await h.state.restoreSession(),false)
})
function historyHarness(){
 const requests=[];const unmount=[];let token='session-a';const store=Vue.reactive({data:{account_source_id:'scope-a',ai_brain_history:[]}})
 const {descriptor}=parse(read('components/AiBrainHistory.vue'))
 const code=ts.transpileModule(compileScript(descriptor,{id:'today-history'}).content,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText
 const exports={};new Function('require','exports','fetch','document',code)(name=>{
  if(name==='vue')return {...Vue,onMounted:()=>{},onUnmounted:fn=>unmount.push(fn)}
  if(name.includes('stores/dashboard'))return {useDashboardStore:()=>store}
  if(name.includes('sessionResponse'))return {getSessionToken:()=>token,buildAuthHeaders:()=>({}),handleSessionResponse:()=>{}}
  if(name.includes('aiHistoryToday'))return dates
  if(name==='lucide-vue-next')return {Brain:{},ChevronDown:{},Users:{}}
  if(name.endsWith('.vue'))return {__esModule:true,default:{}}
  throw Error(name)
 },exports,(path,options)=>new Promise((resolve,reject)=>requests.push({path,options,resolve,reject})),{visibilityState:'visible',addEventListener:()=>{},removeEventListener:()=>{}})
 const scope=Vue.effectScope();const state=scope.run(()=>exports.default.setup({}, {expose:()=>{}}))
 return {state,store,requests,setToken:v=>{token=v},close(){unmount.forEach(fn=>fn());scope.stop()}}
}
const historyRow=id=>({history_id:id,time:dates.shanghaiDay()+' 01:00:00'})
const listResponse=(items,scope='scope-a')=>({ok:true,status:200,json:async()=>({items,account_source_id:scope})})
test('today list renders more than 24 items with no visible history pagination',async()=>{
 const h=historyHarness();try{const task=h.state.fetchServerHistory();assert.match(h.requests[0].path,/today_only=true/)
 h.requests[0].resolve(listResponse(Array.from({length:40},(_,i)=>historyRow(String(i)))));await task
 assert.equal(h.state.history.value.length,40);assert.doesNotMatch(read('components/AiBrainHistory.vue'),/加载更多历史记录|handleLoadMore/)
 }finally{h.close()}
})
test('failed background history refresh retains the successful list',async()=>{
 const h=historyHarness();try{let task=h.state.fetchServerHistory();h.requests[0].resolve(listResponse([historyRow('a')]));await task
 task=h.state.fetchServerHistory();h.requests[1].reject(Error('offline'));await task;assert.equal(h.state.history.value[0].history_id,'a')
 }finally{h.close()}
})
test('obsolete-session or wrong-account history cannot be published',async()=>{
 const h=historyHarness();try{let task=h.state.fetchServerHistory();h.setToken('session-b');h.requests[0].resolve(listResponse([historyRow('old')]));await task
 assert.equal(h.state.history.value.length,0)
 task=h.state.fetchServerHistory();h.requests[1].resolve(listResponse([historyRow('wrong')],'scope-b'));await task;assert.equal(h.state.history.value.length,0)
 }finally{h.close()}
})
test('detail requests survive list refresh without losing their row or leaving a spinner',async()=>{
 const h=historyHarness();try{let task=h.state.fetchServerHistory();h.requests[0].resolve(listResponse([historyRow('a')]));await task
 const item=h.state.history.value[0];const detail=h.state.toggle(item);const refresh=h.state.fetchServerHistory();h.requests[2].resolve(listResponse([historyRow('a')]));await refresh
 h.requests[1].resolve({ok:true,status:200,json:async()=>({...historyRow('a'),account_scope:'scope-a',macro_assessment:'verified detail'})});await detail
 assert.equal(h.state.history.value[0],item);assert.equal(item.details_loaded,true);assert.equal(h.state.loadingDetails.value.size,0)
 }finally{h.close()}
})
