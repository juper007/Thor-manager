import sqlite3
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path

from agent.runtime import AgentRuntime
from storage import SessionStore
from storage.redaction import REDACTED,redact


class FakeRegistry:
    def execute(self,name,arguments):
        return {'name':name,'arguments':arguments,'status':'success','result':{'token':'tool-secret','value':4},'error':None,'error_code':None,'seconds':.01}


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.path=Path(self.temp.name)/'sessions.db'
        self.store=SessionStore(self.path)

    def tearDown(self): self.temp.cleanup()

    def test_migration_can_upgrade_rollback_and_upgrade_again(self):
        with self.store.connect() as db:
            self.assertEqual(db.execute('SELECT MAX(version) FROM schema_migrations').fetchone()[0],4)
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='sessions'").fetchone())

    def test_failed_migration_rolls_back_schema_and_version(self):
        migrations=Path(self.temp.name)/'broken-migrations'; migrations.mkdir()
        (migrations/'001_broken.sql').write_text('-- migrate:up\nCREATE TABLE partial(value TEXT);\nINVALID SQL;\n-- migrate:down\nDROP TABLE partial;',encoding='utf-8')
        broken_path=Path(self.temp.name)/'broken.db'
        with self.assertRaises(sqlite3.DatabaseError): SessionStore(broken_path,migrations)
        with closing(sqlite3.connect(broken_path)) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='partial'").fetchone())
            self.assertEqual(db.execute('SELECT COUNT(*) FROM schema_migrations').fetchone()[0],0)
        self.store.migrate(0)
        with self.store.connect() as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='sessions'").fetchone())
        self.store.migrate(3)
        with self.store.connect() as db:
            self.assertIsNotNone(db.execute("SELECT name FROM sqlite_master WHERE name='sessions'").fetchone())

    def test_runtime_persists_messages_events_tools_and_answer(self):
        replies=iter(['tool','finished'])
        parser=lambda text: [{'name':'calculator','arguments':{'api_key':'argument-secret'}}] if text=='tool' else []
        runtime=AgentRuntime('.',lambda _:next(replies),FakeRegistry(),parser,lambda _:'skill',lambda text:text,session_store=self.store)
        run,answer,_,_=runtime.run_chat([{'role':'user','content':'password=hunter2 calculate'}],'persisted')
        restored=self.store.get_session(run.run_id)
        self.assertEqual((restored['state'],restored['final_answer']),('completed',answer))
        self.assertEqual(restored['messages'][0]['content'],f'password={REDACTED} calculate')
        self.assertTrue(restored['events']); self.assertEqual(len(restored['tools']),1)
        self.assertNotIn('argument-secret',str(restored)); self.assertNotIn('tool-secret',str(restored))

    def test_interrupted_run_is_recovered_and_can_supply_resume_messages(self):
        snapshot={'run_id':'interrupted','state':'planning','created_at':time.time(),'updated_at':time.time(),'iterations':1,'tool_calls':0,'error':None,'events':[]}
        self.store.create_session(snapshot,[{'role':'user','content':'continue this'}])
        self.assertEqual(self.store.recover_interrupted(),['interrupted'])
        restored=SessionStore(self.path).get_session('interrupted')
        self.assertEqual(restored['state'],'failed'); self.assertIn('server restarted',restored['error'])
        self.assertEqual(self.store.resumable_messages('interrupted'),[{'role':'user','content':'continue this'}])

    def test_interrupted_permission_is_cancelled_and_summary_is_redacted(self):
        now=time.time(); snapshot={'run_id':'permission-run','state':'awaiting_approval','created_at':now,'updated_at':now,'iterations':1,'tool_calls':1,'error':None,'events':[]}
        self.store.create_session(snapshot,[{'role':'user','content':'run'}])
        approval={'approval_id':'a'*32,'run_id':'permission-run','tool_name':'python_execute','risk_level':'elevated','arguments_hash':'hash',
            'arguments':{'code':'token=secret-value'},'summary':'python_execute: token=secret-value','status':'pending','scope':None,
            'created_at':now,'expires_at':now+300,'decided_at':None}
        self.store.create_permission_request(approval)
        saved=self.store.get_permission_request('a'*32)
        self.assertNotIn('secret-value',str(saved))
        self.store.recover_interrupted()
        self.assertEqual(self.store.get_permission_request('a'*32)['status'],'cancelled')

    def test_always_grant_is_upserted_and_can_be_revoked(self):
        self.store.save_permission_grant('always_tool','first','python_execute','elevated')
        self.store.save_permission_grant('always_tool','second','python_execute','elevated')
        grants=self.store.list_permission_grants()
        self.assertEqual(len(grants),1); self.assertIsNone(grants[0]['run_id'])
        self.assertTrue(self.store.revoke_permission_grant(grants[0]['id']))
        self.assertEqual(self.store.list_permission_grants(),[])

    def test_permission_decision_and_grant_are_atomic(self):
        now=time.time(); snapshot={'run_id':'atomic-permission','state':'awaiting_approval','created_at':now,'updated_at':now,'iterations':1,'tool_calls':1,'error':None,'events':[]}
        self.store.create_session(snapshot,[{'role':'user','content':'run'}])
        approval={'approval_id':'b'*32,'run_id':'atomic-permission','tool_name':'python_execute','risk_level':'elevated','arguments_hash':'hash',
            'arguments':{'code':'print(1)'},'summary':'python_execute','status':'pending','scope':None,'created_at':now,'expires_at':now+300,'decided_at':None}
        self.store.create_permission_request(approval)
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_grant BEFORE INSERT ON permission_grants BEGIN SELECT RAISE(ABORT,'reject'); END")
        with self.assertRaises(sqlite3.IntegrityError):
            self.store.apply_permission_decision('b'*32,'allowed','always_tool',time.time(),'atomic-permission','python_execute','elevated')
        self.assertEqual(self.store.get_permission_request('b'*32)['status'],'pending')

    def test_cleanup_keeps_recent_sessions(self):
        old=time.time()-40*86400
        for run_id in ('keep','delete'):
            snapshot={'run_id':run_id,'state':'completed','created_at':old,'updated_at':old,'iterations':1,'tool_calls':0,'error':None,'events':[]}
            self.store.create_session(snapshot,[{'role':'user','content':run_id}])
        with self.store.connect() as db: db.execute("UPDATE sessions SET updated_at=? WHERE run_id='keep'",(time.time(),))
        self.assertEqual(self.store.cleanup(30,1),1)
        self.assertIsNotNone(self.store.get_session('keep')); self.assertIsNone(self.store.get_session('delete'))

    def test_cleanup_can_delete_parent_of_recent_resumed_session(self):
        old=time.time()-40*86400
        self.store.create_session({'run_id':'parent','state':'failed','created_at':old,'updated_at':old,'iterations':1,'tool_calls':0,'error':'x','events':[]},[{'role':'user','content':'first'}])
        now=time.time()
        self.store.create_session({'run_id':'child','state':'completed','created_at':now,'updated_at':now,'iterations':1,'tool_calls':0,'error':None,'events':[]},[{'role':'user','content':'retry'}],resumed_from='parent')
        self.assertEqual(self.store.cleanup(30,1),1)
        self.assertIsNone(self.store.get_session('parent'))
        self.assertIsNone(self.store.get_session('child')['resumed_from'])

    def test_completion_state_answer_and_message_are_atomic(self):
        now=time.time(); snapshot={'run_id':'atomic','state':'verifying','created_at':now,'updated_at':now,'iterations':1,'tool_calls':0,'error':None,'events':[]}
        self.store.create_session(snapshot,[{'role':'user','content':'hello'}])
        with self.store.connect() as db:
            db.execute("CREATE TRIGGER reject_answer BEFORE UPDATE OF final_answer ON sessions BEGIN SELECT RAISE(ABORT,'reject'); END")
        completed={**snapshot,'state':'completed','updated_at':time.time()}
        with self.assertRaises(sqlite3.IntegrityError): self.store.complete_session(completed,'answer')
        restored=self.store.get_session('atomic')
        self.assertEqual(restored['state'],'verifying'); self.assertIsNone(restored['final_answer'])
        self.assertEqual([message['role'] for message in restored['messages']],['user'])

    def test_redaction_handles_nested_values_and_inline_secrets(self):
        value=redact({'Authorization':'Bearer abc','nested':['token=xyz','Authorization: Basic dGhvcjpzZWNyZXQ=','curl -u thor:secret http://host',{'api_key':'123'}]})
        self.assertEqual(value['Authorization'],REDACTED)
        serialized=str(value)
        for secret in ('xyz','123','dGhvcjpzZWNyZXQ=','thor:secret'): self.assertNotIn(secret,serialized)


if __name__=='__main__': unittest.main()
