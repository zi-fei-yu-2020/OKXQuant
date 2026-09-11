"""Allowlisted, read-only OKX resources for dashboards and diagnostics."""
from __future__ import annotations
from typing import Any
from scripts.okx_runtime import OKXEnvironment
from .okx_trade_service import _request
from scripts.algo_reader import read_algo_orders, orders_for_instrument

RESOURCES = {
    "balance": ("/api/v5/account/balance", {}),
    "positions": ("/api/v5/account/positions", {"instType": "SWAP"}),
    "orders": ("/api/v5/trade/orders-pending", {"instType": "SWAP"}),
    "bills": ("/api/v5/account/bills", {"limit": "100"}),
}


def read_private_resource(resource: str, environment: OKXEnvironment, inst_id: str = "") -> list[dict[str, Any]]:
    """Keep one immutable credential/environment snapshot across a cache cycle.

    No trading methods or arbitrary URLs are accepted, and no LIVE fallback is
    attempted when a DEMO request fails. OAuth-only callers retain their CLI path.
    """
    if resource == "algos":
        if inst_id and not inst_id.endswith("-SWAP"):
            raise ValueError("只读保护单查询需要 SWAP 标的")
        rows = read_algo_orders(environment, priority="monitor")
        return orders_for_instrument(rows, inst_id) if inst_id else rows
    if not environment.configured:
        raise ValueError("当前环境的 API Key 未配置")
    if resource not in RESOURCES:
        raise ValueError("不支持的账户只读资源")
    path, params = RESOURCES[resource]
    return _request("GET", path, dict(params), environment, timeout=10)
