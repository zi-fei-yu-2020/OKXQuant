# 第二轮交叉复审：Operations

日期：2026-09-19。审查角色：独立交叉复审；不修改生产源码。

> 最终代码/隔离测试复核：PASS。CROSS-OPS-01/02/03 及 A10 测试契约阻塞已关闭；独立重跑 Operations 28/28、A10 53/53。真实容器降权、旧卷兼容和重建/退出行为仍待主 agent build/服务器验证。下方初审失败保留作为历史证据，以文末关闭记录为准。

## 范围与执行约束

本轮只写：
- tests/test_cross_review_ops.py
- docs/cross-review-operations.md

只读审查 Gateway scheduler/store/worker/supervisor、Dockerfile/compose/entrypoint、systemd 单元、CI/release 工作流、复盘状态与 memory registry 发布锁。没有复审原 A06 app API；对 A07 自定义 AST 解释器仅查看存储目录配置，不评价其执行逻辑。没有运行生产进程、Docker、真实外部 API、交易、部署或 secret 读取；没有再委派。

所有 Python 测试只通过隔离 runner：

```text
wsl --exec /tmp/r20-review-venv/bin/python scripts/run_tests.py --pattern test_cross_review_ops.py -v
```

runner 复制源码、排除真实 data/logs/.env，并阻断未 mock 的网络、DNS、子进程。进程相关测试用 Mock，Docker 项只做源码/路径检查；真实镜像 build、root 旧卷迁移、UID 和重建行为等待主 agent 提供运行证据，不能用这些静态测试替代。

## 初审结论（历史记录）：HOLD

本轮尚未发现证实的 P0。已证实 P1：自定义策略文件不持久、手动任务终态丢失，以及新增 metadata 补写阻塞 trader 时间窗口。pipeline 目录已由 A07 改为持久 overlay 并通过独立存储回归；原始终态丢失已由主 agent 修复并通过独立回归，但通用异常分支还需补齐。下表记录发现时间序列，不把主 agent 的修复归为本 agent 修改。

### CROSS-OPS-01 — P1，自定义 pipeline 文件没有持久卷

**证据**：初审 interceptor_manager.PLUGINS_DIR 为 ROOT_DIR/plugins/interceptors；create_plugin/save_plugin_code 在这里写文件。Dockerfile chown /app/plugins 使其可写，但 compose 的 volume targets 只有 /app/config、/app/data、/app/logs、/app/backups 及 home 下 .okx/.bypy/.npm-global，没有 /app/plugins。注册 JSON 在 /app/data，因此文件和注册元数据的生命周期不一致。

**最小复现**：创建自定义 .py → 容器内生成 /app/plugins/interceptors/custom.py → 重建 app 容器 → 文件丢失，但持久数据卷上的注册记录还在。此次未操作真实 Docker；隔离路径测试直接证明写目录不在任一持久卷下。

测试：test_custom_pipeline_directory_is_on_a_compose_persistent_volume。

**最新复核：代码/隔离存储层面已闭环。** A07 已新增 CUSTOM_PLUGINS_DIR=ROOT_DIR/data/interceptors。目录检查改为识别实际自定义写目录（而非只读 PLUGINS_DIR），已 PASS。新增 test_pipeline_overlay_writes_and_image_replacement_keep_files_and_metadata：验证 create/save 均写 overlay、镜像源文件不变、文件 0600、换一份新的源目录后 overlay 和注册信息仍可解析。测试 mock 规则校验器，只验证存储而不审其解释器逻辑。真实容器重建 smoke 仍待主 agent 提供。

### CROSS-OPS-02 — P1，手动复盘终态落库失败后永久 running

**原始缺陷**：_run_job 的子进程执行完成，finish_job 遇到短暂 OSError/SQLite 锁；tick 取出 Future 异常并移除 running 映射，但 manual_job_requests 仍 running。pending 查询不会再取它，重复点击由于唯一活动请求索引也只能返回同一请求。活着的 worker 不做 recover_processing，只有重启才能解锁请求。

**最小复现**：在临时 GatewayStore request_job → _execute_manual，mock _run_process 返回 0，mock finish_job 抛错；恢复 finish_job，再 tick 三次。要求终态为 success/failed，且不再次执行进程。原始代码失败；主 agent 增加 pending_completions/_finish_job 后，下列两个测试已通过：
- test_completion_persistence_failure_must_not_leave_manual_request_running_forever
- test_sqlite_completion_failure_must_reconcile_without_reexecuting

**遗漏分支**：_run_job 最后的 except Exception 仍直接调用 store.finish_job。_run_process 在子进程启动后 communicate 失败时会抛 RuntimeError，恰好进入该分支；再叠加一次 SQLite 锁，仍回到永久 running。测试 test_post_spawn_io_failure_also_retries_terminal_metadata 当前复现失败。所有已启动/已结束分支均应走同一终态补写通道，不能重放进程。

### CROSS-OPS-03 — P1，metadata 重试位于 tick 同步关键路径，挤掉 trader 窗口

**证据**：新 tick 入口先 _flush_completions，然后才获取当前时间/计算 due。GatewayStore.connect 的 SQLite timeout=10，而 trader 保持每 900 秒、slot 内前 10 秒准入。一次完成状态补写等待锁达到 10 秒，就能使本轮 trader 未入队。pending completion 数量增加时还会叠加等待。

**确定性最小复现**：先生成手动任务的待补写结果；时钟固定在 20:00:00；finish_job 模拟正常默认 SQLite 等待，返回时为 20:00:10，抛 database is locked；tick 应将当期 trader 入队，但返回列表为空。没有真实 sleep 或数据库重负载。

测试：test_slow_manual_completion_retry_does_not_consume_trader_window。

**要求**：metadata 重试独立于调度线程，或采用真正有严格预算的非阻塞写入。不能扩大/取消/降低既有开仓频率作为掩盖。测试 mock 尊重显式 timeout=0 的实现。

## PASS 范围（代码审查 + 隔离测试，不代表真实部署已验收）

|范围|已验证事实|证据/限制|
|---|---|---|
|worker 唯一性|先获得 flock 再写 PID；锁竞争失败者不覆盖 PID、不初始化/恢复别人的 job 表|真实临时 POSIX flock；无真实进程|
|supervisor 启动|worker 已持锁但未写 PID 时不再 spawn；已有 owned process 会 poll/reap|Mock spawn + 真实临时锁|
|daemon stdout|supervisor 将 worker stdout 写文件、stderr 合并，不创建无人消费的 PIPE|检查 Popen 实参；未实际启动 daemon|
|子任务 stdout/退出|子任务 communicate 同时消费 stdout/stderr；超时 killpg 并再次 drain|Mock process；未模拟无限输出/OOM或逃逸 session 的后代|
|线程分离|通知投递不在 schedule_loop；坏 tick 会记录后继续；长 maintenance 占满通用池时 trader/guard 仍使用独立执行器|真实线程 Event、Mock 子任务；manual 排队释放后可完成|
|手动任务可靠性|请求只允许 self_improvement；并发点击只创建一个活动请求；claim 与 run_id 同一 SQLite 事务；manual 不推进 scheduled clock|临时真实 SQLite；并发线程|
|重启恢复|running 请求标 failed/125 unknown，未开始的 pending 保留；不自动重跑结果未知的任务|临时真实 SQLite|
|manual 超时|timeout 写成 failed/124，不留 pending 自动重试|Mock TimeoutExpired|
|开仓频率约束|trader=900 秒、position_guard=60 秒、demo_scalp=60 秒；自动交易默认启用；显式暂停 trader 不移除 guard|未新增/修改生产阈值、filter、开关；CROSS-OPS-03 单独阻塞|
|env 实际路径|compose、Dockerfile、entrypoint 均 /app/config/.env；宿主 .env 作为初始 env_file，与可写持久配置分离|默认部署路径静态一致|
|env 优先级|config、okx_runtime、notifications 的可写文件覆盖陈旧继承环境|只使用临时合成 env 文件，不读取真实秘密|
|root 旧卷迁移设计|entrypoint 在 exec gosu 前，对全部现有 named volume 目标递归 chown；env 文件 chmod/chown；umask 077；HOME 与 named volumes 一致|静态 PASS；真实 root→10001 权限和旧卷内文件 smoke 待主 agent|
|非 root/安全选项|命令通过 gosu okxquant:okxquant 执行；compose init/no-new-privileges；systemd User/Group 与 shared worker 统一|静态，不宣称 UID 已实测|
|CI 全库方向|CI 的 Python 3.11/3.12 及 release gate 均调用 python scripts/run_tests.py --verbose；默认 test_*.py；release 在 registry login 前测试|测试解析真实源码；没有只跑 audit 子集/裸 discover|
|前端 CI|CI 运行 npm run test:unit；build 包含 typecheck；Docker builder 也 npm run build|脚本存在；本 agent 未执行 npm/build|
|复盘失败分类|load_sources/model_review/review_draft/candidate_persistence/report_persistence 分别持久化 failed/phase；旧成功报告和批准记忆保留|五阶段故障注入|
|复盘与交易锁|报告/候选暂存不获取 trade_lock.writer，不直接改变 active prompt；不会因为慢模型长期占用交易 writer|Mock 禁止 writer + 实际候选 SQLite/hash 对比|
|记忆发布边界|publication_gate 同时检查 trader/brain/evolution cycle flock，账号在门内重验；publish/rollback 源码均使用该门|真实 flock；未审记忆内容模型逻辑|
|崩溃状态展示|Gateway 重启的 failed/125 能覆盖旧 engine running 状态，保留旧成功时间|实际临时 job registry 与状态投影|

## 附加回归与 CI 阻塞

除本轮交叉测试外，同样通过隔离 runner 执行：

|pattern|结果|
|---|---|
|test_gateway*.py|13/13 PASS|
|test_evolution_review_status.py|15/15 PASS|
|test_memory_registry.py|21/21 PASS|
|test_audit_a05_intelligence.py|31/31 PASS|
|test_audit_a10_operations.py|52/53，见下|

A10 的 test_completion_write_failure_never_rolls_back_started_job 仍断言旧接口抛 OSError。新增 pending_completions 捕获并补写后，该断言不再成立。需测试 owner 调整为“保留当次 schedule，不重放已运行进程，metadata 最终补写”，不要删除测试或改为 skip。由于 CI/release 确实执行全库，此不一致当前构成发布 gate 阻塞；不是要求生产代码为了旧断言重新丢终态。

A05 测试虽通过，输出有 _load_module_from_file 未定义的 interceptor 日志，已告知主 agent。这可能是 A07 中途修改状态，不在本轮解释器逻辑审查范围；最终集成应确认消失。

## 主 agent 的运行证据待补

1. 镜像 build、前端 typecheck/build 及依赖安装结果。
2. 使用合成旧 root:root named volumes 启动，证明服务 UID/GID=10001、非 root 执行业务；data/config/logs/backups/.okx/.bypy/.npm-global 均实际可写。
3. 故障重启与 SIGTERM：worker PID/锁唯一，旧进程/子进程停止后才接管；Docker 30 秒 stop grace 下不得遗留可提交交易的后代。
4. pipeline 在真实持久目录创建后，重建容器能读取同一内容；不能只有注册 metadata 留存。
5. 定向交叉测试全部通过后再跑一次全库 gate。

本报告暂不把“等待 build 运行结果”写成 PASS，也不为此自行启动容器或生产 worker。

## 最新定向结果（第一次集成复核）

隔离 cross-review 共 28 项：26 PASS，2 FAIL。保留失败断言，不 skip、不 expectedFailure：
- test_post_spawn_io_failure_also_retries_terminal_metadata（CROSS-OPS-02 遗漏分支）
- test_slow_manual_completion_retry_does_not_consume_trader_window（CROSS-OPS-03）

Pipeline 两条路径/存储测试均 PASS；OSError、SQLite completion 原始两条用例也 PASS。以上所有生产修复由原 owner 完成，本轮 agent 未修改生产文件。

## 关闭记录：2026-09-19，主 agent 修复后的独立短复核

### 修复确认

- **CROSS-OPS-01：关闭（静态/隔离存储边界）。** 实际自定义写目录为 data/interceptors；路径位于 compose data 持久卷下。create/save overlay、不写镜像源目录、0600 文件权限、替换源镜像目录后仍读取 overlay 与 metadata 的测试通过。真实容器重建仍待运行验证。
- **CROSS-OPS-02：关闭。** _run_job 的通用 except Exception 已调用 _finish_job，与正常完成、超时和启动失败分支一致。OSError、sqlite3.OperationalError 和子进程通信异常三个故障场景均验证：存储恢复后仅补写终态，不重跑进程，不永久 running。
- **CROSS-OPS-03：关闭（SQLite 锁等待边界）。** _finish_job 显式调用 finish_job(..., timeout=0)，finish_job 将 timeout 传入 connect，connect 再传入 sqlite3.connect。metadata 重试不会使用默认十秒 SQLite busy 等待；确定性 trader 准入窗口测试通过。此结论不是“任意底层文件系统 I/O 永不阻塞”的保证。
- **A10 测试契约阻塞：关闭。** 原测试保留并改为断言：job.last 不回滚、pending_completions 保留结果、flush 时 _run_process 未被调用、补写后队列为空且 job_runs 为 success；不是删除/skip 测试。

### 本次独立重跑

|命令 pattern|结果|退出码|
|---|---:|---:|
|test_cross_review_ops.py -v|28/28 PASS|0|
|test_audit_a10_operations.py|53/53 PASS|0|

均经 /tmp/r20-review-venv/bin/python scripts/run_tests.py，使用一次性源码副本与外部边界 mock，无真实进程/网络/容器操作。本次合计 **81 项独立验证通过**。主 agent 报告的 test_cross_review_*.py 79/79 仅作协作信息，本 agent 不冒认独立重跑过那一整组。

### 最终验收边界

**本轮已证实的代码层 P1 及对应回归/CI 契约阻塞均已关闭；静态与隔离测试范围 PASS。** 未发现证实的 P0。未新增开仓过滤、未改变 trader/guard/scalp 调度周期或自动策略默认开关。

**部署验收仍待主 agent：** 镜像 build、实际业务 UID/GID 降权、root 旧卷及目录可写性、真实容器重建持久化、SIGTERM/强制退出与后代进程回收。缺少这些运行证据时，不把本报告表述为“容器部署全面 PASS”。

本次短复核只追加/更新本报告；未修改生产源码、A10 测试或本轮 28 条交叉测试，未上线、读秘密、commit/push 或再委派。
