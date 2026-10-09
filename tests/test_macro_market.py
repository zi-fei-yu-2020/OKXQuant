import copy
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts import macro_market as m
from scripts import market_context, trading_prompt

NOW = datetime(2026, 10, 9, 17, 0, tzinfo=timezone.utc).timestamp()
CONFIG = {'binding': 'test-binding', 'official_enabled': True, 'fmp_enabled': True, 'api_key': 'private-test-key', 'calendar_timezone': 'UTC'}
XML = '''<feed xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" xmlns:d="http://schemas.microsoft.com/ado/2007/08/dataservices">
<m:properties><d:NEW_DATE>2026-10-08T00:00:00</d:NEW_DATE><d:BC_2YEAR>4.2</d:BC_2YEAR><d:BC_10YEAR>4.5</d:BC_10YEAR></m:properties>
<m:properties><d:NEW_DATE>2026-10-09T00:00:00</d:NEW_DATE><d:BC_2YEAR>4.25</d:BC_2YEAR><d:BC_10YEAR>4.4</d:BC_10YEAR></m:properties></feed>'''
ICS = '''BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
UID:bea-test
SUMMARY:Personal Income and Outlays\\, September
 2026
DTSTART:20261010T123000Z
END:VEVENT
END:VCALENDAR'''
CATALOG = [{'symbol': '^GSPC', 'name': 'S&P 500'}, {'symbol': '^IXIC', 'name': 'NASDAQ Composite'}, {'symbol': '^DXY', 'name': 'US Dollar Index'}]


def source(data, at=NOW):
    return {'status': 'ok', 'last_success_at': at, 'last_attempt_at': at, 'next_attempt_at': at+900, 'data': data}


def cache(sources):
    return {'version': m.VERSION, 'binding': CONFIG['binding'], 'sources': sources}


def getter(url, *, params=None, deadline=None):
    if url == m.TREASURY_URL:
        return XML
    if url in m.CALENDAR_URLS.values():
        return ICS
    if url.endswith('index-list'):
        return json.dumps(CATALOG)
    if url.endswith('quote'):
        return json.dumps([{'symbol': params['symbol'], 'price': 101, 'previousClose': 100, 'timestamp': NOW-60}])
    if url.endswith('economic-calendar'):
        return json.dumps([{'country': 'US', 'event': 'CPI', 'date': '2026-10-10 12:30:00', 'actual': 3.5, 'estimate': 3.3, 'previous': 3.2, 'unit': '%'}])
    raise AssertionError(url)


class MacroAdapterTests(unittest.TestCase):
    def test_ambiguous_and_nonexistent_wall_times_are_rejected(self):
        for stamp in ('2026-11-01T01:30:00', '2026-03-08T02:30:00'):
            with self.subTest(stamp=stamp), self.assertRaises(ValueError):
                m.localize(datetime.fromisoformat(stamp), 'America/New_York')

    def test_date_only_economic_release_time_is_not_guessed(self):
        with self.assertRaises(m.FeedError):
            m.parse_fmp_calendar([{'country':'US','event':'CPI','date':'2026-10-10'}], NOW, 'UTC')

    def test_treasury_units_and_change(self):
        out = m.parse_treasury(XML, NOW)
        self.assertEqual(out['us2y_pct'], 4.25)
        self.assertEqual(out['us2y_change_bp'], 5)
        self.assertEqual(out['us10y_change_bp'], -10)
        self.assertEqual(out['spread_10y_2y_bp'], 15)
        self.assertIsNone(out['published_at'])
        self.assertEqual(out['frequency'], 'daily')

    def test_treasury_future_day_and_nan_are_not_accepted(self):
        self.assertEqual(m.parse_treasury(XML.replace('2026-10-09', '2026-10-12'), NOW)['observation_date'], '2026-10-08')
        with self.assertRaises(m.FeedError):
            m.parse_treasury(XML.replace('4.2', 'NaN'), NOW)
        with self.assertRaises(m.FeedError):
            m.parse_treasury('<!DOCTYPE x>'+XML, NOW)

    def test_calendar_unfolds_and_never_manufactures_actual(self):
        out = m.parse_ics(ICS, 'bea', NOW)
        self.assertEqual(out['events'][0]['event'], 'Personal Income and Outlays, September2026')
        self.assertIsNone(out['events'][0]['actual'])
        self.assertEqual(out['events'][0]['importance'], 'high')
        self.assertEqual(out['events'][0]['scheduled_at'], datetime(2026,10,10,12,30,tzinfo=timezone.utc).timestamp())

    def test_floating_and_all_day_times_are_not_guessed(self):
        for value in ('20261010T123000', '20261010'):
            with self.subTest(value=value), self.assertRaises(m.FeedError):
                m.parse_ics(ICS.replace('20261010T123000Z', value), 'bea', NOW)

    def test_new_york_dst_is_resolved_from_iana_timezone(self):
        out = m.parse_ics(ICS.replace('DTSTART:20261010T123000Z', 'DTSTART;TZID=America/New_York:20261010T083000'), 'bls', NOW)
        self.assertEqual(out['events'][0]['scheduled_at'], datetime(2026,10,10,12,30,tzinfo=timezone.utc).timestamp())

    def test_cancelled_or_recurring_events_not_silently_expanded(self):
        for extra in ('STATUS:CANCELLED', 'RRULE:FREQ=DAILY'):
            with self.subTest(extra=extra), self.assertRaises(m.FeedError):
                m.parse_ics(ICS.replace('END:VEVENT', extra+'\nEND:VEVENT'), 'bea', NOW)

    def test_index_identity_no_etf_broad_dollar_or_futures_substitution(self):
        rows = CATALOG[:2] + [{'symbol': 'UUP', 'name': 'US Dollar ETF'}, {'symbol': 'DTWEXBGS', 'name': 'Trade Weighted US Dollar Index'}, {'symbol': 'DX', 'name': 'US Dollar Index Futures'}]
        self.assertNotIn('dxy', m.resolve_symbols(rows))
        self.assertEqual(m.resolve_symbols(CATALOG)['dxy'], '^DXY')
        self.assertNotIn('dxy', m.resolve_symbols(CATALOG+[{'name':'US Dollar Index', 'symbol':'OTHER'}]))

    def test_quote_identity_time_and_missing_previous_close(self):
        with self.assertRaises(m.FeedError):
            m.parse_quote([{'symbol':'QQQ','price':10,'timestamp':NOW}], '^IXIC', NOW)
        with self.assertRaises(m.FeedError):
            m.parse_quote([{'symbol':'^IXIC','price':10,'timestamp':NOW+120}], '^IXIC', NOW)
        out = m.parse_quote([{'symbol':'^IXIC','price':10,'timestamp':NOW}], '^IXIC', NOW)
        self.assertIsNone(out['change_pct'])
        self.assertEqual(out['delivery'], 'provider_delay_unverified')
        for value in (True, 'NaN', float('inf')):
            with self.assertRaises(m.FeedError):
                m.parse_quote([{'symbol':'^IXIC','price':value,'timestamp':NOW}], '^IXIC', NOW)

    def test_fmp_http200_error_is_not_success(self):
        for text in ('{"Error Message":"private-test-key invalid"}', '<html>error</html>', 'null', '[1]'):
            with self.subTest(text=text), self.assertRaises(m.FeedError) as raised:
                m.fmp_json('quote', 'private-test-key', getter=lambda *a, **k: text)
            self.assertNotIn('private-test-key', str(raised.exception))

    def test_fmp_calendar_unverified_timezone_is_rejected(self):
        rows = [{'country':'US', 'event':'CPI', 'date':'2026-10-10 12:30:00'}]
        with self.assertRaises(m.FeedError):
            m.parse_fmp_calendar(rows, NOW)
        rows[0]['date'] = '2026-10-10T12:30:00Z'
        self.assertEqual(len(m.parse_fmp_calendar(rows, NOW)['events']), 1)

    def test_fmp_future_actual_is_removed_zero_and_missing_remain_distinct(self):
        rows=[{'country':'US','event':'CPI','date':'2026-10-10 12:30:00','actual':99,'estimate':0,'previous':None,'unit':'%'},
              {'country':'US','event':'GDP','date':'2026-10-09 12:30:00','actual':0,'estimate':None}]
        result=m.parse_fmp_calendar(rows,NOW,'UTC')['events']
        future=next(x for x in result if x['event']=='CPI');past=next(x for x in result if x['event']=='GDP')
        self.assertIsNone(future['actual']);self.assertEqual(future['estimate'],0)
        self.assertIsNone(future['previous']);self.assertEqual(past['actual'],0)
        self.assertIsNone(past['unit'])

    def test_collector_connects_each_source_without_key_in_cache(self):
        result=m.collect(CONFIG,now=NOW,getter=getter)
        self.assertTrue(all(v['status']=='ok' for v in result['sources'].values()))
        self.assertEqual(len(result['sources']['fmp_indices']['data']['quotes']),3)
        self.assertNotIn(CONFIG['api_key'],json.dumps(result))
        self.assertEqual(m.snapshot(result,CONFIG,now=NOW)['rates']['status'],'daily_reference')

    def test_official_sources_work_without_key(self):
        calls=[]
        def spy(url,**kwargs):
            calls.append(url);return getter(url,**kwargs)
        result=m.collect({**CONFIG,'api_key':''},now=NOW,getter=spy)
        self.assertEqual(result['sources']['fmp_indices']['status'],'not_configured')
        self.assertTrue(all(not u.startswith(m.FMP_BASE) for u in calls))

    def test_backoff_and_cache_prevent_repeated_probe_requests(self):
        result=m.collect(CONFIG,now=NOW,getter=getter)
        with patch.object(m,'fetch',side_effect=AssertionError('network forbidden')):
            out=m.collect(CONFIG,result,now=NOW+10,getter=lambda *a,**k: self.fail('cached'))
        self.assertEqual(result['sources'],out['sources'])
        def failed(*a,**k):raise m.FeedError('access_denied')
        failed_state=m.collect(CONFIG,now=NOW,getter=failed)
        self.assertEqual(failed_state['sources']['bea']['next_attempt_at'],NOW+3600)
        m.collect(CONFIG,failed_state,now=NOW+60,getter=lambda *a,**k:self.fail('backoff ignored'))

    def test_month_boundary_falls_back_only_for_empty_current_month(self):
        now=datetime(2026,11,1,12,tzinfo=timezone.utc).timestamp();calls=[]
        def month(url,**kwargs):
            if url==m.TREASURY_URL:
                calls.append(kwargs['params']['field_tdr_date_value_month'])
                return '<feed />' if len(calls)==1 else XML
            return getter(url,**kwargs)
        m.collect({**CONFIG,'fmp_enabled':False},now=now,getter=month)
        self.assertEqual(calls,['202611','202610'])


class MacroTransportTests(unittest.TestCase):
    class Response:
        def __init__(self,status=200,chunks=(b'ok',)):self.status_code=status;self.chunks=chunks;self.closed=False
        def __enter__(self):return self
        def __exit__(self,*args):self.closed=True
        def iter_content(self,size):yield from self.chunks
    def test_transport_does_not_follow_redirects_and_closes_response(self):
        response=self.Response()
        with patch.object(m.requests,'get',return_value=response) as get:
            self.assertEqual(m.fetch(m.TREASURY_URL),'ok')
        self.assertFalse(get.call_args.kwargs['allow_redirects']);self.assertTrue(response.closed)
        self.assertEqual(get.call_args.kwargs['timeout'],(4,8))
        with self.assertRaises(m.FeedError):m.fetch('https://untrusted.invalid/')
    def test_credential_bearing_exception_is_sanitized(self):
        with patch.object(m.requests,'get',side_effect=m.requests.RequestException('apikey=secret-test-123')):
            with self.assertRaises(m.FeedError) as e:m.fetch(m.FMP_BASE+'quote')
        self.assertEqual(str(e.exception),'transport_error')
    def test_http_status_and_body_limits_are_not_success(self):
        for status,code in [(302,'http_error'),(403,'access_denied'),(429,'rate_limited')]:
            response=self.Response(status)
            with patch.object(m.requests,'get',return_value=response),self.assertRaises(m.FeedError) as e:
                m.fetch(m.TREASURY_URL)
            self.assertEqual(str(e.exception),code);self.assertTrue(response.closed)
        response=self.Response(chunks=(b'x'*2_000_001,))
        with patch.object(m.requests,'get',return_value=response),self.assertRaises(m.FeedError) as e:
            m.fetch(m.TREASURY_URL)
        self.assertEqual(str(e.exception),'response_too_large');self.assertTrue(response.closed)


class MacroSnapshotTests(unittest.TestCase):
    def setUp(self):self.collected=m.collect(CONFIG,now=NOW,getter=getter)
    def test_aged_quotes_never_enter_facts_even_when_refresh_is_recent(self):
        raw=self.collected['sources']['fmp_indices']['data']['quotes']
        for q in raw.values():q['as_of']=NOW-3600
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        self.assertEqual(out['quotes']['dxy']['status'],'stale_or_market_closed')
        self.assertFalse(any(r.startswith('/macro/quotes/') for r in m.facts(out)))
        self.assertTrue(any(r.startswith('/macro/rates/') for r in m.facts(out)))
    def test_cached_after_error_does_not_renew_data_time(self):
        raw=self.collected['sources']['treasury'];raw.update(status='error',error='timeout',last_attempt_at=NOW+30)
        out=m.snapshot(self.collected,CONFIG,now=NOW+30)
        self.assertEqual(out['sources']['treasury']['status'],'cached_after_error')
        self.assertEqual(out['sources']['treasury']['last_success_at'],NOW)
    def test_stale_rates_and_stale_source_excluded(self):
        self.collected['sources']['treasury']['data']['observation_date']='2026-09-01'
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        self.assertFalse(out['rates']['usable'])
        self.assertFalse(any(r.startswith('/macro/rates/') for r in m.facts(out)))
        out=m.snapshot(self.collected,CONFIG,now=NOW+86401)
        self.assertEqual(m.facts(out),{})
    def test_stale_calendar_coverage_is_not_called_current(self):
        self.collected['sources']['bea']['data']['coverage_end']=NOW-100
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        self.assertEqual(out['sources']['bea']['status'],'outdated_schedule')
    def test_binding_change_drops_previous_credentials_cache(self):
        out=m.snapshot(self.collected,{**CONFIG,'binding':'new-key'},now=NOW)
        self.assertEqual(m.facts(out),{})
    def test_disabled_feeds_never_leak_cached_evidence(self):
        out=m.snapshot(self.collected,{**CONFIG,'official_enabled':False,'fmp_enabled':False},now=NOW)
        self.assertEqual(m.facts(out),{})
    def test_snapshot_does_not_mutate_cache(self):
        before=copy.deepcopy(self.collected);m.snapshot(self.collected,CONFIG,now=NOW+60)
        self.assertEqual(self.collected,before)
    def test_prompt_facts_receipt_counts_only_validated_macro_refs(self):
        out=m.snapshot(self.collected,CONFIG,now=NOW);catalog=m.facts(out)
        package={'instId':'BTC-USDT-SWAP','macro_snapshot':out}
        self.assertEqual(trading_prompt.facts_for(package)['/macro/quotes/dxy/price']['group'],'macro')
        ref='/macro/quotes/dxy/price';value=catalog[ref]['value']
        decisions={'BTC':{'contract_valid':True,'supporting_evidence':[{'ref':ref,'value':value}]}}
        receipt=market_context.usage_receipt({},decisions,{'BTC':catalog},out)
        self.assertEqual(receipt['cited_macro_fact_count'],1)
        self.assertEqual(receipt['cross_asset_quotes_status'],'available')
        decisions['BTC']['contract_valid']=False
        self.assertEqual(market_context.usage_receipt({},decisions,{'BTC':catalog},out)['cited_macro_fact_count'],0)
    def test_raw_or_unknown_macro_citations_do_not_count(self):
        out=m.snapshot(self.collected,CONFIG,now=NOW);catalog=m.facts(out)
        ref='/macro/quotes/dxy/price';value=catalog[ref]['value']
        d={'BTC':{'contract_valid':True,'raw_proposal':{'ref':ref,'value':value}}}
        self.assertEqual(market_context.usage_receipt({},d,{'BTC':catalog},out)['cited_macro_fact_count'],0)
        d['BTC']['supporting_evidence']=[{'ref':ref,'value':value+1}]
        self.assertEqual(market_context.usage_receipt({},d,{'BTC':catalog},out)['cited_macro_fact_count'],0)
    def test_macro_cannot_replace_the_two_technical_evidence_groups(self):
        from test_trading_prompt_contract import package, candidate, response, ref
        pkg=package();pkg['macro_snapshot']=m.snapshot(self.collected,CONFIG,now=NOW)
        proposal=candidate()
        proposal['supporting_evidence']=[ref('/macro_4h','4H_MACRO_BULL'),ref('/macro/quotes/dxy/price',101)]
        result=trading_prompt.validate_response(response(proposal),[pkg])['decisions'][pkg['instId']]
        self.assertFalse(result['contract_valid'])
        self.assertIn('Insufficient evidence groups',result['validation_reason'])

    def test_manifest_freezes_macro_snapshot_and_keeps_it_as_user_data(self):
        from test_trading_prompt_contract import package
        pkg=package();pkg['macro_snapshot']=m.snapshot(self.collected,CONFIG,now=NOW)
        bundle=trading_prompt.compose({'id':'test'}, {'market_context':market_context.context({},pkg['macro_snapshot'])}, [pkg])
        self.assertIn('/macro/rates/us10y_pct',bundle.user)
        self.assertIn('macro_snapshot',bundle.manifest)
        before=bundle.manifest['macro_snapshot']['rates']['us10y_pct']
        pkg['macro_snapshot']['rates']['us10y_pct']=999
        self.assertEqual(bundle.manifest['macro_snapshot']['rates']['us10y_pct'],before)

    def test_shared_macro_facts_are_transmitted_once_but_valid_for_each_coin(self):
        from test_trading_prompt_contract import package
        pkg=package();pkg['macro_snapshot']=m.snapshot(self.collected,CONFIG,now=NOW)
        other=copy.deepcopy(pkg);other['instId']='ETH-USDT-SWAP';other['name']='ETH'
        bundle=trading_prompt.compose({'id':'test'}, {}, [pkg,other])
        payload=json.JSONDecoder().raw_decode(bundle.user)[0]
        self.assertIn('/macro/quotes/dxy/price',payload['shared_macro_facts'])
        self.assertNotIn('/macro/quotes/dxy/price',payload['facts'][pkg['instId']])
        self.assertIn('/macro/quotes/dxy/price',trading_prompt.facts_for(other))
        other['macro_snapshot']['quotes']['dxy']['price']=99
        payload=json.JSONDecoder().raw_decode(trading_prompt.compose({'id':'test'},{},[pkg,other]).user)[0]
        self.assertEqual(payload['shared_macro_facts'],{})
        self.assertEqual(payload['facts'][other['instId']]['/macro/quotes/dxy/price']['value'],99)

    def test_unavailable_snapshot_is_deterministic_and_claims_no_capture(self):
        left=m.snapshot({},CONFIG,now=NOW);right=m.snapshot({},CONFIG,now=NOW+1)
        self.assertEqual(left,right);self.assertIsNone(left['captured_at'])

    def test_context_no_news_values_are_invented(self):
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        ctx=market_context.context({},out)
        self.assertEqual(ctx['economic_release_values']['status'],'no_verified_values')
        self.assertTrue(ctx['macro_feeds']['events'])


if __name__=='__main__':unittest.main()
