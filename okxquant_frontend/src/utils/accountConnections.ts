export type AccountMode = 'demo' | 'live'
export type BindingPurpose = AccountMode | 'news'
export interface ConnectionCapability {
  account_uid?: string
  checked_at?: number
  read_ready?: boolean
  news_ready?: boolean
  write_ready?: boolean
  write_status?: string
  reads?: Record<string, { ok: boolean; error?: string }>
  news_error?: string
}
export interface CredentialsDisplay { api_key: string; secret_key_saved: boolean; passphrase_saved: boolean }
export interface AccountConnection {
  display_label?: string
  credentials_display?: CredentialsDisplay
  id: string
  label: string
  auth_type: 'api_key' | 'oauth'
  mode: AccountMode
  site: string
  status: string
  capabilities: Partial<Record<AccountMode, ConnectionCapability>>
}
export interface AccountCenterState {
  manual_close_enabled?: boolean
  news_interval_seconds?: number
  legacy_connections?: Partial<Record<AccountMode, { configured: boolean; source: string; credentials_display: CredentialsDisplay }>>
  managed: boolean
  active_mode: AccountMode
  connections: AccountConnection[]
  bindings: Record<BindingPurpose, string | null>
  legacy_key_configured: Record<AccountMode, boolean>
  oauth_runtime?: { binary_available: boolean; posix_ready: boolean }
  news?: { schema?: number; connection_status?: string; updated_at?: string; last_success_at?: number; last_attempt_at?: number; message?: string }
}
export function connectionStateLabel(status: string): string {
  return ({ unverified: '待核验', authorization_pending: '等待官方授权', identity_verified: '身份已核验', unavailable: '连接不可用', legacy_preserved: '原有连接已保留' } as Record<string,string>)[status] || '状态待确认'
}
export function newsConnectionLabel(status?: string): string {
  return ({ fresh:'资讯读取成功', partial:'部分资讯不可用', unavailable:'资讯读取失败', unconfigured:'资讯连接未绑定', binding_changed:'绑定已改变，等待更新', error:'资讯连接异常' } as Record<string,string>)[status || ''] || '资讯时效未验证'
}
export function canBind(connection: AccountConnection, purpose: BindingPurpose): boolean {
  const cap=connection.capabilities[purpose==='news'?'live':purpose]
  if(connection.status!=='identity_verified' || !cap?.account_uid) return false
  if(purpose==='news') return !!cap.news_ready
  return connection.auth_type==='api_key' && connection.mode===purpose && !!cap.read_ready
}
export function friendlyConnectionError(value: string): string {
  return ({ oauth_binary_missing:'部署环境尚未安装官方 OAuth 授权组件，请先完成安装。', oauth_transport_requires_linux_or_wsl:'OAuth 传输需要 Linux 或 WSL 环境。', oauth_token_unavailable:'OAuth 授权尚未完成、已过期或被撤销，请重新核验授权。', oauth_account_changed:'授权实际账户与绑定身份不符，已阻止继续请求。', oauth_write_capability_not_validated:'OAuth 交易写入和保护闭环尚未验收，不能开启自动交易。', regional_transport_not_verified:'该站点的连接适配尚未验证，本轮不会改用其他站点。' } as Record<string,string>)[value] || value
}


export function syncBindingSelections(selected: Record<BindingPurpose,string>, previous: AccountCenterState | undefined, next: AccountCenterState): void {
  for (const purpose of ['demo','live','news'] as const) {
    const oldBound = previous?.bindings[purpose] || ''
    const choice = selected[purpose]
    if (!previous || choice === oldBound || !next.connections.some(c => c.id === choice)) {
      selected[purpose] = next.bindings[purpose] || ''
    }
  }
}
export function overviewConnection(runtime: any): { mode: string; configured: boolean; source: string; status: string } {
  const c = runtime?.trading_connection, credentials=runtime?.credentials || {}
  const oldEnvironment=String(runtime?.configuration?.['OKX 当前环境'] || '')
  const mode=c?.mode || (typeof credentials.simulated_trading === 'boolean' ? (credentials.simulated_trading?'demo':'live') : oldEnvironment.includes('DEMO')?'demo':oldEnvironment.includes('LIVE')?'live':'unknown')
  const configured=c?.configured ?? credentials.okx_configured ?? credentials.okx ?? false
  return { mode, configured:!!configured, source:c?.auth_type || credentials.credential_source || 'api_key', status:c?.status || credentials.connection_status || (configured?'configured':'unconfigured') }
}
