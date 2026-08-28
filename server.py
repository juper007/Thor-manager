#!/usr/bin/env python3
import base64, hmac, json, os, re, shutil, socket, subprocess, threading, time, urllib.request, urllib.error, urllib.parse, uuid
from collections import deque
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
from agent import models
from agent.runtime import AgentRuntime,ServiceBusy,validate_messages
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

def edge_chat(messages, max_tokens=4096):
    return models.edge_chat(messages,max_tokens=max_tokens)

runtime=AgentRuntime(
    ROOT,
    lambda messages: edge_chat(messages),
    agent_tools.DEFAULT_REGISTRY,
    agent_tools.parse_tool_calls,
    agent_tools.load_skill_instructions,
    lambda text: agent_tools.TOOL_CALL_RE.sub('',text).strip(),
    AI_CONCURRENCY,
)


def agent_chat(messages): return runtime.chat(messages)

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
        if route != '/api/chat': self.send_error(404); return
        try:
            length=int(self.headers.get('Content-Length','0'))
            if length <= 0 or length > 2_000_000: raise ValueError('invalid request size')
            incoming=json.loads(self.rfile.read(length))
            content,events,sources=agent_chat(validate_messages(incoming.get('messages')))
            public_events=[{'name':e['name'],'arguments':e['arguments'],'seconds':e['seconds'],'error':e['error']} for e in events]
            body=(json.dumps({'message':{'role':'assistant','content':content},'tools_used':public_events,'sources':sources,'done':True},ensure_ascii=False)+'\n').encode()
            self.send_response(200); self.send_header('Content-Type','application/x-ndjson; charset=utf-8'); self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
        except ValueError as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(400); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except ServiceBusy as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(429); self.send_header('Content-Type','application/json'); self.send_header('Retry-After','5'); self.end_headers(); self.wfile.write(body)
        except urllib.error.HTTPError as e:
            detail=e.read().decode(errors='replace')[:1000]; body=json.dumps({'error':detail,'done':True}).encode(); self.send_response(502); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
        except Exception as e:
            body=json.dumps({'error':str(e),'done':True}).encode(); self.send_response(502); self.send_header('Content-Type','application/json'); self.end_headers(); self.wfile.write(body)
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
