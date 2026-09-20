"""Manage bounded pure-data interceptor rules, not executable Python modules.

Storage/API contract: filenames, order, enable flags and function signatures stay
stable. plugins/interceptors is the read-only legacy/source layer; administrator
edits/new rules live in data/interceptors and shadow matching source filenames.
Deleted source rules are config tombstones. No source-directory write is needed.
"""
from __future__ import annotations
import copy
import json
import logging
import os
import re
import stat
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from scripts.config_lock import configuration_write
from scripts.rule_interpreter import (
    ENGINE, MAX_SOURCE_BYTES, PLAN_KEY, Budget, RuleError, RuleDataRequired,
    copy_data, validate_rule,
)

logger = logging.getLogger("okxquant_interceptors")
ROOT_DIR = Path(__file__).resolve().parent.parent
PLUGINS_DIR = ROOT_DIR / "plugins" / "interceptors"
CUSTOM_PLUGINS_DIR = ROOT_DIR / "data" / "interceptors"
CONFIG_FILE = ROOT_DIR / "data" / "interceptor_plugins.json"
MAX_PLUGINS = 64
MAX_CONFIG_BYTES = 5_000_000  # bounded 64-rule transactional source manifest
DEFAULT_ORDER = ["01_macro_trend_filter.py", "02_confidence_gatekeeper.py",
                 "03_adx_volatility_filter.py", "04_risk_reward_gatekeeper.py",
                 "99_custom_template_sample.py"]
DEFAULT_ENABLED = {filename: filename != "99_custom_template_sample.py" for filename in DEFAULT_ORDER}


def _filename(filename):
    # Retain safe legacy names (Unicode, dots, spaces and underscores), while
    # excluding traversal, Windows ADS/device paths, controls and separators.
    if (type(filename) is not str or not filename.endswith(".py") or len(filename) <= 3
            or len(filename) > 128 or ".." in filename
            or re.search(r'[\x00-\x1f<>:"/\\|?*]', filename)):
        raise ValueError("Invalid rule filename/path")
    stem=filename.split(".",1)[0].upper()
    if stem in {"CON","PRN","AUX","NUL"} | {f"COM{i}" for i in range(1,10)} | {f"LPT{i}" for i in range(1,10)}:
        raise ValueError("Reserved device filename")
    return filename


def ensure_plugins_dir():
    # Never mkdir/chmod/write the read-only image's source plugin directory.
    if CUSTOM_PLUGINS_DIR.is_symlink() or CONFIG_FILE.is_symlink():
        raise ValueError("Symlink rule storage/config is not permitted")
    CUSTOM_PLUGINS_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)


def _path(directory, filename):
    filename = _filename(filename)
    path = directory / filename
    if directory.is_symlink() or path.is_symlink() or path.resolve().parent != directory.resolve():
        raise ValueError("Rule path is a symlink or outside its storage directory")
    return path


def _read_bounded(path, limit):
    if path.is_symlink(): raise ValueError("Symlink files are not permitted")
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(fd, "rb") as handle:
        info = os.fstat(handle.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > limit:
            raise ValueError("Rule/config must be a bounded regular file")
        data = handle.read(limit + 1)
    if len(data) > limit: raise ValueError("Rule/config file exceeds its size budget")
    return data.decode("utf-8-sig")


def _atomic_text(path, content):
    if path.is_symlink(): raise ValueError("Symlink targets are not permitted")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(prefix=".rule-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content); handle.flush(); os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary): os.unlink(temporary)


def _check_config(config):
    if type(config) is not dict: raise ValueError("Invalid interceptor config object")
    order=config.get("pipeline_order", []); enabled=config.get("enabled", {}); deleted=config.get("deleted", [])
    if type(order) is not list or type(enabled) is not dict or type(deleted) is not list:
        raise ValueError("Invalid interceptor config structure")
    if max(len(order), len(enabled), len(deleted)) > MAX_PLUGINS:
        raise ValueError("Interceptor configuration count limit exceeded")
    for name in list(order)+list(enabled)+list(deleted): _filename(name)
    if any(type(v) not in (bool, int) or v not in (True,False,0,1) for v in enabled.values()):
        raise ValueError("Enabled values must be booleans")
    sources=config.get('sources',{})
    if type(sources) is not dict or len(sources)>MAX_PLUGINS:raise ValueError('Invalid rule source manifest')
    for name,source in sources.items():
        _filename(name)
        if type(source) is not str or len(source.encode('utf8'))>MAX_SOURCE_BYTES:
            raise ValueError('Rule source exceeds manifest budget')
    return config


def load_config():
    ensure_plugins_dir()
    if CONFIG_FILE.exists():
        try: return _check_config(json.loads(_read_bounded(CONFIG_FILE, MAX_CONFIG_BYTES)))
        except (OSError, ValueError, UnicodeError, RecursionError):
            raise ValueError("Interceptor configuration is unreadable; it was not reset") from None
    return {"pipeline_order": copy.deepcopy(DEFAULT_ORDER), "enabled": copy.deepcopy(DEFAULT_ENABLED)}


def save_config(config):
    ensure_plugins_dir()
    with configuration_write(CONFIG_FILE):
        _check_config(config)
        content=json.dumps(config, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
        if len(content.encode("utf-8"))>MAX_CONFIG_BYTES: raise ValueError("Interceptor config is too large")
        _atomic_text(CONFIG_FILE,content)


def _available(config):
    files={}
    for directory in (PLUGINS_DIR, CUSTOM_PLUGINS_DIR):
        if directory.is_symlink(): raise ValueError("Symlink plugin directory is not permitted")
        if not directory.exists(): continue
        count=0
        with os.scandir(directory) as entries:
            for entry in entries:
                count+=1
                if count>MAX_PLUGINS*4: raise ValueError("Rule directory entry budget exceeded")
                if entry.name.endswith(".py"):
                    _filename(entry.name);files[entry.name]=directory / entry.name
                    if len(files)>MAX_PLUGINS: raise ValueError("Too many interceptor rules")
    for name in config.get('sources',{}): files[name]=_path(CUSTOM_PLUGINS_DIR,name)
    for name in config.get("deleted",[]): files.pop(name,None)
    return files


def _resolve(filename, config=None):
    _filename(filename);config=load_config() if config is None else config
    if filename in config.get("deleted",[]): raise FileNotFoundError("Rule was deleted")
    if filename in config.get('sources',{}):return _path(CUSTOM_PLUGINS_DIR,filename)
    for directory in (CUSTOM_PLUGINS_DIR, PLUGINS_DIR):
        path=_path(directory,filename)
        if path.exists(): return path
    raise FileNotFoundError("Rule does not exist: "+filename)


def _source_text(path, config=None):
    config=load_config() if config is None else config
    if path.parent==CUSTOM_PLUGINS_DIR and path.name in config.get('sources',{}):
        return config['sources'][path.name]
    return _read_bounded(_path(path.parent,path.name),MAX_SOURCE_BYTES)


def _mirror_committed(filename, source):
    # Compatibility/editor file only. The atomic config manifest is authoritative.
    # A failed mirror cannot roll back an already committed operation or activate
    # an uncommitted orphan. Retrying a request therefore never replays effects.
    try:_atomic_text(_path(CUSTOM_PLUGINS_DIR,filename),source)
    except OSError:logger.warning('Committed rule mirror unavailable; using source manifest')


def _program(path):
    if path.parent not in (PLUGINS_DIR, CUSTOM_PLUGINS_DIR): raise ValueError("Unknown rule storage directory")
    return validate_rule(_source_text(path),migrate_legacy=True)


def parse_plugin_metadata(file_path):
    filename=file_path.name
    result={"filename":filename,"id":filename[:-3],"name":filename,"version":"1.0.0","author":"Custom",
            "description":"","tags":[],"enabled":True,"size_bytes":0,"updated_at":0,
            "valid_syntax":True,"supported_rule":True,"error":"","execution_engine":ENGINE,
            "storage":"custom" if file_path.parent==CUSTOM_PLUGINS_DIR else "source"}
    try:
        path=_path(file_path.parent,filename)
        if path.parent not in (PLUGINS_DIR,CUSTOM_PLUGINS_DIR): raise ValueError("Unknown rule storage directory")
        content=_source_text(path)
        result["size_bytes"]=len(content.encode("utf-8"));result["updated_at"]=int(CONFIG_FILE.stat().st_mtime if path.parent==CUSTOM_PLUGINS_DIR and path.name in load_config().get('sources',{}) else path.stat().st_mtime)
        program=validate_rule(content,migrate_legacy=True)
        result["compatibility_migration"]=program.source!=content
        for line in program.docstring.splitlines():
            key,sep,value=line.strip().partition(":")
            if sep and key in {"id","name","version","author","description","tags"}:
                result[key]=[t.strip() for t in value.split(",") if t.strip()] if key=="tags" else value.strip()
    except (OSError,ValueError,UnicodeError):
        result.update(valid_syntax=False,supported_rule=False,error="Rule source is missing, unsafe or outside the supported bounded subset")
    return result


def list_plugins():
    ensure_plugins_dir();config=load_config();files=_available(config)
    order=list(dict.fromkeys(f for f in config.get("pipeline_order",[]) if f in files))
    order.extend(f for f in sorted(files) if f not in order)
    # Explicitly enabled registered rules must not disappear silently if a file is lost.
    missing=[f for f,v in config.get("enabled",{}).items() if v and f not in files and f not in config.get("deleted",[])]
    order.extend(f for f in missing if f not in order)
    if len(order)>MAX_PLUGINS: raise ValueError("Too many interceptor rules")
    result=[]
    for filename in order:
        meta=parse_plugin_metadata(files.get(filename,PLUGINS_DIR/filename))
        meta["enabled"]=bool(config.get("enabled",{}).get(filename,DEFAULT_ENABLED.get(filename,True)))
        result.append(meta)
    return result


def get_plugin_detail(filename):
    ensure_plugins_dir()
    try: file_path=_resolve(filename)
    except ValueError as exc: raise FileNotFoundError("Invalid rule filename/path") from None
    meta=parse_plugin_metadata(file_path)
    meta["enabled"]=bool(load_config().get("enabled",{}).get(filename,DEFAULT_ENABLED.get(filename,True)))
    meta["code"]=_source_text(file_path)
    return meta


def save_plugin_code(filename, code):
    _filename(filename)
    program=validate_rule(code,migrate_legacy=True)
    ensure_plugins_dir()
    with configuration_write(CONFIG_FILE):
        config=load_config();files=_available(config)
        if filename not in files and len(files)>=MAX_PLUGINS: raise ValueError("Too many interceptor rules")
        config.setdefault("sources",{})[filename]=program.source
        if filename in config.get("deleted",[]): config["deleted"].remove(filename)
        save_config(config)
        _mirror_committed(filename,program.source)
    return get_plugin_detail(filename)


def toggle_plugin(filename, enabled):
    _filename(filename)
    if type(enabled) is not bool: raise ValueError("enabled must be boolean")
    with configuration_write(CONFIG_FILE):
        config=load_config();path=_resolve(filename,config)
        if enabled: _program(path)
        config.setdefault("enabled",{})[filename]=enabled;save_config(config)
    return {"filename":filename,"enabled":enabled}


def reorder_plugins(new_order):
    if type(new_order) is not list or len(new_order)>MAX_PLUGINS: raise ValueError("Invalid rule order")
    for filename in new_order: _filename(filename)
    with configuration_write(CONFIG_FILE):
        config=load_config();files=_available(config)
        order=list(dict.fromkeys(f for f in new_order if f in files))
        order.extend(f for f in sorted(files) if f not in order)
        config["pipeline_order"]=order;save_config(config)
    return list_plugins()


def create_plugin(filename, code):
    if type(filename) is not str: raise ValueError("Invalid filename")
    filename=filename if filename.endswith(".py") else filename+".py"
    _filename(filename);program=validate_rule(code,migrate_legacy=True);ensure_plugins_dir()
    with configuration_write(CONFIG_FILE):
        config=load_config();files=_available(config)
        if filename in files: raise FileExistsError("Rule already exists: "+filename)
        if len(files)>=MAX_PLUGINS: raise ValueError("Too many interceptor rules")
        config.setdefault("sources",{})[filename]=program.source
        if filename in config.get("deleted",[]): config["deleted"].remove(filename)
        if filename not in config.get("pipeline_order",[]): config.setdefault("pipeline_order",[]).append(filename)
        config.setdefault("enabled",{})[filename]=True;save_config(config)
        _mirror_committed(filename,program.source)
    return get_plugin_detail(filename)


def delete_plugin(filename):
    _filename(filename)
    with configuration_write(CONFIG_FILE):
        config=load_config();_resolve(filename,config)
        if filename not in config.get("deleted",[]): config.setdefault("deleted",[]).append(filename)
        config["pipeline_order"]=[f for f in config.get("pipeline_order",[]) if f!=filename]
        config.setdefault("enabled",{}).pop(filename,None)
        config.setdefault("sources",{}).pop(filename,None)
        save_config(config)  # Hide source BEFORE removing overlay, never resurrect it.
        overlay=_path(CUSTOM_PLUGINS_DIR,filename)
        if overlay.exists(): overlay.unlink()
        # Source tombstones are permanent; deleted custom-only names need not
        # consume the bounded registry forever. Clear only after removing data.
        if not (PLUGINS_DIR/filename).exists():
            config["deleted"].remove(filename)
            save_config(config)
    return True


def _trusted_entry_plans(package, decision):
    """Fixed host-side compatibility adapter, not an interpreter capability.

    Rebuild with the exact existing candidate/policy implementation. Rules receive
    only copied data; they cannot name modules, files, functions or policy sources.
    """
    if decision.get("contract_valid") is not True or not decision.get("candidate_id"): return []
    try:
        from scripts.entry_candidates import catalog
        from scripts.risk_policy import load_policy
        return copy_data(catalog(package,vars(load_policy()))["plans"])
    except Exception:
        return []  # Same legacy ADX try/except behavior; no permission inferred.


def run_interceptor_pipeline(package: dict[str, Any], decision: dict[str, Any], context: dict[str, Any]) -> tuple[str, str, float]:
    """
    Executes all enabled interceptor plugins in sequence.
    Returns: (final_action, rejection_reason, risk_reward_ratio)
    - If all enabled plugins pass: returns (raw_action, "", rr)
    - If any plugin rejects: returns ("WAIT", rejection_reason, rr)
    """
    budget = Budget(steps=100_000, loops=4_000, values=600_000, chars=2_000_000, seconds=2.0)
    try:
        package, decision, context = copy_data([package, decision, context], budget)
        if any(type(v) is not dict for v in (package, decision, context)):
            raise RuleError("Pipeline inputs must be plain dictionaries")
        context.pop(PLAN_KEY, None)  # Never trust caller-supplied reconstructed plans.
        inst_id = package.get("instId", "")
        action_value = decision.get("action", "WAIT")
        raw_action = action_value.upper() if type(action_value) is str else "WAIT"
        if raw_action not in {"BUY_LONG", "SELL_SHORT", "WAIT"}: raw_action = "WAIT"
        if type(inst_id) is not str: raise RuleError("Instrument identity must be text")
        if raw_action == "WAIT": return "WAIT", "", 0.0
        entry = float(decision.get("entry_price", 0) or 0)
        tp = float(decision.get("take_profit_price", 0) or 0)
        sl = float(decision.get("stop_loss_price", 0) or 0)
        active_inst_ids = context.get("active_inst_ids", set())
        active_position_sides = context.get("active_position_sides", {})
        if type(active_inst_ids) not in (list, tuple, set, frozenset, dict) or type(active_position_sides) is not dict:
            raise RuleError("Invalid position context")
    except (RuleError, TypeError, ValueError, OverflowError):
        return "WAIT", "Invalid or over-budget pure-data rule inputs", 0.0

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
    try:
        plugins = list_plugins()
    except (OSError, ValueError):
        return "WAIT", "Interceptor registry is unavailable; no configured rules were bypassed", rr
    plans = None
    for p_info in plugins:
        if not p_info.get("enabled"):
            continue

        filename = p_info["filename"]
        try:
            program = _program(_resolve(filename))
            try:
                passed, reason = program.execute(package, decision, context, budget=budget)
            except RuleDataRequired:
                # Only an actually reached reserved data read invokes this fixed
                # adapter. Ordinary/high-ADX decisions do not read risk config or
                # rebuild plans. Restarting is safe: rules have no side effects;
                # both passes consume the SAME finite pipeline budget.
                if plans is None: plans = _trusted_entry_plans(package, decision)
                context[PLAN_KEY] = plans
                passed, reason = program.execute(package, decision, context, budget=budget)
            if not passed:
                # Interception triggered!
                return "WAIT", str(reason or f"触发风控拦截插件 [{p_info.get('name', filename)}] 规则"), rr
        except Exception as e:
            logger.error("Error executing interceptor plugin %s: %s", filename, e)
            # Fail-closed or warn
            return "WAIT", f"风控插件 [{p_info.get('name', filename)}] 运行异常: {e}，安全降级为 WAIT", rr

    return raw_action, "", rr


def run_sandbox_test(custom_scenario: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """Evaluate bounded data-rule scenarios (legacy API name; no host Python sandbox)."""
    if custom_scenario is not None:
        custom_scenario = copy_data(custom_scenario)
        if type(custom_scenario) is not dict:
            raise ValueError("Custom scenario must be a plain dictionary")
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
        "execution_engine": ENGINE,
        "arbitrary_python": False,
        "total_plugins_count": len(plugins),
        "enabled_plugins_count": len([p for p in plugins if p.get("enabled")]),
        "duration_total_ms": round((time.time() - total_start) * 1000, 2),
        "results": test_results,
    }
