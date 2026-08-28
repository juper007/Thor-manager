import subprocess
import uuid


def python_execute(args):
    code=str(args.get('code',''))
    if not code.strip(): raise ValueError('code is required')
    if len(code)>12000: raise ValueError('code exceeds the 12000 character limit')
    name='thor-code-'+uuid.uuid4().hex[:12]
    command=['docker','run','--rm','-i','--name',name,'--network','none','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=256m','--memory','1g','--memory-swap','1g','--cpus','2','--pids-limit','64','--cap-drop','ALL','--security-opt','no-new-privileges','--user','65534:65534','--entrypoint','python','nvcr.io/nvidia/pytorch:26.05-py3','-I','-']
    try: completed=subprocess.run(command,input=code,text=True,capture_output=True,timeout=30)
    except subprocess.TimeoutExpired:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
        return {'return_code':124,'stdout':'','stderr':'Execution timed out after 30 seconds.','sandbox':{'network':'disabled','memory_mb':1024,'cpus':2}}
    return {'return_code':completed.returncode,'stdout':completed.stdout[-12000:],'stderr':completed.stderr[-6000:],'sandbox':{'network':'disabled','memory_mb':1024,'cpus':2}}
