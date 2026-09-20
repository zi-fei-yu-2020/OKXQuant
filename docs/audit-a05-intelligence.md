# A05：模型、新闻、复盘与运行记忆审计

日期：2026-09-19（Asia/Shanghai）
工作方式：10-agent 并行工作区中的 A05；仅修改本域独占文件。未改 prompt_library.py，未撤销其他 agent 改动，未 commit/push/部署，未读取运行秘密，未调用真实模型/交易接口，未再委派。

## 结论

本轮修复的是响应完整性、错误后缓存残留、时效/账户来源、提示词事实、新闻容错及研究产物隔离，不是调整交易频率或新增开仓门槛。未新增置信度阈值、AI 强制批准或放宽安全规则；复盘仍只产生报告/待审候选，不自动发布运行记忆。

A05 专属隔离回归：31 项通过。跨域执行消费者仍需使用新增的来源字段；不能仅凭本域通过宣称账户隔离和策略切换已全链路闭环。

## 覆盖范围与审查深度

| 链路 | 文件 | 本轮覆盖 |
|---|---|---|
| 推理、解析、错误与重试 | scripts/ai_brain_trader.py、model_json.py；okxquant_backend/llm_transport.py、llm_manager.py、council_manager.py | 阅读调用边界、响应协议、重试预算、缓存与取消挂单路径；修改核心缺陷并隔离测试 |
| 提示词与程序事实 | scripts/trading_prompt.py；okxquant_backend/interceptor_manager.py、prompt_views.py | 核对用户层/动态数据层、运行 profile 快照、变量目录、执行约束与 UI 描述；prompt_library 只读协作 |
| WAIT 与决策呈现 | scripts/wait_audit.py、wait_repair.py、wait_diagnostics.py、wait_counters.py、decision_reporting.py | 核对纠错仅修 WAIT 审计、不授权新交易、不叠加 JSON 重生成，诊断与正常 WAIT 分离；经现有全量测试回归 |
| 新闻 | scripts/news_connection.py、news_sentiment_harvester.py | 核对独立新闻绑定、generation、分区时效、情绪 UNKNOWN、来源报道的信任边界与缓存异常；修容错，不修改熔断规则 |
| 复盘与记忆 | scripts/self_improvement_engine.py、evolution_evidence.py、evolution_status.py、evolution_shield.py、memory_registry.py；okxquant_backend/memory_routes.py | 核对报告/候选/发布分离、版本归因、账户 scope、显式管理员发布与证据审核；修复盘版本/去重错误 |
| 研究与回放 | scripts/shadow_research.py、research_lab.py、research_data.py、backtest_engine.py、calculus_replay.py、scenario_replay.py、opportunity_replay.py、scenario_shadow.py | 静态抽查数据来源、时间顺序、成本假设、只读/影子/手动复核边界；修离线计算默认覆盖运行快照 |
| 跨域消费者（只读） | ai_factor_trader、entry_gateway、strategy_evidence、execution_profiles、account_connections、public_market | 确认生产者字段如何归档/执行；记录未闭环接口，不越权修改 |

说明：这是边界导向审计，不是逐行形式化证明；未用真实行情、真实账户或付费推理验证收益/交易表现。

## 已修复的可证实问题

### A05-01 / 高：完成状态和协议结构不能可靠拦截不完整模型输出

- 委员会 CIO 原调用未设置 require_complete，完整 JSON 文本仍可能来自截断/拒绝/工具调用响应。
- verify_completion 原先对部分错误类型直接 AttributeError，对 Responses 的消息级 incomplete/refusal 和文本旁工具调用没有完整校验。
- 修复：CIO 和 WAIT 纠错均要求完成校验；三协议检查容器类型、已知完成状态、拒绝/工具块，异常统一为 ContractError。保留旧网关缺省 finish/status 的兼容行为，未假定所有代理严格返回终止元数据。
- 测试：畸形 envelope、refusal 旁有效 JSON、消息级 incomplete、工具调用、正常多协议响应、经理层真实提取入口、一次重生成与传输错误不叠加重试。

### A05-02 / 中：委员会超时预算被隐式扩大

- 原 member_timeout 最少 15 秒、CIO 剩余预算最少 25 秒；例如声明 2 秒仍给终审 25 秒。
- 修复：单调时钟共享截止时间；不增加用户预算；预算耗尽不开始终审，超时结果不采用。
- 限制：Python 线程和 urllib socket timeout 不是进程级强杀，阻塞中的调用可能晚返回；保证的是晚结果不被接受，不保证物理调用在截止瞬间终止。

### A05-03 / 高：过期、跨账户或失败周期可能遗留管理/决策指令

- 原管理文件用生成结束时间续命，没有账户/绑定/持仓来源；缓存读取也不核对账户来源。
- 原历史写入发生在决策缓存发布之后，历史落盘失败返回 None，却留下可读取的有效开仓缓存。
- 修复：本轮实际捕获时间传给 begin_signal_frame；在取消挂单前、真实命令屏障内、证据及缓存发布前核对原有 300 秒时效和账户来源。取消屏障使用捕获的环境而非重新选择账户。
- 管理文件的 timestamp 改为本轮捕获时间，generated_at 单独记录生成完成时间；补 account_scope、connection_id、binding_version、strategy_profile_hash、position_basis。
- 缓存读取拒绝无来源/不同来源、绑定版本变化、静态有效策略变化及 NaN/Inf 失效时间。异常返回前清空决策/管理缓存，防止部分发布遗留。
- 兼容：所有真实 OKXEnvironment（environment/account-center/account-center-unbound）仍调用 assert_current；只有原有只读市场 adapter 的最小 mode/identity mock 不强求其不存在的注册表元数据。专属测试验证真实环境不绕过绑定拒绝，并重用原 brain fixture 验证兼容。
- 剩余：消费端需重新核验，文件之间不是事务，磁盘不可写时无法保证完成清缓存；已发出的撤单不能因后续审计失败而撤销。

### A05-04 / 中：提示词 context、策略快照和 UI 风险事实不一致

- construct_full_market_prompt(profile=...) 原仍调用无参 execution_profiles.runtime()，可能拼入另一个激活策略的执行约束。
- preference_layers 把变量变成 runtime_data 引用，但缺 profile_name、active_instruments、timezone、strategy_version 对应值。
- 修复：执行配置与偏好使用同一个传入 profile；补四项元数据。未知变量显式 UNKNOWN 并给 runtime_variable_unknown 告警，不新增开仓拦截。
- 不调用 render_variables 把新闻/记忆展开到指令层：保持 USER 数据引用；不修改传入 runtime。
- profile_signature 只取有效静态偏好/冲突、id/name 与执行配置，不取模块被编辑器动态展开后的 flat 副本。同 ID 文本编辑会变更指纹，时间/新闻的动态副本变化不会误判策略切换。
- 固定 UI 文案“目标 2.5；底线 2.0”改为本轮基础 minimum_net_rr 和执行预设事实，并注明最终执行器复核；没有改变 RR 计算或门槛。
- 主 agent 协作：_clean_profile 的 compile_modules(render=False) 与本域兼容；运行 profile 的非空 pipelines 优先使用原始模块，simple 分支的静态策略拼接也保留变量引用。

### A05-05 / 中：新闻缓存畸形可让整轮提示词失败

- 缓存根列表、畸形分区、非字典新闻行/情绪行可触发 .get()/迭代异常。
- 修复：检查根/分区/行类型；坏分区不可用但保留其他有效新闻；损坏情绪为 UNKNOWN，不伪装中性；处理数值溢出响应。
- 已有时效、绑定 generation、24h 情绪不等于小时预测的约束保留。
- 测试：坏缓存、坏分区/行、坏情绪、未来报道、过期报道、绑定版本变化。

### A05-06 / 中：复盘把新发布版本冒充为当次输入版本

- 报告原在推理结束重新读取 active_version，若期间人工发布记忆，会把新版本误标成本次复盘依据。
- 仅用 active_version 去重也不能区分未纳管旧 Markdown 的内容变化（版本都为 None）。
- 修复：报告固定使用模型调用前 memory_state 的版本和 prompt_hash；新增 memory_prompt_hash_at_review，去重同时比较版本与哈希。
- 测试：推理期间版本变化仍归因旧快照；同 ledger、同 None 版本、内容变化会重跑复盘；未变化则复用成功报告。NO_CHANGE 不创建 registry、不发布记忆。

### A05-07 / 中：离线回放默认覆盖运行计算快照

- calculus_replay 原默认写 data/calculus_snapshot.json，与运行 brain 输出冲突。
- 修复：默认输出 data/calculus_replay_report.json，增加 mode=offline_replay、order_authorized=false。
- 测试：在临时目录放运行快照 sentinel，离线计算完成后原文件原样保留。
- 显式 --output 仍属操作者指定路径，未增加不相关文件策略。

## 给后端/UI/执行域的字段契约

### 1. 决策和管理来源（需执行域接入，高优先级）

- cache row：account_scope、connection_id、binding_version、strategy_profile_hash、position_basis。
- decision.strategy_profile_hash：同一个静态策略指纹，随既有 decision 对象进入 evidence，不依赖 archiver 新增白名单字段。
- ai_position_management.json：同上来源 + timestamp（捕获时间）+ generated_at（生成时间）+ position_basis（本轮持仓输入）+ instructions。
- 推荐最终消费者在已有写屏障内比较账户/绑定、0 <= age <= 300、实际持仓方向/数量/生命周期和策略指纹；这是旧指令来源复核，不是新增开仓评分线。
- 审查时 ai_factor_trader.execute_ai_position_management 仍仅检查 timestamp 大于 300，未使用新增来源，也未拒绝未来时间；需要对应 owner 修复，A05 不能声称已关闭该漏洞。
- entry_gateway 原仅比较 execution_profile_signature。相同 profile ID 只改文字时该签名可能不变；直接使用 batch/evidence 的路径不经过 get_latest_ai_decision。建议比较 decision.strategy_profile_hash 与 trading_prompt.profile_signature(active_profile())，保障策略编辑在执行边界生效。独立场景策略/分钟实验的契约应由其 owner 分别处理，不把 AI 审批变成所有策略前置要求。

### 2. 模型与 WAIT 状态

- trading_output_validation.status 为 pending/blocked/unavailable/rejected/incomplete/validated 等状态，不能统一展示成正常 WAIT。
- model_failure 为 allowlist 诊断；json_response 的 regenerated 只代表 JSON 语法可解析，不代表交易授权。
- WAIT correction 的 corrected 只代表审计字段被修好，不能推断开仓获批或管理动作发生。
- UI 显示模型原始 action/confidence 与最终 action/validation_reason 的区别；confidence 为未校准证据分数，不是胜率。

### 3. 提示词与记忆状态

- composition.profile_hash 现为静态有效策略指纹；system_hash/user_hash 仍是本轮实际模型文本哈希，两者不可混用。
- runtime_variable_unknown 应展示为可诊断缺失，不提示“账户无仓/没有新闻风险”。
- memory_version_at_review、memory_prompt_hash_at_review 是输入归因；active_memory_updated_at / active_version 才是当前运行记忆状态。
- recommendations / actions_taken 是建议，不是执行回执；proposed_change_status 是提议，不等于 published。
- evolution_status 对无 account_scope 的旧报告保留兼容显示，同时 report_scope_verified=false；UI 不应把这种报告标成当前账户已核验复盘。
- prompt_views.rendered_snapshots 仍组合多个共享历史文件，缺少统一 frame/scope 事务校验。界面应将其标为最近历史快照；跨账户切换时不能误标为当前实时输入。后续由 UI/API owner 协同补齐，避免 A05 擅改既有响应结构。

### 4. 新闻/回测展示

- 新闻 captured_at/last_success_at、section_freshness、sentiment_fresh 与连接状态分开；部分成功不代表全部分区新鲜。
- 没有可用新闻不是市场平稳证明；24h 情绪不是当前小时方向预测。
- research_lab 的候选仍需人工审查，scenario/opportunity/shadow 不是订单回执；calculus_replay 新增明确离线标记。

## 未修改但确认保留的边界

- llm_transport 对推理请求有总预算、有限重试、Retry-After 和脱敏错误；不用于订单重试。
- memory_registry 的候选与不可变版本分离，显式管理员发布/回滚仍经 publication_gate、修订号和账户检查；读取不初始化，NO_CHANGE 不自动发布。
- WAIT、全 WAIT、HOLD/KEEP 均保持合法；没有加入凑交易/最低分或强制批准。
- 研究模块保留真实数据/完整 funding/前向 provenance/样本约束及手动复核标签；未替换真实市场验证为收益保证。

## 测试和复现

执行：

~~~powershell
wsl /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a05_*.py'
~~~

结果：31 项通过，测试在 /tmp 的一次性源码快照中运行，排除运行 data/logs/.env，阻止未 mock 的网络/DNS/子进程；本域用临时文件、伪凭据和模型响应，无真实交易/模型调用。

追加全量快照回归：2026-09-19 20:57 开始的版本运行 1772 项，8 failures + 2 errors。所有失败属于并行新增 test_audit_a03_equity（7 failures + 2 errors）及原 test_qq_bind（1 failure）；未改这些跨域文件。A05、原 brain 回归及 UTF-8 检查无失败。并行修改持续进行，数量/跨域结果仅代表该次快照，不宣称整个工作区全绿。

日志：%TEMP%/okxquant-audit-a05-targeted.log、%TEMP%/okxquant-audit-a05-full.log。修改文件通过 git diff --check。

## 剩余风险

1. 高：管理指令最终消费端账户/持仓绑定及直接 batch/evidence 的策略文本切换保护仍需跨域落地。
2. 中：提示词、状态、历史文件跨文件发布不是事务；磁盘异常和并发切换有短暂视图不一致，UI 必须区分历史与当前。
3. 中：委员会失败仍沿用既有单模型 fallback；本轮未变更策略。降级来源/预算应在 UI 明确展示，不能描述成所有角色均成功。
4. 中：新闻黑天鹅规则仍是文本匹配且可重复触发；可能受重复报道、否定句和误报影响。本轮不擅自放宽/收紧熔断，需单独人工评估。
5. 研究限制：OHLC 不提供真实排队/成交、历史合约元数据不全、统计比较不构成收益承诺；没有在线前向试验结论。
6. 旧兼容数据：缺账户来源的旧报告/legacy 记忆需显式标记；旧代理缺省 finish/status 仍兼容，不等于能够证明上游完整返回。

## 实际改动清单

- scripts/ai_brain_trader.py
- scripts/trading_prompt.py
- scripts/model_json.py
- scripts/news_connection.py
- scripts/self_improvement_engine.py
- scripts/calculus_replay.py
- okxquant_backend/council_manager.py
- tests/test_audit_a05_intelligence.py
- docs/audit-a05-intelligence.md
