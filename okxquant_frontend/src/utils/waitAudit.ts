export interface AuditCondition { ref: string; op: string; value: number | string | boolean }
export interface DirectionAudit {
  code: string
  reason: string
  evidence: Array<{ ref: string; value: unknown; interpretation: string }>
  reconsider: { conditions: AuditCondition[]; reason: string }
  net_rr_check?: { net_rr: number; minimum: number; scope: string }
}
export interface WaitRepair { status: string; attempted?: boolean; initial_error?: string; remaining_error?: string; error_type?: string; http_status?: number | null }
export interface WaitDiagnostics {
  version: string
  since: number
  observed_rounds: number
  streaks: { no_program_plans: number | null; model_all_wait: number | null; audit_incomplete: number | null; audited_wait_with_plans: number | null }
  last_cycle?: { program_plans?: number | null; model_entry_proposals?: number | null; incomplete?: number; audited_wait?: number; repair_corrected?: number }
}
export interface WaitAuditRecord {
  instId: string
  status: string
  reason?: string
  error?: string | null
  updated_at?: number
  audit?: { long: DirectionAudit; short: DirectionAudit; previous_review?: { reason: string } } | null
  previous_check?: { required?: boolean; trigger_checks?: Record<string, string> }
  wait_repair?: WaitRepair | null
}
export interface WaitAuditState {
  status: string
  updated_at?: number
  no_entry_candidate_streak?: number
  legacy_final_wait_streak?: number
  diagnostics?: WaitDiagnostics | null
  incomplete_count?: number
  alert?: boolean
  message?: string
  items: WaitAuditRecord[]
}
export interface ProgramEntryPlan {
  id: string
  setup: string
  action: string
  entry_price: number
  stop_loss_price: number
  take_profit_price: number
  net_rr: number
}
export interface DecisionCycle {
  items?: Array<{ instId: string; action: string; status: string; reason?: string; candidate_id?: string | null; wait_repair?: WaitRepair | null;
    entry_plans?: { plans: ProgramEntryPlan[]; checks: Array<{ reason: string; side: string; setup: string; net_rr?: number }>; error?: string };
    candidate_reviews?: Array<{ candidate_id: string; reason: string }> }>
  wait_diagnostics?: WaitDiagnostics | null
  executed_actions?: string[]
  timestamp?: string
  status?: string
  evaluated_count?: number
  counts?: Record<string, number>
  environment_notices?: string[]
  unavailable_reason?: string
}
export function waitStreakBadges(audit?: WaitAuditState, diagnostics?: WaitDiagnostics | null) {
  const d = diagnostics || audit?.diagnostics
  if (!d) {
    const legacy = audit?.legacy_final_wait_streak ?? audit?.no_entry_candidate_streak ?? 0
    return legacy > 0 ? [{key:'legacy',label:`最终 WAIT 连续 ${legacy} 轮`,warning:!!audit?.alert}] : []
  }
  const result: Array<{key:string;label:string;warning:boolean}> = []
  if ((d.streaks.no_program_plans || 0) > 0) result.push({key:'plans',label:`连续 ${d.streaks.no_program_plans} 轮无程序草案`,warning:d.streaks.no_program_plans! >= 8})
  else if ((d.streaks.model_all_wait || 0) > 0) result.push({key:'model',label:`模型连续 ${d.streaks.model_all_wait} 轮 WAIT`,warning:d.streaks.model_all_wait! >= 8})
  if ((d.streaks.audit_incomplete || 0) > 0) result.push({key:'audit',label:`审计异常连续 ${d.streaks.audit_incomplete} 轮`,warning:true})
  return result
}
export function auditLabel(status?: string): string {
  return ({ audited_wait: 'WAIT · 审计通过', incomplete: '决策不完整', execution_rejected: '候选被风控拒绝', entry_candidate: '开仓候选 · 待执行核验' } as Record<string, string>)[status || ''] || '待审计'
}
export function conditionText(c: AuditCondition): string {
  if (c.op === 'available') return `${c.ref} 恢复可用`
  const op = ({ gt: '>', gte: '≥', lt: '<', lte: '≤', eq: '=', ne: '≠' } as Record<string, string>)[c.op] || c.op
  return `${c.ref} ${op} ${String(c.value)}`
}

export function reviewLabel(row: WaitAuditRecord): string {
  if (!row.previous_check?.required) return ""
  return row.status === "audited_wait" && row.audit?.previous_review ? "\u524d\u8f6e\u6761\u4ef6\u5df2\u590d\u67e5" : "\u524d\u8f6e\u6761\u4ef6\u5f85\u590d\u67e5"
}

export interface OpportunityShadow {
  mode: 'shadow'; updated_at?: number; stale?: boolean; status?: string
  items: Array<{ instrument: string; ready_count?: number; baseline_plans?: number; error?: string
    opportunities: Array<{ id: string; setup: string; side: string; state: string; reason: string; net_rr?: number }> }>
}
