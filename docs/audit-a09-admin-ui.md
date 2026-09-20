# A09 管理前端／按钮接口审查

- 日期：2026-09-19（北京时间）
- 身份：A09，第 8 个启动 agent；与其他 agent 并行，不撤销或覆盖他们的修改。
- 写入范围：`okxquant_frontend/src/views/admin/**`、`src/router/index.ts`、`src/stores/auth.ts`；新增 `src/api/adminPrompt.ts`、`src/composables/useApiAction.ts`、`tests/audit-a09-admin-ui.test.mjs` 和本文件。
- 未修改公共 components、公共 utils/types、后端、策略算法、开仓条件、账户数据或秘密；未执行真实账户操作、停策略、提交、推送、部署、再委派。
- `AdminLayout.vue`、导航配置、现有 `useApi.ts` 已只读检查。浏览器／隔离后端验收按约定交由主 agent；本次没有启动服务或浏览器。

## 1. 设计与审查方式

读取 frontend-design skill，沿用既有工作台主题：卡片、细边框、紧凑按钮、中文提示；使用已有 `--bg-card`、`--border-subtle`、`--text-main`、`--text-muted`、品牌和错误色 token，不引入字体、配色或公共组件重构。

页面重点是明确区分：

1. 编辑中的提示词、已保存策略和已激活策略。
2. 独立执行模式绑定与短线／波段的实际有效限制。
3. 分钟策略运行范围、实盘显式授权与实际成交。
4. 复盘任务入队、运行、历史报告及运行记忆发布。

逐页对照当前 Python 路由／请求模型，检查路由、所有按钮的处理函数、请求字段、权限、加载失败、异步状态与重复提交。测试执行实际 Vue 页面 script（外部依赖全部使用内存 fake），另编译全部管理页 Vue 模板，不连接真实账户。

## 2. 覆盖矩阵

表内路径除注明外以 `/api/v1/admin` 为前缀。只读检查不等于已在线点验；真实副作用按钮没有实际触发。

| 页面／路由 | 按钮与 API／关键字段 | 权限与处理结果 |
| --- | --- | --- |
| `/admin/login` | `/auth/login`：`username,password`；登录跳转 | 原有 loading 互斥保留；路由必须通过 `/auth/me` 验证，不能只信本地 token |
| 管理外壳／`/admin` | 导航、主题、返回终端、文档、退出；`/auth/logout` | 保留正常退出；默认 overview；全部管理子路由继承会话保护 |
| `/admin/overview` | `/runtime`，必要的运行状态更新、快捷导航 | 加首屏失败重试，不把加载失败当作正常空结果 |
| `/admin/accounts` | `/accounts`；连接添加／旧 Key 导入／OAuth／probe；用途 bind/unbind、activate、delete；`confirmation,connection_id,enabled,mode`；CLI 检查／安装；资讯读取 | 全中心遵循后端 superadmin 限制，普通管理员显示权限说明而非可操作的失效表单；`perform` 同步互斥；保留外部授权后的状态核验；无真实请求 |
| `/admin/security` | `/config`、`/instruments`、`/instruments/support`；新增 `inst_id`、删除确认；`/account-baseline` 的 `initial_capital,confirmation`；snapshot；close 的 `close_token,admin_password,confirmation` | 既有受保护操作不绕过；本金拒绝截断型脏数值；新开平仓确认清空旧密码；写入互斥，支持状态原有 generation 保护保留 |
| `/admin/llm` | models/providers、启停、远端 fetch、单个／批量收录、激活、清空、删除、测试；`model_id,provider_id,reasoning_effort` | 修复远端收录后自动激活遗漏 provider_id；提交互斥与超级管理员写权限；保存后清空已提交 Key；部分批量失败如实提示；删除冗余普通刷新按钮，错误重试保留 |
| `/admin/council` | config GET/PUT：`enabled,consensus_mode,timeout_seconds,roles`；suite、reset-role、test | 总开关独立持久生效：读服务器已保存配置再仅改 enabled，不保存／丢弃本页角色草稿；失败不伪翻转；suite/reset 提示丢弃未保存内容；写入互斥 |
| `/admin/promptlib` | library；保存、激活、复制、新建、删除、导入／导出、历史／回滚；模块增删、排序、开关、变量插入、复制预览 | 见第 3 节；写入需超级管理员；所有新写入有互斥，未保存离页／切页确认；历史失败不显示旧列表；文件读取失败不保留旧导入内容 |
| `/admin/promptlib` 分钟策略 | GET `/strategy/engine`；POST `/strategy/engine/live-authorization`：`enabled,confirmation` | 仅 superadmin；仅 `environment=live` 提供授权操作；确认词 `ENABLE LIVE SCALP`／`DISABLE LIVE SCALP`；取消／错误词不发请求；DEMO 仅说明独立实盘确认；见第 4 节 |
| `/admin/evolution` | library、memory；复盘模板保存；POST `/gateway/jobs/self_improvement/run`；查询报告／任务状态入口 | 模板保存保留交易管线并显式 `editor_mode=modules`；未保存切换保护；请求词统一 `RUN EVOLUTION`，仅显示排队／运行回执与 request_id；查询 `/memory` 不覆盖模板草稿 |
| `/admin/interceptors` | 清单、源码、toggle `enabled`、reorder `pipeline_order`、code `code`、新建 `filename,code`、删除、沙盒、导出 | 超级管理员写权限和页面级互斥；排序失败重新读取；保留测试报告；删除与公共 AppDialog 重复的关闭按钮 |
| `/admin/notify` | notifications、schedule；通道 toggle；统一保存；诊断、测试 `channel,confirmation`；QQ 扫码／捕获；`briefing_times` | 通道切换只更新本通道，失败回滚，不回读覆盖别的通道／时间草稿；提交后清空秘密输入；QQ 成功只刷新 QQ；轮询防重叠和过期响应、关闭／卸载清理；调度生效提示真实 |
| `/admin/gateway` | gateway GET、死信 replay `confirmation:REPLAY id` | 原有必要队列刷新保留；重放同步互斥；提示“已重新入队”，不说送达成功 |
| `/admin/agents` | agents GET、状态更新 | 只读；加首屏失败重试，保留外部进程状态更新 |
| `/admin/backup` | simple config/test、run、download、multipart upload、restore；各原有确认词 | 保留受信备份与不恢复秘密／不操作保护订单说明；包括 multipart 的整操作互斥；上传／恢复／配置遵循超级管理员权限；保存后清空 credentials；失败重试 |
| `/admin/plugins` | plugins GET、清单更新 | 只读；失败重试；外部安装后的必要更新保留 |
| `/admin/audit` | audit `limit=200`、本地搜索、详情、必要更新 | 首屏失败重试；移除详情中与 AppDialog 重复的关闭按钮 |
| `/admin/adminsys` | users、新建 `username,password,role`、enabled、unlock 确认、password `current_password,new_password` | auth store 保留用户 id，普通管理员修改自己不再请求 `/users/0/password`；用户列表仅 superadmin；修改本人密码立即清本地会话并去登录；重置别人不错误退出自己 |
| `/admin/about` | about、update-status | 只读版本检查，互斥 loading 已存在；加首屏失败重试；不执行更新／部署 |
| 兼容地址 | `/admin/symbols`、`manual-trade` → security；`backups` → backup；`/doc` → docs | 保留 router 重定向；`LegacyRedirect.vue` 无活动路由引用，不新增旧站跳转 |

## 3. 策略与执行模式

### 独立、明确、原子

- `execution_profile` 仅允许 `standard | small300`。新建有明确选项，默认显式 standard，而非依赖缺省绑定。
- 当前创建 API 不接受 execution_profile。为避免“先创建再 PUT 绑定”的半成功状态，UI 读取 `stable/export`，复用现有原子 `/prompt-profiles/import` 一次写入新方案及显式绑定。**没有发明或扩展后端接口。** 审计事件因此是 profile.import，已在此说明。
- 复制继续用 create 的 `source_id`，不发送缺省 execution_profile，不覆盖后端保留的执行配置。
- “保存执行模式”仅发 `name,execution_profile`，不发 editor_mode、pipelines 或提示词正文；不清除未保存模块。
- “保存提示词”不发 execution_profile，同时保留未保存的执行模式选择；两种保存互不偷偷代办。
- 编辑交易／复盘任一管线时合并其余 pipeline_views，避免后端以完整 pipelines 替换时清空另一组模板。
- 激活前必须先处理未保存内容。相同策略／相同标签重复点击不再清空草稿。

### 生效与展示

- 删除旧 small300 硬编码 `0.5% / 30U / 90U / 3x`。
- 限制数字全部取接口 `execution_settings`，保留实际 0，不虚构缺失数值。
- 明确标注为**基础上限**；实际按当前环境短线／波段模式执行，不等价于各 horizon 的最终限制，也不代表 LIVE 和 DEMO 一致。
- 保存／激活说明：下一次新决策生效；不立即触发推演；旧仓保护保持。非当前策略保存不声称立即运行。
- 本地模块拼接不包含实时变量求值，故预览改为“拼接正文／模块与插槽”，说明未填实时行情，不再冒称“已代入真实盘口／实发效果”。
- 内置 small300 的后端绑定固定，UI 禁止直接改为 standard，指引复制后切换；自定义方案可独立绑定。

## 4. 分钟策略运行范围（最终补充需求）

已按主 agent 给出的契约完成 UI 与 4 项回归：

- GET 返回的 `engine_version,environment,enabled,authorized,status` 显示为就绪／未启用／需确认／绑定已变化；未知值显示待核验。
- DEMO 展示“模拟盘已启用／未启用；实盘需切换账户后单独确认”。不会因授权状态改变 DEMO。
- LIVE 授权必须由超级管理员点击后，在对话框输入完全匹配的确认词；`enabled` 使用布尔值。取消、错误词、普通管理员、非 live 环境均不发授权请求。
- 风险说明：真实损益；授权绑定账户、策略与风控；变更需重新确认；不切换账户、不立即提交订单、不改变旧仓保护。
- 策略保存、激活、回滚等重新加载后同步核验引擎范围；请求 generation 防止旧 ready 覆盖新状态。运行范围请求失败只影响本区，有独立重试，不让整个提示词页失效。
- “ready”只称“已就绪（不代表已成交）”。没有通过 DOM 或初始加载自动授权。
- **后端 route 和真实授权绑定验收归主 agent／A01；本次测试全为 fake，无实盘授权行为。**

## 5. 问题严重性与改动摘要

| 等级 | 已修问题 | 影响 |
| --- | --- | --- |
| P1 | small300 旧确认参数、基础值与实际周期语义混淆 | 操作者按错误限制理解执行行为 |
| P1 | 保存单组 pipelines 可能清空另一组 | 交易编辑影响复盘／复盘编辑影响交易；现保留全部视图 |
| P1 | 路由忽略 restoreSession 的失败返回 | 缓存 token 在会话核验失败时仍可进入管理页；服务端权限并未被绕过，但前端边界失真 |
| P1 | 新建执行模式缺省／绑定与正文耦合 | 无法可靠独立选择执行模式；改为显式原子绑定 |
| P2 | 普通管理员密码目标 ID 为 0、本人改密后未清会话 | 正常功能失效和会话状态不一致 |
| P2 | 大量写按钮重复点击／确认期间可并发 | 增加重复新建、测试发送、重放或高风险提交可能；页面级互斥包含确认窗口 |
| P2 | 通知通道回读覆盖其他草稿、失败假开关 | 编辑丢失／显示不真实；现局部更新与回滚 |
| P2 | LLM 自动激活缺少 provider_id | 同名模型可能歧义；现明确绑定供应商 |
| P2 | 委员会开关显示已开启但仅改本地 | 现独立保存 enabled，并保持其他草稿 |
| P2 | 复盘确认请求 `RUN JOB`、排队与完成混淆 | 改 `RUN EVOLUTION`；仅显示回执，报告与发布仍分开 |
| P2 | 首屏失败空白、旧历史／异步日志串页、导入旧文件、复制失败报成功 | 加显式重试、清旧数据、响应序号和正确反馈 |
| P3 | 重复关闭与配置普通刷新、预览冒称实发 | 保留公共可访问关闭按钮，移除冗余；实际状态核验／错误重试不删 |

## 6. 验证

- `npm.cmd run typecheck`：通过（两套 tsconfig）。`onBeforeRouteLeave` 已实际使用，不再有 TS6133。
- `node --experimental-strip-types --test tests/audit-a09-admin-ui.test.mjs`：**39/39 通过**。
- `npm.cmd run build`：通过，包含 assets prepare、typecheck 和 Vite 构建。
- 最近一次 `npm.cmd run test:unit`：**235 tests，233 通过，1 失败，1 跳过**。
  - 跳过：Windows 无开发者模式／权限的 symlink fixture，既有环境限制。
  - 失败：`tests/audit-a08-public-ui.test.mjs` 的 `displayed daily ratio never substitutes for an execution circuit signal`。主 agent 正把 telemetry 迁移到 risk_status；此跨域测试尚需与当前语义统一。A09 未修改公共组件或 A08 测试，已通知主 agent。
- `git diff --check`（A09 修改的已跟踪代码）：通过；仅 Git Windows CRLF 提示，无空白错误。
- 39 项回归覆盖：实际 handler 请求体与权限、互斥释放、动态参数、原子新建、独立绑定、四条管线保留、草稿保护、通知局部更新、密码 ID、异步日志、16 数据页失败重试、全部管理页 Vue 编译、排队回执与 LIVE 授权门禁。

## 7. 跨域事项与主 agent 验收清单

1. **A08／主 agent**：统一最后一项 telemetry 断言，再跑完整 unit；不应恢复用展示用亏损比例推断执行熔断。
2. **A10／主 agent**：落实 `/gateway/jobs/self_improvement/run` 的持久排队、重复请求复用、仅允许 self_improvement；前端已使用 `RUN EVOLUTION` 和 202 回执语义。现有“查询复盘报告”只读 `/memory`，另保留 `/admin/gateway` 状态入口；不凭空调用未约定的单请求 GET。若新增 request_id 查询协议，再由主 agent接入。
3. **A01／主 agent**：落实 strategy/engine 的 GET 与 live-authorization POST、同算法 LIVE 显式 opt-in、账户／策略／风控绑定变化失效。前端没有修改运行服务、环境或授权默认值。
4. **后端现有限制**：部分配置为全量 PUT，没有版本条件更新。本次避免本页草稿之间互相覆盖，但没有假装解决不同管理员同时写同一配置的后端乐观锁问题。
5. **隔离浏览器验收**：普通管理员／超级管理员分别遍历 18 个实际管理页面；窄屏弹窗焦点和 busy 状态；新增 standard/small300 策略只产生一次写入；独立绑定保留提示词草稿；策略激活后 engine 状态刷新；DEMO 无授权按钮，LIVE 输入确认词后只发对应 POST；错误重试、QQ 关闭轮询、复盘排队回执与报告查询。
6. 不需在真实账户验收平仓、切换环境、授权或恢复备份。使用 mock／隔离目录；不停止或收紧运行中的策略。
