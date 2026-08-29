import tempfile
import threading
import time
import unittest
from pathlib import Path

from agent.permissions import PermissionEngine
from agent.runtime import AgentRuntime
from agent.state import AgentRun
from storage import SessionStore
from tools.base import RiskLevel,ToolSpec
from tools.registry import ToolRegistry


SCHEMA={'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}


def parser(text):
    return [{'name':'danger','arguments':{'value':'original'}}] if text=='tool' else []


class PermissionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.store=SessionStore(Path(self.temp.name)/'sessions.db')

    def tearDown(self): self.temp.cleanup()

    def runtime(self,replies,calls,ttl=5):
        iterator=iter(replies); registry=ToolRegistry()
        registry.register(ToolSpec('danger','Elevated test tool.',SCHEMA,lambda args:calls.append(dict(args)) or {'ok':True},RiskLevel.ELEVATED))
        engine=PermissionEngine(self.store,ttl)
        runtime=AgentRuntime('.',lambda _:next(iterator),registry,parser,lambda _:'skill',lambda text:text,
            session_store=self.store,permission_engine=engine,total_timeout=5)
        return runtime,engine

    def wait_pending(self,engine,run_id):
        deadline=time.time()+2
        while time.time()<deadline:
            rows=engine.list(run_id,'pending')
            if rows: return rows[0]
            time.sleep(.01)
        self.fail('permission request did not become pending')

    def test_elevated_tool_waits_for_exact_once_approval(self):
        calls=[]; runtime,engine=self.runtime(['tool','done'],calls); result=[]
        thread=threading.Thread(target=lambda:result.append(runtime.run_chat([{'role':'user','content':'go'}],'allow-once')))
        thread.start(); approval=self.wait_pending(engine,'allow-once')
        self.assertEqual(calls,[]); self.assertEqual(runtime.get_run('allow-once').state.value,'awaiting_approval')
        engine.decide(approval['approval_id'],'allow','once'); thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(calls,[{'value':'original'}]); self.assertEqual(result[0][1],'done')

    def test_read_policy_never_creates_an_approval(self):
        engine=PermissionEngine(self.store); run=AgentRun('read-only')
        spec=ToolSpec('reader','Read.',SCHEMA,lambda _:None,RiskLevel.READ)
        self.assertTrue(engine.authorize(run,spec,{'value':'x'})['allowed'])
        self.assertEqual(engine.list('read-only'),[])

    def test_safe_write_and_destructive_policies_require_approval(self):
        for risk in (RiskLevel.SAFE_WRITE,RiskLevel.DESTRUCTIVE):
            engine=PermissionEngine(); run=AgentRun('risk-'+risk.value); run.transition('planning'); run.transition('executing')
            spec=ToolSpec('tool_'+risk.value,'Risk test.',SCHEMA,lambda _:None,risk); result=[]
            thread=threading.Thread(target=lambda:result.append(engine.authorize(run,spec,{'value':'x'})))
            thread.start(); approval=self.wait_pending(engine,run.run_id); engine.decide(approval['approval_id'],'deny'); thread.join(2)
            self.assertFalse(result[0]['allowed']); self.assertEqual(result[0]['error_code'],'permission_denied')

    def test_denied_tool_is_reported_without_execution(self):
        calls=[]; runtime,engine=self.runtime(['tool','permission denied'],calls); result=[]
        thread=threading.Thread(target=lambda:result.append(runtime.run_chat([{'role':'user','content':'go'}],'deny')))
        thread.start(); approval=self.wait_pending(engine,'deny'); engine.decide(approval['approval_id'],'deny'); thread.join(2)
        self.assertEqual(calls,[]); self.assertEqual(result[0][2][0]['error_code'],'permission_denied')

    def test_argument_change_after_approval_is_rejected(self):
        calls=[]; runtime,engine=self.runtime(['tool','changed arguments rejected'],calls); result=[]
        thread=threading.Thread(target=lambda:result.append(runtime.run_chat([{'role':'user','content':'go'}],'tamper')))
        thread.start(); approval=self.wait_pending(engine,'tamper')
        with engine._condition: engine._pending[approval['approval_id']]['arguments']['value']='changed'
        engine.decide(approval['approval_id'],'allow'); thread.join(2)
        self.assertEqual(calls,[]); self.assertEqual(result[0][2][0]['error_code'],'permission_arguments_changed')

    def test_session_and_always_tool_grants_are_persisted(self):
        calls=[]; runtime,engine=self.runtime(['tool','done'],calls); result=[]
        thread=threading.Thread(target=lambda:result.append(runtime.run_chat([{'role':'user','content':'go'}],'session-grant')))
        thread.start(); approval=self.wait_pending(engine,'session-grant'); engine.decide(approval['approval_id'],'allow','session'); thread.join(2)
        self.assertIsNotNone(self.store.find_permission_grant('session-grant','danger','elevated'))
        self.assertIsNone(self.store.find_permission_grant('different-run','danger','elevated'))
        # Always-tool grants apply to future runs and survive a new engine instance.
        snapshot={'run_id':'always-source','state':'planning','created_at':time.time(),'updated_at':time.time(),'iterations':0,'tool_calls':0,'error':None,'events':[]}
        self.store.create_session(snapshot,[{'role':'user','content':'x'}])
        self.store.save_permission_grant('always_tool','always-source','danger','elevated')
        self.assertIsNotNone(PermissionEngine(self.store)._grant('future-run','danger','elevated'))

    def test_expired_request_does_not_execute(self):
        calls=[]; runtime,engine=self.runtime(['tool','expired'],calls,ttl=1); result=[]
        thread=threading.Thread(target=lambda:result.append(runtime.run_chat([{'role':'user','content':'go'}],'expires')))
        thread.start(); self.wait_pending(engine,'expires'); thread.join(2)
        self.assertFalse(thread.is_alive()); self.assertEqual(calls,[]); self.assertEqual(result[0][2][0]['error_code'],'permission_expired')


if __name__=='__main__': unittest.main()
