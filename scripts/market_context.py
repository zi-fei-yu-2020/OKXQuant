"""Bounded, source-labelled context. No invented cross-asset quotes or extra model calls."""
import re
from collections import Counter

VERSION='market-context-v1'
_MACRO=re.compile(r'美股|标普|纳斯达克|纳指|道琼斯|美联储|联储|非农|通胀|利率|美债|国债|美元指数|CPI|FOMC|FED|NASDAQ|S&P|SPX|DXY|VIX|payroll|treasury',re.I)
_OUTLOOK=re.compile(r'预测|预计|认为|或将|探索|forecast|expects|considering|plans to',re.I)
_NEGATIVE=re.compile(r'否认|辟谣|并未|尚未|不会|取消|denies|not yet|cancelled',re.I)


def category(item,coins):
    title=str(item.get('title') or '')
    if _MACRO.search(title):return 'macro_report'
    targets={str(c).upper() for c in coins}
    if targets & {str(c).upper() for c in item.get('coins',[])}:return 'asset_report'
    if any(re.search(r'(?<![A-Z0-9])'+re.escape(c)+r'(?![A-Z0-9])',title.upper()) for c in targets):return 'asset_report'
    return 'other_crypto_report'


def _tokens(text):
    latin=set(re.findall(r'[a-z]{3,}',text.lower()))-{'the','and','for','with','that','this','from'}
    pairs=set()
    for part in re.findall(r'[\u4e00-\u9fff]+',text):pairs.update(part[i:i+2] for i in range(len(part)-1))
    return latin|pairs


def related(a,b):
    """Conservative display grouping, not independent verification or fact equivalence."""
    if abs(float(a['source_at'])-float(b['source_at']))>900:return False
    left=str(a.get('title') or '')+' '+str(a.get('summary') or '')
    right=str(b.get('title') or '')+' '+str(b.get('summary') or '')
    if set(re.findall(r'\d+(?:\.\d+)?',left))!=set(re.findall(r'\d+(?:\.\d+)?',right)):return False
    if bool(_NEGATIVE.search(left))!=bool(_NEGATIVE.search(right)):return False
    if bool(_OUTLOOK.search(left))!=bool(_OUTLOOK.search(right)):return False
    x,y=_tokens(left),_tokens(right)
    return min(len(x),len(y))>=12 and len(x&y)/min(len(x),len(y))>=.85


def select_reports(items,coins,now,limit=6):
    """Cover a recent macro and relevant-asset report before filling by recency/importance."""
    limit=max(1,min(int(limit),12));groups=[];seen=set();exact_duplicates=0
    def rank(item):
        recent=int(0<=now-item['source_at']<=7200)
        return (recent,int(category(item,coins)!='other_crypto_report')+int(item.get('importance')=='high'),item['source_at'])
    for original in sorted(items,key=rank,reverse=True):
        title=re.sub(r'[\W_]+','',str(original.get('title') or '')).casefold()
        if not title:continue
        # Equal titles can still carry conflicting numeric claims. Preserve those.
        key=(title,tuple(sorted(re.findall(r'\d+(?:\.\d+)?',str(original.get('summary') or '')))),bool(_NEGATIVE.search(str(original.get('summary') or ''))))
        if key in seen:exact_duplicates+=1;continue
        seen.add(key)
        item={**original,'summary':str(original.get('summary') or '')[:400],
              'context_category':category(original,coins),'claim_status':'source_report_not_independently_verified',
              'contains_outlook_language':bool(_OUTLOOK.search(str(original.get('title') or '')+' '+str(original.get('summary') or '')))}
        parent=next((g for g in groups if related(g,item)),None)
        if parent is not None:
            parent.setdefault('_related',[]).append(item)
        else:groups.append(item)
    chosen=[]
    for wanted in ('macro_report','asset_report'):
        match=next((g for g in groups if g['context_category']==wanted and 0<=now-g['source_at']<=7200),None)
        if match is not None and len(chosen)<limit:chosen.append(match)
    for g in groups:
        if len(chosen)>=limit:break
        if not any(g is x for x in chosen):chosen.append(g)
    result=[];extra_budget=2
    for g in chosen:
        item={k:v for k,v in g.items() if k!='_related'};links=g.get('_related') or []
        if links:
            take=links[:extra_budget];extra_budget-=len(take)
            item['related_reports']=[{k:x.get(k) for k in ('id','title','summary','source_at','url','claim_status','contains_outlook_language')} for x in take]
            item['related_report_count']=len(links)
            item['grouping_note']='Similar reports grouped for coverage, not independent confirmations; differences remain unverified.'
        result.append(item)
    return result,{'input_articles':len(items),'selected_primary_articles':len(result),
                   'related_reports_included':2-extra_budget,'exact_duplicates':exact_duplicates,
                   'categories':dict(Counter(i['context_category'] for i in result)),
                   'method':'recent_macro_asset_coverage_with_conservative_related_reports_v1'}


def context(snapshot, macro_snapshot=None):
    items=(snapshot or {}).get('items') or []
    result = {'version':VERSION,'news_digest':(snapshot or {}).get('digest'),
            'news_status':(snapshot or {}).get('connection_status','unavailable'),
            'provided_articles':len(items),'sentiment_available':bool((snapshot or {}).get('sentiment_fresh')),
            'categories':dict(Counter(i.get('context_category','unclassified_report') for i in items)),
            'cross_asset_quotes':{'status':'not_connected','values':{}},
            'economic_release_values':{'status':'not_connected','values':{}},
            'price_macro_semantics':'macro_4h is crypto price structure, not equity/dollar/rates market data',
            'missing_data_policy':'unknown, never zero, bullish or evidence of no event risk'}
    from scripts.macro_market import VERSION as MACRO_VERSION
    if macro_snapshot and macro_snapshot.get('version') == MACRO_VERSION:
        result['interest_rates'] = macro_snapshot.get('rates') or {'status': 'unavailable'}
        result['macro_feeds'] = macro_snapshot
    return result



def usage_receipt(snapshot,decisions,catalogs,macro_snapshot=None):
    """Validated citations only; neither attention weights nor causal impact are inferred."""
    items=(snapshot or {}).get('items') or [];cited={};sentiment=set();macro_refs={}
    def refs(value,depth=0):
        if depth>12:return
        if isinstance(value,dict):
            if isinstance(value.get('ref'),str):yield value
            for k,v in value.items():
                if k not in {'raw_proposal','entry_plans','raw_response'}:yield from refs(v,depth+1)
        elif isinstance(value,list):
            for v in value[:100]:yield from refs(v,depth+1)
    for inst,decision in decisions.items():
        if not isinstance(decision,dict) or decision.get('contract_valid') is not True:continue
        catalog=catalogs.get(inst) or {}
        reviewed={k:decision.get(k) for k in ('supporting_evidence','counter_evidence','candidate_reviews','wait_audit')}
        for item in refs(reviewed):
            ref=item['ref'];fact=catalog.get(ref)
            if not isinstance(fact,dict) or type(item.get('value')) is bool or item.get('value')!=fact.get('value'):continue
            match=re.fullmatch(r'/news/articles/(\d+)/(?:id|title|source_at|importance)',ref)
            if match and int(match[1])<len(items):
                identifier=items[int(match[1])].get('id')
                if isinstance(identifier,str) and identifier:cited.setdefault(identifier,set()).add(inst)
            elif ref.startswith('/news/sentiment/'):sentiment.add(inst)
            elif ref.startswith('/macro/'):macro_refs.setdefault(ref,set()).add(inst)
    status='cited' if cited or sentiment else 'provided_without_structured_citation' if items or (snapshot or {}).get('sentiment_fresh') else 'unavailable'
    result = {'version':VERSION,'status':status,'provided_article_count':len(items),
            'cited_article_count':len(cited),'cited_articles':[{'id':k,'instruments':sorted(v)} for k,v in sorted(cited.items())],
            'sentiment_cited_by':sorted(sentiment),'snapshot_digest':(snapshot or {}).get('digest'),
            'semantics':'validated_reference_usage_not_internal_weights_or_causal_contribution',
            'decision_stage':'model_contract_validation_before_execution',
            'cross_asset_quotes_status':'not_connected','economic_release_values_status':'not_connected'}

    from scripts.macro_market import VERSION as MACRO_VERSION, SOURCES
    if macro_snapshot and macro_snapshot.get('version') == MACRO_VERSION:
        from scripts.macro_market import facts
        supplied = facts(macro_snapshot)
        verified_refs = {k: v for k, v in macro_refs.items() if k in supplied}
        result.update({'macro_snapshot_digest': macro_snapshot.get('digest'),
            'provided_macro_fact_count': len(supplied), 'cited_macro_fact_count': len(verified_refs),
            'cited_macro_refs': [{'ref': k, 'instruments': sorted(v)} for k, v in sorted(verified_refs.items())],
            'macro_status': 'cited' if verified_refs else 'provided_without_structured_citation' if supplied else 'unavailable',
            'macro_source_statuses': {k: {'status': v.get('status'), 'usable': v.get('usable')} for k, v in (macro_snapshot.get('sources') or {}).items() if k in SOURCES},
            'cross_asset_quotes_status': context(snapshot, macro_snapshot)['cross_asset_quotes']['status'],
            'interest_rates_status': (macro_snapshot.get('rates') or {}).get('status', 'unavailable'),
            'economic_release_values_status': context(snapshot, macro_snapshot)['economic_release_values']['status']})
    return result
