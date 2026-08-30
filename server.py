#!/usr/bin/env python3
import json, logging, os, re, subprocess, threading, time, urllib.request, urllib.error, urllib.parse, uuid
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from agent import models
from agent.auth import Authenticator
from agent.runtime import AgentRuntime,RunCancelled,RunLimitError,ServiceBusy,validate_messages,validate_run_id,validate_run_mode
from agent.permissions import PermissionEngine
from agent.mcp import MCPConnectionManager
from agent.notifications import NotificationService
from agent.scheduler import RunScheduler
from agent.verification import VerificationAgent
from agent.worktrees import WorktreeManager
from tools import mcp as mcp_tools
from monitoring import collector,cpu_percent,disk_net,gpu_utilization,history,lock,parse,read_text,state
from storage import SessionStore
import agent_tools

ROOT = Path(__file__).resolve().parent


def positive_int_env(name,default):
    try: return max(1,int(os.environ.get(name,str(default))))
    except ValueError: return default


IMAGE_API = 'http://127.0.0.1:8188'
GENERATED_DIR = ROOT / 'generated'
GENERATED_DIR.mkdir(exist_ok=True)
image_history_lock = threading.Lock()
AI_CONCURRENCY = positive_int_env('THOR_AI_CONCURRENCY',1)
SESSION_DB = Path(os.environ.get('THOR_SESSION_DB',str(ROOT/'data'/'sessions.db')))
session_store = SessionStore(SESSION_DB)
session_store.recover_interrupted()
session_store.cleanup(positive_int_env('THOR_SESSION_MAX_AGE_DAYS',30),positive_int_env('THOR_SESSION_KEEP_RECENT',100))
permission_engine=PermissionEngine(session_store,positive_int_env('THOR_APPROVAL_TTL_SECONDS',300))
mcp_manager=MCPConnectionManager(session_store)
mcp_tools.configure(mcp_manager)
notification_service=NotificationService(session_store)
authenticator=Authenticator(lambda: session_store)


class LoginRateLimiter:
    def __init__(self,max_failures=5,window_seconds=60):
        self.max_failures=max(1,int(max_failures)); self.window_seconds=max(1,int(window_seconds)); self._failures={}; self._lock=threading.Lock()
    def retry_after(self,address,now=None):
        now=time.time() if now is None else float(now)
        with self._lock:
            attempts=[value for value in self._failures.get(address,[]) if value>now-self.window_seconds]
            if attempts: self._failures[address]=attempts
            else: self._failures.pop(address,None)
            return max(1,int(attempts[0]+self.window_seconds-now)+1) if len(attempts)>=self.max_failures else 0
    def fail(self,address,now=None):
        now=time.time() if now is None else float(now)
        with self._lock:
            attempts=[value for value in self._failures.get(address,[]) if value>now-self.window_seconds]
            attempts.append(now); self._failures[address]=attempts
            return max(1,int(attempts[0]+self.window_seconds-now)+1) if len(attempts)>=self.max_failures else 0
login_limiter=LoginRateLimiter(positive_int_env('THOR_AUTH_MAX_FAILURES',5),positive_int_env('THOR_AUTH_WINDOW_SECONDS',60))

def image_api_key():
    key = os.environ.get('THOR_IMAGE_API_KEY', '')
    if key: return key
    try:
        for line in Path('/home/juper007/qwen-image/qwen-image.env').read_text().splitlines():
            if line.startswith('IMAGE_API_KEY='): return line.split('=', 1)[1].strip()
    except OSError: pass
    return ''

def image_history():
    try: rows = [json.loads(line) for line in (GENERATED_DIR/'history.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError): rows = []
    return list(reversed(rows[-100:]))

def edge_chat(messages, max_tokens=4096, on_delta=None):
    return models.edge_chat(messages,max_tokens=max_tokens,on_delta=on_delta)

runtime=AgentRuntime(
    ROOT,
    lambda messages: edge_chat(messages),
    agent_tools.DEFAULT_REGISTRY,
    agent_tools.parse_tool_calls,
    agent_tools.load_skill_instructions,
    lambda text: agent_tools.TOOL_CALL_RE.sub('',text).strip(),
    AI_CONCURRENCY,
    max_iterations=positive_int_env('THOR_AGENT_MAX_ITERATIONS',8),
    max_tool_calls=positive_int_env('THOR_AGENT_MAX_TOOL_CALLS',16),
    session_store=session_store,
    permission_engine=permission_engine,
    stream_model_call=lambda messages,callback: edge_chat(messages,on_delta=callback),
    context_limit=positive_int_env('THOR_CONTEXT_CHARACTER_LIMIT',120000),
    verification_agent=VerificationAgent(lambda messages: edge_chat(messages,max_tokens=1024)) if os.environ.get('THOR_VERIFY_AGENT','0')=='1' else None,
)

def run_schedule(schedule):
    run_id='scheduled-'+str(schedule['id'])+'-'+uuid.uuid4().hex[:12]
    try:
        run,answer,_,_=runtime.run_chat([{'role':'user','content':schedule['prompt']}],run_id=run_id,mode='agent')
        notification_service.send('schedule.completed',{'schedule_id':schedule['id'],'run_id':run.run_id,'answer':answer[:2000]})
    except Exception as exc:
        notification_service.send('schedule.failed',{'schedule_id':schedule['id'],'run_id':run_id,'error':str(exc)[:1000]})
        raise

scheduler=RunScheduler(session_store,run_schedule,positive_int_env('THOR_SCHEDULER_POLL_SECONDS',5))
if os.environ.get('THOR_SCHEDULER_ENABLED','0')=='1': scheduler.start()


def agent_chat(messages): return runtime.chat(messages)

def agent_run_chat(messages,run_id=None,mode='agent',owner_id='thor'): return runtime.run_chat(messages,run_id,mode=mode,owner_id=owner_id)

def persisted_run_mode(session):
    for event in session.get('events',[]):
        if event.get('type')=='run.mode': return validate_run_mode(event.get('payload',{}).get('mode'))
    return 'agent'

def json_response(handler,status,value):
    body=json.dumps(value,ensure_ascii=False).encode()
    handler.send_response(status); handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Content-Length',str(len(body))); handler.end_headers(); handler.wfile.write(body)

def public_tool_events(events):
    return [{key:event.get(key) for key in ('name','arguments','seconds','error')} for event in events]

class Handler(SimpleHTTPRequestHandler):
    def send_json(self,status,value,**headers):
        body=json.dumps(value,ensure_ascii=False).encode()
        self.send_response(status); self.send_header('Content-Type','application/json; charset=utf-8')
        self.send_header('Content-Length',str(len(body)))
        for name,value in headers.items(): self.send_header(name.replace('_','-'),str(value))
        self.end_headers(); self.wfile.write(body)

    def send_chat_error(self,status,error,**headers):
        self.send_json(status,{'error':str(error),'done':True},**headers)

    def end_headers(self):
        self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma','no-cache')
        self.send_header('Expires','0')
        super().end_headers()
    def authenticated(self):
        try: user=authenticator.identify(self.headers.get('Authorization',''),self.headers.get('Cookie',''))
        except ValueError as exc: self.send_error(503,str(exc)); return False
        if user: self.user_id=user; return True
        if not authenticator.configured(): self.send_error(503,'authentication is not configured'); return False
        if self.path.startswith('/api/'):
            self.send_json(401,{'error':'authentication required'},WWW_Authenticate='Basic realm="Jetson Thor Monitor"'); return False
        self.send_response(303); self.send_header('Location','/login'); self.end_headers(); return False
    def do_GET(self):
        route = self.path.split('?',1)[0]
        if route == '/login':
            body=(ROOT/'login.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if not self.authenticated(): return
        if route == '/api/auth/me': json_response(self,200,{'username':self.user_id,'is_admin':authenticator.is_admin(self.user_id)}); return
        if route == '/api/admin/users':
            if not authenticator.is_admin(self.user_id): self.send_error(403); return
            json_response(self,200,{'users':authenticator.list_accounts()}); return
        if route == '/api/stats':
            with lock: body = json.dumps({**state, "history": list(history)}).encode()
            self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Cache-Control','no-store'); self.end_headers(); self.wfile.write(body); return
        if route == '/api/health':
            loaded = []
            try:
                req=urllib.request.Request(IMAGE_API+'/health'); image=json.loads(urllib.request.urlopen(req,timeout=2).read()); loaded=[image.get('loaded_pipeline')] if image.get('loaded_pipeline') else []
            except Exception: pass
            body=json.dumps({"status":"ok","monitor":True,"ai":{"cuda":True,"loaded":["engine-64k",*loaded],"state":{"generating":False,"completed":len(image_history())},"available":True}}).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body); return
        if route == '/api/images/history':
            body=json.dumps(image_history(),ensure_ascii=False).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body); return
        if route.startswith('/api/images/history/'):
            name=Path(urllib.parse.unquote(route.rsplit('/',1)[-1])).name; path=GENERATED_DIR/name
            if name.endswith('.png') and path.is_file():
                body=path.read_bytes(); self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
            self.send_error(404); return
        if route == '/api/chat/models':
            body=b'[{"name":"engine-64k","size":0,"context_length":64000}]'; self.send_response(200); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body); return
        if route == '/api/advanced/mcp': json_response(self,200,{'servers':mcp_manager.status()}); return
        if route == '/api/advanced/memories':
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); project=query.get('project',[str(ROOT)])[0]
            json_response(self,200,{'project':project,'memories':session_store.memories(project)}); return
        if route == '/api/advanced/schedules': json_response(self,200,{'schedules':session_store.list_schedules()}); return
        if route == '/api/advanced/notifications': json_response(self,200,{'endpoints':session_store.notification_endpoints()}); return
        if route == '/api/advanced/usage':
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try: since=float(query.get('since',[time.time()-86400])[0])
            except ValueError: self.send_error(400); return
            json_response(self,200,session_store.usage_summary(since)); return
        if route == '/api/advanced/worktrees':
            try: json_response(self,200,{'worktrees':WorktreeManager(ROOT).list()})
            except Exception as exc: json_response(self,409,{'error':str(exc)})
            return
        if route == '/api/chat/approvals':
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            run_id=query.get('run_id',[None])[0]; status=query.get('status',[None])[0]
            if run_id is not None:
                try: run_id=validate_run_id(run_id)
                except ValueError: self.send_error(400); return
            if status not in (None,'pending','allowed','denied','expired','cancelled'): self.send_error(400); return
            if run_id is not None and session_store.get_session(run_id,self.user_id) is None: self.send_error(404); return
            approvals=permission_engine.list(run_id,status)
            approvals=[item for item in approvals if session_store.get_session(item['run_id'],self.user_id) is not None]
            json_response(self,200,{'approvals':approvals}); return
        if route == '/api/chat/permission-grants':
            json_response(self,200,{'grants':permission_engine.grants(self.user_id)}); return
        if route == '/api/chat/sessions':
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try: rows=session_store.list_sessions(query.get('limit',['50'])[0],query.get('offset',['0'])[0],self.user_id)
            except ValueError: self.send_error(400); return
            json_response(self,200,{'sessions':rows}); return
        if route.startswith('/api/chat/sessions/'):
            run_id=urllib.parse.unquote(route.rsplit('/',1)[-1])
            try: run_id=validate_run_id(run_id)
            except ValueError: self.send_error(400); return
            session=session_store.get_session(run_id,self.user_id)
            if session is None: self.send_error(404); return
            json_response(self,200,session); return
        if route.startswith('/api/chat/runs/'):
            run_id=urllib.parse.unquote(route.rsplit('/',1)[-1])
            try: run_id=validate_run_id(run_id)
            except ValueError: self.send_error(400); return
            snapshot=runtime.run_snapshot(run_id)
            if snapshot is not None and snapshot.get('owner_id','thor')!=self.user_id: snapshot=None
            if snapshot is None:
                stored=session_store.get_session(run_id,self.user_id)
                if stored is not None: snapshot={key:stored[key] for key in ('run_id','state','created_at','updated_at','iterations','tool_calls','error')}; snapshot['events']=stored['events']
            if snapshot is None: self.send_error(404); return
            body=json.dumps(snapshot,ensure_ascii=False).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if route == '/ai':
            html=(ROOT/'ai-workspace.html').read_text(encoding='utf-8')
            body=html.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if route == '/admin':
            if not authenticator.is_admin(self.user_id): self.send_error(403); return
            body=(ROOT/'admin.html').read_bytes(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif route == '/': self.path = '/index.html'
        return super().do_GET()
    def do_POST(self):
        route=self.path.split('?',1)[0]
        if route == '/api/auth/login':
            try:
                address=self.client_address[0]; retry_after=login_limiter.retry_after(address)
                if retry_after: self.send_json(429,{'error':'too many login attempts'},Retry_After=retry_after); return
                incoming=self.read_json_body(4096); username=incoming.get('username',''); password=incoming.get('password','')
                if not authenticator.verify(username,password):
                    retry_after=login_limiter.fail(address); logging.warning('authentication failed from %s',address)
                    if retry_after: self.send_json(429,{'error':'too many login attempts'},Retry_After=retry_after)
                    else: self.send_json(401,{'error':'invalid username or password'})
                    return
                token=authenticator.issue(username); self.send_json(200,{'username':username},Set_Cookie=authenticator.cookie(token))
            except ValueError as exc: self.send_json(503,{'error':str(exc)})
            return
        if route == '/api/auth/logout':
            self.send_json(200,{'logged_out':True},Set_Cookie=authenticator.clear_cookie()); return
        if not self.authenticated(): return
        if route == '/api/admin/users':
            if not authenticator.is_admin(self.user_id): self.send_error(403); return
            try:
                incoming=self.read_json_body(4096); username=incoming.get('username','')
                authenticator.create_user(username,incoming.get('password',''),incoming.get('is_admin',False))
                self.send_json(201,{'created':True,'username':username})
            except ValueError as exc: self.send_json(400,{'error':str(exc)})
            return
        if route.startswith('/api/admin/users/') and route.endswith('/password'):
            if not authenticator.is_admin(self.user_id): self.send_error(403); return
            username=urllib.parse.unquote(route.split('/')[-2])
            try:
                incoming=self.read_json_body(4096); authenticator.change_password(username,incoming.get('password',''))
                self.send_json(200,{'updated':True,'username':username})
            except ValueError as exc: self.send_json(400,{'error':str(exc)})
            except KeyError: self.send_error(404)
            return
        if route == '/api/advanced/mcp':
            try:
                incoming=self.read_json_body(64_000); name=incoming.get('name','')
                if not re.fullmatch(r'[A-Za-z0-9._-]{1,80}',name): raise ValueError('invalid MCP server name')
                mcp_manager.add(name,incoming.get('command'),incoming.get('cwd'),incoming.get('env'),incoming.get('enabled',True)); json_response(self,201,{'created':True,'name':name})
            except ValueError as exc: json_response(self,400,{'error':str(exc)})
            return
        if route.startswith('/api/advanced/mcp/'):
            parts=route.split('/'); name=urllib.parse.unquote(parts[-2]); action=parts[-1]
            try:
                if action=='connect': client=mcp_manager.connect(name); json_response(self,200,{'connected':True,'name':name,'tools':client.list_tools()})
                elif action=='disconnect': json_response(self,200,{'disconnected':mcp_manager.disconnect(name),'name':name})
                else: self.send_error(404)
            except KeyError: self.send_error(404)
            except Exception as exc: json_response(self,409,{'error':str(exc)})
            return
        if route == '/api/advanced/memories':
            try:
                incoming=self.read_json_body(64_000); project=incoming.get('project',str(ROOT)); key=incoming.get('key',''); content=incoming.get('content','')
                if not isinstance(project,str) or not isinstance(key,str) or not key or not isinstance(content,str) or not content: raise ValueError('project, key, and content are required')
                session_store.remember(project,key,content); json_response(self,201,{'saved':True,'project':project,'key':key})
            except ValueError as exc: json_response(self,400,{'error':str(exc)})
            return
        if route == '/api/advanced/schedules':
            try:
                incoming=self.read_json_body(64_000); schedule_id=session_store.create_schedule(incoming.get('name',''),incoming.get('prompt',''),incoming.get('interval_seconds'))
                json_response(self,201,{'created':True,'schedule_id':schedule_id})
            except (TypeError,ValueError) as exc: json_response(self,400,{'error':str(exc)})
            return
        if route.startswith('/api/advanced/schedules/'):
            try:
                schedule_id=int(route.rsplit('/',1)[-1]); incoming=self.read_json_body(4096)
                if not session_store.set_schedule_enabled(schedule_id,incoming.get('enabled')): self.send_error(404)
                else: json_response(self,200,{'updated':True,'schedule_id':schedule_id})
            except ValueError as exc: json_response(self,400,{'error':str(exc)})
            return
        if route == '/api/advanced/notifications':
            try:
                incoming=self.read_json_body(16_000); name=incoming.get('name',''); url=incoming.get('url','')
                if not name or not isinstance(url,str) or not url.lower().startswith('https://'): raise ValueError('name and an HTTPS URL are required')
                session_store.add_notification_endpoint(name,url); json_response(self,201,{'created':True,'name':name})
            except ValueError as exc: json_response(self,400,{'error':str(exc)})
            return
        if route == '/api/advanced/worktrees':
            try:
                incoming=self.read_json_body(4096); result=WorktreeManager(ROOT).create(incoming.get('name',''),incoming.get('base','HEAD')); json_response(self,201,result)
            except (ValueError,FileExistsError,subprocess.SubprocessError) as exc: json_response(self,409,{'error':str(exc)})
            return
        if route in ('/api/images/generations','/api/images/edits'):
            return self.proxy_image(route)
        if route == '/api/chat/cancel':
            try:
                incoming=self.read_json_body(4096)
                if 'run_id' not in incoming: raise ValueError('run_id is required')
                run_id=validate_run_id(incoming['run_id'])
                existing=runtime.run_snapshot(run_id)
                if existing is None: existing=session_store.get_session(run_id,self.user_id)
                if existing is None or existing.get('owner_id','thor')!=self.user_id: self.send_error(404); return
                snapshot=runtime.cancel(run_id)
                if snapshot is None: self.send_error(404); return
                self.send_json(200,snapshot)
            except ValueError as e:
                self.send_json(400,{'error':str(e)})
            return
        if route.startswith('/api/chat/approvals/'):
            approval_id=urllib.parse.unquote(route.rsplit('/',1)[-1])
            if not re.fullmatch(r'[0-9a-f]{32}',approval_id): self.send_error(400); return
            try:
                request=session_store.get_permission_request(approval_id)
                if request is None or session_store.get_session(request['run_id'],self.user_id) is None: self.send_error(404); return
                incoming=self.read_json_body(4096)
                approval=permission_engine.decide(approval_id,incoming.get('decision'),incoming.get('scope','once'))
                json_response(self,200,approval)
            except KeyError: self.send_error(404)
            except ValueError as e: json_response(self,409,{'error':str(e)})
            return
        if route.startswith('/api/chat/sessions/') and route.endswith('/resume'):
            run_id=urllib.parse.unquote(route.split('/')[-2])
            try:
                run_id=validate_run_id(run_id); incoming=self.read_json_body(4096)
                new_run_id=validate_run_id(incoming.get('run_id'))
                session=session_store.get_session(run_id,self.user_id)
                if session is None: raise KeyError(run_id)
                mode=persisted_run_mode(session)
                messages=validate_messages(session_store.resumable_messages(run_id,self.user_id))
                run,content,events,sources=runtime.run_chat(messages,new_run_id,resumed_from=run_id,mode=mode,owner_id=self.user_id)
                public_events=public_tool_events(events)
                json_response(self,200,{'run_id':run.run_id,'resumed_from':run_id,'run_state':run.state.value,'mode':mode,'message':{'role':'assistant','content':content},'tools_used':public_events,'sources':sources,'done':True})
            except KeyError: self.send_error(404)
            except ValueError as e: json_response(self,400,{'error':str(e),'done':True})
            except ServiceBusy as e: json_response(self,429,{'error':str(e),'done':True})
            except RunCancelled as e: json_response(self,409,{'error':str(e),'done':True})
            except RunLimitError as e: json_response(self,408,{'error':str(e),'done':True})
            except Exception as e: json_response(self,502,{'error':str(e),'done':True})
            return
        if route != '/api/chat': self.send_error(404); return
        try:
            incoming=self.read_json_body(2_000_000)
            requested_run_id=validate_run_id(incoming.get('run_id'))
            messages=validate_messages(incoming.get('messages'))
            mode=validate_run_mode(incoming.get('mode'))
            if incoming.get('stream') is True:
                self.stream_chat(messages,requested_run_id,mode); return
            run,content,events,sources=agent_run_chat(messages,requested_run_id,mode,self.user_id)
            public_events=public_tool_events(events)
            body=(json.dumps({'run_id':run.run_id,'run_state':run.state.value,'mode':mode,'message':{'role':'assistant','content':content},'tools_used':public_events,'sources':sources,'done':True},ensure_ascii=False)+'\n').encode()
            self.send_response(200); self.send_header('Content-Type','application/x-ndjson; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        except ValueError as e:
            self.send_chat_error(400,e)
        except ServiceBusy as e:
            self.send_chat_error(429,e,Retry_After=5)
        except RunCancelled as e:
            self.send_chat_error(409,e)
        except RunLimitError as e:
            self.send_chat_error(408,e)
        except urllib.error.HTTPError as e:
            self.send_chat_error(502,e.read().decode(errors='replace')[:1000])
        except Exception as e:
            self.send_chat_error(502,e)
    def stream_chat(self,messages,run_id,mode='agent'):
        self.send_response(200); self.send_header('Content-Type','application/x-ndjson; charset=utf-8')
        self.send_header('X-Accel-Buffering','no'); self.send_header('Connection','close'); self.end_headers()
        def send(value):
            self.wfile.write((json.dumps(value,ensure_ascii=False)+'\n').encode()); self.wfile.flush()
        try:
            send({'type':'start','run_id':run_id,'mode':mode})
            run,content,events,sources=runtime.run_chat(messages,run_id,on_delta=lambda delta:send({'type':'delta','run_id':run_id,'message':{'role':'assistant','content':delta}}),mode=mode,owner_id=self.user_id)
            public_events=public_tool_events(events)
            send({'type':'final','run_id':run.run_id,'run_state':run.state.value,'mode':mode,'message':{'role':'assistant','content':''},'final_content':content,'tools_used':public_events,'sources':sources,'done':True})
        except Exception as exc:
            try: send({'type':'error','run_id':run_id,'error':str(exc),'done':True})
            except (BrokenPipeError,ConnectionResetError): pass
    def do_DELETE(self):
        if not self.authenticated(): return
        route=self.path.split('?',1)[0]
        if route.startswith('/api/admin/users/'):
            if not authenticator.is_admin(self.user_id): self.send_error(403); return
            username=urllib.parse.unquote(route.rsplit('/',1)[-1])
            if username==self.user_id: self.send_json(409,{'error':'cannot delete the current user'}); return
            try: authenticator.delete_user(username); self.send_json(200,{'deleted':True,'username':username})
            except ValueError as exc: self.send_json(409,{'error':str(exc)})
            except KeyError: self.send_error(404)
            return
        if route.startswith('/api/advanced/mcp/'):
            name=urllib.parse.unquote(route.rsplit('/',1)[-1]); mcp_manager.disconnect(name)
            if not session_store.delete_mcp_server(name): self.send_error(404)
            else: json_response(self,200,{'deleted':True,'name':name})
            return
        if route.startswith('/api/advanced/memories/'):
            key=urllib.parse.unquote(route.rsplit('/',1)[-1]); query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query); project=query.get('project',[str(ROOT)])[0]
            if not session_store.forget(project,key): self.send_error(404)
            else: json_response(self,200,{'deleted':True,'project':project,'key':key})
            return
        if route.startswith('/api/advanced/notifications/'):
            value=route.rsplit('/',1)[-1]
            if not value.isdigit(): self.send_error(400); return
            if not session_store.delete_notification_endpoint(int(value)): self.send_error(404)
            else: json_response(self,200,{'deleted':True,'endpoint_id':int(value)})
            return
        if route.startswith('/api/advanced/worktrees/'):
            name=urllib.parse.unquote(route.rsplit('/',1)[-1])
            try: WorktreeManager(ROOT).remove(name); json_response(self,200,{'removed':True,'name':name})
            except Exception as exc: json_response(self,409,{'error':str(exc)})
            return
        if not route.startswith('/api/chat/permission-grants/'):
            self.send_error(404); return
        value=route.rsplit('/',1)[-1]
        if not value.isdigit(): self.send_error(400); return
        if not permission_engine.revoke(int(value),self.user_id): self.send_error(404); return
        json_response(self,200,{'revoked':True,'grant_id':int(value)})
    def read_json_body(self,max_bytes):
        length=int(self.headers.get('Content-Length','0'))
        if length <= 0 or length > max_bytes: raise ValueError('invalid request size')
        try: value=json.loads(self.rfile.read(length))
        except json.JSONDecodeError: raise ValueError('request body must be valid JSON')
        if not isinstance(value,dict): raise ValueError('request body must be a JSON object')
        return value
    def proxy_image(self, route):
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > 50_000_000: raise ValueError('invalid request size')
            incoming=self.rfile.read(length); target='/v1/images/edits' if route.endswith('edits') else '/v1/images/generations'
            content_type=self.headers.get('Content-Type','application/json')
            req=urllib.request.Request(IMAGE_API+target,data=incoming,headers={'Authorization':'Bearer '+image_api_key(),'Content-Type':content_type},method='POST')
            with urllib.request.urlopen(req,timeout=1800) as response: result=json.loads(response.read())
            remote=result.get('data',[{}])[0].get('url')
            if not remote: raise ValueError('image service returned no result URL')
            parsed=urllib.parse.urlsplit(remote); local_url=IMAGE_API+parsed.path
            image_req=urllib.request.Request(local_url,headers={'Authorization':'Bearer '+image_api_key()})
            with urllib.request.urlopen(image_req,timeout=120) as response: image=response.read()
            stamp=int(time.time()); name=f'{stamp}-{uuid.uuid4().hex[:10]}.png'; (GENERATED_DIR/name).write_bytes(image)
            if route.endswith('generations'):
                source=json.loads(incoming); mode='text-to-image'
            else:
                source={}; mode='image-to-image'
                match=re.search(br'name="prompt"\r\n\r\n(.*?)\r\n--',incoming,re.S)
                if match: source['prompt']=match.group(1).decode('utf-8','replace')
            record={'file':name,'created':stamp,'model':'qwen-image-edit-2511' if mode=='image-to-image' else 'qwen-image-2512','mode':mode,'seed':result.get('seed'),'seconds':'','prompt':source.get('prompt','')}
            with image_history_lock:
                with (GENERATED_DIR/'history.jsonl').open('a',encoding='utf-8') as out: out.write(json.dumps(record,ensure_ascii=False)+'\n')
            self.send_response(200); self.send_header('Content-Type','image/png'); self.send_header('Content-Length',str(len(image))); self.send_header('X-Gen-Model',record['model']); self.send_header('X-Gen-Seed',str(record['seed'])); self.send_header('X-History-File',name); self.end_headers(); self.wfile.write(image)
        except urllib.error.HTTPError as e:
            detail=e.read().decode(errors='replace')[:2000]; body=json.dumps({'detail':detail}).encode(); self.send_response(e.code if 400<=e.code<500 else 502); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except Exception as e:
            body=json.dumps({'detail':str(e)}).encode(); self.send_response(502); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def translate_path(self, path):
        decoded=urllib.parse.unquote(urllib.parse.urlsplit(path).path)
        candidate=(ROOT/decoded.lstrip('/')).resolve()
        try: candidate.relative_to(ROOT)
        except ValueError: return str(ROOT/'__not_found__')
        return str(candidate)
    def log_message(self, fmt, *args): pass

if __name__ == '__main__':
    threading.Thread(target=collector, daemon=True).start()
    port = int(os.environ.get('THOR_MONITOR_PORT', '8090'))
    print(f'Thor Monitor listening on 0.0.0.0:{port}', flush=True)
    ThreadingHTTPServer(('0.0.0.0', port), Handler).serve_forever()
