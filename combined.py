#!/usr/bin/env python3
import base64,hmac,json,os,sys,threading,time,uuid
from pathlib import Path
ROOT=Path(__file__).resolve().parent; AI_APP=Path('/home/juper007/aiserver/app'); sys.path.insert(0,str(AI_APP))
import uvicorn
from fastapi import FastAPI,Request
from fastapi.responses import FileResponse,HTMLResponse,JSONResponse
import imgservice
import server as monitor
app=FastAPI(title='Jetson Thor Control Plane')
@app.middleware('http')
async def authentication(request:Request,call_next):
    password=os.environ.get('THOR_MONITOR_PASSWORD',''); expected='Basic '+base64.b64encode(('thor:'+password).encode()).decode()
    if password and not hmac.compare_digest(request.headers.get('Authorization',''),expected): return JSONResponse({'detail':'Authentication required'},401,headers={'WWW-Authenticate':'Basic realm="Jetson Thor"'})
    response=await call_next(request)
    response.headers['Cache-Control']='no-store, no-cache, must-revalidate, max-age=0'
    response.headers['Pragma']='no-cache'
    response.headers['Expires']='0'
    return response
for route in imgservice.app.router.routes:
    if getattr(route,'path','') not in ('/','/api/health','/api/images'): app.router.routes.append(route)
HISTORY_DIR=Path('/home/juper007/aiserver/generated'); HISTORY_DIR.mkdir(parents=True,exist_ok=True)
history_lock=threading.Lock()
@app.post('/api/images')
def generate_and_save(req:imgservice.GenRequest):
    response=imgservice.generate(req); stamp=int(time.time()); name=f'{stamp}-{uuid.uuid4().hex[:8]}.png'
    (HISTORY_DIR/name).write_bytes(response.body)
    record={'file':name,'created':stamp,'model':response.headers.get('X-Gen-Model',req.model),'seed':response.headers.get('X-Gen-Seed'),'seconds':response.headers.get('X-Gen-Seconds'),'prompt':req.prompt,'width':req.width,'height':req.height}
    with history_lock:
        with (HISTORY_DIR/'history.jsonl').open('a',encoding='utf-8') as f: f.write(json.dumps(record,ensure_ascii=False)+'\n')
    response.headers['X-History-File']=name
    return response
@app.get('/api/images/history')
def image_history():
    try:
        rows=[json.loads(x) for x in (HISTORY_DIR/'history.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    except (OSError,json.JSONDecodeError): rows=[]
    return list(reversed(rows[-100:]))
@app.get('/api/images/history/{filename}')
def history_image(filename:str):
    if not filename.endswith('.png') or '/' in filename or '\\' in filename: return JSONResponse({'detail':'Not found'},404)
    path=HISTORY_DIR/filename
    return FileResponse(path,media_type='image/png') if path.is_file() else JSONResponse({'detail':'Not found'},404)
@app.get('/api/health')
def health(): return {'status':'ok','ai':imgservice.health(),'monitor':monitor.state.get('timestamp',0)>0}
@app.get('/api/stats')
def stats():
    with monitor.lock: return {**monitor.state,'history':list(monitor.history)}
@app.get('/')
def dashboard(): return FileResponse(ROOT/'index.html')
@app.get('/ai')
def ai_page():
    html=(ROOT/'ai-workspace.html').read_text(encoding='utf-8').replace('</head>','<link rel="stylesheet" href="/ai-history.css?v=1"></head>').replace('</body>','<script src="/ai-history.js?v=1"></script></body>')
    return HTMLResponse(html)
@app.get('/{asset_name}')
def asset(asset_name:str):
    if asset_name not in {'style.css','detail.css','app.js','memory.js','tabs.css','tabs.js','ai-workspace.css','ai-workspace.js','ai-history.css','ai-history.js'}: return JSONResponse({'detail':'Not found'},404)
    return FileResponse(ROOT/asset_name)
if __name__=='__main__':
    threading.Thread(target=monitor.collector,daemon=True).start(); threading.Thread(target=imgservice._watchdog_loop,daemon=True).start()
    uvicorn.run(app,host='0.0.0.0',port=int(os.environ.get('THOR_MONITOR_PORT','8090')),log_level='info')
