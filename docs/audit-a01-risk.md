# A01 — 资金、杠杆、组合风险与模拟/实盘语义审计

日期：2026-09-19。工作区为多人并行修改；本报告仅归属 A01，不代表全库审计已完成。

## 范围与约束

完整审查以下授权模块：

- `scripts/risk_policy.py`
- `scripts/capital_pool.py`
- `scripts/execution_profiles.py`
- `scripts/execution_leverage.py`
- `scripts/strategy_modes.py`
- `scripts/execution_costs.py`
- `scripts/demo_scalp_policy.py`

实际修改前三个模块，新增 `tests/test_audit_a01_risk.py` 与本报告。后四个模块没有为重构而修改，关键语义由新增回归覆盖。

辅助只读核对：`entry_gateway.py`、`position_guard.py`、`okx_runtime.py`、`demo_scalp.py`、`ai_factor_trader.py`、`prompt_library.py`、`exit_policy.py`、`backtest_engine.py`、`entry_research.py`、`instrument_pool.py`、`strategy_evidence.py`、账本/账户基线相关源码、README、资金池/执行预设/执行生命周期文档，以及相关测试。辅助核对不等于完整审查这些模块；没有写入其他 agent 的范围。

未读取真实 data 内容或秘密文件；测试没有访问真实 data、交易所或模型服务；未部署、提交、推送、委派或调整实际账户。

## 先核对的现有契约

1. 当前代码与 `test_requested_improvements.py` 确认 small300 基础预设为净值上限 300 USDT、单笔风险上限 2%、单标的保证金 150 USDT、总保证金 270 USDT、基础杠杆上限 6。旧说明的 0.5%/30/90/3 已过时，不能据此回退代码。
2. **基础预设不等于最终模式参数。** `mode_policy` 按现有模式继续取交集：scalp 单笔上限 0.4%，swing 0.5%；demo scalp 使用独立 `scalp_max_leverage`（默认 20），swing 杠杆上限 5。small300 的 live scalp 上限为 6，live 不自动设置杠杆。300 USDT 满额时最终模式单笔风险通常最多为 1.2/1.5 USDT，仍可能被请求预算、组合剩余额度和保证金进一步缩小。
3. 可选 `capital_pool` 与 small300 预设不是同一开关。前者默认关闭、显式启用需专属账户/虚拟额度确认，live 还需独立 opt-in；它可与 small300 叠加取较小额度。
4. 虚拟净值 = 初始额度 + 已剔除外部资金流的账户权益变化。切换、重启和入金不能重置亏损；首次分配仍要求空仓且无入场挂单；不接管、不缩放已有大仓。
5. 可用保证金与虚拟保证金余量取小，而非把交易所已经扣除的已用保证金再从可用余额减一次。已有 `available_margin_fraction` 继续在最终订单计算中保留。
6. 费用是每基础币价格单位，不能按杠杆倍数放大。maker-entry 只是准入参考，不是限价单 maker 成交保证；DEMO 的 1.2 净 RR 采样策略不授权 live。

上述比例、频率、最小手数处理、费用假设、保护与熔断阈值均未修改。

## 已修复问题与证据

### A01-01 / P1：small300-only 分配丢失 enabled 标志

- 原因：`cap_allocation` 仅替换净值/可用额度，保留可选资金池关闭时的 `Budget.enabled=False`。
- 后果：对于非 minute-engine 的 small300，`entry_gateway` 跳过其已有的最终挂单复核，且不写 `plan['capital_pool']`；预检期间新增挂单可未被该复核发现。
- 修复：small300 成功分配后返回 `enabled=True`，保留可选资金池配置签名与完整 detail。
- 这不是新准入过滤，而是让已经启用的虚拟资金分配走已有 reservation race protection 和证据路径。
- 证据：真实 `entry_gateway.prepare` 离线回归中，修复前挂单仅查询一次；模拟第二次查询新增挂单仍返回成功。修复后稳定查询两次，变化时由已有保护拒绝，且没有持久化新订单 intent；稳定时正常返回计划及资金明细。
- 测试：`test_small300_gateway_records_virtual_budget_and_rechecks_pending`、`test_small300_gateway_uses_existing_reservation_race_protection`。

### A01-02 / P2：USD totalEq 再次压低已验证的 USDT 风险净值

- 原因：可选资金池关闭时，`Budget.equity` 来自 USD `totalEq`；small300 完成 USDT 分配后仍做 `min(allocation.equity, state.risk_equity)`，混合两个计价单位。
- 确定性复现：USDT eq=300、折算 totalEq=270、资金池 USDT risk_equity=300，原代码返回最终 equity=270。
- 修复：没有其他已启用 USDT 资金池时，直接使用 small300 的 USDT risk_equity；有可选资金池时才在两个 USDT 净值之间取小。
- 验证：实际权益仅 250 时仍只得 250，不凭空补足 300；两个池叠加 200/300 只得 200，不再扣一次亏损；可用余额不重复扣已用保证金。
- 测试：`test_small300_risk_equity_is_usdt_not_converted_usd_minimum`、`test_small300_never_synthesizes_300_from_a_smaller_usdt_account`、`test_nested_pool_caps_intersect_without_second_loss_deduction`。
- 范围限制：网关前置 USD 账户回撤仍有跨域问题，见剩余风险，不能宣称币种问题已全链路消除。

### A01-03 / P1（可用性）：demo 额度登记阻断独立 live 初始化

- 原因：`assert_new_scope` 扫描所有 scope，只按 allocation_id 查重，不区分 demo/live。small300 固定使用 `execution-small300-v1`，因此曾经启用 demo 就能阻断 live 的同名分配，即使实际账户/权益完全独立。
- 修复：新状态记录 `environment`；检查迁移时跳过明确属于另一环境的同名额度。老状态通过既有规范 `okx:demo:` / `okx:live:` scope 识别环境。
- 安全保留：同一环境换 key/账户仍要求显式迁移；无法辨认环境的旧自定义 scope 仍不自动重置；可选 live 资金池仍需 allow_live；没有将 demo 仓位、损益或回撤复制到 live。
- 验证：demo 净值降至 297 后，live 独立初始化为其实际权益约束下的 300，demo 仍为 297；可选资金池显式 live opt-in 也正常独立初始化；旧规范 scope 和新自定义 scope 均有覆盖。
- 测试：`test_demo_to_live_initializes_separate_small_allocation`、`test_explicit_live_pool_does_not_migrate_demo_losses`、`test_legacy_canonical_demo_scope_is_not_a_live_migration`、同环境迁移及未知旧 scope 保守处理测试。

### A01-04 / P2：已无剩余数量的挂单仍阻断组合风险

- 原因：`exposure` 在计算剩余数量前访问元数据、方向和止损；当完整成交但尚残留于挂单快照的行缺少这些字段时，会抛 KeyError/RiskRejected，尽管剩余风险为 0。
- 修复：先计算 `sz - accFillSz`，零剩余立即跳过；实际已成交风险仍由持仓及保护覆盖校验计算。
- 部分成交的剩余数量仍严格要求有效方向、元数据与止损，未放宽保护要求。
- 测试：`test_zero_remaining_entry_has_no_stop_or_metadata_requirement`、`test_partially_filled_entry_reserves_only_remainder_and_still_needs_stop`。

### A01-05 / P2：环境变量覆盖自定义 profile 的显式资金模式

- 原因：原 `settings_for` 将 `OKXQUANT_CAPITAL_MODE=300/small300/small` 无条件置于保存的 `execution_profile` 之上；选择显式 standard 后仍可执行 small300。
- 与主 agent 协调后修复优先级：内置 id=small300 固定绑定；否则显式 `execution_profile` 优先；只有字段缺省的旧调用才使用 env 回退。未知显式绑定仍沿用原拒绝语义，不被 env 掩盖。
- 签名结构没有改变：`sha256(json.dumps({'profile_id': ..., 'execution': ...}, sort_keys=True))`。相同 profile 改绑会改变签名；被显式绑定覆盖的 env 变化不改变实际配置或签名。
- 测试：`ProfileBindingTests` 三项。

## 测试结果

所有命令通过 WSL 指定虚拟环境执行 `scripts/run_tests.py`；runner 创建临时源快照、空 data/logs、受控环境变量，默认阻断未 mock 的网络与子进程。

```text
wsl.exe --cd /mnt/d/wangkai/workspace/OKXQuant /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a01_*.py' -v
```

- 修复前先运行 19 项 A01 用例：4 failures + 4 errors，覆盖上文 A01-01 至 A01-04 的具体故障；没有先改代码再凭空声称复现。
- 修复后最终 A01：**27/27 PASS**。
- 另分别用同一隔离 runner 执行：
  - `test_capital_pool.py`：38 PASS
  - `test_requested_improvements.py`：14 PASS
  - `test_strategy_risk.py`：24 PASS
  - `test_execution_lifecycle_repairs.py`：15 PASS
  - `test_demo_scalp_policy.py`：4 PASS
  - `test_exit_policy.py`：20 PASS
- 合计新增与相关回归 **142 项通过**。不是全量测试结论；其他 agent 同时修改源码，主 agent 仍需最终整合快照验证。
- 授权源码和新增测试的 `git diff --check` 通过。
- WSL 打印宿主 localhost 代理映射告警，不影响离线测试；没有网络连接或真实下单。

## 主 agent / UI 接口协调

1. 自定义 profile 需明确保存 `execution_profile: 'standard' | 'small300'`；该绑定现优先于 env。`runtime()` 返回形状仍为 `{profile_id, execution, signature}`，无缓存；读取已保存 profile 即生效，旧决策由既有签名门禁拒绝。
2. 保持 `active_profile_id` 与 `active_profile()['id']` 一致；损坏激活记录仍不得静默扩大预算。
3. UI 应以 `runtime()['execution']` 展示实际基础预设，不复刻 env 优先级，不在 prompt 描述中硬编码参数。`standard` 配置只有 id/label，详细基础风险仍需 `load_policy()`；模式实际限制还要结合 `mode_policy()`，不能把预设 2%/6x 描述成所有开仓路径的最终参数。
4. `Budget.enabled=True` 现在也可能代表 small300-only，不只代表 `capital_pool.json.enabled`；当前网关用途是已有挂单复核/计划证据，二者都应适用。可选资金池的 `config_signature` 没有被覆盖。
5. `capital_pool` 状态增加可选 `environment` 字段，不改变数据库 schema、allocation fingerprint 或 VERSION；旧规范 scope 可兼容，未知旧自定义 scope 仍需审查迁移。

## 跨域依赖与剩余风险（未擅自修改）

### P1：全局账本日亏损门禁未按账户/环境隔离

`risk_policy.ledger_daily_drawdown` 聚合所有当日 closed 行，未按账本已有的 `environment_id` 过滤；`entry_gateway` 和 `ai_factor_trader` 的调用不传账户身份。它还使用全局 `account_initial_state`，`account_baseline.py` 的未配置默认本金为 10000。共享账本保留多账户/环境已平仓记录时，可相互抵消或误触熔断，默认大本金也不等于 small300 额度。

完整修复必须协调调用方的冻结账户身份、账本旧记录处理和账户基线归属；没有通过取消原熔断、降低频率、猜账户或把所有本金统一写成 300 来绕过。当前独立账户盯市/资金池回撤仍保留，但不能替代该门禁的账户隔离修复。

### P2：网关前置账户回撤仍混用 USD 权益与 USDT 资金流

`entry_gateway.equity_guard` 使用 totalEq，并直接累计 USDT 转账。small300 的 USDT 资金池校正发生在其后；因此 A01-02 只解决最终分配的二次取小，不能阻止前置账户门禁因计价变化误触。standard 且可选资金池关闭时的 `capital_pool.admit` 也维持 legacy totalEq 计价，没有擅自重新定义多币种账户权益。需网关 agent 统一 observation 的币种契约与兼容迁移。

### P2：small300 盯市基线并非由独立 guard 持续刷新

`capital_pool.observe_existing` / `initialize_flat` 只处理可选 pool，small300 的独立 scope 当前在 `cap_allocation` 中更新。没有新候选期间可能漏掉中途净值峰值/日边界；重新进入时累计净值变化仍会保留，但不能假称已经记录所有中途回撤。修复需要 position_guard/网关观测链路协调；不能为了“补齐”而从当前持仓猜首次基线。

### P2：固定请求预算和回放/执行差异需要分别说明

`demo_scalp.py` 的请求预算仍有 `min(instrument_budget, 15)`，instrument_pool 有 15 默认值；最终风险网关继续把请求当上限。它不会给小账户凭空增加本金，但可能使大额 standard 请求达不到比例额度。预算来源在 A01 之外，不按未经确认的新策略需求修改。

回放引擎有可配置的初始资金及撮合假设，不能把其默认研究资金当实际余额。真实 limit 单可 taker 成交，DEMO 1.2 RR 策略禁止 live，live 不自动重设杠杆；从 demo 切 live 不是复制模拟成交或认证收益。

### 保留的设计边界

- small300 是虚拟账户级 cap，不是独立交易所钱包，也不是每份 prompt profile 的资金子账户。
- 同一账户切换 profile 不重建 small300 loss baseline；期间同账户其他交易的权益变化无法在现有专属账户模型下自动拆分归因。
- 同环境账户/key 切换仍保守要求迁移，未知旧自定义 scope 不能自动判断环境。
- 首次选择 small300 时已有仓位仍不创建新基线；保护和退出不因本模块强平或重设杠杆。仓位生命周期的退出参数绑定由 exit-policy agent 管理。
- 没有新增准入过滤、提高阈值、降低策略频率、停开仓或放弃最小手数/真实杠杆/保护覆盖安全要求；也没有利润预测或实盘成交等同模拟的承诺。
