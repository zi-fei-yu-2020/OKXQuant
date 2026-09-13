"""Encrypted account center with explicit purpose bindings and fail-closed changes.

No runtime migration on import/read. Existing keys stay effective until an explicit
flat-account bind. OAuth trading remains gated until real write capability testing.
"""
from contextlib import contextmanager
import copy
import json
import os
from pathlib import Path
import time
import uuid
from cryptography.fernet import InvalidToken
from okxquant_backend import connection_transport as transport
from okxquant_gateway import secrets as vault

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / 'data'
STORE = DATA / 'account_connections.enc'
VERSION = 1


class AccountChangeError(RuntimeError): pass


def load():
    if not STORE.exists():
        return {'version':VERSION,'revision':0,'managed':False,'active_mode':None,
                'bindings':{'demo':None,'live':None,'news':None},'generations':{'demo':0,'live':0},'connections':{},'events':[]}
    try:
        f = vault._fernet(False)
        if f is None: raise ValueError('missing encryption key')
        state = json.loads(f.decrypt(STORE.read_bytes()))
        if state['version'] != VERSION or not isinstance(state['connections'],dict): raise ValueError('bad schema')
        return state
    except (OSError, ValueError, KeyError, TypeError, InvalidToken):
        raise AccountChangeError('账户连接存储不可读；已阻止回退到其他账号') from None


def save(state, action, details=None):
    state['revision'] += 1
    state['events'] = ([{'at':time.time(),'action':action,**(details or {})}]+state['events'])[:100]
    f = vault._fernet(True)
    vault._atomic_write(STORE, f.encrypt(json.dumps(state,ensure_ascii=False).encode()))


@contextmanager
def registry_guard():
    import fcntl
    DATA.mkdir(parents=True,exist_ok=True)
    with (DATA/'.account-center.lock').open('a+') as handle:
        deadline=time.monotonic()+2
        while True:
            try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline: raise AccountChangeError('账户管理操作正在执行，请稍后重试')
                time.sleep(.02)
        try: yield
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


@contextmanager
def mutation_guard():
    import fcntl
    from scripts import trade_lock, ledger_monitor
    from contextlib import ExitStack
    with trade_lock.writer(timeout=1), ExitStack() as stack:
        DATA.mkdir(parents=True,exist_ok=True)
        handle = stack.enter_context((DATA/'.ai_factor_trader.lock').open('a+'))
        try: fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise AccountChangeError('策略周期正在运行，不能更换账号或交易环境') from None
        try:
            with ledger_monitor.lock('.ledger-sync.lock',blocking=False), registry_guard(): yield
        except BlockingIOError: raise AccountChangeError('账本正在同步，请稍后重试') from None
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def _connection(state, identity):
    try: return state['connections'][identity]
    except KeyError: raise AccountChangeError('连接不存在') from None


def public_status():
    state = load()
    connections = []
    for c in state['connections'].values():
        row={k:copy.deepcopy(c[k]) for k in ('id','label','auth_type','mode','site','status','generation','capabilities','created_at') if k in c}
        row.update(display_label=display_label(c['label']),credentials_display=credentials_display(c.get('credentials',{}),c['auth_type']))
        connections.append(row)
    from scripts.okx_runtime import legacy_environment
    active_legacy = legacy_environment().mode
    legacy = {mode:legacy_environment(mode=mode).configured and (mode==active_legacy or legacy_environment(mode=mode).source=='separate-credentials') for mode in ('demo','live')}
    legacy_details={}
    for mode,configured in legacy.items():
        e=legacy_environment(mode=mode)
        credentials={'api_key':e.api_key,'secret_key':e.secret_key,'passphrase':e.passphrase} if configured else {}
        legacy_details[mode]={'configured':configured,'source':e.source,'credentials_display':credentials_display(credentials,'api_key')}
    from scripts.okx_runtime import _load_dotenv
    manual_close=_load_dotenv().get('OKXQUANT_MANUAL_CLOSE_ENABLED','0')=='1'
    from okxquant_gateway.scheduler import JOBS
    news_interval=next(job.interval_seconds for job in JOBS if job.name=='news')
    try: news=json.loads((DATA/'news_sentiment.json').read_text(encoding='utf8'))
    except (OSError,ValueError): news={}
    news={k:news.get(k) for k in ('schema','connection_status','updated_at','last_attempt_at','last_success_at','message')}
    return {'version':VERSION,'revision':state['revision'],'managed':state['managed'],
            'active_mode':state['active_mode'] or legacy_environment().mode,'bindings':state['bindings'],
            'connections':connections,'legacy_key_configured':legacy,'legacy_connections':legacy_details,'recent_events':state['events'][:12],
            'trading_connection':trading_connection_summary(state),'manual_close_enabled':manual_close,'news_interval_seconds':news_interval,
            'oauth_write_status':'not_validated','automatic_fallback':False,'news':news,
            'oauth_runtime':{'binary_available':transport.AUTH_BINARY.is_file(),'posix_ready':os.name=='posix'}}


def create(label, auth_type, mode, site='global', credentials=None):
    if auth_type not in {'api_key','oauth'} or mode not in {'demo','live'} or site not in {'global','eea','us','tr'}:
        raise AccountChangeError('无效的认证方式、环境或站点')
    if not label.strip() or len(label)>60: raise AccountChangeError('请输入不超过60字的连接名称')
    if auth_type == 'api_key' and not all((credentials or {}).get(k) for k in ('api_key','secret_key','passphrase')):
        raise AccountChangeError('Key、Secret、Passphrase必须同时填写')
    with registry_guard():
        state=load(); identity=uuid.uuid4().hex
        state['connections'][identity]={'id':identity,'label':label.strip(),'auth_type':auth_type,'mode':mode,'site':site,
          'status':'unverified','generation':1,'capabilities':{},'created_at':time.time(),
          **({'credentials':{k:credentials[k] for k in ('api_key','secret_key','passphrase')}} if auth_type=='api_key' else {})}
        save(state,'connection_created',{'connection_id':identity,'auth_type':auth_type})
    return identity


def oauth_start(identity):
    with registry_guard():
        state=load(); c=_connection(state,identity)
        if c['auth_type']!='oauth': raise AccountChangeError('该连接不是OAuth授权')
        if identity in state['bindings'].values(): raise AccountChangeError('已绑定连接不能重新登录；请先新建候选连接')
        if any(_connection(state,x)['auth_type']=='oauth' for k,x in state['bindings'].items() if k!='news' and x):
            raise AccountChangeError('已有OAuth交易绑定；需要独立运行环境验证后才能新增授权')
        if c['status']=='authorization_pending' and time.time()<c.get('authorization_expires_at',c.get('authorization_started_at',0)+1800):
            raise AccountChangeError('授权流程尚未过期，请先完成当前官方授权')
        c['generation']+=1;c['capabilities']={};c['status']='authorization_pending';c['authorization_started_at']=time.time();c['authorization_expires_at']=time.time()+1800
        generation=c['generation'];site=c['site'];transport.invalidate(identity)
        save(state,'oauth_started',{'connection_id':identity})
    try:
        payload=transport.auth_command(identity,'login',site)
        uri=str(payload.get('verificationUri') or payload.get('verification_uri') or '')
        from urllib.parse import urlsplit
        parsed=urlsplit(uri);host=parsed.hostname or ''
        if parsed.scheme!='https' or parsed.port not in (None,443) or parsed.username or parsed.password or not (host in {'okx.com','okx.us','okx.com.tr'} or host.endswith(('.okx.com','.okx.us','.okx.com.tr'))):
            raise AccountChangeError('官方授权地址无法核验')
        code=str(payload.get('userCode') or payload.get('user_code') or '')
        expires=int(payload.get('expiresIn') or payload.get('expires_in') or 0)
        if not code or not 0<expires<=1800: raise AccountChangeError('授权码响应不完整')
        with registry_guard():
            state=load();current=_connection(state,identity)
            if current['generation']!=generation: raise AccountChangeError('授权版本已改变')
            current['authorization_expires_at']=time.time()+expires
            save(state,'oauth_pending',{'connection_id':identity})
        return {'status':'pending','verification_uri':uri,'user_code':code,'expires_in':expires}
    except Exception:
        with registry_guard():
            state=load();current=state['connections'].get(identity)
            if current and current['generation']==generation:
                current['status']='unavailable';save(state,'oauth_start_failed',{'connection_id':identity})
        raise


def probe(identity, mode):
    if mode not in {'demo','live'}: raise AccountChangeError('无效环境')
    with registry_guard():
        c=copy.deepcopy(_connection(load(),identity))
    prior=c.get('capabilities',{}).get(mode,{})
    results={};uid=''
    paths={'identity':'/api/v5/account/config','balance':'/api/v5/account/balance','positions':'/api/v5/account/positions',
           'pending_orders':'/api/v5/trade/orders-pending','protection_orders':'/api/v5/trade/orders-algo-pending',
           'positions_history':'/api/v5/account/positions-history','bills':'/api/v5/account/bills'}
    for name,path in paths.items():
        params={'instType':'SWAP'} if name in {'positions','pending_orders','positions_history'} else {'ordType':'oco'} if name=='protection_orders' else {}
        try:
            rows=transport.request(c,'probe',mode,'GET',path,params,timeout=4,verify_identity=name!='identity')
            if name=='identity':
                uid=str(rows[0].get('uid') or '') if len(rows)==1 else ''
                if not uid: raise transport.ConnectionError('identity_not_returned')
                if prior.get('account_uid') and prior['account_uid']!=uid: raise transport.ConnectionError('account_identity_changed')
                c['capabilities'].setdefault(mode,{})['account_uid']=uid
            results[name]={'ok':True}
        except transport.ConnectionError as exc:
            results[name]={'ok':False,'error':exc.code}
            if name=='identity': uid='';break
    cap={'checked_at':time.time(),'account_uid':uid,'reads':results,'read_ready':len(results)==len(paths) and all(r['ok'] for r in results.values()),
         'write_ready':False,'write_status':'not_validated' if c['auth_type']=='oauth' else 'existing_signed_transport'}
    if uid:
        cap['canonical_scope']=prior.get('canonical_scope') if prior.get('account_uid')==uid and prior.get('canonical_scope') else _uid_scope(mode,uid)
    if mode=='live' and uid:
        try:
            transport.request(c,'news','live','GET','/api/v5/orbit/news-search',{'limit':1},timeout=4)
            transport.request(c,'news','live','GET','/api/v5/orbit/currency-sentiment-query',{'ccy':'BTC','period':'24h'},timeout=4)
            cap['news_ready']=True
        except transport.ConnectionError as exc: cap.update(news_ready=False,news_error=exc.code)
    with registry_guard():
        state=load();current=_connection(state,identity)
        if current['generation']!=c['generation']: raise AccountChangeError('授权在检查期间发生改变，请重试')
        current['capabilities'][mode]=cap;current['status']='identity_verified' if uid else 'unavailable'
        save(state,'connection_probed',{'connection_id':identity,'mode':mode,'read_ready':cap['read_ready']})
    return cap


def _flat(connection,mode):
    if connection is None: return
    for path,params in (('/api/v5/account/positions',{'instType':'SWAP'}),('/api/v5/trade/orders-pending',{'instType':'SWAP'}),
                        ('/api/v5/trade/orders-algo-pending',{'ordType':'oco'}),('/api/v5/trade/orders-algo-pending',{'ordType':'conditional'}),
                        ('/api/v5/trade/orders-algo-pending',{'ordType':'trigger'}),
                        ('/api/v5/trade/orders-algo-pending',{'ordType':'move_order_stop'}),
                        ('/api/v5/trade/orders-algo-pending',{'ordType':'iceberg'}),
                        ('/api/v5/trade/orders-algo-pending',{'ordType':'twap'})):
        rows=transport.request(connection,'probe',mode,'GET',path,params,timeout=4)
        if len(rows)>=100: raise AccountChangeError('订单列表可能分页，不能确认无在途风险')
        if path.endswith('/positions'):
            import math
            if any('pos' not in r or not math.isfinite(float(r['pos'])) for r in rows): raise AccountChangeError('持仓数量不可核验')
            if any(abs(float(r['pos']))>0 for r in rows): raise AccountChangeError('账户仍有持仓，不能解绑、换号或切换环境')
        elif rows: raise AccountChangeError('账户仍有挂单或保护单，不能解绑、换号或切换环境')


def _legacy_connection(mode):
    from scripts.okx_runtime import legacy_environment
    env=legacy_environment(mode=mode)
    if mode!=legacy_environment().mode and env.source=='legacy-or-oauth': return None
    if not env.configured:
        if not transport.AUTH_BINARY.exists() and not any((DATA/n).exists() for n in ('account_initial_state.json','position_trackers.json','trading_ledger.json')): return None
        raise AccountChangeError('旧连接无法核验；不能将未知账户视为空仓')
    return {'id':'legacy','auth_type':'api_key','mode':mode,'site':'global','credentials':{'api_key':env.api_key,'secret_key':env.secret_key,'passphrase':env.passphrase}}


def _current(state,mode):
    identity=state['bindings'].get(mode)
    if identity: return _connection(state,identity)
    if state['managed']: return None
    from scripts.okx_runtime import legacy_environment
    if not legacy_environment(mode=mode).configured and mode!=legacy_environment().mode: return None
    return _legacy_connection(mode)


def _unresolved():
    from scripts import strategy_evidence
    if not strategy_evidence.DB_PATH.exists(): return
    import sqlite3
    from contextlib import closing
    with closing(sqlite3.connect(strategy_evidence.DB_PATH.resolve().as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT 1 FROM intents WHERE state IN ('unknown','acknowledged') LIMIT 1").fetchone():
            raise AccountChangeError('存在未确认交易意图，先完成对账再更换连接')


def _archive_runtime(keep_financial=False):
    # Fixed file allowlist; history is moved into an audit archive, never deleted.
    target=DATA/'account-switch-archive'/uuid.uuid4().hex
    names=('ai_brain_decisions.json','trading_state.json','trading_ledger.json','position_trackers.json',
           'account_initial_state.json','web_data.json','state_snapshot.json','ledger_sync_status.json')
    if keep_financial: names=tuple(n for n in names if n not in {'trading_ledger.json','account_initial_state.json'})
    for name in names:
        source=DATA/name
        if source.is_file():
            target.mkdir(parents=True,exist_ok=True)
            os.replace(source,target/name)


def bind(purpose,identity,confirmation):
    if purpose not in {'demo','live','news'} or confirmation!='BIND '+purpose.upper():
        raise AccountChangeError('请确认绑定用途')
    with (registry_guard() if purpose=='news' else mutation_guard()):
        state=load()
        if identity and state['bindings'].get(purpose)==identity:
            return {**public_status(),'no_change':True}
        was_managed=state['managed'];c=_connection(state,identity);mode='live' if purpose=='news' else purpose
        cap=c.get('capabilities',{}).get(mode,{})
        if c['status']!='identity_verified': raise AccountChangeError('连接身份尚未核验，或正在重新授权')
        if not cap.get('account_uid') or not 0<=time.time()-cap.get('checked_at',0)<=300:
            raise AccountChangeError('请先核验目标环境的账户身份，结果有效期5分钟')
        if purpose=='news' and not cap.get('news_ready'): raise AccountChangeError('资讯读取能力尚未通过检查')
        if purpose!='news':
            if not cap.get('read_ready'): raise AccountChangeError('账户必要读取接口未全部通过')
            if c['auth_type']=='oauth': raise AccountChangeError('OAuth交易写入与保护闭环尚未验证，不能绑定自动交易')
            old_connection=_current(state,purpose)
            _unresolved();_flat(old_connection,purpose);_flat(c,purpose)
            same_account=_canonicalize(old_connection,c,purpose)
            if not state['managed']:
                from scripts.okx_runtime import legacy_environment
                state['active_mode']=legacy_environment().mode
                if purpose!=state['active_mode']: _flat(_current(state,state['active_mode']),state['active_mode'])
                # Preserve the other existing key binding explicitly; no hidden fallback after migration.
                other='live' if purpose=='demo' else 'demo'
                old=legacy_environment(mode=other)
                if old.configured and (other==state['active_mode'] or old.source=='separate-credentials'):
                    copied=uuid.uuid4().hex
                    state['connections'][copied]={**_legacy_connection(other),'id':copied,'label':'原有'+other+'连接',
                        'status':'legacy_preserved','generation':1,'capabilities':{},'created_at':time.time()}
                    _canonicalize(_legacy_connection(other),state['connections'][copied],other)
                    state['bindings'][other]=copied
                state['managed']=True
            if purpose==state['active_mode'] or not was_managed: _archive_runtime(keep_financial=same_account or purpose!=state['active_mode'])
            state.setdefault('generations',{'demo':0,'live':0})[purpose]+=1
        state['bindings'][purpose]=identity
        if purpose=='news': _invalidate_news_cache('binding_changed')
        save(state,'purpose_bound',{'purpose':purpose,'connection_id':identity})
    return public_status()


def unbind(purpose,confirmation):
    if purpose not in {'demo','live','news'} or confirmation!='UNBIND '+purpose.upper(): raise AccountChangeError('解绑确认不匹配')
    with (registry_guard() if purpose=='news' else mutation_guard()):
        state=load();identity=state['bindings'].get(purpose)
        if not identity: raise AccountChangeError('该用途没有受管理的绑定；旧配置请先迁移')
        if purpose!='news':
            _unresolved();_flat(_connection(state,identity),purpose)
            if purpose==state['active_mode']: _archive_runtime()
            state.setdefault('generations',{'demo':0,'live':0})[purpose]+=1
        state['bindings'][purpose]=None
        if purpose=='news': _invalidate_news_cache('unconfigured')
        save(state,'purpose_unbound',{'purpose':purpose,'connection_id':identity,'remote_revoked':False})
    return {'state':public_status(),'remote_revoked':False,'message':'仅解除本系统用途绑定；未声称撤销交易所授权'}


def activate(mode,confirmation):
    if mode not in {'demo','live'} or confirmation!='ACTIVATE '+mode.upper(): raise AccountChangeError('环境切换确认不匹配')
    with mutation_guard():
        state=load()
        if not state['managed'] or not state['bindings'].get(mode): raise AccountChangeError('请先完成该环境的安全绑定')
        if state['active_mode']==mode: return public_status()
        target=_current(state,mode);cap=target.get('capabilities',{}).get(mode,{})
        if not cap.get('read_ready') or not 0<=time.time()-cap.get('checked_at',0)<=300: raise AccountChangeError('目标账户需重新检查接口能力')
        _unresolved();_flat(_current(state,state['active_mode']),state['active_mode']);_flat(target,mode)
        state['active_mode']=mode;_archive_runtime();save(state,'environment_activated',{'mode':mode})
    return public_status()


def delete(identity,confirmation):
    if confirmation!='DELETE CONNECTION': raise AccountChangeError('删除确认不匹配')
    with registry_guard():
        state=load();c=_connection(state,identity)
        if identity in state['bindings'].values(): raise AccountChangeError('请先解除所有用途绑定')
        if c['status']=='authorization_pending' and time.time()<c.get('authorization_expires_at',c.get('authorization_started_at',0)+1800):
            raise AccountChangeError('授权流程仍在进行，过期后再删除，避免留下异步写入的凭据')
        home=transport.oauth_home(identity)
        if home.exists():
            expected=transport.OAUTH_ROOT.resolve()/identity
            if home.is_symlink() or home.resolve()!=expected or expected.parent!=transport.OAUTH_ROOT.resolve():
                raise AccountChangeError('授权目录归属无法核验，不能自动清理')
            import shutil
            shutil.rmtree(home)
        state['connections'].pop(identity);transport.invalidate(identity);save(state,'connection_removed',{'connection_id':identity})
    return {'removed':True,'local_directory_removed':True,'remote_revoked':False,
            'message':'本地连接与受管理目录已删除；未声称撤销OKX授权，请到官方授权管理核对。'}


def resolve_environment():
    state=load()
    if not state['managed']: return None
    mode=state['active_mode'];identity=state['bindings'].get(mode)
    from scripts.okx_runtime import OKXEnvironment
    if not identity:
        return OKXEnvironment(mode,'','','',source='account-center-unbound',connection_id='disabled-'+mode,binding_version=state.get('generations',{}).get(mode,0))
    c=_connection(state,identity)
    if c['auth_type']!='api_key': raise AccountChangeError('OAuth交易连接尚未通过写入闭环')
    k=c['credentials']
    return OKXEnvironment(mode,k['api_key'],k['secret_key'],k['passphrase'],source='account-center',
                          connection_id=identity,binding_version=state.get('generations',{}).get(mode,0),account_scope=c.get('capabilities',{}).get(mode,{}).get('canonical_scope',''))


def assert_current(environment):
    if getattr(environment,'source','')=='account-center-unbound': raise AccountChangeError('当前交易用途已解绑，禁止借用旧Key或全局OAuth')
    if not getattr(environment,'connection_id',''):
        if load()['managed']: raise AccountChangeError('账户中心已启用，未绑定版本的旧连接不能继续执行')
        return
    current=resolve_environment()
    if current is None or current.connection_id!=environment.connection_id or current.mode!=environment.mode or current.binding_version!=environment.binding_version:
        raise AccountChangeError('账户绑定已改变，旧任务和旧决策不能继续执行')


def news_connection():
    state=load();identity=state['bindings'].get('news')
    if not identity: raise AccountChangeError('资讯连接未绑定；不会借用交易Key或自动切换认证')
    return copy.deepcopy(_connection(state,identity))


def import_legacy(mode):
    c=_legacy_connection(mode)
    if c is None: raise AccountChangeError('该环境没有可导入的旧Key')
    return create('原有'+('模拟盘' if mode=='demo' else '实盘')+' Key','api_key',mode,credentials=c['credentials'])


def _invalidate_news_cache(status):
    from scripts.public_market import atomic_json
    atomic_json(DATA/'news_sentiment.json',{'schema':2,'connection_status':status,'updated_at':None,'last_success_at':None,
        'latest_news':[],'coins_sentiment':{},'stale_sections':True,'macro_sentiment':'UNKNOWN（资讯连接已改变）',
        'message':'资讯绑定发生变化，等待新连接成功读取；不会复用旧授权的内容。'})


def _uid_scope(mode,uid):
    import hashlib
    return 'okx:'+mode+':account:'+hashlib.sha256(str(uid).encode()).hexdigest()[:24]


def _canonicalize(old,new,mode):
    def identity(c):
        cap=c.get('capabilities',{}).get(mode,{})
        uid=cap.get('account_uid')
        if not uid:
            rows=transport.request(c,'probe',mode,'GET','/api/v5/account/config',{},timeout=4,verify_identity=False)
            uid=str(rows[0].get('uid') or '') if len(rows)==1 else ''
        if not uid: raise AccountChangeError('账户UID无法核验，不能迁移风险与账本归属')
        return uid
    uid=identity(new);same=old is not None and identity(old)==uid
    scope=_uid_scope(mode,uid)
    if same:
        if old['id']=='legacy':
            from scripts.okx_runtime import legacy_environment
            scope=legacy_environment(mode=mode).identity
        else: scope=old.get('capabilities',{}).get(mode,{}).get('canonical_scope') or scope
    cap=new.setdefault('capabilities',{}).setdefault(mode,{})
    cap.update(account_uid=uid,canonical_scope=scope)
    return same


def display_label(label):
    names={'原有demo Key（待核验）':'原有模拟盘 Key','原有live Key（待核验）':'原有实盘 Key','原有demo连接':'原有模拟盘连接','原有live连接':'原有实盘连接'}
    return names.get(label,label)


def credentials_display(credentials,auth_type):
    if auth_type!='api_key':return {'api_key':'不适用（OAuth）','secret_key_saved':False,'passphrase_saved':False}
    key=str(credentials.get('api_key') or '')
    masked=(key[:4]+'********'+key[-4:]) if len(key)>12 else '********' if key else '未配置'
    return {'api_key':masked,'secret_key_saved':bool(credentials.get('secret_key')),'passphrase_saved':bool(credentials.get('passphrase'))}


def trading_connection_summary(state=None):
    state=load() if state is None else state
    from scripts.okx_runtime import legacy_environment
    mode=state['active_mode'] or legacy_environment().mode
    if state['managed']:
        c=state['connections'].get(state['bindings'].get(mode))
        configured=bool(c and c['auth_type']=='api_key' and all(c.get('credentials',{}).get(k) for k in ('api_key','secret_key','passphrase')))
        return {'mode':mode,'configured':configured,'management':'account_center','auth_type':c['auth_type'] if c else 'none',
                'status':'configured' if configured else 'unbound' if not c else 'not_ready'}
    env=legacy_environment(mode=mode)
    return {'mode':mode,'configured':env.configured,'management':'legacy','auth_type':'api_key' if env.configured else 'none',
            'status':'configured' if env.configured else 'unconfigured'}


def runtime_credentials(llm_configured=False):
    c=trading_connection_summary()
    # Keep historical names while publishing the same canonical contract as /health.
    return {'okx':c['configured'],'llm':bool(llm_configured),'okx_configured':c['configured'],
            'llm_configured':bool(llm_configured),'simulated_trading':c['mode']=='demo','okx_environment':c['mode'],
            'credential_source':c['auth_type'],'connection_status':c['status']}


def set_manual_close(enabled,confirmation):
    expected='ENABLE MANUAL CLOSE' if enabled is True else 'DISABLE MANUAL CLOSE'
    if type(enabled) is not bool or confirmation!=expected:raise AccountChangeError('手动平仓开关确认不匹配')
    from okxquant_backend.settings_store import update_env
    with registry_guard():
        update_env({'OKXQUANT_MANUAL_CLOSE_ENABLED':'1' if enabled else '0'})
    return {'manual_close_enabled':enabled,'message':'仅更新后台手动平仓权限；不会平仓，也不影响独立持仓风控。'}
