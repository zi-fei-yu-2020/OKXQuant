"""Disposable, digest-versioned compact projections of immutable evidence events.

The source is opened read-only and checked on every call. No source absence or
read failure may serve old cache data. Only changed/new payloads are decoded;
rolling windows, removals, scopes and corrected digests are checked each time.
The sidecar is rebuildable and never authorizes trading or replaces evidence.
"""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3


def projected_events(path, *, scope, since, kinds, namespace, project):
    path=Path(path).resolve()
    placeholders=",".join("?" for _ in kinds)
    query=f"SELECT id,kind,at,digest FROM events WHERE scope=? AND kind IN ({placeholders}) AND at>=? ORDER BY at,id"
    cache_path=path.with_name(path.stem+"_projections.db")
    with closing(sqlite3.connect(path.as_uri()+"?mode=ro",uri=True,timeout=1)) as source:
        # One source snapshot keeps metadata and subsequently fetched payloads coherent.
        source.execute("BEGIN")
        metadata=source.execute(query,(scope,*kinds,since)).fetchall()
        def read_one(identity):
            raw=source.execute("SELECT payload FROM events WHERE id=? AND scope=?",(identity,scope)).fetchone()
            if raw is None:raise ValueError("Evidence disappeared inside source snapshot")
            return project(json.loads(raw[0]))
        try:
            cache=sqlite3.connect(cache_path,timeout=1)
        except (OSError,sqlite3.Error):
            return [(i,k,at,read_one(i)) for i,k,at,d in metadata]
        try:
            os.chmod(cache_path,0o600)
            cache.execute("CREATE TABLE IF NOT EXISTS projections(namespace TEXT,scope TEXT,id TEXT,digest TEXT,payload TEXT, PRIMARY KEY(namespace,scope,id))")
            existing={i:(d,raw) for i,d,raw in cache.execute("SELECT id,digest,payload FROM projections WHERE namespace=? AND scope=?",(namespace,scope))}
            result=[];updates=[]
            for identity,kind,at,digest in metadata:
                old=existing.pop(identity,None);value=None
                if old and old[0]==digest:
                    try:value=json.loads(old[1])
                    except (ValueError,TypeError):pass
                if not isinstance(value,dict):
                    value=read_one(identity)
                    updates.append((namespace,scope,identity,digest,json.dumps(value,ensure_ascii=False,allow_nan=False,separators=(",",":"))))
                result.append((identity,kind,float(at),value))
            with cache:
                cache.executemany("INSERT OR REPLACE INTO projections VALUES (?,?,?,?,?)",updates)
                cache.executemany("DELETE FROM projections WHERE namespace=? AND scope=? AND id=?",[(namespace,scope,i) for i in existing])
            return result
        except (OSError,sqlite3.Error):
            # Corrupt/locked/unwritable optimization falls back to authoritative evidence.
            return [(i,k,at,read_one(i)) for i,k,at,d in metadata]
        finally:
            cache.close()
