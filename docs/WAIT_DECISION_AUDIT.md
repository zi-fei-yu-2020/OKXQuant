# WAIT 决策审计与日志分类

## 目的与边界
`trading-evidence-v1` 增加必须执行的 WAIT 子契约 `wait-evidence-v1`。不更改开仓、撤单、止损、风控预算或模拟盘设置，不设交易频率配额。

审计通过只表示引用、条件、价格计算和跨轮复查可核验，不证明没有经济优势。审计失败保持不下新单，但明确标为 `decision_status=incomplete`，不再伪装为正常 WAIT。

## WAIT 输出
每个 WAIT 必须有 `summary_reason` 和 `wait_audit`：
- `version=wait-evidence-v1`。
- `long`、`short`：分别包含 `code`、`reason`、`evidence`、`reconsider`。
- `code` 为 `no_setup`、`confirmation_pending`、`data_missing`、`position_constraint`、`net_rr_below_minimum` 之一。
- evidence 引用格式与开仓相同，必须匹配当前标的 facts 的真实值。缺数据须列出实际缺失的已知字段；反向仓位/浮亏约束须匹配实际持仓。
- `reconsider.conditions` 为1～3个 ALL 条件，每个含 `ref/op/value`。支持 gt/gte/lt/lte/eq/ne，缺数据仅用 available。已满足、缺少操作数、非有限值或不可能的动力学阈值不能用来继续等待。
- 声称净盈亏比不足时必须提供 `geometry` 中入场/止损/止盈及引用；程序使用既有手续费、滑点及最低净盈亏比重算，只否定此具体方案，不证明所有价位均无机会。
- 普通限价单不等于未来指标确认。可以研究已成立假设的限价候选，依赖未来确认的方案仍需 WAIT。

## 跨轮复查
- 状态按账户环境身份隔离保存至 `data/wait_audit_<scope hash>.json`，原子替换，不包含凭据。
- 模型下一轮输入 `previous_wait_reviews`：前轮条件、程序计算的 met/not_met/unknown、变更字段、review_id、是否必须复查。
- 条件触发、不可计算、等待基准超过1小时，或模型主动修改条件时，必须提供 `previous_review`，精确匹配review_id并引用至少1项变化的新证据解释继续等待。
- 相同条件逐轮重复不会重置1小时复查基准。
- 本轮审计失败不会丢弃上次有效条件，避免通过错误输出绕开前轮触发。
- 8轮连续没有开仓候选触发诊断提示；不会自动降低阈值或强制交易。计数从新版部署后开始，不把旧版无审计WAIT追认为审计通过。
- 状态损坏或持久化失败明确显示不完整；不得偷偷重置历史。独立持仓风控继续运行。

## 日志与前后台
`trading_state.json` 新增 `decision_cycle` 与 `environment_notices`。
- `动作` 保留真实操作、维护与执行结果。
- `决策` 列出审查标的数、候选数、等待审计通过数、决策不完整数以及逐币多空阻碍/失败原因。
- `环境限制` 单独列出WLD等观察标的，不再写入 `executed_actions` 冒充唯一交易动作。该标的开仓拦截和已有仓位保护完全保留。
- 公共 `/api/all` 与管理员 `/api/v1/admin/runtime` 提供 `wait_audit` 和 `decision_cycle`。
- 观察面板与管理员决策页共用 `DecisionAuditPanel`，展示上次审计时间、证据、条件与复查解释；旧版或不完整WAIT不显示为审计通过。
- 未产生模型结果、熔断与环境限制不是“正常等待”，日志明确区分。

## 验证
使用 `scripts/run_tests.py` 在无凭据、无网络、禁子进程的临时源快照运行测试；不得在实时数据目录直接 discovery。
覆盖证据伪造、缺失字段、价格几何、净成本、条件命中、条件偷偷迁移、过期基准、环境隔离、原子写失败、长期等待告警和WLD日志分类。
部署前的模型兼容性检查只重放历史快照，并在临时目录保存跨轮状态；没有交易写入。线上验收等待正常15分钟调度，不额外触发交易。
