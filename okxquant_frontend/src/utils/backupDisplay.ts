/** Pure presentation of the persisted backup configuration and manifest. */
export function backupConfiguration(simple: any) {
  const configured = simple?.configured === true
  const local = (simple?.target?.type || simple?.destination) === 'local'
  return {
    configured,
    label: configured ? (local ? '本地目标已配置 · 无需凭据' : '远端目标字段已配置') : '目标配置不完整',
    note: local
      ? (simple?.directory_write_verified === true ? '目录可写已验证' : '尚未验证目录可写；配置状态不代表备份成功')
      : (simple?.connection_verified === true ? '连接已验证；请以备份清单确认写入结果' : '尚未验证远端连接和写入；字段齐全不代表备份成功'),
    className: configured ? 'text-[var(--color-brand)]' : 'text-amber-500',
  }
}

/** Runtime manifests use Beijing wall time; ISO timestamps may carry an offset. */
export function backupTime(value: unknown): string {
  if (typeof value !== 'string') return '--'
  const text = value.trim()
  const match = /^(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})(?:\.\d{1,3})?(Z|[+-]\d{2}:\d{2})?$/.exec(text)
  if (!match) return '--'
  const [, y, m, d, hh, mm, ss, zone] = match
  const days = new Date(Date.UTC(Number(y), Number(m), 0)).getUTCDate()
  if (Number(m) < 1 || Number(m) > 12 || Number(d) < 1 || Number(d) > days || Number(hh) > 23 || Number(mm) > 59 || Number(ss) > 59) return '--'
  const date = new Date(text.replace(' ', 'T') + (zone ? '' : '+08:00'))
  if (!Number.isFinite(date.getTime())) return '--'
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit',
    hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23',
  }).format(date)
}

export function backupLatest(manifest: any) {
  const statuses: Record<string, { label: string; className: string }> = {
    success: { label: '成功', className: 'text-emerald-500' },
    failed: { label: '失败', className: 'text-red-500' },
    partial: { label: '部分成功', className: 'text-amber-500' },
    running: { label: '执行中', className: 'text-[var(--color-brand)]' },
    skipped: { label: '已跳过', className: 'text-[var(--text-muted)]' },
    unknown: { label: '未知', className: 'text-[var(--text-muted)]' },
  }
  const raw = typeof manifest?.status === 'string' ? manifest.status : ''
  const status = Object.hasOwn(statuses, raw) ? raw : 'unknown'
  return { status, ...statuses[status], startedAt: backupTime(manifest?.started_at), finishedAt: backupTime(manifest?.finished_at) }
}
