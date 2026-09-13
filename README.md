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
cp env.example .env
python -m uvicorn okxquant_backend.app:app --host 0.0.0.0 --port 8080
python -m okxquant_gateway.worker
```

Frontend:

```bash
cd okxquant_frontend
npm ci
npm run typecheck
npm run test:unit
npm run build
```

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
