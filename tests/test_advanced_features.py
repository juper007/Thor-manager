import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest import mock

from agent.context import compact_messages
from agent.mcp import MCPClient,MCPConnectionManager,MCPError
from agent.notifications import NotificationService
from agent.scheduler import RunScheduler
from agent.verification import VerificationAgent
from agent.worktrees import WorktreeManager
from storage.database import SessionStore
from tools import mcp as mcp_tools


class FakeMCPClient:
    def __init__(self,command,cwd=None,env=None): self.command=command; self.closed=False
    def connect(self): return {'protocolVersion':'2025-03-26'}
    def list_tools(self): return [{'name':'echo','inputSchema':{'type':'object'}}]
    def close(self): self.closed=True
    def is_connected(self): return not self.closed


class AdvancedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=SessionStore(Path(self.temp.name)/'sessions.db')

    def test_context_compaction_preserves_recent_messages(self):
        policy='SYSTEM POLICY '+('never bypass approval '*200)
        messages=[{'role':'system','content':policy},*({'role':'user','content':chr(65+i)*2000} for i in range(12))]
        compacted,info=compact_messages(messages,14_000,3)
        self.assertEqual(compacted[0],messages[0]); self.assertEqual(compacted[-3:],messages[-3:]); self.assertGreater(info['messages_compacted'],0)
        self.assertLess(info['compacted_characters'],info['original_characters'])

    def test_mcp_server_storage_and_connection_lifecycle(self):
        manager=MCPConnectionManager(self.store,FakeMCPClient)
        manager.add('demo',['demo-server']); self.assertFalse(manager.status()[0]['connected'])
        client=manager.connect('demo'); self.assertEqual(client.list_tools()[0]['name'],'echo')
        self.assertTrue(manager.status()[0]['connected']); self.assertTrue(manager.disconnect('demo')); self.assertTrue(client.closed)

    def test_mcp_list_does_not_start_disconnected_servers(self):
        manager=mock.Mock(); manager.status.return_value=[{'name':'demo','connected':False,'enabled':True}]
        mcp_tools.configure(manager)
        self.assertEqual(mcp_tools.mcp_list({})['servers'][0]['tools'],[])
        manager.connect.assert_not_called()

    def test_mcp_timeout_closes_process(self):
        client=MCPClient([sys.executable,'-c','import time; time.sleep(2)'],timeout=.05)
        with self.assertRaises(MCPError): client.connect()
        self.assertFalse(client.is_connected())

    def test_mcp_secrets_require_host_environment_reference(self):
        manager=MCPConnectionManager(self.store,FakeMCPClient)
        with self.assertRaises(ValueError): manager.add('bad',['server'],env={'API_TOKEN':'literal-secret'})
        manager.add('good',['server'],env={'API_TOKEN':{'from_env':'MCP_API_TOKEN'},'LOG_LEVEL':'info'})
        self.assertEqual(manager.servers()[0]['env']['API_TOKEN'],{'from_env':'MCP_API_TOKEN'})

    def test_project_memory_is_scoped_and_redacted(self):
        self.store.remember('project-a','preference','token=secret-value')
        self.store.remember('project-b','preference','different')
        rows=self.store.memories('project-a'); self.assertEqual(len(rows),1)
        self.assertNotIn('secret-value',rows[0]['content']); self.assertTrue(self.store.forget('project-a','preference'))

    def test_scheduler_runs_due_items_and_advances_deadline(self):
        schedule_id=self.store.create_schedule('health','check health',60,next_run_at=10)
        seen=[]; scheduler=RunScheduler(self.store,lambda item:seen.append(item['id']))
        self.assertEqual(scheduler.tick(11),[(schedule_id,'completed')]); self.assertEqual(seen,[schedule_id])
        schedule=self.store.list_schedules()[0]; self.assertEqual(schedule['next_run_at'],71); self.assertEqual(schedule['last_status'],'completed')

    def test_notifications_use_pinned_https_sender(self):
        self.store.add_notification_endpoint('ops','https://example.com/hook')
        service=NotificationService(self.store)
        with mock.patch('agent.notifications.post_json',return_value=204) as send: result=service.send('run.completed',{'run_id':'x'})
        self.assertEqual(result[0]['status'],'sent'); self.assertEqual(send.call_args.args[0],'https://example.com/hook')

    def test_verification_agent_requires_structured_result(self):
        verifier=VerificationAgent(lambda messages:json.dumps({'passed':True,'issues':[],'summary':'ok'}))
        self.assertTrue(verifier.verify('request','answer','evidence')['passed'])
        self.assertFalse(VerificationAgent(lambda messages:'not json').verify('request','answer','evidence')['passed'])

    def test_worktree_names_are_validated_before_git(self):
        repository=Path(self.temp.name)/'repo'; repository.mkdir(); manager=WorktreeManager(repository)
        with self.assertRaises(ValueError): manager.create('../escape')

    def test_usage_summary_aggregates_metrics(self):
        self.store.record_usage('run_seconds',2.5,tags={'state':'completed'},created_at=100)
        summary=self.store.usage_summary(99)
        self.assertEqual(summary['metrics'][0]['samples'],1); self.assertEqual(summary['metrics'][0]['total'],2.5)
