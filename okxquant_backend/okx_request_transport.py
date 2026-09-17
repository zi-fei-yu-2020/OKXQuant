"""Bounded transient READ retries for signed OKX REST calls.
No account/environment fallback, no order retry, no raw upstream body in diagnostics.
"""
import errno,json,logging,math,re,socket,ssl,time
import urllib.error,urllib.request
from datetime import datetime,timezone
from email.utils import parsedate_to_datetime

LOG=logging.getLogger(__name__)
RETRY_HTTP={429,500,502,503,504}
RETRY_API={'50011','50061'}
MAX_BODY=4*1024*1024
LABELS={'http_error':'交易所HTTP请求失败','timeout':'交易所请求超时','network_error':'交易所网络暂不可用',
        'certificate_error':'交易所TLS证书校验失败','deadline_exceeded':'交易所读取总预算耗尽',
        'invalid_response':'交易所响应格式无效','rate_limited':'交易所限流'}

class OKXRequestError(RuntimeError):
    def __init__(self,details):
        self.details=dict(details);self.status_code=details.get('http_status') or 0
        self.attempts=details.get('attempts',0);self.category=details.get('category','network_error')
        label=LABELS.get(self.category,LABELS['network_error'])
        if self.status_code:label+=f'（HTTP {self.status_code}）'
        super().__init__(f'{label}，已尝试 {self.attempts} 次；未切换账户或交易环境')


def safe_headers(headers):
    headers={str(k).lower():str(v) for k,v in (headers or {}).items()}
    ray=headers.get('cf-ray','')
    return {'edge_request_id':ray if re.fullmatch(r'[a-fA-F0-9]{8,32}-[A-Z]{3}',ray) else None,
            'response_type':'json' if 'application/json' in headers.get('content-type','').lower() else 'html' if 'text/html' in headers.get('content-type','').lower() else 'other',
            'edge':'cloudflare' if headers.get('server','').lower()=='cloudflare' else 'unknown'}


def retry_delay(value,attempt):
    if value:
        try:seconds=float(value)
        except (TypeError,ValueError):
            try:
                date=parsedate_to_datetime(value)
                if date.tzinfo is None:date=date.replace(tzinfo=timezone.utc)
                seconds=(date-datetime.now(timezone.utc)).total_seconds()
            except (ValueError,TypeError,OverflowError):seconds=-1
        if math.isfinite(seconds) and seconds>=0:return seconds
    return .3*(2**(attempt-1))


def network_error(exc):
    cause=exc.reason if isinstance(exc,urllib.error.URLError) else exc
    if isinstance(cause,ssl.SSLError):return 'certificate_error',False
    if isinstance(cause,TimeoutError) or getattr(cause,'errno',None)==errno.ETIMEDOUT:return 'timeout',True
    transient=isinstance(cause,ConnectionError) or getattr(cause,'errno',None) in {errno.ECONNRESET,errno.ECONNREFUSED,errno.EPIPE,socket.EAI_AGAIN}
    return 'network_error',transient


def emit(scope,kind,details):
    # Diagnostics must not turn an already completed call into a repeatable write.
    try:
        from scripts.strategy_evidence import best_effort
        best_effort(scope,kind,details)
    except Exception:pass


def request_json(factory,*,method,path,timeout,scope):
    if isinstance(timeout,bool) or not math.isfinite(timeout) or timeout<=0:raise ValueError('Invalid OKX request timeout')
    method=method.upper();readonly=method=='GET';maximum=3 if readonly else 1
    start=time.monotonic();deadline=start+timeout
    # Strip any untrusted query/string. Credential values and endpoint URL are never retained.
    endpoint=path if re.fullmatch(r'/api/v5/[a-zA-Z0-9_/-]+',path) else 'private_endpoint'
    history=[]
    for attempt in range(1,maximum+1):
        remaining=deadline-time.monotonic()
        details={'method':method,'endpoint':endpoint,'attempts':attempt,'max_attempts':maximum,
                 'category':'network_error','http_status':None,'provider_code':None}
        retryable=False;delay_value=None;failure=None
        if remaining<=0:
            details.update(category='deadline_exceeded',attempts=attempt-1)
        else:
            # Build a fresh timestamp/signature and recheck account binding on EVERY attempt.
            request=factory()
            remaining=deadline-time.monotonic()
            try:
                if remaining<=0:raise TimeoutError()
                per_attempt=min(remaining,timeout/2) if readonly else remaining
                with urllib.request.urlopen(request,timeout=per_attempt) as response:
                    body=response.read(MAX_BODY+1)
                if time.monotonic()>deadline:
                    details['category']='deadline_exceeded'
                elif len(body)>MAX_BODY:
                    details['category']='invalid_response'
                else:
                    payload=json.loads(body.decode('utf-8'))
                    if not isinstance(payload,dict) or 'code' not in payload:
                        details['category']='invalid_response'
                    elif readonly and str(payload.get('code')) in RETRY_API:
                        details.update(category='rate_limited',http_status=200,provider_code=str(payload['code']))
                        retryable=True
                    elif str(payload.get('code'))=='0' and (not isinstance(payload.get('data'),list) or any(not isinstance(r,dict) for r in payload['data'])):
                        details['category']='invalid_response'
                    else:
                        if attempt>1 and str(payload.get('code'))=='0':emit(scope,'exchange_read_recovered',{'method':method,'endpoint':endpoint,'attempts':attempt,'elapsed_ms':round((time.monotonic()-start)*1000),'failures':history})
                        return payload
            except urllib.error.HTTPError as exc:
                failure=exc;details.update(category='http_error',http_status=exc.code,**safe_headers(exc.headers))
                delay_value=exc.headers.get('Retry-After') if exc.headers else None
                retryable=readonly and exc.code in RETRY_HTTP
                # Never read/log the error body: it may echo authentication fields.
                exc.close()
            except (urllib.error.URLError,OSError) as exc:
                failure=exc;category,transient=network_error(exc);details['category']=category
                retryable=readonly and transient
            except (ValueError,UnicodeError) as exc:
                failure=exc;details['category']='invalid_response'
        details['elapsed_ms']=round((time.monotonic()-start)*1000)
        LOG.warning('OKX request failure method=%s endpoint=%s category=%s status=%s attempt=%s/%s edge_request_id=%s',
                    method,endpoint,details['category'],details['http_status'],details['attempts'],maximum,details.get('edge_request_id'))
        history.append(dict(details));delay=retry_delay(delay_value,attempt)
        if not retryable or attempt==maximum or delay>=deadline-time.monotonic():
            details['failures']=history
            emit(scope,'exchange_read_failure' if readonly else 'exchange_write_unknown',details)
            raise OKXRequestError(details) from None
        time.sleep(delay)
    raise AssertionError('unreachable')
