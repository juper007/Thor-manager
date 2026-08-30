import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.context import compact_messages
from agent.mcp import MCPConnectionManager
from agent.notifications import NotificationService
from agent.scheduler import RunScheduler
from agent.verification import VerificationAgent
from agent.worktrees import WorktreeManager
from storage.database import SessionStore


class FakeMCPClient:
    def __init__(self,command,cwd=None,env=None): self.command=command; self.closed=False
    def connect(self): return {'protocolVersion':'2025-03-26'}
    def list_tools(self): return [{'name':'echo','inputSchema':{'type':'object'}}]
    def close(self): self.closed=True


class AdvancedFeatureTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.store=SessionStore(Path(self.temp.name)/'sessions.db')

    def test_context_compaction_preserves_recent_messages(self):
        messages=[{'role':'user','content':chr(65+i)*2000} for i in range(12)]
        compacted,info=compact_messages(messages,14_000,3)
        self.assertEqual(compacted[-3:],messages[-3:]); self.assertGreater(info['messages_compacted'],0)
        self.assertLess(info['compacted_characters'],info['original_characters'])

    def test_mcp_server_storage_and_connection_lifecycle(self):
        manager=MCPConnectionManager(self.store,FakeMCPClient)
        manager.add('demo',['demo-server']); self.assertFalse(manager.status()[0]['connected'])
        client=manager.connect('demo'); self.assertEqual(client.list_tools()[0]['name'],'echo')
        self.assertTrue(manager.status()[0]['connected']); self.assertTrue(manager.disconnect('demo')); self.assertTrue(client.closed)

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
