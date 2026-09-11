import { observedNumber } from './observationDisplay.ts'

export function feeAccounting(trade: any) {
  const verified = trade?.status === 'closed' && trade?.fee_allocation === 'verified_from_archived_fills'
    && trade?.fee_reconciliation?.status === 'verified'
  const opening = verified ? observedNumber(trade.open_fee) : null
  const closing = verified ? observedNumber(trade.close_fee) : null
  const valid = verified && opening !== null && closing !== null
  return { verified: valid, label: valid ? '成交费用已核对' : '费用分摊待核对',
    opening: valid ? opening : null, closing: valid ? closing : null,
    reason: trade?.fee_reconciliation?.reason || '缺少完整成交证据' }
}
export function feeText(value: unknown) {
  const n = observedNumber(value)
  if (n === null) return '--'
  return (n > 0 ? '+' : '') + String(n) + ' USDT'
}
export function ledgerValue(trade: any, keys: string[]) {
  for (const key of keys) if (Object.hasOwn(trade || {}, key)) return observedNumber(trade[key])
  return null
}
export function ledgerNumberText(value: number | null, decimals: number, suffix: string) {
  return value === null ? '--' : (value >= 0 ? '+' : '') + value.toFixed(decimals) + suffix
}
export function ledgerNumberColor(value: number | null) {
  return value === null ? 'var(--text-muted)' : value >= 0 ? 'var(--color-up)' : 'var(--color-down)'
}
