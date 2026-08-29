import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from storage.redaction import redact,redacted_json


TERMINAL_STATES={'completed','failed','cancelled'}


class SessionStore:
    def __init__(self,path,migrations_dir=None):
        self.path=Path(path)
        self.migrations_dir=Path(migrations_dir or Path(__file__).with_name('migrations'))
        self.path.parent.mkdir(parents=True,exist_ok=True)
        self.migrate()

    @contextmanager
    def connect(self):
        connection=sqlite3.connect(self.path,timeout=10)
        connection.row_factory=sqlite3.Row
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA busy_timeout=10000')
        connection.execute('PRAGMA journal_mode=WAL')
        try:
            with connection: yield connection
        finally: connection.close()

    def _migrations(self):
        result=[]
        for path in sorted(self.migrations_dir.glob('[0-9][0-9][0-9]_*.sql')):
            version=int(path.name.split('_',1)[0]); text=path.read_text(encoding='utf-8')
            up,down=text.split('-- migrate:down',1)
            result.append((version,path.name,up.split('-- migrate:up',1)[-1],down))
        return result

    def migrate(self,target=None):
        migrations=self._migrations()
        latest=migrations[-1][0] if migrations else 0
        target=latest if target is None else int(target)
        if target<0 or target>latest: raise ValueError('invalid migration target')
        with self.connect() as db:
            db.execute('CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at REAL NOT NULL)')
            current=db.execute('SELECT COALESCE(MAX(version),0) FROM schema_migrations').fetchone()[0]
            if target>current:
                for version,name,up,_ in migrations:
                    if current<version<=target:
                        quoted_name=db.execute('SELECT quote(?)',(name,)).fetchone()[0]
                        db.executescript(f'BEGIN IMMEDIATE;\n{up}\nINSERT INTO schema_migrations VALUES ({version},{quoted_name},{time.time()});\nCOMMIT;')
            elif target<current:
                for version,_,_,down in reversed(migrations):
                    if target<version<=current:
                        db.executescript(f'BEGIN IMMEDIATE;\n{down}\nDELETE FROM schema_migrations WHERE version={version};\nCOMMIT;')
        return target

    def create_session(self,snapshot,messages,resumed_from=None):
        now=time.time()
        with self.connect() as db:
            db.execute('INSERT INTO sessions(run_id,state,created_at,updated_at,iterations,tool_calls,error,resumed_from) VALUES (?,?,?,?,?,?,?,?)',
                (snapshot['run_id'],snapshot['state'],snapshot['created_at'],snapshot['updated_at'],snapshot['iterations'],snapshot['tool_calls'],snapshot.get('error'),resumed_from))
            for sequence,message in enumerate(messages,1):
                db.execute('INSERT INTO messages(run_id,sequence,role,content,created_at) VALUES (?,?,?,?,?)',
                    (snapshot['run_id'],sequence,message['role'],redact(message['content']),now))
        self.save_snapshot(snapshot)

    def save_snapshot(self,snapshot):
        with self.connect() as db:
            db.execute('UPDATE sessions SET state=?,updated_at=?,iterations=?,tool_calls=?,error=? WHERE run_id=?',
                (snapshot['state'],snapshot['updated_at'],snapshot['iterations'],snapshot['tool_calls'],redact(snapshot.get('error')),snapshot['run_id']))
            for event in snapshot.get('events',[]):
                db.execute('INSERT OR REPLACE INTO run_events VALUES (?,?,?,?,?,?)',
                    (snapshot['run_id'],event['sequence'],event['timestamp'],event['type'],event['state'],redacted_json(event.get('payload',{}))))

    def record_tool_execution(self,run_id,sequence,result):
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO tool_executions(run_id,sequence,name,arguments_json,result_json,status,error,error_code,seconds,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)',(
                run_id,sequence,result.get('name',''),redacted_json(result.get('arguments',{})),redacted_json(result.get('result')),
                result.get('status','unknown'),redact(result.get('error')),result.get('error_code'),result.get('seconds'),time.time()))

    def create_permission_request(self,approval):
        with self.connect() as db:
            db.execute('INSERT INTO permission_requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',(
                approval['approval_id'],approval['run_id'],approval['tool_name'],approval['risk_level'],approval['arguments_hash'],
                redacted_json(approval['arguments']),redact(approval['summary']),approval['status'],approval.get('scope'),approval['created_at'],approval['expires_at'],approval.get('decided_at')))

    def decide_permission(self,approval_id,status,scope,decided_at):
        with self.connect() as db:
            cursor=db.execute("UPDATE permission_requests SET status=?,scope=?,decided_at=? WHERE approval_id=? AND status='pending'",(status,scope,decided_at,approval_id))
            if cursor.rowcount!=1: raise ValueError('approval is no longer pending')

    def get_permission_request(self,approval_id):
        with self.connect() as db:
            row=db.execute('SELECT * FROM permission_requests WHERE approval_id=?',(approval_id,)).fetchone()
        if row is None: return None
        result=dict(row); result['arguments']=json.loads(result.pop('arguments_json')); return result

    def list_permission_requests(self,run_id=None,status=None):
        sql='SELECT * FROM permission_requests'; clauses=[]; params=[]
        if run_id: clauses.append('run_id=?'); params.append(run_id)
        if status: clauses.append('status=?'); params.append(status)
        if clauses: sql+=' WHERE '+' AND '.join(clauses)
        sql+=' ORDER BY created_at DESC LIMIT 100'
        with self.connect() as db: rows=[dict(row) for row in db.execute(sql,params)]
        for row in rows: row['arguments']=json.loads(row.pop('arguments_json'))
        return rows

    def save_permission_grant(self,scope,run_id,tool_name,risk_level):
        stored_run=run_id if scope=='session' else None
        with self.connect() as db:
            db.execute('INSERT OR REPLACE INTO permission_grants(scope,run_id,tool_name,risk_level,created_at,expires_at) VALUES (?,?,?,?,?,?)',(
                scope,stored_run,tool_name,risk_level,time.time(),None))

    def find_permission_grant(self,run_id,tool_name,risk_level):
        now=time.time()
        with self.connect() as db:
            row=db.execute("SELECT * FROM permission_grants WHERE tool_name=? AND risk_level=? AND (scope='always_tool' OR (scope='session' AND run_id=?)) AND (expires_at IS NULL OR expires_at>?) ORDER BY CASE scope WHEN 'session' THEN 0 ELSE 1 END LIMIT 1",
                (tool_name,risk_level,run_id,now)).fetchone()
        return dict(row) if row else None

    def complete_session(self,snapshot,answer):
        now=time.time()
        with self.connect() as db:
            run_id=snapshot['run_id']
            sequence=db.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM messages WHERE run_id=?',(run_id,)).fetchone()[0]
            db.execute('INSERT INTO messages(run_id,sequence,role,content,created_at) VALUES (?,?,?,?,?)',(run_id,sequence,'assistant',redact(answer),now))
            db.execute('UPDATE sessions SET state=?,updated_at=?,iterations=?,tool_calls=?,error=?,final_answer=? WHERE run_id=?',(
                snapshot['state'],snapshot['updated_at'],snapshot['iterations'],snapshot['tool_calls'],redact(snapshot.get('error')),redact(answer),run_id))
            for event in snapshot.get('events',[]):
                db.execute('INSERT OR REPLACE INTO run_events VALUES (?,?,?,?,?,?)',
                    (run_id,event['sequence'],event['timestamp'],event['type'],event['state'],redacted_json(event.get('payload',{}))))

    def get_session(self,run_id):
        with self.connect() as db:
            row=db.execute('SELECT * FROM sessions WHERE run_id=?',(run_id,)).fetchone()
            if row is None: return None
            result=dict(row)
            result['metadata']=json.loads(result.pop('metadata_json'))
            result['messages']=[dict(item) for item in db.execute('SELECT role,content,created_at FROM messages WHERE run_id=? ORDER BY sequence',(run_id,))]
            result['events']=[{**dict(item),'payload':json.loads(item['payload_json'])} for item in db.execute('SELECT sequence,timestamp,type,state,payload_json FROM run_events WHERE run_id=? ORDER BY sequence',(run_id,))]
            for event in result['events']: event.pop('payload_json')
            result['tools']=[{**dict(item),'arguments':json.loads(item['arguments_json']),'result':json.loads(item['result_json']) if item['result_json'] else None} for item in db.execute('SELECT * FROM tool_executions WHERE run_id=? ORDER BY sequence',(run_id,))]
            for item in result['tools']: item.pop('arguments_json'); item.pop('result_json')
            return result

    def list_sessions(self,limit=50,offset=0):
        limit=max(1,min(int(limit),100)); offset=max(0,int(offset))
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT run_id,state,created_at,updated_at,iterations,tool_calls,error,final_answer,resumed_from FROM sessions ORDER BY updated_at DESC LIMIT ? OFFSET ?',(limit,offset))]

    def recover_interrupted(self):
        now=time.time(); message='server restarted before the run reached a terminal state'
        with self.connect() as db:
            rows=[row[0] for row in db.execute("SELECT run_id FROM sessions WHERE state NOT IN ('completed','failed','cancelled')")]
            db.executemany("UPDATE sessions SET state='failed',error=?,updated_at=? WHERE run_id=?",[(message,now,run_id) for run_id in rows])
            db.execute("UPDATE permission_requests SET status='cancelled',decided_at=? WHERE status='pending'",(now,))
        return rows

    def resumable_messages(self,run_id):
        session=self.get_session(run_id)
        if session is None: raise KeyError(run_id)
        if session['state'] not in ('failed','cancelled'): raise ValueError('only failed or cancelled sessions can be resumed')
        messages=[{'role':item['role'],'content':item['content']} for item in session['messages'] if item['role'] in ('user','assistant')]
        if messages and messages[-1]['role']=='assistant': messages.pop()
        return messages

    def cleanup(self,max_age_days=30,keep_recent=100):
        cutoff=time.time()-max(1,int(max_age_days))*86400
        with self.connect() as db:
            protected=[row[0] for row in db.execute('SELECT run_id FROM sessions ORDER BY updated_at DESC LIMIT ?',(max(0,int(keep_recent)),))]
            placeholders=','.join('?' for _ in protected)
            sql="DELETE FROM sessions WHERE state IN ('completed','failed','cancelled') AND updated_at<?"
            params=[cutoff]
            if protected: sql+=f' AND run_id NOT IN ({placeholders})'; params.extend(protected)
            cursor=db.execute(sql,params)
            return cursor.rowcount
