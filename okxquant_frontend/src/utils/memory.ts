export type MemoryAction = 'ADD' | 'REVISE' | 'DEACTIVATE' | 'CLEAR_LEGACY'
export interface PublishedRule { id: string; text: string; enabled: boolean; source?: string; reviewed_at?: string }
export interface MemoryCandidate {
  id: string; status: string; created_at: string; published_version?: number | null
  proposal: { action: MemoryAction; text: string; target_rule_id?: string | null; source: string; rationale?: string; lint_reasons: string[]; suggested_trade_ids?: string[] }
  review?: { note?: string; reviewer?: string; at?: string }
}
export interface MemoryPublication {
  status: string; managed: boolean; scope: string; revision: number; active_version?: number | null
  published_at?: string | null; effective_updated_at?: string | null; prompt_hash?: string
  format?: string; content?: string; prompt_text?: string; legacy_context?: string
  rules: PublishedRule[]; pending_count?: number; rejected_count?: number; message?: string
  candidates?: MemoryCandidate[]
  versions?: Array<{ id: number; created_at: string; actor: string; reason: string }>
  legacy_unpublished?: Array<{ id: string; rule_text: string; enabled?: boolean }>
  evidence_hash?: string
  evidence_trades?: Array<{ id: string; instrument: string; closed_at: string; net_pnl: number | null }>
}
export const memoryActionLabel = (action: string) => ({ ADD: '新增经验', REVISE: '修订规则', DEACTIVATE: '停用规则', CLEAR_LEGACY: '移除旧兼容上下文' } as Record<string,string>)[action] || action
export const memoryCandidateLabel = (status: string) => ({ pending: '待人工审核', blocked: '静态检查未通过', rejected: '已拒绝', published: '已发布' } as Record<string,string>)[status] || status
