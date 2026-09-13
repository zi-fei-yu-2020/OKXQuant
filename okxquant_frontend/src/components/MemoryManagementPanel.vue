<script setup lang="ts">
import { ref, computed } from 'vue'
import { useApi } from '../composables/useApi'
import { useToast } from '../composables/useFeedback'
import { useAuthStore } from '../stores/auth'
import AppCard from './ui/AppCard.vue'
import AppDialog from './ui/AppDialog.vue'
import AppButton from './ui/AppButton.vue'
import AppField from './ui/AppField.vue'
import PublishedMemoryPanel from './PublishedMemoryPanel.vue'
import { memoryActionLabel, memoryCandidateLabel } from '../utils/memory'
import type { MemoryPublication, MemoryCandidate, MemoryAction } from '../utils/memory'
const props=defineProps<{ publication?: MemoryPublication; schedule?: string | string[] }>()
const emit=defineEmits<{ updated: [value: any] }>()
const { api }=useApi();const toast=useToast();const auth=useAuthStore()
const busy=ref(false)
const action=ref<MemoryAction>('ADD'),text=ref(''),target=ref(''),rationale=ref('')
const dialog=ref(false),kind=ref<'initialize'|'publish'|'reject'|'restore'>('publish')
const candidate=ref<MemoryCandidate>(),version=ref<number>(),preview=ref<any>()
const baseEvidenceHash=ref<string>()
const baseRevision=ref(0),baseScope=ref(''),phrase=ref(''),note=ref(''),selectedTrades=ref<string[]>([])
const requiredPhrase=computed(()=>({initialize:'INITIALIZE MEMORY',publish:'PUBLISH MEMORY',reject:'',restore:'ROLLBACK MEMORY'}[kind.value]))
const needsEvidence=computed(()=>kind.value==='publish' && ['ADD','REVISE'].includes(candidate.value?.proposal.action || ''))
const canSubmit=computed(()=>!busy.value && phrase.value===requiredPhrase.value &&
  (kind.value==='initialize' || note.value.trim().length>=(kind.value==='reject'?4:10)) &&
  (!needsEvidence.value || selectedTrades.value.length>=2))
const enabledRules=computed(()=>(props.publication?.rules || []).filter(r=>r.enabled))
async function perform(fn:()=>Promise<void>){busy.value=true;try{await fn()}catch(e:any){if(!e?.silent)toast.error(e.message || '记忆操作未完成')}finally{busy.value=false}}
async function refresh(){const result=await api('/api/v1/admin/memory');emit('updated',result)}
async function create(){await perform(async()=>{
 const result=await api('/api/v1/admin/memory/candidates',{method:'POST',body:JSON.stringify({scope:props.publication?.scope,action:action.value,text:text.value,target_rule_id:target.value || null,rationale:rationale.value,request_id:Date.now().toString(36)+'-'+Math.random().toString(36).slice(2)})})
 text.value='';rationale.value='';target.value='';await refresh();toast.success(result.message)
})}
function openReview(type:typeof kind.value,item?:MemoryCandidate){
 kind.value=type;candidate.value=item;baseRevision.value=props.publication?.revision || 0;baseScope.value=props.publication?.scope || ''
 baseEvidenceHash.value=props.publication?.evidence_hash
 phrase.value='';note.value='';selectedTrades.value=[];preview.value=undefined;dialog.value=true
}
async function openVersion(id:number){await perform(async()=>{
 const value=await api(`/api/v1/admin/memory/versions/${id}`)
 openReview('restore');version.value=id;preview.value=value
})}
async function submit(){if(!canSubmit.value)return;await perform(async()=>{
 let path='/api/v1/admin/memory/initialize'
 const body:any={scope:baseScope.value,confirmation:phrase.value}
 if(kind.value!=='initialize'){body.expected_revision=baseRevision.value;body.note=note.value}
 if(kind.value==='publish'){path=`/api/v1/admin/memory/candidates/${candidate.value?.id}/publish`;body.supporting_trade_ids=selectedTrades.value;body.evidence_hash=baseEvidenceHash.value}
 if(kind.value==='reject'){path=`/api/v1/admin/memory/candidates/${candidate.value?.id}/reject`;delete body.confirmation}
 if(kind.value==='restore')path=`/api/v1/admin/memory/versions/${version.value}/restore`
 const result=await api(path,{method:'POST',body:JSON.stringify(body)})
 dialog.value=false;await refresh();toast.success(result.message || '记忆已纳管；模型输入保持不变。')
})}
</script>
<template>
  <div class="min-w-0 space-y-4" data-memory-management>
    <PublishedMemoryPanel :publication="publication" />
    <AppCard class="p-4 space-y-3 min-w-0">
      <header class="flex flex-wrap justify-between items-center gap-2"><h3 class="font-semibold text-sm">记忆审核与发布</h3><AppButton size="sm" :loading="busy" @click="perform(refresh)">刷新记忆状态</AppButton></header>
      <p class="text-xs leading-relaxed" style="color:var(--text-muted)">复盘调度：{{ Array.isArray(schedule)?schedule.join('、'):schedule || '待核验' }}（北京时间）。报告、待审核候选与已发布规则分开。NO_CHANGE 不清空候选，也不伪造规则更新日期。</p>
      <template v-if="!publication?.active_version">
        <p class="text-sm">首次纳管将逐字保留当前实际模型输入。旧结构化记录仅进入待审核区，不自动启用。</p>
        <p v-if="publication?.legacy_unpublished?.length" class="text-xs">发现 {{ publication.legacy_unpublished.length }} 条旧结构化记录，其旧 enabled/评分标记不代表已经发布。</p>
        <AppButton v-if="auth.isSuperadmin" :disabled="busy" @click="openReview('initialize')">纳管当前记忆（不改变输入）</AppButton>
      </template>
      <form v-if="auth.isSuperadmin" class="space-y-3 border-t pt-3" style="border-color:var(--border-subtle)" @submit.prevent="create">
        <div class="grid grid-cols-1 sm:grid-cols-2 gap-3">
          <AppField label="候选操作" v-slot="field"><select :id="field.id" v-model="action" class="ui-input w-full"><option v-for="a in (['ADD','REVISE','DEACTIVATE','CLEAR_LEGACY'] as const)" :key="a" :value="a">{{ memoryActionLabel(a) }}</option></select></AppField>
          <AppField v-if="action==='REVISE'||action==='DEACTIVATE'" label="目标已发布规则" v-slot="field"><select :id="field.id" v-model="target" class="ui-input w-full"><option value="">请选择规则</option><option v-for="rule in enabledRules" :key="rule.id" :value="rule.id">{{ rule.id }} · {{ rule.text.slice(0,30) }}</option></select></AppField>
        </div>
        <AppField v-if="action==='ADD'||action==='REVISE'" label="候选经验（不会立即注入模型）" v-slot="field"><textarea :id="field.id" v-model="text" class="ui-input w-full" rows="3" maxlength="3000" required /></AppField>
        <p v-else-if="action==='CLEAR_LEGACY'" class="text-xs" style="color:var(--color-warn)">只创建“移除旧兼容上下文”的候选。真正移除仍需审核和明确发布确认。</p>
        <AppField label="提议理由与适用边界" v-slot="field"><textarea :id="field.id" v-model="rationale" class="ui-input w-full" rows="2" maxlength="3000" /></AppField>
        <AppButton type="submit" :loading="busy" :disabled="!publication || ((action==='ADD'||action==='REVISE')&&text.trim().length<10) || ((action==='REVISE'||action==='DEACTIVATE')&&!target)">提交候选，稍后审核</AppButton>
      </form>
    </AppCard>
    <AppCard class="p-4 min-w-0 space-y-3">
      <h3 class="font-semibold text-sm">候选队列 · 待审 {{ publication?.pending_count || 0 }} · 拒绝/静态阻断 {{ publication?.rejected_count || 0 }}</h3>
      <p v-if="!publication?.candidates?.length" class="text-sm" style="color:var(--text-muted)">暂无已登记候选。没有新经验时无需为了更新日期而添加规则。</p>
      <details v-for="item in publication?.candidates || []" :key="item.id" class="min-w-0 border rounded-lg p-3 text-sm" style="border-color:var(--border-subtle);overflow-wrap:anywhere" :data-memory-candidate="item.id">
        <summary class="cursor-pointer min-h-11">{{ memoryActionLabel(item.proposal.action) }} · {{ memoryCandidateLabel(item.status) }} · {{ item.created_at }}</summary>
        <div class="space-y-2 mt-2">
          <p>{{ item.proposal.text || memoryActionLabel(item.proposal.action) }}</p>
          <p class="text-xs" style="color:var(--text-muted)">来源 {{ item.proposal.source }} · {{ item.proposal.target_rule_id || '无目标规则' }}</p>
          <p v-if="item.proposal.rationale" class="text-xs">提议说明：{{ item.proposal.rationale }}</p>
          <p v-if="item.proposal.lint_reasons.length" class="text-xs" style="color:var(--color-warn)">静态检查：{{ item.proposal.lint_reasons.join('；') }}。不能直接发布；如需修改，请提交新的明确候选。</p>
          <p v-if="item.review?.note" class="text-xs">审核记录：{{ item.review.note }}</p>
          <div v-if="auth.isSuperadmin" class="flex flex-wrap gap-2">
            <AppButton v-if="item.status==='pending'" size="sm" :disabled="busy" @click="openReview('publish',item)">核验证据并发布</AppButton>
            <AppButton v-if="item.status==='pending'||item.status==='blocked'" size="sm" :disabled="busy" @click="openReview('reject',item)">拒绝候选</AppButton>
          </div>
        </div>
      </details>
    </AppCard>
    <AppCard v-if="publication?.versions?.length" class="p-4 space-y-3 min-w-0">
      <h3 class="font-semibold text-sm">不可变发布历史</h3>
      <div v-for="item in publication.versions" :key="item.id" class="flex flex-wrap items-center justify-between gap-2 border-b pb-2 text-xs" style="border-color:var(--border-subtle);overflow-wrap:anywhere"><span class="min-w-0">v{{ item.id }} · {{ item.created_at }} · {{ item.reason }}</span><AppButton v-if="auth.isSuperadmin && item.id!==publication.active_version" size="sm" :disabled="busy" @click="openVersion(item.id)">查看并回滚到此内容</AppButton></div>
    </AppCard>
    <AppDialog v-model:open="dialog" :busy="busy" :title="kind==='initialize'?'纳管当前有效记忆':kind==='publish'?'审核并发布记忆':kind==='reject'?'拒绝记忆候选':'回滚已发布内容'">
      <div class="space-y-3 text-sm min-w-0">
        <p v-if="kind==='initialize'">仅建立版本快照并登记旧候选，当前实际模型记忆文本保持一致；不会启用旧的4条基准心法。</p>
        <template v-else>
          <p v-if="candidate">{{ memoryActionLabel(candidate.proposal.action) }}：{{ candidate.proposal.text || candidate.proposal.target_rule_id || '移除旧上下文' }}</p>
          <pre v-if="preview" class="max-h-60 overflow-y-auto whitespace-pre-wrap text-xs" style="overflow-wrap:anywhere">{{ preview.content }}</pre>
          <p class="text-xs" style="color:var(--text-muted)">静态检查与最少两条证据仅是准入检查，不证明收益。请人工核对适用场景和反例；发布不能覆盖硬风控。版本变化或策略正在执行时会拒绝本次发布。</p>
          <div v-if="needsEvidence" class="space-y-2">
            <p class="font-semibold text-xs">选择至少两条当前账户已平仓证据（{{ selectedTrades.length }} 条）</p>
            <div class="max-h-52 overflow-y-auto space-y-2">
              <label v-for="trade in publication?.evidence_trades || []" :key="trade.id" class="flex items-start gap-2 min-h-11 text-xs"><input v-model="selectedTrades" type="checkbox" :value="trade.id"><span class="break-all">{{ trade.instrument }} · {{ trade.closed_at }} · 净盈亏 {{ trade.net_pnl ?? '--' }} U<br>{{ trade.id }}</span></label>
              <p v-if="!publication?.evidence_trades?.length">没有可核验的当前账户平仓证据，不能发布新经验。</p>
            </div>
          </div>
          <AppField label="人工审核说明 / 拒绝或回滚原因" v-slot="field"><textarea :id="field.id" v-model="note" rows="3" class="ui-input w-full" maxlength="3000" /></AppField>
        </template>
        <AppField v-if="requiredPhrase" :label="'输入确认短语：'+requiredPhrase" v-slot="field"><input :id="field.id" v-model="phrase" class="ui-input w-full font-mono" autocomplete="off"></AppField>
      </div>
      <template #footer><AppButton :disabled="busy" @click="dialog=false">取消</AppButton><AppButton variant="primary" :disabled="!canSubmit" :loading="busy" @click="submit">确认执行</AppButton></template>
    </AppDialog>
  </div>
</template>
