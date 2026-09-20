# 第二轮独立交叉复审：交易链路

日期：2026-09-19。生产代码只读；本轮仅新增本报告与 `tests/test_cross_review_trading.py`。

## 发布结论：BLOCKED

**发现 5 个 P1、1 个 P2；未发现可复现 P0。** 当前汇集版本不能据本次审查放行发布，需主 agent 修复下面的具体缺口并重跑定向测试。没有要求等待其他分区全库测试，也没有建议停策略、提高门槛、调整现有参数或强制开单。

关键问题已在审查过程中即时上报。这里的“阻塞”是发布审查结论，不是对运行中的策略发出暂停指令。

### 复现命令与结果

```powershell
wsl /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern test_cross_review_trading.py -v
```

最近一次隔离运行：**21 个独立新测试，15 PASS、6 FAIL，0 ERROR**；进程退出码 1。6 个失败是对期望安全/兼容性契约的真实断言，不使用 expectedFailure，不跳过、不掩盖问题。没有导入任何旧测试模块或重复跑旧测试全集。

Runner 使用一次性源码快照，排除实际 data/日志/配置/秘密；外部网络和真实子进程被禁止。测试中全部账户、余额、价格、身份、API/CLI 回执均为合成 fixture。LIVE 撤权测试运行真实 `submit_protected_limit_order`、`run_cmd_result` 和写操作 barrier，**仅在 subprocess 边界截获，不会真的发送订单**；preflight 用可控的已成功预检替身，专门复现预检完成到发送之间的窗口。生命周期测试运行真实 `_prepare()`、风险计算和 durable intent，只有外部账户/行情读取及当前配置用 fixture 替代。

## 范围与路径

已只读复审：

- `scripts/risk_policy.py`
- `scripts/capital_pool.py`
- `scripts/execution_profiles.py`
- `scripts/execution_leverage.py`
- `scripts/strategy_engine_runtime.py`
- `scripts/entry_gateway.py`
- `scripts/entry_reconciliation.py`
- `scripts/decision_authorization.py`
- `scripts/ai_factor_trader.py`
- `scripts/position_lifecycle.py`

必要调用方仅只读核对：分钟调度/物化、策略模式和分钟成本策略、决策证据持久化、账户绑定检查、CLI 写屏障、保护单覆盖、推理端 `position_basis` 构造、独立持仓守护的 small300 观测调用。账户基线、线程 frame、AI 管理授权、网关 profile hash、LIVE 绑定、手动 review API 已由主 agent 接管，本轮不修改这些实现。A07 plugin 解释器不在本轮范围。

路径：

1. `execution_profiles.runtime/settings_for` → `risk_policy.load_policy` → `execution_leverage.mode_policy`：取得当前 profile、预算参数与 horizon 限制。
2. legacy standard/small300 从 `execute_portfolio` 提案，分钟引擎从 `demo_scalp` 物化并固化决策/授权；最终进入 `entry_gateway._prepare`。
3. 网关核验账户/决策/策略、历史未知回执、USDT 权益、现仓/挂单/保护、实际杠杆、价格、预算，最后写 durable intent。
4. `submit_protected_limit_order` 存本地 horizon intent 后走 CLI；未知回执保留 reservation，由 `entry_reconciliation` 只读恢复。
5. 旧仓通过生命周期隔离、云端覆盖读回及风险降低管理；不因新仓预算不足而当然改杠杆或重置保护。

## 可复现发现

### CR-T01 — P1：LIVE 撤权已经落盘，但预检后的本地下单仍被发送

**位置**：`scripts/entry_gateway.py:370–375`；`scripts/ai_factor_trader.py:665–683`；`scripts/ai_factor_trader.py:162–170`；`scripts/strategy_engine_runtime.py:162–173`。

**条件与复现**：

1. 当前 managed LIVE 账户已显式授权分钟引擎，决策持有对应 binding/record_id。
2. gateway 预检成功并已创建 intent；进入 `save_horizon_intent` 阶段时执行真实 `disable_live()`，状态已变为 disabled。
3. 后续真实 `run_cmd_result` 仍到达 `subprocess.run` 下单边界。断言期望 0 次、实际 1 次。

测试：`ReleaseBlockingRegressions.test_live_revocation_after_preflight_before_dispatch_is_rechecked`。

**根因**：LIVE binding 最后一次核验在 `prepare` 内；调用者返回以后还有本地写入/组装命令，实际 dispatcher 只核账户当前性，没有复核这份分钟授权。撤销与重新授权的 record_id 绑定在 gateway 内有效，但没有覆盖这个最后阶段。

**推荐最小修**：在真实下单发送边界，用持久化决策/plan 的原始 binding 和 record_id 重新核验当前授权、账户与配置；不能只检查 enabled，因为撤销后重授会变为 enabled 但属于不同 record_id。对同进程/跨进程的撤权与发送建立明确顺序，尽量复用现有 writer/config 锁而不持锁等待模型。若确定尚未发送，记录明确的本地未发送结果，避免将其长期留作“交易所结果未知”；真正的超时请求仍必须保留 unknown 并只读对账。

**边界**：这是撤权发生在本地 dispatch **之前**的确定性复现；不要求系统撤回已经到达交易所的在途请求，不承诺网络/交易所原子撤销。

### CR-T02 — P1：同量重开新仓可消费旧仓加仓授权

**位置**：`scripts/entry_gateway.py:268–272`、`313–316`；上游 `scripts/ai_brain_trader.py:1019` 仅保存 side/size。

**条件与复现**：

- 推理时是 `posId=old-position, cTime=1000, side=long, size=1`。
- 推理后旧仓已关闭，出现 `posId=new-position, cTime=2000, side=long, size=1`；新仓保护有效、余额和价格均满足现有门槛。
- 真实 `_prepare()` 没有抛出拒绝，反而生成了新的 entry intent。对照测试中，相同生命周期的有效加仓正常通过。

测试：`ReleaseBlockingRegressions.test_old_scale_decision_cannot_attach_to_reopened_same_size_position`；对照：`PassedTradingBoundaries.test_same_lifecycle_standard_gateway_can_reserve_scale_in`。

**根因**：推理依据与预检现仓只比 size；稍后的 identities 检查只能发现“本次预检期间”的变动，不能发现“推理完成至预检开始前”的同量换仓。即便测试在 basis 中提供 posId/cTime，网关也忽略它们；当前上游甚至没有固化这些字段。

**推荐最小修**：在非零仓位的 `position_basis` 固化 account scope、instId、side、posId、cTime、size；网关用新鲜仓位核对完整身份。旧决策缺生命周期证据时仅要求该次加仓重新推理；零仓新开路径保持零仓语义，旧仓保护继续运行，不把生命周期问题变成新信号过滤或全局停策略。

### CR-T03 — P1：standard/no-pool 的风险预算仍把 USD 当作 USDT

**位置**：`scripts/capital_pool.py:233–235` → `scripts/entry_gateway.py:300–306` → `scripts/risk_policy.py:131–133`。

**条件与复现**：standard、可选资金池 disabled；已核对 observation 为 300 USDT，余额 `totalEq=330`（USD），`details.USDT.eq=300`。

`capital_pool.admit()` 返回 `Budget.equity=330`，后续普通 swing 的 `.005` 预算得到 **1.65 USDT**，而同一 USDT 权益基数下应是 **1.50 USDT**。反向汇率差异则会错误缩减可用风险预算。330 是为暴露量纲差异的合成值，不是当前市场价格判断。

测试：`ReleaseBlockingRegressions.test_standard_disabled_pool_budget_stays_in_usdt_not_totalEq_usd`。

**根因**：USDT 迁移已改 `equity_guard`，small300 `cap_allocation` 也做了转换，但默认 disabled 分支仍返回原 USD `totalEq`；standard 没有后续修正，形成部分迁移。

**推荐最小修**：disabled 分支也从同版本、已验证的 USDT observation 取得风险权益，或显式进行可验证的币种转换；禁止将 USD 数字无标识注入 `risk_usdt` 算术。不要改 `.004/.005` 参数，也不要将这个修复顺带变成专户门槛。

### CR-T04 — P1：USDT 迁移把可选资金池专户限制扩散到 standard，新增拒单

**位置**：`scripts/entry_gateway.py:102` → `scripts/capital_pool.py:206–209` → `scripts/capital_pool.py:197–203`。

**条件与复现**：standard、可选资金池 disabled、USDT 余额完整可用；账户另有 0.001 BTC，且所有 fixture 的 liab 都是 0。首次 `equity_guard()` 就抛 `Capital pool requires a dedicated USDT account; foreign assets are not attributed`，没有进入后续正常新仓风险计算。

测试：`ReleaseBlockingRegressions.test_standard_no_pool_does_not_gain_dedicated_account_requirement`。

**根因**：通用账户权益观察调用的 `currency_observation()` 同时携带原本属于专用资金池的 `check_account()`。本轮汇集前的 gateway 用 totalEq 做账户观察，未无条件要求 standard 账户清空其他资产。这是本轮迁移新增的准入限制，不是既有 RR/杠杆阈值。

**推荐最小修**：分离“提取并校验同版本 USDT 权益”与“已启用专用资金池的账户归属/负债检查”。仅在本来适用该约束的可选池/small300 路径应用专户规则；standard 的现金流与多资产权益口径需主 agent 明确实现，不能靠无授权地扩大专户要求解决量纲问题。不要移除已有明确适用的资金池约束，也不要伪造未知外部现金流。

### CR-T05 — P1：入场单 TTL 清理误撤风险降低退出单

**位置**：`scripts/ai_factor_trader.py:378–394`，调用位置 `2086–2095`。

**条件与复现**：普通 pending 订单为 `reduceOnly=true`、state=live、创建时间距今 600 秒。`clean_stale_open_orders()` 无视 reduceOnly，将其当超时 entry 调用 `swap cancel`。测试期望只读一次，实际发生一次读和一次撤单。

测试：`ReleaseBlockingRegressions.test_stale_entry_cleanup_must_not_cancel_reduce_only_exit`。

**根因**：docstring 声称只清理 stale entry，实际条件只有状态/订单 ID/240 秒年龄，未区分退出单。此问题在旧逻辑中已存在，首轮审查未覆盖；不把它描述为某个首轮 agent 新引入。

**推荐最小修**：筛选真实新增暴露订单，规范处理 reduceOnly 的布尔/字符串表示；reduce-only 退出单不得进入 entry TTL 路径。如需退出单自己的生命周期管理，另有明确语义，不共享入场失效计时器。

**边界**：复现的是普通订单接口上的风险降低退出委托，不声称这段函数取消了 algo 列表中的云端 OCO，也不声称已发生实际损失。

### CR-T06 — P2：small300 熔断观察恢复路径仍用旧 USD 比较

**位置**：`scripts/execution_profiles.py:102–111`；实际调用链见 `scripts/position_guard.py:44–48`。

**条件与复现**：已有 small300 allocation；账户 equity_guard 已持久化 USDT state 并因 blocked 抛异常，使 observation 参数为 None。持久化 state 是同一 `uTime` 的 270 USDT，当前余额是 `eq=270 USDT, totalEq=280 USD`。`observe_existing()` 在第 109 行比较 270 与 280 后拒绝，无法更新该 allocation 的 NAV。

测试：`ReleaseBlockingRegressions.test_small300_blocked_guard_fallback_accepts_same_usdt_balance_version`。

**根因**：fallback 仍假设 equity_state.equity 是旧 USD；正常 observation 分支已有 USDT 转换。USDT/USD 不完全相等时持续记录 observation_failed，损害已有 allocation 的连续观测。

**推荐最小修**：按显式 equity_currency 校验对应余额字段：USDT state 应比 details.USDT.eq，并保留严格同一时间版本检查；遗留 USD state 走已声明的迁移逻辑。不能直接删除金额校验，也不能把失败当成新分配并重置损失。

**边界**：独立保护调用方捕获这类观测错误，因此本测试不证明旧仓保护因此停止；确认的是 NAV/亏损连续观察受阻，故列 P2 而非夸大为必然裸仓。

## PASS 范围（只对已覆盖契约作保证）

15 个 PASS 测试验证：

1. **small300 有效单笔风险为 scalp 0.4%、swing 0.5%，不是 2%**。300 USDT fixture 分别得到预算上限 1.2 / 1.5 USDT；最终 size 仍受止损成本、lot、可用保证金和组合剩余风险裁剪。
2. small300 的 DEMO horizon 杠杆上限实测为 scalp 20、swing 5；这是 ceiling，不意味着每次都用满。`choose` 的默认新仓目标 scalp 10 / swing 3，现仓保持当前杠杆；LIVE 自动分钟杠杆还需要当前显式 binding。未调任何参数。
3. 1/3/5 倍算术对照中，同一 stop 风险预算的 size 和 risk_usdt 不因杠杆倍增，保证金随杠杆改变。
4. small300 在可选资金池 disabled 时，其自身 cap_allocation 已正确采用 USDT，而不是 USD totalEq。CR-T03 指的是 standard 未经过该修正的分支。
5. LIVE 没有持久化显式授权时 disabled；DEMO 配置不能代替 LIVE consent。
6. disable→authorize 会更换 record_id，旧持久化分钟决策不能通过 `_check_live_decision`；账户 binding_version 变化会令保存的 consent 失效。CR-T01 指的是最后 dispatch 阶段未再次消费这个正确契约。
7. 有现仓时 choose 不调整杠杆；在 apply 重读时发现同标的暴露只做 GET 并拒绝 set-leverage。
8. USD→USDT 无历史同点证据的迁移保留 blocked、保存 legacy state，明确 `interval_unattributed` 和 `historical_fx_reconstructed=False`；没有伪称重建历史外部损益。
9. reconciliation 对 timeout 保留 unknown，只做 GET；相同 account/decision 的 durable intent 不能再次插入形成重发。
10. 已知 acknowledged 的正向接单证据不能被空历史扫描抹成不存在；当前 pending snapshot 可恢复同一订单，不重复下单。
11. 更换旧仓生命周期时，旧 tracker 被归档并移出当前映射，证据中 `cloud_stop_changed=False`；这不等于入口加仓 basis 已修，见 CR-T02。
12. 云端覆盖读取未知时不盲目取消/补发保护；返回 UNKNOWN。
13. `validate_management` 对不同 position ID 或账户 binding version 拒绝；本轮仅验证该 API 的输入契约，不宣称主 agent 尚在整合的所有最终执行分支都已覆盖。
14. 完整 `_prepare()` 的同生命周期 standard 有效加仓控制样例能生成 32 字符 intent，最终风险不超过 1.5 USDT，杠杆保持 3；这排除了 CR-T02 因整体 fixture 无效导致的假阳性。

## 修复后验收与非承诺边界

- 主 agent 修复后直接重跑本文件；6 个失败断言应自然通过，不应删除/跳过来换绿。
- 最小修必须保留现有有效候选和原预算参数，不追加信号门槛，不用低频成交替代正确性。
- 不读取真实秘密、余额或订单，不发送真实授权/撤权 API。本地测试的 authorize/disable 只落在临时目录。
- 不承诺撤回已发送的在途订单、杜绝手动/外部账户交易、交易所读写原子性、保证 maker、保证滑点或绝对避免清算。未做实时网络、实盘回执或生产持仓验证。
- 外部第三方恰在预检后改变仓位是不可完全控制的市场/账户边界；**本报告 CR-T02 是新仓在预检开始前已存在且可读、代码却忽略已提供生命周期字段**，不是把外部风险错误当作本地可完全修复的承诺。
- 本轮未修改生产文件，未 commit/push/部署，未调参，未再委派。所有实现修改转交主 agent，审查者不等待他人全库测试。

## 复审生产文件指纹

以下 SHA-256 对应定位行号的本地生产源码；并行修改后应以函数名和复现用例重新定位。

```text
risk_policy.py f153598f91e74438a930561664e27384691cdd51501ffdd12ae85495477a067a
capital_pool.py 4b9b6e57a7693b1dd9c18582716c2579474aca565fb9831e2e09965b11a57ffd
execution_profiles.py 4f5ecdc94d3c7f1eaaa5e82ee083a0947416357f5ca97ee0509c51b845de7763
execution_leverage.py e0aee59bda0ea19d9428a90f02715fe5020f9261adbf6cf7bc6169655954d5ed
strategy_engine_runtime.py 88094e306899923fdfb9e28ffa9418abde7390784f2b48f49b93961582272c38
entry_gateway.py 24b0f733510c9c3d31e5f9cbedc4a83113d4f78df45c0ac82266b9e4a582a765
entry_reconciliation.py 4d90c73226c05099b4a79ed2302eabef4b5efe058b97dfc00d4096dd9dea1bff
decision_authorization.py ea99ff86bbdcfe7a4f6b97e2381df463996253a06a67366780aec885652a4d97
ai_factor_trader.py 0833ea6739f8fd263d811deb8fd4980c3ca752dc52e473ed5f870b087a721193
position_lifecycle.py 8f86e2fbde4d4ba1330d1c700d44a628cd66fb9e4292029f79bd50b06f1f1eee
```

---

## 最终关闭复核（2026-09-19）——本节取代前述历史 BLOCKED 结论

**结论：CR-T01～CR-T06 全部关闭；在本次交易链路定向审查范围内可发布，无剩余阻塞项。** 前文的 5 P1 / 1 P2、15 PASS / 6 FAIL 以及旧源码指纹保留为修复前审计记录，不代表当前版本状态。

本轮按主 agent 要求，仅只读核对这六项修复及直接调用关系，没有扩展其他审查范围，没有修改生产代码。仅在原 LIVE 撤权用例追加 `not_submitted` 持久化及不进入 recoverable-intents 的断言，并追加本节。

### 独立复跑

```powershell
wsl /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern test_cross_review_trading.py -v
```

补充断言后的最终结果：**21/21 PASS，0 FAIL、0 ERROR，退出码 0**。保留六个原失败断言，没有删除、跳过或转成 expectedFailure。主 agent 报告的全部 cross-review 79/79 不作为本人的重复执行结果；本人仅独立复跑本文件。

| 事项 | 关闭依据 |
|---|---|
| CR-T01：撤权后的末端下单 | `decision_authorization.entry_dispatch_guard` 在最后 CLI 调用外持有 LIVE consent 文件锁及适用的 prompt 文件锁，核对账户、策略 hash、execution signature、完整 engine binding 与 record_id；gateway 将所需字段写入 plan。授权写入/撤销使用同一 consent 锁，prompt 写入使用相同配置锁。原撤权窗口测试不再到达 subprocess；新增断言确认 intent 为 `not_submitted`、不属于未知回执恢复集合。已开始发送的异常仍保留 unknown，不伪称未发送。 |
| CR-T02：同量重开仓 | producer 的 `position_basis` 已携带 posId/cTime；gateway 对非零仓位除 size、方向外，要求唯一仓位及匹配的 posId/cTime。旧生命周期被拒绝，同生命周期正常加仓控制用例继续通过。 |
| CR-T03：standard USD/USDT 混用 | disabled-pool 的 Budget 现在使用 `usdt_equity(balance)`；300 USDT / 330 USD fixture 的风险基数为 300、普通 swing 预算为 1.50 USDT。 |
| CR-T04：standard 新增专户门槛 | 通用 equity_guard 使用独立的 `usdt_equity`；专用池的 `check_account` 仍留在池路径。standard/no-pool 加少量 BTC 的原失败 fixture 通过，不再因这次 USDT 提取而触发专户拒单。 |
| CR-T05：退出单被 entry TTL 误撤 | TTL 循环先排除归一化后为 true/1 的 reduceOnly；原超时退出单 fixture 仅发生读取，不再调用 cancel。 |
| CR-T06：small300 fallback 旧单位比较 | fallback 保留同一 uTime 检查，并将已保存 equity 与 USDT 组件比较；270 USDT / 280 USD fixture 正常更新已有 allocation，不重建/清零历史损失。 |

### 可发布范围与剩余边界

- 可发布范围：本报告覆盖的 standard/small300 预算与币种分支、同向加仓生命周期绑定、DEMO 与显式授权 LIVE 分钟入口的末端授权、reduce-only 退出单 TTL 豁免、small300 熔断观测恢复，以及原 15 项 PASS 的回执/保护契约。
- small300 有效单笔风险仍为 **scalp 0.4% / swing 0.5%**，不是 2%；本轮未改这些参数。LIVE 仍需当前账户/策略对应的显式授权，可发布不等于已获准实盘运行。
- 本次关闭针对六项可复现代码问题，不是整个仓库或所有生产配置的无条件上线背书，也不覆盖 A07 plugin 解释器等其他分工。
- 锁和末端校验约束的是遵守这些配置写入口的本地顺序；不能撤回已发送到交易所的请求，也不能阻止交易所侧或外部操作者随后改变仓位。未知外部回执仍须只读对账，不允许盲目重发。
- standard 使用 USDT 组件完成此处预算计算；不将其宣称为对全部非 USDT 资产做了完整投资组合压力测试。资金池专户/负债要求仍按其原适用路径执行。
- 历史 USD→USDT 迁移无可验证同点证据的区间仍标记 unattributed，不能把兼容桥接解释成已重建历史汇率或盈亏。
- 未做实盘网络、费用/滑点、交易所读写原子性、清算或在途撤单验证，不作收益或绝对安全承诺。

**本轮生产文件保持只读；未 commit/push/部署、未读取秘密、未再委派。交易复审分工结束。**
