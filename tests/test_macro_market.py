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
CONFIG = {'binding': 'test-binding', 'official_enabled': True}
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

def getter(url, *, params=None, deadline=None):
    if url == m.TREASURY_URL:return XML
    if url in m.CALENDAR_URLS.values():return ICS
    raise AssertionError('Unexpected non-official request')

class OfficialMacroTests(unittest.TestCase):
    def setUp(self):self.collected=m.collect(CONFIG,now=NOW,getter=getter)
    def test_only_official_sources_are_collected_and_snapshotted(self):
        self.assertEqual(set(self.collected['sources']),{'treasury','bea','bls'})
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        self.assertEqual(set(out['sources']),{'treasury','bea','bls'})
        self.assertNotIn('quotes',out)
        self.assertEqual(out['cross_asset_quotes']['status'],'not_connected')
    def test_legacy_flags_and_environment_cannot_restore_provider_calls(self):
        calls=[]
        def spy(url,**kwargs):calls.append(url);return getter(url,**kwargs)
        with patch.dict('os.environ',{'FMP_API_KEY':'do-not-use'}):
            out=m.collect({**CONFIG,'fmp_enabled':True,'api_key':'do-not-use'},now=NOW,getter=spy)
        self.assertEqual(set(calls),{m.TREASURY_URL,*m.CALENDAR_URLS.values()})
        self.assertNotIn('do-not-use',json.dumps(out))
    def test_treasury_dates_units_changes_and_spread(self):
        out=m.parse_treasury(XML,NOW)
        self.assertEqual(out['us2y_pct'],4.25);self.assertEqual(out['us2y_change_bp'],5)
        self.assertEqual(out['us10y_change_bp'],-10);self.assertEqual(out['spread_10y_2y_bp'],15)
        self.assertEqual(out['frequency'],'daily');self.assertIsNone(out['published_at'])
    def test_future_dates_nonfinite_and_entities_are_rejected(self):
        self.assertEqual(m.parse_treasury(XML.replace('2026-10-09','2026-10-12'),NOW)['observation_date'],'2026-10-08')
        for text in (XML.replace('4.2','NaN'),'<!DOCTYPE x>'+XML):
            with self.assertRaises(m.FeedError):m.parse_treasury(text,NOW)
    def test_calendar_is_schedule_only_and_unfolded(self):
        out=m.parse_ics(ICS,'bea',NOW)['events'][0]
        self.assertEqual(out['event'],'Personal Income and Outlays, September2026')
        self.assertNotIn('actual',out);self.assertNotIn('estimate',out)
        self.assertEqual(out['importance'],'high')
    def test_floating_all_day_cancelled_recurring_are_not_guessed(self):
        for value in (ICS.replace('20261010T123000Z','20261010'),ICS.replace('20261010T123000Z','20261010T123000'),ICS.replace('END:VEVENT','STATUS:CANCELLED\nEND:VEVENT'),ICS.replace('END:VEVENT','RRULE:FREQ=DAILY\nEND:VEVENT')):
            with self.assertRaises(m.FeedError):m.parse_ics(value,'bea',NOW)
    def test_dst_and_explicit_calendar_times(self):
        for stamp in ('2026-11-01T01:30:00','2026-03-08T02:30:00'):
            with self.assertRaises(ValueError):m.localize(datetime.fromisoformat(stamp),'America/New_York')
        out=m.parse_ics(ICS.replace('DTSTART:20261010T123000Z','DTSTART;TZID=America/New_York:20261010T083000'),'bea',NOW)
        self.assertEqual(out['events'][0]['scheduled_at'],datetime(2026,10,10,12,30,tzinfo=timezone.utc).timestamp())
    def test_backoff_does_not_retry_early(self):
        m.collect(CONFIG,self.collected,now=NOW+10,getter=lambda *a,**k:self.fail('cached request'))
        def fail(*a,**k):raise m.FeedError('access_denied')
        failed=m.collect(CONFIG,now=NOW,getter=fail)
        m.collect(CONFIG,failed,now=NOW+60,getter=lambda *a,**k:self.fail('backoff bypassed'))
        self.assertEqual(failed['sources']['bea']['next_attempt_at'],NOW+3600)
    def test_month_boundary_empty_feed_falls_back_once(self):
        now=datetime(2026,11,1,12,tzinfo=timezone.utc).timestamp();calls=[]
        def month(url,**kwargs):
            if url==m.TREASURY_URL:
                calls.append(kwargs['params']['field_tdr_date_value_month'])
                return '<feed />' if len(calls)==1 else XML
            return getter(url,**kwargs)
        m.collect(CONFIG,now=now,getter=month);self.assertEqual(calls,['202611','202610'])
    def test_old_cache_schema_is_rejected_even_with_matching_binding(self):
        old=copy.deepcopy(self.collected);old['version']='macro-feeds-v1'
        old['sources']['fmp_indices']={'data':{'quotes':{'dxy':{'price':99,'usable':True}}}}
        out=m.snapshot(old,CONFIG,now=NOW);self.assertEqual(m.facts(out),{})
        fresh=m.collect(CONFIG,old,now=NOW,getter=getter)
        self.assertNotIn('fmp_indices',fresh['sources']);self.assertNotIn('index_catalog',fresh)
    def test_retired_quote_and_release_values_never_become_evidence(self):
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        out['quotes']={'dxy':{'price':99,'usable':True}}
        out['events'][0].update(actual=99,estimate=88,previous=77)
        catalog=m.facts(out)
        self.assertFalse(any('/quotes/' in ref or ref.endswith(('/actual','/estimate','/previous')) for ref in catalog))
        self.assertEqual(m.facts({'version':'macro-feeds-v1','rates':{'usable':True,'us2y_pct':9}}),{})
        context=market_context.context({},out)
        self.assertEqual(context['cross_asset_quotes']['status'],'not_connected')
        self.assertEqual(context['economic_release_values']['status'],'not_connected')
    def test_snapshot_strips_unexpected_calendar_value_fields(self):
        self.collected['sources']['bea']['data']['events'][0]['actual']=99
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        self.assertTrue(all('actual' not in e for e in out['events']))
    def test_stale_rates_and_calendar_coverage_are_not_current(self):
        self.collected['sources']['treasury']['data']['observation_date']='2026-09-01'
        self.collected['sources']['bea']['data']['coverage_end']=NOW-1
        out=m.snapshot(self.collected,CONFIG,now=NOW)
        self.assertFalse(out['rates']['usable']);self.assertEqual(out['sources']['bea']['status'],'outdated_schedule')
        self.assertEqual(m.facts(m.snapshot(self.collected,CONFIG,now=NOW+86401)),{})
    def test_cached_after_error_does_not_renew_success_time(self):
        self.collected['sources']['treasury'].update(status='error',error='timeout',last_attempt_at=NOW+20)
        out=m.snapshot(self.collected,CONFIG,now=NOW+30)
        self.assertEqual(out['sources']['treasury']['status'],'cached_after_error')
        self.assertEqual(out['sources']['treasury']['last_success_at'],NOW)
    def test_disabled_changed_binding_and_empty_snapshot(self):
        self.assertEqual(m.facts(m.snapshot(self.collected,{**CONFIG,'official_enabled':False},now=NOW)),{})
        self.assertEqual(m.facts(m.snapshot(self.collected,{**CONFIG,'binding':'changed'},now=NOW)),{})
        self.assertEqual(m.snapshot({},CONFIG,now=NOW),m.snapshot({},CONFIG,now=NOW+1))
    def test_snapshot_does_not_mutate_cache(self):
        before=copy.deepcopy(self.collected);m.snapshot(self.collected,CONFIG,now=NOW+60)
        self.assertEqual(self.collected,before)
    def test_official_fact_citations_require_validated_matching_evidence(self):
        snap=m.snapshot(self.collected,CONFIG,now=NOW);catalog=m.facts(snap);ref='/macro/rates/us2y_pct'
        d={'BTC':{'contract_valid':True,'supporting_evidence':[{'ref':ref,'value':4.25}]}}
        self.assertEqual(market_context.usage_receipt({},d,{'BTC':catalog},snap)['cited_macro_fact_count'],1)
        d['BTC']['supporting_evidence'][0]['value']=99
        self.assertEqual(market_context.usage_receipt({},d,{'BTC':catalog},snap)['cited_macro_fact_count'],0)
        d['BTC']['raw_proposal']={'ref':ref,'value':4.25}
        self.assertEqual(market_context.usage_receipt({},d,{'BTC':catalog},snap)['cited_macro_fact_count'],0)
    def test_macro_cannot_replace_technical_evidence(self):
        from test_trading_prompt_contract import package,candidate,response,ref
        pkg=package();pkg['macro_snapshot']=m.snapshot(self.collected,CONFIG,now=NOW)
        c=candidate();c['supporting_evidence']=[ref('/macro_4h','4H_MACRO_BULL'),ref('/macro/rates/us2y_pct',4.25)]
        out=trading_prompt.validate_response(response(c),[pkg])['decisions'][pkg['instId']]
        self.assertFalse(out['contract_valid'])
    def test_prompt_shares_official_facts_once_and_freezes_manifest(self):
        from test_trading_prompt_contract import package
        pkg=package();pkg['macro_snapshot']=m.snapshot(self.collected,CONFIG,now=NOW)
        other=copy.deepcopy(pkg);other['instId']='ETH-USDT-SWAP'
        bundle=trading_prompt.compose({'id':'test'},{},[pkg,other]);payload=json.JSONDecoder().raw_decode(bundle.user)[0]
        self.assertIn('/macro/rates/us2y_pct',payload['shared_macro_facts'])
        self.assertNotIn('/macro/rates/us2y_pct',payload['facts'][pkg['instId']])
        pkg['macro_snapshot']['rates']['us2y_pct']=99
        self.assertEqual(bundle.manifest['macro_snapshot']['rates']['us2y_pct'],4.25)


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
        with patch.object(m.requests,'get') as get, self.assertRaises(m.FeedError):
            m.fetch('https://financialmodelingprep.com/stable/quote')
        get.assert_not_called()
    def test_credential_bearing_exception_is_sanitized(self):
        with patch.object(m.requests,'get',side_effect=m.requests.RequestException('apikey=secret-test-123')):
            with self.assertRaises(m.FeedError) as e:m.fetch(m.TREASURY_URL)
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



if __name__=='__main__':unittest.main()
