"""Public read-only display allowlist; raw diagnostics and control APIs remain private."""
import re
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

PRIVATE_EXACT = frozenset({"/api/all", "/api/overview", "/api/trades", "/api/ai/last-prompt", "/api/ai/history", "/api/v1/status"})
PRIVATE_PREFIXES = ("/api/trades/", "/api/ai/", "/api/v1/cache/", "/api/v1/account/")


def private_monitor_path(path):
    path=path.rstrip("/") or "/"
    return path in PRIVATE_EXACT or any(path.startswith(prefix) for prefix in PRIVATE_PREFIXES)


PUBLIC_READS = frozenset({"/api/all", "/api/overview", "/api/trades", "/api/ai/history"})


def public_monitor_path(path):
    path = path.rstrip("/") or "/"
    return path in PUBLIC_READS or bool(re.fullmatch(r"/api/(?:trades|ai/history)/[^/]+", path))


async def protect_monitor_data(request: Request, call_next):
    public_read = request.method in {"GET", "HEAD"} and public_monitor_path(request.url.path)
    private=private_monitor_path(request.url.path) and not public_read
    if private and request.method != "OPTIONS" and not getattr(request.state,"monitor_user",None):
        # Lazy import avoids an application/mounted-router import cycle. Both
        # entry points validate against the same live session/user store.
        from okxquant_backend.app import require_admin_header
        try:
            request.state.monitor_user=require_admin_header(
                request.headers.get("X-OKXQuant-Admin-Token"),request.headers.get("X-OKXQuant-Session"))
        except HTTPException as exc:
            return JSONResponse({"detail":exc.detail},status_code=exc.status_code,
                headers={"Cache-Control":"private, no-store"})
    response=await call_next(request)
    if private:
        response.headers["Cache-Control"]="private, no-cache, no-store, must-revalidate"
        vary=response.headers.get("Vary","")
        if "X-OKXQuant-Session".lower() not in vary.lower():
            response.headers["Vary"]=(vary+", " if vary else "")+"X-OKXQuant-Session"
    if public_read:
        response.headers["Cache-Control"]="no-cache, no-store, must-revalidate"
    return response
