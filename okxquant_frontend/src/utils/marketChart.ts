import { observedNumber } from './observationDisplay.ts'
import type { KLineData, Period } from 'klinecharts'

export const CHART_PERIODS = [
  { id: '15m', label: '15分', ms: 900000, period: { type: 'minute', span: 15 } },
  { id: '1H', label: '1时', ms: 3600000, period: { type: 'hour', span: 1 } },
  { id: '4H', label: '4时', ms: 14400000, period: { type: 'hour', span: 4 } },
  { id: '1D', label: '1日', ms: 86400000, period: { type: 'day', span: 1 } },
] satisfies Array<{ id: string; label: string; ms: number; period: Period }>
export type ChartPeriod = '15m' | '1H' | '4H' | '1D'
export interface ChartBar extends KLineData { volume: number; turnover: number; confirmed: boolean }
export interface CandleSnapshot {
  bars: ChartBar[]; precision: number; asOf: number; stale: boolean; hasGaps: boolean
}
export const CHART_INDICATORS = [
  { id: 'VWAP', label: 'VWAP · UTC 日内加权均价', main: true, params: [] },
  { id: 'MA', label: 'MA · 移动平均线', main: true, params: [5, 10, 20] },
  { id: 'EMA', label: 'EMA · 指数均线', main: true, params: [12, 26, 50] },
  { id: 'BOLL', label: 'BOLL · 布林带', main: true, params: [20, 2] },
  { id: 'SAR', label: 'SAR · 抛物线转向', main: true, params: [] },
  { id: 'BBI', label: 'BBI · 多空均线', main: true, params: [] },
  { id: 'VOL', label: 'VOL · 成交量', main: false, params: [] },
  { id: 'MACD', label: 'MACD · 趋势动量', main: false, params: [12, 26, 9] },
  { id: 'RSI', label: 'RSI · 相对强弱', main: false, params: [6, 12, 24] },
  { id: 'KDJ', label: 'KDJ · 随机指标', main: false, params: [9, 3, 3] },
  { id: 'OBV', label: 'OBV · 能量潮', main: false, params: [] },
  { id: 'WR', label: 'WR · 威廉指标', main: false, params: [14] },
]

const instPattern = /^[A-Z0-9]{1,24}-USDT-SWAP$/
export function chartInstruments(...groups: Array<Array<{ instId?: string }> | undefined>): string[] {
  const ids = new Set<string>()
  for (const group of groups) for (const row of Array.isArray(group) ? group : []) if (row && typeof row.instId === 'string' && instPattern.test(row.instId)) ids.add(row.instId)
  return ids.size ? [...ids] : ['BTC-USDT-SWAP']
}

export function parseCandleSnapshot(payload: any, instId: string, period: ChartPeriod): CandleSnapshot {
  if (!payload || payload.instId !== instId || payload.bar !== period || !Array.isArray(payload.candles) || !payload.candles.length || payload.candles.length > 300) throw new Error('K线数据与当前标的不匹配')
  const bars: ChartBar[] = payload.candles.map((row: any) => {
    const values = [row.ts, row.open, row.high, row.low, row.close, row.vol, row.turnover].map(observedNumber)
    if (values.some(v => v === null) || !Number.isSafeInteger(values[0]) || values[0]! <= 0 || typeof row.confirmed !== 'boolean') throw new Error('K线观测不完整')
    const [timestamp, open, high, low, close, volume, turnover] = values as [number, number, number, number, number, number, number]
    if (low <= 0 || low > Math.min(open, close) || high < Math.max(open, close) || volume < 0 || turnover < 0) throw new Error('K线价格或成交量无效')
    return { timestamp, open, high, low, close, volume, turnover, confirmed: row.confirmed }
  })
  if (bars.some((row, index) => index > 0 && row.timestamp <= bars[index - 1]!.timestamp)) throw new Error('K线时间顺序无效')
  const asOf = observedNumber(payload.as_of_ms)
  if (asOf === null || asOf <= 0 || !Number.isInteger(payload.price_precision) || payload.price_precision < 0 || payload.price_precision > 10 || typeof payload.stale !== 'boolean' || typeof payload.has_gaps !== 'boolean') throw new Error('K线状态不可核验')
  return { bars, precision: payload.price_precision, asOf, stale: payload.stale, hasGaps: payload.has_gaps }
}

// Same computation as upstream's custom indicator, with a complete UTC date key
// and an explicit missing value for zero volume (rather than inventing a VWAP).
export function chartVwap(rows: KLineData[]) {
  let session = '', weighted = 0, volume = 0
  return rows.map(row => {
    const day = new Date(row.timestamp).toISOString().slice(0, 10)
    if (day !== session) { session = day; weighted = 0; volume = 0 }
    const amount = observedNumber(row.volume)
    if (amount !== null && amount > 0) { weighted += ((row.high + row.low + row.close) / 3) * amount; volume += amount }
    return { vwap: volume > 0 ? weighted / volume : null }
  })
}

export function candleCountdown(start: number | undefined, period: ChartPeriod, serverNow: number): string {
  if (!start || !Number.isFinite(serverNow)) return '--'
  const width = CHART_PERIODS.find(p => p.id === period)!.ms
  const seconds = Math.ceil((start + width - serverNow) / 1000)
  if (seconds <= 0) return '等待新K线'
  const hh = Math.floor(seconds / 3600), mm = Math.floor((seconds % 3600) / 60), ss = seconds % 60
  return (hh ? String(hh).padStart(2, '0') + ':' : '') + String(mm).padStart(2, '0') + ':' + String(ss).padStart(2, '0')
}

export interface ChartPriceLine { id: string; label: string; price: number; tone: 'brand' | 'up' | 'down' | 'warn' }
export function chartPriceLines(instId: string, positions: any[], orders: any[]): ChartPriceLine[] {
  const lines: ChartPriceLine[] = []
  const add = (id: string, label: string, value: unknown, tone: ChartPriceLine['tone']) => {
    const price = observedNumber(value)
    if (price !== null && price > 0) lines.push({ id, label, price, tone })
  }
  for (const [i, p] of (Array.isArray(positions) ? positions : []).entries()) {
    if (!p || p.instId !== instId) continue
    const side = p.side === 'long' ? '多' : p.side === 'short' ? '空' : ''
    add(`cost-${i}`, `${side}持仓均价`, p.avgPx, 'brand')
    // Do not turn an unverified or stale protection reading into an assurance.
    if (p.cloud_oco_verified === true && p.protectionStatus === 'fully_protected') {
      add(`sl-${i}`, `${side}止损参考`, p.displayStop, 'down')
      add(`tp-${i}`, `${side}止盈参考`, p.takeProfitPx, 'up')
    }
  }
  for (const [i, o] of (Array.isArray(orders) ? orders : []).entries()) if (o && o.instId === instId && ['live', 'partially_filled'].includes(o.state)) add(`order-${o.ordId || i}`, '挂单价格', o.px, 'warn')
  return lines
}
