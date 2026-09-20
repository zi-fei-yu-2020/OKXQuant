#!/usr/bin/env python3
"""Read-only browser layout review against an explicitly supplied local preview.

Requires Playwright + Chromium. Credentials come from environment, never artifacts.
Writes screenshots and geometry reports, not application state. Mutating admin
requests after login are blocked in the browser unless a test explicitly mocks one.
"""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright, expect

ADMIN = ['overview', 'llm', 'council', 'promptlib', 'evolution', 'interceptors', 'security', 'gateway', 'notify', 'agents', 'plugins', 'backup', 'audit', 'adminsys', 'about', 'decisions']
PUBLIC = ['/', '/factors', '/news', '/lab', '/history', '/docs']
LAYOUT = """() => {
  const width = window.innerWidth;
  const visible = e => { const r=e.getBoundingClientRect(); const s=getComputedStyle(e); return r.width>0 && r.height>0 && s.visibility!=='hidden' && s.display!=='none'; };
  const outside = [...document.querySelectorAll('main *, dialog[open] *')].filter(e => {
    if (!visible(e) || e.closest('pre, .overflow-x-auto, .table-scroll, .table-scroll-container, [hidden]')) return false;
    const r=e.getBoundingClientRect(); return r.left < -1 || r.right > width + 1;
  }).slice(0,12).map(e => ({tag:e.tagName, cls:typeof e.className==='string'?e.className:'',text:(e.textContent||'').trim().slice(0,70),width:Math.round(e.getBoundingClientRect().width)}));
  const unlabeled=[...document.querySelectorAll('button')].filter(e=>visible(e)&&!e.textContent.trim()&&!e.getAttribute('aria-label')&&!e.getAttribute('title')).map(e=>e.outerHTML.slice(0,220));
  return { width, scrollWidth:document.documentElement.scrollWidth, overflow:document.documentElement.scrollWidth>width+1, outside, unlabeled, h1:[...document.querySelectorAll('h1')].filter(visible).map(e=>e.textContent.trim()), bodyBackground:getComputedStyle(document.body).backgroundColor, horizontalScrollContainers:[...document.querySelectorAll('.overflow-x-auto, .table-scroll-container')].filter(e=>e.scrollWidth>e.clientWidth).length };
}"""


def market_fixture():
    """Browser-only examples. Never sent to, or persisted by, the trading backend."""
    factors = []
    for i, (name, price) in enumerate([('BTC', 67231.52), ('ETH', 3245.87), ('SOL', 148.23), ('DOGE', .1356), ('SUI', 1.8942), ('LINK', 14.523)]):
        factors.append({'instId': name+'-USDT-SWAP', 'name': name, 'type': 'crypto', 'price': price, 'chg24h': (-1 if i % 2 else 1) * 2.43, 'rsi': 54, 'macd_hist': 1.2, 'adx_1h': 28.5,
            'calculus': {'velocity_1h': .78, 'accel_1h': .21, 'jerk_1h': -.08}, 'smart_money': {'weighted_long_pct': 62.4, 'net_flow_usdt': '1,234,567.89 U'},
            'decision': {'action': 'WAIT', 'confidence': 82, 'leverage': 3, 'margin_usdt': 125, 'entry_price': price, 'take_profit_price': price*1.04, 'stop_loss_price': price*.98, 'risk_reward_ratio': '2.0 : 1', 'summary_reason': '仅用于浏览器布局检查的示例信号，并非真实交易建议。'}})
    return {'timestamp':'UI fixture', 'is_stale':False,
        'account':{'total_eq':123456.78,'avail_eq':102456.78,'margin_usage_pct':17.01,'initial_capital':100000,'cum_net_pnl':23456.78,'cum_realized_pnl':23000,'cum_roi_pct':23.46,'cum_total_fees':123.45,'pos_upl_total':456.78},
        'positions_summary':{'total_count':2,'long_count':1,'short_count':1,'items':[
            {'instId':'BTC-USDT-SWAP','name':'BTC','side':'long','pos':'12','lever':'3','avgPx':65000,'markPx':67231.52,'upl':267.78,'uplRatio':12.43,'margin_usdt':800,'displayStop':64000,'takeProfitPx':70000,'protectionStatus':'fully_protected','protectionCoveragePct':100},
            {'instId':'ETH-USDT-SWAP','name':'ETH','side':'short','pos':'48','lever':'3','avgPx':3300,'markPx':3245.87,'upl':189,'uplRatio':8.91,'margin_usdt':500,'displayStop':3400,'takeProfitPx':3100,'protectionStatus':'fully_protected','protectionCoveragePct':100}]},
        'pending_orders':[{'ordId':'ui-fixture-1','instId':'SOL-USDT-SWAP','inst':'SOL','name':'SOL','side':'buy','side_raw':'buy','posSide':'long','px':'145.2500','sz':'120','state':'live','cTime':'0','time':'示例时间'}],
        'factors':factors,'logs':['[UI fixture] 浏览器测试示例，不会写入系统日志。'],'trades':[], 'today_stats':{'win_trades':3,'loss_trades':1,'net_realized':456.78,'win_rate':75,'total_fees':12.34},
        'ai_last_prompt':'UI fixture only — example prompt for scroll and clipboard checks.\n'*80,
        'ai_brain_history':[{'time':'示例决策周期','macro_assessment':'示例：验证较长的说明文本在窄屏下正确换行，不是实时行情判断。','position_management':[{'instId':'BTC-USDT-SWAP','action':'HOLD','reason':'示例持仓管理说明'}]}],
        'news_intelligence':{'latest_news':[],'coins_sentiment':{},'macro_sentiment':'暂无真实情报'}, 'review':{}}


def check_interactions(page, context, base, output, blocked):
    results = []
    native_dialogs = []
    page.on('dialog', lambda dialog: (native_dialogs.append(dialog.type), dialog.dismiss()))
    for width, height in [(390,844),(1440,1000)]:
        page.set_viewport_size({'width':width,'height':height})
        page.goto(base+'/admin/overview',wait_until='networkidle')
        if width == 390:
            opener = page.get_by_role('button',name='打开管理导航')
            opener.click()
            expect(page.get_by_role('dialog',name='工作空间导航')).to_be_visible()
            for _ in range(25):
                page.keyboard.press('Tab')
                assert page.evaluate("!!document.activeElement.closest('dialog[open]')"), 'Focus escaped mobile navigation'
            page.keyboard.press('Escape')
            expect(page.locator('dialog[open]')).to_have_count(0)
            expect(opener).to_be_focused()
            results.append({'width':width,'check':'mobile navigation, keyboard trap, escape and restored focus','passed':True})

        page.goto(base+'/admin/backup',wait_until='networkidle')
        writes_before=len(blocked)
        page.get_by_role('button',name='立即备份',exact=True).click()
        modal=page.get_by_role('dialog',name='填写确认信息')
        expect(modal).to_be_visible()
        confirmation=modal.get_by_role('button',name='确认并继续')
        expect(confirmation).to_be_disabled()
        modal.locator('input').fill('WRONG')
        expect(confirmation).to_be_disabled()
        modal.locator('input').fill('BACKUP OKXQuant')
        expect(confirmation).to_be_enabled()
        page.screenshot(path=str(output/f'{width}-confirmation.png'))
        modal.get_by_role('button',name='取消',exact=True).click()
        expect(page.locator('dialog[open]')).to_have_count(0)
        assert len(blocked)==writes_before, 'Cancelling a confirmation submitted an operation'
        results.append({'width':width,'check':'exact phrase gating and cancellation without write','passed':True})

        page.goto(base+'/admin/adminsys',wait_until='networkidle')
        opener=page.get_by_role('button',name='新建管理员',exact=True)
        opener.click()
        modal=page.get_by_role('dialog',name='新增管理员',exact=True)
        expect(modal).to_be_visible()
        modal.locator('input:not([type=password])').fill('ui-fixture-user')
        modal.locator('input[type=password]').fill('PreviewNotPersisted123')
        def mock_validation(route):
            if route.request.method=='POST':
                route.fulfill(status=422, content_type='application/json', body=json.dumps({'detail':'浏览器测试：服务器拒绝此示例请求，未创建任何账户。'}))
            else: route.fallback()
        context.route('**/api/v1/admin/users', mock_validation)
        modal.get_by_role('button',name='创建',exact=True).click()
        expect(modal.get_by_role('alert')).to_be_visible()
        assert not page.evaluate(LAYOUT)['overflow'], 'Modal exceeded the viewport'
        page.screenshot(path=str(output/f'{width}-modal-error.png'))
        modal.get_by_role('button',name='关闭弹窗',exact=True).click()
        expect(page.locator('dialog[open]')).to_have_count(0)
        expect(opener).to_be_focused()
        context.unroute('**/api/v1/admin/users',mock_validation)
        for button in page.get_by_role('button',name='关闭通知').all():
            if button.is_visible(): button.click()
        results.append({'width':width,'check':'mocked validation error visible inside modal, close and restored focus','passed':True})
    assert not native_dialogs, f'Native browser dialogs still used: {native_dialogs}'
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', default='http://127.0.0.1:8080')
    parser.add_argument('--output', default='okxquant_frontend/.ui-artifacts')
    parser.add_argument('--quick', action='store_true')
    parser.add_argument('--fixtures', action='store_true', help='Use browser-only market fixtures to check populated tables/cards')
    parser.add_argument('--interactions-only', action='store_true')
    parser.add_argument('--interactions', action='store_true', help='Check dialogs, focus, confirmation phrases and mocked API errors')
    parser.add_argument('--channel', default=None, help='Installed browser channel, e.g. msedge; no browser download')
    args = parser.parse_args()
    if urlparse(args.base_url).hostname not in {'localhost', '127.0.0.1', '::1'}:
        parser.error('Use a local isolated preview, not a production host.')
    password = os.environ.get('OKXQUANT_UI_TEST_PASSWORD')
    if not password:
        parser.error('Set OKXQUANT_UI_TEST_PASSWORD for the temporary preview account.')
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    results, js_errors, blocked = [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, channel=args.channel)
        context = browser.new_context(viewport={'width': 1440, 'height': 1000}, device_scale_factor=1)
        page = context.new_page()
        page.on('pageerror', lambda error: js_errors.append(str(error)))
        def guard(route):
            request = route.request
            if request.method not in {'GET', 'HEAD', 'OPTIONS'} and not request.url.endswith(('/auth/login', '/logout')):
                blocked.append({'method': request.method, 'path': urlparse(request.url).path})
                route.fulfill(status=409, content_type='application/json', body=json.dumps({'detail': 'UI review: writes disabled'}))
            else:
                route.continue_()
        context.route('**/api/**', guard)
        if args.fixtures:
            fixture = market_fixture()
            context.route('**/api/all?*', lambda route: route.fulfill(status=200, content_type='application/json', body=json.dumps(fixture)))
        for theme in ['light', 'dark']:
            page.goto(args.base_url + '/admin/login')
            page.evaluate("theme => { localStorage.setItem('okxquant_theme',theme); document.documentElement.dataset.theme=theme; document.documentElement.classList.toggle('dark',theme==='dark'); }", theme)
            for width, height in [(1440,1000), (390,844)]:
                page.set_viewport_size({'width':width,'height':height})
                page.screenshot(path=str(output/f'{theme}-{width}-login.png'))
        page.set_viewport_size({'width':1440,'height':1000})
        page.get_by_label('管理员账号', exact=True).fill(os.environ.get('OKXQUANT_UI_TEST_USERNAME','admin'))
        page.locator('#login-password').fill(password)
        page.get_by_role('button', name='登录工作空间').click()
        page.wait_for_url('**/admin/overview')
        for theme in ([] if args.interactions_only else ['light','dark']):
            page.evaluate("theme => localStorage.setItem('okxquant_theme',theme)", theme)
            sizes = [(1440,1000),(390,844)] if args.quick else [(1440,1000),(1024,900),(768,1024),(390,844),(320,740)]
            for width,height in sizes:
                page.set_viewport_size({'width':width,'height':height})
                for route in PUBLIC + ['/admin/'+name for name in ADMIN]:
                    page.goto(args.base_url + route, wait_until='networkidle' if route.startswith('/admin/') else 'load')
                    page.wait_for_timeout(180)
                    actual_path = urlparse(page.url).path.rstrip('/') or '/'
                    assert actual_path == route, f'Unexpected redirect: {route} -> {actual_path}'
                    result = page.evaluate(LAYOUT)
                    result.update({'theme':theme,'route':route,'state':'fixture' if args.fixtures and route in PUBLIC else 'empty'})
                    results.append(result)
                    slug=route.strip('/').replace('/','-') or 'terminal'
                    if width in {1440,390}: page.screenshot(path=str(output/f'{theme}-{width}-{slug}.png'))
                    print(json.dumps({k:result[k] for k in ['theme','width','route','overflow','unlabeled']},ensure_ascii=False),flush=True)
        interaction_results = check_interactions(page, context, args.base_url, output, blocked) if args.interactions or args.interactions_only else []
        browser.close()
    report={'layouts':results,'javascriptErrors':js_errors,'blockedWrites':blocked,'interactions':interaction_results}
    (output/'layout-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    overflow=[item for item in results if item['overflow']]
    print(f'Checked {len(results)} layouts; page overflow: {len(overflow)}; JavaScript errors: {len(js_errors)}')
    return 1 if overflow or js_errors or any(item['unlabeled'] for item in results) else 0

if __name__ == '__main__':
    raise SystemExit(main())
