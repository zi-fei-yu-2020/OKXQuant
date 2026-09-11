export function newsIsFresh(value: any, now = Date.now() / 1000): boolean {
  return value?.schema === 2 && value?.connection_status === 'fresh' && typeof value.last_success_at === 'number' && Number.isFinite(value.last_success_at) && now >= value.last_success_at && now - value.last_success_at <= 1200
}
export function newsTime(value: unknown): string {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? new Date(value * 1000).toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai', hour12: false }) : '--'
}
export function newsStatusText(value: any, now = Date.now() / 1000): string {
  if (value?.schema !== 2) return '旧版资讯缓存 · 时效未验证'
  if (newsIsFresh(value, now)) return '独立资讯连接正常'
  if (value?.connection_status === 'fresh') return '资讯缓存已过期'
  return ({ partial: '部分资讯不可用 · 请查看分区状态', unavailable: '资讯连接失败 · 不表示市场没有新闻', unconfigured: '未绑定独立资讯连接', binding_changed: '资讯绑定已改变 · 等待新数据' } as Record<string,string>)[value?.connection_status] || '资讯状态不可用'
}
