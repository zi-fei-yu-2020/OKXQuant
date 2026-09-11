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

export function dataHealthLabel(row: {fresh?: boolean; data_status?: string}) {
  if (row.data_status === 'unconfigured') return '未配置'
  if (row.data_status === 'partial') return '数据不完整'
  if (row.data_status === 'unavailable' || row.data_status === 'invalid') return '数据不可用'
  return row.fresh === true ? '正常新鲜' : '延迟过期'
}
