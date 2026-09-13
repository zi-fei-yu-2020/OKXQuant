export interface MacroAnalysis {
  text: string
  analyzed_at: string
  status: 'ready' | 'running' | 'blocked' | 'failed' | 'stale' | 'empty' | 'incomplete'
  message: string
  source?: string
  age_seconds?: number | null
}

function timestamp(value: unknown): number {
  if (typeof value === 'number') return value > 1e12 ? value : value * 1000
  if (typeof value !== 'string') return 0
  const date = value.match(/^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}/)?.[0]
  return date ? Date.parse(date.replace(' ', 'T') + '+08:00') : 0
}

export function resolveMacroAnalysis(data: { macro_analysis?: MacroAnalysis; macro_assessment?: string; ai_brain_history?: Array<{ time?: string; macro_assessment?: string }> } | null, now = Date.now()): MacroAnalysis {
  if (data?.macro_analysis) return data.macro_analysis
  const entries = (data?.ai_brain_history || []).filter(row => row.macro_assessment?.trim() && timestamp(row.time) > 0 && timestamp(row.time) <= now + 60000)
    .sort((a, b) => timestamp(b.time) - timestamp(a.time))
  const latest = entries[0]
  if (latest) {
    const stale = now - timestamp(latest.time) > 1800000
    return { text: latest.macro_assessment!, analyzed_at: latest.time || '', status: stale ? 'stale' : 'ready', message: stale ? '上次分析已过期，不能作为当前交易依据' : '', source: 'history' }
  }
  if (data?.macro_assessment?.trim()) return { text: data.macro_assessment, analyzed_at: '', status: 'stale', message: '分析时间未知，不作为当前交易依据' }
  return { text: '', analyzed_at: '', status: 'empty', message: '尚未取得可用模型分析；请查看任务运行状态' }
}

export function macroStatusLabel(status: MacroAnalysis['status']): string {
  return { ready: '分析结果', running: '更新中', blocked: '本轮暂停', failed: '本轮未就绪', stale: '历史结果', empty: '暂无结果', incomplete: '决策审计不完整' }[status]
}
