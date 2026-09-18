#!/usr/bin/env python3
"""
OKXQuant AI LLM-Native Self-Improvement & Strategy Evolution Engine v0.1.0 (self_improvement_engine.py)
Evidence-based, review-only reports and pending memory proposals.
Never submits orders or automatically publishes memory, sizing or risk changes.
"""

# Standalone scheduler children must not depend on an inherited PYTHONPATH.
import sys as _sys
from pathlib import Path as _Path
_project_root = str(_Path(__file__).resolve().parents[1])
if _project_root not in _sys.path:
    _sys.path.insert(0, _project_root)


import os
import sys
import json
import time
import datetime
import urllib.request
import tempfile
import hashlib
from typing import Dict, Any, List, Optional, Tuple

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

try:
    from okxquant_backend.config import settings as standalone_settings
except ImportError:
    standalone_settings = None

WORKSPACE_DIR = PROJECT_ROOT
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
LOGS_DIR = os.path.join(WORKSPACE_DIR, "logs")

LEDGER_JSON_FILE = os.path.join(DATA_DIR, "trading_ledger.json")
REPORT_JSON_FILE = os.path.join(DATA_DIR, "self_improvement_report.json")
AI_DECISIONS_FILE = os.path.join(DATA_DIR, "ai_brain_decisions.json")
AI_MEMORY_FILE = os.path.join(DATA_DIR, "ai_trading_memory.json")
AI_MEMORY_MD_FILE = os.path.join(DATA_DIR, "AI_TRADING_MEMORY.md")
EVOLUTION_LAST_PROMPT_FILE = os.path.join(DATA_DIR, "self_improvement_last_prompt.txt")
LOG_FILE = os.path.join(LOGS_DIR, "self_improvement.log")
EVOLUTION_LOCK_FILE = os.path.join(DATA_DIR, ".self_improvement.lock")
REVIEW_PROTOCOL = 'evidence-feedback-v2'
REVIEW_TIMEOUT = 180.0
REVIEW_ATTEMPT_TIMEOUT = 80.0
REVIEW_MAX_ATTEMPTS = 2

from instrument_pool import load_instruments
from prompt_library import active_profile, apply_module_layout
from okxquant_gateway.telemetry import ModelCallTelemetry
TARGET_INSTRUMENTS = [item["name"] for item in load_instruments()]

def atomic_write_json(path: str, payload: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(prefix=".evolution-", suffix=".tmp", dir=os.path.dirname(path))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, allow_nan=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def clamp(value, lower, upper, default):
    try:
        return max(lower, min(upper, float(value)))
    except (TypeError, ValueError):
        return default


def single_evolution_cycle(func):
    def wrapped(*args, **kwargs):
        # Read-only prompt views are cross-platform; execution still requires the real POSIX lock.
        import fcntl
        lock_handle = open(EVOLUTION_LOCK_FILE, "a+", encoding="utf-8")
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            lock_handle.close()
            log_msg("Self-evolution skipped: another cycle is still running")
            return None
        try:
            return func(*args, **kwargs)
        finally:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
            lock_handle.close()
    return wrapped


def log_msg(msg: str):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    timestamp = datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{timestamp}] {msg}"
    print(line)
    try:
        os.makedirs(LOGS_DIR, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_cpa_client_config() -> Tuple[str, str]:
    """Resolve LLM credentials only from process environment or local .env."""
    try:
        from okxquant_backend.llm_manager import get_active_llm_runtime
        active_llm = get_active_llm_runtime()
        if active_llm.get("base_url"):
            return active_llm["base_url"], active_llm.get("api_key", "")
    except Exception:
        pass
    if standalone_settings:
        return standalone_settings.llm_base_url, standalone_settings.llm_api_key
    return (
        os.getenv("LLM_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY") or "",
    )

def load_closed_trades(scope=None):
    from scripts.memory_registry import scope_of
    scope=scope_of(scope)
    account_init_file = os.path.join(DATA_DIR, "account_initial_state.json")
    reset_time_str = "1970-01-01 00:00:00"
    if os.path.exists(account_init_file):
        try:
            with open(account_init_file, "r", encoding="utf-8") as f:
                acc_init = json.load(f)
                reset_time_str = acc_init.get("reset_time", "1970-01-01 00:00:00")
        except Exception:
            pass

    from scripts.evolution_evidence import review_rows, enrich
    if not os.path.exists(LEDGER_JSON_FILE):
        return []
    with open(LEDGER_JSON_FILE, "r", encoding="utf-8") as f:
        rows, excluded = review_rows(json.load(f), scope, TARGET_INSTRUMENTS, reset_time_str)
    if excluded:
        log_msg("Review excluded non-evidence rows: " + json.dumps(excluded, sort_keys=True))
    return enrich(rows, scope, DATA_DIR)

EVOLUTION_SYSTEM_PROMPT = """你是 OKXQuant 的首席投资官，负责基于真实已平仓交易证据进行认知复盘。模型只输出严格 JSON；宿主程序负责北京时间戳与 Markdown 渲染。

【证据纪律】
1. 只允许根据输入台账中真实可见的字段归因；不得把盈亏结果倒推成未提供的微积分、定积分、概率、新闻或聪明钱事实。
2. 只有输入中存在对应开平仓快照时，才可对 v/a/j/I、energy_integral、deviation_area_integral、延续/击穿概率、VaR/CVaR 作因果分析；否则必须明确标记“数理快照不可观测”，不得编造。
3. 单笔交易或小样本通常不足以证伪长期规律。证据不足时允许 NO_CHANGE，禁止为了每日报告强行制造新心法。
4. 长期记忆只是软启发式，永远不得弱化数据有效性、4H 方向否决、R:R、ATR、杠杆、保证金、OCO、禁止逆势补仓或 JSON 契约等硬风控。
5. 同时审查盈利与亏损、手续费、仓位规模、退出原因和反例；区分已验证事实、待验证假设与随机波动。

【记忆更新规则】
- ADD：多个独立样本支持新的可复用经验。
- REVISE：新证据明确限定旧经验的适用条件。
- INVALIDATE：充分反例证明旧经验失效。
- NO_CHANGE：证据不足、无新增交易或结论无法区分策略问题与随机性。
- 输出 0~4 条结论即可；没有高质量新证据时宁可空数组，不得凑数。

必须输出严格 JSON 对象，不得输出 Markdown、代码围栏或额外解释。
"""

def resolve_memory_update(change_status: str, proposed_memory: Any, existing_memory: List[str]) -> Tuple[str, List[str], bool]:
    """Normalize LLM memory change and preserve existing lessons when evidence is insufficient."""
    status = str(change_status or "NO_CHANGE").upper()
    if status not in {"NO_CHANGE", "ADD", "REVISE", "INVALIDATE"}:
        status = "NO_CHANGE"
    proposed = proposed_memory if isinstance(proposed_memory, list) else []
    preserve = status == "NO_CHANGE" or not proposed
    return status, list(existing_memory if preserve else proposed), preserve


def call_llm_evolution_review(closed_trades: List[Dict[str, Any]], existing_memory_md: str = "", timestamp_str: str = "") -> Dict[str, Any]:
    base_url, api_key = get_cpa_client_config()
    if not api_key:
        log_msg("[AI Evolution] Model credentials unavailable; review failed, approved memory retained.")
        return {}

    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj_str = timestamp_str or datetime.datetime.now(tz_bj).strftime("%Y-%m-%d %H:%M:%S (北京时间)")

    total = len(closed_trades)
    wins = [t for t in closed_trades if t["net_pnl"] > 0]
    losses = [t for t in closed_trades if t["net_pnl"] < 0]
    win_rate = round(len(wins) / total * 100, 1) if total > 0 else 0.0
    total_net = round(sum(t["net_pnl"] for t in closed_trades), 2)
    from scripts.evolution_evidence import feedback
    evidence_feedback = feedback(closed_trades)
    total_fees = evidence_feedback['fee_cost']
    fee_summary = f"{total_fees:.8f} USDT" if total_fees is not None else "unknown (incomplete fee evidence)"

    memory_context = f"""======================= 【当前系统已有的历史长期记忆库】 =======================
{existing_memory_md.strip()}
""" if existing_memory_md.strip() else "当前长期记忆库为空 (系统初始冷启动状态)"

    prompt = f"""======================= 【当前认知复盘基准时间】 =======================
【复盘基准时间】: {now_bj_str}

{memory_context}

======================= 【当前账户已结算交易证据（模拟盘不等于实盘）】 =======================
【统计汇总】:
- 总平仓笔数: {total} 笔 (胜 {len(wins)} / 负 {len(losses)} / 平 {total-len(wins)-len(losses)} | 胜率: {win_rate}%)
- 累计净盈亏: {total_net:+.2f} USDT | 累计手续费消耗: {fee_summary}
- 当前聚焦标的池: {TARGET_INSTRUMENTS}

【程序计算的费用与证据反馈（不得以模型估算替换）】：
见文末完整运行证据 JSON 的 feedback，禁止用估算替代程序统计。
手续费 fee 为负代表扣费、为正代表返佣；null 表示不可观测，不得补成零。零净盈亏是持平，不是亏损。
memory_cohorts 按实际记忆指纹、代码版本及执行预设分组，仅是描述性结果；禁止据此宣称某条心法导致盈利或已通过对照实验。
decision_evidence 只来自同账户真实成交 → 客户端订单意图 → 原始决策的精确链路。linked 表示开仓链路完整，partial 表示仅部分加仓/开仓可关联；缺失字段仍不可观测。它不包含退出时的行情快照，不得补写退出指标。

【逐笔历史交易明细 (按时间排序)】:
见文末 financial_rows 全量财务明细与 entry_details 已展开指标；未展开不等于原始归档不存在。

【复盘与长期记忆进化任务】:
规则变更必须通过 memory_proposals 提交待审核候选，不能宣称已经应用。ADD 为新增；REVISE/DEACTIVATE 必须引用当前已发布正文中的 rule_ 规则ID，不能猜测旧报告里的类别名。supporting_trade_ids 只能引用输入台账的 trade_id，引用不等于程序已认可该经验。NO_CHANGE 必须提交空候选列表。宿主会保存候选，但只有管理员核验真实成交证据并发布后才生效。
请严格基于可观测台账证据复盘。仅可描述 decision_evidence.entries.features 中已归档的开仓指标；时间匹配不是因果证明。未提供的 v/a/j/I、定积分、概率、VaR 或退出快照必须标注不可观测。提出候选时，rationale 应写清适用条件、反例、与当前基线如何比较、费用后验证指标及何时撤回；优先提出单变量、可前向验证的假设。证据不足时使用 NO_CHANGE，不得强行生成新规律。输出标准 JSON：
{{
  "change_status": "NO_CHANGE" | "ADD" | "REVISE" | "INVALIDATE",
  "diagnosis_insights": [
    "0~4 条有台账字段支持的诊断；区分已验证事实与待验证假设"
  ],
  "evolution_actions": [
    "0~4 条可执行改进；证据不足时只提出数据采集或观察建议"
  ],
  "ai_long_term_memory": [
    "0~4 条有多个独立样本支持的软启发式；不得覆盖任何硬风控"
  ],
  "memory_proposals": [
    {{"action": "ADD", "text": "待审核的软经验", "target_rule_id": null,
      "supporting_trade_ids": ["只能填写输入中真实trade_id"], "rationale": "适用条件；证据与反例；单变量对照；费用后验证指标；撤回条件"}}
  ],
  "memory_overwrites_reason": "说明证据支持何种变更；NO_CHANGE 时明确为何不覆盖旧记忆"
}}
"""

    profile = active_profile()
    effective_evolution_system = apply_module_layout(EVOLUTION_SYSTEM_PROMPT, profile, "evolution_system", f"{profile.get('name', '稳健')}自进化系统提示词模板")
    effective_evolution_user = apply_module_layout(prompt, profile, "evolution_user", f"{profile.get('name', '稳健')}自进化用户提示词模板")
    from scripts.evolution_evidence import prompt_payload
    runtime_evidence=prompt_payload(closed_trades)
    # Runtime data must not pass through the editor's 12k/module truncation.
    effective_evolution_user += '\n\n【完整运行证据 JSON】\n' + json.dumps(runtime_evidence,ensure_ascii=False,allow_nan=False,separators=(',',':'))
    effective_evolution_user += '\n财务清单覆盖全部输入交易；详细指标为明确抽样，不代表总体。只能对已展开指标作事实陈述，不得把未展开误称为原始证据缺失。'
    try:
        snapshot = f"【SYSTEM PROMPT】:\n{effective_evolution_system.strip()}\n\n{'='*70}\n【USER PROMPT ({now_bj_str})】：\n{effective_evolution_user.strip()}"
        fd, temp_path = tempfile.mkstemp(prefix=".evolution-prompt-", suffix=".tmp", dir=DATA_DIR)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(snapshot)
        os.replace(temp_path, EVOLUTION_LAST_PROMPT_FILE)
    except OSError:
        pass

    model_name = os.environ.get("LLM_MODEL") or "gemini-3.8-flash-high"
    effort = os.environ.get("LLM_REASONING_EFFORT") or "high"
    api_format = "openai_chat"
    try:
        from okxquant_backend.llm_manager import get_active_llm_runtime, execute_llm_request
        active_llm = get_active_llm_runtime()
        model_name = os.environ.get("LLM_MODEL") or active_llm.get("model") or model_name
        effort = os.environ.get("LLM_REASONING_EFFORT") or active_llm.get("reasoning_effort") or effort
        api_format = active_llm.get("api_format", "openai_chat")
        base_url = active_llm.get("base_url") or base_url
        api_key = active_llm.get("api_key") or api_key
    except Exception:
        execute_llm_request = None

    telemetry = ModelCallTelemetry(
        "self_improvement", model_name, str(effort), effective_evolution_system, effective_evolution_user
    )
    try:
        t0 = time.time()
        log_msg(f"🚀 正在调用 {model_name} ({api_format}) 生成证据复盘与待审核建议（不自动修改策略）...")
        raw_res = None
        content = ""
        if execute_llm_request:
            content, _, usage_dict, _ = execute_llm_request(
                messages=[
                    {"role": "system", "content": effective_evolution_system},
                    {"role": "user", "content": effective_evolution_user}
                ],
                model=model_name,
                base_url=base_url,
                api_key=api_key,
                api_format=api_format,
                reasoning_effort=effort,
                temperature=0.2,
                response_format={"type": "json_object"},
                timeout=REVIEW_TIMEOUT, attempt_timeout=REVIEW_ATTEMPT_TIMEOUT, max_attempts=REVIEW_MAX_ATTEMPTS,
                require_complete=True,
            )
            raw_res = {"usage": usage_dict} if isinstance(usage_dict, dict) else {}
        else:
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": effective_evolution_system},
                    {"role": "user", "content": effective_evolution_user}
                ],
                "temperature": 0.2,
                "response_format": {"type": "json_object"}
            }
            if effort not in ("none", "auto"):
                payload["reasoning_effort"] = effort
            req = urllib.request.Request(
                f"{base_url}/chat/completions",
                data=json.dumps(payload).encode("utf-8"),
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json", "User-Agent": "Mozilla/5.0"}
            )
            from okxquant_backend.llm_transport import request_json
            from scripts.model_json import verify_completion, text_content
            res,_,_,_=request_json(req.full_url,dict(req.header_items()),payload,REVIEW_TIMEOUT,
                max_attempts=REVIEW_MAX_ATTEMPTS,attempt_timeout=REVIEW_ATTEMPT_TIMEOUT)
            verify_completion(res,'openai_chat')
            content=text_content(res['choices'][0]['message']['content'])
            raw_res=res

        content = (content or "").strip()
        if content.startswith("```json"):
            content = content[7:]
        if content.startswith("```"):
            content = content[3:]
        if content.endswith("```"):
            content = content[:-3]

        review_json = json.loads(content.strip())
        if not isinstance(review_json, dict):
            review_json = {}
        telemetry.finish("success", raw_res, output_chars=len(content))
        log_msg(f"✅ AI 大脑认知复盘完成 (耗时 {round(time.time() - t0, 2)}s)")
        return review_json
    except Exception as e:
        telemetry.finish("failed", error=e)
        log_msg(f"Error in LLM evolution review: {type(e).__name__}")
        raise

@single_evolution_cycle
def run_self_evolution(force: bool = False):
    tz_bj = datetime.timezone(datetime.timedelta(hours=8))
    now_bj = datetime.datetime.now(tz_bj)
    timestamp_str = now_bj.strftime("%Y-%m-%d %H:%M:%S")
    log_msg("🧬 启动 OKXQuant AI 大脑自进化认知复盘与实战心法提炼 (v0.1.0 Crypto Focus)...")

    from scripts import memory_registry
    memory_scope=memory_registry.scope_of()
    status_file = os.path.join(DATA_DIR, 'self_improvement_status.json')
    def record_status(status, **details):
        atomic_write_json(status_file, {'status': status, 'last_attempt_at': timestamp_str, 'account_scope':memory_scope, **details})
    def record_failure(phase,exc,**details):
        messages={'load_sources':'复盘证据读取失败','model_review':'模型调用或输出校验失败',
                  'review_draft':'复盘草稿保存失败','candidate_persistence':'待审核候选暂存失败',
                  'report_persistence':'复盘报告保存失败'}
        from okxquant_backend.llm_transport import public_failure
        record_status('failed',phase=phase,error_type=type(exc).__name__,
                      message=messages[phase]+'；保留上次成功报告和运行记忆',model_failure=public_failure(exc),**details)
    record_status('running',phase='load_sources')
    try:
        memory_state=memory_registry.view(DATA_DIR,scope=memory_scope,legacy_paths={'md':AI_MEMORY_MD_FILE,'json':AI_MEMORY_FILE})
        closed_trades = load_closed_trades(scope=memory_scope)
    except Exception as exc:
        record_failure('load_sources',exc)
        raise RuntimeError('Review source unavailable; previous report and memory preserved') from exc
    total_trades = len(closed_trades)
    ledger_revision = hashlib.sha256(
        json.dumps(closed_trades, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    if not force and os.path.exists(REPORT_JSON_FILE):
        try:
            with open(REPORT_JSON_FILE, "r", encoding="utf-8") as f:
                previous_report = json.load(f)
            if (previous_report.get('review_status') == 'success' and previous_report.get('review_protocol') == REVIEW_PROTOCOL
                    and previous_report.get("ledger_revision") == ledger_revision
                    and previous_report.get('account_scope') == memory_scope
                    and previous_report.get('memory_version_at_review') == memory_state['active_version']):
                record_status('no_new_evidence', ledger_revision=ledger_revision)
                log_msg("No new closed-trade evidence; keeping the successful review and approved memory")
                return previous_report
        except Exception:
            pass

    # 1. Base Stats
    win_trades = [t for t in closed_trades if t["net_pnl"] > 0]
    loss_trades = [t for t in closed_trades if t["net_pnl"] <= 0]
    win_count = len(win_trades)
    win_rate = round(win_count / total_trades * 100, 1) if total_trades > 0 else 0.0
    total_win_amt = sum(t["net_pnl"] for t in win_trades)
    total_loss_amt = abs(sum(t["net_pnl"] for t in loss_trades))
    profit_factor = round(total_win_amt / total_loss_amt, 6) if total_loss_amt > 0 else None

    existing_memory_md=memory_state['content']
    existing_core_lessons=[r['text'] for r in memory_state['rules'] if r.get('enabled',True)]

    # 2. Review only; approved memory can change solely via human publication.
    record_status('running',phase='model_review',ledger_revision=ledger_revision)
    try:
        llm_review = call_llm_evolution_review(closed_trades, existing_memory_md=existing_memory_md, timestamp_str=timestamp_str)
        if not isinstance(llm_review, dict) or llm_review.get('change_status') not in {'NO_CHANGE', 'ADD', 'REVISE', 'INVALIDATE'}:
            raise ValueError('Review missing a valid change_status')
        for field in ('diagnosis_insights', 'evolution_actions', 'ai_long_term_memory'):
            values = llm_review.get(field, [])
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                raise ValueError('Invalid review field: ' + field)
    except Exception as exc:
        record_failure('model_review',exc,ledger_revision=ledger_revision)
        log_msg('Review failed; successful report, approved memory and pending candidates retained')
        raise RuntimeError('Self-improvement model review failed; this ledger revision remains retryable') from exc

    # Preserve the validated response before attempting database/report writes.
    # It is an audit draft, never automatically reused as current inference or active memory.
    try:
        atomic_write_json(os.path.join(DATA_DIR,'self_improvement_review_draft.json'),
            {'account_scope':memory_scope,'ledger_revision':ledger_revision,'review_protocol':REVIEW_PROTOCOL,
             'reviewed_at':timestamp_str,'memory_version':memory_state['active_version'],
             'memory_prompt_hash':memory_state['prompt_hash'],'status':'report_fields_validated_not_published','review':llm_review})
    except Exception as exc:
        record_failure('review_draft',exc,ledger_revision=ledger_revision)
        raise RuntimeError('Review draft persistence failed; approved memory retained') from exc

    change_status, _, _ = resolve_memory_update(llm_review.get("change_status", "NO_CHANGE"), [], [])
    insights = llm_review.get("diagnosis_insights", [])
    actions_taken = llm_review.get("evolution_actions", [])
    insights = [x.strip()[:3000] for x in insights if isinstance(x, str) and x.strip()][:4] if isinstance(insights, list) else []
    actions_taken = [x.strip()[:3000] for x in actions_taken if isinstance(x, str) and x.strip()][:4] if isinstance(actions_taken, list) else []

    raw_asset_mults = llm_review.get("asset_multipliers", {})
    if not isinstance(raw_asset_mults, dict):
        raw_asset_mults = {}
    asset_mults = {
        asset: clamp(raw_asset_mults.get(asset, 1.0), 0.5, 1.5, 1.0)
        for asset in TARGET_INSTRUMENTS
    }
    change_status, long_term_memory, preserve_existing_memory = resolve_memory_update(
        change_status, llm_review.get("ai_long_term_memory", []), existing_core_lessons
    )

    # Review proposals are durable, append-only candidates; NO_CHANGE never erases pending work.
    proposed_change_status=change_status
    proposals=llm_review.get('memory_proposals')
    if proposals is None:
        action={'ADD':'ADD','REVISE':'REVISE','INVALIDATE':'DEACTIVATE'}.get(proposed_change_status)
        proposals=[{'action':action,'text':t} for t in llm_review.get('ai_long_term_memory',[])][:4] if action else []
    record_status('running',phase='candidate_persistence',ledger_revision=ledger_revision)
    try:
        if memory_registry.scope_of()!=memory_scope:raise ValueError('Account changed during memory review')
        if llm_review.get('change_status')=='NO_CHANGE' and proposals:raise ValueError('NO_CHANGE cannot propose rule changes')
        if not isinstance(proposals,list) or len(proposals)>4:raise ValueError('Invalid memory proposal list')
        for proposal in proposals:
            if not isinstance(proposal,dict) or proposal.get('action') not in ('ADD','REVISE','DEACTIVATE'):
                raise ValueError('Invalid automatic memory proposal')
        candidate_ids=memory_registry.stage_review(proposals,ledger_revision,data_dir=DATA_DIR,scope=memory_scope,
            legacy_paths={'md':AI_MEMORY_MD_FILE,'json':AI_MEMORY_FILE})
    except Exception as exc:
        record_failure('candidate_persistence',exc,ledger_revision=ledger_revision)
        raise RuntimeError('Memory candidate persistence failed; approved memory retained') from exc
    # A separate successful human publication is the only live rule change.
    long_term_memory=existing_core_lessons
    preserve_existing_memory=True
    change_status='NO_CHANGE'
    try:
        candidates=[c for c in memory_registry.view(DATA_DIR,scope=memory_scope,admin=True)['candidates'] if c['id'] in candidate_ids] if candidate_ids else []
    except Exception as exc:
        record_failure('candidate_persistence',exc,ledger_revision=ledger_revision)
        raise RuntimeError('Staged candidates cannot be read; approved memory retained') from exc

    # Build a report artifact, never overwrite the active Markdown memory.
    md_content = f"""# OKXQuant AI 交易复盘（报告，不等于已应用的策略变更）

> **本次复盘时间**: {timestamp_str} (北京时间)
> **复盘样本覆盖**: 已平仓 {total_trades} 笔 | 样本胜率: {win_rate}%
> **模式说明**: 基于当前账户的已平仓台账复盘，模拟盘样本不等于实盘结果。新建议单独接受审核，不自动覆盖运行记忆、权重或风险参数。

---

## 🧠 核心实战心法与直觉提示词 (Heuristic Lessons)

"""
    for idx, item in enumerate(long_term_memory, 1):
        clean_item = item.strip()
        # Strip any existing leading bracket timestamp
        if clean_item.startswith("[") and "]" in clean_item:
            clean_item = clean_item.split("]", 1)[1].strip()
        md_content += f"{idx}. {clean_item}\n"

    md_content += f"""
---

## 🔍 痛点归因与记忆更新依据 (Diagnosis & Evolution Rationale)

- 🔄 **本轮认知迭代覆盖摘要**: {llm_review.get('memory_overwrites_reason', '结合最新平仓损益完成记忆时效性检验与动态覆盖')}
"""
    for ins in insights:
        clean_ins = ins.strip()
        if clean_ins.startswith("[") and "]" in clean_ins:
            clean_ins = clean_ins.split("]", 1)[1].strip()
        md_content += f"- 💡 [{timestamp_str}] {clean_ins}\n"

    # 4. Save Dashboard Report
    from scripts.evolution_evidence import feedback, prompt_payload
    report_payload = {
        "timestamp": timestamp_str,
        "ledger_revision": ledger_revision,
        "total_trades": total_trades,
        "evidence_feedback": feedback(closed_trades),
        "win_rate": win_rate,
        "profit_factor": profit_factor,
        "mode": "review_only",
        "review_status": "success",
        "review_protocol": REVIEW_PROTOCOL,
        "evidence_input_coverage": prompt_payload(closed_trades)["coverage"],
        "review_markdown": md_content,
        "completed_at": datetime.datetime.now(tz_bj).strftime('%Y-%m-%d %H:%M:%S'),
        "proposed_change_status": proposed_change_status,
        "pending_candidate_count": sum(c['status']=='pending' for c in candidates),
        "memory_candidate_ids": candidate_ids,
        "memory_version_at_review": memory_registry.view(DATA_DIR,scope=memory_scope)['active_version'],
        "account_scope": memory_scope,
        "recommendations": actions_taken,
        "change_status": change_status,
        "memory_preserved": preserve_existing_memory,
        "insights": insights,
        "actions_taken": actions_taken,
        "core_lessons": long_term_memory
    }

    record_status('running',phase='report_persistence',ledger_revision=ledger_revision)
    try:
        if memory_registry.scope_of()!=memory_scope:raise ValueError('Account changed before report persistence')
        atomic_write_json(REPORT_JSON_FILE, report_payload)
    except Exception as exc:
        record_failure('report_persistence',exc,ledger_revision=ledger_revision)
        raise RuntimeError('Successful review report could not be saved; previous report retained') from exc
    try:
        review_md_file = os.path.join(DATA_DIR, "self_improvement_review.md")
        tmp_md = review_md_file + ".tmp"
        with open(tmp_md, "w", encoding="utf-8") as f:
            f.write(md_content)
        os.replace(tmp_md, review_md_file)
        log_msg(f"📝 复盘报告已写入 {review_md_file}；运行记忆保持不变")
    except Exception as e:
        log_msg(f"Markdown 记忆写入异常: {e}")

    record_status('success',phase='complete',ledger_revision=ledger_revision)

    log_msg(f"🧬 自进化认知复盘完成 | 状态={change_status} | 当前保留 {len(long_term_memory)} 条启发式长期记忆")
    try:
        from qq_notifier import notify_evolution_report
        top_lesson = long_term_memory[0] if long_term_memory else "保持风控原则"
        notify_evolution_report(win_rate, total_trades, change_status, top_lesson)
    except Exception as e:
        log_msg(f"自进化通知发送失败: {e}")
    return report_payload

if __name__ == "__main__":
    force_run = "--force" in sys.argv or "-f" in sys.argv
    res = run_self_evolution(force=force_run)
    print(json.dumps(res, indent=2, ensure_ascii=False))
