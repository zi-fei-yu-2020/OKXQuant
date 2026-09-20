# A04：持仓生命周期、退出与账本证据审查

日期：2026-09-19。范围为本次离线代码审查及确定性回归，不代表实盘撮合或全库审计通过。

## 约束与改动归属

- 与其他 agent 共用工作区；未 reset/checkout、撤销或覆盖他人修改，未委派、commit、push、部署。
- 未读取真实 `.env`、账户 data 或秘密；测试通过 WSL 隔离 runner，运行于不复制真实运行数据的临时源码快照，网络和子进程边界由 runner 拦截。
- 未新增入场门槛、延迟、冷却、停开仓、风险比例或熔断阈值。未因改变退出参数而主动扩大或缩小持仓。
- 初始授权范围全部做了代码阅读；实际修改列于下面。未发现需要改动的模块不作重构式修改。
- 用户追加授权：在 `scripts/execution_profiles.py` 末尾新增 `observe_existing`，修补 A01 已报告的 small300 连续观测缺口；保留 A01 原有改动，未编辑 A01 文档/测试/其他资金模块。

## 审查覆盖

| 链路 | 阅读/核查模块 | 验证要点 |
|---|---|---|
| tracker 重启恢复与生命周期 | `position_guard.py`, `position_lifecycle.py`, `exit_policy.py` | JSON 重载后相同身份保留峰值/未决 amendment；不同身份先保存证据再退休；standard/small300 双向切换不得重绑已有仓位 |
| 分钟仓与动态退出 | `scalp_management.py`, `minute_exit.py`, `profit_protection.py`, `protection_policy.py` | 入场上下文 scope/side/时间校验；完整闭合 K 线；真实 ATR；费用保护；止损只收紧、tick 舍入、云端覆盖与不确定修改不盲重试 |
| 平仓归因与成交 | `close_attribution.py`, `close_evidence.py`, `fill_accounting.py` | 分批/部分成交、撤单后的成交量、订单去重；生命周期时间范围；直接 ID 与时间关联分级；成交手续费与回执总费用核对 |
| 账本与统计 | `ledger_accounting.py`, `ledger_duration.py`, `ledger_monitor.py`, `sync_full_ledger.py`, `horizon_stats.py`, `db_manager.py` | 实际净收益不再扣费；稳定生命周期 ID；未决结算保留；原子镜像；缺失/null/零区分；毫秒时间；按账户统计 |
| 来源与证据持久化 | `strategy_origin.py`, `strategy_evidence.py`, `evidence_sync.py` | fill→order→decision 证据链；scope/kind 身份冲突；分页重启幂等；归档失败不得推进 cursor；归档短暂不可用不抹去既有证明 |
| A01 授权续接 | `position_guard.py`, `execution_profiles.py` | 已初始化 small300 NAV/峰值/跨日/资金流持续观察；不新建基线；原有 capital_pool.advance 与 small300 回撤参数；异常不阻断保护 |

只读检查调用边界：`ai_factor_trader.py`、`entry_gateway.py`、`entry_reconciliation.py`、`capital_pool.py`、`risk_policy.py`、`strategy_modes.py`、`trade_quality.py` 及相关既有测试。跨域问题见后文。

## 可证明缺陷与修复

### A04-01 / P1：运行预设覆盖已有仓位退出快照

`exit_policy.resolve` 每次成功读取 runtime 都覆盖 tracker 中已验证的 `exitPolicy`。切换 standard→small300 可提前已有仓位退出；反向切换又可推迟原退出时点。

修复：生命周期内绑定首次验证的快照，JSON 重启恢复后不重绑；新 tracker 才采用新预设。runtime 继续读取以报告健康状态，状态区分 `preset_id` 与 `active_preset_id`、增加 lifecycle binding，不改变阈值数值。失读仍沿用最后验证值或原有 fallback；新生命周期正常清除旧权威。

### A04-02 / P1：账本 ID 冲突及更新回执重复计入 PnL/费用

旧 closed ID 仅含 `uTime + instrument`，同毫秒双向持仓会冲突；同一生命周期结算回执更新 `uTime` 又成为新交易，旧行继续保留，导致重复统计。

修复：使用 account scope、instrument、side、posId、cTime 生成稳定 ID；同生命周期页面重叠只采用最新回执；迟到的旧版本页面不回退已持久化的新结算。同版本内容冲突则保留现有账本并报错。保存原始毫秒 `position_created_at`，有充分身份信息才将旧格式行视为已替代。

SQLite 镜像：账本显式记录已证明同生命周期的 `superseded_ledger_ids`；在同一事务内替换旧投影，插入失败回滚旧投影删除。不全表清理；当前账本仍引用的 ID 不删除；其他账户/未决行不因共享旧 ID 消失。原始回执证据仍保留。

### A04-03 / P1：新同向仓位挤掉旧的待结算生命周期

旧 holding ID 只包含币种/方向，且合并逻辑把“当前有相同币种方向”当成可以抛弃旧 holding/pending 的依据。

修复：holding 也绑定完整生命周期；projection 根据身份而不是单纯币种/方向判断是否仍存活。旧仓归零但未见结算回执时保留 `closed_pending`，清除估算收益和费用，新仓独立显示。仅有明确生命周期关联才退休旧行。

### A04-04 / P1：损坏账本被静默覆盖

旧 JSON 读取异常被吞掉，随后用最近一页交易所数据重建，可能覆盖尚未决算或已离开 API 窗口的证据。

修复：已有账本必须是合法对象数组；解析或形状错误直接中止该次同步，不覆盖文件。不涉及停止交易或新增入场控制。

### A04-05 / P2：仍有仓位余额的 partial-close 回执提前完成生命周期

旧同步无条件将 positions-history 每条回执标记为 closed，即使 positions 同时证明同一 posId/cTime 仍有余额。

修复：这种回执保存在 holding 的 `partial_close_receipt` 和原始回执归档中，不提前计入完成交易数/胜率/最终 PnL；后续短窗口缺失时仍保留该 partial receipt。等待真正归零后的权威结算，不编造部分成交净收益。

### A04-06 / P2：归档短暂不可用抹去已验证费用/来源证据

相同回执重新同步时，fill archive 的缺失、临时读失败或容量上限，会把先前验证的开/平手续费覆盖成 null，并丢失开仓 ID/来源信息。

修复：只有完整 `exit_snapshot` 未变且当前是明确的 archive availability 故障时，保留既有验证结果并标注 `evidence_refresh`；当前数据矛盾、摘要损坏或回执发生变化不恢复旧费用。将一份先前已验证证明保存为 `prior_lifecycle_evidence`，不当作变化后回执的当前权威。不重复扣除手续费，交易所 realizedPnl 仍为净值依据。

### A04-07 / P2：部分成交遗漏和邻接生命周期串归因

- `closing_orders` 不接受 `partially_filled`，已发生的累计成交会从归因证据中消失。
- 已有完整 cTime/uTime 时仍把上界扩到 uTime+5 秒，可能把下一生命周期的平仓订单归到上一生命周期。

修复：纳入部分成交累计量并按 ordId 去重；完整生命周期严格限定到 uTime。仅缺开仓时间的 legacy 回执保留旧窄时间启发式，不将它升级成更强证明。成交未齐时保留 partial，不按盈亏推测来源。

### A04-08 / P2：交易所开仓订单被误认为本系统自动开仓

原策略来源逻辑将“已验证 opening order，但没有本地 decision/submission”解释成自动交易证据。交易所订单本身不能证明提交者。

修复：保留 opening ID，但来源保持 `external_or_unlinked`；多个 opening ID 也不自动断言系统下单。只有精确 fill/order/decision 关联才称为 linked。

### A04-09 / P2：证据幂等校验忽略 scope/kind

事件 ID 重用时仅比较 payload digest，相同 payload 但不同账户或事件种类会被 `INSERT OR IGNORE` 静默忽略。

修复：幂等键冲突校验同时比较 scope、kind、digest；batch 内冲突回滚整批，原证据不被覆盖。

### A04-10 / P2：退休 tracker 时归档失败仍丢弃未决证据

生命周期重置用 best-effort 归档后无条件 pop；guard 的已平仓/不再持仓分支也直接 pop。

修复：新增 `position_lifecycle.retire`，先持久化完整旧 tracker（含未决 amendment 与入场/退出上下文）再移除本地权威。归档失败保留 tracker；身份更替返回 unknown，继续既有云端保护/硬止损核查，不拿旧状态管理新仓。guard 的直接退休路径使用此函数。主交易循环仍有跨域待接入路径，未声称全部解决。

### A04-11 / P2：未知费用/毛收益被统计成零，旧账户行混入统计

horizon 汇总用 `or 0` 吞掉未知费用/毛收益；ledger 保留其他账户历史时，统计却未限定当前 scope。

修复：未知总额为 null，另给已观测小计及未知样本数量；真实零保持零，净收益仍只汇总已确认数值。运行时写 horizon_stats 显式传当前账户 scope，其他账户及无 scope legacy 行不混入当前账户统计，原 ledger 行不删除。

### A04-12 / P2：A01 授权续接——small300 独立 scope 缺少持续盯市

已有 small300 scope 原本仅在 `cap_allocation` 新开仓路径推进，没有候选时可能漏中途净值峰值和跨日基线。

修复：新增 `execution_profiles.observe_existing`，guard 每次已有权益观测后调用。只查 `env.identity + ':execution:small300'` 已存在状态，不调用新 scope 初始化；当前 runtime 切到 standard 也不重置既有 small300 基线。复用 `capital_pool.currency_observation`、相同 Config 和 `capital_pool.advance`，保持 small300 现有 daily/peak 参数，不添加门槛。

若 account guard 在持久化已完成现金流对账的观测后抛出既有拦截，helper 只允许恢复与本次 balance **时间及 totalEq 完全对应**的账户观测；旧版本/现金流对账失败不前推状态。观测模块错误仅记录异常类型，不泄露原始异常内容，不妨碍退出、保护或既有可选 pool 路径。

## SQLite 字段语义回归修正

中途主 agent 全量快照指出两项既有回归：

- `test_explicit_nulls_do_not_fall_back`
- `test_missing_numbers_are_unknown_not_zero`

原因是 A04 初次修改错误地将镜像字段 `pnl` 改为优先从 `net_pnl` 取值。已撤回该优先级改动，保留既有契约：`pnl` 缺失或显式 null 均是未知，不用其他字段补齐，零不触发 fallback。**未修改上述旧测试或其断言。** 新增回归也明确覆盖缺失、null、零、已知值及不再减一次费用。整个 `test_db_mirror_accounting` 已纳入最终 runner。

## 测试记录

指定命令（WSL 内）：

```sh
cd /mnt/d/wangkai/workspace/OKXQuant
/tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a04_*.py'
```

- 首轮 12 个新增复现中 11 个失败，修复后通过；后续新增确定性回归继续先复现再修复。
- 最终 **236 tests，OK**；其中 36 个新增 A04 用例，200 个复用既有安全回归（包含 A01 授权续接回归）。
- 新文件：`test_audit_a04_lifecycle.py`、`test_audit_a04_evidence.py`、`test_audit_a04_small300_observation.py`、`test_audit_a04_existing.py`。
- 既有回归来自 exit_policy、fill_accounting、monitor_ledger、close_evidence、scalp_management、horizon_stats、execution_lifecycle_repairs、db_mirror_accounting、protection_policy、audit_a01_risk。
- 覆盖重复 fills、分批退出总量不等于 openMaxPos、手续费返还/零值、重复 billId、冲突 billId、同毫秒时间、归档摘要损坏、net 模式未知、相邻生命周期、账户隔离、read-back 不确定不重试、费用缺失、SQLite 原子回滚等。
- small300 专项：无 scope 不初始化、峰值/跨日/入金不重置、与现有阈值一致、重复 balance 不重复归档、账户/环境不复制、过时对账状态不推进、账户拦截后恢复精确观测、观测异常仍执行原持仓保护且不下开仓单。
- `git diff --check` 对本次文件通过。没有声称全库测试通过；主 agent 负责其全量快照和其他域问题。

## 跨域报告与剩余风险

### A03：尚未终结的 intent 状态集合

`entry_gateway.reconcile_intents` 将 live/partially_filled 的 intent 写成 `pending`，而 `strategy_evidence.unresolved` 当前只取 unknown/acknowledged。pending 可能不再进入下一次 sweep；已报告主 agent，请 A03 决定完整 pending reservation/reconciliation 状态契约。未擅自改变执行状态机或新入场行为。

### A03：主交易循环 tracker 持久化边界

`ai_factor_trader.load_trackers` 对读取/解析异常返回空字典，且主循环还有多处直接 pop/del。还存在 horizon intent 的 consume 与 tracker 保存非同一事务的窗口。本次仅修授权 guard/lifecycle 退休路径；需 A03 在主循环采用等价的证据先持久化保证，尤其未决 stop amendment。未删除未决数据来消除问题。

### A01/网关：账户总权益与 USDT 流量口径

A01 文档已说明 equity_guard 用 totalEq（USD）与 USDT 外部现金流的前置账户口径问题；本次 helper 只消费已对账观测并将资金池权益转换成 USDT，未重定义网关多币种账户语义。small300 连续观察不解决这个前置问题。

### 数据能力边界

1. positions-history/orders-history 当前同步窗口为最近 100 条；fills-history 有交易所保留期且每轮有限分页，fill archive 也有 50,000 条容量上限。测试证明缺失时不伪造结果，但不能声称所有历史已齐；停机超窗口后可能需要人工补证。
2. legacy 行如果没有账户、posId 或开仓时间，不自动猜测/合并；已有秒精度旧行无法凭空恢复丢失的毫秒，可能保留待人工核对记录。未对真实 data 执行迁移或清理。
3. SQLite 旧 schema 不单独保存 scope；已证明的 lifecycle aliases 可安全迁移，但历史共享 ID 已经导致的覆盖无法凭现有镜像重建，需原始回执。
4. 缺 posId/cTime、net 模式翻仓、非 USDT 手续费、成交跨生命周期边界、损坏摘要等仍保守报告未知；没有用估算手续费或方向猜来源。
5. holding 的 unrealized PnL 不是已结算收益；partial receipt 保留在 holding，不提前纳入 completed-trade 胜率/PnL。
6. 统计 API 的新 null/unknown 语义需要展示端正确显示未知，不能再把 null 渲染成“0 手续费”；展示端不在 A04 写范围。
7. guard 的观测/保护只覆盖实际运行时采样，不可能重建停机期间从未观测的峰值；readonly observe-only 模式仍不修改 NAV。
8. 没有验证真实交易所回执延迟、交易所实际撮合、生产并发进程崩溃或交易账户；这些不是离线单元回归能够证明的事项。
