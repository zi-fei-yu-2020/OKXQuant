import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { syncBindingSelections, overviewConnection, canBind, connectionStateLabel, newsConnectionLabel, friendlyConnectionError } from '../src/utils/accountConnections.ts'
import { newsIsFresh, newsStatusText, newsTime } from '../src/utils/newsStatus.ts'

const connection={id:'fixture',label:'fixture',auth_type:'oauth',mode:'demo',site:'global',status:'identity_verified',capabilities:{demo:{account_uid:'A',read_ready:true},live:{account_uid:'A',read_ready:true,news_ready:true}}}
test('OAuth read success allows news only, not automatic trading',()=>{
 assert.equal(canBind(connection,'demo'),false)
 assert.equal(canBind(connection,'live'),false)
 assert.equal(canBind(connection,'news'),true)
 assert.equal(canBind({...connection,status:'authorization_pending'},'news'),false)
})
test('Key binding cannot cross account environments',()=>{
 const key={...connection,auth_type:'api_key'}
 assert.equal(canBind(key,'demo'),true)
 assert.equal(canBind(key,'live'),false)
})
test('news freshness requires successful source time, not a recent attempt',()=>{
 assert.equal(newsIsFresh({schema:2,connection_status:'fresh',last_success_at:1000},1100),true)
 assert.equal(newsIsFresh({schema:2,connection_status:'fresh',last_success_at:1000,last_attempt_at:3000},3000),false)
 assert.equal(newsIsFresh({schema:2,connection_status:'fresh',last_success_at:4000},3000),false)
 assert.equal(newsIsFresh({updated_at:'just now'},3000),false)
 assert.equal(newsTime(undefined),'--')
})
test('source failure is distinct from no market news',()=>{
 assert.ok(newsStatusText({schema:2,connection_status:'unavailable'}).includes('不表示市场没有新闻'))
 assert.equal(newsConnectionLabel('unconfigured'),'资讯连接未绑定')
 assert.equal(connectionStateLabel('unverified'),'待核验')
 assert.ok(friendlyConnectionError('oauth_account_changed').includes('已阻止'))
})
test('account center uses accessible component dialogs rather than browser alerts',()=>{
 const source=readFileSync(new URL('../src/views/admin/AccountsPage.vue',import.meta.url),'utf8')
 for(const token of ['AppDialog','AppField','grid-cols-1','items-start','type="password"','autocomplete="new-password"','clearSecrets']) assert.ok(source.includes(token))
 for(const token of ['window.alert','window.confirm','v-html','localStorage.setItem']) assert.ok(!source.includes(token))
})
test('news view does not invent mention counts or claim monitoring healthy on stale input',()=>{
 const source=readFileSync(new URL('../src/components/NewsIntelligence.vue',import.meta.url),'utf8')
 assert.ok(source.includes('<NewsConnectionStatus'))
 assert.ok(source.includes('资讯监测待恢复'))
 assert.ok(source.includes("s.mentions == null ? '--'"))
})

test('overview understands both old and canonical runtime contracts',()=>{
 assert.deepEqual(overviewConnection({credentials:{okx:true},configuration:{'OKX 当前环境':'模拟盘 DEMO'}}),{mode:'demo',configured:true,source:'api_key',status:'configured'})
 assert.equal(overviewConnection({credentials:{okx_configured:true,simulated_trading:false}}).mode,'live')
 assert.equal(overviewConnection({trading_connection:{mode:'demo',configured:false,status:'unbound',auth_type:'none'},credentials:{okx:true}}).configured,false)
 assert.equal(overviewConnection({}).mode,'unknown')
})
test('binding selectors initially show actual bindings and preserve unsaved choices',()=>{
 const initial={bindings:{demo:'A',live:'B',news:'B'},connections:[{id:'A'},{id:'B'},{id:'C'}]}
 const selected={demo:'',live:'',news:''}
 syncBindingSelections(selected,undefined,initial)
 assert.deepEqual(selected,{demo:'A',live:'B',news:'B'})
 selected.demo='C'
 syncBindingSelections(selected,initial,initial)
 assert.equal(selected.demo,'C')
 const next={...initial,bindings:{demo:'A',live:'C',news:null}}
 syncBindingSelections(selected,initial,next)
 assert.deepEqual(selected,{demo:'C',live:'C',news:''})
})
test('removed draft selection resets to current binding, never to an arbitrary account',()=>{
 const previous={bindings:{demo:'A',live:null,news:null},connections:[{id:'A'},{id:'C'}]}
 const selected={demo:'C',live:'',news:''}
 syncBindingSelections(selected,previous,{...previous,connections:[{id:'A'}]})
 assert.equal(selected.demo,'A')
})
test('account configuration has one UI entry; protected closing remains in instrument page',()=>{
 const page=readFileSync(new URL('../src/views/admin/SecurityPage.vue',import.meta.url),'utf8')
 for(const text of ['保存环境与凭证','保存平仓开关','startOauth','saveEnvironment','live_secret','demo_secret'])assert.ok(!page.includes(text))
 assert.ok(page.includes('confirmClose'))
 assert.ok(page.includes('saveCapital'))
 assert.ok(page.includes('/admin/accounts'))
 const accounts=readFileSync(new URL('../src/views/admin/AccountsPage.vue',import.meta.url),'utf8')
 for(const text of ['允许后台手动平仓','ENABLE MANUAL CLOSE','data-masked-credentials','配置文件中的旧凭据','检测 Node/npm/CLI'])assert.ok(accounts.includes(text))
})
