export interface ScenarioCandidate {
  id: string
  status: string
  display_status?: string
  reason?: string
  initial_net_rr?: number | null
  definition: {
    instrument: string
    scenario: string
    side: 'long' | 'short'
    trigger: number
    stop: number
    target: number
    created_at_ms: number
    expires_at_ms: number
    target_basis: string
    remaining_uncertainty: string
  }
}
export interface ScenarioShadowStatus {
  enabled: boolean
  mode: 'shadow_only'
  order_authorized: false
  stale?: boolean
  status?: string
  message?: string
  updated_at_ms?: number
  candidates?: ScenarioCandidate[]
  frames?: Record<string, { at_ms: number; status: string; reason?: string }>
}
export function scenarioLabel(value: string): string {
  return ({ trend_pullback: '趋势回踩', range_breakout: '区间突破' } as Record<string, string>)[value] || value
}
export function shadowStateLabel(value: string): string {
  return ({ armed: '候选已建立 · 等待触发', triggered_research: '条件已触发 · 尚未复核', review_expired: '复核窗口已过期', invalidated: '结构或监测条件失效', expired: '候选已过期', rejected: '研究门槛未通过', observed: '已检查收盘数据', data_unavailable: '数据不可用', observation_only: '环境受限 · 仅观察', monitoring_gap: '监测中断 · 不补发触发' } as Record<string, string>)[value] || '状态待核验'
}
export function shadowReason(value?: string): string {
  return ({ waiting_fixed_price_trigger: '按冻结条件等待价格触发，不自动移动门槛。', initial_geometry_or_net_rr_insufficient: '当前方案价格结构或扣成本后的盈亏比未达标。', current_price_net_rr_insufficient: '触发后的现价使成本后盈亏比不足。', chase_limit_exceeded: '价格偏离预定触发位过多，不追价。', frozen_structure_broken: '价格已破坏冻结的失效边界。', macro_environment_invalidated: '已收盘的大周期环境不再支持原候选。', fixed_expiry_reached: '固定有效期已结束。', monitoring_gap_no_retroactive_trigger: '监测存在缺口，不补发历史触发。', closed_5m_trigger_requires_fresh_review: '需新行情、模型证据、账户状态及最终风控复核；不是下单授权。', environment_not_supported_or_unknown: '标的支持状态不可用或不允许开仓。' } as Record<string, string>)[value || ''] || value || '尚无完整原因'
}
export function shadowPrice(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) ? String(Number(value.toPrecision(8))) : '--'
}
