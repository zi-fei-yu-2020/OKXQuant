type HealthSnapshot = {
  is_stale?: boolean
  data_health?: { status?: string; partial?: boolean }
} | null

export function dashboardIsStale(data: HealthSnapshot): boolean {
  return !!data && (
    data.is_stale === true ||
    data.data_health?.partial === true ||
    ['STALE', 'PARTIAL', 'OFFLINE'].includes(data.data_health?.status || '')
  )
}

export function monitorConnectionLabel(data: any, error: unknown, isStale: boolean): string {
  if (data?.risk_status?.daily_blocked === true) return '日内熔断已触发'
  const errors = Array.isArray(data?.data_health?.errors) ? data.data_health.errors : []
  const hasAccount = typeof data?.account?.total_eq === 'number' || (typeof data?.account?.total_eq === 'string' && data.account.total_eq.trim() !== '' && Number.isFinite(Number(data.account.total_eq)))
  const ledgerOnly = errors.length > 0 && errors.every((item: unknown) => /^(?:bills|ledger_sync|账本)/i.test(String(item)))
  if (ledgerOnly && hasAccount) return '账本更新延迟'
  if (error || !hasAccount || isStale) return '账户数据更新延迟'
  return '数据已更新'
}
