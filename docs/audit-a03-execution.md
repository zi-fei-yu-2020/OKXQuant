# A03 交易执行闭环审查与修复

日期：2026-09-19。上线基线按任务指定的 `4f8e37d` 审查；工作区同时存在其他 agent 的修改。本报告不代表已部署或已实盘验证。

## 范围与操作约束

仅修改 A03 授权的源文件、`tests/test_audit_a03_*.py` 和本报告；用户后续明确扩大授权至 `strategy_evidence.py`、`position_lifecycle.py`，用于 A04 交接。未 commit、push、部署、再委派；未读取真实 `.env`、账户运行数据或密钥。没有新增开仓信号门槛，没有调整风险比例、杠杆参数、费用或收益风险比。没有重发不确定订单，没有取消已有 OCO 来换取开仓可用性。保留其他 agent 的并行差异。

| 授权源文件 | 审查覆盖 | 本次修改 |
|---|---|---|
| `scripts/ai_factor_trader.py` | AI 周期互斥、推理释放/重获锁、提交、回执、保护交接、异常返回 | 单次 dispatch 异常边界、回执验证、ACK 落库异常、tracker 损坏/归档、horizon 先保存后消费 |
| `scripts/demo_scalp.py` | 1M 候选、账户冻结、分钟持久 claim、固定请求预算、统一提交、研究失败不重发、DEMO 硬限制 | 未修改；LIVE 契约待主 agent 协调 |
| `scripts/entry_gateway.py` | 账户绑定、决策、报价/仓位/挂单预检、权益/资金复核、杠杆、意图创建 | 核对队列推进、USDT 权益观察及旧状态迁移 |
| `scripts/entry_reconciliation.py` | typed 51603、回执/订单/成交/仓位、多页完整性、已接受证据 | 持久 ordId 恢复、成交 ordId 恢复、pending 持续核对、冲突证据不猜测 |
| `scripts/entry_diagnostics.py` | 失败码、脱敏、单笔回执形态 | 内层业务状态、身份校验、列表 sCode 提取 |
| `scripts/trade_lock.py` | 线程/进程互斥、重入、推理窗口、带 scope 参数的调用 | 全部调用共用已部署默认锁文件 |
| `scripts/initial_protection.py` | 成交等待、当前仓位身份/数量、完整 OCO、未知返回 | 非有限/缺失仓位数量不能被解释为平仓 |
| `scripts/close_execution.py` | 撤剩余普通订单、生命周期复核、reduce-only 平仓、只读确认 | 未修改退出策略；跨周期未知 close 归 A04 协调 |
| `scripts/algo_reader.py` | 账户快照、分页、缓存 epoch、写屏障、有界读重试 | 同 algoId 的矛盾分页行不再静默保留旧绿色记录 |

## 完整执行链

1. **AI / 程序入口**：AI 冻结账户并持有周期锁，LLM 推理窗口释放 writer、恢复后刷新仓位；程序 1M 引擎先采集/排名，再在同一 writer 下持久化账户+分钟 claim。研究逻辑发生在提交结果之后、writer 之外；研究失败不重发。
2. **最终预检**：统一进入 `submit_protected_limit_order -> entry_gateway.prepare`。重新核对账户绑定、持久决策、执行配置签名、候选有效期、真实 tick、行情、仓位基础、挂单、保护、权益与资金分配。外层旧缓存不是实际下单授权。
3. **杠杆**：沿用 `execution_leverage` 单次设置、读回确认和配置签名校验。杠杆变动后再次查仓位/报价。没有盲目重试 POST。本报告只读审查该跨域依赖。
4. **持久意图**：`begin_intent` 在开仓写之前提交，账户+决策+品种唯一；沿用 32 字符 client ID。附加 horizon/context 文件准备发生在下单之前。未修改 unique 约束或用新 ID 重放旧决定。
5. **下单与回执**：只发送一次带相同计划价格/数量和附加 TP/SL 的 limit 命令。ACK 不是成交，也不是保护已生效。业务失败、身份矛盾、多行回执或缺失订单号均保留原意图，交只读核对。
6. **保护**：新仓按当前生命周期和当前数量核对完整、有效 OCO；已有保护不因 ACK 被默认满足。异常/不完整读取保持 UNKNOWN；本次修复非有限数量误报 flat、矛盾分页误报完整保护。
7. **恢复**：意图持续从 unknown/acknowledged/pending 核对到 filled/canceled/mmp_canceled，或在严格缺失证明下进入 not_found。每批最多处理既有上限 20 条，但不再在积压超过 20 时拒绝进行任何读取。

## 已复现并修复的问题

P1 表示可能破坏互斥、回执真实性或保护判断；P2 表示恢复、可用性、证据或计量不一致。均为离线确定性复现，不声称已经导致线上成交事故。

| 编号 / 严重性 | 证实问题 | 修复与回归 |
|---|---|---|
| A03-01 / P1 | 默认 writer 与账户/品种/方向参数 writer 使用不同文件；不同进程可以绕过同账户组合锁 | 所有 scope 提示共用原默认 `hash('||')` 文件，保留旧默认调用兼容；验证真实 flock 争用、重入和推理窗口 |
| A03-02 / P1 | 只取 ordId，可能把外层 code=0、内层 sCode 失败或错误 client/instrument 回执认作 ACK | 单笔统一解析；校验外层/内层状态和存在的身份 echo；未知结果不重发，补列表业务码诊断 |
| A03-03 / P2 | live/partially_filled 写成 pending 后，旧 unresolved() 不再返回它，生命周期无法继续终态核对 | 共享 unresolved() 与 A03 恢复查询均包括 pending；pending 的正向接受证据禁止按空历史抹掉；预检已确认的 live 挂单复用账户快照，不冗余查询详情 |
| A03-04 / P2 | 51603 后没有利用已持久化的 exchange ordId，也没有继续查精确成交中的 ordId | client ID 明确缺失后查持久回执 ordId；精确成交匹配后按 ordId 查详情；同账户/客户端/品种/订单一致才采纳；冲突 ID 保留未知 |
| A03-05 / P2 | 第一条失败终止整批核对；超过 20 条时一次读取都不做，积压无法自愈 | 保留失败意图与原有账户不确定性约束，但完成本批其他核对；21 条已证明不存在的意图可在两批有界排空 |
| A03-06 / P2 | ACK 后持久化抛错直接打断调用；异常返回不完整，调用方难以区分提交结果 | 捕获边界异常、保留原意图/可用回执、返回明确只读恢复状态；断言写调用只有一次 |
| A03-07 / P1 | pos=NaN 或缺失值被过滤成无仓位，初始保护返回 flat | 当前目标仓位数量必须可解析且有限，否则返回 unverified；没有绕过既有保护/安全退出流程 |
| A03-08 / P1 | 分页同 algoId 出现状态矛盾时，仅保留第一次 live 行 | 完全相同的重叠页仍去重；矛盾快照走原有有界读重试，不返回部分绿色覆盖，不增加写重试 |
| A03-09 / P2（A01 指派） | equity_guard 的 USD totalEq 与 USDT 资金流水混算，USD 折价可凭空产生回撤 | 接入 currency_observation，持久状态显式 USDT；保留历史的单次单位迁移见下节 |

### 4f8e37d 的 51603 安全约束保留

- 只有 typed `OKXAPIError.code == '51603'` 或成功空响应进入缺失路径；timeout、503、认证异常、含“51603”字样的普通异常不当作不存在。
- 保留原 120 秒宽限、7 日安全窗口、分页上限及读取预算；长旧 client ID 不发送无效详情查询。
- 缺失证明仍扫描 pending、history、fills-history 和仓位；无身份成交、非零仓位、分页不完整保留意图。
- 已 ACK / 已观察 pending 的接受证据不能被空历史覆盖。未取得完整证明时不使用新 client ID 重发。
- 用户指出中途快照的两个失败 `test_acknowledged_exchange_id_recovers_missing_client_id_lookup`、`test_conflicting_journal_order_ids_do_not_choose_one` 已单独重跑为 OK；它们是先写失败回归、随后修复期间的快照。

## 权益单位兼容迁移

新观察取真实 `details[USDT].eq`，不会用 totalEq 或 availEq 猜测；external_flow_total 和 external_flow_origin 原本来自 USDT 流水，不换算、不重置。

- 已标注 USDT：直接沿用原回撤更新逻辑。同时间戳仅比较 USDT eq，USD 汇率重估不再制造“矛盾权益”。
- 旧状态与当前余额同时间戳：可用两者准确衔接该观察点，按比例转换历史 day_anchor/peak，保留既有回撤比例和 blocked。
- 有同时间戳资金池 account_equity：使用这一 USDT 观察衔接，后续 USDT 真实盈亏正常计量。兼容可选资金池及 execution-small300 scope。
- 无同时间戳 USDT 证据：不拿当前汇率冒充历史汇率；在首次 USDT 观察点保留已记录的回撤比例和 blocked，原状态完整存于 `currency_migration.legacy_state`，标记 `interval_unattributed=true`。该衔接区间的真实 USDT 盈亏无法从旧 USD 快照独立还原，不能声称已准确重建。真实流水仍累计，后续观察全部按实际 USDT 变化计量。
- 单位迁移本身不解除已有 blocked，也不清空已记录亏损；迁移元数据随后续观察保留。倒退时间戳不覆盖旧状态。

此无历史汇率的衔接方案已向主 agent 明确说明，待最终兼容口径确认。范围外 `execution_profiles.observe_existing()` 的无 observation 分支仍需由主 agent / A01 按 equity_currency 修改余额版本比较；不能继续将 USDT equity 与 USD totalEq 比较。

## small300 的 15U 预算复核

新增完整 1M gateway 测试，合成余额为 5000 USDT / 5500 USD，账户真实杠杆已是 10，不发任何私有写请求：

- 程序请求 budget=15 不是实际止损额度。small300 资金权益为 300 USDT，沿用 scalp 模式 0.4% 单笔比例，最终风险不超过 1.2 USDT（lot 向下取整可更低），而非 15 USDT。
- 调用方预算低于模式上限时仍取较小值。
- A01 `cap_allocation.enabled=True` 后，账户级 pending 读取恰为两次（初读、最终复核）；若必须更改杠杆，其模块另有品种级检查，不应与上述两次混淆。
- 两次账户挂单快照间出现新预留会在意图创建前拒绝，且没有开仓/杠杆写操作。本次不改变预算常数或模式参数。

## 重大需求差距：DEMO -> LIVE 的同一 1M 策略语义

**已发现，尚未实施 LIVE 启用，不能宣称已完成同策略实盘支持。** 当前 `demo_scalp.enabled()`、run 的冻结账户复核、gateway 的 minute_engine 检查都硬限定 DEMO；另有 `demo_scalp_policy.execution_policy()` 拒绝 LIVE，自动杠杆策略也区分环境。UI 显示同一个 small300 不等于 LIVE 已运行同一 1M 引擎。

向主 agent / A06 / A09 提出的最小安全接口（待确认，非已存在接口）：

1. 将中性引擎版本/决策规则/风险配置与环境执行授权分开；保留原规则数值，不用 LIVE 静默替换为不同周期引擎。
2. 旧 `enabled + version=demo-scalp-v2` 仅授权 DEMO。新持久配置按 environments.demo / environments.live 保存；LIVE 默认 enabled=false。
3. LIVE 管理员确认记录绑定 connection_id、binding_version、engine_version、execution_signature、policy_signature 和确认时间/管理员身份；不得只信任客户端传来的布尔值。切账户或改策略版本使旧确认失效。
4. A06/主 agent 负责受保护的管理员持久化入口；A03 在引擎运行入口和最终订单入口消费同一授权契约，不能生成或代替管理员确认。
5. A09 展示“同一引擎但 LIVE 未授权 / 已授权未调度 / 正在运行”，禁止把所有 LIVE 默认启用，也不能用一个笼统 small300 标签掩盖无 1M 引擎。
6. A01 需协调 sampling policy / leverage 的环境硬编码：只改 enabled() 不满足语义一致。授权后采用什么明确版本的规则和杠杆写权限，必须是可审计契约。

新增回归已证明旧 DEMO enabled 配置不会悄悄启用 LIVE。本次没有切 LIVE、创建管理员确认或修改实际运行配置。

## 测试

规定命令，WSL 隔离解释器和临时源码快照，禁网/禁外部子进程，排除真实 data/logs/.env：

```text
wsl --exec /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a03_*.py'
```

- `test_audit_a03_execution.py`：21 项，执行/恢复/锁/保护新回归。
- `test_audit_a03_equity.py`：10 项，币种与历史迁移新回归。
- `test_audit_a03_budget.py`：4 项，small300 预算/二次复核/旧配置不启用 LIVE。
- `test_audit_a03_lifecycle.py`：9 项，A04 交接的新鲜 pending 复用、tracker 损坏、归档及 horizon 持久化顺序。
- `test_audit_a03_regressions.py`：加载 10 个既有执行相关测试模块的 167 项，不复制或修改原断言。

执行链修复第一阶段实跑 187 项全绿。最终新增 44 项定向测试单独实跑全绿；规定通配命令合计 211 项，汇总当前尚有旧 fixture 兼容失败（12 个子测试失败、4 个用例报错）：`test_strategy_integration.py` 三个正常预检用例报错，保护覆盖用例的 12 个子测试失败；`test_demo_scalp_policy.py` 完整 gateway 用例报错。它们的余额 fixture 缺少 USDT eq，仅含 totalEq/availEq。已请求主 agent 补齐范围外合成数据，不以放松生产校验换取测试绿色。最终复跑结果将在本节更新。

## 未解决跨域 / 主 agent 协调清单

- **A01 / 主 agent**：确认上面的旧权益衔接口径；修复 execution_profiles 无 observation 分支的 USD 对比；补齐范围外旧测试的 USDT eq。日亏账本跨 scope 由主 agent 的 risk_policy 变更处理，未来 account_baseline 由 A07 处理。
- **A04 / 主 agent**：用户指派的 pending、tracker、horizon 三项交接已完成，见追加章节；保留 A04 的已有证据身份检查和归档实现。其他退出契约仍需整合：close_execution 每次调用生成新 UUID，单次不重发不等于跨周期未知 close 不重复；best_effort 预写也不保证持久化成功。不能简单停掉紧急安全退出，需主 agent 继续确认此契约。
- **A06 / A09 / A01**：上述环境解耦、持久管理员授权及真实运行状态展示仍是需求缺口，等待主 agent 确认契约与分工；本次未将 LIVE 默认启用。
- **A07**：请求层 typed 错误、预算及 POST 不重试依赖保持不变。A03 没有改服务请求层；coordination、HTTP 错误或缺失字段不构成订单不存在证明。
- **账户管理负责人 / 主 agent**：范围外 `account_connections._unresolved()` 仍仅检查 unknown/acknowledged；A03 的 pending 修复没有擅自改变账户切换策略。其另有交易所 flat 复核，但持久状态枚举应统一审查。
- **明确限制**：旧已接受订单如果回执/交易所历史均已不可用，或剩余成交无法绑定，仍保留意图；不能以解除锁为由抹除正向证据。文件 writer 约束同一共享 data 目录，不能声称提供跨主机/不同 data 根目录的分布式锁。
## A04 追加授权的最终交接（已实现）

- **A03-10 / P1：损坏 tracker 被当作空状态覆写。** `load_trackers` 返回带 readable 标记的 `TrackerSnapshot`；仅文件确实不存在才是新空状态。损坏 JSON、错误结构和非有限数值标为 UNKNOWN。`save_trackers` 拒绝覆盖损坏原件，包括传入普通空 dict 的情况。`position_lifecycle.reconcile` 对此返回 unknown；真实管理函数继续验证云端保护，不从损坏本地状态推导新止损，不为了修文件撤掉已有 OCO。
- **A03-11 / P1：主交易循环直接删除 tracker 丢失未决改单/上下文。** 平仓成功及 prune 的全部直接 pop/del 已改用 A04 的 `position_lifecycle.retire`：先归档完整旧状态，再删除；归档失败保留 tracker。未重写 A04 的归档实现和已有 scope/kind 冲突检查。
- **A03-12 / P1：horizon 消费先于 tracker 落盘，崩溃后短线模式/上下文丢失。** 主循环先采用 mode/context/exit preset，连同完整源 `entryIntent` 原子保存 tracker，再按 expected 源值比较后消费 horizon。保存失败原 horizon 保留；崩溃于保存后清理前可凭已保存源继续收尾；替换成更新决策的 horizon 不会被旧消费删除。
- **pending 调整**：授权扩展后，共享 `strategy_evidence.unresolved` 包含 pending。gateway 使用已有预检的新鲜账户挂单快照确认 live/partial，匹配时不再请求订单详情；挂单消失时才继续精确只读终态查询。没有为已知 live 单新增全局停开仓门槛。
- 新增源文件改动归属：`scripts/strategy_evidence.py` 只扩展 unresolved 状态枚举；`scripts/position_lifecycle.py` 只增加 TrackerSnapshot 和不可读快照的 unknown 判断。其他 A04 差异均保留。

追加定向命令：

```text
wsl --exec /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a03_[bel]*.py' --verbose
```

实测 `Ran 44 tests ... OK`。该子集只用于分离新回归与范围外旧 fixture 问题，未代替规定的完整 `test_audit_a03_*.py` 命令，后者失败已如实记录。

## A05 执行边界：明确交主 agent 续接，未声称修复

已阅读 `docs/audit-a05-intelligence.md`。按用户允许的剩余整合安排，A03 完成后释放文件，不在 tracker/horizon 收尾期间交叉改来源契约。仍需实现：

1. `ai_factor_trader.py::execute_ai_position_management`：读取缓存后、实际 CLOSE/UPDATE_SL 写前，校验 account_scope、connection_id、binding_version、strategy_profile_hash、0 <= age <= 300，以及当前仓位 direction/size/posId/cTime。当前旧消费者尚未使用这些 A05 来源字段；未来时间也尚需拒绝。
2. `entry_gateway.py::_prepare`：读取 decision 并校验执行配置附近，将 AI 决策 `strategy_profile_hash` 与 `trading_prompt.profile_signature(active_profile())` 比对，使相同 profile ID 编辑文本后旧指令失效。不能把该 AI 来源契约无差别套给分钟/独立程序策略。

这属于配置竞态/旧授权失效修复，不是增加信号评分门槛。主 agent 接入后应同步更新旧 AI 管理测试的来源 fixture 并复跑 A03 的回执、归档、horizon 回归。

## 给 A06 / A09 的最小持久授权契约（提案，未切 LIVE）

```json
{
  "version": "demo-scalp-v2",
  "enabled": true,
  "environments": {
    "demo": {"enabled": true},
    "live": {"enabled": false, "confirmation": null}
  }
}
```

旧配置的 enabled 仅用于 DEMO 兼容；LIVE 不继承。全局自动交易暂停保持生效。confirmation 由受保护的管理员接口写入：account_scope、connection_id、binding_version、engine_version、strategy_profile_hash、execution_profile_signature、policy_signature，以及服务端生成的 confirmed_by/confirmed_at。客户端不能通过自报管理员身份或一个布尔值授权。

共同状态接口字段：engine_version、environment、enabled、authorized、status（disabled / confirmation_required / binding_changed / ready）。ready 不是正在运行，调度心跳独立展示。授权操作不切环境、不启动调度、不发送订单。涉及同一引擎的 sampling policy / leverage 环境分支须由主 agent/A01 一并整合，不能仅改开关就宣称语义一致。
## 最终停止扩展与释放记录（2026-09-19，以本节为最终状态）

按主 agent 最新指令，停止所有新工作、释放全部 A03 原始及追加写范围，不再修改生产代码。保留已经完成的真实修复，不回滚其他 agent 或自己的已完成补丁。A05 消费边界、LIVE 显式授权和旧 fixture 兼容由主 agent 续接。

### 文件完整性

- 停止前最后一项新增是完整的 `tests/test_audit_a03_leverage.py`（15 项待实现契约测试）。
- **`scripts/execution_leverage.py` 尚未修改**：`git diff --numstat -- scripts/execution_leverage.py` 无输出，原有 DEMO-only 实现完整保留；没有半写的 LIVE 分支。
- 12 个涉及的源文件和 6 个 A03 测试文件全部可 UTF-8 读取并通过 AST parse，共 **18 个完整 Python 文件**；限定源文件 `git diff --check` 通过，仅有 Git CRLF 提示。
- 最后测试命令已结束，未启动后台 helper，没有遗留本 agent 正在执行的写命令。未 commit/push/部署、切账户或启用 LIVE。

### 最后一次规定命令的实跑结果

```text
wsl --exec /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a03_*.py' --verbose
Ran 226 tests in 11.407s
FAILED (failures=17, errors=7)
```

失败数包含子测试，不等于失败方法数。此前“211 项 / 12 failures / 4 errors”是尚未加入 LIVE 杠杆契约测试时的历史结果，不是最终全绿声明。

| 测试文件 | 最终状态 |
|---|---|
| `test_audit_a03_execution.py` | 21/21 通过，包括用户指出的两个 51603 恢复测试 |
| `test_audit_a03_equity.py` | 10/10 通过 |
| `test_audit_a03_budget.py` | 4/4 通过 |
| `test_audit_a03_lifecycle.py` | 9/9 通过 |
| 上述已实现修复的新回归合计 | **44/44 通过** |
| `test_audit_a03_leverage.py` | 8 项通过，4 个方法产生 5 个失败断言，3 个方法报错；LIVE 实现尚未开始 |
| `test_audit_a03_regressions.py` 加载的 167 项旧测试 | 162 项通过，1 个方法的 12 个子测试失败，4 个方法报错；全部为已报告的旧余额 fixture 缺 eq |

### 未通过用例明细：LIVE 杠杆待实现契约

文件 `tests/test_audit_a03_leverage.py`，类 `LiveLeverageTests`：

失败：
- `test_existing_position_keeps_current_leverage_even_when_opted_in`：原实现先拒绝所有 LIVE，未到授权后已有仓位保护分支。
- `test_opted_in_live_and_demo_have_identical_effective_policy_and_target`：cap=12 和 cap=20 两个子测试均失败，LIVE 仍使用旧 policy.max_leverage。
- `test_pending_entry_prevents_releveraging_instrument`：原实现先拒绝所有 LIVE，未到授权后 pending 检查分支。
- `test_unverified_readback_retains_one_write_only`：LIVE 原实现不执行授权后的单次写/读回。

报错：
- `test_authorized_apply_writes_once_and_reads_back_under_same_scope`：旧 DEMO-only 拒绝。
- `test_live_one_minute_authorization_does_not_grant_swing_auto_leverage`：测试所提议的 `apply(..., horizon=...)` 参数尚不存在（TypeError），不是生产文件半写。
- `test_timeout_is_read_reconciled_without_second_write`：旧 DEMO-only 拒绝。

这 15 项测试表达待主 agent 确认/实现的接口方案，其中 `demo_scalp.execution_binding` 用 create=True mock；真实共享授权接口尚未落地。8 项通过主要覆盖现有默认拒绝、无操作、DEMO 兼容和未调参行为，**不能据此声称已实现细粒度 LIVE 授权验证**。既有仓位并没有因本轮代码改动被调杠杆；上述两个已有仓位/pending 测试是尚未进入拟议 opt-in 分支。

### 未通过用例明细：旧余额 fixture

`test_strategy_integration.StrategyIntegrationTests` 报错：
- `test_final_entry_accepts_profitable_stop_using_mark_and_preserves_budget`
- `test_full_final_preflight_uses_actual_leverage_and_commits_before_write`
- `test_gateway_rounds_real_tick_and_cli_sends_identical_verified_prices`

同类失败：
- `test_invalid_existing_oco_blocks_final_entry_before_reservation` 的 12 个子测试，因缺失 USDT eq 提前报余额契约错误，未到预期 coverage 判断。

`test_demo_scalp_policy.DemoPolicyTests` 报错：
- `test_full_gateway_uses_same_rr_and_durable_decision`

以上 fixture 只有 USD totalEq / availEq，主 agent 需补真实语义的合成 USDT eq；未在生产代码中回退混用单位，也未修改范围外旧测试。

### 最终有效参数语义，未重调

small300 的配置上限 per_trade_equity_pct=2% 仍被既有 mode_policy 与 horizon 上限取 min：scalp 0.4%，swing 0.5%。300 USDT 风险权益时，分别最多 1.2 USDT / 1.5 USDT，另受请求预算、余量及 lot 取整限制。没有采用“profile 预算乘 horizon 比例”的新解释，也没有把 2% 标作已实际应用。

待接杠杆方案须同时覆盖 mode_policy / choose / apply：有效 scope 绑定的 LIVE 1M 管理员授权后采用与 DEMO 相同的模式上限、目标选择与单次设置/读回；无授权保持现状；已有任一方向仓位或挂单不调杠杆。用户已将各 horizon effective 参数的 display 快照交由主 agent，不能继续固定显示 5x 或宣称每笔 2%。