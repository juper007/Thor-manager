import json
import re


REDACTED='[REDACTED]'
SENSITIVE_KEYS=re.compile(r'(?:password|passwd|secret|token|api[_-]?key|authorization|cookie)',re.I)
INLINE_SECRETS=re.compile(
    r'(?i)(\b(?:password|passwd|secret|token|api[_-]?key|authorization)\b\s*[:=]\s*)([^\s,;]+)'
)
AUTH_CREDENTIAL=re.compile(r'(?i)\b(Basic|Bearer)\s+[^\s,;]+')
CURL_USER=re.compile(r'(?i)(\bcurl\b[^\r\n]*?\s-u\s+)(?:"[^"]*"|\'[^\']*\'|[^\s]+)')


def redact(value):
    """Return a JSON-compatible copy with common credentials removed."""
    if isinstance(value,dict):
        return {str(key):(REDACTED if SENSITIVE_KEYS.search(str(key)) else redact(item)) for key,item in value.items()}
    if isinstance(value,(list,tuple)): return [redact(item) for item in value]
    if isinstance(value,str):
        value=AUTH_CREDENTIAL.sub(lambda match:match.group(1)+' '+REDACTED,value)
        value=CURL_USER.sub(lambda match:match.group(1)+REDACTED,value)
        return INLINE_SECRETS.sub(r'\1'+REDACTED,value)
    if value is None or isinstance(value,(bool,int,float)): return value
    return redact(str(value))


def redacted_json(value):
    return json.dumps(redact(value),ensure_ascii=False,separators=(',',':'))
