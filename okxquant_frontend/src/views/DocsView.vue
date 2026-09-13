<script setup lang="ts">
import AppDialog from '../components/ui/AppDialog.vue'
import HeaderBar from '../components/HeaderBar.vue'
import PageHeader from '../components/ui/PageHeader.vue'
import AppBadge from '../components/ui/AppBadge.vue'
import DocsContents from '../components/DocsContents.vue'
import { useClipboard } from '../composables/useClipboard'
const { copyText: copyToClipboard } = useClipboard()
import { ref, nextTick, onMounted, onUnmounted } from 'vue'
import {
  ShieldCheck,
  Cpu,
  FileText,
  Copy,
  Terminal,
  Users,
  Brain,
  TrendingUp,
  Layers,
  ShieldAlert,
  Menu,
  Server,
} from 'lucide-vue-next'


const activeSection = ref('overview')
const mobileMenuOpen = ref(false)
const copiedTag = ref('')
const zoomImage = ref<string | null>(null)

const sections = [
  { id: 'overview', title: '1. 系统架构与量化哲学', icon: TrendingUp },
  { id: 'dashboard', title: '2. 双翼工作台与资产控制舱', icon: Terminal },
  { id: 'council', title: '3. 可选多模型决策委员会 (Council Pro)', icon: Users },
  { id: 'prompt_studio', title: '4. 提示词策略与语义变量插槽', icon: FileText },
  { id: 'interceptors', title: '5. Python 物理拦截插件 (Fail-Closed)', icon: ShieldCheck },
  { id: 'llm_hub', title: '6. 模型连接与 API 协议支持', icon: Cpu },
  { id: 'self_evolution', title: '7. 自进化认知与长期记忆闭环', icon: Brain },
  { id: 'deployment', title: '8. 生产部署与多通道通知', icon: Server },
  { id: 'faq', title: '9. 常见问题解答与风控底线 (FAQ)', icon: ShieldAlert },
  { id: 'entry_exit', title: '10. 入场候选与统一退出规则', icon: Layers },
]

async function copyText(text: string, tag: string) {
  if (!(await copyToClipboard(text))) return
  copiedTag.value = tag
  setTimeout(() => {
    copiedTag.value = ''
  }, 2000)
}

async function scrollToSection(id: string) {
  activeSection.value = id
  mobileMenuOpen.value = false
  await nextTick()
  requestAnimationFrame(() => {
    const el = document.getElementById(id)
    if (el) el.scrollIntoView({ behavior: window.matchMedia('(prefers-reduced-motion: reduce)').matches ? 'instant' : 'smooth', block: 'start' })
  })
}

// Scroll spy
function onScroll() {
  const headerBottom = document.querySelector('.terminal-header')?.getBoundingClientRect().bottom || 68
  const scrollPadding = parseFloat(getComputedStyle(document.documentElement).scrollPaddingTop) || 0
  for (let i = sections.length - 1; i >= 0; i--) {
    const el = document.getElementById(sections[i].id)
    const anchorLine = el ? Math.max(headerBottom, scrollPadding + (parseFloat(getComputedStyle(el).scrollMarginTop) || 0)) + 2 : headerBottom
    if (el && el.getBoundingClientRect().top <= anchorLine) {
      activeSection.value = sections[i].id
      break
    }
  }
}

onMounted(() => {
  window.addEventListener('scroll', onScroll, { passive: true })
})

onUnmounted(() => {
  window.removeEventListener('scroll', onScroll)
})
</script>

<template>
  <div class="terminal-shell docs-shell">
    <HeaderBar />
    <main class="terminal-main docs-main">
      <PageHeader title="使用文档" description="按功能查阅账户连接、决策证据、持仓保护与运行记忆。" eyebrow="工作空间 / 文档中心">
        <template #actions>
          <AppBadge tone="neutral">v0.1.0</AppBadge>
          <button type="button" class="ui-button ui-button--secondary docs-menu-button" aria-haspopup="dialog" :aria-expanded="mobileMenuOpen" @click="mobileMenuOpen = true" data-docs-menu>
            <Menu class="size-4" aria-hidden="true" />章节目录
          </button>
        </template>
      </PageHeader>
      <nav class="docs-shortcuts" aria-label="常用功能说明">
        <button type="button" @click="scrollToSection('entry_exit')">入场与退出规则</button>
        <button type="button" @click="scrollToSection('dashboard')">决策与等待审计</button>
        <button type="button" @click="scrollToSection('self_evolution')">复盘与运行记忆</button>
      </nav>
      <div class="docs-layout">
        <aside class="docs-sidebar" aria-label="章节索引">
          <p class="docs-sidebar__label">文档目录</p>
          <DocsContents :sections="sections" :active="activeSection" @select="scrollToSection" />
        </aside>
        <article class="docs-content min-w-0 space-y-14">
        <!-- 1. 系统概览与量化哲学 -->
        <section id="overview" class="space-y-4 pt-2">
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 01</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              系统架构与量化哲学
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            <strong>OKXQuant</strong>
            是一套专为高波动加密货币（Crypto）打造的<strong>机构级全自动波段量化决策与执行系统</strong>。系统依托
            OKX 交易所官方 REST/WebSocket V5 生产 API 与 @okx_ai
            官方交易底座，运行在严格的北京时间（UTC+8）自然日财务基准之上，聚焦 1H~4H
            大级别顺势波段，以<strong>“证据优先、宁缺毋滥、Fail-Closed”</strong
            >为风控目标。系统不保证盈利或零 bug；demo 验证不等于 live 安全，实盘仍需独立核验执行与保护闭环。
          </p>

          <!-- 4 Core Pillars Grid -->
          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3.5 pt-2">
            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div
                class="flex items-center space-x-2 text-xs font-mono font-bold"
                style="color: var(--color-up)"
              >
                <ShieldCheck class="w-4 h-4" />
                <span>Fail-Closed 物理硬拦截</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                风控不只依赖 LLM 提示词。Python 插件默认检查 4H 顺势、评分合法性、1H ADX 与价格盈亏比；
                最终执行层还会独立复核报价时效、扣除手续费与滑点后的净 RR 及风险预算，高评分不能绕过这些 gate。
              </p>
            </div>

            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div
                class="flex items-center space-x-2 text-xs font-mono font-bold"
                style="color: var(--color-brand)"
              >
                <Users class="w-4 h-4" />
                <span>多模型决策委员会 (Council Pro)</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                默认单脑决策，Council 为可选模式。启用后，各交易员并发提交方案，由 CIO 模型终审；不是数值加权投票，也不提供额外交易授权。
              </p>
            </div>

            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div
                class="flex items-center space-x-2 text-xs font-mono font-bold"
                style="color: var(--text-main)"
              >
                <Layers class="w-4 h-4" />
                <span>语义数据插槽提示词系统</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                全网快讯、自进化心法、多标的数理矩阵等动态数据抽象为标准语义变量插槽（如
                <code>&#123;&#123;news_intelligence&#125;&#125;</code
                >），支持模块自由解耦与策略方案一键导入导出。
              </p>
            </div>

            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div
                class="flex items-center space-x-2 text-xs font-mono font-bold"
                style="color: var(--color-warn)"
              >
                <Brain class="w-4 h-4" />
                <span>自进化认知复盘闭环</span>
              </div>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                自进化按配置时间调度，默认每日北京时间 20:00，基于当前账户已平仓台账生成 review-only 复盘与候选。
                候选不自动推广，也不覆盖现有运行记忆；纳管后统一读取 <code>data/memory_registry.db</code> 的已发布版本，不自动修改权重或风险参数。
              </p>
            </div>
          </div>
        </section>

        <!-- 2. 双翼工作台与资产控制舱 -->
        <section
          id="dashboard"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 02</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              双翼量化工作台与资产控制舱
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            前台终端采用机构级**「双翼量化工作台（Dual-Wing Workstation）」**架构，支持在宽屏下的
            62% : 38% 双翼并行视角与全景纵向视角间一键切换：
          </p>

          <div class="grid grid-cols-1 md:grid-cols-2 gap-3.5 pt-1 text-xs font-mono">
            <div
              class="p-3.5 rounded-xl border space-y-1.5"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="font-bold text-sm" style="color: var(--text-main)">
                左翼：主控与操盘中心 (62%)
              </div>
              <p style="color: var(--text-muted)">
                •
                <strong>4 单元独立 Bento 资产控制舱</strong
                >：官方总权益、基准净盈亏水线、今日已结、持仓净盈亏分离解耦。<br />
                •
                <strong>高密度交互式操盘台 (Tactical Desk)</strong
                >：分段查看当前 demo/live 环境的持仓与限价挂单池，支持币种快速筛选与云端 OCO 止损覆盖状态核验；展示不等于保护必然生效。
              </p>
            </div>
            <div
              class="p-3.5 rounded-xl border space-y-1.5"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="font-bold text-sm" style="color: var(--text-main)">
                右翼：六币因果动力学与微结构雷达 (38%)
              </div>
              <p style="color: var(--text-muted)">
                • <strong>微积分物理动能指标</strong>：实时计算一阶速度 $v$、二阶加速度
                $a$、三阶冲击 $j$ 与 ADX 趋势动量。<br />
                •
                <strong>聪明钱微结构</strong
                >：展示官方聪明钱多头比例与净敞口（多头名义金额减空头名义金额），不是 24 小时资金净流入。资讯走独立资讯绑定，不会切换 demo/live 交易环境；缺失数据保留未知标记。点击卡片可查看数理推演与当轮 Prompt。
              </p>
            </div>
          </div>

          <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
            <strong>账户切换：</strong>在后台「账户连接」分别管理 demo、live 与资讯绑定；保存候选连接不会切换当前交易账户，绑定与激活环境是独立操作。
            交易绑定需核验目标身份与读取能力；OAuth 读取成功不等于交易授权，当前不能绑定自动交易。
            更换交易账户或切换环境需显式确认，受交易锁约束，并核验相关账户无持仓、挂单或保护单、无未确认交易意图；核验失败则拒绝切换，不会自动平仓或撤单。
            demo 验证不等于 live 安全；后台手动平仓开关也不关闭独立止损与持仓风控。
          </p>

          <!-- Screenshot Card -->
          <div
            class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <span
                >实机截图 · 双翼量化工作台 (左翼操盘台 + 右翼六币微积分雷达 + 亮暗双模切换)</span
              >
              <span class="font-bold" style="color: var(--color-brand)">点击图片放大</span>
            </div>
            <img
              src="/images/dashboard_trading.png"
              alt="双翼量化工作台全景"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/dashboard_trading.png'"
            />
          </div>
          <div class="docs-feature-note" data-docs-audit-layout>
            <h3>如何阅读决策与等待审计</h3>
            <p>面板按“本轮结论 → 标的主要阻碍 → 完整证据”组织。每个标的只出现一次；默认显示做多、做空的主要原因，同方向还有其他检查时标记“另 N 项”。点击该行的“详情”，可查看全部形态检查、程序方案、模型选择、净 R:R、重审条件及前轮复查。</p>
            <ul>
              <li>顶部保留审查、候选和等待已审数量；待补全、执行未通过等异常有值才展示，缺失数据不显示成零。</li>
              <li>连续统计分别记录“无程序草案、模型全 WAIT、审计异常”和“有草案且审计通过后全 WAIT”；程序草案不是模型独立候选，更不是已成交订单。旧的最终 WAIT 计数保留，包含校验失败，不能解释为每轮都正常审查通过。新分类从首个完成周期开始累计，不倒推旧数据。</li>
              <li>宏观方向限制使用 <code>macro_constraint</code>：4H 多头规则限制做空、4H 空头规则限制做多；必须引用本标的宏观事实。<code>position_constraint</code> 只用于真实非零反向持仓或同向浮亏，空仓不能使用。输入 <code>wait_constraints</code> 明确每个方向允许的类别及前轮复查要求。</li>
              <li>对已返回 WAIT 但审计不完整的输出，正常决策任务至多增加一次 20 秒、单次 HTTP 尝试的受限纠错。纠错只能修正指定 WAIT 的分类、证据和复查字段；不能改成开仓，不能修改其他已通过决策、持仓管理或撤单指令，仍须通过同一校验器。失败后保留不完整状态，不无限循环。若本轮已有有效开仓候选、持仓风控或撤单指令，则不追加纠错等待，优先进入原执行核验。</li>
              <li>初次失败内容、错误和纠错结果写入同账户证据库的 <code>wait_audit_repair</code> 记录；超出长度限制时明确保留片段与指纹。面板详情显示初次错误与纠错状态，原始文本不默认发布到公开面板。纠错调用单独记录用量。</li>
              <li>这些诊断与纠错不改变入场触发、盈亏比或风险预算，也不是强制开仓倒计时。审计通过仅表示证据和条件可核验，不代表已经证明没有交易优势。</li>
              <li>审计不完整、草案生成错误、执行拒绝直接显示原因，不会被精简布局隐藏。程序草案与模型选择都不等于已下单或成交。</li>
              <li>执行记录与审计解释按需展开，环境限制单独标注。前台和后台复用同一面板；静默刷新保留用户已展开的内容。</li>
            </ul>
            <p>桌面和手机使用同一组数据，支持明暗主题与键盘展开。监控仍以 3 秒周期请求完整快照；刷新延迟通过“数据更新延迟”状态提示，不用常驻错误弹窗打断阅读。</p>
          </div>
        </section>

        <!-- 3. 多模型决策委员会 -->
        <section
          id="council"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 03</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              多模型决策委员会 (Council Pro)
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            系统默认单脑，Council 默认关闭、可按需启用。启用后采用<strong>多交易员提案与 CIO 模型终审</strong>，
            各席位审查同一份统一证据；多模型意见不是真实证据或胜率证明，也不能保证消除幻觉。
          </p>

          <div
            class="rounded-xl border p-4 text-xs font-mono space-y-2.5 shadow-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div class="font-bold text-xs" style="color: var(--text-main)">
              委员会实际决策流程（非确定性投票）：
            </div>
            <div class="grid grid-cols-1 md:grid-cols-3 gap-2.5 pt-1">
              <div
                class="p-2.5 rounded-lg border"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
              >
                <div class="font-bold" style="color: var(--color-down)">
                  1. 并发交易提案
                </div>
                <div class="text-[11px] mt-1" style="color: var(--text-muted)">
                  已启用的交易员基于统一输入提交支持证据、反证与失效条件，意见仅供终审参考。
                </div>
              </div>
              <div
                class="p-2.5 rounded-lg border"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
              >
                <div class="font-bold" style="color: var(--color-brand)">
                  2. CIO 模型终审
                </div>
                <div class="text-[11px] mt-1" style="color: var(--text-muted)">
                  CIO 汇总审查提案，可全部 WAIT；席位权重不构成数值加权投票，strict / weighted / aggressive 配置名不是确定性表决机制。
                </div>
              </div>
              <div
                class="p-2.5 rounded-lg border"
                style="background-color: var(--bg-card-subtle); border-color: var(--border-subtle)"
              >
                <div class="font-bold" style="color: var(--color-up)">
                  3. 统一契约与执行 gate
                </div>
                <div class="text-[11px] mt-1" style="color: var(--text-muted)">
                  终审输出仍须通过统一证据契约、最终报价与净 RR gate，任何席位均无绕过独立风控的权限。
                </div>
              </div>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div
            class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <span
                >实机截图 · 多模型决策委员会控制台 (席位动态启停、思考强度微调与现场辩论测试)</span
              >
              <span class="font-bold" style="color: var(--color-brand)">点击图片放大</span>
            </div>
            <img
              src="/images/admin_council.png"
              alt="多模型委员会控制台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/admin_council.png'"
            />
          </div>
        </section>

        <!-- 4. 提示词策略与变量插槽 -->
        <section
          id="prompt_studio"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 04</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              提示词策略与语义变量插槽
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            提示词策略工作室支持对四大核心管线（交易 System、交易
            User、自进化 System、自进化 User）进行可视化定制。引入<strong
              >语义变量插槽（Semantic Slots）</strong
            >引擎；自定义偏好不能覆盖统一证据契约与独立风控：
          </p>

          <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
            单脑与 Council 使用统一证据契约 trading-evidence-v1：支持证据、反证与失效条件必须可核验。
            程序基于闭合（已收盘）的 15M/1H K线生成入场草案；模型可审查草案，也可提出符合契约的独立方案，草案不是下单授权。
            选用 candidate_id 后不得改写草案价格与失效点；最终报价时效、触发有效性、净 RR 与风险预算仍须通过执行 gate。
          </p>

          <!-- Variable Table -->
          <div
            class="rounded-xl border overflow-x-auto shadow-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <table class="w-full text-left text-xs font-mono whitespace-nowrap">
              <thead>
                <tr
                  class="border-b"
                  style="
                    border-color: var(--border-subtle);
                    background-color: var(--bg-card-subtle);
                    color: var(--text-muted);
                  "
                >
                  <th class="p-3 font-bold">变量占位符</th>
                  <th class="p-3 font-bold">数据分类</th>
                  <th class="p-3 font-bold">注入内容与实战用途</th>
                </tr>
              </thead>
              <tbody class="divide-y" style="border-color: var(--border-subtle)">
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-brand)">
                    &#123;&#123;news_intelligence&#125;&#125;
                  </td>
                  <td class="p-3" style="color: var(--text-main)">全网快讯</td>
                  <td class="p-3" style="color: var(--text-muted)">
                    注入全网最新重大突发要闻、黑天鹅熔断状态与宏观情绪标签
                  </td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-up)">
                    &#123;&#123;trading_memory&#125;&#125;
                  </td>
                  <td class="p-3" style="color: var(--text-main)">自进化心法</td>
                  <td class="p-3" style="color: var(--text-muted)">
                    注入当前运行记忆；新复盘候选不会自动进入该插槽
                  </td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--text-main)">
                    &#123;&#123;market_matrix&#125;&#125;
                  </td>
                  <td class="p-3" style="color: var(--text-main)">微积分数理</td>
                  <td class="p-3" style="color: var(--text-muted)">
                    注入 6 币种最新价、微积分动力学 (v/a/j)、1H ADX 与聪明钱净敞口
                  </td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-warn)">
                    &#123;&#123;account_positions&#125;&#125;
                  </td>
                  <td class="p-3" style="color: var(--text-main)">账户敞口</td>
                  <td class="p-3" style="color: var(--text-muted)">
                    注入在途持仓方向、均价、标记价、未结浮盈 UPL 及云端止损防线
                  </td>
                </tr>
                <tr class="hover:bg-[var(--bg-card-hover)] transition-colors">
                  <td class="p-3 font-bold" style="color: var(--color-brand)">
                    &#123;&#123;pending_orders&#125;&#125;
                  </td>
                  <td class="p-3" style="color: var(--text-main)">挂单池</td>
                  <td class="p-3" style="color: var(--text-muted)">
                    注入在途 Maker 限价挂单价格、张数及被动成交状态
                  </td>
                </tr>
              </tbody>
            </table>
          </div>

          <!-- Screenshot Card -->
          <div
            class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <span
                >实机截图 · 提示词策略工作室
                (语义插槽工具条、多方案打包导入导出与实发效果实时预览)</span
              >
              <span class="font-bold" style="color: var(--color-brand)">点击图片放大</span>
            </div>
            <img
              src="/images/admin_prompt_studio.png"
              alt="提示词策略工作室"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/admin_prompt_studio.png'"
            />
          </div>
        </section>

        <!-- 5. Python 物理拦截插件 -->
        <section
          id="interceptors"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 05</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              Python 物理拦截插件中心 (Fail-Closed)
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            发单执行层遵循 <strong>Fail-Closed</strong>：校验异常或风控拒绝时不授权新开仓，候选可降级 WAIT；
            已发出的请求仍需确认与对账，不能把 WAIT 当作撤单或平仓成功。以下四个内置插件默认启用，另有默认关闭的自定义示例；最终执行 gate 独立复核。
          </p>

          <div class="grid grid-cols-1 sm:grid-cols-2 gap-3 pt-1">
            <div
              class="p-3.5 rounded-xl border space-y-1.5 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="docs-rule-heading flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main)"
                  >01_macro_trend_filter.py</span
                >
                <span
                  class="px-2 py-0.2 rounded text-[9px] font-bold border"
                  style="
                    background-color: var(--color-up-bg);
                    color: var(--color-up);
                    border-color: var(--color-up-border);
                  "
                  >顺势铁律</span
                >
              </div>
              <p class="text-[11px]" style="color: var(--text-muted)">
                4H 宏观多头通道严禁摸顶开空；4H 空头通道严禁接飞刀做多。
              </p>
            </div>

            <div
              class="p-3.5 rounded-xl border space-y-1.5 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="docs-rule-heading flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main)"
                  >02_confidence_gatekeeper.py</span
                >
                <span
                  class="px-2 py-0.2 rounded text-[9px] font-bold border"
                  style="
                    background-color: var(--color-up-bg);
                    color: var(--color-up);
                    border-color: var(--color-up-border);
                  "
                  >评分合法性</span
                >
              </div>
              <p class="text-[11px]" style="color: var(--text-muted)">
                入场 score / confidence 仅校验合法性：须为 0~100 的有限数值且非布尔值，是未校准研究评分而非胜率；无 75/80 硬门槛，也无 Meme 特殊分数线。
                AI 主动平仓（CLOSE_MARKET）的 85 门槛仍单独保留，不适用于入场，也不限制独立止损与保护退出。
              </p>
            </div>

            <div
              class="p-3.5 rounded-xl border space-y-1.5 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="docs-rule-heading flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main)"
                  >03_adx_volatility_filter.py</span
                >
                <span
                  class="px-2 py-0.2 rounded text-[9px] font-bold border"
                  style="
                    background-color: var(--color-up-bg);
                    color: var(--color-up);
                    border-color: var(--color-up-border);
                  "
                  >猴市过滤</span
                >
              </div>
              <p class="text-[11px]" style="color: var(--text-muted)">
                默认 ADX 插件对低于18的普通信号保持拦截；已通过证据契约、可重建的收盘触发程序计划可以继续接受最终风控。ADX不是胜率，也不是单独的下单授权。
              </p>
            </div>

            <div
              class="p-3.5 rounded-xl border space-y-1.5 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <div class="docs-rule-heading flex items-center justify-between">
                <span class="font-bold text-xs font-mono" style="color: var(--text-main)"
                  >04_risk_reward_gatekeeper.py</span
                >
                <span
                  class="px-2 py-0.2 rounded text-[9px] font-bold border"
                  style="
                    background-color: var(--color-up-bg);
                    color: var(--color-up);
                    border-color: var(--color-up-border);
                  "
                  >价格几何 2.0R</span
                >
              </div>
              <p class="text-[11px]" style="color: var(--text-muted)">
                该插件检查入场、止盈与止损的价格几何 RR ≥ 2.0；最终执行层另按手续费、滑点和最终价格重算净 RR，默认至少 2.0，并检查风险预算。
              </p>
            </div>
          </div>

          <!-- Screenshot Card -->
          <div
            class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <span
                >实机截图 · 物理拦截插件中心
                (4H顺势、评分合法性、ADX过滤与现场沙箱测试；历史截图阈值以当前文字说明为准)</span
              >
              <span class="font-bold" style="color: var(--color-brand)">点击图片放大</span>
            </div>
            <img
              src="/images/admin_interceptors.png"
              alt="物理拦截插件中心"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/admin_interceptors.png'"
            />
          </div>
        </section>

        <!-- 6. 模型连接与协议格式 -->
        <section
          id="llm_hub"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 06</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              模型连接与 API 协议支持
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            后台「模型连接」模块实现了跨厂商无缝兼容，支持纳管市场上所有主流大模型及其代理网关：
          </p>

          <div
            class="rounded-xl border p-4 text-xs font-mono space-y-2 shadow-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div class="font-bold text-xs" style="color: var(--text-main)">
              支持的 API 协议标准：
            </div>
            <ul class="space-y-1.5 list-disc list-inside" style="color: var(--text-muted)">
              <li>
                <strong>OpenAI Chat</strong> (<code>/chat/completions</code>)：兼容 DeepSeek
                R1/V3、OpenAI GPT-4o、Qwen 2.5、GLM-4 等绝大部分供应商与 OneAPI/NewAPI 聚合网关。
              </li>
              <li>
                <strong>OpenAI Responses</strong> (<code>/responses</code>)：支持 OpenAI o3-mini、o1
                等新一代推理协议标准。
              </li>
              <li>
                <strong>Claude Messages</strong> (<code>/messages</code>)：原生适配 Anthropic Claude
                3.7 Sonnet / Haiku 及其 Thinking 协议。
              </li>
              <li>
                <strong>思考强度自适应 (Reasoning Effort)</strong>：支持在
                HIGH（长链推理）、MEDIUM、LOW 及 AUTO
                间自由微调，并由执行器自动转换或剥离非兼容参数。
              </li>
            </ul>
          </div>

          <!-- Screenshot Card -->
          <div
            class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <span
                >实机截图 · 模型连接控制台
                (全局生效模型卡片、预设一键填入、连通性与思考链时延诊断)</span
              >
              <span class="font-bold" style="color: var(--color-brand)">点击图片放大</span>
            </div>
            <img
              src="/images/admin_llm.png"
              alt="模型连接控制台"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/admin_llm.png'"
            />
          </div>
        </section>

        <!-- 7. 自进化认知与长期记忆闭环 -->
        <section
          id="self_evolution"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 07</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              自进化认知与长期记忆闭环 (review-only)
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            OKXQuant 的自进化是<strong>review-only 复盘与候选审核流程</strong>：按配置时间运行，
            默认每日北京时间 20:00，读取当前账户已平仓台账；demo 样本不等于 live 结果，复盘成功不代表策略已改善或已应用。
          </p>

          <div class="space-y-2 text-xs font-sans" style="color: var(--text-muted)">
            <div>
              • <strong style="color: var(--text-main)">可配置调度</strong>：按保存的复盘时间触发，也支持立即复盘；无新证据可返回 NO_CHANGE，不承诺每次产生新心法；
            </div>
            <div>
              •
              <strong style="color: var(--text-main)">版本化候选审核面板</strong
              >：超级管理员先提交新增、修订或停用候选，核验成交证据、说明理由并确认后发布。显式人工修改也必须走审核发布，不支持直接覆盖运行文件；
            </div>
            <div>
              • <strong style="color: var(--text-main)">报告、候选与运行记忆分离</strong>：复盘报告写入
              <code>self_improvement_review.md</code>，候选、发布历史与当前有效版本统一保存于 <code>data/memory_registry.db</code>。
              候选待证据审核或被拒绝，不自动推广，不自动覆盖现有运行记忆、权重或风险参数。旧 Markdown/JSON 仅作为兼容迁移来源，纳管后不再作为生效入口。
            </div>
            <div>
              • <strong style="color: var(--text-main)">自动证据反馈</strong>：剔除未结算、未知盈亏、账户不匹配或重复样本；只通过真实成交与原始决策精确关联开仓快照，按实际记忆、代码与执行预设追踪费用后结果。未关联不等于零值，分组盈亏不等于策略因果优势。进一步自动推广需要独立前向对照、灰度和回退验证，目前不自动启用。
            </div>
          </div>

          <!-- Screenshot Card -->
          <div
            class="rounded-2xl border p-2 sm:p-3 overflow-hidden shadow-xl group"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div
              class="text-[11px] font-mono px-2 py-1 flex items-center justify-between border-b mb-2"
              style="border-color: var(--border-subtle); color: var(--text-muted)"
            >
              <span
                >实机截图 · 自进化认知配置与长期心法记忆库
                (复盘状态、实战心法CRUD管理与立即复盘；历史截图调度以当前配置为准)</span
              >
              <span class="font-bold" style="color: var(--color-brand)">点击图片放大</span>
            </div>
            <img
              src="/images/admin_evolution.png"
              alt="自进化配置与实战心法面板"
              class="w-full rounded-xl cursor-zoom-in group-hover:opacity-95 transition-opacity"
              @click="zoomImage = '/images/admin_evolution.png'"
            />
          </div>
          <div class="docs-feature-note" data-docs-review-layout>
            <h3>复盘结果与运行记忆分别阅读</h3>
            <p>策略复盘页在宽屏并排展示“最新策略复盘”和“运行记忆”，手机端上下排列。报告区先展示任务状态、最近成功报告、样本量、样本胜率及本次变更建议；待审核与审核未通过数量独立呈现。</p>
            <ul>
              <li>关键发现分为“报告观察、待验证假设、数据缺口”；默认展示原文摘要节选，展开后保留全部原文。模型自称的“已验证事实”不等于程序已独立核验。</li>
              <li>证据核对、改进建议、完整报告和任务详情按需展开。旧报告没有结构化证据时显示“旧报告未记录”，不会补造历史快照或伪装成已验证改进。</li>
              <li>复盘失败、超时、账户范围不匹配与运行记忆不可用仍直接提示；上次成功报告可以保留，但不会冒充最近尝试成功。</li>
              <li><strong>最近成功报告时间、版本发布 / 纳管时间、有效内容变更时间</strong>含义不同：纳管可以建立版本快照而不改变模型输入，NO_CHANGE 也不会为了推进日期而修改记忆。</li>
              <li>运行记忆优先展示版本、内容最近变更和已审核启用规则数量。“当前模型输入”可查看实际完整文本，“历史兼容上下文”不是新审批规则；完整内容指纹位于“版本与来源”。</li>
              <li>显示 0 条已审核启用规则不代表基础策略停止运行，也不表示自动启用旧心法。候选提交、审核发布和回滚仍使用原有权限、证据与确认流程。</li>
            </ul>
          </div>
        </section>

        <!-- 8. 生产部署与多通道通知 -->
        <section
          id="deployment"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 08</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              生产部署与多通道通知告警
            </h2>
          </div>

          <p class="text-xs sm:text-sm leading-relaxed font-sans" style="color: var(--text-muted)">
            系统支持在标准 Linux (Ubuntu/Debian) 云服务器上秒级开箱即用：
          </p>

          <div
            class="rounded-xl border p-4 text-xs font-mono space-y-2 shadow-xs"
            style="background-color: var(--bg-card); border-color: var(--border-subtle)"
          >
            <div class="flex items-center justify-between" style="color: var(--text-muted)">
              <span>极速启动命令序列:</span>
              <button
                @click="
                  copyText(
                    'git clone [your-repository-url].git\ncd OKXQuant\npip install -r requirements.txt\n./scripts/start_standalone.sh',
                    'deploy_cmd',
                  )
                "
                class="flex items-center space-x-1 cursor-pointer hover:underline"
                style="color: var(--color-brand)"
              >
                <Copy class="w-3 h-3" />
                <span>{{ copiedTag === 'deploy_cmd' ? '已复制命令' : '复制命令' }}</span>
              </button>
            </div>
            <pre
              class="p-3 rounded-lg overflow-x-auto leading-relaxed border"
              style="
                background-color: var(--bg-card-subtle);
                border-color: var(--border-subtle);
                color: var(--color-up);
              "
            >
git clone [your-repository-url].git
cd OKXQuant
pip install -r requirements.txt
./scripts/start_standalone.sh</pre
            >
          </div>

          <div class="text-xs space-y-1.5 font-sans" style="color: var(--text-muted)">
            <p><strong style="color: var(--text-main)">支持的告警通知渠道：</strong></p>
            <p>
              1.
              <strong>企业微信 Webhook</strong>：开仓、平仓、止盈止损移动、自进化报告实时推送到群；
            </p>
            <p>
              2. <strong>Telegram Bot</strong>：支持自定义 API Base 反向代理，国内海外无障碍推送；
            </p>
            <p>
              3. <strong>QQ 机器人频道</strong>：支持腾讯官方开放平台 AppID 与 ClientSecret
              私聊推送；
            </p>
            <p>
              4. <strong>通用 Webhook</strong>：支持飞书、钉钉、Discord 及私有运维告警服务无缝对接。
            </p>
          </div>
        </section>

        <!-- 9. 常见问题解答与风控底线 (FAQ) -->
        <section
          id="faq"
          class="space-y-4 pt-6 border-t"
          style="border-color: var(--border-subtle)"
        >
          <div class="flex items-center space-x-2">
            <span
              class="px-2.5 py-0.5 rounded text-[11px] font-mono font-bold border"
              style="
                background-color: var(--color-brand-bg);
                color: var(--color-brand);
                border-color: var(--color-brand-border);
              "
              >CHAPTER 09</span
            >
            <h2
              class="text-xl sm:text-2xl font-black tracking-wide"
              style="color: var(--text-main)"
            >
              常见问题解答与风控底线 (FAQ)
            </h2>
          </div>

          <div class="space-y-3">
            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <h3 class="text-sm font-bold" style="color: var(--text-main)">
                Q1: 为什么策略推演经常输出 WAIT？是系统出故障了吗？
              </h3>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                不一定是故障。<strong>WAIT 表示本轮不授权该候选开仓</strong
                >。候选未成立、证据或数据不合格、默认趋势/ADX 插件拦截、最终报价或净 RR 不通过、风险预算不足均可能导致等待。
                应查看本轮审计原因；入场评分没有 75/80 硬门槛，WAIT 也不代表已有持仓或挂单已清空。
              </p>
            </div>

            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <h3 class="text-sm font-bold" style="color: var(--text-main)">
                Q2: 我的 OKX API Key 和大模型密钥会泄露吗？
              </h3>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                不能保证绝不泄露。后台对凭据采用掩码展示（如 <code>sk-***abcd</code>），
                但掩码与 <code>.gitignore</code> 都不是完整安全边界；仍需限制主机与备份访问、使用最小权限并妥善保管凭据。
              </p>
            </div>

            <div
              class="p-4 rounded-xl border space-y-2 shadow-xs"
              style="background-color: var(--bg-card); border-color: var(--border-subtle)"
            >
              <h3 class="text-sm font-bold" style="color: var(--text-main)">
                Q3: 如何将策略分享给他人，或从策略广场导入？
              </h3>
              <p class="text-xs leading-relaxed" style="color: var(--text-muted)">
                在后台「提示词策略」页面点击「导出策略方案」即可下载标准
                <code>.json</code> 策略包；在另一台服务器上点击「导入策略」，系统将自动校验 JSON
                结构与变量合法性并即刻装载生效。
              </p>
            </div>
          </div>
        </section>

        <section id="entry_exit" class="space-y-4 pt-6 border-t" style="border-color:var(--border-subtle)">
          <div class="flex flex-wrap items-center gap-2">
            <span class="docs-chapter">CHAPTER 10</span>
            <h2 class="text-xl sm:text-2xl font-semibold">入场候选与统一退出规则</h2>
          </div>
          <div class="docs-feature-note" data-docs-json-contract>
            <h3>模型 JSON 失败与历史审计展示</h3>
            <p>严格解析拒绝截断、重复键、非有限数及供应商声明未完成的响应，记录错误位置和内容指纹。契约格式失败最多追加一次完整重新生成，预算 20 秒且仅一次 HTTP 请求，不叠加 WAIT 纠错，也不增加交易写入重试。</p>
            <p>重新生成仍需通过完整输出契约和实时风控。失败不会复用旧指令；页面将上次审计标为历史，收起时只展示短原因，详情保留完整依据与重审条件。</p>
          </div>
          <div class="docs-feature-note" data-docs-entry-target>
            <h3>趋势回踩先核验方向，目标价必须有观察依据</h3>
            <p>回踩形态必须有同向的已收盘 1H 均线结构；4H 区间分类不代表短周期没有下行压力。单根反弹、减速或聪明钱持仓比例不能替代趋势前提。收盘突破保留独立研究路径，不要求所有指标同时同向。</p>
            <p>交易记录按开仓成交、订单与决策 ID 关联实际形态，不再按多空方向自动标注“顺势”。持仓峰值和本地止损绑定交易所持仓生命周期，重新开仓不能继承上笔止损；现有云端保护不放宽。</p>
            <p><code>closed-candle-plans-v3</code> 使用已收盘 15M / 1H K 线，保留 8 根 15M K 线的突破窗口。初始止损取结构防守位置与 1.5 ATR 波动距离中较宽的一侧；最终数量仍由风险预算、精度和账户约束决定，不会因止损变宽而直接增加风险额度。</p>
            <p>目标价采用此前 12 根已收盘小时 K 线的通道边界。候选中的 <code>target_observation</code> 保存时间框架、价格字段、窗口起止收盘时间和目标价格，不使用“3 × 止损距离”自动推远目标来凑盈亏比。成本后空间不足时记录实际几何与拒绝原因，不凭空假设更远的获利空间。</p>
            <p>目标延伸必须另有明确、经过验证的规则，不能把人为远端目标视为已观察到的行情空间。候选版本或证据改变后，旧 <code>candidate_id</code> 不能授权新订单。既有订单与持仓的止盈止损不会因此被追溯改写。</p>
          </div>
          <div class="docs-feature-note" data-docs-exit-policy>
            <h3>一套预设统一浮盈保护、阶梯锁利与动能退出</h3>
            <p><code>scripts/exit_policy.py</code> 定义 <code>position-exits-v1</code>。下表是工程阈值，不是经过校准的获利概率，也不保证持有到固定 R 倍数。</p>
            <div class="docs-table-scroll" tabindex="0" role="region" aria-label="退出预设参数对照，可横向滚动">
              <table>
                <thead><tr><th scope="col">参数</th><th scope="col">standard</th><th scope="col">small300</th></tr></thead>
                <tbody>
                  <tr><th scope="row">时间退出持有时长门槛</th><td>6 小时</td><td>4 小时</td></tr>
                  <tr><th scope="row">时间退出价格浮盈上界</th><td>0.15 ATR</td><td>0.10 ATR</td></tr>
                  <tr><th scope="row">一级浮盈保护启动</th><td>2.5 ATR</td><td>1.8 ATR</td></tr>
                  <tr><th scope="row">二级锁利启动</th><td>4 ATR</td><td>3 ATR</td></tr>
                  <tr><th scope="row">一级收益保留距离下限</th><td>0.5 ATR</td><td>0.3 ATR</td></tr>
                  <tr><th scope="row">二级收益保留距离下限</th><td>1.5 ATR</td><td>1 ATR</td></tr>
                  <tr><th scope="row">动能退出峰值门槛</th><td>2.5 ATR</td><td>1.8 ATR</td></tr>
                  <tr><th scope="row">动能退出回撤距离</th><td>1.2 ATR</td><td>0.8 ATR</td></tr>
                </tbody>
              </table>
            </div>
            <ul>
              <li>动态退出优先使用采集的 <code>atr_15m</code>，缺失时使用有效 <code>atr</code> 并标记来源；不额外注入价格百分比下限，不混用多个 ATR 启动口径。</li>
              <li>一级启动距离为 <code>max(预设一级 ATR 门槛, 估算往返成本 × 1.5)</code>。不再用 <code>0.8R</code> 取较小值提前启动，成本保护、收益保留比例与阶梯下限在同一门槛后计算。</li>
              <li>动能退出也须满足同一成本启动条件及所选预设的峰值、回撤条件。已有更紧止损始终保留，不能为延长持仓而放宽或重置。</li>
              <li>AI 的 <code>UPDATE_SL</code> 止盈改单共用该启动条件，并核验成本覆盖、行情缓冲及最近 300 秒内的 ATR 观察。ATR 缺失或过期时不发改单，原云端保护保留。</li>
              <li>硬止损、云端 OCO 核验失败后的安全退出，以及独立 AI 平仓校验不等待盈利门槛；不增加下单、撤单或平仓的盲目重试。</li>
            </ul>
          </div>
          <div class="docs-feature-note" data-docs-exit-fallback>
            <h3>预设读取异常时仍保持持仓保护</h3>
            <p>每个持仓的 <code>exitPolicy</code> 保存最近核验的预设 ID、规则版本和执行签名，通过 <code>position_trackers.json</code> 持久化。读取失败时优先沿用同版本的有效快照，标记 <code>last_verified</code>，不会静默把 small300 切成 standard。</p>
            <p>没有有效历史快照时使用明确标记的 <code>conservative_fallback</code>：从现有预设取较早的启动、时间和回撤门槛，以及较高的保护下限。降级不覆盖已核验快照；状态变化时告警，同一异常不持续刷屏，恢复读取后回到 <code>active_profile</code>。新开仓仍受独立风险校验约束。</p>
          </div>
          <div class="docs-feature-note" data-docs-exit-evidence>
            <h3>退出日志对应实际条件</h3>
            <p>交易记录根据交易所毫秒回执计算持仓时长；不足一分钟显示秒数，不再截断成“0分钟”。旧记录可由明确的北京时间开平仓时间补充显示，但不会猜测尚未结算的结束时间。</p>
            <p>新发现持仓若首次保护快照完整但覆盖不足，会以 6 秒读取预算进行一次强制新鲜复查；读取异常不叠加外层重试。复查已归零不再发平仓，身份或数量变化不使用旧快照退出；仍不能确认保护时保留 fail-closed 安全退出。</p>
            <p>平仓请求发送前记录尝试，之后区分已接受、响应未确认、交易所归零观察和已确认结果。响应失败后只做有时限的只读复查，不盲目重复平仓。“已归零”不等于已证明由本次请求平仓，来源仍须交易所订单关联；历史证据缺失不能被强行改成手动或止损。</p>
            <p>时间退出要求持仓时间严格超过预设门槛，且有符号价格浮盈低于所选 ATR 上界；可能是亏损，也可能是小幅盈利，不统一称作“无波动横盘”。巡检说明与交易记录备注使用同一份实际参数。</p>
            <pre class="docs-example">预设 small300：持仓 4.02h &gt; 4h，价格浮盈 -0.200 ATR &lt; 0.1 ATR，时间退出</pre>
            <p><code>exit_evidence</code> 记录预设及来源、实际持有时长、触发门槛、价格浮盈以及 ATR 数值、来源和观察时间。未获平仓确认时保留持仓及 <code>lastExitAttempt</code>，不会记录为成功平仓。日志中的平仓前浮盈不是最终净收益，结算仍以交易所回执和账本为准。</p>
          </div>
        </section>
        </article>
      </div>
    </main>
    <AppDialog :open="mobileMenuOpen" title="文档目录" size="sm" @update:open="mobileMenuOpen = $event">
      <DocsContents :sections="sections" :active="activeSection" @select="scrollToSection" />
    </AppDialog>

    <!-- Image Zoom Modal -->
    <AppDialog
      v-if="zoomImage"
      :open="!!zoomImage"
      title="界面预览"
      size="xl"
      @update:open="
        (open) => {
          if (!open) zoomImage = null
        }
      "
    >
      <img
        :src="zoomImage"
        alt="放大的界面截图"
        class="w-full max-h-[70dvh] object-contain rounded-lg cursor-zoom-out"
        @click="zoomImage = null"
      />
    </AppDialog>
  </div>
</template>

<style scoped>
.docs-main { padding-bottom: 100px; }
.docs-layout { display: grid; grid-template-columns: minmax(0,1fr); gap: 2rem; align-items: start; }
.docs-sidebar { display: none; position: sticky; top: 88px; max-height: calc(100dvh - 110px); overflow-y: auto; min-width: 0; }
.docs-sidebar__label { margin: 0 .75rem .75rem; color: var(--text-muted); font-size: .75rem; font-weight: 600; }
.docs-content { max-width: 1040px; overflow-wrap: anywhere; }
/* The application already reserves 84px via html scroll-padding-top. */
.docs-content > section { scroll-margin-top: 16px; }
.docs-shortcuts { display: flex; flex-wrap: wrap; gap: .5rem; margin: 0 0 1.5rem; }
.docs-shortcuts button { min-height: 44px; padding: .5rem .875rem; border: 1px solid var(--border-subtle); border-radius: .5rem; color: var(--text-muted); background: var(--bg-card); font-size: .8125rem; cursor: pointer; }
.docs-shortcuts button:hover { color: var(--color-brand); border-color: var(--color-brand-border); background: var(--color-brand-bg); }
.docs-shortcuts button:focus-visible, .docs-table-scroll:focus-visible { outline: 2px solid var(--color-brand); outline-offset: 2px; }
.docs-feature-note { padding: 1rem; border: 1px solid var(--border-subtle); border-radius: .75rem; background: var(--bg-card); display: grid; gap: .75rem; font-size: .8125rem; line-height: 1.8; min-width: 0; }
.docs-feature-note h3 { font-weight: 650; font-size: .9375rem; color: var(--text-main); }
.docs-feature-note p, .docs-feature-note ul { color: var(--text-muted); }
.docs-feature-note ul { display: grid; gap: .5rem; list-style: disc; padding-left: 1.25rem; }
.docs-chapter { padding: .25rem .5rem; font-size: .75rem; font-weight: 600; color: var(--color-brand); border: 1px solid var(--color-brand-border); background: var(--color-brand-bg); border-radius: .375rem; }
.docs-table-scroll { min-width: 0; overflow-x: auto; max-width: 100%; border: 1px solid var(--border-subtle); border-radius: .5rem; }
.docs-table-scroll table { min-width: 440px; font-size: .8125rem; }
.docs-table-scroll th, .docs-table-scroll td { padding: .625rem .75rem; }
.docs-example { white-space: pre-wrap; overflow-wrap: anywhere; padding: .875rem; background: var(--bg-card-subtle); border: 1px solid var(--border-subtle); border-radius: .5rem; }
@media (min-width: 1024px) {
  .docs-layout { grid-template-columns: 240px minmax(0,1fr); }
  .docs-sidebar { display: block; }
  .docs-menu-button { display: none; }
}
</style>
