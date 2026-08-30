"""Jetson telemetry collection independent from the HTTP server."""
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from collections import deque
from pathlib import Path


state={'timestamp':0,'cpu':0,'gpu':0,'memory':{},'temps':{},'power':{},'clocks':[],'raw':''}
history=deque(maxlen=300)
lock=threading.Lock()


def read_text(path,default=''):
    try: return Path(path).read_text().strip()
    except OSError: return default


def cpu_percent():
    rows=read_text('/proc/stat').splitlines()
    if not rows: return 0
    try: values=list(map(int,rows[0].split()[1:]))
    except ValueError: return 0
    if len(values)<5: return 0
    idle,total=values[3]+values[4],sum(values)
    previous=getattr(cpu_percent,'prev',(idle,total)); cpu_percent.prev=(idle,total)
    return round(100*(1-(idle-previous[0])/max(1,total-previous[1])),1)


def gpu_utilization():
    try:
        output=subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,utilization.memory','--format=csv,noheader,nounits'],text=True,timeout=.8)
        values=[int(float(value.strip())) for value in output.splitlines()[0].split(',')]
        return values[0],values[1]
    except (OSError,subprocess.SubprocessError,ValueError,IndexError): return 0,0


def parse(line):
    item={'timestamp':int(time.time()*1000),'raw':line,'cpu':cpu_percent()}
    ram=re.search(r'RAM\s+(\d+)/(\d+)MB',line)
    if ram:
        used,total=map(int,ram.groups())
        item['memory']={'used':used,'total':total,'percent':round(used*100/total,1) if total else 0}
    cpus=re.search(r'CPU \[(.*?)\]',line)
    item['clocks']=[int(value) for value in re.findall(r'@(\d+)',cpus.group(1))] if cpus else []
    item['gpu'],item['gpu_memory']=gpu_utilization()
    gpu_match=re.search(r'(?:GR3D_FREQ|GPU)\s+(\d+)%',line)
    if gpu_match: item['gpu']=int(gpu_match.group(1))
    item['temps']={key:float(value) for key,value in re.findall(r'(cpu|gpu|tj|soc\d+)@([\d.]+)C',line)}
    item['power']={key:int(value) for key,value in re.findall(r'(VDD_GPU|VDD_CPU_SOC_MSS|VIN(?:_SYS_5V0)?)\s+(\d+)mW',line)}
    return item


def disk_net():
    disk=shutil.disk_usage('/'); network={}
    for row in read_text('/proc/net/dev').splitlines()[2:]:
        if ':' not in row: continue
        name,values=row.split(':',1); numbers=values.split()
        try:
            if name.strip()!='lo': network[name.strip()]={'rx':int(numbers[0]),'tx':int(numbers[8])}
        except (ValueError,IndexError): pass
    memory={}
    for row in read_text('/proc/meminfo').splitlines():
        if ':' in row:
            key,value=row.split(':',1)
            try: memory[key]=int(value.strip().split()[0])*1024
            except (ValueError,IndexError): pass
    total,available=memory.get('MemTotal',0),memory.get('MemAvailable',0)
    detail={'total':total,'available':available,'used':max(0,total-available),'free':memory.get('MemFree',0),
        'buffers':memory.get('Buffers',0),'cached':memory.get('Cached',0)+memory.get('SReclaimable',0),'shared':memory.get('Shmem',0),
        'swap_total':memory.get('SwapTotal',0),'swap_used':max(0,memory.get('SwapTotal',0)-memory.get('SwapFree',0))}
    processes=[]
    try:
        for process in Path('/proc').iterdir():
            if not process.name.isdigit(): continue
            try:
                status=(process/'status').read_text(); name=re.search(r'^Name:\s+(.+)$',status,re.M).group(1)
                rss=int(re.search(r'^VmRSS:\s+(\d+)',status,re.M).group(1))*1024
                if rss: processes.append({'pid':int(process.name),'name':name,'rss':rss})
            except (OSError,AttributeError): pass
    except OSError: pass
    detail['processes']=sorted(processes,key=lambda item:item['rss'],reverse=True)[:8]
    try: uptime=float(read_text('/proc/uptime','0').split()[0])
    except (ValueError,IndexError): uptime=0
    try: load=list(os.getloadavg())
    except OSError: load=[0,0,0]
    disk_percent=round(disk.used*100/disk.total,1) if disk.total else 0
    return {'disk':{'used':disk.used,'total':disk.total,'percent':disk_percent},'network':network,
        'memory_detail':detail,'uptime':uptime,'hostname':socket.gethostname(),'load':load}


def collector():
    while True:
        try:
            process=subprocess.Popen(['tegrastats','--interval','1000'],stdout=subprocess.PIPE,text=True)
            for line in process.stdout:
                item=parse(line.strip()); item.update(disk_net())
                with lock: state.update(item); history.append({key:item.get(key) for key in ('timestamp','cpu','gpu','memory','temps','power')})
        except Exception as exc:
            with lock: state.update({'timestamp':int(time.time()*1000),'error':str(exc),**disk_net()})
            time.sleep(2)
