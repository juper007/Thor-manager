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

    def create_session(self,snapshot,messages,resumed_from=None,conversation_id=None):
        now=time.time()
        with self.connect() as db:
            conversation_id=conversation_id or snapshot['run_id']
            owner_id=snapshot.get('owner_id','thor')
            existing=[dict(row) for row in db.execute('SELECT m.role,m.content FROM messages m JOIN sessions s ON s.run_id=m.run_id WHERE s.conversation_id=? AND s.owner_id=? ORDER BY s.created_at,m.sequence',(conversation_id,owner_id))]
            incoming=[{'role':item['role'],'content':redact(item['content'])} for item in messages]
            if existing and incoming[:len(existing)]!=existing: raise ValueError('conversation history does not match stored messages')
            db.execute('INSERT INTO sessions(run_id,state,created_at,updated_at,iterations,tool_calls,error,resumed_from,owner_id,conversation_id) VALUES (?,?,?,?,?,?,?,?,?,?)',
                (snapshot['run_id'],snapshot['state'],snapshot['created_at'],snapshot['updated_at'],snapshot['iterations'],snapshot['tool_calls'],snapshot.get('error'),resumed_from,owner_id,conversation_id))
            stored_messages=messages[len(existing):]
            for sequence,message in enumerate(stored_messages,1):
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

    def apply_permission_decision(self,approval_id,status,scope,decided_at,run_id,tool_name,risk_level,owner_id='thor'):
        with self.connect() as db:
            cursor=db.execute("UPDATE permission_requests SET status=?,scope=?,decided_at=? WHERE approval_id=? AND status='pending'",(status,scope,decided_at,approval_id))
            if cursor.rowcount!=1: raise ValueError('approval is no longer pending')
            if status=='allowed' and scope in ('session','always_tool'):
                stored_run=run_id if scope=='session' else None
                grant_key=f'{owner_id}:{scope}:{stored_run or "*"}:{tool_name}:{risk_level}'
                db.execute('INSERT INTO permission_grants(grant_key,scope,run_id,tool_name,risk_level,created_at,expires_at,owner_id) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(grant_key) DO UPDATE SET created_at=excluded.created_at,expires_at=excluded.expires_at',(
                    grant_key,scope,stored_run,tool_name,risk_level,time.time(),None,owner_id))

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

    def save_permission_grant(self,scope,run_id,tool_name,risk_level,owner_id='thor'):
        stored_run=run_id if scope=='session' else None
        grant_key=f'{owner_id}:{scope}:{stored_run or "*"}:{tool_name}:{risk_level}'
        with self.connect() as db:
            db.execute('INSERT INTO permission_grants(grant_key,scope,run_id,tool_name,risk_level,created_at,expires_at,owner_id) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(grant_key) DO UPDATE SET created_at=excluded.created_at,expires_at=excluded.expires_at',(
                grant_key,scope,stored_run,tool_name,risk_level,time.time(),None,owner_id))

    def list_permission_grants(self,owner_id=None):
        sql='SELECT * FROM permission_grants'; params=[]
        if owner_id is not None: sql+=' WHERE owner_id=?'; params.append(owner_id)
        with self.connect() as db: return [dict(row) for row in db.execute(sql+' ORDER BY created_at DESC',params)]

    def revoke_permission_grant(self,grant_id,owner_id=None):
        with self.connect() as db:
            sql='DELETE FROM permission_grants WHERE id=?'; params=[int(grant_id)]
            if owner_id is not None: sql+=' AND owner_id=?'; params.append(owner_id)
            cursor=db.execute(sql,params)
            return cursor.rowcount==1

    def find_permission_grant(self,run_id,tool_name,risk_level,owner_id='thor'):
        now=time.time()
        with self.connect() as db:
            row=db.execute("SELECT * FROM permission_grants WHERE owner_id=? AND tool_name=? AND risk_level=? AND (scope='always_tool' OR (scope='session' AND run_id=?)) AND (expires_at IS NULL OR expires_at>?) ORDER BY CASE scope WHEN 'session' THEN 0 ELSE 1 END LIMIT 1",
                (owner_id,tool_name,risk_level,run_id,now)).fetchone()
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

    def get_session(self,run_id,owner_id=None):
        with self.connect() as db:
            sql='SELECT * FROM sessions WHERE run_id=?'; params=[run_id]
            if owner_id is not None: sql+=' AND owner_id=?'; params.append(owner_id)
            row=db.execute(sql,params).fetchone()
            if row is None: return None
            result=dict(row)
            result['metadata']=json.loads(result.pop('metadata_json'))
            result['messages']=[dict(item) for item in db.execute('SELECT role,content,created_at FROM messages WHERE run_id=? ORDER BY sequence',(run_id,))]
            result['events']=[{**dict(item),'payload':json.loads(item['payload_json'])} for item in db.execute('SELECT sequence,timestamp,type,state,payload_json FROM run_events WHERE run_id=? ORDER BY sequence',(run_id,))]
            for event in result['events']: event.pop('payload_json')
            result['tools']=[{**dict(item),'arguments':json.loads(item['arguments_json']),'result':json.loads(item['result_json']) if item['result_json'] else None} for item in db.execute('SELECT * FROM tool_executions WHERE run_id=? ORDER BY sequence',(run_id,))]
            for item in result['tools']: item.pop('arguments_json'); item.pop('result_json')
            return result

    def list_sessions(self,limit=50,offset=0,owner_id=None):
        limit=max(1,min(int(limit),100)); offset=max(0,int(offset))
        where=' WHERE s.owner_id=?' if owner_id is not None else ''
        params=[owner_id,limit,offset] if owner_id is not None else [limit,offset]
        with self.connect() as db:
            return [dict(row) for row in db.execute("""SELECT s.run_id,s.state,s.created_at,s.updated_at,s.iterations,s.tool_calls,s.error,s.final_answer,s.resumed_from,
                COALESCE((SELECT json_extract(e.payload_json,'$.mode') FROM run_events e WHERE e.run_id=s.run_id AND e.type='run.mode' ORDER BY e.sequence LIMIT 1),'agent') AS mode
                FROM sessions s"""+where+' ORDER BY s.updated_at DESC LIMIT ? OFFSET ?',params)]

    def list_conversations(self,limit=50,offset=0,owner_id=None):
        limit=max(1,min(int(limit),100)); offset=max(0,int(offset)); clauses=[]; params=[]
        if owner_id is not None: clauses.append('s.owner_id=?'); params.append(owner_id)
        where=(' WHERE '+' AND '.join(clauses)) if clauses else ''
        sql="""SELECT s.conversation_id AS run_id,s.state,grouped.created_at,s.updated_at,
            grouped.iterations,grouped.tool_calls,s.error,s.final_answer,s.resumed_from,
            COALESCE((SELECT json_extract(e.payload_json,'$.mode') FROM run_events e WHERE e.run_id=s.run_id AND e.type='run.mode' ORDER BY e.sequence LIMIT 1),'agent') AS mode
            FROM sessions s JOIN (SELECT owner_id,conversation_id,MIN(created_at) created_at,SUM(iterations) iterations,SUM(tool_calls) tool_calls FROM sessions GROUP BY owner_id,conversation_id) grouped
            ON grouped.owner_id=s.owner_id AND grouped.conversation_id=s.conversation_id
            AND s.run_id=(SELECT latest.run_id FROM sessions latest WHERE latest.owner_id=s.owner_id AND latest.conversation_id=s.conversation_id ORDER BY latest.updated_at DESC,latest.created_at DESC LIMIT 1)"""+where+' ORDER BY s.updated_at DESC LIMIT ? OFFSET ?'
        params.extend((limit,offset))
        with self.connect() as db: return [dict(row) for row in db.execute(sql,params)]

    def get_conversation(self,conversation_id,owner_id=None):
        sql='SELECT run_id FROM sessions WHERE conversation_id=?'; params=[conversation_id]
        if owner_id is not None: sql+=' AND owner_id=?'; params.append(owner_id)
        sql+=' ORDER BY updated_at DESC LIMIT 1'
        with self.connect() as db: row=db.execute(sql,params).fetchone()
        if row is None: return None
        result=self.get_session(row['run_id'],owner_id)
        result['latest_run_id']=result['run_id']
        with self.connect() as db:
            result['messages']=[dict(item) for item in db.execute('SELECT m.role,m.content,m.created_at FROM messages m JOIN sessions s ON s.run_id=m.run_id WHERE s.conversation_id=? AND s.owner_id=? ORDER BY s.created_at,m.sequence',(conversation_id,result['owner_id']))]
        result['run_id']=conversation_id
        return result

    def conversation_exists(self,conversation_id,owner_id=None):
        sql='SELECT 1 FROM sessions WHERE conversation_id=?'; params=[conversation_id]
        if owner_id is not None: sql+=' AND owner_id=?'; params.append(owner_id)
        with self.connect() as db: return db.execute(sql+' LIMIT 1',params).fetchone() is not None

    def recover_interrupted(self):
        now=time.time(); message='server restarted before the run reached a terminal state'
        with self.connect() as db:
            rows=[row[0] for row in db.execute("SELECT run_id FROM sessions WHERE state NOT IN ('completed','failed','cancelled')")]
            db.executemany("UPDATE sessions SET state='failed',error=?,updated_at=? WHERE run_id=?",[(message,now,run_id) for run_id in rows])
            db.execute("UPDATE permission_requests SET status='cancelled',decided_at=? WHERE status='pending'",(now,))
        return rows

    def resumable_messages(self,run_id,owner_id=None):
        session=self.get_session(run_id,owner_id)
        if session is None: raise KeyError(run_id)
        if session['state'] not in ('failed','cancelled'): raise ValueError('only failed or cancelled sessions can be resumed')
        messages=[{'role':item['role'],'content':item['content']} for item in session['messages'] if item['role'] in ('user','assistant')]
        if messages and messages[-1]['role']=='assistant': messages.pop()
        return messages

    def cleanup(self,max_age_days=30,keep_recent=100):
        cutoff=time.time()-max(1,int(max_age_days))*86400
        with self.connect() as db:
            protected={(row['owner_id'],row['conversation_id']) for row in db.execute('SELECT owner_id,conversation_id FROM sessions GROUP BY owner_id,conversation_id ORDER BY MAX(updated_at) DESC LIMIT ?',(max(0,int(keep_recent)),))}
            candidates=[(row['owner_id'],row['conversation_id']) for row in db.execute("SELECT owner_id,conversation_id FROM sessions GROUP BY owner_id,conversation_id HAVING MAX(updated_at)<? AND SUM(CASE WHEN state NOT IN ('completed','failed','cancelled') THEN 1 ELSE 0 END)=0",(cutoff,))]
            deleted=0
            for owner_id,conversation_id in candidates:
                if (owner_id,conversation_id) in protected: continue
                deleted+=db.execute('DELETE FROM sessions WHERE owner_id=? AND conversation_id=?',(owner_id,conversation_id)).rowcount
            return deleted

    def list_users(self):
        with self.connect() as db:
            return [dict(row) for row in db.execute('SELECT username,is_admin,created_at,updated_at FROM users ORDER BY username')]

    def get_user(self,username):
        with self.connect() as db: row=db.execute('SELECT * FROM users WHERE username=?',(username,)).fetchone()
        return dict(row) if row else None

    def save_user(self,username,password_hash,is_admin=False):
        now=time.time()
        with self.connect() as db:
            cursor=db.execute('INSERT INTO users(username,password_hash,is_admin,created_at,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(username) DO NOTHING',
                (username,password_hash,int(bool(is_admin)),now,now))
            return cursor.rowcount==1

    def update_user_password(self,username,password_hash):
        with self.connect() as db:
            return db.execute('UPDATE users SET password_hash=?,updated_at=? WHERE username=?',(password_hash,time.time(),username)).rowcount==1

    def delete_user(self,username):
        with self.connect() as db: return db.execute('DELETE FROM users WHERE username=?',(username,)).rowcount==1

    def upsert_mcp_server(self,name,command,cwd=None,env=None,enabled=True):
        now=time.time()
        with self.connect() as db:
            db.execute('INSERT INTO mcp_servers(name,command_json,cwd,env_json,enabled,created_at,updated_at) VALUES (?,?,?,?,?,?,?) ON CONFLICT(name) DO UPDATE SET command_json=excluded.command_json,cwd=excluded.cwd,env_json=excluded.env_json,enabled=excluded.enabled,updated_at=excluded.updated_at',
                (name,json.dumps(command,ensure_ascii=False,separators=(',',':')),cwd,json.dumps(env or {},ensure_ascii=False,separators=(',',':')),int(bool(enabled)),now,now))

    def list_mcp_servers(self,enabled=None):
        sql='SELECT * FROM mcp_servers'; params=[]
        if enabled is not None: sql+=' WHERE enabled=?'; params.append(int(bool(enabled)))
        with self.connect() as db: rows=[dict(row) for row in db.execute(sql+' ORDER BY name',params)]
        for row in rows:
            row['command']=json.loads(row.pop('command_json')); row['env']=json.loads(row.pop('env_json')); row['enabled']=bool(row['enabled'])
        return rows

    def delete_mcp_server(self,name):
        with self.connect() as db: return db.execute('DELETE FROM mcp_servers WHERE name=?',(name,)).rowcount==1

    def remember(self,project_key,memory_key,content):
        now=time.time()
        with self.connect() as db:
            db.execute('INSERT INTO project_memories(project_key,memory_key,content,created_at,updated_at) VALUES (?,?,?,?,?) ON CONFLICT(project_key,memory_key) DO UPDATE SET content=excluded.content,updated_at=excluded.updated_at',
                (project_key,memory_key,redact(content),now,now))

    def memories(self,project_key,limit=50):
        with self.connect() as db: return [dict(row) for row in db.execute('SELECT memory_key,content,created_at,updated_at FROM project_memories WHERE project_key=? ORDER BY updated_at DESC LIMIT ?', (project_key,max(1,min(int(limit),200))))]

    def forget(self,project_key,memory_key):
        with self.connect() as db: return db.execute('DELETE FROM project_memories WHERE project_key=? AND memory_key=?',(project_key,memory_key)).rowcount==1

    def create_schedule(self,name,prompt,interval_seconds,next_run_at=None):
        if not isinstance(name,str) or not name.strip() or not isinstance(prompt,str) or not prompt.strip(): raise ValueError('schedule name and prompt are required')
        now=time.time(); interval=max(60,int(interval_seconds))
        with self.connect() as db:
            cursor=db.execute('INSERT INTO scheduled_runs(name,prompt,interval_seconds,next_run_at,enabled,created_at,updated_at) VALUES (?,?,?,?,1,?,?)',
                (name,redact(prompt),interval,float(next_run_at or now+interval),now,now))
            return cursor.lastrowid

    def list_schedules(self):
        with self.connect() as db: return [dict(row) for row in db.execute('SELECT * FROM scheduled_runs ORDER BY next_run_at,id')]

    def due_schedules(self,now=None):
        with self.connect() as db: return [dict(row) for row in db.execute('SELECT * FROM scheduled_runs WHERE enabled=1 AND next_run_at<=? ORDER BY next_run_at,id',(float(now or time.time()),))]

    def finish_schedule(self,schedule_id,status,finished_at=None):
        now=float(finished_at or time.time())
        with self.connect() as db:
            db.execute('UPDATE scheduled_runs SET last_run_at=?,last_status=?,next_run_at=?+interval_seconds,updated_at=? WHERE id=?',(now,status,now,now,int(schedule_id)))

    def set_schedule_enabled(self,schedule_id,enabled):
        with self.connect() as db: return db.execute('UPDATE scheduled_runs SET enabled=?,updated_at=? WHERE id=?',(int(bool(enabled)),time.time(),int(schedule_id))).rowcount==1

    def add_notification_endpoint(self,name,url):
        now=time.time()
        with self.connect() as db:
            db.execute('INSERT INTO notification_endpoints(name,url,enabled,created_at,updated_at) VALUES (?,?,1,?,?) ON CONFLICT(name) DO UPDATE SET url=excluded.url,enabled=1,updated_at=excluded.updated_at',(name,url,now,now))

    def delete_notification_endpoint(self,endpoint_id):
        with self.connect() as db: return db.execute('DELETE FROM notification_endpoints WHERE id=?',(int(endpoint_id),)).rowcount==1

    def notification_endpoints(self):
        with self.connect() as db: return [dict(row) for row in db.execute('SELECT * FROM notification_endpoints WHERE enabled=1 ORDER BY name')]

    def record_usage(self,metric,value,run_id=None,tags=None,created_at=None):
        with self.connect() as db: db.execute('INSERT INTO usage_samples(run_id,metric,value,tags_json,created_at) VALUES (?,?,?,?,?)',(run_id,metric,float(value),redacted_json(tags or {}),float(created_at or time.time())))

    def usage_summary(self,since=None):
        since=float(since or time.time()-86400)
        with self.connect() as db:
            metrics=[dict(row) for row in db.execute('SELECT metric,COUNT(*) samples,SUM(value) total,AVG(value) average,MIN(value) minimum,MAX(value) maximum FROM usage_samples WHERE created_at>=? GROUP BY metric ORDER BY metric',(since,))]
            runs=dict(db.execute("SELECT COUNT(*) total,SUM(state='completed') completed,SUM(state='failed') failed,SUM(state='cancelled') cancelled,COALESCE(AVG(tool_calls),0) average_tool_calls FROM sessions WHERE created_at>=?",(since,)).fetchone())
        return {'since':since,'runs':runs,'metrics':metrics}
