<script setup lang="ts">
import { computed } from 'vue'
import AppCard from './ui/AppCard.vue'
import AppBadge from './ui/AppBadge.vue'
import EvolutionEvidencePanel from './EvolutionEvidencePanel.vue'
import type { EvolutionFeedback } from './EvolutionEvidencePanel.vue'
import { insightGroups, insightExcerpt, reviewStatusLabel, changeProposalLabel } from '../utils/evolutionDisplay'
import { observedNumber } from '../utils/observationDisplay'
export interface EvolutionReview {
  status?: string
  last_attempt_at?: string | null
  last_success_at?: string | null
  active_memory_updated_at?: string | null
  review_change_status?: string | null
  sample_size?: number | null
  win_rate?: number | null
  pending_candidates?: number
  rejected_candidates?: number
  insights?: string[]
  recommendations?: string[]
  review_markdown?: string
  message?: string
  report_scope_verified?: boolean
  evidence_feedback?: EvolutionFeedback
}
const props = defineProps<{ review?: EvolutionReview }>()
const groups = computed(() => insightGroups(props.review?.insights))
const failed = computed(() => ['failed', 'timeout'].includes(props.review?.status || ''))
const statusTone = computed(() => failed.value || props.review?.status === 'other_scope_report' ? 'warning' : props.review?.status === 'success' ? 'success' : props.review?.status === 'running' ? 'brand' : 'neutral')
const winRate = computed(() => {
  const value = observedNumber(props.review?.win_rate)
  return value !== null && value >= 0 && value <= 100 ? `${value}%` : '—'
})
</script>
<template>
  <AppCard class="research-panel min-w-0" data-evolution-review>
    <header class="research-header">
      <h3>最新策略复盘</h3>
      <span role="status"><AppBadge :tone="statusTone">{{ reviewStatusLabel(review?.status) }}</AppBadge></span>
    </header>
    <p class="research-date">最近成功报告 <time>{{ review?.last_success_at || '尚无成功报告' }}</time><span>北京时间</span></p>
    <p v-if="failed" class="research-notice" role="status">{{ review?.message || '最近尝试未完成；下方保留上次成功报告，不代表本次运行成功。' }}</p>
    <p v-if="review?.status === 'no_new_evidence'" class="research-note">最近检查暂无新增平仓证据，继续展示上次成功报告。</p>
    <p v-if="review?.report_scope_verified === false || review?.status === 'other_scope_report'" class="research-notice" data-review-scope-warning>报告账户范围未核验或不匹配，仅供追溯；不能作为当前账户的已验证结果。</p>

    <dl class="research-metrics">
      <div><dt>复盘样本</dt><dd>{{ observedNumber(review?.sample_size) ?? '—' }}<small>笔</small></dd></div>
      <div><dt>样本胜率</dt><dd>{{ winRate }}</dd></div>
      <div><dt>本次变更建议</dt><dd class="research-metric-text">{{ changeProposalLabel(review?.review_change_status) }}</dd></div>
    </dl>
    <div class="research-queue"><span>待审核 <strong>{{ review?.pending_candidates ?? '—' }}</strong></span><span>审核未通过 <strong>{{ review?.rejected_candidates ?? '—' }}</strong></span><span class="research-queue__note" data-evolution-authority>建议未自动执行</span></div>

    <section class="research-findings" data-review-findings>
      <div class="research-section-title"><h4>关键发现</h4><span>模型复盘 · 摘要节选</span></div>
      <p v-if="!groups.length" class="research-note">暂无复盘结论，不推断策略处于最优状态。</p>
      <details v-for="group in groups" :key="group.id" class="research-finding" :data-insight-group="group.id">
        <summary>
          <span class="research-finding__heading"><strong>{{ group.label }}</strong><span>{{ group.items.length }} 条</span></span>
          <span class="research-finding__excerpt">{{ insightExcerpt(group.items[0]!.text) }}</span>
          <span class="research-finding__toggle" aria-hidden="true"><span class="research-closed">展开</span><span class="research-open">收起</span><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"><path d="m6 9 6 6 6-6" /></svg></span>
        </summary>
        <div class="research-disclosure-body">
          <p class="research-note">以下为模型原文；文中的“已验证事实”是报告自身标签，不等于系统已完成独立核验。</p>
          <ul class="research-text-list"><li v-for="item in group.items" :key="item.index" data-insight-original>{{ item.original }}</li></ul>
        </div>
      </details>
    </section>

    <div class="research-resources">
      <details class="action-disclosure research-disclosure" data-review-evidence>
        <summary><span>证据核对</span><span class="research-summary-meta">{{ review?.evidence_feedback ? '查看结构化反馈' : '旧报告未记录' }}</span></summary>
        <div class="research-disclosure-body"><EvolutionEvidencePanel :feedback="review?.evidence_feedback" /></div>
      </details>
      <details v-if="review?.recommendations?.length" class="action-disclosure research-disclosure" data-review-recommendations>
        <summary><span>改进建议</span><span class="research-summary-meta">{{ review.recommendations.length }} 条 · 未自动执行</span></summary>
        <ul class="research-disclosure-body research-text-list"><li v-for="(item,i) in review.recommendations" :key="i">{{ item }}</li></ul>
      </details>
      <details v-if="review?.review_markdown" class="action-disclosure research-disclosure" data-review-original>
        <summary><span>完整复盘报告</span><span class="research-summary-meta">模型原文</span></summary>
        <div class="research-disclosure-body"><p class="research-note">模型文本，不是执行记录。</p><pre tabindex="0" aria-label="完整复盘报告原文" class="research-original">{{ review.review_markdown }}</pre></div>
      </details>
      <details class="action-disclosure research-disclosure" data-review-task>
        <summary><span>任务详情与说明</span></summary>
        <div class="research-disclosure-body">
          <dl class="research-metadata">
            <div><dt>最近尝试（北京时间）</dt><dd>{{ review?.last_attempt_at || '—' }}</dd></div>
            <div><dt>最近成功报告</dt><dd>{{ review?.last_success_at || '—' }}</dd></div>
            <div><dt>运行记忆内容更新</dt><dd>{{ review?.active_memory_updated_at || '—' }}</dd></div>
            <div><dt>原始变更建议</dt><dd>{{ review?.review_change_status || '—' }}</dd></div>
          </dl>
          <p v-if="review?.message && !failed" class="research-note">{{ review.message }}</p>
          <p class="research-note">复盘报告不等于已应用的策略变更。NO_CHANGE 或建议尚未通过审核时，运行记忆日期不推进，风险参数不会自动修改。</p>
          <p class="research-note">复盘会生成报告和改进候选，但不会自动改交易规则。增加开单次数不等于提升策略；候选需要费用后表现、前向样本和回退条件验证。</p>
        </div>
      </details>
    </div>
  </AppCard>
</template>
<style src="./research-panels.css"></style>
