#!/usr/bin/env python3
import base64, hmac, json, os, re, shutil, socket, subprocess, threading, time, urllib.request, urllib.error, urllib.parse, uuid
from collections import deque
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from agent import models
from agent.runtime import AgentRuntime,RunCancelled,RunLimitError,ServiceBusy,validate_messages,validate_run_id
from agent.permissions import PermissionEngine
from storage import SessionStore
import agent_tools

ROOT = Path(__file__).resolve().parent


def positive_int_env(name,default):
    try: return max(1,int(os.environ.get(name,str(default))))
    except ValueError: return default


state = {"timestamp": 0, "cpu": 0, "gpu": 0, "memory": {}, "temps": {}, "power": {}, "clocks": [], "raw": ""}
history = deque(maxlen=300)
lock = threading.Lock()
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
    session_store=session_store,
    permission_engine=permission_engine,
    stream_model_call=lambda messages,callback: edge_chat(messages,on_delta=callback),
)


def agent_chat(messages): return runtime.chat(messages)

def agent_run_chat(messages,run_id=None): return runtime.run_chat(messages,run_id)

def json_response(handler,status,value):
    body=json.dumps(value,ensure_ascii=False).encode()
    handler.send_response(status); handler.send_header('Content-Type','application/json; charset=utf-8')
    handler.send_header('Content-Length',str(len(body))); handler.end_headers(); handler.wfile.write(body)

def read_text(path, default=""):
    try: return Path(path).read_text().strip()
    except OSError: return default

def cpu_percent():
    vals = list(map(int, read_text('/proc/stat').splitlines()[0].split()[1:]))
    idle, total = vals[3] + vals[4], sum(vals)
    prev = getattr(cpu_percent, 'prev', (idle, total)); cpu_percent.prev = (idle, total)
    return round(100 * (1 - (idle-prev[0]) / max(1, total-prev[1])), 1)

def gpu_utilization():
    try:
        out = subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,utilization.memory','--format=csv,noheader,nounits'], text=True, timeout=.8)
        values = [int(float(v.strip())) for v in out.splitlines()[0].split(',')]
        return values[0], values[1]
    except (OSError, subprocess.SubprocessError, ValueError, IndexError): return 0, 0

def parse(line):
    item = {"timestamp": int(time.time()*1000), "raw": line, "cpu": cpu_percent()}
    ram = re.search(r'RAM\s+(\d+)/(\d+)MB', line)
    if ram:
        used, total = map(int, ram.groups()); item["memory"] = {"used": used, "total": total, "percent": round(used*100/total,1)}
    cpus = re.search(r'CPU \[(.*?)\]', line)
    item["clocks"] = [int(x) for x in re.findall(r'@(\d+)', cpus.group(1))] if cpus else []
    item["gpu"], item["gpu_memory"] = gpu_utilization()
    gpu_match = re.search(r'(?:GR3D_FREQ|GPU)\s+(\d+)%', line)
    if gpu_match: item["gpu"] = int(gpu_match.group(1))
    item["temps"] = {k: float(v) for k,v in re.findall(r'(cpu|gpu|tj|soc\d+)@([\d.]+)C', line)}
    item["power"] = {k: int(v) for k,v in re.findall(r'(VDD_GPU|VDD_CPU_SOC_MSS|VIN(?:_SYS_5V0)?)\s+(\d+)mW', line)}
    return item

def disk_net():
    disk = shutil.disk_usage('/')
    net = {}
    for row in read_text('/proc/net/dev').splitlines()[2:]:
        name, values = row.split(':',1); nums = values.split()
        if name.strip() != 'lo': net[name.strip()] = {"rx": int(nums[0]), "tx": int(nums[8])}
    mi = {}
    for row in read_text('/proc/meminfo').splitlines():
        if ':' in row:
            key, value = row.split(':', 1)
            try: mi[key] = int(value.strip().split()[0]) * 1024
            except (ValueError, IndexError): pass
    total, available = mi.get('MemTotal',0), mi.get('MemAvailable',0)
    detail = {"total":total,"available":available,"used":max(0,total-available),"free":mi.get('MemFree',0),
        "buffers":mi.get('Buffers',0),"cached":mi.get('Cached',0)+mi.get('SReclaimable',0),"shared":mi.get('Shmem',0),
        "swap_total":mi.get('SwapTotal',0),"swap_used":max(0,mi.get('SwapTotal',0)-mi.get('SwapFree',0))}
    procs = []
    for p in Path('/proc').iterdir():
        if not p.name.isdigit(): continue
        try:
            status=(p/'status').read_text(); name=re.search(r'^Name:\s+(.+)$',status,re.M).group(1)
            rss=int(re.search(r'^VmRSS:\s+(\d+)',status,re.M).group(1))*1024
            if rss: procs.append({"pid":int(p.name),"name":name,"rss":rss})
        except (OSError, AttributeError): pass
    detail['processes']=sorted(procs,key=lambda x:x['rss'],reverse=True)[:8]
    return {"disk": {"used": disk.used, "total": disk.total, "percent": round(disk.used*100/disk.total,1)}, "network": net, "memory_detail": detail,
            "uptime": float(read_text('/proc/uptime','0').split()[0]), "hostname": socket.gethostname(), "load": list(os.getloadavg())}

def collector():
    while True:
        try:
            proc = subprocess.Popen(['tegrastats','--interval','1000'], stdout=subprocess.PIPE, text=True)
            for line in proc.stdout:
                item = parse(line.strip()); item.update(disk_net())
                with lock: state.update(item); history.append({k:item.get(k) for k in ('timestamp','cpu','gpu','memory','temps','power')})
        except Exception as e:
            with lock: state.update({"timestamp":int(time.time()*1000), "error":str(e), **disk_net()})
            time.sleep(2)

class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header('Cache-Control','no-store, no-cache, must-revalidate, max-age=0')
        self.send_header('Pragma','no-cache')
        self.send_header('Expires','0')
        super().end_headers()
    def authenticated(self):
        password = os.environ.get('THOR_MONITOR_PASSWORD', '')
        if not password:
            self.send_error(503,'THOR_MONITOR_PASSWORD is not configured'); return False
        expected = 'Basic ' + base64.b64encode(('thor:' + password).encode()).decode()
        if hmac.compare_digest(self.headers.get('Authorization',''), expected): return True
        self.send_response(401); self.send_header('WWW-Authenticate','Basic realm="Jetson Thor Monitor"'); self.end_headers()
        return False
    def do_GET(self):
        if not self.authenticated(): return
        route = self.path.split('?',1)[0]
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
        if route == '/api/chat/approvals':
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            run_id=query.get('run_id',[None])[0]; status=query.get('status',[None])[0]
            if run_id is not None:
                try: run_id=validate_run_id(run_id)
                except ValueError: self.send_error(400); return
            if status not in (None,'pending','allowed','denied','expired','cancelled'): self.send_error(400); return
            json_response(self,200,{'approvals':permission_engine.list(run_id,status)}); return
        if route == '/api/chat/permission-grants':
            json_response(self,200,{'grants':permission_engine.grants()}); return
        if route == '/api/chat/sessions':
            query=urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query)
            try: rows=session_store.list_sessions(query.get('limit',['50'])[0],query.get('offset',['0'])[0])
            except ValueError: self.send_error(400); return
            json_response(self,200,{'sessions':rows}); return
        if route.startswith('/api/chat/sessions/'):
            run_id=urllib.parse.unquote(route.rsplit('/',1)[-1])
            try: run_id=validate_run_id(run_id)
            except ValueError: self.send_error(400); return
            session=session_store.get_session(run_id)
            if session is None: self.send_error(404); return
            json_response(self,200,session); return
        if route.startswith('/api/chat/runs/'):
            run_id=urllib.parse.unquote(route.rsplit('/',1)[-1])
            try: run_id=validate_run_id(run_id)
            except ValueError: self.send_error(400); return
            snapshot=runtime.run_snapshot(run_id)
            if snapshot is None:
                stored=session_store.get_session(run_id)
                if stored is not None: snapshot={key:stored[key] for key in ('run_id','state','created_at','updated_at','iterations','tool_calls','error')}; snapshot['events']=stored['events']
            if snapshot is None: self.send_error(404); return
            body=json.dumps(snapshot,ensure_ascii=False).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        if route == '/ai':
            html=(ROOT/'ai-workspace.html').read_text(encoding='utf-8')
            body=html.encode(); self.send_response(200); self.send_header('Content-Type','text/html; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body); return
        elif route == '/': self.path = '/index.html'
        return super().do_GET()
    def do_POST(self):
        if not self.authenticated(): return
        route=self.path.split('?',1)[0]
        if route in ('/api/images/generations','/api/images/edits'):
            return self.proxy_image(route)
        if route == '/api/chat/cancel':
            try:
                incoming=self.read_json_body(4096)
                if 'run_id' not in incoming: raise ValueError('run_id is required')
                run_id=validate_run_id(incoming['run_id'])
                snapshot=runtime.cancel(run_id)
                if snapshot is None: self.send_error(404); return
                body=json.dumps(snapshot,ensure_ascii=False).encode(); self.send_response(200); self.send_header('Content-Type','application/json'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
            except ValueError as e:
                body=json.dumps({'error':str(e)}).encode(); self.send_response(400); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
            return
        if route.startswith('/api/chat/approvals/'):
            approval_id=urllib.parse.unquote(route.rsplit('/',1)[-1])
            if not re.fullmatch(r'[0-9a-f]{32}',approval_id): self.send_error(400); return
            try:
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
                messages=validate_messages(session_store.resumable_messages(run_id))
                run,content,events,sources=runtime.run_chat(messages,new_run_id,resumed_from=run_id)
                public_events=[{'name':e['name'],'arguments':e['arguments'],'seconds':e['seconds'],'error':e['error']} for e in events]
                json_response(self,200,{'run_id':run.run_id,'resumed_from':run_id,'run_state':run.state.value,'message':{'role':'assistant','content':content},'tools_used':public_events,'sources':sources,'done':True})
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
            if incoming.get('stream') is True:
                self.stream_chat(messages,requested_run_id); return
            run,content,events,sources=agent_run_chat(messages,requested_run_id)
            public_events=[{'name':e['name'],'arguments':e['arguments'],'seconds':e['seconds'],'error':e['error']} for e in events]
            body=(json.dumps({'run_id':run.run_id,'run_state':run.state.value,'message':{'role':'assistant','content':content},'tools_used':public_events,'sources':sources,'done':True},ensure_ascii=False)+'\n').encode()
            self.send_response(200); self.send_header('Content-Type','application/x-ndjson; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        except ValueError as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(400); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except ServiceBusy as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(429); self.send_header('Content-Type','application/json'); self.send_header('Retry-After','5'); self.end_headers(); self.wfile.write(body)
        except RunCancelled as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(409); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except RunLimitError as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(408); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except urllib.error.HTTPError as e:
            detail=e.read().decode(errors='replace')[:1000]; body=json.dumps({'error':detail,'done':True}).encode(); self.send_response(502); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except Exception as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(502); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
    def stream_chat(self,messages,run_id):
        self.send_response(200); self.send_header('Content-Type','application/x-ndjson; charset=utf-8')
        self.send_header('X-Accel-Buffering','no'); self.send_header('Connection','close'); self.end_headers()
        def send(value):
            self.wfile.write((json.dumps(value,ensure_ascii=False)+'\n').encode()); self.wfile.flush()
        try:
            send({'type':'start','run_id':run_id})
            run,content,events,sources=runtime.run_chat(messages,run_id,on_delta=lambda delta:send({'type':'delta','run_id':run_id,'message':{'role':'assistant','content':delta}}))
            public_events=[{'name':e['name'],'arguments':e['arguments'],'seconds':e['seconds'],'error':e['error']} for e in events]
            send({'type':'final','run_id':run.run_id,'run_state':run.state.value,'message':{'role':'assistant','content':''},'final_content':content,'tools_used':public_events,'sources':sources,'done':True})
        except Exception as exc:
            try: send({'type':'error','run_id':run_id,'error':str(exc),'done':True})
            except (BrokenPipeError,ConnectionResetError): pass
    def do_DELETE(self):
        if not self.authenticated(): return
        route=self.path.split('?',1)[0]
        if not route.startswith('/api/chat/permission-grants/'):
            self.send_error(404); return
        value=route.rsplit('/',1)[-1]
        if not value.isdigit(): self.send_error(400); return
        if not permission_engine.revoke(int(value)): self.send_error(404); return
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
