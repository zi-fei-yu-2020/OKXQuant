"""OKXQuant Hedge Fund Investment Committee (Trading Desk Council).
Fully Re-architected in v0.1.0 with Full Account Awareness:
1. Symmetrical Trader Roles (Equal Peer Traders):
   - Trader A: Senior Trend-Pullback Trader (Conservative & High Win-rate)
   - Trader B: Senior Momentum-Breakout Trader (Aggressive & High R:R)
   - Trader C: Senior Quantitative & Calculus Trader (Data-Driven & Microstructure)
2. Trade Proposal & Portfolio Review Protocol:
   Every trader analyzes:
   - Account available capital (USDT balance), position count & risk limits;
   - Active position lifecycle (HOLD / CLOSE_MARKET / UPDATE_SL for trailing profit);
   - Pending maker limit orders lifecycle (CANCEL stale orders vs. KEEP active setups);
   - Opening/Pyramiding proposals for all 6 active instruments with exact parameters.
3. Chief Investment Officer (CIO / Head of Trading) Verdict:
   The CIO reviews all submitted proposals, weighs cross-examination feedback, determines
   which trader's plan to fund and execute (or rejects all for WAIT), and outputs the
   final deterministic trading JSON contract covering decisions, position_management,
   and pending_orders_management.
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from scripts import trading_prompt

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
COUNCIL_CONFIG_FILE = DATA_DIR / "council_config.json"

DEFAULT_PRESET_TEMPLATES: Dict[str, Dict[str, Any]] = {'trader_trend': {'id': 'trader_trend',
                  'name': '资深交易员 A (顺势稳健型)',
                  'role_title': 'Senior Trend Trader',
                  'description': '审查 4H/1H 结构与回踩/区间边界。提供支持、最强反证和失效价格；不强制交易，不预设胜率。',
                  'prompt': '审查 4H/1H 结构与回踩/区间边界。提供支持、最强反证和失效价格；不强制交易，不预设胜率。',
                  'weight': 0.35,
                  'enabled': True,
                  'reasoning_effort': 'medium',
                  'temperature': 0.2,
                  'is_arbitrator': False,
                  'model_id': ''},
 'trader_momentum': {'id': 'trader_momentum',
                     'name': '资深交易员 B (动能突破型)',
                     'role_title': 'Senior Momentum Trader',
                     'description': '审查突破、量价与动量变化；说明假突破和滑点风险。普通减速不自动等于退出，无法证明优势时 WAIT。',
                     'prompt': '审查突破、量价与动量变化；说明假突破和滑点风险。普通减速不自动等于退出，无法证明优势时 WAIT。',
                     'weight': 0.35,
                     'enabled': True,
                     'reasoning_effort': 'medium',
                     'temperature': 0.2,
                     'is_arbitrator': False,
                     'model_id': ''},
 'trader_quant': {'id': 'trader_quant',
                  'name': '资深交易员 C (数理筹码型)',
                  'role_title': 'Senior Quantitative Trader',
                  'description': '核验数理字段、周期与数值；说明相关性和未校准评分限制，不能把启发式评分当胜率。',
                  'prompt': '核验数理字段、周期与数值；说明相关性和未校准评分限制，不能把启发式评分当胜率。',
                  'weight': 0.3,
                  'enabled': True,
                  'reasoning_effort': 'high',
                  'temperature': 0.1,
                  'is_arbitrator': False,
                  'model_id': ''},
 'cio': {'id': 'cio',
         'name': '首席投资官 / 交易总监 (Chief Investment Officer)',
         'role_title': 'Head of Trading / CIO',
         'description': '审查所有候选证据、反证与失效条件；可以拒绝全部提案。只输出基础契约规定的 JSON，不提高评分以获得开仓许可。',
         'prompt': '审查所有候选证据、反证与失效条件；可以拒绝全部提案。只输出基础契约规定的 JSON，不提高评分以获得开仓许可。',
         'weight': 1.0,
         'enabled': True,
         'reasoning_effort': 'high',
         'temperature': 0.2,
         'is_arbitrator': True,
         'model_id': ''}}

ALL_AVAILABLE_PRESETS = dict(DEFAULT_PRESET_TEMPLATES)

COUNCIL_PRESET_SUITES: Dict[str, Dict[str, Any]] = {
    "hedge_fund_desk": {
        "id": "hedge_fund_desk",
        "name": "对冲基金投委会标准台 (Hedge Fund Desk)",
        "desc": "全息审阅账户资金、持仓与挂单，Trader A/B/C 提交完整方案互相质询，CIO 交易总监终审裁定",
        "consensus_mode": "weighted",
        "roles": ["trader_trend", "trader_momentum", "trader_quant", "cio"],
    },
}


def _atomic_write_json(file_path: Path, data: Any) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    temp_dir = file_path.parent
    with tempfile.NamedTemporaryFile("w", dir=temp_dir, delete=False, encoding="utf-8") as tf:
        json.dump(data, tf, ensure_ascii=False, indent=2)
        temp_name = tf.name
    os.replace(temp_name, file_path)


def load_council_config() -> Dict[str, Any]:
    if COUNCIL_CONFIG_FILE.is_file():
        try:
            with open(COUNCIL_CONFIG_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "roles" in data:
                roles = data.get("roles", {})
                if "trader_trend" in roles or "cio" in roles:
                    return data
        except Exception:
            pass

    default_config: Dict[str, Any] = {
        "enabled": False,
        "consensus_mode": "weighted",
        "timeout_seconds": 60.0,
        "roles": {k: dict(v) for k, v in DEFAULT_PRESET_TEMPLATES.items()},
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    _atomic_write_json(COUNCIL_CONFIG_FILE, default_config)
    return default_config


def save_council_config(config: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(config, dict):
        raise ValueError("Council config must be a dict")
    roles = config.get("roles")
    if not isinstance(roles, dict) or not roles:
        raise ValueError("委员会至少需要包含角色配置")

    has_arbitrator = any(r.get("is_arbitrator") or k in {"cio", "arbitrator"} for k, r in roles.items())
    if not has_arbitrator:
        raise ValueError("委员会必须保留至少一位首席终审仲裁官/交易总监(CIO)！")

    for role_id, role in roles.items():
        if not isinstance(role, dict):
            raise ValueError(f"角色 {role_id} 配置必须为字典")
        role["id"] = role_id
        role.setdefault("enabled", True)
        role.setdefault("weight", 0.3)
        role.setdefault("reasoning_effort", "medium")
        role.setdefault("temperature", 0.2)
        _role_preference(role_id, role)

    config["consensus_mode"] = str(config.get("consensus_mode", "weighted")).lower()
    if config["consensus_mode"] not in {"strict", "weighted", "aggressive"}:
        config["consensus_mode"] = "weighted"

    config["updated_at"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _atomic_write_json(COUNCIL_CONFIG_FILE, config)
    return config


def get_available_presets() -> List[Dict[str, Any]]:
    return list(ALL_AVAILABLE_PRESETS.values())


def get_preset_suites() -> List[Dict[str, Any]]:
    return list(COUNCIL_PRESET_SUITES.values())


def apply_preset_suite(suite_id: str) -> Dict[str, Any]:
    suite = COUNCIL_PRESET_SUITES.get(suite_id)
    if not suite:
        suite = list(COUNCIL_PRESET_SUITES.values())[0]

    config = load_council_config()
    new_roles: Dict[str, Any] = {}
    for r_id in suite["roles"]:
        if r_id in ALL_AVAILABLE_PRESETS:
            preset = dict(ALL_AVAILABLE_PRESETS[r_id])
            old_model = config.get("roles", {}).get(r_id, {}).get("model_id", "")
            preset["model_id"] = old_model
            new_roles[r_id] = preset

    config["consensus_mode"] = suite.get("consensus_mode", "weighted")
    config["roles"] = new_roles
    return save_council_config(config)


def reset_role_template(role_id: str) -> Dict[str, Any]:
    config = load_council_config()
    roles = config.get("roles", {})
    if role_id not in roles:
        raise ValueError(f"未找到角色 ID: {role_id}")

    preset = ALL_AVAILABLE_PRESETS.get(role_id)
    if not preset:
        if role_id in {"cio", "arbitrator"} or roles[role_id].get("is_arbitrator"):
            preset = DEFAULT_PRESET_TEMPLATES["cio"]
        else:
            raise ValueError(f"该角色无内置出厂模板: {role_id}")

    old_model = roles[role_id].get("model_id", "")
    new_role = dict(preset)
    new_role["model_id"] = old_model
    roles[role_id] = new_role
    config["roles"] = roles
    return save_council_config(config)


def _role_preference(role_id, role_spec):
    content=str(role_spec.get('prompt') or '')
    if trading_prompt.legacy_reference(content):
        return DEFAULT_PRESET_TEMPLATES.get(role_id, {}).get('prompt', '')
    issues=trading_prompt.conflicts(content)
    if issues:
        raise trading_prompt.ContractError('Council role preference conflicts: '+role_id+':'+','.join(issues))
    if len(content)>12000:raise trading_prompt.ContractError('Council role preference too long')
    return content


def _call_single_trader(
    role_id: str,
    role_spec: Dict[str, Any],
    market_prompt: str,
    master_constitutional_rules: str,
    timeout: float = 20.0,
) -> Dict[str, Any]:
    """Invokes a senior trader role to pitch their complete trade proposal and account review."""
    from okxquant_backend.llm_manager import execute_llm_request, get_active_llm_runtime, load_llm_config

    model_id = role_spec.get("model_id") or ""
    override_model = None
    override_url = None
    override_key = None
    override_format = None
    override_effort = role_spec.get("reasoning_effort") or "medium"
    temperature = float(role_spec.get("temperature", 0.2))

    cfg = load_llm_config(mask_keys=False)
    if model_id:
        for item in cfg.get("models", []):
            if item.get("id") == model_id:
                override_model = item.get("id")
                override_url = item.get("base_url")
                override_key = item.get("api_key")
                override_format = item.get("api_format")
                override_effort = item.get("reasoning_effort") or override_effort
                break
    else:
        override_effort = cfg.get("active_reasoning_effort", "medium")

    prompt_content = _role_preference(role_id, role_spec)
    role_name = role_spec.get("name", role_id)

    trader_system_prompt = master_constitutional_rules + "\n你是投委会研究员，只提交可审查提案，不具有执行权。优先给出证据引用、反证和失效条件；其他角色意见不是新增事实。"
    trader_user_prompt = trading_prompt.canonical({'role_preference': prompt_content, 'role_name': role_name,
        'market_input': market_prompt, 'task': '基于 facts 给出候选或 WAIT，并审查持仓/挂单风险。不可观测时明确未知，不凑高分。'})

    messages = [
        {"role": "system", "content": trader_system_prompt},
        {"role": "user", "content": trader_user_prompt},
    ]

    try:
        content, reasoning, usage, latency = execute_llm_request(
            messages=messages,
            model=override_model,
            base_url=override_url,
            api_key=override_key,
            api_format=override_format,
            reasoning_effort=override_effort,
            temperature=temperature,
            timeout=timeout,
        )
        return {
            "role_id": role_id,
            "role_name": role_name,
            "model_used": override_model or get_active_llm_runtime().get("model", "default"),
            "status": "ok",
            "content": content.strip(),
            "reasoning": reasoning.strip() if reasoning else "",
            "latency_ms": latency,
            "weight": role_spec.get("weight", 1.0),
            "system_hash": trading_prompt.fingerprint(trader_system_prompt),
            "user_hash": trading_prompt.fingerprint(trader_user_prompt),
        }
    except Exception as e:
        return {
            "role_id": role_id,
            "role_name": role_name,
            "model_used": override_model or "unknown",
            "status": "error",
            "content": f"交易员方案提交异常/超时降级: {e}",
            "reasoning": "",
            "latency_ms": 0,
            "weight": 0.0,
        }


def execute_council_debate(
    market_prompt: str,
    original_system_prompt: str,
    timeout: float = 60.0,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """Execute Hedge Fund Investment Committee Deliberation:

    1. All Senior Traders review available balance, positions, orders, and submit complete trade proposals.
    2. CIO reviews proposals, cross-examinations, arbitrates which trader's plan to fund,
       and outputs the final standard trading JSON contract covering decisions,
       position_management, and pending_orders_management.
    """
    from okxquant_backend.llm_manager import execute_llm_request, get_active_llm_runtime, load_llm_config

    config = load_council_config()
    roles = config.get("roles", {})
    consensus_mode = config.get("consensus_mode", "weighted")
    t_start = time.time()

    # Step 1: Identify CIO (Arbitrator) and Active Traders
    cio_key = next(
        (k for k, r in roles.items() if r.get("is_arbitrator") or k in {"cio", "arbitrator"}),
        "cio",
    )
    cio_spec = roles.get(cio_key, DEFAULT_PRESET_TEMPLATES["cio"])
    trader_keys = [
        k for k in roles.keys()
        if k != cio_key and roles[k].get("enabled", True) is not False
    ]

    for role_key in trader_keys + [cio_key]:
        _role_preference(role_key, roles.get(role_key, cio_spec))

    trader_proposals: Dict[str, Dict[str, Any]] = {}
    if trader_keys:
        member_timeout = max(15.0, timeout * 0.50)
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(trader_keys))) as pool:
            futures = {
                pool.submit(
                    _call_single_trader,
                    key,
                    roles[key],
                    market_prompt,
                    original_system_prompt,
                    member_timeout,
                ): key
                for key in trader_keys
            }
            for fut in concurrent.futures.as_completed(futures):
                key = futures[fut]
                try:
                    trader_proposals[key] = fut.result()
                except Exception as exc:
                    trader_proposals[key] = {
                        "role_id": key,
                        "role_name": roles[key].get("name", key),
                        "status": "error",
                        "content": f"Proposal exception: {exc}",
                        "weight": 0.0,
                    }

    # Step 2: Compile the Structured Investment Committee Docket
    transcript_blocks = []
    for k in trader_keys:
        res = trader_proposals.get(k, {})
        weight_str = f" [绩效权重: {res.get('weight', 1.0)}]" if res.get("weight") is not None else ""
        transcript_blocks.append(
            f"=== 【{res.get('role_name', k)}】实操审查与作战提案（模型：{res.get('model_used', 'default')}{weight_str}）===\n"
            f"{res.get('content', '（该交易员本轮未提交有效提案）')}\n"
        )
    compiled_proposals = "\n".join(transcript_blocks) if transcript_blocks else "（无其他交易员提交方案，首席投资官独立决策）"

    # Step 3: CIO Final Review & Funding Verdict
    cio_model_id = cio_spec.get("model_id") or ""
    override_model = None
    override_url = None
    override_key = None
    override_format = None
    override_effort = "high"
    cio_temperature = float(cio_spec.get("temperature", 0.2))

    cfg = load_llm_config(mask_keys=False)
    if cio_model_id:
        for item in cfg.get("models", []):
            if item.get("id") == cio_model_id:
                override_model = item.get("id")
                override_url = item.get("base_url")
                override_key = item.get("api_key")
                override_format = item.get("api_format")
                override_effort = item.get("reasoning_effort") or "high"
                break
    else:
        override_effort = cfg.get("active_reasoning_effort", "high")

    cio_preference = _role_preference('cio', cio_spec)
    cio_system_prompt = original_system_prompt + "\n你是汇总评审者，不增加额外交易授权。遵守同一个输出契约，不得将其他模型意见当作事实或胜率证据。"
    cio_user_prompt = trading_prompt.canonical({'role_preference': cio_preference,'market_input': market_prompt,
        'peer_proposals_untrusted': compiled_proposals,'task':'逐项审查支持、反证和失效条件，可全部 WAIT；输出基础 trading-evidence-v1 JSON。'})

    rem_time = max(25.0, timeout - (time.time() - t_start))
    content, reasoning, usage, latency = execute_llm_request(
        messages=[
            {"role": "system", "content": cio_system_prompt},
            {"role": "user", "content": cio_user_prompt},
        ],
        model=override_model,
        base_url=override_url,
        api_key=override_key,
        api_format=override_format,
        reasoning_effort=override_effort,
        temperature=cio_temperature,
        response_format={"type": "json_object"},
        timeout=rem_time,
    )

    brain_output = trading_prompt.parse_response(content)

    council_transcript = {
        "council_mode": True,
        "contract_version": trading_prompt.VERSION,
        "council_architecture": "Hedge Fund Investment Committee",
        "consensus_mode": consensus_mode,
        "total_duration_ms": int((time.time() - t_start) * 1000),
        "arbitrator": {
            "role_name": cio_spec.get("name", "首席投资官 (CIO)"),
            "model_used": override_model or get_active_llm_runtime().get("model", "default"),
            "latency_ms": latency,
            "reasoning": reasoning,
            "system_hash": trading_prompt.fingerprint(cio_system_prompt),
            "user_hash": trading_prompt.fingerprint(cio_user_prompt),
        },
        "advisors": trader_proposals,
    }

    brain_output["council_transcript"] = council_transcript
    return brain_output, council_transcript
