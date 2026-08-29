import json
import urllib.request


EDGE_LLM_URL='http://127.0.0.1:8080/v1/chat/completions'


def edge_chat(messages,max_tokens=4096,on_delta=None):
    payload=json.dumps({
        'model':'engine-64k',
        'messages':messages,
        'stream':bool(on_delta),
        'max_tokens':max_tokens,
        'temperature':0.7,
    }).encode()
    request=urllib.request.Request(
        EDGE_LLM_URL,
        data=payload,
        headers={'Authorization':'Bearer local-key','Content-Type':'application/json'},
    )
    with urllib.request.urlopen(request,timeout=900) as response:
        if on_delta is None:
            result=json.loads(response.read()); return result.get('choices',[{}])[0].get('message',{}).get('content','')
        chunks=[]; done=False; received=False
        for raw in response:
            line=raw.decode('utf-8',errors='replace').strip()
            if not line.startswith('data:'): continue
            data=line[5:].strip()
            if data=='[DONE]': done=True; break
            try: event=json.loads(data)
            except json.JSONDecodeError as exc: raise RuntimeError('Edge LLM returned malformed streaming JSON') from exc
            if event.get('error'):
                detail=event['error']; message=detail.get('message') if isinstance(detail,dict) else str(detail)
                raise RuntimeError('Edge LLM streaming error: '+str(message))
            choices=event.get('choices') or []
            if not choices: continue
            received=True; delta=choices[0].get('delta',{}).get('content') or ''
            if delta: chunks.append(delta); on_delta(delta)
        if not done: raise RuntimeError('Edge LLM stream ended before [DONE]')
        if not received: raise RuntimeError('Edge LLM stream contained no choices')
        return ''.join(chunks)
