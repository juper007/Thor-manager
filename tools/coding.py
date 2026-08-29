import difflib
import hashlib
import os
import re
import subprocess
import tempfile
import uuid
from pathlib import Path

from tools.workspace import MAX_READ_BYTES,WorkspaceError,_inside


SANDBOX_IMAGE='nvcr.io/nvidia/pytorch:26.05-py3'
BLOCKED_COMMANDS=re.compile(r'(?i)(?:^|[;&|]\s*)(sudo|su|ssh|scp|mount|umount|shutdown|reboot|docker)\b|\bgit\s+(push|reset|clean)\b|\brm\s+[^\n]*-[^\n]*r')


def sha256_bytes(data): return hashlib.sha256(data).hexdigest()


def _target(manager,args,allow_new=False):
    root=manager.select(args.get('workspace')); raw=Path(args['path'])
    path=(raw if raw.is_absolute() else root/raw).resolve(strict=not allow_new)
    if not _inside(path,root): raise WorkspaceError('path escapes the selected workspace')
    if manager.protected(path,root): raise WorkspaceError('path is protected')
    if path.exists() and (not path.is_file() or manager.ignored(path,root)): raise WorkspaceError('path is not a writable text file')
    return root,path


def _existing(path):
    if not path.exists(): return b'',False
    data=path.read_bytes()
    if len(data)>MAX_READ_BYTES: raise WorkspaceError('file exceeds write size limit')
    if b'\x00' in data: raise WorkspaceError('binary files cannot be changed')
    try: data.decode('utf-8')
    except UnicodeDecodeError: raise WorkspaceError('file is not valid UTF-8 text')
    return data,True


def _preview(path,old,new):
    return ''.join(difflib.unified_diff(old.splitlines(True),new.splitlines(True),fromfile=str(path),tofile=str(path)))[:50_000]


def _apply(path,data,mode=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        if mode is not None: os.chmod(name,mode)
        os.replace(name,path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


def file_write(manager,args):
    root,path=_target(manager,args,True); old,existed=_existing(path); expected=args.get('expected_sha256')
    if existed and not expected: raise WorkspaceError('expected_sha256 is required for an existing file')
    if expected and expected!=sha256_bytes(old): raise WorkspaceError('file changed since it was read')
    content=args['content']; new=content.encode('utf-8')
    if len(new)>MAX_READ_BYTES: raise WorkspaceError('new content exceeds write size limit')
    diff=_preview(path.relative_to(root),old.decode('utf-8'),content)
    if args.get('apply',False): _apply(path,new,path.stat().st_mode if existed else 0o644)
    return {'workspace':str(root),'path':path.relative_to(root).as_posix(),'applied':bool(args.get('apply',False)),'before_sha256':sha256_bytes(old) if existed else None,'after_sha256':sha256_bytes(new),'diff':diff}


def file_patch(manager,args):
    root,path=_target(manager,args); old_bytes,_=_existing(path); expected=args['expected_sha256']
    if expected!=sha256_bytes(old_bytes): raise WorkspaceError('file changed since it was read')
    old=old_bytes.decode('utf-8'); needle=args['old_text']; count=old.count(needle)
    if count!=1: raise WorkspaceError(f'old_text must match exactly once; found {count}')
    new=old.replace(needle,args['new_text'],1); diff=_preview(path.relative_to(root),old,new)
    if args.get('apply',False): _apply(path,new.encode(),path.stat().st_mode)
    return {'workspace':str(root),'path':path.relative_to(root).as_posix(),'applied':bool(args.get('apply',False)),'before_sha256':expected,'after_sha256':sha256_bytes(new.encode()),'diff':diff}


def _sandbox(manager,args,test=False):
    root=manager.select(args.get('workspace')); command=args['command'].strip()
    if not command or BLOCKED_COMMANDS.search(command): raise WorkspaceError('command is empty or blocked by policy')
    timeout=min(args.get('timeout_seconds',120),300); name='thor-work-'+uuid.uuid4().hex[:12]
    mount=f'{root}:/workspace:rw'; cmd=['docker','run','--rm','--name',name,'--network','none','--memory','2g','--memory-swap','2g','--cpus','4','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}','-v',mount,'-w','/workspace','--entrypoint','/bin/bash',SANDBOX_IMAGE,'-lc',command]
    try: result=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
        return {'return_code':124,'stdout':'','stderr':f'timed out after {timeout} seconds','test':test}
    return {'return_code':result.returncode,'stdout':result.stdout[-20000:],'stderr':result.stderr[-10000:],'test':test,'sandbox':{'network':'disabled','memory_mb':2048,'cpus':4,'pids':128}}


def shell_execute(manager,args): return _sandbox(manager,args,False)
def test_run(manager,args): return _sandbox(manager,args,True)


def git_commit(manager,args):
    root=manager.select(args.get('workspace')); message=args['message'].strip()
    if not message or len(message)>200: raise WorkspaceError('commit message must contain 1 to 200 characters')
    status=manager._git(root,'status','--porcelain')
    if not status.stdout.strip(): raise WorkspaceError('nothing to commit')
    result=manager._git(root,'commit','-m',message,timeout=60)
    revision=manager._git(root,'rev-parse','--short','HEAD').stdout.strip()
    return {'workspace':str(root),'commit':revision,'output':result.stdout[-10000:]}
