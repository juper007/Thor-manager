"""Minimal MCP 2025-03-26 stdio client and managed connection registry."""
import json
import os
import subprocess
import threading
import queue


class MCPError(RuntimeError): pass


class MCPClient:
    def __init__(self,command,cwd=None,env=None,timeout=30):
        if not isinstance(command,list) or not command or not all(isinstance(x,str) and x for x in command):
            raise ValueError('MCP command must be a non-empty string array')
        self.command=command; self.cwd=cwd; self.env=env or {}; self.timeout=max(.1,float(timeout))
        self.process=None; self._request_id=0; self._lock=threading.Lock()

    def connect(self):
        if self.process and self.process.poll() is None: return self
        environment=os.environ.copy(); environment.update({str(k):str(v) for k,v in self.env.items()})
        self.process=subprocess.Popen(self.command,cwd=self.cwd,env=environment,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',bufsize=1)
        result=self.request('initialize',{'protocolVersion':'2025-03-26','capabilities':{},'clientInfo':{'name':'thor-monitor','version':'1'}})
        self.notify('notifications/initialized',{})
        return result

    def _write(self,value):
        if not self.process or self.process.poll() is not None: raise MCPError('MCP server is not connected')
        self.process.stdin.write(json.dumps(value,separators=(',',':'))+'\n'); self.process.stdin.flush()

    def _readline(self):
        output=queue.Queue(maxsize=1)
        threading.Thread(target=lambda:output.put(self.process.stdout.readline()),daemon=True).start()
        try: return output.get(timeout=self.timeout)
        except queue.Empty as exc: raise MCPError(f'MCP request timed out after {self.timeout} seconds') from exc

    def request(self,method,params=None):
        with self._lock:
            self._request_id+=1; request_id=self._request_id
            self._write({'jsonrpc':'2.0','id':request_id,'method':method,'params':params or {}})
            while True:
                line=self._readline()
                if not line: raise MCPError('MCP server closed the connection')
                try: response=json.loads(line)
                except json.JSONDecodeError: continue
                if response.get('id')!=request_id: continue
                if 'error' in response: raise MCPError(str(response['error']))
                return response.get('result')

    def notify(self,method,params=None): self._write({'jsonrpc':'2.0','method':method,'params':params or {}})
    def list_tools(self): return self.request('tools/list').get('tools',[])
    def call_tool(self,name,arguments=None): return self.request('tools/call',{'name':name,'arguments':arguments or {}})
    def close(self):
        process=self.process; self.process=None
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=2)
            except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=2)


class MCPConnectionManager:
    def __init__(self,store,client_factory=MCPClient): self.store=store; self.client_factory=client_factory; self._clients={}; self._lock=threading.Lock()
    def servers(self): return self.store.list_mcp_servers()
    def add(self,name,command,cwd=None,env=None,enabled=True): self.store.upsert_mcp_server(name,command,cwd,env,enabled)
    def connect(self,name):
        with self._lock:
            if name in self._clients: return self._clients[name]
            server=next((x for x in self.store.list_mcp_servers(True) if x['name']==name),None)
            if not server: raise KeyError(name)
            client=self.client_factory(server['command'],server['cwd'],server['env']); client.connect(); self._clients[name]=client; return client
    def disconnect(self,name):
        with self._lock: client=self._clients.pop(name,None)
        if client: client.close()
        return client is not None
    def status(self):
        configured=self.servers(); connected=set(self._clients)
        return [{**item,'connected':item['name'] in connected} for item in configured]
    def close(self):
        for name in list(self._clients): self.disconnect(name)
