"""Local-only transport panel acceptance. Verification POSTs are mocked, never billed."""
import argparse,json,os,re,copy
from pathlib import Path
from urllib.parse import urlparse
from playwright.sync_api import sync_playwright,expect

def main():
 p=argparse.ArgumentParser();p.add_argument('--base',default='http://127.0.0.1:8095');p.add_argument('--output',default='okxquant_frontend/.ui-artifacts/transport-20261009');args=p.parse_args()
 assert urlparse(args.base).hostname in {'localhost','127.0.0.1','::1'}
 out=Path(args.output);out.mkdir(parents=True,exist_ok=True);checks=[];errors=[];writes=[]
 with sync_playwright() as pw:
  browser=pw.chromium.launch(headless=True,channel='msedge');context=browser.new_context(viewport={'width':1440,'height':1000})
  health=context.request.get(args.base+'/health');assert health.headers.get('x-okxquant-preview')=='isolated-source-snapshot'
  assert health.json()['credentials']['okx_configured'] is False
  login=context.request.post(args.base+'/api/v1/admin/auth/login',data={'username':'admin','password':os.environ['OKXQUANT_UI_TEST_PASSWORD']});assert login.status==200
  receipt=login.json();page=context.new_page();page.on('pageerror',lambda e:errors.append(str(e)))
  page.goto(args.base+'/');page.evaluate("r=>{localStorage.setItem('okxquant.admin.session.id',r.session_token);localStorage.setItem('okxquant.admin.session.user',JSON.stringify(r.user));}",receipt)
  page.goto(args.base+'/admin/llm');page.get_by_text('OpenAI',exact=True).first.click();page.get_by_role('button',name=re.compile(r'^模型 \(')).click()
  panel=page.locator('[data-llm-transport]');expect(panel).to_be_visible();expect(panel).to_contain_text('未验证')
  model=panel.get_by_label('验证模型',exact=True).input_value()
  state=context.request.get(args.base+'/api/v1/admin/llm/transport',params={'provider_id':'openai','model_id':model},headers={'X-OKXQuant-Session':receipt['session_token']}).json()
  checks.append('real saved-provider state is readable with no model request')
  def guard(route):
   path=urlparse(route.request.url).path;method=route.request.method
   if method not in ('GET','HEAD','OPTIONS'):
    writes.append(path)
    if path=='/api/v1/admin/llm/transport/verify':route.fulfill(status=202,json={'job_id':'fixture-job','status':'queued'});return
    if path=='/api/v1/admin/llm/transport':
     draft=route.request.post_data_json;state['policy']=draft['policy'];route.fulfill(json=state);return
    route.abort();return
   if '/llm/transport/verification/' in path:
    verified=copy.deepcopy(state);verified['effective_mode']='stream';verified['capabilities']['stream']={'status':'verified','checked_at':'2026-10-09T00:00:00+00:00'}
    route.fulfill(json={'status':'completed','ok':True,'state':verified,'diagnostics':{'first_byte_ms':0,'total_ms':150000,'heartbeat_count':5,'completion_seen':True}});return
   route.continue_()
  context.route('**/api/**',guard)
  panel.get_by_role('button',name='验证流式',exact=True).click();dialog=page.get_by_role('dialog',name='验证响应能力');expect(dialog).to_contain_text('可能产生费用');dialog.get_by_role('button',name='取消',exact=True).click();assert not writes
  checks.append('canceling explicit paid-probe confirmation sends no request')
  panel.get_by_role('button',name='验证流式',exact=True).click();page.get_by_role('dialog',name='验证响应能力').get_by_role('button',name='确认并继续',exact=True).click()
  expect(panel).to_contain_text('本次协议与完整 JSON 验证通过',timeout=10000);expect(panel.locator('[data-transport-diagnostics]')).to_contain_text('0 ms')
  assert writes.count('/api/v1/admin/llm/transport/verify')==1
  checks.append('one explicit probe then read-only polling; zero remains observed zero')
  panel.get_by_label('响应方式',exact=True).select_option('json');panel.get_by_role('button',name='保存传输配置',exact=True).click();expect(panel).to_contain_text('配置已保存')
  assert writes.count('/api/v1/admin/llm/transport/verify')==1
  checks.append('saving transport policy does not generate a model probe')
  panel.locator('summary').click()
  for theme in ['light','dark']:
   page.evaluate("t=>{document.documentElement.dataset.theme=t;document.documentElement.classList.toggle('dark',t==='dark')}",theme)
   for width in [320,390,768,1440]:
    page.set_viewport_size({'width':width,'height':1000});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1'),(theme,width)
    panel.screenshot(path=str(out/f'{theme}-{width}-panel.png'));checks.append(f'panel layout {theme} {width}')
  actors=context.request.get(args.base+'/api/v1/admin/agents',headers={'X-OKXQuant-Session':receipt['session_token']}).json()
  actors['model_calls']=[{'id':1,'caller':'trading_brain','model':'fixture','status':'failed','duration_ms':135000,'total_tokens':None,'transport':{'transport_mode':'stream','http_status':200,'first_byte_ms':0,'first_content_ms':60000,'max_gap_ms':30000,'heartbeat_count':4,'attempts':1,'completion_seen':False,'failure_phase':'idle','request_id':'req_fixture12345678','cf_ray':'0123456789abcdef-HKG'}}]
  context.route('**/api/v1/admin/agents',lambda route:route.fulfill(json=actors))
  page.goto(args.base+'/admin/agents');detail=page.locator('[data-model-transport]');expect(detail).to_be_visible();detail.locator('summary').click()
  expect(detail).to_contain_text('首包 0 ms');expect(detail).to_contain_text('CF-Ray 0123456789abcdef-HKG');expect(detail).to_contain_text('完整结束 否')
  page.set_viewport_size({'width':390,'height':1000});assert page.evaluate('document.documentElement.scrollWidth <= innerWidth + 1')
  checks.append('actual-call diagnostic viewer preserves zero, failure phase and safe IDs')
  context.unroute('**/api/**',guard);context.request.post(args.base+'/api/v1/admin/auth/logout',headers={'X-OKXQuant-Session':receipt['session_token']})
  assert not errors,errors;browser.close()
 report={'checks':checks,'passed':len(checks),'javascript_errors':errors,'mocked_writes':writes,'model_requests_sent':0};(out/'ui-report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8');print(json.dumps(report,ensure_ascii=False))

if __name__=='__main__':main()
