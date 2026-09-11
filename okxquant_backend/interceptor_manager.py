"""
OKXQuant Quantitative Trading System - Interceptor Plugins Manager
============================================================
Manages physical risk interceptor plugins written in Python.
Allows dynamic loading, enabling/disabling, reordering, hot-editing,
and pipeline execution for both built-in and user-crafted rule scripts.
"""

from __future__ import annotations

import ast
import copy
import importlib.util
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("okxquant_interceptors")

ROOT_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT_DIR / "plugins" / "interceptors"
CONFIG_FILE = ROOT_DIR / "data" / "interceptor_plugins.json"

DEFAULT_ORDER = [
    "01_macro_trend_filter.py",
    "02_confidence_gatekeeper.py",
    "03_adx_volatility_filter.py",
    "04_risk_reward_gatekeeper.py",
    "99_custom_template_sample.py",
]

DEFAULT_ENABLED = {
    "01_macro_trend_filter.py": True,
    "02_confidence_gatekeeper.py": True,
    "03_adx_volatility_filter.py": True,
    "04_risk_reward_gatekeeper.py": True,
    "99_custom_template_sample.py": False,
}


def ensure_plugins_dir() -> None:
    PLUGINS_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)


def load_config() -> dict[str, Any]:
    ensure_plugins_dir()
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception as e:
            logger.warning("Failed to load interceptor config, using default: %s", e)
    return {
        "pipeline_order": copy.deepcopy(DEFAULT_ORDER),
        "enabled": copy.deepcopy(DEFAULT_ENABLED),
    }


def save_config(config: dict[str, Any]) -> None:
    ensure_plugins_dir()
    tmp = CONFIG_FILE.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    os.replace(tmp, CONFIG_FILE)


def parse_plugin_metadata(file_path: Path) -> dict[str, Any]:
    filename = file_path.name
    res = {
        "filename": filename,
        "id": filename.replace(".py", ""),
        "name": filename,
        "version": "1.0.0",
        "author": "Custom",
        "description": "",
        "tags": [],
        "enabled": True,
        "size_bytes": 0,
        "updated_at": 0,
        "valid_syntax": True,
        "error": "",
    }
    if not file_path.exists():
        return res

    try:
        content = file_path.read_text(encoding="utf-8")
        res["size_bytes"] = len(content.encode("utf-8"))
        res["updated_at"] = int(file_path.stat().st_mtime)

        # Check Python syntax
        try:
            ast.parse(content)
        except SyntaxError as syn_err:
            res["valid_syntax"] = False
            res["error"] = f"语法错误 (Line {syn_err.lineno}): {syn_err.msg}"

        # Parse docstring header metadata
        match = re.search(r'"""(.*?)"""', content, re.DOTALL)
        if match:
            doc = match.group(1)
            for line in doc.splitlines():
                line = line.strip()
                if line.startswith("id:"):
                    res["id"] = line.split(":", 1)[1].strip()
                elif line.startswith("name:"):
                    res["name"] = line.split(":", 1)[1].strip()
                elif line.startswith("version:"):
                    res["version"] = line.split(":", 1)[1].strip()
                elif line.startswith("author:"):
                    res["author"] = line.split(":", 1)[1].strip()
                elif line.startswith("description:"):
                    res["description"] = line.split(":", 1)[1].strip()
                elif line.startswith("tags:"):
                    raw_tags = line.split(":", 1)[1].strip()
                    res["tags"] = [t.strip() for t in raw_tags.split(",") if t.strip()]
    except Exception as e:
        res["error"] = str(e)

    return res


def list_plugins() -> list[dict[str, Any]]:
    ensure_plugins_dir()
    config = load_config()
    enabled_map = config.get("enabled", {})
    pipeline_order = config.get("pipeline_order", [])

    all_files = sorted([f.name for f in PLUGINS_DIR.glob("*.py")])
    # Build complete order list
    known_order = [f for f in pipeline_order if f in all_files]
    new_files = [f for f in all_files if f not in known_order]
    full_order = known_order + new_files

    result = []
    for filename in full_order:
        file_path = PLUGINS_DIR / filename
        meta = parse_plugin_metadata(file_path)
        meta["enabled"] = bool(enabled_map.get(filename, True if filename != "99_custom_template_sample.py" else False))
        result.append(meta)

    return result


def get_plugin_detail(filename: str) -> dict[str, Any]:
    ensure_plugins_dir()
    if not filename.endswith(".py") or "/" in filename or "\\" in filename or ".." in filename:
        raise FileNotFoundError(f"无效的插件文件名: {filename}")
    file_path = (PLUGINS_DIR / filename).resolve()
    if not file_path.is_relative_to(PLUGINS_DIR.resolve()) or not file_path.exists():
        raise FileNotFoundError(f"插件不存在: {filename}")

    meta = parse_plugin_metadata(file_path)
    config = load_config()
    meta["enabled"] = bool(config.get("enabled", {}).get(filename, True))
    meta["code"] = file_path.read_text(encoding="utf-8")
    return meta


def save_plugin_code(filename: str, code: str) -> dict[str, Any]:
    ensure_plugins_dir()
    if not filename.endswith(".py") or "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError("无效的插件文件名")

    # Validate Python syntax before saving
    try:
        ast.parse(code)
    except SyntaxError as err:
        raise ValueError(f"代码语法校验失败 (第 {err.lineno} 行): {err.msg}") from err

    file_path = (PLUGINS_DIR / filename).resolve()
    if not file_path.is_relative_to(PLUGINS_DIR.resolve()):
        raise ValueError("插件文件路径越界")
    tmp_path = file_path.with_suffix(".tmp")
    tmp_path.write_text(code, encoding="utf-8")
    os.replace(tmp_path, file_path)

    return get_plugin_detail(filename)


def toggle_plugin(filename: str, enabled: bool) -> dict[str, Any]:
    config = load_config()
    config.setdefault("enabled", {})[filename] = bool(enabled)
    save_config(config)
    return {"filename": filename, "enabled": bool(enabled)}


def reorder_plugins(new_order: list[str]) -> list[dict[str, Any]]:
    config = load_config()
    all_files = {f.name for f in PLUGINS_DIR.glob("*.py")}
    valid_order = [f for f in new_order if f in all_files]
    # Append any remaining files
    for f in sorted(all_files):
        if f not in valid_order:
            valid_order.append(f)

    config["pipeline_order"] = valid_order
    save_config(config)
    return list_plugins()


def create_plugin(filename: str, code: str) -> dict[str, Any]:
    ensure_plugins_dir()
    if not filename.endswith(".py"):
        filename = f"{filename}.py"
    if "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError("无效的文件名")

    file_path = (PLUGINS_DIR / filename).resolve()
    if not file_path.is_relative_to(PLUGINS_DIR.resolve()):
        raise ValueError("插件文件路径越界")
    if file_path.exists():
        raise FileExistsError(f"插件文件已存在: {filename}")

    # Syntax check
    try:
        ast.parse(code)
    except SyntaxError as err:
        raise ValueError(f"代码语法校验失败 (第 {err.lineno} 行): {err.msg}") from err

    file_path.write_text(code, encoding="utf-8")

    # Register in config
    config = load_config()
    if filename not in config.get("pipeline_order", []):
        config.setdefault("pipeline_order", []).append(filename)
    config.setdefault("enabled", {})[filename] = True
    save_config(config)

    return get_plugin_detail(filename)


def delete_plugin(filename: str) -> bool:
    ensure_plugins_dir()
    if not filename.endswith(".py") or "/" in filename or "\\" in filename or ".." in filename:
        raise ValueError("无效的文件名")

    file_path = (PLUGINS_DIR / filename).resolve()
    if not file_path.is_relative_to(PLUGINS_DIR.resolve()):
        raise ValueError("插件文件路径越界")
    if not file_path.exists():
        raise FileNotFoundError(f"插件不存在: {filename}")

    file_path.unlink()

    config = load_config()
    if filename in config.get("pipeline_order", []):
        config["pipeline_order"].remove(filename)
    if filename in config.get("enabled", {}):
        del config["enabled"][filename]
    save_config(config)

    return True


def _load_module_from_file(file_path: Path) -> Any:
    """Dynamically load python module from file path."""
    module_name = f"okxquant_plugin_{file_path.stem}_{int(file_path.stat().st_mtime)}"
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load module spec from {file_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def run_interceptor_pipeline(package: dict[str, Any], decision: dict[str, Any], context: dict[str, Any]) -> tuple[str, str, float]:
    """
    Executes all enabled interceptor plugins in sequence.
    Returns: (final_action, rejection_reason, risk_reward_ratio)
    - If all enabled plugins pass: returns (raw_action, "", rr)
    - If any plugin rejects: returns ("WAIT", rejection_reason, rr)
    """
    inst_id = package.get("instId", "")
    raw_action = str(decision.get("action", "WAIT")).upper()
    if raw_action not in {"BUY_LONG", "SELL_SHORT", "WAIT"}:
        raw_action = "WAIT"

    entry = float(decision.get("entry_price", 0) or 0)
    tp = float(decision.get("take_profit_price", 0) or 0)
    sl = float(decision.get("stop_loss_price", 0) or 0)

    rr = 0.0
    if raw_action == "BUY_LONG" and entry > sl > 0 and tp > entry:
        rr = (tp - entry) / (entry - sl)
    elif raw_action == "SELL_SHORT" and sl > entry > tp > 0:
        rr = (entry - tp) / (sl - entry)

    # If already WAIT, return immediately
    if raw_action == "WAIT":
        return "WAIT", "", rr

    # 1. Base Core Pre-check: Data Completeness & Direction Collisions
    if package.get("data_quality") != "valid":
        return "WAIT", "关键原始行情不完整，安全降级为 WAIT。", rr

    active_inst_ids = context.get("active_inst_ids", set())
    active_position_sides = context.get("active_position_sides", {})
    if inst_id in active_inst_ids:
        pos_side = active_position_sides.get(inst_id, "")
        is_same = (pos_side == "long" and raw_action == "BUY_LONG") or (pos_side == "short" and raw_action == "SELL_SHORT")
        if not is_same:
            return "WAIT", "已有反向或不兼容持仓，禁止借决策通道反向开仓，安全降级为 WAIT。", rr

    # 2. Pipeline Execution across all enabled plugins
    plugins = list_plugins()
    for p_info in plugins:
        if not p_info.get("enabled"):
            continue

        filename = p_info["filename"]
        file_path = PLUGINS_DIR / filename
        if not file_path.exists():
            logger.error("Enabled interceptor plugin is missing: %s", filename)
            return "WAIT", f"启用的风控插件 [{filename}] 不存在，拒绝开仓", rr

        try:
            mod = _load_module_from_file(file_path)
            if not hasattr(mod, "check_risk"):
                logger.error("Enabled interceptor plugin %s missing check_risk", filename)
                return "WAIT", f"启用的风控插件 [{filename}] 缺少 check_risk，拒绝开仓", rr

            passed, reason = mod.check_risk(package, decision, context)
            if not passed:
                # Interception triggered!
                return "WAIT", str(reason or f"触发风控拦截插件 [{p_info.get('name', filename)}] 规则"), rr
        except Exception as e:
            logger.error("Error executing interceptor plugin %s: %s", filename, e)
            # Fail-closed or warn
            return "WAIT", f"风控插件 [{p_info.get('name', filename)}] 运行异常: {e}，安全降级为 WAIT", rr

    return raw_action, "", rr


def run_sandbox_test(custom_scenario: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Run a sandbox test of all enabled interceptors against standard mock scenarios."""
    plugins = list_plugins()
    scenarios = [
        {
            "name": "场景 1: 4H 多头通道中尝试逆势做空 (BTC)",
            "package": {
                "name": "BTC",
                "instId": "BTC-USDT-SWAP",
                "macro_4h": "4H_MACRO_BULL (大级别多头通道)",
                "adx_1h": 25.0,
                "data_quality": "valid",
            },
            "decision": {
                "action": "SELL_SHORT",
                "confidence": 85.0,
                "entry_price": 80000.0,
                "take_profit_price": 76000.0,
                "stop_loss_price": 81500.0,
            },
            "context": {"active_inst_ids": set(), "active_position_sides": {}},
        },
        {
            "name": "场景 2: 1H ADX 仅 14 的无序震荡市尝试做多 (ETH)",
            "package": {
                "name": "ETH",
                "instId": "ETH-USDT-SWAP",
                "macro_4h": "4H_MACRO_BULL",
                "adx_1h": 14.2,
                "data_quality": "valid",
            },
            "decision": {
                "action": "BUY_LONG",
                "confidence": 88.0,
                "entry_price": 2400.0,
                "take_profit_price": 2600.0,
                "stop_loss_price": 2320.0,
            },
            "context": {"active_inst_ids": set(), "active_position_sides": {}},
        },
        {
            "name": "场景 3: 置信度仅 75% 的低确定性开多 (SOL)",
            "package": {
                "name": "SOL",
                "instId": "SOL-USDT-SWAP",
                "macro_4h": "4H_MACRO_BULL",
                "adx_1h": 28.5,
                "data_quality": "valid",
            },
            "decision": {
                "action": "BUY_LONG",
                "confidence": 75.0,
                "entry_price": 100.0,
                "take_profit_price": 120.0,
                "stop_loss_price": 92.0,
            },
            "context": {"active_inst_ids": set(), "active_position_sides": {}},
        },
        {
            "name": "场景 4: 完美顺势、高置信度 (85%)、真实 2.5R 优质做多单 (SUI)",
            "package": {
                "name": "SUI",
                "instId": "SUI-USDT-SWAP",
                "macro_4h": "4H_MACRO_BULL",
                "adx_1h": 32.0,
                "data_quality": "valid",
            },
            "decision": {
                "action": "BUY_LONG",
                "confidence": 85.0,
                "entry_price": 0.80,
                "take_profit_price": 0.95,
                "stop_loss_price": 0.74,
            },
            "context": {"active_inst_ids": set(), "active_position_sides": {}},
        },
    ]

    if custom_scenario:
        scenarios.append(custom_scenario)

    test_results = []
    total_start = time.time()

    for sc in scenarios:
        t0 = time.time()
        final_action, reason, rr = run_interceptor_pipeline(sc["package"], sc["decision"], sc["context"])
        dur_ms = round((time.time() - t0) * 1000, 2)
        test_results.append({
            "scenario": sc["name"],
            "raw_action": sc["decision"]["action"],
            "final_action": final_action,
            "intercepted": final_action == "WAIT" and sc["decision"]["action"] != "WAIT",
            "reason": reason,
            "risk_reward": f"{rr:.2f}R" if rr > 0 else "--",
            "duration_ms": dur_ms,
        })

    return {
        "status": "success",
        "total_plugins_count": len(plugins),
        "enabled_plugins_count": len([p for p in plugins if p.get("enabled")]),
        "duration_total_ms": round((time.time() - total_start) * 1000, 2),
        "results": test_results,
    }
