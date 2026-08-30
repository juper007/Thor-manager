"""Remote webhook notifications with public HTTPS-only destination validation."""
import json
from tools.web import post_json


class NotificationService:
    def __init__(self,store,timeout=10): self.store=store; self.timeout=timeout
    def send(self,event,payload):
        results=[]; body=json.dumps({'event':event,'payload':payload},ensure_ascii=False).encode()
        for endpoint in self.store.notification_endpoints():
            url=endpoint['url']
            try:
                status=post_json(url,body,self.timeout)
                results.append({'name':endpoint['name'],'status':'sent','http_status':status})
            except Exception as exc: results.append({'name':endpoint['name'],'status':'failed','error':str(exc)[:300]})
        return results
