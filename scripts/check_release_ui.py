#!/usr/bin/env python3
"""Functional UI gate for the marked disposable preview only, never production.

Uses browser-only financial fixtures. The only real writes are session/user
and optional-feature settings in the disposable preview; no exchange actions.
"""
import argparse
import copy
import json
import os
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from playwright.sync_api import sync_playwright,expect
from check_ui import market_fixture


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url',default='http://127.0.0.1:8091')
    parser.add_argument('--output',required=True);parser.add_argument('--channel',default='msedge')
    args=parser.parse_args();base=args.base_url.rstrip('/')
    if urlparse(base).hostname not in {'127.0.0.1','localhost','::1'}:parser.error('Only a local isolated preview is allowed')
    password=os.environ['OKXQUANT_UI_TEST_PASSWORD'];output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    checks=[];errors=[];writes=[];private_requests=[];trade_queries=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel=args.channel)
        context=browser.new_context(viewport={'width':1440,'height':1000},timezone_id='Asia/Shanghai')
        health=context.request.get(base+'/health')
        assert health.headers.get('x-okxquant-preview')=='isolated-source-snapshot','Refusing unmarked server'
        assert health.json()['credentials']['okx_configured'] is False,'Refusing configured exchange credentials'
        for path in ['/api/all','/api/overview','/api/trades','/api/ai/last-prompt','/api/v1/status','/api/v1/cache/horizon-stats']:
            assert context.request.get(base+path).status==401,path
        checks.append('anonymous private APIs denied')
        page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(base+'/history?state=closed#ledger')
        expect(page).to_have_url(__import__('re').compile(r'/admin/login'))
        checks.append('anonymous front route redirects to login')
        page.get_by_label('\u7ba1\u7406\u5458\u8d26\u53f7',exact=True).fill('admin')
        page.locator('#login-password').fill(password)
        page.get_by_role('button',name='\u767b\u5f55\u5de5\u4f5c\u7a7a\u95f4').click()
        expect(page).to_have_url(__import__('re').compile(r'/trades\?state=closed#ledger'))
        checks.append('login restores canonical deep link plus query/hash')
        session=page.evaluate("localStorage.getItem('okxquant.admin.session.id')")
        assert session,'No session obtained'
        headers={'X-OKXQuant-Session':session}
        existing=context.request.get(base+'/api/v1/admin/users',headers=headers).json()
        users=existing.get('users',[]) if isinstance(existing,dict) else existing
        if not any(x.get('username')=='ui_reader' for x in users):
            response=context.request.post(base+'/api/v1/admin/users',headers=headers,data={'username':'ui_reader','password':password,'role':'admin'})
            assert response.status==200,'Cannot create isolated reader fixture'
        # Restore all fixture settings before leaving. No trading toggle is enabled.
        original=context.request.get(base+'/api/v1/admin/runtime-features',headers=headers).json()
        for profile in ['standard','light']:
            response=context.request.put(base+'/api/v1/admin/runtime-features',headers=headers,data={'profile':profile})
            assert response.status==200 and response.json()['profile']==profile
        assert context.request.get(base+'/api/v1/admin/runtime-controls',headers=headers).json()['automatic_trader'] is False
        checks.append('light/standard optional configuration works without enabling entries')
        fixture=market_fixture();scope='okx:demo:ui-fixture';fixture['account_source_id']=scope
        fixture['okx_environment']='demo';fixture['data_health']={'status':'LIVE','partial':False,'cache_age_seconds':0}
        def bucket(net,closed):return {'net_pnl':net,'closed':closed,'wins':3,'losses':max(0,closed-3),'breakeven':0,'win_rate':3/closed if closed else 0,'opened':closed+2,'pending_settlements':2,'fees':-1,'gross_pnl':net+1 if net is not None else None,'funding_fee':0,'completeness':{'net_pnl':net is not None}}
        fixture['horizon_stats']={'scope':scope,'timezone':'Asia/Shanghai','as_of':'2026-10-08T12:00:00+08:00','periods':{'today':{'scalp':bucket(12.34,4),'swing':bucket(3.21,5),'unknown':bucket(0,0),'pending_settlements':2,'coverage':{'start':'2026-10-08T00:00:00+08:00','complete':False}},'all':{'scalp':bucket(1234.56,103),'swing':bucket(321.09,50),'unknown':bucket(0,0),'pending_settlements':2,'coverage':{'start':'2026-09-01T00:00:00+08:00','complete':False}}}}
        financial_fixture=copy.deepcopy(fixture['horizon_stats'])
        def route_guard(route):
            request=route.request;path=urlparse(request.url).path
            if request.method not in ('GET','HEAD','OPTIONS') and not path.endswith(('/auth/login','/auth/logout')):
                writes.append(path);route.fulfill(status=409,json={'detail':'No production-affecting browser writes in this audit'});return
            if path.startswith(('/api/all','/api/trades','/api/ai/')):
                private_requests.append((path,bool(request.headers.get('x-okxquant-session'))))
            if path=='/api/all':
                route.fulfill(json=fixture);return
            if path=='/api/trades':
                query=parse_qs(urlparse(request.url).query);trade_queries.append(query)
                offset=int(query.get('offset',['0'])[0]);state=query.get('state',['all'])[0]
                rows=[{'id':f'fixture-{i}','inst':'BTC','instId':'BTC-USDT-SWAP','strategy':'fixture strategy','status':'closed_pending','open_time':'2026-10-08 10:00:00','close_time':'--','net_pnl':None,'environment_id':scope} for i in range(offset,min(offset+30,65))]
                if state=='active':rows=[]
                route.fulfill(json={'items':rows,'total':0 if state=='active' else 65,'counts':{'all':65,'active':0,'closed':65,'pending':65},'offset':offset,'limit':30,'account_source_id':scope});return
            if path=='/api/ai/last-prompt':route.fulfill(json={'prompt':'SCOPED UI AUDIT PROMPT <script>window.__prompt_injected=true</script>','scope':scope,'recorded_at':1791446400});return
            if path=='/api/ai/history':route.fulfill(json={'items':[],'total':0,'limit':25,'offset':0,'account_source_id':scope});return
            route.continue_()
        context.route('**/api/**',route_guard)
        page.goto(base+'/');panel=page.locator('[data-strategy-telemetry]')
        expect(panel).to_contain_text('12.34')
        page.get_by_role('tab',name='\u5168\u5c40\u7edf\u8ba1',exact=True).click()
        expect(panel).to_contain_text('1234.56')
        page.get_by_role('tab',name='\u5f53\u65e5\u7edf\u8ba1',exact=True).click()
        expect(panel).to_contain_text('12.34')
        checks.append('today/all switches use distinct authoritative period totals')
        page.screenshot(path=str(output/'today-statistics.png'))
        fixture['horizon_stats']={'scope':scope,'scalp':{'net_pnl':99999,'closed':99},'periods':{},'error':'statistics_unavailable'}
        page.reload();expect(page.locator('[data-strategy-telemetry]')).not_to_contain_text('99999')
        checks.append('empty periods never display legacy all-time totals as today')
        fixture['horizon_stats']={'scope':scope,'scalp':{'net_pnl':99999,'closed':99}}
        page.reload();expect(page.locator('[data-strategy-telemetry]')).not_to_contain_text('99999')
        checks.append('absent periods never display legacy all-time totals as today')
        fixture['horizon_stats']=financial_fixture
        fixture['horizon_stats']['periods']['today']['scalp']['net_pnl']=None
        page.reload();expect(page.locator('[data-strategy-telemetry]')).not_to_contain_text('12.34')
        checks.append('unknown PnL remains unavailable')
        for old,new in [('/factors','/decisions'),('/news','/market-intelligence'),('/lab','/reviews'),('/history','/trades')]:
            page.goto(base+old+'?probe=1#keep');expect(page).to_have_url(base+new+'?probe=1#keep')
        checks.append('all four legacy redirects preserve query and hash')
        page.goto(base+'/trades');expect(page.get_by_text('fixture strategy').first).to_be_visible()
        # Label-independent assertion: pagination controls must cause a real offset read.
        next_button=page.get_by_role('button',name=__import__('re').compile('\u4e0b\u4e00\u9875'))
        next_button.click();page.wait_for_timeout(350)
        assert any(q.get('offset')==['30'] for q in trade_queries),'Pagination did not request next offset'
        search=page.locator('input[placeholder]').first
        search.fill('fixture');page.wait_for_timeout(500)
        assert any(q.get('q')==['fixture'] and q.get('offset')==['0'] for q in trade_queries),'Search not sent to full-ledger API'
        checks.append('trade pagination and search use server-side full-ledger requests')
        page.goto(base+'/admin/decisions')
        with page.expect_request(lambda r:urlparse(r.url).path=='/api/ai/last-prompt'):
            page.get_by_role('button',name='\u5b9e\u65f6\u63d0\u793a\u8bcd',exact=False).click()
        expect(page.get_by_role('dialog')).to_contain_text('SCOPED UI AUDIT PROMPT')
        assert any(path=='/api/ai/last-prompt' and present for path,present in private_requests),'Prompt fetch omitted authentication'
        page.keyboard.press('Escape')
        page.get_by_role('button',name='\u5b9e\u65f6\u63d0\u793a\u8bcd',exact=False).click()
        expect(page.get_by_role('dialog')).to_contain_text('SCOPED UI AUDIT PROMPT')
        expect(page.get_by_role('dialog')).not_to_contain_text('UI fixture only')
        page.keyboard.press('Escape')
        assert not page.evaluate('Boolean(window.__prompt_injected)'), 'Prompt HTML was executed'
        checks.append('advanced prompt audit fetch is authenticated')
        assert all(has_header for _,has_header in private_requests),'Private fetch omitted session: '+str([path for path,has_header in private_requests if not has_header])
        checks.append('all observed private browser fetches carry session headers')
        page.goto(base+'/admin/overview');expect(page.get_by_text('\u7b97\u529b\u4e0e\u8fd0\u884c\u8d1f\u8f7d\u914d\u7f6e',exact=False)).to_be_visible()
        assert page.get_by_role('button',name='\u542f\u7528\u8c03\u5ea6',exact=False).is_disabled(),'New install must not enable without credentials'
        body=page.locator('main').inner_text()
        assert '\u62e6\u622a\u5668\u5df2\u751f\u6548' not in body,'Unverified guard readiness claim'
        checks.append('new-install readiness does not fabricate enforcement or allow unconfigured enable')
        page.screenshot(path=str(output/'console-overview.png'))
        # Reader role checks use an actual server-issued isolated session.
        reader=context.request.post(base+'/api/v1/admin/auth/login',data={'username':'ui_reader','password':password})
        assert reader.status==200;reader_token=reader.json()['session_token'];reader_headers={'X-OKXQuant-Session':reader_token}
        assert context.request.get(base+'/api/v1/admin/runtime-features',headers=reader_headers).status==200
        assert context.request.put(base+'/api/v1/admin/runtime-features',headers=reader_headers,data={'profile':'standard'}).status==403
        assert context.request.put(base+'/api/v1/admin/runtime-controls',headers=reader_headers,data={'automatic_trader':False,'confirmation':'PAUSE AUTO'}).status==403
        checks.append('ordinary reader can inspect but cannot change runtime or entry controls')
        # Positive authentication gate against the ACTUAL preview API, no financial mock.
        context.unroute('**/api/**',route_guard)
        with page.expect_response(lambda r:urlparse(r.url).path=='/api/all') as actual_response:
            page.goto(base+'/')
        assert actual_response.value.status==200,'Actual authenticated home API did not succeed'
        assert actual_response.value.request.headers.get('x-okxquant-session'),'Actual home request omitted authentication'
        checks.append('unmocked authenticated home API succeeds')
        context.request.put(base+'/api/v1/admin/runtime-features',headers=headers,data={'profile':original['profile'],'overrides':original['features']})
        context.request.post(base+'/api/v1/admin/auth/logout',headers=headers)
        page.goto(base+'/admin/overview');expect(page).to_have_url(__import__('re').compile(r'/admin/login'))
        checks.append('revoked session returns to login')
        page.get_by_label('\u7ba1\u7406\u5458\u8d26\u53f7',exact=True).fill('ui_reader')
        page.locator('#login-password').fill(password)
        page.get_by_role('button',name='\u767b\u5f55\u5de5\u4f5c\u7a7a\u95f4').click()
        expect(page).to_have_url(__import__('re').compile(r'/admin/overview'))
        page.get_by_role('link',name='\u8d26\u6237\u4e0e\u4ea4\u6613',exact=True).click()
        expect(page).to_have_url(base+'/admin/security')
        assert page.locator('a[href="/admin/accounts"]').count()==0,'Reader sees superadmin account navigation'
        checks.append('ordinary-reader navigation hides account administration')
        ui_reader_token=page.evaluate("localStorage.getItem('okxquant.admin.session.id')")
        context.request.post(base+'/api/v1/admin/auth/logout',headers={'X-OKXQuant-Session':ui_reader_token})
        context.request.post(base+'/api/v1/admin/auth/logout',headers=reader_headers)
        assert not errors,errors
        browser.close()
    report={'checks':checks,'passed':len(checks),'javascript_errors':errors,'blocked_browser_writes':writes,'fixture_only':True}
    (output/'functional-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=True))

if __name__=='__main__':main()
