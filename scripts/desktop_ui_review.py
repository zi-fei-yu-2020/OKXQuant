"""Helpers for an existing, visible Windows Playwright page.

No browser startup, credential handling, configuration saves, or backend fixtures.
Import into the live Playwright controller and inspect pages/state one at a time.
"""
from __future__ import annotations
import json
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ['/', '/factors', '/news', '/lab', '/history', '/docs']
ADMIN = ['overview','llm','council','promptlib','evolution','interceptors','security','gateway','notify','agents','plugins','backup','audit','adminsys','about','decisions']
REPORTS = []
ERRORS = []
BLOCKED = []
OUTPUT = ROOT / 'okxquant_frontend' / '.ui-artifacts' / 'windows-review'


def protect(context, page):
    """Prevent accidental writes while opening actual administrative UI pages."""
    def route(request_route):
        request = request_route.request
        if request.method not in {'GET','HEAD','OPTIONS'} and not request.url.endswith(('/auth/login','/auth/me','/logout')):
            BLOCKED.append({'method':request.method,'path':urlparse(request.url).path})
            request_route.fulfill(status=409, content_type='application/json', body=json.dumps({'detail':'只读界面检查：未向后端提交此操作。'}))
        else:
            request_route.continue_()
    page.route('**/api/**', route)
    page.on('pageerror', lambda error: ERRORS.append({'page':urlparse(page.url).path,'error':str(error)[:300]}))
    page.on('response', lambda response: ERRORS.append({'page':urlparse(page.url).path,'path':urlparse(response.url).path,'status':response.status}) if response.status>=400 and '/api/' in response.url else None)
    OUTPUT.mkdir(parents=True, exist_ok=True)


def save():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    (OUTPUT/'report.json').write_text(json.dumps({'states':REPORTS,'errors':ERRORS,'blockedWrites':BLOCKED},ensure_ascii=False,indent=2),encoding='utf-8')


def inspect(page, name, settle=True):
    # Hidden Chrome tabs can defer rendering and start color transitions only
    # when computed styles are read. Inspect the visible, settled page rather
    # than a mix of old backgrounds and new foreground theme tokens.
    page.bring_to_front()
    page.evaluate('() => { void document.body.offsetHeight }')
    pending=False
    pending_animations=False
    if settle:
        try: page.wait_for_load_state('networkidle',timeout=8000)
        except PlaywrightTimeoutError: pending=True
        page.wait_for_timeout(500)
        try:
            page.wait_for_function("() => !document.getAnimations().some(a => a.playState === 'running' && a.effect && Number.isFinite(a.effect.getComputedTiming().endTime))", timeout=2500)
        except PlaywrightTimeoutError:
            pending_animations=True
    report=page.evaluate((ROOT/'scripts'/'ui_browser_audit.js').read_text(encoding='utf-8'))
    report['name']=name
    report['pendingNetwork']=pending
    report['pendingAnimations']=pending_animations
    REPORTS[:] = [item for item in REPORTS if item['name'] != name]
    REPORTS.append(report)
    safe_name=''.join(c if c.isalnum() or c in '-_' else '-' for c in name)
    page.screenshot(path=str(OUTPUT/(safe_name+'.png')),full_page=True)
    save()
    print(json.dumps({'page':name,'theme':report['theme'],'contrastIssues':len(report['contrast']),'unlabeledFields':len(report['unlabeledFields']),'pageOverflow':report['pageOverflow'],'pendingNetwork':pending},ensure_ascii=False),flush=True)
    return report


def visit(page, path, theme='light'):
    base = urlparse(page.url)
    origin = f'{base.scheme}://{base.netloc}'
    # A headed browser may navigate while the interactive controller is idle.
    # Synchronize on a completed navigation before evaluating the document.
    page.goto(origin+path,wait_until='domcontentloaded')
    page.wait_for_timeout(500)
    current = page.evaluate('(theme) => { localStorage.setItem("okxquant_theme", theme); return document.documentElement.dataset.theme }',theme)
    if current != theme:
        page.reload(wait_until='domcontentloaded')
        page.wait_for_timeout(500)
    if '/admin/login' in page.url and path!='/admin/login':
        raise RuntimeError('Administrator session unavailable; do not retry passwords automatically.')
    return inspect(page,theme+'-'+(path.strip('/').replace('/','-') or 'terminal'))


def pages(page, theme='light', admin=True):
    for path in PUBLIC + (['/admin/'+name for name in ADMIN] if admin else []):
        visit(page,path,theme)
        page.wait_for_timeout(900)
