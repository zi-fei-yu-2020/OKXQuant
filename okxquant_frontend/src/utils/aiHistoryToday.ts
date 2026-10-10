const dayFormatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit' })
const timeFormatter = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' })
export function shanghaiDay(now: number | Date = Date.now()): string {
  const fields = Object.fromEntries(dayFormatter.formatToParts(now).map(p => [p.type, p.value]))
  return [fields.year, fields.month, fields.day].join('-')
}
function historyInstant(row: any): number | null {
  const raw = row?.time ?? row?.timestamp
  if (typeof raw === 'number') { const ms = raw > 1e11 ? raw : raw * 1000; return Number.isFinite(ms) && Number.isFinite(new Date(ms).getTime()) ? ms : null }
  if (typeof raw !== 'string' || !raw.trim()) return null
  const text = /^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2}(?:\.\d+)?$/.test(raw) ? raw.replace(' ', 'T') + '+08:00' : raw
  const at = Date.parse(text)
  return Number.isFinite(at) ? at : null
}
export function historyDay(row: any): string | null {
  const at = historyInstant(row)
  return at === null ? null : shanghaiDay(at)
}
export function historyTime(row: any): string {
  const at = historyInstant(row)
  if (at === null) return '—'
  const f = Object.fromEntries(timeFormatter.formatToParts(at).map(p => [p.type, p.value]))
  return [f.year, f.month, f.day].join('-') + ' ' + [f.hour, f.minute, f.second].join(':')
}
export function todayHistory(rows: any[], now: number | Date = Date.now()): any[] {
  const day = shanghaiDay(now)
  return rows.filter(row => historyDay(row) === day)
}
