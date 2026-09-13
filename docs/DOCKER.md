# OKXQuant Docker 部署说明

本项目的容器部署面向 Linux 服务器；Windows 开发者应使用 Docker Desktop 的 Linux 容器或 WSL。交易脚本使用 Unix 的 `fcntl` 文件锁，不能在 Windows Python 中通过安装同名包来替代。

> **部署入口统一为 `compose.yaml`**，旧的 `docker-compose.yml` 已移除，避免误用不完整的配置。请显式使用 `docker compose -f compose.yaml`。

## 镜像与构建前检查

- 前端使用 Node.js 22 和 `npm ci`，构建包含严格类型检查。
- Python 运行镜像默认使用 3.12，内置 Node.js/npm 与 OKX CLI，支持 POSIX 文件锁。
- `NODE_IMAGE`、`PYTHON_IMAGE`、`OKX_CLI_SPEC` 均作为构建参数生效。Node 与 Python 默认使用兼容的 Debian/bookworm 镜像；不要把 Alpine 的 Node 二进制混入 Debian 运行镜像。
- `docker/entrypoint.sh` 初始化持久化配置目录及权限；CLI 的 HOME 与 Compose 挂载目录一致。
- 文档图片复制到前端 `/images/` 及后端文档目录，不再引用不存在的根目录 `static/`。
- 密钥、运行数据、浏览器截图与本地验证报告不进入构建上下文。

启动前仍须确认实际生效的交易环境、凭据、旧数据卷覆盖值和唯一的调度器。不要通过关闭风控、伪造 CLI 返回值或改用真实账户来绕过验证。

## 1. 准备配置

在项目根目录执行：

```bash
cp env.example .env
chmod 600 .env
```

编辑 `.env`，至少核对以下项目：

```dotenv
# 模型服务：请填写自己实际可用的服务地址、模型名与凭证
LLM_BASE_URL=https://your-provider.example/v1
LLM_API_KEY=replace_with_your_key
LLM_MODEL=replace_with_your_model
LLM_REASONING_EFFORT=high

# 首次验证使用模拟盘；实盘凭证不要用于构建或测试
OKXQUANT_OKX_ENV=demo
OKX_DEMO_API_KEY=
OKX_DEMO_SECRET_KEY=
OKX_DEMO_PASSPHRASE=

# 初始化管理员凭证：12～128 位，包含字母和数字
OKXQUANT_SETUP_TOKEN=replace_with_a_long_random_setup_token
OKXQUANT_HTTP_PORT=8080
TZ=Asia/Shanghai
```

不要把 `.env`、API Key 或运行数据库提交到 Git。访问令牌不要使用本文示例值。

## 2. 检查、构建与启动

确认配置和数据卷后执行：

```bash
# 只验证 Compose 语法，不启动服务，也不输出展开后的密钥
# 该检查不能替代镜像构建或交易执行验证。
docker compose -f compose.yaml config --quiet

docker compose -f compose.yaml build
docker compose -f compose.yaml up -d

docker compose -f compose.yaml ps
docker compose -f compose.yaml logs -f --tail=100 app
```

本机访问入口：

- 交易监控终端：`http://localhost:8080/`
- 管理后台：`http://localhost:8080/admin/`
- 产品说明页面：`http://localhost:8080/docs`
- API 交互文档：`http://localhost:8080/api/docs`
- 健康检查：`http://localhost:8080/health`

**启动后端并非只打开网页**：正常运行模式会启动 Gateway，由其调度交易和维护任务。首次部署应使用模拟盘，并先核对凭证、交易环境和调度配置。

## 3. 配置与数据持久化

`compose.yaml` 声明以下 Named Volumes：

| Volume | 容器路径 | 用途 |
| --- | --- | --- |
| `okxquant_config` | `/app/config` | 管理后台修改后的运行配置 |
| `okxquant_data` | `/app/data` | SQLite 数据库、决策、提示词、记忆和加密密钥存储 |
| `okxquant_logs` | `/app/logs` | 运行日志 |
| `okxquant_backups` | `/app/backups` | 本地备份 |
| `okxquant_okx` | `/home/okxquant/.okx` | 预留给 OKX CLI 的本地配置 |
| `okxquant_bypy` | `/home/okxquant/.bypy` | 预留给网盘备份客户端的配置 |
| `okxquant_npm` | `/home/okxquant/.npm-global` | 预留给 npm 全局安装的目录 |

宿主机 `.env` 用于 Compose 注入初始环境；容器内 `OKXQUANT_ENV_FILE=/app/config/.env` 用于保存后台配置更新。加载时，持久化配置可覆盖继承的环境变量，加密凭证存储也会参与最终配置解析。因此修改宿主机 `.env` 后，仍应核对后台实际生效值。

镜像设置 `HOME=/home/okxquant`，并保留镜像内置的 `/usr/local/bin/okx`。可选的后台 CLI 升级写入 `/home/okxquant/.npm-global`，其 `bin` 优先加入 PATH；即使该卷为空，内置 CLI 仍可使用。

普通 `down` 不删除 Named Volumes。**不要执行 `down -v`，除非明确要销毁配置、账户数据库和运行数据。**

## 4. 更新与回滚

Docker 模式下，管理后台不会直接执行 Git 更新。镜像应由宿主机管理：

```bash
# 先备份持久化数据，并在宿主机确认所用分支、提交与镜像标签
docker compose -f compose.yaml build --pull
docker compose -f compose.yaml up -d
```

`main` 用于同步上游，`dev` 用于二次开发。发布前记录实际提交和可回滚镜像标签；不要仅靠默认的 `local` 构建标识判断版本。不要同时启动旧版独立 scheduler 和 Gateway 调度器。

## 5. 部署前验证

不启动交易服务的验证命令：

```bash
# Linux / WSL 的独立虚拟环境
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python scripts/run_tests.py --verbose

# 前端构建自动包含 Vue 和 Vite 配置的类型检查
cd okxquant_frontend
npm ci
npm run build
```

测试入口会创建临时源码副本，不复制真实 `.env`、`data/`、日志、备份或用户主目录，并阻止未 mock 的网络访问和子进程启动。不要在实盘目录直接运行旧的 `unittest discover` 命令。

详细说明见 `docs/TESTING.md`。通过单元测试和前端构建不代表容器镜像或实盘交易已经验证通过。
