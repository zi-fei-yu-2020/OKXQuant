import type { DecisionCycle, WaitAuditState } from './waitAudit.ts'
export type CycleItem = NonNullable<DecisionCycle['items']>[number]
export type PlanCheck = NonNullable<CycleItem['entry_plans']>['checks'][number]

export function checkLabel(reason: string): string {
  return ({ existing_macro_direction_veto: '大周期方向限制', quote_moved_beyond_closed_trigger: '报价已偏离收盘触发位置', no_observed_target: '缺少可观察的目标位', pullback_requires_established_trend:'回踩缺少同向1H趋势，反转需重新确认', closed_candle_trigger_not_met: '收盘触发条件未满足', net_rr_below_policy: '成本后盈亏比未达要求', invalid_geometry: '价格结构无效', market_data_invalid: '行情不可用', environment_not_verified_for_entry: '交易环境尚未核验', entry_candle_provenance_missing: '缺少同一时点的收盘证据' } as Record<string,string>)[reason] || reason || '检查未就绪'
}
export function setupLabel(setup: string): string {
  return ({ all: '全部形态', pullback_reclaim: '回踩回收', closed_range_breakout: '区间突破' } as Record<string,string>)[setup] || setup || '未知形态'
}
export function sideLabel(side: string): string { return side === 'long' ? '做多' : side === 'short' ? '做空' : '方向未明' }
export function decisionLabel(item: CycleItem): string {
  if (item.status === 'incomplete') return '审计未通过'
  if (item.status === 'audited_wait') return '等待 · 审计通过'
  if (item.status === 'execution_rejected') return '执行未通过'
  if (item.status === 'entry_candidate') return '开仓候选 · 待执行核验'
  return item.action === 'WAIT' ? '等待 · 尚未审计' : item.action || '状态待核验'
}
export function incompleteReason(item: CycleItem, fallback?: string | null): string {
  if (item.status !== 'incomplete') return ''
  return (item.reason || fallback || '本轮未提供完整的可核验审计说明').replace(/^WAIT审计不完整[:：]\s*/, '')
}
export function groupedPlanChecks(checks: PlanCheck[] = []) {
  const groups = new Map<string, { key: string; side: string; reason: string; setups: string[]; ratios: number[]; checks: PlanCheck[] }>()
  for (const check of checks) {
    const key = JSON.stringify([check.side, check.reason])
    const group = groups.get(key) || { key, side: check.side, reason: check.reason, setups: [], ratios: [], checks: [] }
    if (!group.setups.includes(check.setup)) group.setups.push(check.setup)
    if (Number.isFinite(check.net_rr) && !group.ratios.includes(check.net_rr!)) group.ratios.push(check.net_rr!)
    group.checks.push(check); groups.set(key, group)
  }
  return [...groups.values()]
}

/** One disclosure per instrument; cycle results take priority over saved audit status. */
export function mergedAuditRows(cycle?: DecisionCycle, audit?: WaitAuditState) {
  const cycleItems = new Map((cycle?.items || []).map(item => [item.instId, item]))
  const auditItems = new Map((audit?.items || []).map(item => [item.instId, item]))
  const ids = [...new Set([...cycleItems.keys(), ...auditItems.keys()])]
  return ids.map(instId => {
    const item = cycleItems.get(instId)
    const record = auditItems.get(instId)
    const status = item?.status || record?.status || 'unknown'
    const repair = item?.wait_repair || record?.wait_repair
    const diagnostic = status === 'incomplete' && repair?.remaining_error ? repair.remaining_error : item ? incompleteReason(item, record?.status === status ? record.error : undefined)
      : status === 'incomplete' ? record?.error || record?.reason || '缺少完整审计说明' : ''
    return { instId, item, record, status, diagnostic, repair,
      programError: item?.entry_plans?.error ? checkLabel(item.entry_plans.error) : '',
      executionReason: status === 'execution_rejected' ? item?.reason || record?.reason || '执行核验未通过，未确认下单' : '',
      checks: groupedPlanChecks(item?.entry_plans?.checks),
      plans: item?.entry_plans?.plans || [],
      historical: !item || !!cycle?.unavailable_reason }
  })
}
export type AuditDisplayRow = ReturnType<typeof mergedAuditRows>[number]

export function compactAuditStatus(status: string): string {
  return ({audited_wait:'等待 · 已审计', incomplete:'决策待补全', execution_rejected:'执行未通过',
    entry_candidate:'候选待核验'} as Record<string,string>)[status] || '待审计'
}

export function directionSummary(row: AuditDisplayRow, side: 'long' | 'short') {
  const action = side === 'long' ? 'BUY_LONG' : 'SELL_SHORT'
  const plans = row.plans.filter(plan => plan.action === action)
  if (plans.length) {
    const selected = plans.find(plan => plan.id === row.item?.candidate_id)
    return { text: selected ? `${setupLabel(selected.setup)} · 模型已选择` : `${plans.length} 个程序方案 · 模型未选择`, extra: 0 }
  }
  const priority: Record<string,number> = { pullback_requires_established_trend:2, market_data_invalid:0, entry_candle_provenance_missing:0,
    environment_not_verified_for_entry:0, invalid_geometry:1, no_observed_target:2,
    net_rr_below_policy:3, existing_macro_direction_veto:4, quote_moved_beyond_closed_trigger:5,
    closed_candle_trigger_not_met:6 }
  const groups = row.checks.filter(group => group.side === side)
    .sort((a,b) => (priority[a.reason] ?? 0) - (priority[b.reason] ?? 0))
  const primary = groups[0]
  if (primary) {
    const shortLabels: Record<string,string> = { pullback_requires_established_trend:'缺少同向趋势，不能当作回踩', no_observed_target:'缺少目标位', net_rr_below_policy:'净盈亏比不足',
      closed_candle_trigger_not_met:'等待收盘触发', existing_macro_direction_veto:'大周期方向限制' }
    const ratios = primary.ratios.length ? `（${primary.ratios.map(value => value.toFixed(2)).join(' / ')}）` : ''
    return { text: (shortLabels[primary.reason] || checkLabel(primary.reason)) + ratios, extra: groups.length - 1 }
  }
  const direction = row.record?.status === row.status ? row.record.audit?.[side] : undefined
  const labels: Record<string,string> = { confirmation_pending:'等待条件确认', net_rr_below_minimum:'净盈亏比不足',
    macro_constraint:'大周期方向限制', position_constraint:'现有持仓约束', data_unavailable:'数据待核验',
    no_observed_edge:'尚无可核验优势', no_valid_setup:'暂无有效形态' }
  const text = direction?.code && labels[direction.code] ? labels[direction.code]
    : direction?.reason ? (direction.reason.length > 28 ? direction.reason.slice(0,28) + '…' : direction.reason) : '暂无方向说明'
  return { text, extra: 0 }
}
