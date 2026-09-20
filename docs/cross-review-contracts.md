# 第二轮独立交叉复审：Admin API ↔ 前端契约

**最终关闭复核：30/30 contracts 与前端 UI harness 通过，CR-01 至 CR-06 全部关闭。详见文末追加；首次发现和失败结果保留作历史记录。**

日期：2026-09-19。结果对应共享工作区最后一次隔离测试快照，不代表全库或生产验收。

## 约束与方法

本轮生产只读，仅新增 `tests/test_cross_review_contracts.py` 和本报告。未重审 A04 原账本算法，未涉及 A07 plugin 解释器。A10 queue 仅核对请求、持久状态、权限及 UI 衔接。未委派、commit/push、部署、读取真实秘密或调用真实账户。

使用真实 FastAPI/Starlette TestClient、实际 route/RBAC、临时 auth DB/profile/consent/baseline/GatewayStore。环境选择、当前绑定验证和自动交易开关是替换边界；没有直接调用 route 函数冒充 HTTP 验证。合成账户、密码和异常标记不是实际秘密。

前端另外执行实际 SFC script 的异步 handler，并将实际 Gateway Vue template 编译后 SSR；Vue 生命周期、组件壳和网络边界为测试替身。这不是浏览器视觉验收，但也不是仅 grep。

## 最后测试结果

- 未发现可复现 P0，不代表全库无 P0。
- **30 个 HTTP 测试方法：25 通过、5 个失败方法；unittest 报 6 个 failure record**（公共 cache 方法包含两个失败子用例）。未添加 expectedFailure/skip，保留未修问题的正常失败断言。
- LIVE 过期确认错户授权已由主 agent 修复，本复审已独立验证通过。
- 仍开放：**P1 两类**（legacy 写权限、公共 cache scope）；**P2 三类**（500 缺 no-store、LIVE POST 异常原文、手动请求状态未显示）。
- 前端 harness：5 项 PASS、1 项降级反馈观察、1 项 P2 SSR 缺口；network_calls=0、files_written=0。

## P1：已立即报告的发现

### CR-01 / P1 / 已修复并复核：LIVE 确认错户

原复现：GET `/api/v1/admin/strategy/engine` 显示 LIVE A；弹窗期间当前绑定切到 LIVE B；发送原 UI payload `{"enabled":true,"confirmation":"ENABLE LIVE SCALP"}`，API 返回200并持久化B授权。服务端单次请求中两次读取当前绑定不能保护 GET/人工确认间隔。

主 agent 修复：POST 增加 expected_binding，业务上缺失409；前端弹窗前复制GET状态；authorize/disable 内比较 BINDING_FIELDS，撤销还比较 record_id。

**修复后实际 HTTP PASS：**缺失binding拒绝；有效A快照提交到B拒绝；同scope修改profile后旧快照拒绝；正常授权/撤销成功；旧record_id不能撤销重签授权；已授权后改profile/binding变为binding_changed。前端动态执行也证明弹窗内界面从A改成B，提交的仍是弹窗前A快照。

回归：`test_live_authorization_must_not_silently_target_a_different_account_than_confirmation_view`、`test_valid_live_account_a_binding_cannot_authorize_current_account_b`、`test_same_scope_profile_edit_rejects_pre_edit_authorization_snapshot`、`test_old_revoke_snapshot_cannot_revoke_new_authorization_record`。

### CR-02 / P1 / 开放：legacy 写接口绕过 superadmin

新 `/prompt-profiles` 写接口和 PromptStudio 的 useApiAction 限制为superadmin，但 `app.update_prompt_library`、`app.update_prompt_override` 仍只调用 require_admin_header。

| 普通 admin 的实际 HTTP 请求 | 真实临时存储结果 |
|---|---|
| PUT `/api/v1/admin/prompt-library`，active_style=small300 | 200；active profile从stable变成small300 |
| PUT `/api/v1/admin/prompts`，普通preference content | 200；运行override文件已经写入 |

不是按钮隐藏问题，合法普通admin会话可直接修改。建议对等价写入口统一 require_superadmin，保留list/export/history/状态只读权限。

失败回归：`test_legacy_prompt_library_cannot_bypass_superadmin_activation_role`、`test_legacy_prompt_override_cannot_bypass_superadmin_edit_role`。

### CR-03 / P1 / 开放：公共cache绕过账户scope

`app.cache` 对 decisions/self-improvement 原样返回文件，没有当前账户投影。

实际复现：当前为LIVE A；临时 `ai_brain_decisions.json`、`self_improvement_report.json` 中所有scope标记均明确为上一LIVE账户，含独特合成报告标记；**未登录** GET `/api/v1/cache/decisions`、`/api/v1/cache/self-improvement` 都200返回上一账户标记/内容。

对照：旧账户CACHE_DATA请求 `/api/all`、`/api/overview` 正确返回当前账户initializing空快照，且no-store。修复dashboard不等于旧cache route已经安全。

建议这些路径复用同一scope/可公开投影；不要删除历史文件掩盖问题。

失败回归：`test_public_cache_routes_do_not_bypass_current_account_snapshot_scope`，两个子用例。

## P2：错误与状态展示

### CR-04 / P2 / 开放：未捕获admin 500缺少no-store

对真实profile PUT的原子替换 `os.replace` 注入存储失败。原文件保留、HTTP500、不误报saved均PASS，但Cache-Control缺失。middleware在 `await call_next` 后设置header，异常由外层返回时未覆盖。正常401/403/422的no-store通过。

这证明header缺口，不声称CDN已实际缓存500。建议统一异常响应或外层保证敏感route no-store。

失败回归：`test_admin_unhandled_storage_failure_is_not_cacheable`。

### CR-05 / P2 / 开放：LIVE写接口返回底层异常原文

对 `_current_binding` 注入含**合成敏感标记**的RuntimeError。GET engine status安全返回unavailable/error_type，不含标记；携带先前有效expected_binding的POST返回409，其detail含异常全文。

这不是发现真实密钥泄漏，只证明写错误边界缺少与读状态一致的去敏保护。建议固定安全消息/错误类型/request_id，详细诊断仅进入受控去敏记录。

失败回归：`test_live_consent_failure_is_sanitized_in_status_and_write_response`。测试不打印真实秘密，合成标记检查使用布尔断言。

### CR-06 / P2 / 开放：手动请求状态未接到“查看任务状态”目标页

后端A10 queue实际PASS：202→pending持久化→去重→claim后running→finish后success/finished_at。GET `/api/v1/admin/gateway` 的manual_requests能读回准确状态。

Evolution提示已写成“提交时任务已排队/运行中”，没有谎称当前一直运行；但“查看任务状态”链接目标GatewayPage只展示scheduler.jobs和deliveries，没有消费manual_requests，也没有请求关联的recent_runs。

**实际SSR复现：**将API形状中的pending self_improvement请求#41278913送入实际Gateway Vue模板，HTML能显示self_improvement定时作业，却不含request_id。用户无法通过新按钮查看对应手动请求的当前状态。

建议显示request_id/job/status/created_at/finished_at/run_id；定时排程不是手动请求状态。

API PASS：`test_finished_manual_review_is_visible_via_actual_gateway_status_endpoint`。SSR复现在同一测试文件 `UI_HARNESS_JS`。

## 按按钮/字段的PASS范围

| UI操作/展示 | 契约与结论 |
|---|---|
| 新建profile | GET stable/export；POST import的payload.profile.execution_profile及name_override正确，创建/绑定同次写，尚未激活 |
| 复制profile | POST prompt-profiles的name/description/source_id正确；复制已保存偏好和binding，不复制active状态 |
| 导入 | 非法binding原子拒绝，不产生半成品profile/revision，不自动activate |
| 编辑提示词 | PUT name/editor_mode/pipelines或flat template成功；保存与activate分离 |
| 独立执行绑定 | PUT name/execution_profile不擦除原模板/编辑模式，不重新启用disabled profile |
| Activate/hotload | effective_for=next_fresh_decision；decision_generation_triggered=false；not_regenerated；没有立即推演或订单 |
| History/rollback | GET history及POST revision_id形状正确；读者可读，写需superadmin，rollback不自动activate |
| Validate/delete | validate不写；active profile删除409，切离后可删除 |
| 保存/切换失败 | 非法输入422、存储失败500；原文件和active id不变；实际UI handler显示err并保留draft |
| 手动review | 仅superadmin、仅self_improvement、精确RUN EVOLUTION；错确认400/越权403；202受理不等于已完成/已发布 |
| Review状态API | manual_requests/request_id/status/finished_at真实持久化通过；目标UI缺口见CR-06 |
| LIVE engine | admin可读；paused=authorized true/enabled false，不冒充ready；DEMO不能借该route创建LIVE consent |
| LIVE授权/撤销 | enabled/confirmation/expected_binding正确；陈旧scope/profile/record_id拒绝；无隐式账户切换或立即下单 |
| account_baseline | superadmin、确认词、金额范围；双账户独立保存；旧数据保留；损坏文件拒绝覆盖 |
| legacy demo→LIVE基线 | 无主旧demo资本不自动赋给LIVE；显式保存后按账户记录；其他LIVE仍unconfigured |
| 公共dashboard snapshot | 旧账户envelope不返回；scope切换返回initializing空快照；no-store通过，不包括有缺陷的旧cache路由 |
| risk_status | 当前scope观测/待决数量；其他账户高回撤不污染当前；未知为null而不是false/0 |
| mode_limits | 按当前LIVE consent/policy投影scalp/swing；UI实际script消费两个模式的上限，不用基础预设替代 |

实际 `saveExecutionProfile` 中，PUT成功但后续GET刷新失败时，loadFailed=true且最终banner为“执行模式已保存”。这是**写成功、刷新失败**，不是失败PUT被谎报成功；未升级为P1。可改善为“已保存，刷新失败待核验”。

配置、ready状态、有效模式上限和历史样本不代表实盘成交或收益；本报告不把模拟等同实盘收益。

## 复现

HTTP隔离命令：

```sh
cd /mnt/d/wangkai/workspace/OKXQuant
/tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern test_cross_review_contracts.py -v
```

最后输出：Ran 30 tests；FAILED (failures=6)。5个失败方法，其中一个两个子用例，其余25个方法PASS；没有未mock网络/子进程违规。

前端只读harness保存在同一测试文件的 `UI_HARNESS_JS`；从仓库根目录提取到Node标准输入运行，使用现有node_modules，不下载依赖：

```powershell
python -c "import ast,pathlib; t=ast.parse(pathlib.Path('tests/test_cross_review_contracts.py').read_text(encoding='utf-8')); print(next(ast.literal_eval(n.value) for n in t.body if isinstance(n,ast.Assign) and any(isinstance(x,ast.Name) and x.id=='UI_HARNESS_JS' for x in n.targets)))" | node --input-type=module
```

本轮通过Node REPL执行同一字符串。TypeScript只在内存中移除import/类型；保留实际页面handler和Gateway模板，替换API与生命周期边界。Gateway manual-request SSR项当前FAIL P2，其余安全状态断言通过。

## 限定与交付

- 没运行真实下单、外部模型或review worker；queue claim/finish在真实临时Store中确定性推进，不声称worker已完成真实模型复盘。
- 无完整浏览器视觉/真实SPA网络联调；前端script/SSR与HTTP分别验证。
- 没将合成故障标记称为已泄漏真实密钥。
- 未清除旧cache，未改生产，未重审账本。开放项由主agent集成修复后重跑，不用修改失败断言掩盖问题。
- `git diff --check` 对本轮文件通过；不宣称全库测试通过。

## 关闭复核追加：2026-09-19（最终结论，以本节为准）

主 agent 集成修复后，本 reviewer 仅短复核原发现，未扩展到其他领域、未修改生产文件。上文开放项与失败记录保留为历史复现证据，当前关闭状态如下：

| 编号 | 最终状态 | 独立复核证据 |
|---|---|---|
| CR-01 LIVE 过期确认错户 | **CLOSED / PASS** | 缺失 expected_binding 精确返回409；A→B、同scope改profile、旧record_id撤销均拒绝；正确快照授权/撤销通过；前端实际handler保留弹窗前快照 |
| CR-02 legacy两个写入口权限 | **CLOSED / PASS** | 两个原失败TestClient用例均通过：普通admin不得写prompt-library或prompts，原active状态/override文件不被修改 |
| CR-03 公共cache scope | **CLOSED / PASS** | decisions及self-improvement两个旧账户子用例通过；旧账户内容不再由公共cache返回；dashboard与risk_status的scope回归同时通过 |
| CR-04 admin异常500 no-store | **CLOSED / PASS** | 真实profile PUT注入存储异常，HTTP500仍带no-store，原文件保持不变 |
| CR-05 LIVE写错误去敏 | **CLOSED / PASS** | 持有效expected_binding的写请求遇到底层合成敏感异常，409响应不再返回原文；GET状态去敏回归仍通过 |
| CR-06 Gateway手动请求状态表 | **CLOSED / PASS** | 使用实际Gateway Vue模板SSR，请求#41278913可见，pending/running/success/failed分别显示排队中/运行中/已完成/失败；API状态持久化链也通过 |

### 本次复跑

- 指定WSL隔离runner运行 `--pattern test_cross_review_contracts.py -v`：**30/30，OK**。原失败断言均保留，没有skip/expectedFailure或放宽断言。
- 同文件只读Node UI harness：**6项PASS + 1项原有降级反馈观察**，退出码0，network_calls=0、files_written=0。
- 新Gateway模板使用TypeScript类型断言，旧SSR harness直接送入JS VM会报语法错误。本次仅给harness增加内存中的TypeScript去类型步骤（并兼容compiler-dom函数模式的with语句），未改生产模板；另将手动请求ID和四种生命周期状态改为强断言。
- 主agent报告的全部cross-review **79/79** 作为集成背景记录；本reviewer独立复跑并确认的是本域 **30/30 + UI harness**，不冒称重新跑过79项。

### 可发布范围

从本轮契约复审角度，原CR-01至CR-06均已关闭，**本域无未关闭P0/P1/P2阻断项**。可纳入发布的已验收范围为：profile新增/复制/import/编辑/执行绑定/activate/hotload及失败保护；manual review请求、角色、确认词、持久状态和Gateway状态表；LIVE授权与撤销的绑定/版本防串户；baseline迁移及公共snapshot/cache/risk_status账户隔离；mode_limits/未知风险展示；已测异常去敏与no-store。

此结论不扩大为全库、生产运行或实盘收益认证。真实worker/交易所/模型调用、浏览器视觉及部署仍由主agent的整体发布流程验收。配置保存、授权ready、请求排队或完成复盘均不等于已执行订单、已发布运行记忆或获得实盘收益。
