import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest

from scripts import horizon_funnel

BJ=timezone(timedelta(hours=8))

class HorizonFunnelTests(unittest.TestCase):
    def test_bounded_event_and_ledger_funnel(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path=Path(tmp)/'evidence.db';scope='okx:demo:test'
            now=datetime(2026,10,7,12,0,tzinfo=BJ).timestamp()
            db=sqlite3.connect(db_path)
            try:
                db.execute('CREATE TABLE events(id TEXT, scope TEXT, kind TEXT, at REAL, payload TEXT, digest TEXT)')
                decision={'decision':{'horizon':'scalp','decision_status':'entry_candidate'},'features':{'strategy_engine':'demo_scalp_v2'}}
                submission={'plan':{'horizon':'scalp'},'transport_ok':True,'failure':None,'decision_id':'d1'}
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('d1',scope,'decision',now-60,json.dumps(decision),'x'))
                db.execute('INSERT INTO events VALUES (?,?,?,?,?,?)',('s1',scope,'entry_submission',now-30,json.dumps(submission),'x'))
                db.commit()
            finally:
                db.close()
            rows=[{'id':'t1','environment_id':scope,'instId':'BTC-USDT-SWAP','horizon':'scalp','status':'closed',
                   'open_time':'2026-10-07 11:00:00','close_time':'2026-10-07 11:10:00','net_pnl':2,'fee':-.2}]
            result=horizon_funnel.rebuild(rows,scope=scope,now=now,db_path=db_path)
            one=result['1d']['scalp']
            self.assertEqual((one['decisions'],one['entry_candidate'],one['submitted'],one['filled'],one['closed']),(1,1,1,1,1))
            self.assertEqual(one['wins'],1)

if __name__=='__main__':unittest.main()
