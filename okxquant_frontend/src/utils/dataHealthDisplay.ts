const sourceLabels: Record<string, string> = {
  'ai_brain_decisions.json': 'AI 决策',
  'factor_library_snapshot.json': '因子快照',
  'news_sentiment.json': '新闻情绪',
  'trading_ledger.json': '交易账本',
}

/** Short labels retain the original filename in the table's title/accessible name. */
export function dataHealthSource(value: unknown): string {
  if (typeof value !== 'string' || !value.trim()) return '--'
  const basename = value.split(/[\\/]/).pop() || value
  return sourceLabels[basename] || basename
}

export function isDataHealthDisabled(row: {
  fresh?: boolean | null
  data_status?: string
  status?: string
  required?: boolean
  enabled?: boolean
}): boolean {
  return (
    row.status === 'disabled' ||
    row.data_status === 'disabled' ||
    row.data_status === 'not_required' ||
    row.status === 'not_required' ||
    row.required === false ||
    row.enabled === false
  )
}

export function dataHealthLabel(row: {
  fresh?: boolean | null
  data_status?: string
  status?: string
  required?: boolean
  enabled?: boolean
}): string {
  if (isDataHealthDisabled(row)) {
    return '已停用'
  }
  if (row.data_status === 'unconfigured' || row.status === 'unconfigured') return '未配置'
  if (row.data_status === 'partial' || row.status === 'partial') return '数据不完整'
  if (
    row.data_status === 'unavailable' ||
    row.data_status === 'invalid' ||
    row.status === 'unavailable' ||
    row.status === 'invalid'
  ) {
    return '数据不可用'
  }
  return row.fresh === true ? '正常新鲜' : '延迟过期'
}
