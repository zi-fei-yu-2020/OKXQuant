import test from 'node:test'
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'

// Source-only documentation contract: no backend imports, credentials, network or services.
// Runtime references: plugins/interceptors, scripts/{trading_prompt,entry_candidates,
// entry_gateway,ai_factor_trader,self_improvement_engine}.py and
// okxquant_backend/{council_manager,schedule_store,scheduler,account_connections}.py.
const source = readFileSync(new URL('../src/views/DocsView.vue', import.meta.url), 'utf8')
const template = source.match(/<template>([\s\S]*)<\/template>/)?.[1]
assert.ok(template, 'DocsView must retain its rendered template')
const textOf = html => html
  .replace(/<!--[\s\S]*?-->/g, '')
  .replace(/<[^>]*>/g, '')
  .replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&amp;/g, '&')
  .replace(/\s+/g, ' ').trim()
const text = textOf(template)
function section(id) {
  const match = template.match(new RegExp(`<section\\b[^>]*\\bid="${id}"[^>]*>([\\s\\S]*?)</section>`))
  assert.ok(match, `Missing documentation section: ${id}`)
  return textOf(match[1])
}
function includesAll(value, fragments) {
  for (const fragment of fragments) assert.ok(value.includes(fragment), `Missing runtime explanation: ${fragment}`)
}

test('entry score is a legal uncalibrated value, not a 75/80 admission or Meme gate', () => {
  includesAll(section('interceptors'), [
    'score / confidence 仅校验合法性', '0~100', '有限数值且非布尔值',
    '未校准研究评分而非胜率', '无 75/80 硬门槛', '无 Meme 特殊分数线',
  ])
  assert.doesNotMatch(text, /(?:75|80)\s*[%％]|Meme\s*币种提至\s*85/)
  assert.doesNotMatch(text, /(?:置信度|评分|score)[^。；]{0,20}(?:低于|未达到)\s*(?:75|80)/i)
  includesAll(section('faq'), ['入场评分没有 75/80 硬门槛', '本轮审计原因', '不代表已有持仓或挂单已清空'])
})

test('AI initiated closing retains its separate 85 threshold without disabling independent protection', () => {
  includesAll(section('interceptors'), [
    'AI 主动平仓（CLOSE_MARKET）的 85 门槛仍单独保留',
    '不适用于入场', '不限制独立止损与保护退出',
  ])
})

test('single brain is default and optional council uses model review rather than deterministic voting', () => {
  includesAll(section('overview'), ['默认单脑决策', 'Council 为可选模式', '不是数值加权投票'])
  includesAll(section('council'), [
    '默认单脑', 'Council 默认关闭', 'CIO 模型终审', '可全部 WAIT',
    '不构成数值加权投票', 'strict / weighted / aggressive 配置名不是确定性表决机制',
  ])
  assert.doesNotMatch(text, /三种委员会共识机制|一票否决制|加权共识制|动能突破优先|按各席位置信度加权投票|无条件强制降级|优先表决权/)
})

test('unified evidence and closed candle drafts do not authorize execution', () => {
  includesAll(section('prompt_studio'), [
    '自定义偏好不能覆盖统一证据契约与独立风控', '单脑与 Council 使用统一证据契约 trading-evidence-v1',
    '支持证据、反证与失效条件必须可核验', '闭合（已收盘）的 15M/1H K线',
    '符合契约的独立方案', '草案不是下单授权', 'candidate_id 后不得改写草案价格与失效点',
    '最终报价时效、触发有效性、净 RR 与风险预算',
  ])
  assert.doesNotMatch(text, /彻底解除了所有预设锁定/)
})

test('real trend and ADX plugins remain distinct from the final quote and net RR gates', () => {
  includesAll(section('interceptors'), [
    '01_macro_trend_filter.py', '02_confidence_gatekeeper.py',
    '03_adx_volatility_filter.py', '04_risk_reward_gatekeeper.py',
    '4H 宏观多头通道严禁摸顶开空', '4H 空头通道严禁接飞刀做多',
    '低于18的普通信号保持拦截', '价格几何 RR ≥ 2.0', '手续费、滑点和最终价格重算净 RR',
    '默认至少 2.0', '风险预算', '四个内置插件默认启用', '默认关闭的自定义示例',
    '已发出的请求仍需确认与对账',
  ])
  includesAll(section('overview'), ['独立复核报价时效', '扣除手续费与滑点后的净 RR', '高评分不能绕过'])
})

test('evolution is configurable daily review-only, never a six-hour memory promotion loop', () => {
  for (const id of ['overview', 'self_evolution']) {
    includesAll(section(id), ['配置时间', '默认每日北京时间 20:00', 'review-only', '当前账户已平仓台账', '不自动推广'])
    assert.match(section(id), /不(?:自动)?覆盖现有运行记忆/)
  }
  includesAll(section('self_evolution'), [
    'NO_CHANGE', '立即复盘', '显式人工修改', 'self_improvement_review.md',
    'data/memory_registry.db', '待证据审核或被拒绝', '旧 Markdown/JSON', '权重或风险参数',
    '复盘成功不代表策略已改善或已应用', 'demo 样本不等于 live 结果',
  ])
  includesAll(section('prompt_studio'), ['注入当前运行记忆', '新复盘候选不会自动进入该插槽'])
  assert.doesNotMatch(text, /每\s*(?:6|六)\s*小时|每日\s*4\s*次|高频弹性调度|时效覆盖与动态经验淘汰|同步生成并即刻注入/)
  assert.doesNotMatch(text, /(?<!不)自动(?:更新|覆盖)\s*(?:data\/)?AI_TRADING_MEMORY\.md/)
})

test('account candidates, binding and activation are separate guarded operations', () => {
  includesAll(section('dashboard'), [
    '分别管理 demo、live 与资讯绑定', '保存候选连接不会切换当前交易账户', '绑定与激活环境是独立操作',
    '核验目标身份与读取能力', 'OAuth 读取成功不等于交易授权', '当前不能绑定自动交易',
    '显式确认', '交易锁', '无持仓、挂单或保护单', '无未确认交易意图',
    '核验失败则拒绝切换', '不会自动平仓或撤单', '手动平仓开关也不关闭独立止损与持仓风控',
  ])
})

test('docs promise neither profit, zero bugs nor live safety from demo or masking', () => {
  includesAll(section('overview'), ['不保证盈利或零 bug', 'demo 验证不等于 live 安全'])
  includesAll(section('dashboard'), ['demo 验证不等于 live 安全', '展示不等于保护必然生效'])
  includesAll(section('faq'), ['不能保证绝不泄露', '不是完整安全边界', '最小权限'])
  assert.doesNotMatch(text, /彻底消除单一模型|100%\s*OCO|绝不会。|全本地无害化存储|已严密阻断任何凭证提交/)
  assert.doesNotMatch(text, /(?<!不)保证(?:盈利|零\s*bug)|稳赚|零风险/i)
})

test('entry and exit documentation matches observed targets and unified preset protection', () => {
  includesAll(section('entry_exit'), ['closed-candle-plans-v3','8 根 15M','1.5 ATR','12 根已收盘小时 K 线','target_observation',
    '不使用“3 × 止损距离”','position-exits-v1','6 小时','4 小时','2.5 ATR','1.8 ATR','0.15 ATR','0.10 ATR',
    '估算往返成本 × 1.5','0.8R','UPDATE_SL','300 秒','已有更紧止损始终保留','last_verified','conservative_fallback',
    'position_trackers.json','不会静默把 small300 切成 standard','active_profile','exit_evidence','lastExitAttempt','不增加下单、撤单或平仓的盲目重试'])
  assert.ok(source.includes("id: 'entry_exit'"))
  assert.doesNotMatch(section('entry_exit'),/3R.*保证.*净.*2/)
})
test('documented exit table stays in sync with the pure runtime presets', () => {
  const policy=readFileSync(new URL('../../scripts/exit_policy.py',import.meta.url),'utf8')
  const configs=['standard','small300'].map(id=>policy.match(new RegExp(`'${id}': \\{([\\s\\S]*?)\\}`))?.[1])
  assert.ok(configs.every(Boolean))
  const rows=[...template.matchAll(/<tr><th scope="row">([^<]+)<\/th><td>([^<]+)<\/td><td>([^<]+)<\/td><\/tr>/g)]
  const fields=['time_stop_seconds','time_stop_profit_atr','tier1_breakeven_atr','tier2_lock_atr','tier1_floor_atr','tier2_floor_atr','kinetic_peak_atr','kinetic_pullback_atr']
  assert.equal(rows.length,fields.length)
  fields.forEach((field,index)=>configs.forEach((config,side)=>{
    const value=Number(config.match(new RegExp(`'${field}': ([\\d.]+)`))?.[1])
    assert.equal(parseFloat(rows[index][side+2]),field==='time_stop_seconds'?value/3600:value,field)
  }))
})

test('audit and review reading guides describe actual grouping, evidence and distinct timestamps', () => {
  includesAll(section('dashboard'),['每个标的只出现一次','另 N 项','全部形态检查','静默刷新','3 秒','净敞口','不是 24 小时资金净流入'])
  includesAll(section('self_evolution'),['报告观察、待验证假设、数据缺口','模型自称的“已验证事实”','旧报告未记录',
    '版本发布 / 纳管时间','有效内容变更时间','当前模型输入','版本与来源','基础策略'])
  assert.doesNotMatch(text,/本地改动|尚未上线|尚未部署/)
})
test('documentation and dashboard use the same header and page heading components', () => {
  const dashboard=readFileSync(new URL('../src/views/DashboardView.vue',import.meta.url),'utf8')
  for(const view of [source,dashboard]){
    assert.ok(view.includes("import HeaderBar from '../components/HeaderBar.vue'"))
    assert.ok(view.includes('<HeaderBar />'));assert.ok(view.includes('<PageHeader'))
  }
  assert.ok(source.includes('terminal-shell docs-shell'));assert.ok(source.includes('terminal-main docs-main'))
  assert.ok(!source.includes('<!-- Top Header Navigation (Slim & Clean) -->'))
  assert.ok(source.includes('title="文档目录"'))
  assert.ok(source.includes("prefers-reduced-motion: reduce"))
  const toc=readFileSync(new URL('../src/components/DocsContents.vue',import.meta.url),'utf8')
  for(const token of ['aria-label="文档章节目录"',':aria-current','min-height: 44px','focus-visible'])assert.ok(toc.includes(token),token)
})

test('all chapters, original screenshot order and zoom interactions remain available', () => {
  for (const id of ['overview', 'dashboard', 'council', 'prompt_studio', 'interceptors', 'llm_hub', 'self_evolution', 'deployment', 'faq']) section(id)
  const images = ['dashboard_trading', 'admin_council', 'admin_prompt_studio', 'admin_interceptors', 'admin_llm', 'admin_evolution']
  assert.deepEqual([...template.matchAll(/src="\/images\/([^"/]+)\.png"/g)].map(match => match[1]), images)
  for (const name of images) assert.ok(template.includes(`@click="zoomImage = '/images/${name}.png'"`))
  assert.match(template, /<AppDialog\s+v-if="zoomImage"/)
  assert.ok(template.includes(':src="zoomImage"'))
  assert.ok(template.includes('grid grid-cols-1 md:grid-cols-3'))
  includesAll(section('interceptors'), ['历史截图阈值以当前文字说明为准'])
  includesAll(section('self_evolution'), ['历史截图调度以当前配置为准'])
})
