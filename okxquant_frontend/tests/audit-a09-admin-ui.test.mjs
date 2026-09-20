import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync, readdirSync } from 'node:fs'
import * as Vue from 'vue'
import ts from 'typescript'
import { parse, compileScript } from '@vue/compiler-sfc'
import * as promptApi from '../src/api/adminPrompt.ts'

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const noop = () => {}
// Execute the actual page scripts with in-memory stores/API only. No network or secrets.
function evaluate(source, environment) {
  let code = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ESNext } }).outputText
  let ast = ts.createSourceFile('fixture.js', code, ts.ScriptTarget.Latest, true)
  const imports = ast.statements.filter(ts.isImportDeclaration)
  for (const node of [...imports].reverse()) code = code.slice(0, node.getStart(ast)) + code.slice(node.end)
  code = code.replace(/^export\s+/gm, '')
  ast = ts.createSourceFile('fixture.js', code, ts.ScriptTarget.Latest, true)
  const names = []
  const binding = node => {
    if (ts.isIdentifier(node)) names.push(node.text)
    else for (const element of node.elements || []) if (element.name) binding(element.name)
  }
  for (const node of ast.statements) {
    if (ts.isVariableStatement(node)) for (const declaration of node.declarationList.declarations) binding(declaration.name)
    if (ts.isFunctionDeclaration(node) && node.name) names.push(node.name.text)
  }
  for (const node of imports) {
    if (node.importClause?.name && !(node.importClause.name.text in environment)) environment[node.importClause.name.text] = {}
    for (const item of node.importClause?.namedBindings?.elements || []) if (!(item.name.text in environment)) environment[item.name.text] = {}
  }
  const keys = [...new Set(imports.flatMap(node => [node.importClause?.name?.text, ...(node.importClause?.namedBindings?.elements || []).map(item => item.name.text)]).filter(Boolean))]
  return new Function(...keys, code + '\nreturn {' + [...new Set(names)].join(',') + '}')(...keys.map(key => environment[key]))
}
function page(name, overrides = {}) {
  const calls = [], messages = [], lifecycle = {}, auth = { isSuperadmin: true, user: { id: 7, username: 'tester', role: 'superadmin' }, logout: noop, ...overrides.auth }
  const toast = Object.fromEntries(['error','warning','success','info'].map(tone => [tone, text => messages.push({ tone, text })]))
  const env = {
    ...Vue, ...promptApi,
    onMounted: fn => { lifecycle.mounted = fn }, onUnmounted: fn => { lifecycle.unmounted = fn }, onBeforeRouteLeave: fn => { lifecycle.leave = fn },
    useToast: () => toast, useFeedback: () => Vue.ref(null), useErrorFeedback: noop,
    useAuthStore: () => auth,
    useDialogs: () => ({ confirm: overrides.confirm || (async () => true), prompt: overrides.prompt || (async () => 'RUN EVOLUTION') }),
    useRouter: () => ({ replace: overrides.replace || noop, push: noop }),
    useApi: () => ({ api: async (path, options = {}) => { calls.push({ path, ...options, data: options.body ? JSON.parse(options.body) : undefined }); return overrides.api ? overrides.api(path, options) : {} } }),
    readableLog: value => value,
    backupConfiguration: () => ({}), backupLatest: () => ({}),
  }
  const { useApiAction } = evaluate(read('composables/useApiAction.ts'), { ...env })
  env.useApiAction = useApiAction
  const source = read('views/admin/' + name + '.vue').split('<script setup lang="ts">')[1].split('</script>')[0]
  const state = evaluate(source, env)
  if (state.loading) state.loading.value = false
  return { ...state, calls, messages, lifecycle, auth }
}
function fixture() {
  const pipelines = Object.fromEntries(['trading_system','trading_user','evolution_system','evolution_user'].map(key => [key, [{ id:key, title:key, content:key, enabled:true }]]))
  const profile = { id:'custom-a',name:'A',enabled:true,execution_profile:'standard',execution_settings:{id:'standard'},pipelines:structuredClone(pipelines),pipeline_views:pipelines }
  return { active_profile_id:'custom-a', profiles:[profile,{id:'small300',name:'300',execution_profile:'small300',execution_settings:{id:'small300',equity_cap_usdt:412,per_trade_equity_pct:0.013,max_leverage:4.2},pipeline_views:pipelines}] }
}
async function studio(overrides = {}) {
  const lib = fixture()
  const state = page('PromptStudioPage', { ...overrides, api: overrides.api || (async path => path === '/api/v1/admin/prompt-library' ? structuredClone(lib) : {}) })
  await state.loadLib()
  state.calls.length = 0
  return state
}

test('execution summary uses arbitrary server values, preserves zero and never invents limits', () => {
  const text = promptApi.executionSummary({id:'small300',equity_cap_usdt:412,per_trade_equity_pct:0.013,single_asset_margin_usdt:0,max_leverage:4.2})
  assert.match(text,/412U/); assert.match(text,/1.3%/); assert.match(text,/0U/); assert.match(text,/4.2 倍/)
  assert.doesNotMatch(text,/30U|90U|0.5%/)
  assert.equal(promptApi.executionSummary(undefined),'执行参数尚未加载')
  assert.equal(promptApi.executionSummary({id:'small300',max_leverage:NaN}),'执行参数尚未加载')
})
test('explicit execution binding wins over a stale settings display', () => {
  assert.equal(promptApi.executionBinding({execution_profile:'standard',execution_settings:{id:'small300'}}),'standard')
  assert.equal(promptApi.executionBinding({execution_settings:{id:'small300'}}),'small300')
})
test('atomic new-strategy package carries an explicit binding and does not change prompt text', () => {
  const source = {format:'okxquant-prompt-profile',version:3,profile:{name:'source',execution_profile:'small300',trading_system:'keep',pipelines:{x:[1]}}}
  const result = promptApi.newProfilePackage(source,' New ','standard')
  assert.equal(result.profile.execution_profile,'standard'); assert.equal(result.profile.name,'New'); assert.equal(result.profile.trading_system,'keep')
  assert.equal(source.profile.execution_profile,'small300'); assert.deepEqual(result.profile.pipelines,source.profile.pipelines)
})
test('page action lock covers confirmation, prevents second writes and releases after rejection', async () => {
  let release, count = 0
  const env = {...Vue,useAuthStore:()=>({isSuperadmin:true}),useToast:()=>({warning:noop})}
  const { useApiAction } = evaluate(read('composables/useApiAction.ts'),env)
  const {action,actionBusy} = useApiAction()
  const run = action(async()=>{count++;await new Promise(resolve=>{release=resolve});throw new Error('failure')})
  const first = run(); assert.equal(actionBusy.value,true); await run(); assert.equal(count,1)
  release(); await assert.rejects(first,/failure/); assert.equal(actionBusy.value,false)
})
test('action permission and unresolved initial load block writes without changing strategy state', async () => {
  let count = 0, blocked = true
  const env = {...Vue,useAuthStore:()=>({isSuperadmin:false}),useToast:()=>({warning:noop})}
  const {useApiAction} = evaluate(read('composables/useApiAction.ts'),env)
  const {action} = useApiAction(()=>blocked)
  await action(async()=>{count++},false)(); blocked=false
  await action(async()=>{count++})(); assert.equal(count,0)
  await action(async()=>{count++},false)(); assert.equal(count,1)
})
test('prompt save preserves evolution pipelines, omits execution binding and preserves its unsaved selector', async () => {
  const s = await studio(); s.dirty.value=true; s.selectedExecution.value='small300'; s.workingModules.value[0].content='edited'
  await s.saveProfile(); const put=s.calls.find(x=>x.method==='PUT')
  assert.equal(put.data.pipelines.trading_system[0].content,'edited')
  assert.equal(put.data.pipelines.evolution_system[0].content,'evolution_system')
  assert.equal(put.data.pipelines.evolution_user[0].content,'evolution_user')
  assert.equal('execution_profile' in put.data,false); assert.equal(s.selectedExecution.value,'small300')
})
test('execution save sends only name and binding, leaves unsaved prompt text intact', async () => {
  const s=await studio(); s.selectedExecution.value='small300'; s.workingModules.value[0].content='draft'; s.dirty.value=true
  await s.saveExecutionProfile(); const put=s.calls.find(x=>x.method==='PUT')
  assert.deepEqual(put.data,{name:'A',execution_profile:'small300'})
  assert.equal(s.workingModules.value[0].content,'draft'); assert.equal(s.dirty.value,true)
})
test('new strategy uses exactly one atomic write with explicit execution and never activates', async () => {
  const lib=fixture()
  const s=await studio({api:async path=>{
    if(path.endsWith('/stable/export'))return {format:'okxquant-prompt-profile',version:3,profile:{trading_system:'template',execution_profile:'small300'}}
    if(path.endsWith('/import'))return {profile:{id:'new',name:'New'}}
    return structuredClone(lib)
  }})
  s.newProfileName.value='New'; s.newExecutionProfile.value='standard'; await s.createProfile()
  const writes=s.calls.filter(x=>x.method==='POST'); assert.equal(writes.length,1)
  assert.equal(writes[0].path,'/api/v1/admin/prompt-profiles/import');assert.equal(writes[0].data.payload.profile.execution_profile,'standard')
  assert.equal(writes[0].data.payload.profile.trading_system,'template');assert.ok(!s.calls.some(x=>x.path.endsWith('/activate')))
})
test('activation refuses unsaved prompt or execution changes', async()=>{
  const s=await studio();s.dirty.value=true;await s.activateProfile();assert.equal(s.calls.length,0)
  s.dirty.value=false;s.selectedExecution.value='small300';await s.activateProfile();assert.equal(s.calls.length,0)
})
test('activation confirmation uses actual execution settings and next-fresh-decision semantics',async()=>{
  let confirmation='';const s=await studio({confirm:async text=>{confirmation=text;return true}})
  await s.selectProfile('small300');await s.activateProfile()
  assert.match(confirmation,/412U/);assert.match(confirmation,/1.3%/);assert.match(confirmation,/下一次新决策/);assert.match(confirmation,/旧仓保护保持/)
  assert.equal(s.calls.filter(x=>x.method==='POST').length,1)
})
test('selecting the current profile or pipeline never clears its draft',async()=>{
  const s=await studio();s.workingModules.value[0].content='draft';s.dirty.value=true
  await s.selectProfile(s.selectedProfileId.value);await s.switchPipeline(s.activePipeline.value)
  assert.equal(s.workingModules.value[0].content,'draft');assert.equal(s.dirty.value,true)
})
test('strategy load failures expose retry state, retry restores the actual active binding',async()=>{
  let fail=true;const lib=fixture();lib.active_profile_id='small300'
  const s=page('PromptStudioPage',{api:async()=>{if(fail)throw new Error('offline');return lib}})
  await s.loadLib();assert.equal(s.loadFailed.value,true);assert.equal(s.loading.value,false)
  fail=false;await s.loadLib(true);assert.equal(s.loadFailed.value,false);assert.equal(s.selectedExecution.value,'small300')
})
test('failed history load does not display a previous strategy revision list',async()=>{
  const s=page('PromptStudioPage',{api:async()=>{throw new Error('offline')}});s.historyList.value=[{id:'old'}]
  await s.showHistory();assert.deepEqual(s.historyList.value,[]);assert.equal(s.historyError.value,'offline');assert.equal(s.historyLoading.value,false)
})
test('normal admin cannot send prompt mutations even by invoking handlers directly',async()=>{
  const s=await studio({auth:{isSuperadmin:false}});await s.saveProfile();await s.createProfile();assert.equal(s.calls.length,0)
})
test('LLM remote import auto-activation includes provider identity for duplicate model ids',async()=>{
  const s=page('LlmPage',{api:async path=>path.endsWith('/models')?{providers:[]}: {}})
  s.selectedProvider.value={id:'provider-b',name:'B'}
  await s.importRemoteModel({id:'same-model'},true)
  assert.equal(s.calls.find(x=>x.path.endsWith('/activate')).data.provider_id,'provider-b')
})
test('notification toggle preserves other channel drafts and unsaved schedule without a full reload',async()=>{
  const s=page('NotifyPage');s.config.value={qq:{enabled:false},telegram:{chat_id:'draft'},_briefingTimes:'09:31'}
  await s.toggleChannel('qq',true);assert.equal(s.config.value.qq.enabled,true)
  assert.equal(s.config.value.telegram.chat_id,'draft');assert.equal(s.config.value._briefingTimes,'09:31');assert.equal(s.calls.length,1)
})
test('failed notification toggle rolls back optimistic state but keeps drafts',async()=>{
  const s=page('NotifyPage',{api:async()=>{throw new Error('failed')}});s.config.value={qq:{enabled:false},telegram:{chat_id:'draft'}}
  await s.toggleChannel('qq',true);assert.equal(s.config.value.qq.enabled,false);assert.equal(s.config.value.telegram.chat_id,'draft');assert.equal(s.calls.length,1)
})
test('notification save clears submitted secrets without overwriting unsaved schedule',async()=>{
  const s=page('NotifyPage');s.config.value={qq:{_secret:'test-fake'},telegram:{_token:'test-fake'},wechat:{},webhook:{},_briefingTimes:'08:32'}
  await s.saveAll();assert.equal(s.config.value.qq._secret,'');assert.equal(s.config.value.telegram._token,'');assert.equal(s.config.value._briefingTimes,'08:32')
})
test('QQ-only refresh preserves other drafts and canonicalizes only its own channel',async()=>{
  const s=page('NotifyPage',{api:async path=>path.endsWith('/schedule')?{briefing_times:['09:00']}:{qq:{openid:'new'},telegram:{chat_id:'server'}}})
  s.config.value={qq:{},telegram:{chat_id:'draft'},_briefingTimes:'08:32'};await s.loadConfig(true,'qq')
  assert.equal(s.config.value.qq.openid,'new');assert.equal(s.config.value.telegram.chat_id,'draft');assert.equal(s.config.value._briefingTimes,'08:32')
})
test('normal admins change their own nonzero user id and are logged out after password rotation',async()=>{
  let loggedOut=0,route='';const s=page('AdminSysPage',{auth:{isSuperadmin:false,user:{id:19},logout:()=>loggedOut++},replace:value=>{route=value}})
  await s.load();assert.equal(s.pwdUserId.value,19);assert.equal(s.calls.length,0)
  s.newPassword.value='fake-long-password';s.currentPassword.value='fake-current';await s.changePassword()
  assert.equal(s.calls[0].path,'/api/v1/admin/users/19/password');assert.equal(loggedOut,1);assert.equal(route,'/admin/login')
})
test('capital baseline rejects invalid numeric strings rather than truncating them',async()=>{
  const s=page('SecurityPage');s.capitalConfirm.value='UPDATE CAPITAL'
  for(const value of ['123oops','Infinity','-1','0']){s.newCapital.value=value;await s.saveCapital()}
  assert.equal(s.calls.length,0)
})
test('log tab response arriving late cannot replace the selected log',async()=>{
  let finish;const s=page('DecisionsPage',{api:async path=>path.includes('source=trader')?await new Promise(resolve=>{finish=resolve}):{content:'backend new'}})
  const first=s.fetchLogStream('trader');await s.fetchLogStream('backend');finish({content:'trader old'});await first
  assert.equal(s.activeLogTab.value,'backend');assert.equal(s.logContent.value,'backend new')
})
test('evolution edit preserves all trading pipelines and explicitly selects module mode',async()=>{
  const lib=fixture(),s=page('EvolutionPage',{api:async path=>path.endsWith('/prompt-library')?lib:{}})
  s.lib.value=lib;s.selectedProfileId.value='custom-a';s.activeTab.value='evolution_system';s.workingModules.value=[{id:'review',content:'edited',enabled:true}]
  await s.savePipelineModules();const body=s.calls.find(x=>x.method==='PUT').data
  assert.equal(body.editor_mode,'modules');assert.equal(body.pipelines.trading_system[0].content,'trading_system');assert.equal(body.pipelines.evolution_user[0].content,'evolution_user')
})
test('evolution submits RUN EVOLUTION, reports queue receipt only, and never reloads prompt drafts',async()=>{
  const s=page('EvolutionPage',{api:async()=>({accepted:true,request_id:'fake-request',status:'queued',detail:'复盘已排队；不会自动发布运行记忆'})})
  s.workingModules.value=[{content:'draft'}];await s.triggerEvolutionNow()
  assert.equal(s.calls.length,1);assert.deepEqual(s.calls[0].data,{confirmation:'RUN EVOLUTION'})
  assert.equal(s.evolutionRequest.value.request_id,'fake-request');assert.match(s.bannerMsg.value.text,/已排队/);assert.doesNotMatch(s.bannerMsg.value.text,/已完成/)
  assert.equal(s.workingModules.value[0].content,'draft')
})
test('evolution query updates report only and leaves editable prompt modules intact',async()=>{
  const s=page('EvolutionPage',{api:async()=>({evolution_review:{status:'running'}})});s.workingModules.value=[{content:'draft'}]
  await s.refreshEvolutionStatus();assert.equal(s.calls[0].path,'/api/v1/admin/memory');assert.equal(s.workingModules.value[0].content,'draft')
})
test('router requires a successful server validation, not just a locally cached token',async()=>{
  let guard;const auth={restoreSession:async()=>false,isAuthenticated:true}
  const code=read('router/index.ts').replace('export default router','')
  evaluate(code,{useAuthStore:()=>auth,pageTitle:()=>'',createWebHistory:()=>({}),createRouter:()=>({beforeEach:fn=>{guard=fn},afterEach:noop})})
  assert.deepEqual(await guard({meta:{requiresAuth:true}}),{name:'admin-login'})
  assert.equal(await guard({name:'admin-login',meta:{}}),undefined)
  auth.restoreSession=async()=>true;assert.deepEqual(await guard({name:'admin-login',meta:{}}),{name:'admin-overview'})
})
test('all routed admin Vue files compile, and data pages retain explicit failure retry controls',()=>{
  const router=read('router/index.ts')
  for(const name of readdirSync(new URL('../src/views/admin/',import.meta.url)).filter(x=>x.endsWith('.vue'))){
    const source=read('views/admin/'+name),{descriptor,errors}=parse(source)
    assert.deepEqual(errors,[],name);compileScript(descriptor,{id:name,inlineTemplate:true})
    if(!['LegacyRedirect.vue','LoginPage.vue'].includes(name)){
      assert.ok(router.includes(name),name+' route missing')
      assert.match(source,name==='AccountsPage.vue'?/perform\(load\)/:/v-if="loadFailed"/,name+' retry missing')
    }
  }
})
test('old small300 prose and mismatched evolution confirmation cannot return',()=>{
  const studio=read('views/admin/PromptStudioPage.vue')
  assert.doesNotMatch(studio,/0\.5%|30U|90U|3x|最高3倍|≤3倍/)
  assert.match(studio,/executionSummary\(executionSettings\)/)
  assert.doesNotMatch(read('views/admin/EvolutionPage.vue'),/RUN JOB/)
})

test('council switch persists only the saved configuration and preserves role drafts',async()=>{
  const saved={enabled:false,consensus_mode:'weighted',timeout_seconds:60,roles:{cio:{prompt:'saved'}}}
  const s=page('CouncilPage',{api:async (_path,options)=>options.method==='PUT'?{config:{...saved,enabled:true}}:saved})
  s.councilConfig.value={...saved,roles:{cio:{prompt:'draft'}}}
  await s.toggleCouncil();const put=s.calls.find(x=>x.method==='PUT')
  assert.equal(put.data.enabled,true);assert.equal(put.data.roles.cio.prompt,'saved')
  assert.equal(s.councilConfig.value.roles.cio.prompt,'draft');assert.equal(s.councilConfig.value.enabled,true)
})
test('council switch failure leaves its visible enabled state unchanged',async()=>{
  const s=page('CouncilPage',{api:async()=>{throw new Error('offline')}});s.councilConfig.value.enabled=false
  await s.toggleCouncil();assert.equal(s.councilConfig.value.enabled,false);assert.match(s.bannerMsg.value.text,/切换失败/)
})
test('new invalid file selection cannot accidentally import the previous valid strategy',t=>{
  let reader
  const original=Object.getOwnPropertyDescriptor(globalThis,'FileReader')
  Object.defineProperty(globalThis,'FileReader',{configurable:true,value:class {constructor(){reader=this}readAsText(){}}})
  t.after(()=>{if(original)Object.defineProperty(globalThis,'FileReader',original);else delete globalThis.FileReader})
  const s=page('PromptStudioPage');s.importRawJson.value='{"old":true}'
  s.handleFileSelect({target:{files:[{name:'broken.json'}]}})
  reader.onload({target:{result:'{'}})
  assert.equal(s.importRawJson.value,'');assert.match(s.importFileError.value,/不是合法的 JSON/)
})

test('clipboard errors do not report successful copy',async t=>{
  const original=Object.getOwnPropertyDescriptor(navigator,'clipboard')
  Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async()=>{throw new Error('denied')}}})
  t.after(()=>{if(original)Object.defineProperty(navigator,'clipboard',original);else delete navigator.clipboard})
  const s=page('PromptStudioPage');await s.copyPreview();assert.equal(s.bannerMsg.value.type,'err');assert.match(s.bannerMsg.value.text,/复制失败/)
})
test('administrator preview does not pretend to contain current market substitutions',()=>{
  const source=read('views/admin/PromptStudioPage.vue')
  assert.doesNotMatch(source,/已代入当前真实盘口|实时渲染对照|实发效果/)
  assert.match(source,/未填入实时行情/);assert.match(source,/不代表实盘与模拟盘一致/)
})

test('all data-page initial load failures exit loading and expose persistent retry',async t=>{
  t.mock.method(console,'error',noop)
  const loaders={AboutPage:'loadAbout',AdminSysPage:'load',AgentsPage:'load',AuditPage:'load',BackupPage:'load',CouncilPage:'loadData',DecisionsPage:'loadDecisions',EvolutionPage:'loadData',GatewayPage:'load',InterceptorsPage:'loadPlugins',LlmPage:'loadConfig',NotifyPage:'loadConfig',OverviewPage:'loadRuntime',PluginsPage:'load',PromptStudioPage:'loadLib',SecurityPage:'loadAll'}
  for(const [name,loader] of Object.entries(loaders)){
    const s=page(name,{api:async()=>{throw new Error('offline')}})
    await s[loader]();assert.equal(s.loading.value,false,name);assert.equal(s.loadFailed.value,true,name)
  }
})
test('evolution same-tab and cancelled navigation preserve an unsaved review template',async()=>{
  const s=page('EvolutionPage',{confirm:async()=>false});s.activeTab.value='evolution_system';s.workingModules.value=[{content:'draft'}];s.dirty.value=true
  await s.switchTab('evolution_system');await s.switchTab('evolution_user')
  assert.equal(s.activeTab.value,'evolution_system');assert.equal(s.workingModules.value[0].content,'draft');assert.equal(await s.lifecycle.leave(),false)
})

test('minute engine authorization is blocked in DEMO even when handler is called directly',async()=>{
  const s=page('PromptStudioPage');s.engineStatus.value={environment:'demo',enabled:true,authorized:false,status:'ready'}
  await s.authorizeLiveEngine(true);await s.authorizeLiveEngine(false);assert.equal(s.calls.length,0)
})
test('LIVE minute engine authorization needs the exact explicit phrase and preserves requested boolean',async()=>{
  for(const enabled of [true,false]){
    const expected=enabled?'ENABLE LIVE SCALP':'DISABLE LIVE SCALP'
    const s=page('PromptStudioPage',{prompt:async()=>expected,api:async()=>({environment:'live',enabled,authorized:enabled,status:'ready'})})
    s.engineStatus.value={environment:'live',enabled:!enabled,authorized:!enabled,status:'confirmation_required'}
    await s.authorizeLiveEngine(enabled)
    assert.deepEqual(s.calls.find(x=>x.method==='POST').data,{enabled,confirmation:expected,expected_binding:{environment:'live',enabled:!enabled,authorized:!enabled,status:'confirmation_required'}})
    assert.equal(s.calls.find(x=>x.method==='POST').path,'/api/v1/admin/strategy/engine/live-authorization')
    assert.ok(s.calls.some(x=>x.path==='/api/v1/admin/strategy/engine'))
    assert.match(s.engineStatusText.value,/不代表已成交/)
  }
})
test('LIVE authorization cancellation, wrong phrase and normal admin do not send requests',async()=>{
  for(const input of [null,'ENABLE LIVE','enable live scalp']){
    const s=page('PromptStudioPage',{prompt:async()=>input});s.engineStatus.value={environment:'live'};await s.authorizeLiveEngine(true);assert.equal(s.calls.length,0)
  }
  const s=page('PromptStudioPage',{auth:{isSuperadmin:false},prompt:async()=>'ENABLE LIVE SCALP'});s.engineStatus.value={environment:'live'}
  await s.loadEngineStatus();await s.authorizeLiveEngine(true);assert.equal(s.calls.length,0)
})
test('engine lookup failure clears stale ready state and offers independent retry',async()=>{
  const s=page('PromptStudioPage',{api:async()=>{throw new Error('unavailable')}});s.engineStatus.value={environment:'live',status:'ready'}
  await s.loadEngineStatus();assert.equal(s.engineStatus.value,null);assert.equal(s.engineError.value,'unavailable');assert.equal(s.engineLoading.value,false)
})
