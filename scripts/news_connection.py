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
        except (connection_transport.ConnectionError,ValueError,KeyError,TypeError,OverflowError) as exc:
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


def _recent(section, now):
    if not isinstance(section, dict): return False
    at = section.get('last_success_at')
    return (section.get('status') == 'fresh' and isinstance(at, (int, float))
            and not isinstance(at, bool) and math.isfinite(at) and 0 <= now-at <= MAX_AGE)


def for_strategy(payload, *, now=None):
    """Read only fresh sections; display caches must not masquerade as strategy inputs."""
    now = time.time() if now is None else now
    if not isinstance(payload, dict) or payload.get('schema') != 2 or payload.get('connection_status') not in {'fresh', 'partial'}:
        return None
    sections = payload.get('sections') or {}
    if not isinstance(sections, dict): return None
    news = []; seen = set()
    for name in ('important', 'latest'):
        section = sections.get(name) or {}
        if not _recent(section, now):
            continue
        rows = section.get('data') or []
        if not isinstance(rows, list): continue
        for row in rows:
            if not isinstance(row, dict): continue
            try:
                at = float(row.get('cTime') or 0) / 1000
            except (TypeError, ValueError):
                continue
            if not math.isfinite(at) or not 0 <= now-at <= 86400:
                continue
            identity = str(row.get('id') or '')
            if not identity or identity in seen:
                continue
            seen.add(identity)
            news.append({'id': identity, 'time': stamp(at), 'source_at': at,
                         'title': str(row.get('title') or '')[:300], 'summary': str(row.get('summary') or '')[:1000],
                         'coins': row.get('ccyList') if isinstance(row.get('ccyList'), list) else [],
                         'importance': 'high' if name == 'important' else row.get('importance', 'unknown'),
                         'url': row.get('sourceUrl', '')})
    sentiment = sections.get('sentiment') or {}
    fresh_sentiment = _recent(sentiment, now) and isinstance(sentiment.get('data'), dict)
    if fresh_sentiment:
        fresh_sentiment = all(isinstance(row, dict) for row in sentiment['data'].values())
    if not news and not fresh_sentiment:
        return None
    filtered = copy.deepcopy(payload)
    filtered['sections'] = {key: value for key, value in sections.items() if isinstance(value, dict)}
    filtered['latest_news'] = sorted(news, key=lambda r: r['source_at'], reverse=True)
    # Clearing the stale score is essential: UNKNOWN macro text alone does not stop factor use.
    filtered['coins_sentiment'] = copy.deepcopy(sentiment.get('data') or {}) if fresh_sentiment else {}
    filtered['sentiment_fresh'] = fresh_sentiment
    filtered['sentiment_period'] = '24h'
    if not fresh_sentiment:
        filtered['macro_sentiment'] = 'UNKNOWN（情绪数据过期或不可用）'
    return filtered


def strategy_snapshot(payload, coins, *, now=None, limit=6):
    """Freeze bounded untrusted news context, selected from ALL fresh feed sections."""
    import hashlib
    import json
    import re
    now = time.time() if now is None else now
    filtered = for_strategy(payload, now=now) or {}
    targets = {str(c).upper() for c in coins}
    sections = filtered.get('sections') or {}
    def relevance(item):
        tagged = {str(c).upper() for c in item.get('coins', [])}
        title = item.get('title', '').upper()
        return bool(targets & tagged or any(re.search(r'(?<![A-Z0-9])' + re.escape(c) + r'(?![A-Z0-9])', title) for c in targets)
                    or re.search(r'比特币|以太坊|美联储|CPI|利率|非农|通胀|ETF|联储|FED|FOMC', title))
    def rank(item):
        # Keep relevance/importance, without letting yesterday's important item crowd out today's event.
        recent = int((now - item['source_at']) <= 7200)
        return (recent, int(relevance(item)) + int(item.get('importance') == 'high'), item['source_at'])
    selected = []; titles = set()
    for item in sorted(filtered.get('latest_news') or [], key=rank, reverse=True):
        normalized = re.sub(r'[\W_]+', '', item['title']).casefold()
        # Exact normalized-title dedup only: do not merge conflicting interpretations heuristically.
        if not normalized or normalized in titles:
            continue
        titles.add(normalized)
        selected.append({**item, 'summary': item['summary'][:400]})
        if len(selected) >= max(1, min(limit, 12)):
            break
    score_rows = {}
    for coin, row in (filtered.get('coins_sentiment') or {}).items():
        score = row.get('sentiment_factor_score')
        if coin in targets and row.get('available') is True and isinstance(score, (int, float)) and not isinstance(score, bool) and math.isfinite(score):
            score_rows[coin] = {k: row.get(k) for k in ('available', 'label', 'bullish_ratio', 'bearish_ratio', 'mentions', 'sentiment_factor_score')}
    stable_capture = filtered.get('last_success_at') or max((item.get('source_at', 0) for item in selected), default=0) or None
    snapshot = {'schema': 1, 'captured_at': stable_capture, 'source': 'okx_official_news',
                'connection_id': filtered.get('connection_id'), 'connection_generation': filtered.get('connection_generation'),
                'connection_status': filtered.get('connection_status', 'unavailable'),
                'macro_sentiment': filtered.get('macro_sentiment') or 'UNKNOWN',
                'sentiment_period': '24h', 'sentiment_fresh': bool(filtered.get('sentiment_fresh')),
                'section_freshness': {k: {'usable': _recent(sections.get(k) or {}, now),
                    'last_success_at': (sections.get(k) or {}).get('last_success_at')} for k in ('latest', 'important', 'sentiment')},
                'items': selected, 'coins_sentiment': score_rows,
                'selection': 'recent_relevant_important_then_time_exact_title_dedup_v1'}
    snapshot['digest'] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True, allow_nan=False).encode()).hexdigest()
    return snapshot


def load_strategy_snapshot(path, coins, *, now=None):
    """Read once and reject a cache belonging to a previous news connection binding."""
    import json
    import logging
    try:
        with open(path, encoding='utf8') as handle:
            payload = json.load(handle)
        if not isinstance(payload, dict):
            raise ValueError('invalid_news_cache')
        connection = account_connections.news_connection()
        if (payload.get('connection_id') != connection['id'] or
                payload.get('connection_generation', 0) != connection.get('generation', 0)):
            payload = {}
        return strategy_snapshot(payload, coins, now=now)
    except (OSError, ValueError, TypeError, KeyError, account_connections.AccountChangeError) as exc:
        logging.getLogger(__name__).warning('News context unavailable: %s', type(exc).__name__)
        return strategy_snapshot({}, coins, now=now)


def render_strategy_snapshot(snapshot):
    """Reported claims are USER data, not instructions or independently verified facts."""
    import json
    if not snapshot or (not snapshot.get('items') and not snapshot.get('sentiment_fresh')):
        return '无可验证新闻输入；不得据此推断市场平稳或不存在事件风险'
    return ('市场情报仅作上下文：24h 情绪标签不是当前小时涨跌预测，也不是开仓指令。'
            '新闻为来源报道，可能重复、矛盾或不准确；不得替代价格结构、成交量及风险校验。'
            '没有可用新闻不代表没有事件风险。\n' +
            json.dumps(snapshot, ensure_ascii=False, allow_nan=False, separators=(',', ':')))
