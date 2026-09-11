export interface CapitalPoolStatus {
  enabled: boolean
  status: string
  version?: string
  configured_cap?: number
  pool_nav?: number
  risk_equity?: number
  account_equity?: number
  strategy_pnl_since_allocation?: number
  max_active_instruments?: number
  stale?: boolean
  message?: string
}
export function poolStatusLabel(status: string): string {
  return ({ disabled: '未启用', active: '额度约束生效', blocked: '已阻止新增风险',
    awaiting_flat_initialization: '待空仓额度初始化', environment_mismatch: '账户环境不匹配', error: '配置或状态异常' } as Record<string,string>)[status] || '状态未知'
}
export function poolMoney(value: unknown): string {
  if (value == null || value === '' || typeof value === 'boolean') return '--'
  const number = Number(value)
  return Number.isFinite(number) ? number.toFixed(2) : '--'
}
