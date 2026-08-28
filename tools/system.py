import os
import shutil
import socket
import subprocess
from pathlib import Path


def system_status(args):
    disk=shutil.disk_usage('/'); mem={}
    for line in Path('/proc/meminfo').read_text().splitlines():
        if ':' in line:
            key,value=line.split(':',1)
            try: mem[key]=int(value.strip().split()[0])*1024
            except (ValueError,IndexError): pass
    gpu={}
    try:
        output=subprocess.check_output(['nvidia-smi','--query-gpu=utilization.gpu,temperature.gpu,power.draw','--format=csv,noheader,nounits'],text=True,timeout=3)
        util,temp,power=[float(x.strip()) for x in output.splitlines()[0].split(',')]
        gpu={'utilization_percent':util,'temperature_c':temp,'power_w':power}
    except Exception: pass
    return {'hostname':socket.gethostname(),'uptime_seconds':float(Path('/proc/uptime').read_text().split()[0]),'load_average':list(os.getloadavg()),'memory':{'total':mem.get('MemTotal',0),'available':mem.get('MemAvailable',0)},'disk':{'total':disk.total,'free':disk.free},'gpu':gpu}
