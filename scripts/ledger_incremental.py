"""Conservative closed-lifecycle reuse: exact receipt and evidence revisions.

Missing inputs or incomplete prior proof always take the original full path.
Price-only live position updates do not affect closed fill-volume ambiguity;
position identity/size, peer receipts, fills, origins, exits and observations do.
"""
import hashlib
import json
import math

VERSION='closed-reconciliation-v1'


def digest(value):
    return hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,
        separators=(',',':'),allow_nan=False).encode('utf-8')).hexdigest()


def row_digest(row):
    return digest({k:v for k,v in row.items() if k not in ('reconciliation_revision','close_notification_status')})


class ClosedReceiptRevisions:
    def __init__(self,scope,reset,instruments,archive,origins,observations,inputs,peers,positions,*,complete=True):
        self.scope=scope;self.reset=reset;self.instruments=instruments
        self.archive=archive;self.origins=origins;self.observations=observations
        self.inputs=inputs;self.peers=peers;self.positions=positions
        self.complete=complete and archive.status=='available'
        self.common={}
        self.origins_by_inst={}
        for oid,value in origins.items():
            self.origins_by_inst.setdefault(value.get('instId'),{})[oid]=value

    def for_receipt(self,receipt):
        if not self.complete:return None
        inst=receipt.get('instId')
        key=(inst,str(receipt.get('posId')),str(receipt.get('cTime')))
        origins=self.origins_by_inst.get(inst,{})
        samples=self.observations.get(key,[])
        # Do not cache an absence as completed origin/observation evidence.
        if not origins or not samples:return None
        try:
            if inst not in self.common:
                def relevant(rows):
                    return [r for r in rows if not isinstance(r,dict) or not r.get('instId') or r.get('instId')==inst]
                self.common[inst]=digest({'version':VERSION,'scope':self.scope,'reset':self.reset,
                    'instruments':[i for i in self.instruments if i.get('instId')==inst],
                    'fills':self.archive.by_instrument.get(inst,[]),'origins':origins,
                    'exit_inputs':{k:relevant(self.inputs.get(k) or []) for k in ('orders','algos','executions')},
                    'peers':[p for p in self.peers if p.get('instId')==inst],
                    'active_positions':[{k:p.get(k) for k in ('instId','posSide','pos','posId','cTime')}
                                        for p in self.positions if p.get('instId')==inst]})
            return digest({'inputs':self.common[inst],'receipt':receipt,'observations':samples})
        except (TypeError,ValueError,OverflowError):return None


def reusable(previous,revision):
    if not revision or not isinstance(previous,dict):return False
    saved=previous.get('reconciliation_revision') or {}
    if saved.get('version')!=VERSION or saved.get('inputs')!=revision:return False
    if (previous.get('status')!='closed' or previous.get('evidence_status')!='complete'
            or previous.get('source_status')!='linked' or not previous.get('position_created_at')
            or (previous.get('fee_reconciliation') or {}).get('status')!='verified'
            or previous.get('attribution_status') not in ('verified','corroborated','mixed')):return False
    for name in ('net_pnl','gross_pnl','fee','open_fee','close_fee'):
        value=previous.get(name)
        if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value):return False
    try:return saved.get('row_digest')==row_digest(previous)
    except (TypeError,ValueError,OverflowError):return False


def stamp(row,revision):
    if revision:
        try:row['reconciliation_revision']={'version':VERSION,'inputs':revision,'row_digest':row_digest(row)}
        except (TypeError,ValueError,OverflowError):pass
