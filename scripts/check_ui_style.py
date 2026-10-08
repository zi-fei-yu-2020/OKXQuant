#!/usr/bin/env python3
"""Focused, fixture-only style acceptance for an isolated UI preview.

Real application APIs, credentials and order writers are not exercised here.
Use check_ui.py for the full public/admin route matrix and check_release_ui.py
for the authenticated/public functional boundary.
"""
from __future__ import annotations
import argparse
import copy
import json
import re
import os
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright, expect
from check_ui import market_fixture

GEOMETRY = """el => {
 const outer=el.getBoundingClientRect();
 const bad=[];
 for(const node of el.querySelectorAll('h3,button,[role=tab],strong,small,p,span,input')){
  const r=node.getBoundingClientRect();if(!r.width||!r.height)continue;
  if(r.left<outer.left-1||r.right>outer.right+1)bad.push({tag:node.tagName,text:node.textContent.trim().slice(0,40),reason:'outside-card'});
  const cs=getComputedStyle(node);
  if((cs.overflowX==='hidden'||cs.textOverflow==='ellipsis')&&node.scrollWidth>node.clientWidth+1)bad.push({tag:node.tagName,text:node.textContent.trim().slice(0,40),reason:'clipped-text'});
 }
 const walker=document.createTreeWalker(el,NodeFilter.SHOW_TEXT);
 while(walker.nextNode()){
  const node=walker.currentNode;if(!node.textContent.trim())continue;
  const range=document.createRange();range.selectNodeContents(node);
  for(const r of range.getClientRects())if(r.width&&(r.left<outer.left-1||r.right>outer.right+1))bad.push({tag:'#text',text:node.textContent.trim().slice(0,40),reason:'text-outside-card'});
 }
 const tabs=[...el.querySelectorAll('[role=tab]')].map(node=>{const r=node.getBoundingClientRect();return {width:r.width,height:r.height,scrollWidth:node.scrollWidth,clientWidth:node.clientWidth}});
 return {width:outer.width,problems:bad,tabs};
}"""


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base',default='http://127.0.0.1:8094')
    parser.add_argument('--channel',default='msedge')
    parser.add_argument('--output',default='okxquant_frontend/.ui-artifacts/style-20261009/targeted')
    args=parser.parse_args()
    if urlparse(args.base).hostname not in {'localhost','127.0.0.1','::1'}:parser.error('Isolated localhost preview only')
    output=Path(args.output);output.mkdir(parents=True,exist_ok=True)
    fixture=market_fixture();fixture.update({'account_source_id':'okx:demo:style-fixture','okx_environment':'demo','logs':['inspection fixture line 1','inspection fixture line 2']})
    fixture['data_health']={'status':'LIVE','partial':False,'cache_age_seconds':0}
    fixture['execution_profile']={'execution':{'id':'standard','max_active_instruments':4,'max_leverage':10}}
    def bucket(net):return {'net_pnl':net,'closed':1234,'wins':789,'losses':445,'win_rate':789/1234,'pending_settlements':12}
    fixture['horizon_stats']={'scope':'okx:demo:redundant-scope','timezone':'Asia/Shanghai','as_of':'2026-10-09T00:19:17+08:00','periods':{'today':{'scalp':bucket(-12345.67),'swing':bucket(123456.78),'unknown':{'closed':0,'opened':0,'pending_settlements':0},'coverage':{'start':'2026-10-09T00:00:00+08:00'}},'all':{'scalp':bucket(9876543.21),'swing':bucket(-1234567.89),'unknown':{'closed':0,'opened':0,'pending_settlements':0},'coverage':{'start':'2026-09-01T00:00:00+08:00'}}}}
    original_stats=copy.deepcopy(fixture['horizon_stats'])
    results=[];errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(headless=True,channel=args.channel)
        context=browser.new_context(viewport={'width':1440,'height':1000})
        health=context.request.get(args.base+'/health')
        assert health.headers.get('x-okxquant-preview')=='isolated-source-snapshot'
        assert health.json()['credentials']['okx_configured'] is False
        def route_guard(route):
            path=urlparse(route.request.url).path
            if route.request.method not in ('GET','HEAD','OPTIONS'):route.abort();return
            if path=='/api/all':route.fulfill(json=fixture);return
            route.continue_()
        context.route('**/*',route_guard)
        page=context.new_page();page.on('pageerror',lambda exc:errors.append(str(exc)))
        for theme in ['light','dark']:
            page.goto(args.base+'/')
            page.evaluate("theme=>localStorage.setItem('okxquant_theme',theme)",theme)
            for width in [320,360,390,430,768,1024,1440]:
                page.set_viewport_size({'width':width,'height':1000 if width>430 else 844})
                page.goto(args.base+'/')
                card=page.locator('[data-strategy-stats]');expect(card).to_be_visible()
                expect(card).to_contain_text(re.compile(r'12,?345\.67'))
                for text in ['okx:demo:redundant-scope','Asia/Shanghai','2026-10-09T00:19:17','2026-10-09T00:00:00','\u7cfb\u7edf\u4fdd\u7559\u8d26\u672c\u53e3\u5f84']:
                    expect(card).not_to_contain_text(text)
                expect(page.locator('[data-strategy-telemetry]')).not_to_contain_text('\u51b3\u7b56\u72b6\u6001')
                for period in ['\u5f53\u65e5\u7edf\u8ba1','\u5168\u5c40\u7edf\u8ba1']:
                    tab=card.get_by_role('tab',name=period,exact=True)
                    tab.focus();page.keyboard.press('Enter');expect(tab).to_have_attribute('aria-selected','true')
                    expect(card).to_contain_text(re.compile(r'9,?876,?543\.21' if period=='\u5168\u5c40\u7edf\u8ba1' else r'12,?345\.67'))
                    geometry=card.evaluate(GEOMETRY)
                    assert not geometry['problems'],(theme,width,period,geometry)
                    assert all(t['height']>=32 and t['width']>=60 and t['scrollWidth']<=t['clientWidth']+1 for t in geometry['tabs']),(theme,width,geometry)
                    assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),(theme,width)
                    results.append({'theme':theme,'width':width,'period':period,'geometry':geometry})
                if width in (320,390,1440):card.screenshot(path=str(output/f'{theme}-{width}-strategy.png'))
            # A wide viewport can still contain a narrow statistics column.
            page.set_viewport_size({'width':1440,'height':1000});page.goto(args.base+'/')
            expect(page.locator('[data-strategy-stats]')).to_contain_text(re.compile(r'12,?345\.67'))
            page.locator('[data-strategy-telemetry]').evaluate("el=>el.style.width='300px'")
            narrow=page.locator('[data-strategy-stats]').evaluate(GEOMETRY)
            assert not narrow['problems'],(theme,'narrow desktop container',narrow)
            results.append({'theme':theme,'container_width':300,'geometry':narrow})
        # Unavailable / unknown / zero must remain distinct, even after cosmetic cleanup.
        fixture['horizon_stats']['periods']['today']['scalp']['net_pnl']=None
        page.goto(args.base+'/');expect(page.locator('[data-strategy-stats]')).not_to_contain_text(re.compile(r'12,?345\.67'))
        fixture['horizon_stats']={'scope':'okx:demo:style-fixture','periods':{},'error':'statistics_unavailable'}
        page.goto(args.base+'/');expect(page.locator('[data-strategy-stats]')).to_contain_text('\u6682\u65e0\u7edf\u8ba1')
        fixture['horizon_stats']=original_stats
        page.goto(args.base+'/trades');logs=page.locator('[data-inspection-logs]')
        expect(logs).to_contain_text('inspection fixture line 1')
        assert logs.evaluate("el=>!el.closest('details')"),'Inspection logs must not need expansion'
        assert not page.evaluate("localStorage.getItem('okxquant.admin.session.id')")
        logs.screenshot(path=str(output/'default-visible-logs.png'))
        # Populated module editor: enlarged controls must wrap inside narrow panels.
        password=os.environ.get('OKXQUANT_UI_TEST_PASSWORD','')
        assert password,'Set the isolated-preview password for admin style checks'
        login=context.request.post(args.base+'/api/v1/admin/auth/login',data={'username':'admin','password':password})
        assert login.status==200
        receipt=login.json()
        page.evaluate("r=>{localStorage.setItem('okxquant.admin.session.id',r.session_token);localStorage.setItem('okxquant.admin.session.user',JSON.stringify(r.user));}",receipt)
        modules=[{'id':'fixture-first','title':'Long module title for narrow layout review','content':'Read-only style fixture; not saved','enabled':True},{'id':'fixture-second','title':'Second module','content':'Fixture only','enabled':False}]
        pipelines={key:copy.deepcopy(modules) for key in ['trading_system','trading_user','evolution_system','evolution_user']}
        library={'active_profile_id':'style-fixture','profiles':[{'id':'style-fixture','name':'Style fixture','enabled':True,'execution_profile':'standard','execution_settings':{'id':'standard'},'pipelines':pipelines,'pipeline_views':pipelines}]}
        context.route('**/api/v1/admin/prompt-library',lambda route:route.fulfill(json=library) if route.request.method=='GET' else route.abort())
        for theme in ['light','dark']:
            page.evaluate("theme=>localStorage.setItem('okxquant_theme',theme)",theme)
            for width in [320,390,768,1440]:
                page.set_viewport_size({'width':width,'height':1000})
                page.goto(args.base+'/admin/promptlib')
                module=page.locator('[data-prompt-module]').first
                expect(module).to_be_visible()
                header=module.locator('[data-module-header]')
                geometry=header.evaluate(GEOMETRY)
                assert not geometry['problems'],(theme,width,'module header',geometry)
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),(theme,width,'module editor')
                toggle=module.locator('button[aria-pressed]')
                expect(toggle).to_have_attribute('aria-pressed','true')
                toggle.hover()
                assert toggle.evaluate("el=>{const p=document.createElement('span');p.style.color='var(--color-up)';el.append(p);const expected=getComputedStyle(p).color;p.remove();return getComputedStyle(el).color===expected;}"),'Enabled toggle lost its semantic color'
                if width in [320,1440]:module.screenshot(path=str(output/f'{theme}-{width}-populated-module.png'))
                results.append({'theme':theme,'width':width,'module_header':geometry})
        # Toggle is a local unsaved draft operation; no API mutation is permitted.
        toggle.click();expect(toggle).to_have_attribute('aria-pressed','false')
        assert toggle.evaluate("el=>{const p=document.createElement('span');p.style.color='var(--color-up)';el.append(p);const expected=getComputedStyle(p).color;p.remove();return getComputedStyle(el).color!==expected;}")
        context.request.post(args.base+'/api/v1/admin/auth/logout',headers={'X-OKXQuant-Session':receipt['session_token']})
        assert not errors,errors
        browser.close()
    report={'checks':len(results)+3,'layouts':results,'javascript_errors':errors,'fixture_only':True}
    (output/'style-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps({'checks':report['checks'],'javascript_errors':errors,'fixture_only':True}))


if __name__=='__main__':main()
