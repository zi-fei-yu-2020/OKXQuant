# 本地测试与 CI

## 安全边界

本项目会通过 Gateway 启动交易和维护任务；部分模块及历史测试也会创建或更新本地数据库、提示词、配置文件。因此不要在运行实盘的工作区直接执行 `python -m unittest discover -s tests`。

统一使用：

```bash
python scripts/run_tests.py --verbose
```

该入口会：

1. 复制源码到系统临时目录，使用当前工作区的修改，不要求先提交。
2. 不复制 `.git`、真实 `.env`、`data/`、`logs/`、`backups/`、`node_modules/` 和前端构建产物。
3. 用空运行目录和隔离的 `HOME` 启动测试子进程，不继承 OKX、LLM、通知和自定义备份凭证。
4. 设置 `OKXQUANT_TESTING=1`，禁止 Dashboard 和 Gateway 启动后台工作。
5. 在测试发现与执行期间阻止未 mock 的网络连接、DNS 查询和子进程启动。即使业务代码捕获了阻止异常，测试命令仍会失败并列出相关测试。
6. 将测试失败状态返回给调用方，完成后清理临时副本。

这是可信项目测试的副作用隔离，不是执行不可信 Python 代码的安全沙箱。不要把真实密钥写进测试源码或测试夹具。

`OKXQUANT_TESTING=1` 本身不能重定向所有状态文件；请始终使用上述测试入口。不要在正常部署环境设置该变量，否则自动调度和监控缓存后台刷新不会启动。

## Python：Linux / WSL

CI 使用 Python 3.11 和 3.12。建议使用独立虚拟环境：

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python -m pip check
python scripts/run_tests.py --verbose
```

`httpx==0.28.1` 已列在 `requirements.txt`，FastAPI 的 `TestClient` 需要它。请在运行测试的同一个解释器环境中安装依赖，不要混用 Windows 和 WSL 的虚拟环境。

`fcntl` 是 Unix 标准库，相关测试还检查 POSIX 文件权限。因此 Windows 用户应进入 WSL 执行 Python 测试，不要安装伪造的 `fcntl` 包或把文件锁替换为空操作。

对于本仓库在 Windows 中的位置，可从 PowerShell 进入 WSL 后执行：

```bash
cd /mnt/d/Project/workspace/OKXQuant
python3 -m venv /tmp/okxquant-test-venv-local
. /tmp/okxquant-test-venv-local/bin/activate
python -m pip install -r requirements.txt
python scripts/run_tests.py --verbose
```

只跑一组测试时：

```bash
python scripts/run_tests.py --pattern 'test_admin_api.py' --verbose
```

新增涉及 HTTP、DNS、CLI 或进程的单元测试时，应在测试中显式 mock 对应边界，不要关闭离线防护。关于行情、账户和模型的端到端联调，应在独立模拟盘环境中另行执行，不属于这套离线测试。

## 前端：Windows / Linux

使用 Node.js 22 和锁文件安装依赖：

```bash
cd okxquant_frontend
npm ci
npm run typecheck
npm run build
```

- `typecheck` 检查 Vue 应用和 `vite.config.ts` 两份 TypeScript 配置。
- `build` 先运行 `typecheck`，成功后才执行 Vite 生产构建。
- 保留 `noUnusedLocals`、`noUnusedParameters` 等严格配置；不要通过关闭这些设置绕过检查。
- PowerShell 如果阻止 `npm.ps1`，使用 `npm.cmd ci`、`npm.cmd run build`，无需修改系统执行策略。

不要在 Windows 和 WSL 之间共用同一个 `node_modules`。切换安装平台时，在目标平台重新执行 `npm ci`。

## GitHub Actions

工作流位于 `.github/workflows/ci.yml`：

- `main`、`dev` 推送触发。
- 目标分支为 `main`、`dev` 的 Pull Request 触发。
- 支持 `workflow_dispatch` 手动触发。
- Ubuntu 上运行 Python 3.11 / 3.12 的隔离测试。
- Node.js 22 下执行 `npm ci` 和包含类型检查的生产构建。
- 不需要仓库 Secrets，不向测试注入交易凭证。

工作流文件需要提交并推送后才会在 GitHub 执行；仓库设置中的 Actions 开关和分支保护仍由仓库管理员管理。本地测试通过不等于远端 CI 已执行成功，也不代表 Docker 镜像或交易所连接已验证。
