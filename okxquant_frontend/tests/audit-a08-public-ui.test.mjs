import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import * as Vue from 'vue'
import { renderToString } from 'vue/server-renderer'
import { parse, compileScript } from '@vue/compiler-sfc'
import ts from 'typescript'
import * as evolution from '../src/utils/evolutionDisplay.ts'
import * as observations from '../src/utils/observationDisplay.ts'
import * as support from '../src/utils/instrumentSupport.ts'

const read = path => readFileSync(new URL('../src/' + path, import.meta.url), 'utf8')
const icon = { setup: () => () => Vue.h('svg', { 'aria-hidden': 'true' }) }
// Render disclosure bodies too, so content/escaping contracts can be checked without a browser.
const surface = { setup: (_, { slots }) => () => Vue.h('section', slots.default?.()) }
function component(file, state = {}) {
  const { descriptor, errors } = parse(read('components/' + file))
  assert.deepEqual(errors, [])
  const compiled = compileScript(descriptor, { id: file, inlineTemplate: true })
  const code = ts.transpileModule(compiled.content, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022 } }).outputText
  const exports = {}
  new Function('require', 'exports', code)(name => {
    if (name === 'vue') return Vue
    if (name === 'lucide-vue-next') return new Proxy({}, { get: () => icon })
    if (name.includes('stores/dashboard')) return { useDashboardStore: () => ({ data: null, error: null, isStale: false, positions: [], pendingOrders: [], logs: [], ...state }) }
    if (name.includes('useClipboard')) return { useClipboard: () => ({ copyText: async () => true }) }
    if (name.includes('evolutionDisplay')) return evolution
    if (name.includes('observationDisplay')) return observations
    if (name.includes('instrumentSupport')) return support
    if (name.includes('/ui/') || name.includes('DecisionAuditPanel') || name.includes('InstrumentSupportNotice')) return { __esModule: true, default: surface }
    if (name.endsWith('.vue')) return { __esModule: true, default: component(name.split('/').pop(), state) }
    throw new Error('Unmocked dependency: ' + name)
  }, exports)
  return exports.default
}
async function render(file, props = {}, state = {}) {
  return renderToString(Vue.createSSRApp(component(file, state), props))
}
const metrics = html => html.match(/<dl class="research-metrics">([\s\S]*?)<\/dl>/)?.[1] || ''

test('net outcome uses verified settled samples, including breakeven in the denominator', () => {
  assert.deepEqual(evolution.reviewNetOutcome({ settled_samples: 5, wins: 2 }, true), { samples: 5, wins: 2, rate: '40%' })
  assert.equal(evolution.reviewNetOutcome({ settled_samples: 3, wins: 1 }, true).rate, '33.3%')
  assert.equal(evolution.reviewNetOutcome({ settled_samples: 2, wins: 0 }, true).rate, '0%')
  assert.equal(evolution.reviewNetOutcome({ settled_samples: 2, wins: 2 }, true).rate, '100%')
})
test('unknown scope, empty evidence, non-finite and impossible counts cannot become a net win rate', () => {
  for (const scope of [false, undefined]) assert.equal(evolution.reviewNetOutcome({ settled_samples: 5, wins: 2 }, scope).rate, '—')
  for (const feedback of [undefined, {}, { settled_samples: 0, wins: 0 }, { settled_samples: 2, wins: 3 }, { settled_samples: 2, wins: -1 }, { settled_samples: 2.5, wins: 1 }, { settled_samples: Infinity, wins: 1 }, { settled_samples: 2, wins: NaN }, { settled_samples: '2', wins: '1' }]) assert.equal(evolution.reviewNetOutcome(feedback, true).rate, '—')
})
test('review separates net outcome from unverified legacy rate and retains full source', async () => {
  const review = { status: 'success', report_scope_verified: true, sample_size: 5, win_rate: 80, pending_candidates: 2, evidence_feedback: { settled_samples: 5, wins: 2 }, review_markdown: '# Report <script>unsafe()</script>' }
  const before = structuredClone(review), html = await render('EvolutionReviewPanel.vue', { review })
  assert.match(metrics(html), /净费用后胜率<\/dt><dd>40%/)
  assert.doesNotMatch(metrics(html), /80%/)
  assert.match(html, /历史报告胜率（口径未核验）/)
  assert.match(html, /净盈利 2 \/ 已结算 5 笔/)
  assert.match(html, /待审核/)
  assert.match(html, /&lt;script&gt;unsafe\(\)&lt;\/script&gt;/)
  assert.deepEqual(review, before)
})
test('legacy success, missing scope and different-account reports never claim verified net win rate', async () => {
  for (const review of [{ status: 'success', win_rate: 99 }, { report_scope_verified: false, evidence_feedback: { settled_samples: 2, wins: 2 } }, { evidence_feedback: { settled_samples: 2, wins: 2 } }]) {
    const html = await render('EvolutionReviewPanel.vue', { review })
    assert.match(metrics(html), /净费用后胜率<\/dt><dd>—/)
    assert.match(html, /净费用后胜率尚未核验/)
  }
})
test('review failure is explicit even when backend supplies its generic informational message', async () => {
  const html = await render('EvolutionReviewPanel.vue', { review: { status: 'failed', message: '复盘结果与运行记忆分开保存', error_message: 'model timeout' } })
  assert.match(html, /最近尝试未完成/)
  assert.match(html, /model timeout/)
  assert.doesNotMatch(html, /报告账户范围未核验或不匹配/)
})
test('lab long audit is closed by default, with model configuration not invented', async () => {
  const html = await render('SelfEvolutionLab.vue')
  assert.match(html, /<details[^>]*data-lab-audit/)
  assert.doesNotMatch(html.match(/<details[^>]*data-lab-audit[^>]*>/)?.[0] || '', /\bopen(?:=|>)/)
  assert.match(html, /决策与等待审计/)
  assert.match(html, /当前配置模型/)
  assert.match(html, /尚未取得/)
  assert.doesNotMatch(html, /gemini-/)
  assert.match(html, /已发布运行记忆/)
})
test('unavailable memory is never labelled as an active published version', async () => {
  const html = await render('PublishedMemoryPanel.vue', { publication: { status: 'unavailable', active_version: 8, rules: [], message: '读取失败' } })
  assert.match(html, /状态不可用/)
  assert.doesNotMatch(html, /版本 v8|条已审核启用规则/)
})
test('small300 settings track arbitrary runtime values, not fixed historical risk or leverage', async () => {
  for (const [risk, leverage, margin] of [[0.02, 6, 270], [0.0125, 4, 160]]) {
    const data = { execution_profile: { execution: { id: 'small300', per_trade_equity_pct: risk, max_leverage: leverage, max_active_instruments: 2, total_margin_usdt: margin } } }
    const html = await render('StrategyTelemetryPanel.vue', {}, { data })
    assert.ok(html.includes(`${risk * 100}%`)); assert.ok(html.includes(`${leverage}x`)); assert.ok(html.includes(`${margin.toFixed(2)} U`))
    assert.doesNotMatch(html, /波段 5x|短线最高 20x|0\.5%|3x|实时更新/)
  }
})
test('missing telemetry does not fabricate statistics, leverage, standard mode, or circuit health', async () => {
  const html = await render('StrategyTelemetryPanel.vue')
  assert.match(html, /暂无统计/); assert.match(html, /待核验/)
  assert.doesNotMatch(html, /标准风控|未触发|0 笔|0 胜|NaN|Infinity|实时更新/)
})
test('telemetry zero remains zero while failed snapshots remain visibly historical', async () => {
  const data = { horizon_stats: { scalp: { closed: 0, wins: 0, net_pnl: 0 } }, execution_profile: { error: 'unreadable' } }
  const html = await render('StrategyTelemetryPanel.vue', {}, { data, error: 'offline' })
  assert.match(html, /0 笔 · 0 胜/); assert.match(html, /0.00 U/)
  assert.match(html, /历史快照 · 更新延迟/); assert.match(html, /配置不可用/)
})
test('displayed daily ratio never substitutes for an execution circuit signal', async () => {
  const data = { today_stats: { total_pnl: -90 }, account: { initial_capital: 100 }, execution_profile: { execution: { daily_drawdown_pct: 0.08 } } }
  assert.match(await render('StrategyTelemetryPanel.vue', {}, { data }), /待核验/)
  data.risk_status = { daily_blocked: true, daily_drawdown: 0.09, daily_threshold: 0.08 }
  assert.match(await render('StrategyTelemetryPanel.vue', {}, { data }), /已触发/)
  data.risk_status.daily_blocked = false
  assert.match(await render('StrategyTelemetryPanel.vue', {}, { data }), /未触发/)
})
test('prompt entry is in Header only, with no floating refresh or fake live indicator', () => {
  assert.match(read('components/HeaderBar.vue'), /<FloatingActions/)
  assert.doesNotMatch(read('views/DashboardView.vue'), /FloatingActions/)
  const source = read('components/FloatingActions.vue')
  assert.match(source, /data-header-prompt/)
  assert.doesNotMatch(source, /RefreshCw|fetchDashboard|animate-pulse|fixed bottom/)
})
test('prompt missing and historical records have honest states and copy only when content exists', async () => {
  const empty = await render('FloatingActions.vue')
  assert.match(empty, /暂无已保存提示词/); assert.doesNotMatch(empty, /复制全文/)
  const stale = await render('FloatingActions.vue', {}, { data: { ai_last_prompt: 'original <script>unsafe</script>' }, isStale: true })
  assert.match(stale, /最近取得的提示词记录/); assert.match(stale, /复制全文/); assert.match(stale, /&lt;script&gt;/)
  const available = await render('FloatingActions.vue', {}, { data: { ai_last_prompt: 'original' } })
  assert.match(available, /无法核验它属于本轮/)
})
test('detail dialogs leave closing to the shared accessible top control', () => {
  for (const file of ['FactorDetailModal.vue', 'FloatingActions.vue', 'AboutModal.vue']) {
    const source = read('components/' + file)
    assert.match(source, /AppDialog/)
    assert.doesNotMatch(source, /<button\s+@click="(?:emit\('close'\)|promptModalOpen = false)"/)
  }
  assert.doesNotMatch(read('components/TradesLedger.vue'), /#footer.*关闭/)
  assert.equal((read('components/ui/AppDialog.vue').match(/aria-label="关闭弹窗"/g) || []).length, 1)
})
test('retry controls are conditional and chart navigation is retained', () => {
  assert.match(read('views/DashboardView.vue'), /v-if="store.error \|\| \(store.data && store.isStale\)"/)
  const chart = read('components/MarketCandles.vue')
  assert.match(chart, /<AppButton v-if="delayed"[^>]+aria-label="重试获取K线"/)
  for (const label of ['放大K线', '缩小K线', 'K线回到最新']) assert.ok(chart.includes(label))
})
test('saved stacked layout is restored rather than unconditionally using dual', () => {
  assert.match(read('views/DashboardView.vue'), /getItem\('okxquant_dashboard_layout_v2'\) === 'stacked' \? 'stacked' : 'dual'/)
})
test('signal prompt cannot claim an unsupported cycle linkage or invent risk assessment', async () => {
  const html = await render('FactorDetailModal.vue', { visible: true, instrument: {} })
  assert.match(html, /该记录未提供盈亏比评估/)
  assert.doesNotMatch(html, /100% 审计|目标 R:R ≥ 2.5/)
  assert.match(read('components/FactorDetailModal.vue'), /未核验与本条信号的轮次关联/)
})
test('AI history has truthful missing assessments, stable expansion and accessible load-more', async () => {
  const data = { ai_brain_history: Array.from({ length: 26 }, (_, i) => ({ time: `record-${i}` })) }
  const html = await render('AiBrainHistory.vue', {}, { data })
  assert.match(html, /该记录未提供宏观研判/); assert.doesNotMatch(html, /宏观中性震荡/)
  assert.match(html, /加载更多历史记录（剩余 2 条）/)
  assert.equal((html.match(/aria-expanded="false"/g) || []).length, 24)
  assert.match(read('components/AiBrainHistory.vue'), /expanded.has\(historyKey\(item\)\)/)
})
test('realized PnL is not synthesized from total PnL and empty observations remain unknown', async () => {
  const data = { account: { total_eq: 100 }, today_stats: { total_pnl: 987.65, closed_trades: 0, win_rate: 0 } }
  const html = await render('TopHudRibbon.vue', {}, { data })
  assert.doesNotMatch(html, /987.65|胜率 0.00%/)
  assert.match(html, /今日已结盈亏/)
})
test('news actual zero is retained and protection snapshots never promise live safety', () => {
  assert.match(read('components/NewsIntelligence.vue'), /s.bullish_ratio \?\? s.bullish_pct \?\? '--'/)
  for (const file of ['TacticalDesk.vue', 'PositionList.vue']) {
    const source = read('components/' + file)
    assert.match(source, /!store.error && !store.isStale/)
    assert.doesNotMatch(source, /100% (?:OCO|交易所云端)/)
  }
  assert.doesNotMatch(read('components/TradesLedger.vue'), /t.lever \|\| '3x'/)
})
test('public documentation records dynamic risk settings and the honest prompt/history contracts', () => {
  const docs = read('views/DocsView.vue')
  for (const token of ['execution_profile.execution', '净费用后胜率', '独立时间和轮次关联', '长篇决策与等待审计默认折叠']) assert.ok(docs.includes(token))
})

test('horizon limits are read from effective backend policy and unknown orders are prominent', async () => {
  const data = {execution_profile:{execution:{id:'standard'},mode_limits:{scalp:{max_leverage:12,per_trade_equity_pct:.004},swing:{max_leverage:4,per_trade_equity_pct:.005}}},risk_status:{unresolved_entries:1,daily_blocked:false,daily_drawdown:.002,daily_threshold:.03}}
  const html=await render('StrategyTelemetryPanel.vue',{}, {data})
  assert.match(html,/波段 4x · 短线最高 12x/)
  assert.match(html,/短线 0.4% · 波段 0.5%/)
  assert.match(html,/待核对/);assert.match(html,/未触发/);assert.match(html,/-0.20%/)
  assert.match(html,/按账户可用资金/)
})
