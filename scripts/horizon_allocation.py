"""Deterministic demo horizon allocation gate.

This module limits short-horizon sampling without forcing swing entries.  It is
active for the OKX demo environment by default and remains disabled for live
trading unless an operator explicitly opts in.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import time

from scripts.risk_policy import RiskRejected, number

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
LEDGER_PATH = DATA / "trading_ledger.json"
TRACKERS_PATH = DATA / "position_trackers.json"
INTENTS_PATH = DATA / "horizon_intents.json"
STATUS_PATH = DATA / "horizon_allocation_status.json"
VERSION = "horizon-allocation-v1"
BEIJING = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class Config:
    version: str = VERSION
    enabled: bool = True
    environment: str = "demo"
    scalp_daily_filled_limit: int = 10
    scalp_per_instrument_daily_limit: int = 2
    scalp_active_position_limit: int = 1
    total_active_slot_limit: int = 2
    swing_reserved_slots: int = 1
    scalp_risk_budget_cap_usdt: float = 5.0
    swing_risk_budget_cap_usdt: float = 15.0


def _bool(value, default=False):
    if value is None:
        return default
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}: return True
    if text in {"0", "false", "no", "off"}: return False
    raise RiskRejected("Invalid horizon allocation boolean configuration")


def _integer(values, name, default, minimum=0):
    try: value = int(values.get(name, default))
    except (TypeError, ValueError): raise RiskRejected(f"Invalid {name}") from None
    if value < minimum: raise RiskRejected(f"Invalid {name}")
    return value


def _amount(values, name, default):
    try: value = float(values.get(name, default))
    except (TypeError, ValueError): raise RiskRejected(f"Invalid {name}") from None
    if value <= 0: raise RiskRejected(f"Invalid {name}")
    return value


def load_config(mode, values=None):
    values = os.environ if values is None else values
    mode = str(mode or "demo").lower()
    master = _bool(values.get("OKXQUANT_HORIZON_ALLOCATION_ENABLED"), True)
    live = _bool(values.get("OKXQUANT_HORIZON_ALLOCATION_LIVE"), False)
    enabled = master and (mode == "demo" or (mode == "live" and live))
    config = Config(
        enabled=enabled,
        environment=mode,
        scalp_daily_filled_limit=_integer(values, "OKXQUANT_SCALP_DAILY_FILLED_LIMIT", 10, 1),
        scalp_per_instrument_daily_limit=_integer(values, "OKXQUANT_SCALP_INSTRUMENT_DAILY_LIMIT", 2, 1),
        scalp_active_position_limit=_integer(values, "OKXQUANT_SCALP_ACTIVE_LIMIT", 1, 1),
        total_active_slot_limit=_integer(values, "OKXQUANT_HORIZON_TOTAL_SLOTS", 2, 1),
        swing_reserved_slots=_integer(values, "OKXQUANT_SWING_RESERVED_SLOTS", 1, 0),
        scalp_risk_budget_cap_usdt=_amount(values, "OKXQUANT_SCALP_RISK_CAP_USDT", 5),
        swing_risk_budget_cap_usdt=_amount(values, "OKXQUANT_SWING_RISK_CAP_USDT", 15),
    )
    if config.swing_reserved_slots >= config.total_active_slot_limit:
        raise RiskRejected("Swing reserved slots must be below total horizon slots")
    if config.scalp_active_position_limit > config.total_active_slot_limit - config.swing_reserved_slots:
        raise RiskRejected("Scalp active limit would consume reserved swing slots")
    return config


def config_signature(config):
    return hashlib.sha256(json.dumps(asdict(config), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def effective_config(mode, total_slot_limit=None, values=None):
    config=load_config(mode,values)
    if total_slot_limit is None:return config
    try:external=int(total_slot_limit)
    except (TypeError,ValueError):external=0
    if external<=0 or external==config.total_active_slot_limit:return config
    config=Config(**{**asdict(config),"total_active_slot_limit":min(config.total_active_slot_limit,external)})
    if config.swing_reserved_slots>=config.total_active_slot_limit:
        raise RiskRejected("Execution preset has no room for reserved swing slot")
    if config.scalp_active_position_limit>config.total_active_slot_limit-config.swing_reserved_slots:
        raise RiskRejected("Execution preset cannot preserve the configured swing slot")
    return config


def current_signature(mode, total_slot_limit=None, values=None):
    return config_signature(effective_config(mode,total_slot_limit,values))


def _read_json(path, expected, missing):
    path = Path(path)
    if not path.exists(): return missing, None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(value, expected): raise ValueError("invalid_shape")
        return value, None
    except Exception as exc:
        return missing, type(exc).__name__


def _side(row):
    side = str(row.get("posSide") or row.get("side") or "").lower()
    if side == "net":
        try: side = "long" if float(row.get("pos") or 0) > 0 else "short"
        except (TypeError, ValueError): return "unknown"
    return side if side in {"long", "short"} else "unknown"


def _active_positions(rows):
    result=[]
    for row in rows or []:
        try: active=abs(float(row.get("pos") or 0)) > 0
        except (TypeError, ValueError): active=True
        if active: result.append(row)
    return result


def _entry_orders(rows):
    result=[]
    for row in rows or []:
        if str(row.get("reduceOnly", "false")).lower() in {"true", "1"}: continue
        try: remaining=float(row.get("sz") or 0)-float(row.get("accFillSz") or 0)
        except (TypeError, ValueError): remaining=1
        if remaining > 0: result.append(row)
    return result


def _row_horizon(row):
    horizon = str(row.get("horizon") or row.get("execution_horizon") or row.get("decision_horizon") or "").lower()
    if horizon in {"scalp", "swing"}: return horizon
    setup = str(row.get("setup") or row.get("strategy_type") or "").lower()
    engine = str(row.get("strategy_engine") or "").lower()
    if setup.startswith("scalp_") or engine == "demo_scalp_v2": return "scalp"
    return "unknown"


def _timestamp(value):
    if value in (None, "", "--"): return None
    if isinstance(value, (int, float)):
        value=float(value)
        if value > 10_000_000_000: value /= 1000
        return value
    text=str(value).strip()
    try:
        numeric=float(text)
        if numeric > 10_000_000_000:numeric /= 1000
        return numeric
    except ValueError:
        pass
    try:
        parsed=datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed=parsed.replace(tzinfo=BEIJING)
        return parsed.timestamp()
    except ValueError: return None


def _open_at(row):
    for field in ("position_created_at", "entry_at", "open_at", "open_time"):
        value=_timestamp(row.get(field))
        if value is not None:return value
    return None


def _scope_matches(row, scope):
    if not scope:return True
    observed=row.get("environment_id") or row.get("scope")
    return observed == scope


def _identity(row):
    for field in ("id", "lifecycle_id", "trade_id", "pos_id"):
        if row.get(field) not in (None, ""): return f"{field}:{row[field]}"
    orders=row.get("opening_order_ids") or []
    if orders:return "orders:"+",".join(sorted(map(str,orders)))
    return "fallback:"+"|".join(str(row.get(k) or "") for k in ("instId","side","open_time","open_px"))


def ledger_counts(rows, *, scope, now=None):
    now=time.time() if now is None else float(now)
    day=datetime.fromtimestamp(now,BEIJING).date()
    seen=set(); counts={"scalp":0,"swing":0,"unknown":0};per_instrument={}
    opening_orders=set(); position_ids=set()
    for row in rows or []:
        if not isinstance(row,dict) or not _scope_matches(row,scope):continue
        opened=_open_at(row)
        if opened is None or datetime.fromtimestamp(opened,BEIJING).date()!=day:continue
        identity=_identity(row)
        if identity in seen:continue
        seen.add(identity)
        horizon=_row_horizon(row);counts[horizon]+=1
        inst=str(row.get("instId") or row.get("inst") or "")
        if horizon=="scalp" and inst:per_instrument[inst]=per_instrument.get(inst,0)+1
        opening_orders.update(str(x) for x in row.get("opening_order_ids") or [] if x)
        if row.get("pos_id"):position_ids.add(str(row["pos_id"]))
    return {"day":day.isoformat(),"filled":counts,"scalp_per_instrument":per_instrument,
            "opening_order_ids":opening_orders,"position_ids":position_ids}


def _durable_intents(scope):
    try:
        from scripts.strategy_evidence import DB_PATH
        path=Path(DB_PATH)
        if not path.exists():return {},None
        db=sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=.5)
        try:
            rows=db.execute("SELECT id,payload FROM intents WHERE scope=? AND state IN ('unknown','acknowledged','pending')",(scope,)).fetchall()
        finally:
            db.close()
        result={}
        for identity,raw in rows:
            payload=json.loads(raw)
            if isinstance(payload,dict):result[str(identity)]=payload
        return result,None
    except Exception as exc:
        return {},type(exc).__name__


def _state_horizon(row, trackers, intents, durable, now):
    inst=str(row.get("instId") or "");side=_side(row);key=f"{inst}_{side}"
    tracker=trackers.get(key)
    if isinstance(tracker,dict) and _row_horizon(tracker) in {"scalp","swing"}:return _row_horizon(tracker)
    client=str(row.get("clOrdId") or row.get("client_id") or "")
    saved=durable.get(client)
    if isinstance(saved,dict) and _row_horizon(saved) in {"scalp","swing"}:return _row_horizon(saved)
    saved=intents.get(key)
    if isinstance(saved,dict):
        horizon=_row_horizon(saved); intent_at=_timestamp(saved.get("ts") or saved.get("at") or saved.get("created_at"))
        created=_timestamp(row.get("cTime") or row.get("created_at"))
        if horizon in {"scalp","swing"} and intent_at is not None:
            if (created is not None and abs(created-intent_at)<=600) or (created is None and 0<=now-intent_at<=21600):
                return horizon
    return "unknown"


def _atomic_status(payload):
    try:
        STATUS_PATH.parent.mkdir(parents=True,exist_ok=True)
        fd,tmp=tempfile.mkstemp(prefix=".horizon-allocation-",suffix=".tmp",dir=STATUS_PATH.parent)
        try:
            with os.fdopen(fd,"w",encoding="utf-8") as handle:
                json.dump(payload,handle,ensure_ascii=False,indent=2,allow_nan=False);handle.flush();os.fsync(handle.fileno())
            os.replace(tmp,STATUS_PATH)
        finally:
            if os.path.exists(tmp):os.unlink(tmp)
    except OSError:
        pass


def admit(env, *, horizon, inst_id, side, requested_budget, positions, pending,
          total_slot_limit=None, now=None, ledger_rows=None, trackers=None, intents=None,
          durable_intents=None):
    now=time.time() if now is None else float(now)
    horizon="scalp" if str(horizon).lower()=="scalp" else "swing"
    config=effective_config(getattr(env,"mode","demo"),total_slot_limit)
    requested=number(requested_budget,positive=True)
    cap=config.scalp_risk_budget_cap_usdt if horizon=="scalp" else config.swing_risk_budget_cap_usdt
    base={"version":VERSION,"enabled":config.enabled,"environment":config.environment,"scope":getattr(env,"identity",""),
          "config":asdict(config),"config_signature":config_signature(config),"horizon":horizon,
          "instrument":inst_id,"evaluated_at":now,"requested_budget_usdt":requested,
          "adjusted_budget_usdt":min(requested,cap)}
    if not config.enabled:
        result={**base,"adjusted_budget_usdt":requested,"outcome":"disabled"};_atomic_status(result);return result

    ledger_error=tracker_error=intent_error=durable_error=None
    if ledger_rows is None:ledger_rows,ledger_error=_read_json(LEDGER_PATH,list,[])
    if trackers is None:trackers,tracker_error=_read_json(TRACKERS_PATH,dict,{})
    if intents is None:intents,intent_error=_read_json(INTENTS_PATH,dict,{})
    if durable_intents is None:durable_intents,durable_error=_durable_intents(getattr(env,"identity",""))
    daily=ledger_counts(ledger_rows,scope=getattr(env,"identity",""),now=now)
    active=_active_positions(positions);orders=_entry_orders(pending)
    classified=[]
    for kind,rows in (("position",active),("pending",orders)):
        for row in rows:
            classified.append({"kind":kind,"instId":str(row.get("instId") or ""),"side":_side(row),
                               "horizon":_state_horizon(row,trackers,intents,durable_intents,now),"row":row})
    occupied={(x["instId"],x["side"]) for x in classified if x["horizon"] in {"scalp","unknown"}}
    pending_reservations=[]
    for item in classified:
        if item["kind"]!="pending" or item["horizon"] not in {"scalp","unknown"}:continue
        row=item["row"]; order_ids={str(row.get("ordId") or ""),str(row.get("clOrdId") or "")}-{""}
        if not order_ids.intersection(daily["opening_order_ids"]):pending_reservations.append(item)
    unledgered_positions=[]
    for item in classified:
        if item["kind"]!="position" or item["horizon"] not in {"scalp","unknown"}:continue
        pos_id=str(item["row"].get("posId") or "")
        if not pos_id or pos_id not in daily["position_ids"]:unledgered_positions.append(item)
    reservations=len({(x["instId"],x["side"]) for x in pending_reservations+unledgered_positions})
    inst_reservations=len({(x["instId"],x["side"]) for x in pending_reservations+unledgered_positions if x["instId"]==inst_id})
    snapshot={"day":daily["day"],"filled_today":daily["filled"],
              "scalp_per_instrument":daily["scalp_per_instrument"],"scalp_reservations":reservations,
              "instrument_reservations":inst_reservations,"active_positions":len(active),"pending_entries":len(orders),
              "active_scalp_or_unknown":len(occupied),"state_errors":{k:v for k,v in {
                  "ledger":ledger_error,"trackers":tracker_error,"intents":intent_error,"durable_intents":durable_error}.items() if v}}
    result={**base,"snapshot":snapshot}

    def reject(code,message):
        payload={**result,"outcome":"rejected","reason_code":code,"reason":message};_atomic_status(payload)
        raise RiskRejected(message)

    if horizon=="scalp":
        if ledger_error:reject("scalp_ledger_unavailable","Scalp allocation ledger unavailable; swing entries remain eligible")
        if (tracker_error or intent_error) and classified:
            reject("scalp_horizon_state_unavailable","Scalp horizon state unavailable while exposure exists")
        used=daily["filled"]["scalp"]+reservations
        if used>=config.scalp_daily_filled_limit:
            reject("scalp_daily_limit",f"Scalp daily filled/reserved limit reached ({used}/{config.scalp_daily_filled_limit})")
        instrument_used=daily["scalp_per_instrument"].get(inst_id,0)+inst_reservations
        if instrument_used>=config.scalp_per_instrument_daily_limit:
            reject("scalp_instrument_daily_limit",f"Scalp instrument daily limit reached for {inst_id} ({instrument_used}/{config.scalp_per_instrument_daily_limit})")
        if len(occupied)>=config.scalp_active_position_limit:
            reject("scalp_active_limit",f"Scalp active/pending limit reached ({len(occupied)}/{config.scalp_active_position_limit})")
        scalp_slots=config.total_active_slot_limit-config.swing_reserved_slots
        if len(occupied)>=scalp_slots:
            reject("swing_slot_reserved","New scalp entry would consume the reserved swing slot")
    result["outcome"]="admitted";_atomic_status(result);return result


def public_status(scope, mode="demo", *, rows=None, now=None):
    config=load_config(mode)
    if rows is None:rows,error=_read_json(LEDGER_PATH,list,[])
    else:error=None
    result={"version":VERSION,"enabled":config.enabled,"config":asdict(config),
            "config_signature":config_signature(config),"ledger":ledger_counts(rows,scope=scope,now=now)}
    result["ledger"].pop("opening_order_ids",None);result["ledger"].pop("position_ids",None)
    if error:result["ledger_error"]=error
    saved,saved_error=_read_json(STATUS_PATH,dict,{})
    if saved and saved.get("environment")==mode and saved.get("scope")==scope:
        result["last_preflight"]={k:v for k,v in saved.items() if k not in {"config_signature","scope"}}
    if saved_error:result["status_error"]=saved_error
    return result
