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
