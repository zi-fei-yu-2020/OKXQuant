# A07 账户 / 安全 / 交易读取审计（实际第 9 个 agent）

日期：2026-09-19。工作目录：`D:/wangkai/workspace/OKXQuant`。

## 结论与边界

- **授权域内的修复已落地；线程池调用方和直接读本金文件的调用方仍需跨域接入，不能宣称全链路闭环。**
- 只写用户授权的 A07 源文件、`tests/test_audit_a07_*.py` 和本文；未撤销他人修改，未改 A03 reconciliation 或 A01 risk_policy。
- 未读取真实凭据内容，未执行账户探针/订单/远程写入，未启动服务、提交、push、部署或再委派。
- 没有新增开仓风控阈值、降低杠杆/资金上限、禁用策略或扩大 OAuth 自动交易资格。错误账户/损坏凭据/过期人工确认被拒绝，属于账户正确性边界。
- 所有验证都在 WSL 的一次性源码副本执行；运行器不复制 `.env`、真实 data/logs/home，并阻止未 mock 的网络、DNS 和 subprocess 操作。

## 一、问题与修复

### A07-01：全局本金在账户/环境切换后错误复用

`account_baseline.py` 原来只保存一个全局数值，没有账户归属。

现已提供：

```python
load_account_baseline(scope=None)
update_initial_capital(initial_capital, scope=None)
preserve_account_baseline(scope=None)
```

`scope` 可传 `env.identity` 或环境对象；省略时解析当前选定账户。返回值含 `account_scope`、`baseline_configured`、`baseline_source`。

v2 文件包含 `baselines[account_scope]`，顶层仅作为最近一次保存的兼容投影，**不再是跨账户默认值**：

- 读操作不写盘、不自动认领旧文件。
- 同 scope 修改保留 reset_time、交易统计等历史字段。
- 旧文件明确带 account_scope 时，仅所属账户可使用；迁入 v2 时保留其记录。
- 没有 scope 的旧明确本金仅保留原 DEMO 兼容语义；首次切换前绑定到实际 outgoing DEMO identity。
- **无主旧本金、无 scope 的 INITIAL_CAPITAL 都不会自动成为 LIVE 基线。** LIVE 需要显式更新或明确匹配的 `INITIAL_CAPITAL_ACCOUNT_SCOPE`。
- 旧 `baseline_configured=False` 占位值不被升级成真实本金。
- 首次迁移保留完整 `legacy_baseline`。账户切换不再移走整个本金文件，其余运行态/账本仍按原规则归档；DEMO→LIVE→DEMO 的各自记录可找回。
- 金额有限性、有效范围及非布尔校验；configuration_write 串行 read-modify-write；临时文件/fsync/replace/0600；文件损坏时不覆盖旧内容。
- 未配置基线仍返回兼容展示默认数值，并明确 `baseline_configured=False`，**调用风控必须检查此标志，不能把展示默认值当真实本金**。

### A07-02：全局 signal frame 跨线程污染、单次请求重复取时

`public_market._SIGNAL_AS_OF` 改为 ContextVar；`signal_json` 在网络读取前只捕获一次 as_of_ms，用同一值筛选闭盘并返回时间戳。

新增 `bind_signal_frame(function)`：**在提交线程中调用**，捕获当前 context，每次 worker 调用使用独立 context copy；支持并行/复用，异常后恢复 worker 原上下文，同时继承 deadline 和冻结环境。

`okx_runtime` 的冻结环境也改为 ContextVar，新增 `current_environment()`；`public_market._selected()` 统一使用它。避免另一个线程 freeze/unfreeze 破坏本轮身份。

**尚未完成的部分**：ContextVar 不会自动传进普通 ThreadPoolExecutor。四个真实业务调用方不属于 A07 写权限，必须按下节接入。当前回归证明隔离、显式继承、异常恢复、异步 task 隔离、单次取时及闭盘筛选，不冒充真实调用方已接通。

### A07-03：凭据组混配与损坏密钥库静默回退

- `legacy_environment` 不再逐字段把部分 LIVE/DEMO 专用凭据与全局旧凭据拼接。专用组三项是整体；不完整组不会转而使用 OAuth。
- 显式 `{}` 不再因 truthiness 意外读取进程/.env。
- `cli_env` 清除父进程残留的泛用 OKX 三项后才注入所选组；`_run_cli` 显式传入该子进程环境。
- `_load_dotenv` 对已存在但损坏/无法解密的 vault 使用 strict read；不会静默套用旧 .env/继承凭据。
- 旧 `replace_cli_prefix` 的进程环境副作用尚需调用方迁移，详见跨域依赖。此次未把凭据拼进命令字符串或日志。

### A07-04：并发配置更新和管理员计数丢失

- `secrets.py` 对 store 的 save/delete 使用 configuration_write；首次主密钥创建使用 key-file 锁并二次检查，避免并发生成不同主密钥造成密文无法读取。
- save/delete 对损坏或丢失主密钥的现存 store 拒绝覆盖，保留原始密文。
- `admin_auth.login` 用 BEGIN IMMEDIATE 串行读取/校验/失败计数，保留原 5 次失败锁定规则，修复并发覆盖计数绕过。
- 超管停用前的最后一个有效超管检查也纳入同一写事务。

### A07-05：人工平仓意图跨换绑失效不充分、并发重复与数据异常

- close token 同时绑定 canonical identity、connection_id、binding_version、credential fingerprint；同账户重绑定也使旧 token 失效。
- 整个“读取/确认/撤单/平仓/确认归零”使用已有可重入 trade writer，而非仅逐个 POST 加锁。
- 保留 token 一次性消费；clOrdId 改为 token 派生的唯一短 ID，避免同秒多次操作重名。
- 保存/验证净持仓有符号数量，拒绝用旧多头确认去平相同绝对数量的新空头；拒绝非有限/零确认数量，非有限回读不能冒充平仓成功。
- 未增加任何策略开仓门槛，也未修改策略平仓/对账算法。

### A07-06：读取契约、安全边界与日志

- `OKXClient` 私有请求复用 `okx_trade_service._request_untracked`：统一冻结绑定复核、结构校验、503 GET 重试、业务错误分类及 POST 不重试。
- 私有签名调用只接受固定官方 base 与现有 READ_PATHS/WRITE_PATHS；没有增加新交易 API 或改变现有开单路径。
- 签名头改用 unredirected headers，防止 urllib 将 Key/签名/Passphrase 转发至重定向目标；这不等同于彻底禁止重定向，残余见后文。
- 结构化业务异常保留现有 OKXAPIError（包括 51603），对错误 msg 中所选凭据作脱敏；CLI 进程失败/非法 JSON 不再回显原始 stdout/stderr。
- audit JSONL 串行追加、0600、敏感字段递归脱敏；磁盘/锁失败返回 false 并发出无敏感信息 warning，不把已经成功的管理动作变成可重试失败。recent 流式保留尾部记录。
- outbound URL 拒绝控制字符/反斜杠及 DNS 空结果；继续检查全部解析地址，私有存储 opt-in 仍不能访问 localhost/link-local/metadata 等保护地址。
- 账户中心与旧 setup OAuth 入口统一验证官方 HTTPS 授权链接、端口和无 userinfo；旧入口补上官方域名检查及 1800 秒有效期上限。不访问授权链接。

## 二、完整范围覆盖矩阵

| 文件 | 审查与结果 |
|---|---|
| `okxquant_backend/admin_auth.py` | PBKDF2、session 哈希/过期/注销/禁用撤销、超管边界；修复失败计数和停用竞态。 |
| `okxquant_backend/audit.py` | 私密性、并发追加、脱敏、持久化失败不诱发重复写；已修改。 |
| `okxquant_backend/account_connections.py` | encrypted registry、mutation guard、flat-account 检查、unknown intent、UID canonical scope、bind/version/unbind、news 单独用途；增加本金迁移保留和 OAuth 统一验证。 |
| `okxquant_backend/account_routes.py` | 全部账户路由显式 require_superadmin/header-session；无 Cookie 授权回退；用途/确认字段继续保留。未修改。 |
| `okxquant_backend/account_baseline.py` | scope、迁移、数值、并发、损坏保留；已修改。 |
| `okxquant_backend/connection_transport.py` | news/probe 只读 allowlist，OAuth 独立 HOME、fd3 token、身份核验、NoRedirect、禁止未认证 OAuth 写闭环；保留现有边界。未修改。 |
| `okxquant_backend/net_security.py` | URL/DNS/私网 opt-in 边界、官方 OAuth link；已修改。DNS 验证与实际连接间仍有 TOCTOU。 |
| `okxquant_backend/okx_client.py` | 私有原生客户端统一传输契约；公共行情仍无凭据。已修改。 |
| `okxquant_backend/okx_setup.py` | 安装/诊断/OAuth 路径静态审查；仅 mock 测试，不安装、不探针。补官方链接校验和显式空 env 语义。 |
| `okxquant_backend/okx_trade_service.py` | snapshot/frozen env、人工意图、整笔锁、业务异常、签名、API allowlist、显式 CLI env；已修改。 |
| `okxquant_backend/okx_request_transport.py` | 现有 GET 最多三次/总预算、重签名且每次复核绑定；POST 一次；503/429、TLS、非法 JSON、证据脱敏、51603 分类回归。未重复改已有 OKXAPIError 或恢复算法。 |
| `okxquant_backend/okx_read_service.py` | balance/positions/orders/bills allowlist，保护单专用 reader；没有 live fallback。未修改。 |
| `okxquant_backend/chart_market.py` | public/read_only/不带 demo 私户凭据、OHLC 几何、有限值、成交量/turnover、确认位、排序/重复/间隙/延迟、502 不造 K 线；原有回归通过。图表可显示 intrabar，决策必须走闭盘接口。未修改。 |
| `okxquant_gateway/secrets.py` | 加密主密钥与 store 并发、权限、损坏保护；已修改。 |
| `scripts/okx_runtime.py` | demo/live 原子凭据组、ContextVar 冻结、严格 vault、CLI child env；已修改。旧 string CLI 副作用待下游接入。 |
| `scripts/public_market.py` | 公共 allowlist、缓存边界/无 stale-on-error、跨进程限速、frame、deadline、closed candles、Smart Money news 绑定与凭据轮换 cache key；修改 frame 和冻结环境读取。 |
| `scripts/instrument_pool.py` | 原子 pool 写入与状态投影、合约规格字段；未新增交易门槛。共享状态重写仍需各写入方统一锁/归属，未跨域改其运行状态消费者。 |
| `scripts/instrument_support.py` | demo/live 分离 catalog；请求失败 UNKNOWN 不伪装 unsupported；保留已有持仓管理；现有测试通过。未改变 opening_status 策略。 |
| `scripts/okxquant_okx_setup.py` | 诊断入口读取所选 mode/configured，调用 setup；静态检查、未实际运行。 |
| `scripts/check_live_readonly.py` | 名字中的 live 表示真实调用，不代表允许 LIVE 账户：现有 confirm-demo-readonly 且 mode==demo 校验保留；可选付费模型探针与本地报告写入均未运行。 |

## 三、必须交给主 agent / 文件所有者的跨域依赖

### 1. 四处真实线程池必须在父线程包装 callable（优先）

不改 executor 全局行为，不 monkeypatch ThreadPoolExecutor，不在 worker lambda 内才捕获 context。

- `scripts/ai_brain_trader.py`：
  `executor.map(market.bind_signal_frame(fetch_single_instrument_package), eligible)`
- `scripts/ai_factor_trader.py`：
  `executor.map(market.bind_signal_frame(lambda item: fetch_single_instrument_data(item, all_positions, usdt_available)), TARGET_INSTRUMENTS)`
- `scripts/factor_library.py`：
  `executor.map(market.bind_signal_frame(lambda item: market.run_with_deadline(deadline, compute_instrument_factors, item, smart_money_pool)), instruments)`
- `scripts/demo_scalp.py`：
  `pool.map(market.bind_signal_frame(fetch_single_instrument_package), tradable)`

调用仍先执行原来的 begin_signal_frame；包装后继承该帧/冻结账户，不添加额外数据门槛或交易门槛。`position_guard` 是同步读取，不需要 ThreadPool wrapper。

### 2. 所有本金读者必须按当前/frozen scope 使用 helper

- A01/主 agent：`ledger_daily_drawdown` 已有 rows scope 与 mismatch 检查，继续保留；建议直接 `load_account_baseline(scope)`，并在需要读取存储基线时检查 `baseline_configured`。未配置的兼容展示默认 10000 不是账户真实本金，不能拿它触发日回撤限制；显式传入 initial_capital 的既有路径不应被误禁用。
- 以下 7 个脚本仍直接读取顶层 JSON 的 reset_time / initial capital：
  `daily_summary_and_backup.py`、`close_evidence.py`、`evidence_sync.py`、`evolution_status.py`、`self_improvement_engine.py`、`sync_full_ledger.py`、`wait_audit.py`。
  必须改为 `load_account_baseline(env.identity)` 或已有 scope，而不是读 v2 顶层“最近保存账户”的投影。尤其切回 DEMO 时，顶层可能仍是 outgoing LIVE；直接读会把 reset_time 用错。
- `dashboard/app.py` 与 backend app 已使用 helper；建议在已捕获环境的周期显式传 scope，状态接口一次读取后复用，避免分别调用三次遇到切换时字段不一致。
- 不要删除 legacy_baseline 或旧 scope，不要把尚未归属的历史 LIVE 基线自动认领；需要管理员明确更新/人工核对。

### 3. 主 agent 持有的配置锁必须一并交付

A07 复用 `scripts.config_lock.configuration_write`（本轮主 agent 提供）；它仍是主 agent 管理的共享文件。请确保集成时包含它，不要只摘取 A07 文件而漏掉该依赖。

### 4. CLI 执行端仍有进程全局环境副作用

`replace_cli_prefix` 为兼容既有 string-command 调用仍会写 os.environ。ContextVar 隔离了“选择哪个账户”，但不能使另一个线程随后启动 subprocess 时的进程环境自动隔离。

`ai_factor_trader.run_cmd_result/run_json_cmd`、Brain 的 subprocess 调用以及 debug CLI 脚本，应在实际启动进程处传入捕获环境的 `env=env.cli_env()`，并继续使用已有绑定复核/交易锁。A07 服务自己的 CLI fallback 已显式传 env。未擅自改这些调用方。

### 5. A03 交易恢复与 51603

`OKXAPIError.code` 保持既有结构；51603 是业务“订单不存在”，不是传输失败，也不是自动允许重发订单的证明。A03 的查询恢复/对账逻辑由其文件所有者继续维护，A07 未重复实现或改写 reconciliation。

## 四、仍需跟进的安全/运维边界

- `validate_outbound_url` 是验证器，不是 DNS-pinned transport。backup/notification 等调用方必须配合连接时地址固定、重定向限制，才能完全消除解析后重绑定/重定向 SSRF；本轮未越权修改这些传输文件。
- 账户中心 transport 已 NoRedirect；公共市场及通用签名 REST 仍使用 urllib 默认 opener。签名头已确保不随 redirect 转发，但完全禁止 redirect/私网跳转及严格跨跳总时限仍应统一实现，并同步现有 transport 测试 mock seam。**未把“头不泄漏”写成“已经完全无 SSRF”。**
- CLI 遗留 OAuth 是兼容路径；全局 profile/token 不能等同于账户中心已核验的 UID。账户中心 OAuth 自动交易禁用条件未放宽。
- 旧 DEMO OAuth 诊断失败后可能做明确标注的 LIVE 只读 control probe，setup 会区分结果，不能将其解释为 DEMO ready 或切换交易账户；本轮未运行该探针。
- audit.record 持久化失败会 warning/false，但各管理 API 尚未统一显示 audit degraded 状态；由 A06/主 agent 决定对操作员展示，不应将已完成写操作变成可重试异常。
- 本轮未联网核对 OKX 文档/CLI 发布版本，未声称测试通过代表真实账户有写权限或 DEMO/LIVE 完全一致。

## 五、验证

指定命令：

```bash
cd /mnt/d/wangkai/workspace/OKXQuant
/tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a07_*.py'
```

A07 新增测试：

- `test_audit_a07_baseline.py`：scope/migration、旧数据、LIVE 不认领、切回、并发、损坏/非有限值。
- `test_audit_a07_frames.py`：独立线程/async frame、父→worker 继承、deadline、冻结账户、异常恢复、闭盘/单时钟。
- `test_audit_a07_security.py`：凭据隔离、vault 并发/损坏、鉴权与 Cookie-CSRF、DNS/URL、签名头、结构化错误、登录竞态、close token、OAuth 链接。
- `test_audit_a07_existing.py`：复用账户/本金/管理员/K线/public_market/只读/503/签名/安装诊断/合约支持既有测试，以及 open_source_control 中账户环境与网络安全两组测试，不修改原测试文件。

最终复跑：**212 tests，7.229s，OK**（47 项 A07 新增测试 + 165 项既有本域测试），无未 mock 外部调用违规。15 个修改/新增 Python 文件 AST 解析通过；授权源文件 git diff --check 通过。

一次早期扩大导入整个 `test_open_source_control` 时，碰到 A07 域外 BackupTargetTests 的临时 manifest 路径被新的 backup 路径规则拒绝。已把兼容入口收窄为本域两个 TestCase；没有修改备份实现、删掉原测试或放松备份规则。后续 A07 范围测试全部通过。

测试未验证真实网络、真实订单、远端写权限或四处尚未接入的完整业务线程池链路。没有 commit/push/deploy。
