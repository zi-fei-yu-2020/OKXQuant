# A08 公共展示页审查与 UI 重整

- 日期：2026-09-19（北京时间）
- 执行范围：A08，第 7 个实际启动的 agent；沿用调用方指定模型，不调整运行模型配置。
- 约束：共享代码库，仅写本任务授权的公共 components、DashboardView、DocsView、utils、新 A08 测试和本文件。未改 views/admin、router、auth store、admin API/type、backend、开仓条件；未读取或改动真实 .env/data/秘密，未启动服务或浏览器，未提交、推送、部署、委派，也未撤销他人改动。

## 1. 设计方向与覆盖

先读取 frontend-design skill。对象是只读量化监控用户，页面的首要任务是区分“发生过什么、证据是否完整、哪些建议尚未发布”，不是增加操作入口。

沿用已有视觉 tokens，不引入主题或字体依赖：

- 底色 `#f7f8fb`、卡片 `#ffffff`、正文 `#1d2939`、次级正文 `#586579`、品牌蓝 `#4658d7`、警示 `#936016`；深色继续使用现有变量。
- 标题／正文沿用 Inter、系统中文字体；数字／原文沿用 SFMono/Consolas 等宽字体。
- 保留圆角、细分隔线、双栏监控和窄屏堆叠。页面辨识点是明确并列的“复盘报告／待审核”与“已发布运行记忆”，不是装饰性大卡片。

```text
Header：导航 | 提示词入口 | 主题 | 文档 | 控制台
lab：   核心报告 / 样本 / 净费用后胜率  |  已发布记忆 / 版本
        待审核建议 / 关键发现          |  规则与原文入口
        [折叠：决策与等待审计]
```

### 已审且有变更的源文件（20 个）

全部位于 `okxquant_frontend/src/`：

| 区域 | 文件 |
| --- | --- |
| 外壳及文档 | `views/DashboardView.vue`, `views/DocsView.vue`, `components/HeaderBar.vue`, `components/FloatingActions.vue` |
| 复盘和发布 | `components/SelfEvolutionLab.vue`, `components/EvolutionReviewPanel.vue`, `components/EvolutionEvidencePanel.vue`, `components/PublishedMemoryPanel.vue`, `utils/evolutionDisplay.ts` |
| 弹窗及历史 | `components/FactorDetailModal.vue`, `components/AboutModal.vue`, `components/TradesLedger.vue`, `components/AiBrainHistory.vue` |
| 监控与行情 | `components/StrategyTelemetryPanel.vue`, `components/TopHudRibbon.vue`, `components/TacticalDesk.vue`, `components/PositionList.vue`, `components/MarketCandles.vue`, `components/LedgerLogs.vue`, `components/NewsIntelligence.vue` |

### 只读检查、未修改

- 公共组合：`components/InstrumentMatrix.vue`, `PendingOrders.vue`, `DecisionAuditPanel.vue`, `DocsContents.vue`, `CapitalPoolPanel.vue`, `ScenarioShadowPanel.vue`, `NewsConnectionStatus.vue`。
- 共享基础：`components/ui/AppDialog.vue`, `AppButton.vue`, `AppCard.vue`, `AppBadge.vue`, `AppTable.vue`, `PageHeader.vue`, `EmptyState.vue`, `components/research-panels.css`、`src/style.css`。
- 应用与数据层：`App.vue`, `stores/dashboard.ts`, `types/dashboard.ts`, `utils/{dashboardHealth,dashboardSnapshot,observationDisplay,memory,newsStatus,macroAnalysis}.ts`。
- 本地契约核对：`dashboard/app.py` 的公共快照与 execution profile 投影，`scripts/evolution_status.py`、`evolution_evidence.py`、`execution_profiles.py`、`dashboard_stats.py`、`self_improvement_engine.py`；未执行后端任务。
- 现有前端测试作为回归范围。未宣称逐行审完所有组件或完成浏览器视觉 QA。

## 2. 已完成变更

### 层级和动作精简

1. 提示词入口从右下悬浮区移至 Header，原路径 `FloatingActions.vue` 保留但职责改为 Header action，避免其他引用路径被破坏。
2. 删除悬浮全量刷新；页面与 K 线只在错误／延迟时提供“重试”。K 线周期、指标、缩放、回到最新保留。
3. 信号详情、提示词、关于、费用明细只保留 AppDialog 顶部关闭入口；保留原有 Escape／原生 dialog 焦点行为，未改共享 dialog。
4. lab 长篇决策与等待审计默认折叠，证据、建议、完整报告、任务详情仍可访问；审核未通过计数移到任务详情，待审核和已发布记忆分区。
5. Header 为 320px／1024px 窄布局增加局部样式；窄屏提示词以可访问图标保留，不引入全局主题变化。
6. 修复保存 stacked 布局后下次仍总是 dual 的问题。

### 契约和真实状态

1. 净费用后胜率只在 `report_scope_verified === true` 且 `evidence_feedback.settled_samples/wins` 为合法整数时，按净盈利笔数 / 已结算笔数计算，盈亏平衡包含在分母。零样本、缺失、非有限数、不同账户或未核验范围均为未知。
2. 旧 `review.win_rate` 不冒充已核验净胜率，仅在任务详情保留原始历史值。失败提示不再被后端通用 `message` 覆盖，并展示 `error_message`（若有）。无报告不额外误报“账户不匹配”。
3. 当前配置模型直接读取返回的 `llm_runtime.model`，不使用 store 的硬编码默认模型冒充实际复盘模型。
4. `small300`／standard 的风险比例、最大杠杆、持仓上限、保证金上限读取 `execution_profile.execution` 实际字段。测试覆盖 2%/6x/270U 和 1.25%/4x/160U 两组设置；这只是测试 fixture，不是新增运行默认值。
5. 缺失策略统计不再补 0，保留真实 0，删除“实时更新”标签，失败快照明确为历史。前端日内盈亏／本金参考比例不再被当成实际熔断触发状态；仅后端 `decision_cycle.status === circuit_breaker` 明确标触发，其他情况为未提供触发状态。
6. 提示词没有独立记录时间和轮次关联时明确显示“最近保存记录”，不加跳动绿点，不承诺本轮。信号详情中的全局 prompt 同样标注未核验与该信号的关联。
7. 历史决策没有研判文本时显示缺失，而不是“宏观中性震荡”；展开状态不再依赖数组下标，避免轮询插入新记录后展开错行。超过 24 条的已返回数据保留“加载更多”。
8. “今日已结盈亏”不再回退到可能含未实现盈亏的 `total_pnl`；零成交不显示 0% 胜率；新闻多空比例的真实 0 不丢失；历史杠杆不再默认 3x。
9. 止损覆盖仅描述快照，延迟数据不宣称“100% 云端 OCO”实时保护。日志不再宣称实时滚动或硬编码 15 分钟周期。未就绪的持仓／挂单空态与真正空仓／空单区别显示。
10. Docs 增补 Header 入口、动态参数、净胜率口径和默认折叠说明；关于弹窗去除绝对安全保证。没有修改任何实际交易条件。

## 3. 测试与构建

新增：`okxquant_frontend/tests/audit-a08-public-ui.test.mjs`，21 项。

- 真实 Vue SFC 编译 + SSR 检查：复盘、已发布记忆、动态执行参数、prompt 空／旧状态、历史记录、当日已结盈亏。
- 纯函数检查：净胜率分母、真实零、缺失／异账户／非法输入、数据不变性。
- 源码交互契约：Header 唯一入口、弹窗唯一关闭、条件重试、保留 K 线必要操作、布局持久化、文档一致。
- SSR 使用 UI 容器 stub，**不等于浏览器中已验证 dialog、布局或复制行为**。

执行结果：

| 命令 | 结果 |
| --- | --- |
| `node --experimental-strip-types --test tests/audit-a08-public-ui.test.mjs` | 21/21 通过 |
| `npm.cmd run test:unit` | 最终 195 项：194 通过，0 失败，1 跳过（Windows symlink 权限既有跳过） |
| `npm.cmd run typecheck` | A08 修改阶段通过；最终共享工作树出现下述并行 admin 错误 |
| `npm.cmd run build` | 最终两次尝试均被 `src/views/admin/PromptStudioPage.vue(13,1): TS6133 onBeforeRouteLeave is declared but its value is never read` 阻塞 |
| `node_modules/.bin/vite.cmd build` | 独立生产打包通过；不替代完整 typecheck/build 通过 |
| `git diff --check -- <A08 源文件范围>` | 通过 |

构建错误不在 A08 写集合；已保持其他 agent 修改不动。未安装新依赖，也未启动服务、浏览器或读真实账户。

## 4. 主 agent 协调事项

1. **集成构建**：协调 admin agent 完成／移除 PromptStudioPage 未使用的 `onBeforeRouteLeave`，再跑完整 `npm.cmd run build`。并行工作树仍在变化，最终测试数量与结果应以集成后重跑为准。
2. **Prompt API 元数据**：目前公共 `ai_last_prompt` 只有字符串，建议提供独立 `recorded_at`、`cycle_id`、账户 scope／关联状态和必要的历史标记；不能用 dashboard 快照时间冒充 prompt 生成时间。A08 现已用保守文案降级。
3. **复盘模型溯源**：`llm_runtime.model` 是当前配置，不是产生历史报告的模型。如需展示“复盘模型”，后端应返回报告自身 model/provider、产生时间与版本。
4. **风险状态契约**：公共接口未提供可直接展示的权威“未触发”状态与有效回撤基准；前端已停止推断未熔断。建议后端输出显式 current/unknown/stale 的 circuit 状态、实际基准及时间。
5. **历史加载边界**：新增加载更多仅展开 API 已返回的历史，未发明服务端分页接口。超出返回窗口的记录需要主 agent 与 API 负责人确认分页契约。
6. `EvolutionReviewPanel` / `PublishedMemoryPanel` 为前后台共享组件：本次只改公共展示语义，不触碰 admin 操作权限；主 agent 请将后台复盘页加入冒烟验证。

## 5. 留给隔离 Playwright 的验证清单

按要求本 agent 不启动应用内 Browser、headless browser 或服务。

- 320、375、768、1024、1440 宽度，明暗主题：Header 不溢出，提示词入口可达，lab 双栏／堆叠和 disclosure 文字不被挤压。
- 正常／延迟／空／错误快照：默认没有重复刷新，延迟重试仍可用，恢复后状态正确。
- 提示词与信号详情：顶部只有一个关闭；Escape、焦点回到触发按钮、复制成功／权限失败、长原文滚动。
- lab：默认长审计收起，点击和键盘能展开；待审核不冒充已发布，历史失败报告不冒充当前成功，已结算净率与 fixture 相符。
- 历史决策加载更多；轮询前插记录后仍展开原记录；账户切换后不串行。
- stacked 布局重载恢复；K 线错误重试、正常轮询不重置视窗、缩放与回到最新仍在。
- 检查共享后台复盘组件样式和完整前端 build，解决并行 admin 类型错误后再做最终集成验收。
