# OKXQuant Standalone Deployment v0.1.0

This document describes the Linux/WSL standalone deployment of OKXQuant.

## Services

- okxquant_backend: FastAPI control plane and read-only monitoring API.
- okxquant_gateway: single-owner scheduler and notification delivery worker.
- OKX official CLI: read-only setup and exchange integration preflight.

## Local launch

```bash
python -m uvicorn okxquant_backend.app:app --host 0.0.0.0 --port 8080
python -m okxquant_gateway.worker
```

## Docker launch

```bash
docker compose build app
docker compose up -d app
docker compose ps
```

The service exposes the dashboard at `/`, admin at `/admin/`, and API documentation at `/api/docs`.

## Safety

Keep demo and live credentials separate. Never copy OAuth state or secret volumes between installations. Verify the exact service identity, HOME, PATH, account environment, and OKX CLI readiness before enabling the gateway.

## Testing

Use WSL/Linux and the isolated test runner:

```bash
python scripts/run_tests.py --verbose
```
