export function positionQuotaLabel(execution: {
  quantity_limits_enabled?: boolean
  max_active_instruments?: number
}): string {
  if (execution.quantity_limits_enabled === false) return '日内熔断控制（无数量配额）'
  if (execution.quantity_limits_enabled !== true) return '待核验'
  const count = execution.max_active_instruments
  return typeof count === 'number' && Number.isInteger(count) && count > 0
    ? `${count} 个`
    : '配额不可用'
}
