import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { dataHealthSource, dataHealthLabel } from '../src/utils/dataHealthDisplay.ts'

const overview = readFileSync(new URL('../src/views/admin/OverviewPage.vue', import.meta.url), 'utf8')
const table = readFileSync(new URL('../src/components/ui/AppTable.vue', import.meta.url), 'utf8')

test('known data-health files have readable short labels and unknown files retain their names', () => {
  for (const [file, label] of Object.entries({
    'ai_brain_decisions.json': 'AI 决策', 'factor_library_snapshot.json': '因子快照',
    'news_sentiment.json': '新闻情绪', 'trading_ledger.json': '交易账本',
  })) assert.equal(dataHealthSource(file), label)
  assert.equal(dataHealthSource('/data/ai_brain_decisions.json'), 'AI 决策')
  assert.equal(dataHealthSource('C:\\data\\trading_ledger.json'), '交易账本')
  assert.equal(dataHealthSource('future_pipeline_with_long_name.json'), 'future_pipeline_with_long_name.json')
  for (const value of [undefined, null, '', '  ']) assert.equal(dataHealthSource(value), '--')
})

test('overview contains the wide health table instead of widening the mobile page', () => {
  assert.match(overview, /<style scoped>/)
  assert.match(overview, /\.overview-panels\s*\{\s*grid-template-columns: minmax\(0, 1fr\)/)
  assert.match(overview, /@media \(min-width: 1200px\)[\s\S]*grid-template-columns: minmax\(0, 1\.4fr\) minmax\(460px, 1fr\)/)
  assert.match(overview, /class="data-health min-w-0/)
  assert.match(overview, /\.data-health-scroll\s*\{[^}]*min-width: 0;[^}]*max-width: 100%;[^}]*overflow-x: auto/)
  assert.match(overview, /\.data-health-table\s*\{[^}]*min-width: 420px;[^}]*table-layout: fixed/)
  assert.match(overview, /<AppTable label="运行数据" class="data-health-scroll"/)
  assert.match(table, /role="region"[^>]*tabindex="0"/)
})

test('all four columns and full filenames remain accessible while status badges cannot stack vertically', () => {
  assert.match(overview, /<colgroup>[\s\S]*?<\/colgroup>/)
  for (const heading of ['通道来源', '状态', '更新延时', '字节数']) assert.ok(overview.includes(heading))
  assert.match(overview, /:title="x.file \|\| x.name" :aria-label="x.file \|\| x.name"/)
  assert.match(overview, /dataHealthSource\(x.file \|\| x.name\)/)
  assert.match(overview, /inline-flex items-center space-x-1 whitespace-nowrap/)
  assert.match(overview, /\.data-health-table td\s*\{[^}]*white-space: nowrap;[^}]*overflow-wrap: normal/)
  assert.match(overview, /\.data-health-source\s*\{[^}]*text-overflow: ellipsis;[^}]*white-space: nowrap/)
})

test('operator pause is explicit and never promises exchange connectivity', () => {
  assert.ok(overview.includes('data-runtime-controls'))
  assert.ok(overview.includes('新的自动决策周期已暂停'))
  assert.ok(overview.includes('调度配置不等于交易所连接正常'))
})

test('status messages survive UTF-8 source generation', () => {
  assert.doesNotMatch(overview, /\?{3,}/)
})

test('fresh unconfigured news is not represented as valid market intelligence', () => {
  assert.equal(dataHealthLabel({fresh:false,data_status:'unconfigured'}), '未配置')
  assert.equal(dataHealthLabel({fresh:false,data_status:'partial'}), '数据不完整')
  assert.equal(dataHealthLabel({fresh:false,data_status:'invalid'}), '数据不可用')
  assert.equal(dataHealthLabel({fresh:true,data_status:'fresh'}), '正常新鲜')
  assert.ok(overview.includes('dataHealthLabel(x)'))
})
