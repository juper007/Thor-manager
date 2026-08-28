import json
import urllib.request


EDGE_LLM_URL='http://127.0.0.1:8080/v1/chat/completions'


def edge_chat(messages,max_tokens=4096):
    payload=json.dumps({
        'model':'engine-64k',
        'messages':messages,
        'stream':False,
        'max_tokens':max_tokens,
        'temperature':0.7,
    }).encode()
    request=urllib.request.Request(
        EDGE_LLM_URL,
        data=payload,
        headers={'Authorization':'Bearer local-key','Content-Type':'application/json'},
    )
    with urllib.request.urlopen(request,timeout=900) as response:
        result=json.loads(response.read())
    return result.get('choices',[{}])[0].get('message',{}).get('content','')
