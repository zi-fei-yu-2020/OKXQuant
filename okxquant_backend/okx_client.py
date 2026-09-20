"""Small native OKX REST client; public endpoints work without credentials."""
from __future__ import annotations
from typing import Any
from urllib.parse import urlencode
from .config import settings
from scripts.public_market import get_json as public_json


class OKXClient:
    def __init__(self) -> None:
        self.base_url = settings.okx_base_url.rstrip("/")

    def _send_once(self, selected, method: str, path: str, params: dict[str, Any] | None = None) -> Any:
        # One signed transport contract: binding checks, bounded GET retries,
        # per-item business errors, and no implicit repeat for any write.
        from .okx_trade_service import _request_untracked
        return _request_untracked(method, path, params, selected, timeout=10)

    def _request(self, method, path, params=None):
        from scripts.okx_runtime import selected_environment
        from scripts.algo_reader import algo_mutation
        from okxquant_backend.account_connections import assert_current
        selected = selected_environment()
        assert_current(selected)
        if method.upper() != "GET" and path.startswith("/api/v5/trade/"):
            from scripts.trade_lock import writer
            with writer(), algo_mutation(selected):
                return self._send_once(selected, method, path, params)
        return self._send_once(selected, method, path, params)

    def ticker(self, inst_id: str) -> Any:
        return public_json(f"{self.base_url}/api/v5/market/ticker?" + urlencode({"instId": inst_id}), simulated=settings.okx_simulated)["data"]

    def candles(self, inst_id: str, bar: str = "1H", limit: int = 100) -> Any:
        return public_json(f"{self.base_url}/api/v5/market/candles?" + urlencode({"instId": inst_id, "bar": bar, "limit": limit}), simulated=settings.okx_simulated)["data"]

    def instruments(self, inst_type: str = "SWAP", inst_id: str | None = None) -> Any:
        params = {"instType": inst_type}
        if inst_id:
            params["instId"] = inst_id
        return public_json(f"{self.base_url}/api/v5/public/instruments?" + urlencode(params), simulated=settings.okx_simulated)["data"]

    def balance(self) -> Any:
        return self._request("GET", "/api/v5/account/balance")

    def positions(self) -> Any:
        return self._request("GET", "/api/v5/account/positions", {"instType": "SWAP"})

    def close_position(self, inst_id: str, pos_side: str) -> Any:
        if pos_side not in {"long", "short"}:
            raise ValueError("pos_side must be long or short")
        return self._request(
            "POST",
            "/api/v5/trade/close-position",
            {"instId": inst_id, "mgnMode": "cross", "posSide": pos_side, "autoCxl": "true"},
        )
