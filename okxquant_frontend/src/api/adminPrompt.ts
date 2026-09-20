export type ExecutionProfile = 'standard' | 'small300'

// All displayed limits come from the backend, never from prompt prose.
export function executionSummary(settings: Record<string, unknown> | null | undefined): string {
  if (!settings) return '执行参数尚未加载'
  if (settings.id === 'standard') return String(settings.label || '标准风控（独立配置）')
  const items: string[] = []
  const number = (key: string, label: string, unit: string, scale = 1) => {
    const value = settings[key]
    if (typeof value === 'number' && Number.isFinite(value)) items.push(`${label}${Number((value * scale).toFixed(4))}${unit}`)
  }
  number('equity_cap_usdt', '风险基数上限 ', 'U')
  number('per_trade_equity_pct', '单笔风险 ', '%', 100)
  number('single_asset_margin_usdt', '单标的保证金 ', 'U')
  number('total_margin_usdt', '总保证金 ', 'U')
  number('max_active_instruments', '最多 ', ' 个标的')
  number('max_leverage', '实际杠杆上限 ', ' 倍')
  return items.join(' · ') || String(settings.label || '执行参数尚未加载')
}

export function executionBinding(profile: any): ExecutionProfile {
  if (profile?.execution_profile === 'standard' || profile?.execution_profile === 'small300') return profile.execution_profile
  return profile?.execution_settings?.id === 'small300' ? 'small300' : 'standard'
}

export function newProfilePackage(source: any, name: string, execution: ExecutionProfile) {
  return { ...source, profile: { ...source.profile, name: name.trim(), execution_profile: execution } }
}
