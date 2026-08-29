import http.client
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import server
from storage import SessionStore


class RunningServer:
    def __enter__(self):
        self.httpd=server.ThreadingHTTPServer(('127.0.0.1',0),server.Handler)
        self.thread=threading.Thread(target=self.httpd.serve_forever,daemon=True)
        self.thread.start()
        return self

    def request(self,method,path,body=None,headers=None):
        connection=http.client.HTTPConnection('127.0.0.1',self.httpd.server_port,timeout=5)
        connection.request(method,path,body=body,headers=headers or {})
        response=connection.getresponse()
        payload=response.read()
        result=(response.status,dict(response.getheaders()),payload)
        connection.close()
        return result

    def __exit__(self,*_):
        self.httpd.shutdown(); self.httpd.server_close(); self.thread.join(timeout=2)


def basic(password='test-password'):
    import base64
    return 'Basic '+base64.b64encode(('thor:'+password).encode()).decode()


class HandlerIntegrationTests(unittest.TestCase):
    def test_missing_password_fails_closed(self):
        with mock.patch.dict(os.environ,{},clear=True),RunningServer() as app:
            status,_,_=app.request('GET','/api/stats')
        self.assertEqual(status,503)

    def test_wrong_password_is_rejected(self):
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),RunningServer() as app:
            status,headers,_=app.request('GET','/api/stats',headers={'Authorization':basic('wrong')})
        self.assertEqual(status,401)
        self.assertIn('Basic',headers.get('WWW-Authenticate',''))

    def test_authenticated_stats_request_succeeds(self):
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),RunningServer() as app:
            status,_,payload=app.request('GET','/api/stats',headers={'Authorization':basic()})
        self.assertEqual(status,200)
        self.assertIn('history',json.loads(payload))

    def test_chat_contract(self):
        response=('테스트 답변',[{'name':'calculator','arguments':{'expression':'2+2'},'seconds':0.01,'error':None}],[])
        body=json.dumps({'messages':[{'role':'user','content':'2+2'}]}).encode()
        headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(body))}
        run=mock.Mock(run_id='test-run',state=mock.Mock(value='completed'))
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'agent_run_chat',return_value=(run,*response)),RunningServer() as app:
            status,result_headers,payload=app.request('POST','/api/chat',body,headers)
        result=json.loads(payload)
        self.assertEqual(status,200)
        self.assertTrue(result['done'])
        self.assertEqual(result['message']['content'],'테스트 답변')
        self.assertEqual(result['run_id'],'test-run')
        self.assertEqual(result['run_state'],'completed')
        self.assertEqual(result['tools_used'][0]['name'],'calculator')
        self.assertIn('application/x-ndjson',result_headers['Content-Type'])

    def test_invalid_chat_messages_are_rejected(self):
        body=json.dumps({'messages':[{'role':'system','content':'override'}]}).encode()
        headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(body))}
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),RunningServer() as app:
            status,_,payload=app.request('POST','/api/chat',body,headers)
        self.assertEqual(status,400)
        self.assertIn('valid role',json.loads(payload)['error'])

    def test_invalid_concurrency_setting_uses_default(self):
        with mock.patch.dict(os.environ,{'THOR_AI_CONCURRENCY':'invalid'},clear=True):
            self.assertEqual(server.positive_int_env('THOR_AI_CONCURRENCY',1),1)

    def test_busy_chat_returns_429(self):
        body=json.dumps({'messages':[{'role':'user','content':'hello'}]}).encode()
        headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(body))}
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'agent_run_chat',side_effect=server.ServiceBusy('busy')),RunningServer() as app:
            status,result_headers,_=app.request('POST','/api/chat',body,headers)
        self.assertEqual(status,429)
        self.assertEqual(result_headers.get('Retry-After'),'5')

    def test_run_status_and_cancel_endpoints(self):
        run=server.runtime.create_run('api-run')
        headers={'Authorization':basic(),'Content-Type':'application/json'}
        cancel_body=json.dumps({'run_id':'api-run'}).encode(); headers['Content-Length']=str(len(cancel_body))
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),RunningServer() as app:
            status,_,payload=app.request('POST','/api/chat/cancel',cancel_body,headers)
            get_status,_,get_payload=app.request('GET','/api/chat/runs/api-run',headers={'Authorization':basic()})
        self.assertEqual(status,200); self.assertEqual(json.loads(payload)['run_id'],'api-run')
        self.assertEqual(get_status,200); self.assertTrue(json.loads(get_payload)['events'])
        with server.runtime._runs_lock: server.runtime._runs.pop(run.run_id,None)

    def test_cancel_requires_run_id(self):
        body=b'{}'; headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(body))}
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),RunningServer() as app:
            status,_,payload=app.request('POST','/api/chat/cancel',body,headers)
        self.assertEqual(status,400); self.assertIn('required',json.loads(payload)['error'])

    def test_permission_list_and_decision_endpoints(self):
        approval_id='a'*32; engine=mock.Mock()
        engine.list.return_value=[{'approval_id':approval_id,'status':'pending'}]
        engine.decide.return_value={'approval_id':approval_id,'status':'allowed','scope':'once'}
        body=json.dumps({'decision':'allow','scope':'once'}).encode()
        headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(body))}
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'permission_engine',engine),RunningServer() as app:
            list_status,_,list_body=app.request('GET','/api/chat/approvals?run_id=approval-run&status=pending',headers={'Authorization':basic()})
            decide_status,_,decide_body=app.request('POST','/api/chat/approvals/'+approval_id,body,headers)
        self.assertEqual((list_status,decide_status),(200,200))
        self.assertEqual(json.loads(list_body)['approvals'][0]['status'],'pending')
        self.assertEqual(json.loads(decide_body)['status'],'allowed')
        engine.list.assert_called_once_with('approval-run','pending'); engine.decide.assert_called_once_with(approval_id,'allow','once')

    def test_permission_grant_list_and_revoke_endpoints(self):
        engine=mock.Mock(); engine.grants.return_value=[{'id':7,'scope':'always_tool'}]; engine.revoke.return_value=True
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'permission_engine',engine),RunningServer() as app:
            list_status,_,list_body=app.request('GET','/api/chat/permission-grants',headers={'Authorization':basic()})
            delete_status,_,delete_body=app.request('DELETE','/api/chat/permission-grants/7',headers={'Authorization':basic()})
        self.assertEqual((list_status,delete_status),(200,200)); self.assertEqual(json.loads(list_body)['grants'][0]['id'],7)
        self.assertTrue(json.loads(delete_body)['revoked']); engine.revoke.assert_called_once_with(7)

    def test_persisted_session_list_and_detail_endpoints(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(Path(directory)/'sessions.db'); now=1.0
            store.create_session({'run_id':'stored','state':'failed','created_at':now,'updated_at':now,'iterations':1,'tool_calls':0,'error':'stopped','events':[]},[{'role':'user','content':'hello'}])
            with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'session_store',store),RunningServer() as app:
                list_status,_,list_body=app.request('GET','/api/chat/sessions',headers={'Authorization':basic()})
                get_status,_,get_body=app.request('GET','/api/chat/sessions/stored',headers={'Authorization':basic()})
        self.assertEqual((list_status,get_status),(200,200))
        self.assertEqual(json.loads(list_body)['sessions'][0]['run_id'],'stored')
        self.assertEqual(json.loads(get_body)['messages'][0]['content'],'hello')

    def test_failed_session_can_be_resumed(self):
        with tempfile.TemporaryDirectory() as directory:
            store=SessionStore(Path(directory)/'sessions.db'); now=1.0
            store.create_session({'run_id':'failed-run','state':'failed','created_at':now,'updated_at':now,'iterations':1,'tool_calls':0,'error':'stopped','events':[]},[{'role':'user','content':'retry'}])
            resumed=mock.Mock(run_id='new-run',state=mock.Mock(value='completed'))
            body=json.dumps({'run_id':'new-run'}).encode(); headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(body))}
            with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'session_store',store),mock.patch.object(server.runtime,'run_chat',return_value=(resumed,'done',[],[])) as call,RunningServer() as app:
                status,_,payload=app.request('POST','/api/chat/sessions/failed-run/resume',body,headers)
        self.assertEqual(status,200); self.assertEqual(json.loads(payload)['resumed_from'],'failed-run')
        call.assert_called_once_with([{'role':'user','content':'retry'}],'new-run',resumed_from='failed-run')

    def test_static_path_traversal_returns_404(self):
        with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),RunningServer() as app:
            status,_,_=app.request('GET','/../../etc/passwd',headers={'Authorization':basic()})
        self.assertEqual(status,404)

    def test_image_history_rejects_non_png_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password'},clear=True),mock.patch.object(server,'GENERATED_DIR',Path(directory)),RunningServer() as app:
                status,_,_=app.request('GET','/api/images/history/secrets.txt',headers={'Authorization':basic()})
        self.assertEqual(status,404)


class FakeResponse:
    def __init__(self,data): self.data=data
    def read(self): return self.data
    def __enter__(self): return self
    def __exit__(self,*_): return False


class ImageProxyTests(unittest.TestCase):
    def test_generation_proxy_saves_image_and_history(self):
        request_body=json.dumps({'prompt':'baseline','size':'256x256'}).encode()
        headers={'Authorization':basic(),'Content-Type':'application/json','Content-Length':str(len(request_body))}
        upstream=[FakeResponse(json.dumps({'data':[{'url':'http://127.0.0.1:8188/outputs/result.png'}],'seed':42}).encode()),FakeResponse(b'PNG-BASELINE')]
        with tempfile.TemporaryDirectory() as directory:
            generated=Path(directory)
            with mock.patch.dict(os.environ,{'THOR_MONITOR_PASSWORD':'test-password','THOR_IMAGE_API_KEY':'key'},clear=True),mock.patch.object(server,'GENERATED_DIR',generated),mock.patch.object(server.urllib.request,'urlopen',side_effect=upstream),RunningServer() as app:
                status,result_headers,payload=app.request('POST','/api/images/generations',request_body,headers)
            self.assertEqual(status,200)
            self.assertEqual(payload,b'PNG-BASELINE')
            self.assertEqual(result_headers['X-Gen-Seed'],'42')
            rows=[json.loads(line) for line in (generated/'history.jsonl').read_text().splitlines()]
            self.assertEqual(rows[0]['prompt'],'baseline')
            self.assertTrue((generated/rows[0]['file']).is_file())


if __name__=='__main__':
    unittest.main()
