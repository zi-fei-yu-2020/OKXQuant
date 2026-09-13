# WSL 本地模拟盘运行与验收

## 部署位置

- Windows 源码：`D:/wangkai/workspace/OKXQuant`，分支 dev。
- WSL 发行版：Ubuntu-22.04；Linux 运行副本：`/opt/okxquant-local/app`。
- Python 虚拟环境：`/opt/okxquant-venv`；运行用户：okxquantlocal（非 root）。
- 凭据文件：`/opt/okxquant-local/config/.env`，0600，位于 Git 仓库之外。
- 管理员登录信息：`/opt/okxquant-local/admin-access.txt`；不要提交或粘贴其中的密码。
- 页面：`http://localhost:8080`；后台：`http://localhost:8080/admin`。
- 原始测试日志和交易验收报告：`/opt/okxquant-local/` 及运行副本 logs；浏览器截图在 Windows 源码的 logs/local-acceptance，均不提交。

## 本机控制命令（PowerShell）

```powershell
wsl -d Ubuntu-22.04 -- python3 /opt/okxquant-local/manage.py status
wsl -d Ubuntu-22.04 -- python3 /opt/okxquant-local/manage.py start
wsl -d Ubuntu-22.04 -- python3 /opt/okxquant-local/manage.py pause
wsl -d Ubuntu-22.04 -- python3 /opt/okxquant-local/manage.py resume
wsl -d Ubuntu-22.04 -- python3 /opt/okxquant-local/manage.py restart
wsl -d Ubuntu-22.04 -- python3 /opt/okxquant-local/manage.py stop
```

pause 只暂停将来的自动决策周期，不撤销已有订单、不打断运行中的交易周期；运行中的 Gateway 继续持仓保护、账本及其他维护任务。stop 停止本地服务，**不会平掉交易所仓位**。

- OKXQUANT_GATEWAY_AUTOSTART=0：后端不自动拉起 Gateway，启动时生效；监控仍可执行只读市场及账户请求。
- OKXQUANT_AUTOTRADE_ENABLED=0：Gateway 每次 tick 重读配置，不再安排新的 trader 作业。
- 两项省略时默认 1；只有字面值 1 才启用。OKXQUANT_TESTING=1 仅用于隔离测试，不用于真实部署。

## 本地网络

WSL NAT 不能直接访问 Windows 的 localhost 代理。本次使用仅监听 WSL 主机虚拟网卡地址、仅接受当前 WSL 客户端的转发，将请求送到 Windows 原有 127.0.0.1:7890 代理。未关闭 TLS 验证，未改变 OKX 主机白名单，未向公网开放代理。

Windows 代理关闭或 WSL 重启换 IP 后，旧转发会失效。先恢复可用网络并验证模拟盘只读接口，再 resume；不要用绕过风控或反复下单验证网络。

## 验收命令（WSL）

```bash
cd /mnt/d/wangkai/workspace/OKXQuant
/opt/okxquant-venv/bin/python -m pip check
/opt/okxquant-venv/bin/python scripts/run_tests.py --verbose

# 在 Linux 副本内安装/构建，不能与 Windows 共用 node_modules
cd /opt/okxquant-local/app/okxquant_frontend
npm ci
npm run test:unit
npm run build
```

运行副本 origin 指向本机源码仓库。拉取本地已提交代码必须使用 okxquantlocal 用户，并在更新/重启前确认没有正在执行的交易周期；不推送远端。构建资源由 docs/images 生成，不依赖 Windows Git 软链接权限。

## 模拟盘执行诊断

scripts/demo_execution_smoke.py 需要明确的 DEMO EXECUTION TEST 确认，只接受 demo 静态凭据、允许的标的及 1–100 USDT 名义金额。它是**手工授权的执行诊断，不是 AI 信号或生产 entry_gateway 通过证明**，不会伪造模型决策证据。

诊断使用真实风险/费用预算、原生附带 TP/SL 的限价单、显式附属算法 client ID、读回确认、生产平仓路径。目标标的必须原本无仓位、无挂单、无算法单。只清理与本次订单明确匹配的仓位；未知写入不盲重试，cleanup_required 时必须人工核对，不能直接重跑。

本地验收风险配置为单笔权益风险 0.04%、单标的保证金上限 20 USDT、实际杠杆最高 3 倍；其余政策保留默认值。这是本地验收限制，不是推荐的实盘风险配置。

## 备份与恢复

新归档强制排除密钥、认证文件及内联 provider Key 配置。修复前生成的旧归档已移出下载目录，保存在私有 quarantine/pre-fix-backups；未外传。

恢复仅面向管理员信任的归档。先完整验证和暂存，再逐文件原子替换；拒绝路径穿越、链接、设备、重复冲突及超限内容。运行中的 Gateway/交易任务或未清理的数据库 WAL 会阻止恢复。全目录磁盘故障回滚不在承诺范围内。

本次真实归档恢复在临时目录验证，没有覆盖运行中的账户数据。

## 实盘前边界

- 模拟盘成功不等于实盘滑点、流动性、权限和保护行为已验证；没有收益或零 bug 保证。
- 不放宽默认 ADX/宏观/净盈亏比门禁来制造成交。
- 新闻需要独立授权；未配置时明确未知，不能把 demo Key 当作普通市场资讯授权。
- 云端通知与远程备份目标需各自凭据，未配置的渠道没有做真实发送/上传验收。
- 策略回测、影子研究、长期记忆候选不自动晋级生产；盈利优势需另行前向与样本外验证。
- 切 live 前需要专门的账户、只读核验、资金限额和小额保护/退出演练；本次没有配置或调用 live 交易凭据。
