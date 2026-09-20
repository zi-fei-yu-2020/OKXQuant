# A10：网关、调度、插件、备份、CI/部署及剩余工具审查

日期：2026-09-19。范围：用户指定 A10 独占文件；与其他 agent 并行，不回退其他人的修改。

## 结论和执行边界

已修复本报告列出的实际缺陷，新增隔离回归测试；没有调整任何开仓阈值、trader 的 900 秒间隔或边界后前 10 秒窗口，没有停策略，没有增加自动发布权限。

- 未读取或输出真实 `.env`、运行时 data、账户密钥或真实备份内容。
- 未执行部署、容器构建、systemd 操作、服务器操作、真实下单/通知/云上传。
- 未 commit/push、未再委派；没有修改 `app.py`、README、前端和 A07 的 `okxquant_gateway/secrets.py`。
- 配置写锁复用主 agent 提供的 `scripts/config_lock.py`，没有另造配置锁。
- `git status` 中 A07 secrets、其他后端/前端/策略修改属于并行工作，不归本报告修复清单。

## 已修复问题

| 严重性 | 缺陷 | 修复 |
|---|---|---|
| 高 | supervisor 在 worker 获得 flock 前写 PID，竞争失败的短命进程可覆盖真正 owner；拉起失败可结束监督线程 | 仅 flock 胜者发布 PID；跨进程序列化拉起、探测已有锁、回收退出的子进程；瞬时失败继续监督；仅停止自己拥有的进程 |
| 高 | 通知网络调用串行阻塞调度 tick，长维护任务占满公共池使交易错过原窗口 | 独立调度线程；保护、账本、trader、demo_scalp 分别专用池；备份独立维护池；其余仍复用公共池，不改 JOBS 频率 |
| 高 | 子进程 timeout 仅清理父 Python，后代可能继续执行 | 调度子进程独立 process group；timeout 清理整个组并回收父进程；通信失败不误判为未启动重试 |
| 中 | 队列中的旧/禁用任务仍可能运行，备份排序改变 job identity，单任务异常影响后续任务 | 执行前重新检查当前 spec 和计划；排队日历任务保存原到期时刻；名字绑定 job ID；隔离单任务异常；坏备份配置不阻断核心任务 |
| 中 | 启动失败仍消耗整次计划，重启留下永久 running 记录，完成写盘错误可能被当作启动失败 | 仅未启动的 OSError 回退原时钟并 30 秒退避；执行后的状态写入错误不回退时钟；重启标记未知执行为 failed/125，不自动重放交易 |
| 高 | 备份排除函数 `lstrip("./")` 抹去 `.env` 前导点；嵌套秘密文件、链接可能入包 | 保留前导点；强制排除嵌套 `.env*`、认证/密钥路径；跳过源符号链接、硬链接及特殊文件；排除不因用户清空 exclude 而失效 |
| 高 | 本地交付路径只依赖配置保存阶段验证；job ID 可拼接进入 SQLite/manifest 路径 | 运行时再次限制项目 backups 下目标，拒绝链接目录/目标；job ID 限制为安全标识；排他创建归档/manifest、同目录原子替换本地归档 |
| 高 | 在线数据库直接 tar 时遗漏 WAL 的已提交数据 | data 下 SQLite 文件使用只读连接做一致性热快照，再加入归档；无需用户额外启用独立 SQLite 目标；快照失败则备份失败，不上传伪完整包 |
| 中 | SQLite retention 混合所有数据库计数，多个本地任务相互清理；低于 1 的本地 retention 可删光 | 按数据库及归档 job ID 分别保留；拒绝本地零保留；不清理其他任务的备份 |
| 高 | 密文认证失败前就覆盖目标；verify 比 restore 宽松且无界 `getmembers()` | 认证成功才原子发布明文；verify 复用 restore 的 CRC、路径/链接、PAX/成员数、解压大小上限检查，verify_only 不发布任何文件 |
| 高 | 备份配置并发读改写丢更新，首次创建密钥竞争或损坏存储被当成空存储覆盖 | 整个读改写事务使用配置锁；拒绝重复 job ID；损坏配置/密钥/密文拒绝覆盖，不静默启用默认任务 |
| 高 | 低磁盘清理执行全局 `find /tmp ... -delete` 和共享 npm 清理 | 仅清理本项目 `backups/tmp` 的旧普通文件；拒绝链接目录；日志轮转拒绝链接/目录目标；磁盘检查使用项目所在文件系统 |
| 中 | 诊断模块 import 即运行真实账户 CLI；零订单汇总除零；日报 shell 字符串受带空格路径影响 | 诊断执行移动到 main guard，加超时；零样本安全输出；日报使用当前解释器 argv，不在 import 时创建 backups |
| 高 | 容器默认让 API/任务以 root 执行；旧调度服务与 Gateway 使用不同锁 | root 仅做受限卷所有权初始化，随后 gosu 到固定非特权用户；拒绝任意 env 路径和链接运行目录；旧服务名称改为运行同一 Gateway worker，分享 flock；服务加专用用户/UMask/venv 解释器 |
| 中 | release 发布未跑 Python 离线 gate，checkout 保留凭证，申请无用写权限 | registry login/build 前运行离线测试；不保留 checkout 凭证；移除未使用的 id-token/attestations 写权限；release 触发和 packages 权限未扩大 |
| 中 | 内置插件 registry 把 enabled 等同 healthy，权限清单容易被误认作强制隔离 | enabled 显示 unknown；明确 health_verified=false、permissions_enforced=false、in-process-trusted，不再声称检查过健康/权限 |
| 中 | 测试源快照可跟随源树链接进入运行时路径 | copy_sources 排除源子链接和 env，拒绝顶级源链接；继续清空 HOME、只保留环境 allowlist、禁止未 mock 网络/子进程 |

## 追加闭环：日报本金/重置时间 scope 化

按主 agent 的 A07 联动要求，`daily_summary_and_backup.py` 不再读取 `account_initial_state.json` 顶层兼容投影，而是先取得选定 environment，再调用 `load_account_baseline(scope=environment.identity)`。

- 只有 baseline_configured 严格为 true、account_scope 匹配且本金有限正数时，才显示已确认本金并采用该账户 reset_time；展示默认 10000 不被当作已配置。
- 旧 DEMO 兼容、LIVE 不认领无主旧本金的决策完全交给 A07 helper，不复制旧格式迁移逻辑。
- 复用 `scoped_rows` / `today_lifecycle_stats`，只统计本账户且在对应 reset 后的已平仓行；未配置基线不继承别的账户/无主旧 reset，也不计算本金收益率。
- 发送前再次确认 environment.identity，发生切户则拒绝发送混合报告；不调整策略或开仓行为。
- 新增 5 项合成 fixture 回归：v2 投影不串户、旧 DEMO 兼容、LIVE 不认领、默认值未确认、生成中切户。

## 手工 self_improvement review：给主 agent 的最终接口

```python
GatewayStore.request_job(job_name: str, *, actor: str = "") -> dict[str, Any]
GatewayStore.job_requests(limit: int = 30) -> list[dict[str, Any]]
GatewayStore.pending_job_requests() -> list[dict[str, Any]]
GatewayStore.claim_job_request(request_id: int) -> dict[str, Any] | None
```

API 只需在认证、确认词检查通过后调用：

```python
request = store.request_job("self_improvement", actor=authenticated_actor)
```

返回：

```text
request_id: int
job_name: "self_improvement"
actor: str（最长 160，移除 CR/LF）
status: "pending" | "running" | "success" | "failed"
created_at: 北京时间字符串
finished_at: 未完成为空字符串
run_id: int | None
deduplicated: bool
```

- 除 self_improvement 外统一 ValueError，包括 trader/demo_scalp；DB 也有限定 job_name 的 CHECK。
- 同一任务存在 pending/running 请求时，BEGIN IMMEDIATE + partial UNIQUE index 保证重复点击/多线程/多 API 实例返回同一请求；不会改变最初 actor。终态后允许操作者再次主动请求。
- 使用现有 Gateway DB 新增表/索引，不创建第二套数据库或执行器。claim 与 job_runs 创建在同一事务；finish_job 原子更新 request 终态。
- worker tick 在计划任务之后分派；手工和计划任务共用 running map，不并发执行 self_improvement。
- 固定命令为当前 Python + `scripts/self_improvement_engine.py --force`。不接收用户脚本/参数，不添加 publish/promote/activate。
- 手工执行不更新 `job.last.self_improvement`。计划 review 如跨过当天计划时刻，随后补跑最新已到期时刻一次；原 20:00 不被手动按钮吞掉。trader 的时间判断未改变。
- worker 重启后 pending 保留；running 标记 failed，job_runs return_code=125，说明结果未知，不自动重放。
- `scheduler_snapshot()` 和 `GatewayScheduler.status()` 增加 `manual_requests`。鉴权、确认词、API audit event 和 HTTP 码由主 agent 的 app route 负责；前端确认词由 A09 负责。

## 覆盖与修改文件

### 实际修改

- 网关：`okxquant_gateway/{scheduler,store,supervisor,worker,plugins}.py`。
- 备份存储：`okxquant_backend/{backup_store,backup_secrets}.py`。
- 工具：`scripts/{backup_runtime,backup_restore,cleanup_disk,daily_summary_and_backup,debug_aggregate_orders,debug_audit_bills,run_tests,check_ui}.py`。
- 发布/部署：`.github/workflows/publish-release.yml`、`Dockerfile`、`docker/entrypoint.sh`、`deploy/{okxquant-backend,okxquant-gateway,okxquant-scheduler}.service`。
- 回归：`tests/test_audit_a10_operations.py`、`tests/test_audit_a10_existing.py`、`tests/test_audit_a10_baseline.py`。
- 报告：`docs/audit-a10-operations.md`。

### 已审查但不修改行为

- 网关 `agents.py/channels.py/events.py/publisher.py/telemetry.py`；A07 secrets 仅通过既有隔离测试兼容，不编辑。
- `plugins/interceptors/*.py`：保留全部风控阈值。实际动态注册和执行在范围外 `okxquant_backend/interceptor_manager.py`。
- `scripts/nightly_backup_and_clean.py`、`qq_notifier.py`、`preview_ui.py`、`desktop_ui_review.py`、`ui_browser_audit.js`、`demo_execution_smoke.py`、`remove_retired_personal_wechat.py`。
- `.github/workflows/ci.yml`、`deploy/install.sh`、`compose.yaml`、`requirements.txt`。
- `check_ui.py` 新增 `--channel msedge`，不下载浏览器。本任务未启动/重复做实际浏览器审查，不把主 agent 的旧布局结果当成本次新验证。

## 测试证据

仅使用用户规定入口：

```powershell
wsl /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern 'test_audit_a10_*.py'
```

最终结果：**146 tests，OK**（2026-09-19，约 4 秒测试执行时间）。

- 全部代码复制到 WSL 临时快照；未复制真实 data/.env/home，也未使用真实账户。
- 新增覆盖：并发手工请求去重/持久化/claim、allowlist、--force、原计划保护、失败/重启恢复、任务隔离与超时组清理、配置/密钥并发、秘密排除、危险路径、WAL 一致性、保留策略、密文认证、verify/restore 一致性、只读诊断 import、清理范围、CI/容器静态约束。
- A10 聚合模块复用既有备份/恢复、Gateway、测试隔离和 DEMO smoke 测试。旧 smoke fixture 把 scripts.__path__ 置空，A10 聚合中预加载新增的纯 execution_costs 模块，避免把 fixture 导入问题误当成交易实现缺陷；没有修改或弱化风险策略。
- 各次执行未出现未 mock 外部操作。输出有 WSL localhost proxy 提示和 Starlette/httpx 弃用告警，不影响通过。
- 指定范围 `git diff --check` 通过；未运行完整项目全套、实际 Docker build 或部署。

## 跨域未解决项与主 agent 后续

1. **高 / 插件信任边界**：`okxquant_backend/interceptor_manager.py` 的 exec_module/check_risk 直接在宿主运行，共享可变输入；所谓 run_sandbox_test 不是文件/网络/权限沙箱，也不能中断无限循环。异常 fail-closed 不等于执行隔离。需要该文件 owner 设计真正的进程/权限隔离及超时；本次不修改拦截阈值，不宣称已隔离不可信插件。
2. **高 / 手工按钮链路**：主 agent 实现 POST `/api/v1/admin/gateway/jobs/self_improvement/run` 的鉴权、精确 `RUN EVOLUTION` 确认、API 审计与响应；A09 修正前端 body。A10 仅保证持久队列/执行/状态接口，未代改 app/前端。
3. **中 / 旧手动启动入口**：部署 unit 已统一 Gateway owner，但范围外 `python -m okxquant_backend.scheduler` 仍是独立锁/调度器；不要手动与 Gateway 并用。请对应 owner/主 agent 决定兼容入口收敛，README/运维文档变更由主 agent 处理。
4. **中 / 部署验收**：固定 UID、旧卷所有权迁移、gosu 依赖、源目录只读和后台管理更新功能需要主 agent 实际 build/部署验收。没有执行这些脚本，不能把静态测试说成容器实测。服务强制 SIGKILL/宿主故障的孤儿子进程清理仍依赖容器/systemd cgroup；普通 timeout 已清理作业进程组。
5. **中 / 备份并非完整安装介质**：现有可选 scope 不包括完整前端、plugins、deploy/.github 的全部内容；`root_configs` 已补实际 compose.yaml，但不能把默认归档宣传成可独立重建整套发行版。建议发行镜像/源码版本与数据备份配套保存，scope/API/UI 扩展由主 agent 协调。
6. **中 / 本地目录威胁模型**：恢复使用 descriptor-relative/O_NOFOLLOW；备份侧新增边界/链接检查和原子文件发布，但没有声称抵御同 UID 恶意进程在所有检查之间持续重命名父目录。项目目录仍应只允许可信运行用户管理。不可信插件问题不能靠备份路径检查替代。
7. **中 / 存储失效**：SQLite 持续不可写时，任何 durable completion 都不可能保证成功；保持已启动作业时钟，禁止当未启动重试。恢复后需核对未知 job_runs/request，worker 重启会标记失败而不自动重放。
8. **低 / 供应链与测试环境**：requirements 存在范围依赖、Actions 使用大版本 tag、镜像/CLI 并非全量 digest 锁定；本次不盲目升级或臆造 SHA。Starlette/httpx 弃用告警应由依赖 owner 在全套 CI 中确认。离线 runner 是协作测试隔离机制，不是对恶意 Python 测试的 OS 级沙箱。
