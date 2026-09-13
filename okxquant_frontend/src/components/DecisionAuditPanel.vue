<script setup lang="ts">
import { computed } from 'vue'
import AppCard from './ui/AppCard.vue'
import AppBadge from './ui/AppBadge.vue'
import { checkLabel, setupLabel, sideLabel, mergedAuditRows, compactAuditStatus, directionSummary } from '../utils/entryPlanDisplay'
import { conditionText, reviewLabel, waitStreakBadges } from '../utils/waitAudit'
import type { WaitAuditState, DecisionCycle } from '../utils/waitAudit'

const props = defineProps<{ audit?: WaitAuditState; cycle?: DecisionCycle }>()
const rows = computed(() => mergedAuditRows(props.cycle, props.audit).map(row => ({ ...row,
  directions: (['long', 'short'] as const).map(side => ({ side, ...directionSummary(row, side) })),
})))
const counts = computed(() => props.cycle?.unavailable_reason ? undefined : props.cycle?.counts)
const diagnostics = computed(() => props.audit?.diagnostics || props.cycle?.wait_diagnostics)
const streakBadges = computed(() => waitStreakBadges(props.audit, diagnostics.value))
const diagnosticStart = computed(() => diagnostics.value ? new Date(diagnostics.value.since * 1000).toLocaleString('zh-CN', {timeZone:'Asia/Shanghai',hour12:false}) : '尚未开始')
const headline = computed(() => {
  if (props.audit?.status === 'error' || props.cycle?.unavailable_reason) return '本轮数据待核验'
  if ((counts.value?.incomplete || 0) > 0) return '有决策需要补全'
  if ((counts.value?.execution_rejected || 0) > 0) return '有候选未通过执行'
  if ((counts.value?.entry_candidate || 0) > 0) return `${counts.value!.entry_candidate} 个开仓候选待核验`
  if (counts.value?.entry_candidate === 0) return '本轮暂无开仓候选'
  return rows.value.length ? '最近审计结果' : '等待本轮决策'
})
const stats = computed(() => {
  if (!counts.value) return []
  return [
    { label: '审查', value: props.cycle?.evaluated_count, warning: false },
    { label: '候选', value: counts.value.entry_candidate, warning: false },
    { label: '等待已审', value: counts.value.audited_wait, warning: false },
    ...(['incomplete', 'execution_rejected'] as const).filter(key => (counts.value?.[key] || 0) > 0)
      .map(key => ({ label: key === 'incomplete' ? '待补全' : '执行未通过', value: counts.value?.[key], warning: true })),
  ]
})
const timestamp = computed(() => props.audit?.updated_at
  ? new Date(props.audit.updated_at * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false })
  : '尚无审计记录')
const numberText = (value?: number) => Number.isFinite(value) ? value!.toFixed(2) : '—'
</script>

<template>
  <AppCard class="decision-audit min-w-0 max-w-full" data-decision-audit>
    <header class="audit-header">
      <h3>决策与等待审计</h3>
      <span class="audit-time">上次审计 · {{ timestamp }}</span>
    </header>

    <div class="audit-overview">
      <div class="audit-overview__heading">
        <h4>{{ headline }}</h4>
        <div v-if="streakBadges.length" class="audit-streaks" data-audit-streak>
          <span v-for="badge in streakBadges" :key="badge.key" class="audit-streak" :class="{ 'audit-streak--alert': badge.warning }">{{ badge.label }}</span>
        </div>
      </div>
      <dl v-if="stats.length" class="audit-stats">
        <div v-for="stat in stats" :key="stat.label" :class="{ 'audit-stat--warning': stat.warning }">
          <dt>{{ stat.label }}</dt><dd>{{ stat.value ?? '—' }}</dd>
        </div>
      </dl>
    </div>

    <p v-if="audit?.status === 'error' || cycle?.unavailable_reason" role="status" class="audit-notice">
      {{ cycle?.unavailable_reason || audit?.message || '审计数据不可用，请稍后复查。' }}<span v-if="rows.some(row => row.historical)"> 下方为历史审计，不代表本轮决策成功，也不会复用旧指令。</span>
    </p>
    <p v-if="!rows.length" class="audit-empty">尚无可展示的结构化决策，不能将旧版 WAIT 视为已审计。</p>

    <div v-else class="audit-list" data-entry-plan-review>
      <details v-for="row in rows" :key="row.instId" class="audit-row min-w-0 self-start" :data-wait-audit-card="row.instId">
        <summary class="audit-row__summary">
          <span class="audit-row__identity">
            <strong>{{ row.instId.split('-')[0] }}</strong>
            <AppBadge :tone="row.status === 'incomplete' || row.status === 'execution_rejected' ? 'warning' : row.status === 'entry_candidate' ? 'brand' : 'neutral'">
              {{ row.historical && row.status === 'audited_wait' ? '历史等待 · 已审计' : compactAuditStatus(row.status) }}
            </AppBadge>

          </span>
          <span class="audit-row__preview" data-plan-check-summary>
            <span v-if="row.diagnostic" class="audit-diagnostic" data-decision-diagnostic>审计未通过：{{ row.diagnostic }}</span>
            <span v-else-if="row.programError" class="audit-diagnostic">程序草案不可用：{{ row.programError }}</span>
            <span v-else-if="row.executionReason" class="audit-diagnostic">{{ row.executionReason }}</span>
            <template v-else>
              <span v-for="direction in row.directions" :key="direction.side" class="audit-direction" :data-direction-reason="direction.side">
                <span class="audit-direction__label">{{ sideLabel(direction.side) }}</span>
                <span class="audit-direction__text">{{ direction.text }}<span v-if="direction.extra" class="audit-more"> · 另 {{ direction.extra }} 项</span></span>
              </span>
            </template>
          </span>
          <span class="audit-row__expand" aria-hidden="true"><span class="when-closed">详情</span><span class="when-open">收起</span><svg viewBox="0 0 24 24" width="16" height="16" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 9 6 6 6-6" /></svg></span>
        </summary>

        <div class="audit-row__body">
          <section v-if="row.plans.length" class="audit-detail-section" data-program-plans>
            <h4>程序方案 <span>{{ row.plans.length }} 个 · 不等于已下单</span></h4>
            <article v-for="plan in row.plans" :key="plan.id" class="audit-plan">
              <p class="audit-plan__title">{{ plan.action === 'BUY_LONG' ? '做多' : '做空' }} · {{ setupLabel(plan.setup) }}</p>
              <p>{{ row.item?.candidate_id === plan.id ? (row.status === 'execution_rejected' ? '模型已选择，但执行未通过' : '模型已选择，仍需执行核验') : '模型未选择' }}</p>
              <dl class="audit-plan__prices">
                <div><dt>入场</dt><dd>{{ plan.entry_price }}</dd></div><div><dt>止损</dt><dd>{{ plan.stop_loss_price }}</dd></div>
                <div><dt>目标</dt><dd>{{ plan.take_profit_price }}</dd></div><div><dt>净 R:R</dt><dd>{{ numberText(plan.net_rr) }}</dd></div>
              </dl>
              <p v-for="(review, index) in (row.item?.candidate_reviews || []).filter(r => r.candidate_id === plan.id)" :key="index">未采纳说明：{{ review.reason }}</p>
            </article>
          </section>
          <section v-if="row.item?.entry_plans?.checks?.length" class="audit-detail-section" data-plan-check-details>
            <h4>逐项检查 <span>{{ row.item.entry_plans.checks.length }} 项 · 保留全部形态</span></h4>
            <ul class="audit-checks">
              <li v-for="(check,index) in row.item.entry_plans.checks" :key="index">
                <span>{{ sideLabel(check.side) }} · {{ setupLabel(check.setup) }}</span>
                <span>{{ checkLabel(check.reason) }}<template v-if="Number.isFinite(check.net_rr)">（净 R:R {{ numberText(check.net_rr) }}）</template></span>
              </li>
            </ul>
          </section>
          <section v-if="row.record || row.item?.reason || row.programError || row.repair" class="audit-detail-section" data-model-audit>
            <h4>模型与审计说明 <span v-if="row.record && reviewLabel(row.record)">{{ reviewLabel(row.record) }}</span></h4>
            <div v-if="row.repair" class="audit-repair" data-wait-repair-status>
              <p>{{ row.repair.status === 'corrected' ? '一次纠错后审计通过，动作仍为 WAIT，不授权交易。' : row.repair.status === 'deferred_for_actions' ? '本轮优先处理已有有效候选或风控指令，未追加纠错延迟；原错误保留。' : row.repair.status === 'failed' ? '纠错过程失败，未采纳输出；原审计错误保留。' : row.repair.attempted ? '已进行一次受限纠错，仍未通过；保留决策不完整。' : '本轮未进行纠错；原审计错误保留。' }}</p>
              <p v-if="row.repair.error_type">纠错状态：{{ row.repair.error_type }}<span v-if="row.repair.http_status"> · HTTP {{ row.repair.http_status }}</span></p>
              <p>初次错误：{{ row.repair.initial_error }}</p>
              <p v-if="row.repair.remaining_error">纠错核验：{{ row.repair.remaining_error }}</p>
            </div>
            <p v-if="row.historical" class="audit-notice">历史审计，仅供核对；完整理由和重审条件如下。</p>
            <p v-if="row.diagnostic && row.diagnostic !== row.item?.reason">{{ row.diagnostic }}</p>
            <p v-if="row.item?.reason">{{ row.item.reason }}</p>
            <p v-if="row.programError" class="audit-diagnostic">草案生成：{{ row.programError }}</p>
            <template v-if="row.record">
              <p v-if="row.record.status !== row.status" class="audit-notice">以下为最近保存的审计证据，不代表本轮已通过审查。</p>
              <p v-if="row.record.error && row.record.error !== row.item?.reason">{{ row.record.error }}</p>
              <p v-if="row.record.reason && row.record.reason !== row.item?.reason && row.record.reason !== row.record.error">{{ row.record.reason }}</p>
              <div v-if="row.record.audit" class="audit-evidence-grid grid-cols-1 items-start">
                <section v-for="side in (['long', 'short'] as const)" :key="side" class="audit-evidence">
                  <template v-if="row.record.audit[side]">
                    <h5>{{ sideLabel(side) }}依据</h5>
                    <p>{{ row.record.audit[side].reason }}</p>
                    <ul><li v-for="(e,index) in row.record.audit[side].evidence" :key="index"><code>{{ e.ref }} = {{ e.value }}</code><span>{{ e.interpretation }}</span></li></ul>
                    <p v-if="row.record.audit[side].net_rr_check">该方案净盈亏比 {{ numberText(row.record.audit[side].net_rr_check!.net_rr) }}，门槛 {{ row.record.audit[side].net_rr_check!.minimum }}；仅否定此方案。</p>
                    <p class="audit-reconsider">重审条件（全部满足）：{{ row.record.audit[side].reconsider.conditions.map(conditionText).join(' 且 ') }}</p>
                    <p>{{ row.record.audit[side].reconsider.reason }}</p>
                  </template>
                </section>
              </div>
              <p v-if="row.record.audit?.previous_review">前轮复查：{{ row.record.audit.previous_review.reason }}</p>
            </template>
          </section>
          <p v-if="!row.plans.length && !row.item?.entry_plans?.checks?.length && !row.record && !row.item?.reason && !row.programError">本轮未返回详细证据，不能推断已满足交易条件。</p>
        </div>
      </details>
    </div>

    <footer class="audit-footer">
      <p v-if="cycle?.environment_notices?.length" class="audit-environment"><span>环境限制</span>{{ cycle.environment_notices.join('；') }}</p>
      <details v-if="cycle?.executed_actions?.length" class="audit-footer-disclosure" data-execution-records>
        <summary>执行记录 <span>{{ cycle.executed_actions.length }} 条</span></summary>
        <ul><li v-for="(action,index) in cycle.executed_actions" :key="index">{{ action }}</li></ul>
      </details>
      <details class="audit-footer-disclosure" data-wait-diagnostics>
        <summary>连续统计口径</summary>
        <div v-if="diagnostics" class="audit-diagnostic-counts">
          <p>无程序草案：{{ diagnostics.streaks.no_program_plans ?? '未知' }} 轮 · 模型全 WAIT：{{ diagnostics.streaks.model_all_wait ?? '未知' }} 轮 · 审计异常：{{ diagnostics.streaks.audit_incomplete ?? '未知' }} 轮</p>
          <p>有草案且审计通过后全 WAIT：{{ diagnostics.streaks.audited_wait_with_plans ?? '未知' }} 轮。分类统计起于 {{ diagnosticStart }}，已记录 {{ diagnostics.observed_rounds }} 轮，不把旧数据推算成新口径。</p>
        </div>
        <p>历史最终 WAIT 连续 {{ audit?.legacy_final_wait_streak ?? audit?.no_entry_candidate_streak ?? '未知' }} 轮，包含校验失败，不等于全部正常审查通过。分类统计不改变入场条件，也不触发强制交易。</p>
      </details>
      <details class="audit-footer-disclosure" data-audit-explanation>
        <summary>审计说明</summary>
        <p>审计通过只表示证据与条件可核验，不代表已证明没有交易优势。程序草案、模型选择均不等于订单；条件触发仅要求重新研究，不会强制开仓。</p>
        <p v-if="audit?.message && audit?.status !== 'error'">{{ audit.message }}</p>
      </details>
    </footer>
  </AppCard>
</template>

<style scoped>
.decision-audit { container-type: inline-size; padding: 1rem; color: var(--text-main); font-size: .8125rem; overflow-wrap: anywhere; }
.audit-header, .audit-overview__heading { display: flex; align-items: center; justify-content: space-between; flex-wrap: wrap; gap: .5rem 1rem; }
.audit-header h3 { font-size: .875rem; font-weight: 600; }
.audit-time { font-size: .75rem; color: var(--text-muted); font-variant-numeric: tabular-nums; }
.audit-overview { margin: 1rem 0; padding: .875rem 1rem; border-radius: .75rem; background: var(--bg-card-subtle); border: 1px solid var(--border-subtle); }
.audit-overview h4 { font-size: 1rem; font-weight: 650; }
.audit-streaks { display: flex; flex-wrap: wrap; gap: .375rem; }
.audit-repair { padding: .75rem; border: 1px solid var(--border-subtle); border-radius: .5rem; background: var(--bg-card-subtle); }
.audit-streak { padding: .25rem .5rem; font-size: .75rem; color: var(--text-muted); border-radius: .375rem; }
.audit-streak--alert { background: var(--color-warn-bg); color: var(--text-main); border: 1px solid var(--color-warn-border); }
.audit-stats { display: flex; flex-wrap: wrap; gap: .5rem 1.25rem; margin-top: .625rem; }
.audit-stats > div { display: flex; align-items: baseline; gap: .5rem; }
.audit-stats dt { color: var(--text-muted); font-size: .75rem; }
.audit-stats dd { font-weight: 650; font-variant-numeric: tabular-nums; }
.audit-stat--warning dd { color: var(--color-warn); }
.audit-notice { margin: .75rem 0; padding: .75rem; border: 1px solid var(--color-warn-border); border-radius: .5rem; background: var(--color-warn-bg); }
.audit-empty { padding: .75rem 0; color: var(--text-muted); }
.audit-list { border: 1px solid var(--border-subtle); border-radius: .75rem; overflow: clip; }
.audit-row + .audit-row { border-top: 1px solid var(--border-subtle); }
.audit-row__summary { display: grid; grid-template-columns: minmax(0,1fr) auto; align-items: center; gap: .625rem 1rem; padding: .875rem 1rem; cursor: pointer; list-style: none; min-height: 48px; }
.audit-row__summary::-webkit-details-marker { display: none; }
.audit-row__summary:hover, .audit-row[open] > summary { background: var(--bg-card-subtle); }
.audit-row__summary:focus-visible { outline: 2px solid var(--color-brand); outline-offset: -3px; border-radius: .375rem; }
.audit-row__identity { display: flex; align-items: center; flex-wrap: wrap; gap: .5rem; min-width: 0; }
.audit-row__identity strong { font-size: .875rem; letter-spacing: .025em; }
.audit-saved { color: var(--text-muted); font-size: .75rem; }
.audit-row__preview { grid-column: 1 / -1; grid-row: 2; display: grid; gap: .375rem; min-width: 0; line-height: 1.6; }
.audit-direction { display: flex; gap: .5rem; min-width: 0; align-items: baseline; }
.audit-direction__label { color: var(--text-muted); flex-shrink: 0; font-size: .75rem; }
.audit-direction__text { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.audit-shadow-line { display: block; margin-top: .25rem; overflow-wrap: anywhere; }
.audit-more { color: var(--text-muted); font-size: .75rem; }
.audit-diagnostic { color: var(--text-main); overflow-wrap: anywhere; }
.audit-row__preview .audit-diagnostic { display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
.audit-row__expand { grid-column: 2; grid-row: 1; display: flex; align-items: center; gap: .25rem; color: var(--color-brand); font-size: .75rem; }
.audit-row__expand svg { transition: transform .15s ease; flex-shrink: 0; }
.when-open { display: none; }
.audit-row[open] > summary .when-closed { display: none; }
.audit-row[open] > summary .when-open { display: inline; }
.audit-row[open] > summary svg { transform: rotate(180deg); }
.audit-row__body { padding: 1rem; display: grid; gap: 1rem; border-top: 1px dashed var(--border-subtle); line-height: 1.7; }
.audit-detail-section { display: grid; gap: .625rem; min-width: 0; }
.audit-detail-section h4 { font-size: .8125rem; font-weight: 650; }
.audit-detail-section h4 > span { font-weight: 400; font-size: .75rem; color: var(--text-muted); margin-left: .375rem; }
.audit-checks { display: grid; gap: .5rem; }
.audit-checks li { display: grid; gap: .125rem .75rem; }
.audit-checks li > span:first-child { color: var(--text-muted); font-size: .75rem; }
.audit-plan, .audit-evidence { padding: .75rem; border-radius: .5rem; background: var(--bg-card-subtle); min-width: 0; }
.audit-plan { display: grid; gap: .5rem; }
.audit-plan__title, .audit-evidence h5 { font-weight: 600; }
.audit-plan__prices { display: grid; grid-template-columns: repeat(2,minmax(0,1fr)); gap: .5rem; }
.audit-plan__prices dt { color: var(--text-muted); font-size: .75rem; }
.audit-plan__prices dd { font-variant-numeric: tabular-nums; }
.audit-evidence-grid { display: grid; gap: .75rem; }
.audit-evidence { display: grid; gap: .5rem; }
.audit-evidence ul, .audit-footer-disclosure ul { display: grid; gap: .5rem; }
.audit-evidence li { display: grid; gap: .125rem; }
.audit-evidence code { color: var(--text-muted); font-size: .75rem; white-space: normal; }
.audit-reconsider { padding-top: .5rem; border-top: 1px solid var(--border-subtle); }
.audit-footer { margin-top: .75rem; display: grid; gap: .25rem; }
.audit-environment { display: flex; align-items: baseline; gap: .625rem; line-height: 1.6; color: var(--text-muted); font-size: .75rem; margin-bottom: .375rem; }
.audit-environment > span { white-space: nowrap; }
.audit-footer-disclosure { font-size: .75rem; color: var(--text-muted); }
.audit-footer-disclosure > summary { cursor: pointer; min-height: 44px; padding: .75rem .25rem; color: var(--text-main); }
.audit-footer-disclosure > summary > span { color: var(--text-muted); margin-left: .25rem; }
.audit-footer-disclosure > summary:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 1px; border-radius: .25rem; }
.audit-footer-disclosure p, .audit-footer-disclosure ul { padding: .25rem .75rem .75rem; line-height: 1.7; }
@container (min-width: 720px) {
  .audit-row__summary { grid-template-columns: 195px minmax(0,1fr) auto; }
  .audit-row__preview { grid-column: 2; grid-row: 1; }
  .audit-row__expand { grid-column: 3; }
  .audit-checks li { grid-template-columns: minmax(140px, .65fr) minmax(0,2fr); }
  .audit-plan__prices { grid-template-columns: repeat(4,minmax(0,1fr)); }
  .audit-evidence-grid { grid-template-columns: repeat(2,minmax(0,1fr)); }
}
@container (min-width: 1000px) { .audit-row__preview { grid-template-columns: repeat(2,minmax(0,1fr)); } .audit-diagnostic { grid-column: 1 / -1; } }
@media (max-width: 480px) { .decision-audit { padding: .75rem; } .audit-row__summary, .audit-row__body { padding: .75rem; } .audit-overview { padding: .75rem; } }
@media (prefers-reduced-motion: reduce) { .audit-row__expand svg { transition: none; } }
</style>
