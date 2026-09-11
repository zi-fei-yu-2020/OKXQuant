"""Independent regular-market news connection; no inherited trading credentials."""
from datetime import datetime, timezone, timedelta
import copy
import math
import time
from okxquant_backend import account_connections, connection_transport

MAX_AGE = 1200


def stamp(at):
    return datetime.fromtimestamp(at, timezone(timedelta(hours=8))).strftime('%Y-%m-%d %H:%M:%S') if at else None


def details(rows):
    if not isinstance(rows,list): raise ValueError('invalid_news_response')
    result=[]
    for row in rows:
        if not isinstance(row,dict) or not isinstance(row.get('details'),list): raise ValueError('invalid_news_details')
        result.extend(row['details'])
    if any(not isinstance(r,dict) for r in result): raise ValueError('invalid_news_items')
    return result


def articles(rows):
    result=details(rows)
    from urllib.parse import urlsplit
    for row in result:
        at=float(row.get('cTime') or 0)
        if not math.isfinite(at) or at<0 or not isinstance(row.get('title',''),str): raise ValueError('invalid_news_timestamp_or_title')
        url=str(row.get('sourceUrl') or '')
        if url and urlsplit(url).scheme not in {'https','http'}: row['sourceUrl']=''
    return result


def sentiments(rows,coins):
    result={}
    for row in details(rows):
        ccy=row.get('ccy')
        if not isinstance(ccy,str) or not ccy: raise ValueError('invalid_sentiment_instrument')
        if ccy not in coins: continue
        s=row.get('sentiment') or {}
        bull=float(s['bullishRatio']);bear=float(s['bearishRatio'])
        if not all(math.isfinite(n) and 0<=n<=1 for n in (bull,bear)): raise ValueError('invalid_sentiment_ratio')
        counts=[int(s[k]) for k in ('bullishCnt','bearishCnt','neutralCnt')];mentions=int(row['mentionCnt'])
        if min(counts+[mentions])<0: raise ValueError('invalid_sentiment_count')
        result[ccy]={'ccy':ccy,'label':s.get('label','unknown'),'available':True,'bullish_ratio':f'{bull*100:.1f}%',
            'bearish_ratio':f'{bear*100:.1f}%','bullish_pct':f'{bull*100:.1f}%','bearish_pct':f'{bear*100:.1f}%',
            'long_short_ratio':f'{counts[0]/counts[1]:.2f}' if counts[1]>0 else '--',
            'bull_cnt':counts[0],'bear_cnt':counts[1],'neutral_cnt':counts[2],'mentions':mentions,
            'sentiment_factor_score':round((bull-bear)*.8,2)}
    return result


def collect(coins, previous=None, *, now=None, reader=None, connection=None):
    now=time.time() if now is None else now;previous=previous or {};reader=reader or connection_transport.request
    try:
        connection=connection or account_connections.news_connection()
    except account_connections.AccountChangeError:
        cached=copy.deepcopy(previous)
        cached.update(schema=2,source='okx_official_news',connection_status='unconfigured',last_attempt_at=now,stale_sections=True,
                      message='资讯连接未绑定；不会借用交易Key。旧内容仅供历史查看。',macro_sentiment='UNKNOWN（资讯连接未绑定）')
        cached.setdefault('latest_news',[]);cached.setdefault('coins_sentiment',{})
        cached['updated_at']=stamp(cached.get('last_success_at'))
        return cached
    identity=connection['id'];generation=connection.get('generation',0)
    if previous.get('connection_id')!=identity or previous.get('connection_generation',0)!=generation: previous={}
    if 0<=now-previous.get('last_attempt_at',0)<30: return copy.deepcopy(previous)
    sections=copy.deepcopy(previous.get('sections',{}));fetched={}
    queries={'latest':('/api/v5/orbit/news-search',{'sortBy':'latest','importance':'low','acceptLanguage':'zh-CN','limit':15}),
             'important':('/api/v5/orbit/news-search',{'sortBy':'latest','importance':'high','acceptLanguage':'zh-CN','limit':15}),
             'sentiment':('/api/v5/orbit/currency-sentiment-query',{'ccy':','.join(coins),'period':'24h'})}
    for name,(path,params) in queries.items():
        old=sections.get(name,{})
        try:
            rows=reader(connection,'news','live','GET',path,params,timeout=5)
            parsed=sentiments(rows,coins) if name=='sentiment' else articles(rows)
            fetched[name]=parsed
            sections[name]={'status':'fresh','last_success_at':now,'last_attempt_at':now,'error':None,'data':parsed}
        except (connection_transport.ConnectionError,ValueError,KeyError,TypeError) as exc:
            sections[name]={**old,'status':'stale' if 'data' in old else 'unavailable','last_attempt_at':now,
                            'error':getattr(exc,'code','invalid_response')}
    raw=[];seen=set()
    for name in ('latest','important'):
        for item in sections.get(name,{}).get('data',[]):
            identity_key=str(item.get('id') or '')
            if identity_key and identity_key not in seen:
                seen.add(identity_key);raw.append(item)
    raw.sort(key=lambda r:float(r.get('cTime') or 0),reverse=True)
    items=[]
    for item in raw[:10]:
        at=float(item.get('cTime') or 0)/1000
        items.append({'id':item.get('id'),'time':stamp(at) or '--','source_at':at,'title':str(item.get('title') or ''),
                      'summary':str(item.get('summary') or ''),'coins':item.get('ccyList',[]),'platforms':item.get('platformList',[]),
                      'importance':item.get('importance','unknown'),'url':item.get('sourceUrl','')})
    score_rows=copy.deepcopy(sections.get('sentiment',{}).get('data',{}))
    for ccy in coins:
        score_rows.setdefault(ccy,{'ccy':ccy,'label':'unknown','available':False,'bullish_ratio':'--','bearish_ratio':'--',
                                  'long_short_ratio':'--','mentions':None,'sentiment_factor_score':None})
    scores=[r['sentiment_factor_score'] for r in score_rows.values() if isinstance(r.get('sentiment_factor_score'),(float,int))]
    good=len(fetched)==3
    last_success=now if good else previous.get('last_success_at')
    macro='UNKNOWN（情绪数据不完整或过期）'
    if sections.get('sentiment',{}).get('status')=='fresh' and scores:
        bull=sum(s>.25 for s in scores);bear=sum(s<-.1 for s in scores)
        macro='偏多震荡' if bull>bear else '偏空承压' if bear>bull else '中性平衡'
    return {'schema':2,'source':'okx_official_news','connection_id':connection['id'],'connection_generation':generation,'auth_type':connection['auth_type'],
            'market_environment':'regular_market','connection_status':'fresh' if good else 'partial' if fetched else 'unavailable',
            'last_attempt_at':now,'last_success_at':last_success,'updated_at':stamp(last_success),'timestamp':stamp(last_success),
            'sections':sections,'stale_sections':not good,'latest_news':items,'coins_sentiment':score_rows,
            'macro_sentiment':macro,'message':'资讯连接独立于交易环境；更新时间表示全部资讯分区最近一次成功读取。'}


def for_strategy(payload, *, now=None):
    now=time.time() if now is None else now
    if payload.get('schema')!=2 or payload.get('connection_status') not in {'fresh','partial'}: return None
    recent=[]
    for name in ('latest','important'):
        section=payload.get('sections',{}).get(name,{})
        at=section.get('last_success_at')
        if section.get('status')=='fresh' and isinstance(at,(int,float)) and 0<=now-at<=MAX_AGE:
            recent.extend(section.get('data',[]))
    if not recent: return None
    filtered=copy.deepcopy(payload)
    ids={str(r.get('id')) for r in recent}
    filtered['latest_news']=[r for r in payload.get('latest_news',[]) if str(r.get('id')) in ids and 0<=now-r.get('source_at',0)<=86400]
    sentiment=payload.get('sections',{}).get('sentiment',{})
    if sentiment.get('status')!='fresh' or not 0<=now-sentiment.get('last_success_at',0)<=MAX_AGE:
        filtered['macro_sentiment']='UNKNOWN（情绪数据过期）'
    return filtered
