/** Display only finite observations; empty/unknown values must never become zero. */
export function observedNumber(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && !/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(value.trim())) return null
  const number = Number(value)
  return Number.isFinite(number) ? number : null
}

export function observedText(value: unknown): string {
  const number = observedNumber(value)
  return number === null ? '--' : String(number)
}

export function observedPercent(value: unknown, signed = false): string {
  const number = observedNumber(value)
  if (number === null || (!signed && (number < 0 || number > 100))) return '--'
  return `${signed && number >= 0 ? '+' : ''}${number}%`
}

export function observationColor(value: unknown): string {
  const number = observedNumber(value)
  return number === null ? 'var(--text-muted)' : number >= 0 ? 'var(--color-up)' : 'var(--color-down)'
}

function observedFlow(value: unknown): string {
  const number = observedNumber(value)
  if (number !== null) return `${number} U`
  if (typeof value !== 'string') return '--'
  // The backend may already format USDT with grouping or a magnitude suffix.
  const text = value.trim()
  const match = /^([+-]?(?:(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d*)?|\.\d+))\s*(?:万|亿|[kmb])?\s*(?:U|USDT)$/i.exec(text)
  return match && observedNumber(match[1]!.replaceAll(',', '')) !== null ? text : '--'
}

export function smartMoneyDisplay(value: unknown, supported = true) {
  const money = value && typeof value === 'object' ? value as Record<string, unknown> : {}
  // A ratio/flow from factor-library defaults is not proof of an observation.
  const valid = supported && money.valid === true
  const ratio = valid ? observedPercent(money.weighted_long_pct) : '--'
  const flow = valid ? observedFlow(money.net_flow_usdt) : '--'
  return {
    long: ratio === '--' ? '--' : `${ratio}多`,
    flow,
    unavailable: ratio === '--' && flow === '--',
  }
}
