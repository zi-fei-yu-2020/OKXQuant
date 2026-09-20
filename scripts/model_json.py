"""Strict model text handling and ONE bounded fresh generation, never JSON guessing.
No network or order writes here. Retry uses the original frozen prompt, not partial
JSON; downstream must run the full trading validator and final live risk gateway.
"""
import hashlib
import time
from scripts.trading_prompt import ContractError, parse_response

BUDGET_SECONDS = 20.0


def text_content(value):
    if value is None:
        return ''
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        chunks = []
        for part in value:
            if not isinstance(part, dict) or part.get('type') not in ('text', 'output_text') or not isinstance(part.get('text'), str):
                raise ContractError('模型文本包含非文本内容，不能转换为交易JSON')
            chunks.append(part['text'])
        return ''.join(chunks).strip()
    raise ContractError('模型文本类型不受支持')


def verify_completion(response, protocol):
    """Check envelope structure as well as finish flags; never accept refusal text."""
    def require(condition):
        if not condition:
            raise ContractError('Model response is incomplete, refused, or malformed')
    require(isinstance(response, dict))
    if protocol == 'claude_messages':
        require(response.get('stop_reason') in (None, 'end_turn', 'stop_sequence'))
        blocks = response.get('content', [])
        require(isinstance(blocks, list))
        for block in blocks:
            require(isinstance(block, dict))
            require(block.get('type') in ('text', 'thinking', 'redacted_thinking'))
            if block.get('type') == 'text':
                require(isinstance(block.get('text'), str))
    elif protocol == 'openai_responses':
        require(response.get('status') in (None, 'completed') and not response.get('incomplete_details'))
        require(not response.get('error'))
        if response.get('output_text') is not None:
            require(isinstance(response['output_text'], str))
        output = response.get('output', [])
        require(isinstance(output, list))
        for item in output:
            require(isinstance(item, dict))
            require(item.get('type') in ('message', 'reasoning'))
            require(item.get('status') in (None, 'completed'))
            if item.get('type') == 'message':
                blocks = item.get('content', [])
                require(isinstance(blocks, list))
                for block in blocks:
                    require(isinstance(block, dict))
                    require(block.get('type') == 'output_text' and isinstance(block.get('text'), str))
    else:
        choices = response.get('choices')
        require(isinstance(choices, list) and len(choices) == 1)
        choice = choices[0]
        require(isinstance(choice, dict))
        require(choice.get('finish_reason') in (None, 'stop'))
        message = choice.get('message', {})
        require(isinstance(message, dict))
        require(not message.get('refusal') and not message.get('tool_calls') and not message.get('function_call'))
        text_content(message.get('content'))


def decode_with_regeneration(initial, regenerate=None, *, report=None, clock=time.monotonic):
    """initial/regenerate return text. Transport errors are not retried here.
    Share a single report with caller so failures are archived too. No raw model
    content is published (or interpreted as a partial set of trading instructions).
    """
    report = report if report is not None else {}
    report.update(version='strict-json-v2', status='initial', attempted=False, failures=[])
    def decode(call):
        text = None
        try:
            text = call()
            report['output_chars'] = len(text) if isinstance(text, str) else 0
            return parse_response(text)
        except ContractError as exc:
            diagnostic = {'reason': str(exc)[:300], 'chars': len(text) if isinstance(text, str) else 0}
            if isinstance(text, str):
                diagnostic['sha256'] = hashlib.sha256(text.encode()).hexdigest()
            report['failures'].append(diagnostic)
            raise
    try:
        obj = decode(initial)
        report['status'] = 'valid'
        return obj
    except ContractError:
        if regenerate is None:
            report['status'] = 'rejected'
            raise
    report.update(status='regenerating', attempted=True)
    started = clock()
    try:
        obj = decode(lambda: regenerate(timeout=BUDGET_SECONDS, max_attempts=1))
        if clock() - started > BUDGET_SECONDS:
            raise ContractError('JSON重新生成超出20秒预算，输出未采纳')
        report['status'] = 'regenerated'  # Syntax only, NOT contract or order approval.
        return obj
    except Exception:
        report['status'] = 'rejected'
        raise
    finally:
        report['duration_ms'] = round((clock() - started) * 1000)
