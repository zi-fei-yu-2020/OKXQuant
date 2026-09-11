import { observedNumber } from './observationDisplay.ts'

export function durationText(value: unknown): string {
  const seconds = observedNumber(value)
  if (seconds === null || seconds < 0) return '--'
  if (seconds < 1) return seconds > 0 ? '不足1秒' : '0秒'
  if (seconds < 60) return `${Math.floor(seconds * 10) / 10}秒`
  const whole = Math.floor(seconds), hours = Math.floor(whole / 3600), minutes = Math.floor(whole % 3600 / 60), remainder = whole % 60
  return hours ? `${hours}时${minutes}分${remainder ? `${remainder}秒` : ''}` : `${minutes}分${remainder ? `${remainder}秒` : '钟'}`
}

export function tradeDuration(row: {duration_seconds?: unknown; hold_duration?: string; duration?: string; status?: string; open_time?: string; close_time?: string}): string {
  if (row.duration_seconds !== undefined && row.duration_seconds !== null) return durationText(row.duration_seconds)
  // Compatibility with older saved rows. These timestamps are explicitly Beijing
  // time, not the browser's local timezone; never invent an end time for holdings.
  if (row.status === 'closed' && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(row.open_time || '') && /^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/.test(row.close_time || '')) {
    const start = Date.parse(row.open_time!.replace(' ', 'T') + '+08:00')
    const end = Date.parse(row.close_time!.replace(' ', 'T') + '+08:00')
    return durationText((end - start) / 1000)
  }
  return row.hold_duration || row.duration || '--'
}
