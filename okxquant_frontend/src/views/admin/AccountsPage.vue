<script setup lang="ts">
import { onMounted, reactive, ref, watch } from 'vue'
import { useToast } from '../../composables/useFeedback'
import { useApi } from '../../composables/useApi'
import AppCard from '../../components/ui/AppCard.vue'
import AppButton from '../../components/ui/AppButton.vue'
import AppDialog from '../../components/ui/AppDialog.vue'
import AppField from '../../components/ui/AppField.vue'
import { syncBindingSelections, canBind, connectionStateLabel, friendlyConnectionError, newsConnectionLabel } from '../../utils/accountConnections'
import type { AccountCenterState, AccountConnection, AccountMode, BindingPurpose } from '../../utils/accountConnections'

const { api }=useApi()
const state=ref<AccountCenterState>()
const busy=ref(false)
const error=ref('')
const notice=ref('')
const toast=useToast()
watch(error, text=>{if(text){toast.error(text);error.value=''}}, {flush:'sync'})
watch(notice, text=>{if(text){toast.info(text);notice.value=''}}, {flush:'sync'})
const manualClose=ref(false)
const cliCheck=ref<any>()
const addOpen=ref(false)
const authorization=ref<{ verification_uri:string; user_code:string; expires_in:number }>()
const authorizeOpen=ref(false)
const selected=reactive<Record<BindingPurpose,string>>({demo:'',live:'',news:''})
const form=reactive({label:'',auth_type:'oauth' as 'oauth'|'api_key',mode:'demo' as AccountMode,site:'global',api_key:'',secret_key:'',passphrase:''})
const confirm=ref<{title:string;description:string;phrase:string;path:string;method:string;connection_id?:string;enabled?:boolean;apiPath?:string}>()
const confirmOpen=ref(false)
const phrase=ref('')
const purposes: BindingPurpose[]=['demo','live','news']
const purposeName=(purpose: BindingPurpose)=>({demo:'模拟盘交易',live:'实盘交易',news:'市场资讯'}[purpose])
const probeNames:Record<string,string>={identity:'账户身份',balance:'余额',positions:'持仓',pending_orders:'在途挂单',protection_orders:'云端保护单',positions_history:'历史持仓',bills:'账单'}
const account=(identity: string|null|undefined)=>state.value?.connections.find(c=>c.id===identity)
const isBound=(identity:string)=>Object.values(state.value?.bindings || {}).includes(identity)
const bindingName=(purpose:BindingPurpose)=>account(state.value?.bindings[purpose])?.display_label || account(state.value?.bindings[purpose])?.label || (purpose!=='news'&&!state.value?.managed&&state.value?.legacy_key_configured[purpose]?'原有 Key 连接（兼容模式）':'未绑定')
async function load(){
 const next=await api<AccountCenterState>('/api/v1/admin/accounts')
 if (typeof next.manual_close_enabled !== 'boolean') {
  try { const cfg=await api('/api/v1/admin/config'); if(typeof cfg.editable?.manual_close_enabled==='boolean') next.manual_close_enabled=cfg.editable.manual_close_enabled } catch { /* Keep permission unknown; do not assume disabled. */ }
 }
 syncBindingSelections(selected,state.value,next)
 if (!state.value || manualClose.value===!!state.value.manual_close_enabled) manualClose.value=!!next.manual_close_enabled
 state.value=next
}
function canSaveSelection(purpose:BindingPurpose){
 const chosen=account(selected[purpose])
 return !!chosen && selected[purpose]!==state.value?.bindings[purpose] && canBind(chosen,purpose)
}
function askManualClose(){
 confirm.value={title:'更新后台手动平仓权限',description:'仅修改后台手动平仓的许可，不执行平仓，不影响独立持仓风控；实际平仓仍需原有密码、一次性令牌和确认步骤。',phrase:manualClose.value?'ENABLE MANUAL CLOSE':'DISABLE MANUAL CLOSE',path:'/manual-close',method:'PUT',enabled:manualClose.value}
 phrase.value='';confirmOpen.value=true
}
async function checkCli(){await perform(async()=>{cliCheck.value=await api('/api/v1/admin/okx/cli-check')})}
function askInstallCli(){
 confirm.value={title:'安装或更新 OKX CLI',description:'这是运行依赖维护，不会授权账户。仅超级管理员可操作，OAuth原生组件仍需另行检查。',phrase:'INSTALL OKX CLI',path:'',apiPath:'/api/v1/admin/okx/install-cli',method:'POST'}
 phrase.value='';confirmOpen.value=true
}
async function perform(fn:()=>Promise<void>){busy.value=true;error.value='';try{await fn()}catch(e:any){if(e?.silent)return;error.value=friendlyConnectionError(e.message || '操作失败')}finally{busy.value=false}}
function clearSecrets(){form.api_key='';form.secret_key='';form.passphrase=''}
async function create(){await perform(async()=>{
 const body={label:form.label,auth_type:form.auth_type,mode:form.mode,site:form.site,...(form.auth_type==='api_key'?{api_key:form.api_key,secret_key:form.secret_key,passphrase:form.passphrase}:{})}
 await api('/api/v1/admin/accounts/connections',{method:'POST',body:JSON.stringify(body)})
 clearSecrets();addOpen.value=false;await load();notice.value='连接已保存为候选，未切换交易账户。'
})}
async function importLegacy(mode:AccountMode){await perform(async()=>{await api('/api/v1/admin/accounts/import-legacy',{method:'POST',body:JSON.stringify({mode})});await load();notice.value='旧 Key 已复制到加密连接记录，当前交易账户没有改变。'})}
async function startOAuth(c:AccountConnection){await perform(async()=>{authorization.value=await api(`/api/v1/admin/accounts/connections/${c.id}/oauth/start`,{method:'POST'});authorizeOpen.value=true;await load()})}
async function probe(c:AccountConnection,mode:AccountMode){await perform(async()=>{await api(`/api/v1/admin/accounts/connections/${c.id}/probe`,{method:'POST',body:JSON.stringify({mode})});await load();notice.value='只读检查完成。读取通过不代表交易写入已验收，请查看能力明细。'})}
function ask(operation:'bind'|'unbind'|'activate'|'delete',purpose:BindingPurpose='news',connection_id=''){
 const values={bind:{title:'确认绑定或更换连接',description:'交易用途更换需要旧账户和新账户均无持仓、挂单及未确认请求。若更换当前环境的连接，将影响后续策略任务；本操作不直接提交订单。',phrase:'BIND '+purpose.toUpperCase(),path:`/bindings/${purpose}`,method:'PUT'},unbind:{title:'确认解除用途绑定',description:'解绑不是平仓，也不代表在 OKX 撤销授权。有持仓或挂单时会拒绝交易用途解绑，且不会回退到旧 Key。',phrase:'UNBIND '+purpose.toUpperCase(),path:`/bindings/${purpose}`,method:'DELETE'},activate:{title:'确认切换交易环境',description:'切换后，后续策略任务将使用目标环境。实盘可能产生真实损益；请确认目标账户、额度及策略配置。旧账户必须无持仓或挂单。',phrase:'ACTIVATE '+purpose.toUpperCase(),path:`/activate/${purpose}`,method:'POST'},delete:{title:'删除未绑定连接',description:'先解除所有用途绑定。删除本地连接不等于撤销 OKX 授权或交易所 API Key，请另外核对官方授权管理。',phrase:'DELETE CONNECTION',path:`/connections/${connection_id}`,method:'DELETE'}}
 confirm.value={...values[operation],connection_id};phrase.value='';confirmOpen.value=true
}
async function submitConfirm(){const value=confirm.value;if(!value||phrase.value!==value.phrase)return;await perform(async()=>{
 const result=await api(value.apiPath || '/api/v1/admin/accounts'+value.path,{method:value.method,body:JSON.stringify({confirmation:phrase.value,connection_id:value.connection_id,enabled:value.enabled})})
 confirmOpen.value=false;await load();notice.value=result.message || '账户用途配置已更新；未自动撤销官方授权。'
})}
async function refreshNews(){await perform(async()=>{const result=await api('/api/v1/admin/accounts/news/refresh',{method:'POST'});await load();notice.value=newsConnectionLabel(result.connection_status)})}
onMounted(()=>perform(load))
</script>

<template>
 <div class="space-y-4 min-w-0 max-w-[2160px] mx-auto" data-account-center>
  <header class="flex flex-wrap items-center justify-between gap-3">
   <div class="min-w-0"><h2 class="text-lg font-semibold" style="color:var(--text-main)">账户与资讯连接</h2><p class="mt-1 text-sm" style="color:var(--text-muted)">独立管理模拟盘、实盘与资讯用途。保存授权不自动切换交易；连接失败不自动尝试其他账户。</p></div>
   <div class="flex flex-wrap gap-2"><AppButton :loading="busy" @click="perform(load)">刷新状态</AppButton><AppButton variant="primary" :disabled="busy" @click="clearSecrets();addOpen=true">添加连接</AppButton></div>
  </header>
  <AppCard class="p-4 text-sm leading-relaxed"><p style="color:var(--text-main)">当前交易环境：<strong>{{ state?.active_mode==='live'?'实盘':state?.active_mode==='demo'?'模拟盘':'待确认' }}</strong> · {{ state?.managed?'账户中心管理':'兼容现有 Key 配置' }}</p><p class="mt-1" style="color:var(--text-muted)">OAuth 交易写入与保护闭环尚未完成真实验收，保留现有 Key 链路。资讯读取使用普通市场环境，不会把模拟盘交易切到实盘。</p></AppCard>
  <div class="grid grid-cols-1 xl:grid-cols-3 items-start gap-4 min-w-0">
   <AppCard v-for="purpose in purposes" :key="purpose" class="p-4 min-w-0 space-y-3" :data-account-purpose="purpose">
    <h3 class="font-semibold" style="color:var(--text-main)">{{ purposeName(purpose) }}</h3>
    <p class="text-sm break-words" style="color:var(--text-muted)">{{ bindingName(purpose) }}</p>
    <p v-if="purpose==='news'" class="text-xs leading-relaxed" style="color:var(--text-muted)">{{ newsConnectionLabel(state?.news?.connection_status) }}<br>全部资讯最近成功：{{ state?.news?.updated_at || '--' }}<br>自动采集：每 {{ (state?.news_interval_seconds || 600)/60 }} 分钟尝试一次，手动读取可提前更新。</p>
    <AppField :label="'选择'+purposeName(purpose)+'连接'" v-slot="field"><select :id="field.id" v-model="selected[purpose]" class="ui-input w-full min-w-0"><option value="">尚未绑定，请选择连接</option><option v-if="state?.bindings[purpose]&&!account(state.bindings[purpose])" :value="state.bindings[purpose] || ''" disabled>当前绑定记录不可用</option><option v-for="c in state?.connections || []" :key="c.id" :value="c.id" :disabled="!canBind(c,purpose)">{{ c.display_label || c.label }} · {{ c.auth_type==='oauth'?'OAuth':c.credentials_display?.api_key || 'Key' }}{{ canBind(c,purpose)?'':'（未通过能力检查）' }}</option></select></AppField>
    <p v-if="selected[purpose]&&selected[purpose]!==state?.bindings[purpose]" class="text-xs" style="color:var(--color-warn)">此选择尚未保存，当前绑定仍以上方账户为准。</p>
    <div class="flex flex-wrap gap-2"><AppButton size="sm" :disabled="busy||!canSaveSelection(purpose)" @click="ask('bind',purpose,selected[purpose])">绑定 / 更换</AppButton><AppButton size="sm" :disabled="busy||!state?.bindings[purpose]" @click="ask('unbind',purpose)">解绑</AppButton><AppButton v-if="purpose!=='news'&&state?.active_mode!==purpose" size="sm" variant="danger" :disabled="busy||!state?.bindings[purpose]" @click="ask('activate',purpose)">切换为当前环境</AppButton><AppButton v-if="purpose==='news'" size="sm" :disabled="busy||!state?.bindings.news" @click="refreshNews">读取资讯</AppButton></div>
    <AppButton v-if="purpose!=='news'&&state?.legacy_key_configured[purpose]&&!state?.managed" size="sm" :disabled="busy" @click="importLegacy(purpose)">导入旧 Key 为候选（不切换）</AppButton>
   </AppCard>
  </div>
  <AppCard class="p-4 space-y-3 min-w-0" data-manual-close-settings>
   <h3 class="font-semibold" style="color:var(--text-main)">后台手动平仓权限</h3>
   <label class="flex items-center gap-2 min-h-11 text-sm" style="color:var(--text-main)"><input v-model="manualClose" type="checkbox" :disabled="busy||typeof state?.manual_close_enabled!=='boolean'">允许后台手动平仓</label>
   <p class="text-xs" style="color:var(--text-muted)">当前已保存：{{ typeof state?.manual_close_enabled==='boolean'?(state.manual_close_enabled?'允许':'禁止'):'待获取' }}。修改开关不会执行平仓，也不关闭自动止损和独立风控。</p>
   <div class="flex flex-wrap gap-2"><AppButton :disabled="busy||typeof state?.manual_close_enabled!=='boolean'||manualClose===state.manual_close_enabled" @click="askManualClose">保存平仓开关</AppButton><router-link to="/admin/security" class="ui-button ui-button--secondary">查看标的与受保护平仓操作</router-link></div>
  </AppCard>
  <AppCard class="p-4 min-w-0">
   <details><summary class="cursor-pointer min-h-11 text-sm" style="color:var(--text-main)">配置文件中的旧凭据（脱敏、只读）</summary>
    <p class="text-xs mb-3" style="color:var(--text-muted)">当前生效连接以用途卡片中的已绑定账户为准。旧配置只用于核对或导入候选，不提供第二套环境编辑表单。</p>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3"><div v-for="mode in (['demo','live'] as const)" :key="mode" class="min-w-0 text-xs leading-relaxed" style="color:var(--text-muted);overflow-wrap:anywhere"><h4>{{ mode==='demo'?'模拟盘 DEMO':'实盘 LIVE' }}</h4><p>API Key：{{ state?.legacy_connections?.[mode]?.credentials_display.api_key || (state?.legacy_key_configured[mode]?'已配置，尚无脱敏值':'未配置') }}</p><p>Secret / Passphrase：{{ (state?.legacy_connections?.[mode]?.configured ?? state?.legacy_key_configured[mode])?'已保存，不回传':'未配置' }}</p></div></div>
   </details>
  </AppCard>
  <AppCard class="p-4 min-w-0 space-y-2"><details><summary class="cursor-pointer min-h-11 text-sm" style="color:var(--text-main)">运行依赖维护（不修改账户绑定）</summary><div class="flex flex-wrap gap-2 my-2"><AppButton :disabled="busy" @click="checkCli">检测 Node/npm/CLI</AppButton><AppButton :disabled="busy" @click="askInstallCli">安装 / 更新 CLI</AppButton></div><p v-if="cliCheck" class="text-xs leading-relaxed" style="color:var(--text-muted);overflow-wrap:anywhere">Node：{{ cliCheck.node_version || '未安装' }} · npm：{{ cliCheck.npm_version || '未安装' }} · CLI：{{ cliCheck.okx_version || '未安装' }}<br>{{ cliCheck.okx_path }}</p></details></AppCard>
  <AppCard v-if="!state?.connections.length" class="p-6 text-sm" style="color:var(--text-muted)">尚未建立受管理连接。可先添加资讯 OAuth，或导入现有 Key；不会自动修改当前交易配置。</AppCard>
  <div class="grid grid-cols-1 xl:grid-cols-2 items-start gap-4 min-w-0">
   <AppCard v-for="c in state?.connections || []" :key="c.id" class="p-4 min-w-0 space-y-3" :data-connection-card="c.id">
    <header class="flex flex-wrap justify-between gap-2"><h3 class="font-semibold break-words" style="color:var(--text-main)">{{ c.display_label || c.label }}</h3><span class="text-xs" style="color:var(--text-muted)">{{ c.auth_type==='oauth'?'OAuth':'API Key' }} · {{ connectionStateLabel(c.status) }}</span></header>
    <dl v-if="c.auth_type==='api_key'" class="grid grid-cols-1 sm:grid-cols-3 gap-2 text-xs" style="color:var(--text-muted);overflow-wrap:anywhere" data-masked-credentials>
     <div class="min-w-0"><dt>API Key（脱敏）</dt><dd class="font-mono mt-1">{{ c.credentials_display?.api_key || '--' }}</dd></div>
     <div><dt>Secret Key</dt><dd class="mt-1">{{ c.credentials_display?(c.credentials_display.secret_key_saved?'已保存，不回传':'未配置'):'待读取' }}</dd></div>
     <div><dt>Passphrase</dt><dd class="mt-1">{{ c.credentials_display?(c.credentials_display.passphrase_saved?'已保存，不回传':'未配置'):'待读取' }}</dd></div>
    </dl>
    <p class="text-xs" style="color:var(--text-muted)">{{ c.site }} · 首选{{ c.mode==='demo'?'模拟盘':'普通市场/实盘' }} · 创建连接不会开启交易</p>
    <div class="flex flex-wrap gap-2"><AppButton v-if="c.auth_type==='oauth'" size="sm" :disabled="busy||isBound(c.id)" @click="startOAuth(c)">官方授权</AppButton><AppButton v-if="c.auth_type==='oauth'||c.mode==='demo'" size="sm" :disabled="busy" @click="probe(c,'demo')">检查模拟盘</AppButton><AppButton v-if="c.auth_type==='oauth'||c.mode==='live'" size="sm" :disabled="busy" @click="probe(c,'live')">检查普通市场/实盘</AppButton><AppButton size="sm" variant="ghost" :disabled="busy||isBound(c.id)" @click="ask('delete','news',c.id)">删除候选</AppButton></div>
    <details class="min-w-0 text-xs" data-capability-details><summary class="cursor-pointer min-h-11" style="color:var(--text-main)">授权与接口能力明细（读取不等于交易许可）</summary><div class="space-y-3 mt-2" style="color:var(--text-muted);overflow-wrap:anywhere"><div v-for="(cap,mode) in c.capabilities" :key="mode"><p>{{ mode==='demo'?'模拟盘':'普通市场/实盘' }} · 账户标识 {{ cap?.account_uid || '未核验' }}</p><ul class="mt-1 space-y-1"><li v-for="(result,name) in cap?.reads || {}" :key="name">{{ probeNames[String(name)] || name }}：{{ result.ok?'通过':friendlyConnectionError(result.error || '不可用') }}</li></ul><p>资讯：{{ cap?.news_ready?'通过':'未验证或不可用' }} · 交易写入：{{ c.auth_type==='oauth'?'未验收，禁止接管自动交易':'保留既有签名交易适配器' }}</p></div><p v-if="!Object.keys(c.capabilities).length">尚未进行只读能力检查。</p></div></details>
   </AppCard>
  </div>
  <AppDialog v-model:open="addOpen" title="添加候选连接" :busy="busy" description="不会自动替换正在运行的交易连接。OAuth 令牌不会返回浏览器。">
   <form class="space-y-3" @submit.prevent="create">
    <AppField label="连接名称" required v-slot="field"><input :id="field.id" v-model="form.label" class="ui-input w-full" maxlength="60" required></AppField>
    <AppField label="认证方式" v-slot="field"><select :id="field.id" v-model="form.auth_type" class="ui-input w-full" @change="clearSecrets"><option value="oauth">官方 OAuth</option><option value="api_key">API Key（现有交易链路）</option></select></AppField>
    <div class="grid grid-cols-1 sm:grid-cols-2 gap-3"><AppField label="首选环境" v-slot="field"><select :id="field.id" v-model="form.mode" class="ui-input w-full"><option value="demo">模拟盘</option><option value="live">普通市场/实盘</option></select></AppField><AppField label="OKX 站点" hint="请选择账户实际站点，不会自动切换地区。" v-slot="field"><select :id="field.id" v-model="form.site" class="ui-input w-full"><option value="global">Global</option><option value="eea">EEA（传输待验证）</option><option value="us">US（传输待验证）</option><option value="tr">TR（传输待验证）</option></select></AppField></div>
    <template v-if="form.auth_type==='api_key'"><AppField v-for="key in (['api_key','secret_key','passphrase'] as const)" :key="key" :label="({api_key:'API Key',secret_key:'Secret Key',passphrase:'Passphrase'})[key]" required v-slot="field"><input :id="field.id" v-model="form[key]" type="password" autocomplete="new-password" class="ui-input w-full" required></AppField></template>
    <p v-if="form.auth_type==='oauth'&&!state?.oauth_runtime?.binary_available" class="text-xs" style="color:var(--color-warn)">部署环境尚未检测到官方 OAuth 原生组件。可以先保存连接，完成组件安装后再授权。</p>
    <AppButton type="submit" variant="primary" :loading="busy">保存候选连接</AppButton>
   </form>
  </AppDialog>
  <AppDialog v-model:open="authorizeOpen" title="在 OKX 官方页面完成授权"><div v-if="authorization" class="space-y-3"><p class="text-sm">授权码：<strong class="font-mono">{{ authorization.user_code }}</strong></p><a :href="authorization.verification_uri" target="_blank" rel="noopener noreferrer" class="ui-button ui-button--primary">打开官方授权页面</a><p class="text-xs">授权有效期 {{ authorization.expires_in }} 秒。完成后返回此页，点击对应环境的“检查”按钮核验身份与接口能力。</p></div></AppDialog>
  <AppDialog v-model:open="confirmOpen" :title="confirm?.title || '确认操作'" :description="confirm?.description" :busy="busy"><AppField :label="'输入确认短语：'+confirm?.phrase" v-slot="field"><input :id="field.id" v-model="phrase" class="ui-input w-full font-mono" autocomplete="off"></AppField><template #footer><AppButton :disabled="busy" @click="confirmOpen=false">取消</AppButton><AppButton variant="danger" :disabled="phrase!==confirm?.phrase" :loading="busy" @click="submitConfirm">确认执行</AppButton></template></AppDialog>
 </div>
</template>
