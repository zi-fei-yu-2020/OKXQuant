# A06 管理后端 / API 契约审计

日期：2026-09-19。范围：管理 API、配置持久化与生效边界、账户缓存展示、策略新增/保存/启用契约。

## 结论与范围说明

- 只修改 A06 独占文件；未修改前端、prompt_library、账户/交易模块、LLM/memory 模块或其他 agent 的文件。复用主 agent 新增的 scripts/config_lock.py 进行配置写事务。
- 未 commit、push、部署、读取真实 .env/秘密、调用真实账户、发送通知、创建交易或再委派。所有 Python 导入/测试经 scripts/run_tests.py 在一次性源码副本和净化环境中完成。
- 未新增开仓过滤、未关闭自动策略。账户 scope 校验只用于只读展示/缓存；调度修复仅让超时任务不杀死调度器、延误的每日任务补执行一次。
- 管理路由静态清单覆盖 **132 个 method/path（含两个兼容别名）**。离线 HTTP 测试覆盖 **127 个受保护管理操作的未登录边界**，包含 FastAPI 延迟注册的账户中心和记忆路由。登录/登出/status 等 5 项不放入统一拒绝访问测试，由现有鉴权测试覆盖。
- “全路由覆盖”指下方清单、请求模型/鉴权/前端调用核对和未登录边界覆盖，**不等于 132 条真实远端业务操作全部执行**。涉及交易、外发、上传/还原、安装、更新、OAuth 的真实操作均未执行。

## 已修复问题

|编号|严重性|证据与修复|主要回归|
|---|---|---|---|
|A06-01|高|runtime 直接返回 get_active_llm_runtime()，泄露 api_key；改为响应字段白名单，只返回 api_key_configured。config 的 webhook 回显改脱敏。|runtime_response_does_not_expose_llm_credentials|
|A06-02|高|配置中的换行/NUL 可变成额外 env 项；API 在任何写入前拒绝 CR/LF/NUL，持久化层二次校验。配置 URL 校验 HTTP(S)、主机、端口且不允许 userinfo。|persisted_configuration_rejects_control_characters_before_writes；multiline_rejected_without_partial_write|
|A06-03|中|Telegram API base 不在 MANAGED_KEYS，成功响应后丢失；补白名单。重复 env key 的旧末项覆盖新值；统一移除所有旧项后写新项。布尔持久化为 1/0；跨线程/进程配置锁防止无关字段丢失。|telegram_api_base_roundtrip；duplicate_keys_cannot_override_new_save；concurrent_saves_do_not_lose_unrelated_fields|
|A06-04|高|NotifyPage 把 GET 返回的脱敏 webhook 原样提交，导致星号字符串覆盖真实凭据；保存/开关均识别占位值并保留已有秘密。部分通知更新不再清空无关目标；只根据有效值计算 readiness。|masked_notification_get_values_are_not_saved_as_credentials；partial_notification_save_preserves_other_channels_and_destinations|
|A06-05|中|测试外发失败仍 sent=true，前端会显示已发；现 attempted=true，sent/accepted 只在 accepted: 结果下为 true，result.status 明确 accepted/failed。|failed_notification_test_does_not_claim_sent；accepted_notification_test_is_not_read_receipt|
|A06-06|高|旧同步脚本将真实零余额替换为历史余额；且账户采集失败可写成零资产/空仓。移除历史余额回退，缺失/非有限观测拒绝发布，保留旧文件但不重写时间。每次采集绑定同一环境，发布前核验身份未变。|zero_balance_is_not_replaced_with_old_account_equity；failed_positions_does_not_publish_false_flat_account；account_switch_during_collection_does_not_publish|
|A06-07|高|全局 snapshots/ledger、私有 ledger API 可混入其他账户或未归属行。增加严格 scope 投影（拒绝缺失、矛盾、畸形 marker）；缓存写入 account_source_id。strategy_status 的旧研究/记忆文件不再跨账户复用。|unscoped_and_other_account_ledger_rows_are_excluded；private_ledger_cache_is_filtered_to_selected_account；strategy_status_ignores_unowned_legacy_account_reports|
|A06-08|高|快照脚本用默认本金和账单余额变动伪造权益曲线，失败时还可生成“正常”本金值。改为仅记录成功观测的 USDT 权益、账户 scope、source；没有匹配账户的真实本金时 pnl/roi=null，不发明历史点。原子写并拒绝采集过程中的账户切换。|snapshot_does_not_invent_baseline_or_bill_equity；scoped_baseline_computes_real_zero_equity_loss；snapshot_failure_preserves_existing_file|
|A06-09|中|旧同步器启动时固化交易池，增删币种对常驻进程不生效；改每轮加载。净持仓 short 计数遗漏；按净数量方向计数。日胜负统计改生命周期账本，不再把账单/手续费行当笔数。|pool_is_reloaded_every_sync_cycle；net_short_positions_are_counted|
|A06-10|中|策略更新未接收 execution_profile，且完整 model_dump 会默认覆盖省略字段。保留 modules，增加可选 standard/small300，更新 exclude_unset/exclude_none；返回保存与激活区别。激活/快照响应明确不产生新决策，现有仓位保护不变。|real_profile_save_preserves_modules_binding_and_active_id；activation_does_not_fabricate_fresh_decisions_or_resize_positions|
|A06-11|中|QQ 旧加密 OpenID 优先级可覆盖管理员新目标；明确编辑时删除旧覆盖值。换 Bot 不继承旧 OpenID；旧网关不允许恢复旧 AppID，轮询检查凭据变化后重连。capture timeout 限 10–300 秒。|qq_destination_update_removes_legacy_encrypted_override；qq_old_gateway_cannot_restore_previous_bot_id；qq_new_bot_does_not_inherit_old_openid|
|A06-12|中|调度 JSON 非对象/非法时间导致异常、共享默认列表被污染；恢复深拷贝与校验。串行任务错过恰好一分钟后每日任务永久漏跑；改为当天到时补跑一次。TimeoutExpired/OSError 不再退出主调度器。API 不再承诺无条件 60 秒内生效。|malformed_schedule_falls_back_without_crash；delayed_daily_job_catches_up_once；job_timeout_does_not_kill_scheduler|
|A06-14|中|旧 PUT prompt-library 连续保存当前 custom 时未走 legacy 合并，更新被忽略；改传明确 v1 结构，委托主 agent 写事务合并，并先校验内容。schema 删除已不存在的 aggressive，改接受 stable/small300/custom；422 时不写文件。|legacy_custom_save_updates_again_without_losing_other_profiles|
|A06-13|低|macro validation 非对象造成投影崩溃、stat 文件消失竞态；类型归一与 OSError 回退。|malformed_macro_validation_does_not_crash_projection|

## 主 agent / A09 必须遵守的策略契约

### 保存（PUT /api/v1/admin/prompt-profiles/{profile_id}）

- name 仍为必填；editor_mode 允许 simple、advanced、modules。
- execution_profile 可选，只允许 standard/small300。省略或 null 均不替换当前绑定，其他省略字段也不再被模型默认值覆盖。
- 返回 profile、validation、saved=true、activation_changed=false、decision_generation_triggered=false、effective_for=next_fresh_decision_if_active、existing_positions_protection=unchanged。
- 保存未激活草案**不会自动启用它**。保存已经启用的 profile 会更新它下一次新决策使用的配置；不是“保存永远不影响运行”。

### 启用（POST /api/v1/admin/prompt-profiles/{profile_id}/activate）

- 成功：active_profile_id、profile、execution_settings、effective_for=next_fresh_decision、existing_positions_resized=false、existing_positions_protection=unchanged、decision_generation_triggered=false、decision_cache_status=not_regenerated。
- execution_settings 用本次返回的 profile 计算，避免另一个请求切换当前 profile 后返回不一致的执行预设。
- 禁止根据 HTTP 200、active_profile_id 或旧快照推断“新模型决策已经执行”。disabled/无效目标返回 409；本接口不调用模型推理、也不清空历史缓存。
- small300 的预算、单笔风险等**只读取 execution_settings.execution**；不硬编码历史百分比文案。
- GET prompt-library 的 snapshots_status=historical_not_activation_evidence；GET runtime 的 decision_cache_status 同义。历史记录保留用于审计，不冒充刚启用策略的新结果。

### 策略扩展边界

- A06 不增加新策略注册逻辑，不更改 prompt_library。主 agent 已负责复制、modules 持久化、原子导入、写事务与 execution_profile accepted 字段。
- 新增第三种执行预设时，必须同时更新主 agent 的执行注册/解析与 A06 Literal 枚举、A09 选择器，并增加导入/保存/启用回归；不能依赖未知字段被静默丢弃。
- 现有 execution signature 主要标识 profile ID 和执行设置；同一 ID 的提示词内容更新不能仅靠该签名证明当前缓存已重算。建议决策生产方和前端联动传播 profile revision / prompt hash / account scope；A06 只添加明确的历史标签，不改交易执行过滤。

## 按钮 / 接口 / 生效矩阵

管理前缀均为 /api/v1/admin。只读接口通常 session；敏感写入 superadmin。具体权限以路由表为准，A07 负责权限策略，不在此次批量调整。

|页面/按钮|接口|结果与生效边界|审计情况|
|---|---|---|---|
|登录/退出/恢复会话|POST auth/login、auth/logout；GET auth/me、auth/status|session_token / user；退出撤销当前会话|现有鉴权测试；兼容 login/logout 别名保留|
|Overview / Decisions 刷新|GET overview、runtime、strategy/status|本地状态与历史决策；不触发新推理；runtime 无原始 LLM key|A06 响应回归；历史标签需 A09 展示|
|账户中心创建/导入/检测/绑定/激活/删除/手动平仓开关|accounts 下全部路由；GET okx/runtime / account-snapshot|检查成功、绑定成功、交易环境激活是不同动作；A07 负责执行安全|清单与未登录边界覆盖；既有账户测试|
|旧配置保存 / 初始本金 / CLI/OAuth / 平仓|PUT config、account-baseline；okx/*；POST positions/close|确认短语、角色与账户接管限制；未真实执行|边界覆盖；已接 A07 scope loader；集成测试通过|
|模型/供应商增删、测试、抓取、启用|llm/providers、llm/models、llm/test、llm/fetch-models、llm/activate|保存/连通性测试/启用分开；下一次模型调用读取|A05 模块；A06 列举与边界覆盖|
|委员会角色/套件/测试|council/config、apply-suite、reset-role、test|配置持久化与显式测试分开|A05 模块；静态核对与边界覆盖|
|策略复制/导入/保存/验证/启用/删除/历史/回滚/导出|prompt-profiles 全部路由；GET prompt-library|返回契约见上；未启用草案保存不启用；旧仓位保护不变|A06 真存储往返 + 主 agent 测试|
|旧提示词覆盖保存/旧风格选择|PUT prompts、prompt-library|旧兼容入口；必须与主 profile 单一事实源一致|已修复 A06-14；旧入口需逐步迁移|
|自进化模块保存|PUT prompt-profiles/{id}|保存对应策略，不隐式切换；只有活动 profile 下一轮被使用|A09 文案不要无条件说下一轮生效|
|立即自进化|POST gateway/jobs/self_improvement/run|**当前无后端路由**；前端确认 RUN EVOLUTION，但发送 RUN JOB|U1：A09 / 主 agent / A05 联动，不擅自加执行入口|
|通知加载/保存/通道开关|GET/PUT notifications；PUT channels/{channel}/toggle|脱敏值保留秘密；返回实际 enabled；下一次通知读取|A06 离线回归|
|通知诊断/发送测试|POST notifications/diagnose、notifications/test|诊断不外发；测试需要 SEND TEST {CHANNEL}；attempted 不等于 accepted，不等于已读|A06 返回失败契约修复；无真实外发|
|QQ 绑定/捕获/轮询|notifications/qq/bind/*、capture-openid/*|有时效的异步任务；轮询不代表最终发送成功|A06 超时、换 Bot 与旧目标回归；无真实网关|
|每日简报保存|GET/PUT notifications/schedule|规范化去重排序；北京时间；下个调度轮询读取，正在运行任务可延迟|A06 存储与调度回归|
|交易池增删/支持性刷新|GET/POST instruments；DELETE instruments/{id}；GET instruments/support|同步器每轮重新加载；不能承诺所有页面瞬时刷新|A06 热加载回归；风险保护代码未变|
|拦截器开关/编辑/新建/删除/排序/测试|interceptors 全部路由|配置/代码持久化；执行域验证由拦截器模块负责|静态与边界；未主动部署/执行上传代码|
|备份设置/格式验证/运行/下载/上传/还原|backups/*、backup-jobs/*、backup-credentials、backup-target-types|simple/test 仅校验格式，不是实际远端连接/写入验证；危险操作须确认|静态与边界；未执行备份外发/还原|
|Gateway 重放|POST gateway/deliveries/{id}/replay|REPLAY {id}；仅 dead 投递置 pending；accepted 不等于已投递|静态与边界；不触发真实消息|
|用户创建/启停/解锁/改密|users/*|superadmin 或本人密码校验；修改密码需要重新认证|现有管理 API 测试；A07 域|
|Agents / Plugins / Audit / Logs / About / Update|GET agents、plugins、audit、logs、about、update-status；POST update|只读刷新与显式更新区分；update 可能联网且未真实执行|静态/边界；现有管理测试|
|记忆历史/候选发布/回滚|memory 下全部路由（含 candidates / versions）|A05 的候选/发布契约；旧写入口可能已退役|包含延迟路由边界；不跨域修改|

## 未解决 / 必要联动

|编号|严重性|问题与建议|负责边界|
|---|---|---|---|
|U1|中|“立即自进化”路由缺失、确认词不一致。前端应明确不可用，或由任务负责方实现受控执行并统一确认词；不应显示请求已处理。|A09 + 主 agent/A05|
|U2（已联动）|高→已修|A07 已落地账户级 baseline v2。生成快照已直接调用 load_account_baseline(scope=environment.identity)，不复制旧文件解析逻辑；测试覆盖当前账户 map 与不同账户顶层投影、LIVE 不认领无 scope 旧本金。A07 的明确 legacy_demo 兼容政策由共享 loader 统一负责。|A07 完成；A06 集成回归通过|
|U4|中|历史决策文件当前缺少统一 account scope/profile revision 归属；本次给管理响应加历史标签，但其他读者可能仍将旧判断当当前判断。不得以 execution signature 单独证明同 ID 新提示词已推理。|决策生产方 + A09 / 主页负责方|
|U5|中|通知/备份配置涉及 env 与加密存储两份文件，单文件写事务不等于跨文件事务。toggle 开启配置不全时可先保存输入再返回 400；备份简单设置也可能先保存 credentials 再校验任务。需要以后引入统一事务或返回明确部分保存状态。|A06 / 备份与 secret-store 负责方|
|U6|中|旧 config 环境切换检查的交易文件锁未覆盖整个持久化区间；账户中心已接管时旧入口返回 409，但纯 legacy 模式的安全切换仍需 A07 确认。|A07；本次不改交易请求安全域|

## 测试与限制

最终指定命令：

```text
wsl --exec /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern test_audit_a06_*.py
```

测试副本不包含真实 data/logs/.env，运行器阻断未 mock 的网络、DNS 和子进程。QQ 网关回归仅模拟可选 websockets 模块，不建立连接。现有 review venv 未安装该可选包；未为此安装包或联网。

测试最终计数与附加回归结果见文末。初轮发现测试工具版本字段差异及可选 websockets 缺失，已调整测试夹具；现有 QQ 持久化测试曾揭示兼容断言，已保留成功绑定时的既有存储形态，并在显式改目标/换 Bot 时移除旧覆盖值。没有修改已有测试以“消除失败”。

## 全管理路由清单

下表从 app.py、account_routes.py、memory_routes.py 的 AST 提取，包括 prefix 拼接；不导入生产应用。A = 静态清单 + 未登录 HTTP 边界；AUTH = 登录/status/logout 特殊契约；请求列 — 表示无 Pydantic payload（可能有 path/query/header/upload 参数）。权限为静态调用边界摘要，不替代 A07 的完整 RBAC 审计。

|Method|Route|Handler / source|Request|Role boundary|Coverage|
|---|---|---|---|---|---|
|GET|`/api/v1/admin/about`|`admin_about` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/account-baseline`|`admin_update_account_baseline` (app.py)|`InitialCapitalUpdate`|superadmin|A|
|GET|`/api/v1/admin/accounts`|`status` (account_routes.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/accounts/activate/{mode}`|`activate` (account_routes.py)|`ConfirmRequest`|superadmin|A|
|DELETE|`/api/v1/admin/accounts/bindings/{purpose}`|`unbind` (account_routes.py)|`ConfirmRequest`|superadmin|A|
|PUT|`/api/v1/admin/accounts/bindings/{purpose}`|`bind` (account_routes.py)|`ConfirmRequest`|superadmin|A|
|POST|`/api/v1/admin/accounts/connections`|`create` (account_routes.py)|`CreateConnection`|superadmin|A|
|DELETE|`/api/v1/admin/accounts/connections/{identity}`|`delete` (account_routes.py)|`ConfirmRequest`|superadmin|A|
|POST|`/api/v1/admin/accounts/connections/{identity}/oauth/start`|`oauth_start` (account_routes.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/accounts/connections/{identity}/oauth/status`|`oauth_status` (account_routes.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/accounts/connections/{identity}/probe`|`probe` (account_routes.py)|`ModeRequest`|superadmin|A|
|POST|`/api/v1/admin/accounts/import-legacy`|`import_legacy` (account_routes.py)|`ModeRequest`|superadmin|A|
|PUT|`/api/v1/admin/accounts/manual-close`|`manual_close` (account_routes.py)|`ManualCloseRequest`|superadmin|A|
|POST|`/api/v1/admin/accounts/news/refresh`|`refresh_news` (account_routes.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/agents`|`admin_agents` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/audit`|`admin_audit` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/auth/login`|`admin_login` (app.py)|`AdminLoginRequest`|special/auth|AUTH|
|POST|`/api/v1/admin/auth/logout`|`admin_logout` (app.py)|`-`|special/auth|AUTH|
|GET|`/api/v1/admin/auth/me`|`admin_me` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/auth/status`|`admin_auth_status` (app.py)|`-`|special/auth|AUTH|
|PUT|`/api/v1/admin/backup-credentials`|`update_backup_credentials` (app.py)|`BackupCredentialUpdateRequest`|superadmin|A|
|GET|`/api/v1/admin/backup-jobs`|`backup_jobs_api` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/backup-jobs`|`create_backup_job_api` (app.py)|`BackupJobCreateRequest`|superadmin|A|
|POST|`/api/v1/admin/backup-jobs/import`|`import_backup_job_api` (app.py)|`BackupJobImportRequest`|superadmin|A|
|POST|`/api/v1/admin/backup-jobs/validate`|`validate_backup_job_api` (app.py)|`BackupJobUpdateRequest`|admin/session|A|
|POST|`/api/v1/admin/backup-jobs/verify`|`verify_backup_archive_api` (app.py)|`BackupVerifyRequest`|superadmin|A|
|DELETE|`/api/v1/admin/backup-jobs/{job_id}`|`delete_backup_job_api` (app.py)|`-`|superadmin|A|
|PUT|`/api/v1/admin/backup-jobs/{job_id}`|`update_backup_job_api` (app.py)|`BackupJobUpdateRequest`|superadmin|A|
|GET|`/api/v1/admin/backup-jobs/{job_id}/export`|`export_backup_job_api` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/backup-jobs/{job_id}/run`|`run_backup_job_api` (app.py)|`BackupJobRunRequest`|superadmin|A|
|GET|`/api/v1/admin/backup-target-types`|`backup_target_types` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/backups`|`backup_status` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/backups/download/{filename:path}`|`download_backup_archive` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/backups/methods`|`update_backup_methods` (app.py)|`BackupMethodsUpdate`|admin/session|A|
|POST|`/api/v1/admin/backups/restore`|`restore_backup_archive` (app.py)|`BackupRestoreRequest`|superadmin|A|
|POST|`/api/v1/admin/backups/run`|`run_backup` (app.py)|`BackupRequest`|admin/session|A|
|GET|`/api/v1/admin/backups/simple`|`simple_backup_config` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/backups/simple`|`update_simple_backup` (app.py)|`SimpleBackupUpdateRequest`|superadmin|A|
|POST|`/api/v1/admin/backups/simple/test`|`test_simple_backup` (app.py)|`SimpleBackupUpdateRequest`|superadmin|A|
|POST|`/api/v1/admin/backups/upload`|`upload_backup_archive` (app.py)|`multipart UploadFile`|superadmin|A|
|PUT|`/api/v1/admin/channels/{channel}/toggle`|`toggle_channel` (app.py)|`ChannelToggleRequest`|admin/session|A|
|GET|`/api/v1/admin/config`|`admin_config` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/config`|`update_admin_config` (app.py)|`AdminConfigUpdate`|conditional admin/superadmin|A|
|POST|`/api/v1/admin/council/apply-suite`|`admin_apply_council_suite` (app.py)|`CouncilApplySuiteRequest`|superadmin|A|
|GET|`/api/v1/admin/council/config`|`admin_get_council_config` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/council/config`|`admin_update_council_config` (app.py)|`CouncilConfigUpdateRequest`|superadmin|A|
|POST|`/api/v1/admin/council/reset-role`|`admin_reset_council_role` (app.py)|`CouncilResetRoleRequest`|superadmin|A|
|POST|`/api/v1/admin/council/test`|`admin_test_council_debate` (app.py)|`CouncilTestRequest`|admin/session|A|
|GET|`/api/v1/admin/gateway`|`gateway_status` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/gateway/deliveries/{delivery_id}/replay`|`replay_gateway_delivery` (app.py)|`GatewayReplayRequest`|admin/session|A|
|GET|`/api/v1/admin/instruments`|`admin_instruments` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/instruments`|`add_admin_instrument` (app.py)|`InstrumentAddRequest`|admin/session|A|
|GET|`/api/v1/admin/instruments/support`|`admin_instrument_support` (app.py)|`-`|admin/session|A|
|DELETE|`/api/v1/admin/instruments/{inst_id}`|`delete_admin_instrument` (app.py)|`InstrumentDeleteRequest`|admin/session|A|
|GET|`/api/v1/admin/interceptors`|`admin_list_interceptors` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/interceptors`|`admin_create_interceptor` (app.py)|`InterceptorCreateRequest`|superadmin|A|
|POST|`/api/v1/admin/interceptors/reorder`|`admin_reorder_interceptors` (app.py)|`InterceptorReorderRequest`|superadmin|A|
|POST|`/api/v1/admin/interceptors/test`|`admin_test_interceptors` (app.py)|`InterceptorTestRequest`|admin/session|A|
|DELETE|`/api/v1/admin/interceptors/{filename}`|`admin_delete_interceptor` (app.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/interceptors/{filename}`|`admin_get_interceptor` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/interceptors/{filename}/code`|`admin_save_interceptor_code` (app.py)|`InterceptorCodeRequest`|superadmin|A|
|PUT|`/api/v1/admin/interceptors/{filename}/toggle`|`admin_toggle_interceptor` (app.py)|`InterceptorToggleRequest`|superadmin|A|
|POST|`/api/v1/admin/llm/activate`|`admin_activate_llm_model` (app.py)|`LLMActivateRequest`|superadmin|A|
|POST|`/api/v1/admin/llm/fetch-models`|`admin_fetch_remote_models` (app.py)|`LLMFetchModelsRequest`|admin/session|A|
|GET|`/api/v1/admin/llm/models`|`admin_get_llm_models` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/llm/models`|`admin_upsert_llm_model` (app.py)|`LLMModelUpsertRequest`|superadmin|A|
|DELETE|`/api/v1/admin/llm/models/{model_id}`|`admin_delete_llm_model` (app.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/llm/providers`|`admin_get_llm_models` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/llm/providers`|`admin_upsert_llm_provider` (app.py)|`LLMProviderUpsertRequest`|superadmin|A|
|DELETE|`/api/v1/admin/llm/providers/{provider_id}`|`admin_delete_llm_provider` (app.py)|`-`|superadmin|A|
|DELETE|`/api/v1/admin/llm/providers/{provider_id}/models`|`admin_clear_llm_provider_models` (app.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/llm/providers/{provider_id}/models`|`admin_upsert_llm_model` (app.py)|`LLMModelUpsertRequest`|superadmin|A|
|DELETE|`/api/v1/admin/llm/providers/{provider_id}/models/{model_id}`|`admin_delete_llm_model` (app.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/llm/providers/{provider_id}/toggle`|`admin_toggle_llm_provider` (app.py)|`LLMProviderToggleRequest  /  None`|superadmin|A|
|POST|`/api/v1/admin/llm/test`|`admin_test_llm` (app.py)|`LLMTestRequest`|admin/session|A|
|POST|`/api/v1/admin/login`|`admin_login` (app.py)|`AdminLoginRequest`|special/auth|AUTH|
|POST|`/api/v1/admin/logout`|`admin_logout` (app.py)|`-`|special/auth|AUTH|
|GET|`/api/v1/admin/logs`|`admin_logs` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/memory`|`get_admin_memory` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/memory`|`add_admin_memory_item` (app.py)|`MemoryItemRequest`|superadmin|A|
|PUT|`/api/v1/admin/memory`|`update_admin_memory_all` (app.py)|`MemoryUpdateAllRequest`|retired write (410)|A|
|POST|`/api/v1/admin/memory/candidates`|`create` (memory_routes.py)|`CandidateRequest`|superadmin|A|
|POST|`/api/v1/admin/memory/candidates/{identity}/publish`|`publish` (memory_routes.py)|`PublishRequest`|superadmin|A|
|POST|`/api/v1/admin/memory/candidates/{identity}/reject`|`reject` (memory_routes.py)|`ReviewRequest`|superadmin|A|
|POST|`/api/v1/admin/memory/initialize`|`initialize` (memory_routes.py)|`InitializeRequest`|superadmin|A|
|POST|`/api/v1/admin/memory/rollback`|`rollback_admin_memory_lessons` (app.py)|`-`|retired write (410)|A|
|POST|`/api/v1/admin/memory/toggle/{lesson_id}`|`toggle_admin_memory_lesson` (app.py)|`-`|retired write (410)|A|
|GET|`/api/v1/admin/memory/versions/{version}`|`version_detail` (memory_routes.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/memory/versions/{version}/restore`|`restore` (memory_routes.py)|`RollbackRequest`|superadmin|A|
|DELETE|`/api/v1/admin/memory/{index}`|`delete_admin_memory_item` (app.py)|`-`|retired write (410)|A|
|GET|`/api/v1/admin/notifications`|`notification_config` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/notifications`|`update_notification_config` (app.py)|`NotificationConfigUpdate`|superadmin|A|
|POST|`/api/v1/admin/notifications/diagnose`|`diagnose_notification` (app.py)|`NotificationTestRequest`|admin/session|A|
|POST|`/api/v1/admin/notifications/qq/bind/start`|`qq_bind_start` (app.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/notifications/qq/bind/{task_id}`|`qq_bind_poll` (app.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/notifications/qq/capture-openid/start`|`qq_capture_openid_start` (app.py)|`QQOpenIDCaptureStartRequest  /  None`|superadmin|A|
|GET|`/api/v1/admin/notifications/qq/capture-openid/{capture_id}`|`qq_capture_openid_poll` (app.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/notifications/schedule`|`notification_schedule` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/notifications/schedule`|`update_notification_schedule` (app.py)|`NotificationScheduleUpdate`|admin/session|A|
|POST|`/api/v1/admin/notifications/test`|`send_notification_test` (app.py)|`NotificationTestRequest`|superadmin|A|
|GET|`/api/v1/admin/okx/account-snapshot`|`admin_okx_account_snapshot` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/okx/cli-check`|`admin_okx_cli_check` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/okx/install-cli`|`admin_okx_install_cli` (app.py)|`OkxCliInstallRequest`|superadmin|A|
|POST|`/api/v1/admin/okx/oauth/start`|`admin_okx_oauth_start` (app.py)|`OkxOAuthStartRequest`|superadmin|A|
|GET|`/api/v1/admin/okx/oauth/status`|`admin_okx_oauth_status` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/okx/runtime`|`admin_okx_runtime` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/overview`|`admin_overview` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/plugins`|`admin_plugins` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/positions/close`|`manual_close_position` (app.py)|`ManualCloseRequest`|superadmin|A|
|GET|`/api/v1/admin/prompt-library`|`prompt_library` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/prompt-library`|`update_prompt_library` (app.py)|`PromptLibraryUpdate`|admin/session|A|
|GET|`/api/v1/admin/prompt-profiles`|`prompt_profiles` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/prompt-profiles`|`create_prompt_profile_api` (app.py)|`PromptProfileCreateRequest`|superadmin|A|
|POST|`/api/v1/admin/prompt-profiles/import`|`import_prompt_profile_api` (app.py)|`PromptImportRequest`|superadmin|A|
|POST|`/api/v1/admin/prompt-profiles/validate`|`validate_prompt_profile_api` (app.py)|`PromptProfileUpdateRequest`|admin/session|A|
|DELETE|`/api/v1/admin/prompt-profiles/{profile_id}`|`delete_prompt_profile_api` (app.py)|`-`|superadmin|A|
|PUT|`/api/v1/admin/prompt-profiles/{profile_id}`|`update_prompt_profile_api` (app.py)|`PromptProfileUpdateRequest`|superadmin|A|
|POST|`/api/v1/admin/prompt-profiles/{profile_id}/activate`|`activate_prompt_profile_api` (app.py)|`-`|superadmin|A|
|GET|`/api/v1/admin/prompt-profiles/{profile_id}/export`|`export_prompt_profile_api` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/prompt-profiles/{profile_id}/history`|`prompt_profile_history_api` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/prompt-profiles/{profile_id}/rollback`|`rollback_prompt_profile_api` (app.py)|`PromptRollbackRequest`|superadmin|A|
|GET|`/api/v1/admin/prompts`|`prompt_override` (app.py)|`-`|admin/session|A|
|PUT|`/api/v1/admin/prompts`|`update_prompt_override` (app.py)|`PromptOverrideRequest`|admin/session|A|
|GET|`/api/v1/admin/runtime`|`admin_runtime` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/strategy/status`|`admin_strategy_status` (app.py)|`-`|admin/session|A|
|POST|`/api/v1/admin/update`|`update_application` (app.py)|`UpdateRequest`|admin/session|A|
|GET|`/api/v1/admin/update-status`|`admin_update_status` (app.py)|`-`|admin/session|A|
|GET|`/api/v1/admin/users`|`admin_users` (app.py)|`-`|superadmin|A|
|POST|`/api/v1/admin/users`|`create_admin_user` (app.py)|`AdminCreateRequest`|superadmin|A|
|PUT|`/api/v1/admin/users/{user_id}/enabled`|`update_admin_enabled` (app.py)|`AdminEnabledRequest`|superadmin|A|
|PUT|`/api/v1/admin/users/{user_id}/password`|`update_admin_password` (app.py)|`AdminPasswordRequest`|self or superadmin|A|
|POST|`/api/v1/admin/users/{user_id}/unlock`|`unlock_admin_user` (app.py)|`AdminUnlockRequest`|superadmin|A|

## 最终回归结果

最终复核全部通过，合计 **219 项测试**（57 项 A06 + 162 项现有/主 agent 兼容性测试）：

|隔离测试 pattern|通过数|
|---|---:|
|test_audit_a06_*.py|57|
|test_admin_api.py|14|
|test_notifications.py|7|
|test_qq_bind.py|10|
|test_dashboard_*.py|15|
|test_account_connections.py|53|
|test_prompt_library.py|3|
|test_audit_master_profiles.py|12|
|test_gateway_scheduler.py|4|
|test_backup_*.py|44|

A06 测试额外逐条请求 127 个受保护管理 method/path，断言未登录时只能返回 401/403/422，并带 no-store；请求校验返回 422 不表示执行过合法业务负载。最终运行无未 mock 的外部调用违规。仅出现 review venv 的 Starlette/httpx 弃用提示及宿主 WSL 代理提示，不影响退出码。

曾误用 test_runtime_persistence.py pattern，发现匹配 0 测试；**该次不计入通过数**。上表均为最终实际执行的非零测试组。

## 修改清单

- okxquant_backend/app.py
- okxquant_backend/settings_store.py
- okxquant_backend/schedule_store.py
- okxquant_backend/strategy_status.py
- okxquant_backend/scheduler.py
- okxquant_backend/qq_bind.py
- okxquant_backend/qq_gateway_daemon.py
- okxquant_backend/macro_status.py
- scripts/sync_web_data.py
- scripts/dashboard_stats.py
- scripts/generate_snapshots.py
- tests/test_audit_a06_admin_api.py
- tests/test_audit_a06_stores_cache.py
- docs/audit-a06-admin-api.md

config.py、notifications.py、daemon_web_sync.py 已阅读审查但没有为了凑覆盖而修改。其他 agent 的改动保持原样；不依据整个工作区 diff 认领他人工作。

## 最后一次协作复核

- 主 agent 明确接管 dashboard/app.py 正常/STALE 路径的 scoped_rows → todaystats；A06 未编辑该文件。
- A05 报告的旧 QQ bind 失败已复核并修复兼容性；原 test_qq_bind.py 未改动，最终 10/10 通过。
- A06 新增实际 FastAPI HTTP 路由测试：绑定 start/poll、pending/awaiting_message/bound、过期 410、capture start/poll、普通管理员 403 且未启动任务。远端网关边界 mock，不冒充真实 QQ 联调。
- A07 baseline v2 集成后再次运行 A06 57 项及全部附加测试组，均通过。
