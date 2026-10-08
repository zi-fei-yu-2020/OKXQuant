# OKXQuant v0.1.0

OKXQuant is an AI-assisted quantitative trading system for OKX USDT perpetual swaps. It combines closed-candle market data, deterministic candidates, LLM evidence review, account risk controls, exchange-native OCO protection, lifecycle accounting, and strategy telemetry.

Repository: https://github.com/zi-fei-yu-2020/OKXQuant.git

## Components

- okxquant_backend/: FastAPI control plane, accounts, LLM providers, admin APIs, and dashboard mounting.
- okxquant_gateway/: single-owner scheduler, notification delivery, and job supervision.
- okxquant_frontend/: Vue 3 monitoring console and admin UI.
- scripts/: market collection, candidates, AI brain, risk, execution, position protection, ledger, and replay.
- plugins/: built-in deterministic interceptors.

## Trading pipeline

market data -> closed-candle candidate -> AI evidence review -> account/portfolio risk -> Entry Gateway -> limit + OCO -> position guard -> ledger and strategy telemetry

The model is not an order executor. Final size, leverage, margin, stop, take-profit, protection coverage, idempotency, and exchange writes are controlled by deterministic code.

## Execution profiles

- standard: regular account risk policy.
- small300: adaptive 300 USDT policy. Position size is derived from actual stop distance and risk budget; leverage and margin are ceilings, not fixed order targets.

The system distinguishes scalp and swing horizons. Daily drawdown protection stops new entries after the configured threshold while existing position protection remains active.

## Local development

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python scripts/init_env.py
python -m uvicorn okxquant_backend.app:app --host 0.0.0.0 --port 8080
```

Frontend:

```bash
cd okxquant_frontend
npm ci
npm run typecheck
npm run test:unit
npm run build
```

## First-run workflow and lightweight operation

1. Run `python scripts/init_env.py` once. It refuses to overwrite an existing `.env`, creates a unique temporary administrator password in that private file, and starts new installations in DEMO, light profile, with automatic entries paused.
2. Build and start the combined backend with Docker Compose below. Use the setup token from `.env` for the initial `admin` login; change the password after login. Never put credentials into Git, URLs, screenshots or support reports.
3. In **Account & trading**, connect and verify the intended DEMO account, instrument pool and capital baseline. In **Strategy & risk**, review the actual limits. Model setup is separate from account authorization. AI-assisted decisions require a configured model provider; read-only observation does not. The minute engine retains its separate opt-in/consent contract.
4. Review the overview readiness indicators. A healthy API is not proof of exchange connectivity, trading permission or protection coverage. Explicitly enable future automatic cycles from the overview only after validation.
5. Explore `/decisions`, `/market-intelligence`, `/reviews`, and `/trades`. Old front URLs redirect. Monitoring financial data and audit details require a valid session; market candles and documentation stay public.

The seven primary console sections are overview, account & trading, strategy & risk, model services, runtime & logs, notifications & backups, and system settings. Advanced capabilities remain under contextual subnavigation rather than seventeen always-visible primary entries.

`light` disables optional factor snapshots, market-observation collection, entry-opportunity research, scalp research, and scheduled AI reviews. It does **not** disable position protection, risk checks, news risk input, ledger/evidence collection or existing published strategy memory. A manual review remains available. Saved `data/runtime_features.json` settings override bootstrap environment defaults. Missing settings in an existing deployment preserve standard behavior; unreadable feature settings disable optional work and report an error rather than silently enabling it.

Pausing automatic entries affects **future scheduled cycles only**; it does not cancel exchange orders, interrupt an already-running inference, or stop position protection. LIVE minute consent remains separate. Upgrading never silently changes an existing user's environment, risk parameters or entry switch.

The supported authenticated server entry point is `okxquant_backend.app:app`. The old `python dashboard/app.py` launcher delegates to it; do not launch the dashboard-only ASGI object independently because it is the mounted monitoring component, not a second control plane.

## Docker

```bash
docker compose build app
docker compose up -d app
docker compose ps
```

The Compose project, service, image, and persistent volumes use the okxquant namespace.

## Testing

Backend tests require Linux/WSL because the runtime uses POSIX file locks:

```bash
python scripts/run_tests.py --verbose
```

Do not run tests in a live deployment directory. The test runner creates a disposable source snapshot and strips credentials and runtime data.

## Version

Current release: v0.1.0.

## License

MIT. This project is for research and simulated trading validation. It is not investment advice.


## 2026-09 全链路审查与策略切换

审查范围、修复闭环、standard/small300 的真实有效参数、LIVE 分钟策略独立授权与验收限制，见 `docs/audit-2026-09-20-closure.md`。
新增规则使用有限纯数据规则语言，不执行任意上传的 Python。管理员保存/启用策略不等于已经成交；新决策读取最新配置，已有仓位保持原生命周期保护。
