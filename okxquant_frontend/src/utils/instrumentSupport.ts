import type { InstrumentSupport, InstrumentSupportSummary } from '../types/dashboard'
export function instrumentSupport(instId: string, summary?: InstrumentSupportSummary, environment?: 'demo' | 'live'): InstrumentSupport {
  const row = summary?.items?.[instId]
  if (row && (!environment || row.environment === environment)) return row
  return { instId, environment: environment || 'demo', status: 'unknown', can_open: false, label: '支持状态待确认', checked_at: null,
    message: '等待当前交易环境的合约目录核验；核验前不参与新开仓或加仓。可见行情不代表可交易。' }
}
export function supportTone(support?: InstrumentSupport): 'success' | 'warning' | 'neutral' {
  return support?.status === 'supported' ? 'success' : support?.status === 'unknown' || !support ? 'neutral' : 'warning'
}
export function canOpen(support?: InstrumentSupport): boolean {
  return support?.status === 'supported' && support.can_open === true
}
