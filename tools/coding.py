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
    if manager.ignored(path,root) or (path.exists() and not path.is_file()): raise WorkspaceError('path is not a writable text file')
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


def _apply(path,data,expected,existed,mode=None):
    path.parent.mkdir(parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'wb') as stream: stream.write(data); stream.flush(); os.fsync(stream.fileno())
        current,current_exists=_existing(path)
        if current_exists!=existed or (existed and sha256_bytes(current)!=expected): raise WorkspaceError('file changed before atomic replacement')
        if mode is not None: os.chmod(name,mode)
        os.replace(name,path)
    finally:
        try: os.unlink(name)
        except FileNotFoundError: pass


def file_write(manager,args):
    root,path=_target(manager,args,True)
    with manager.mutation_lock(root):
        old,existed=_existing(path); expected=args.get('expected_sha256')
        if existed and not expected: raise WorkspaceError('expected_sha256 is required for an existing file')
        if expected and expected!=sha256_bytes(old): raise WorkspaceError('file changed since it was read')
        content=args['content']; new=content.encode('utf-8')
        if len(new)>MAX_READ_BYTES: raise WorkspaceError('new content exceeds write size limit')
        diff=_preview(path.relative_to(root),old.decode('utf-8'),content)
        if args.get('apply',False): _apply(path,new,expected,existed,path.stat().st_mode if existed else 0o644)
    return {'workspace':str(root),'path':path.relative_to(root).as_posix(),'applied':bool(args.get('apply',False)),'before_sha256':sha256_bytes(old) if existed else None,'after_sha256':sha256_bytes(new),'diff':diff}


def file_patch(manager,args):
    root,path=_target(manager,args)
    with manager.mutation_lock(root):
        old_bytes,_=_existing(path); expected=args['expected_sha256']
        if expected!=sha256_bytes(old_bytes): raise WorkspaceError('file changed since it was read')
        old=old_bytes.decode('utf-8'); needle=args['old_text']; count=old.count(needle)
        if count!=1: raise WorkspaceError(f'old_text must match exactly once; found {count}')
        new=old.replace(needle,args['new_text'],1); diff=_preview(path.relative_to(root),old,new)
        if args.get('apply',False): _apply(path,new.encode(),expected,True,path.stat().st_mode)
    return {'workspace':str(root),'path':path.relative_to(root).as_posix(),'applied':bool(args.get('apply',False)),'before_sha256':expected,'after_sha256':sha256_bytes(new.encode()),'diff':diff}


def _sandbox(manager,args,test=False):
    root=manager.select(args.get('workspace')); command=args['command'].strip()
    if not command or BLOCKED_COMMANDS.search(command): raise WorkspaceError('command is empty or blocked by policy')
    timeout=min(args.get('timeout_seconds',120),300); name='thor-work-'+uuid.uuid4().hex[:12]
    mount=f'{root}:/workspace:{"ro" if test else "rw"}'; cmd=['docker','run','--rm','--name',name,'--network','none','--read-only','--tmpfs','/tmp:rw,nosuid,nodev,size=512m','--memory','2g','--memory-swap','2g','--cpus','4','--pids-limit','128','--cap-drop','ALL','--security-opt','no-new-privileges','--user',f'{os.getuid()}:{os.getgid()}','-e','PYTHONDONTWRITEBYTECODE=1','-v',mount,'-w','/workspace','--entrypoint','/bin/bash',SANDBOX_IMAGE,'-lc',command]
    try: result=subprocess.run(cmd,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:
        try: subprocess.run(['docker','rm','-f',name],capture_output=True,timeout=10)
        except (OSError,subprocess.SubprocessError): pass
        return {'return_code':124,'stdout':'','stderr':f'timed out after {timeout} seconds','test':test}
    return {'return_code':result.returncode,'stdout':result.stdout[-20000:],'stderr':result.stderr[-10000:],'test':test,'sandbox':{'network':'disabled','memory_mb':2048,'cpus':4,'pids':128}}


def shell_execute(manager,args): return _sandbox(manager,args,False)
def test_run(manager,args): return _sandbox(manager,args,True)


def _index_hash(manager,root): return manager._git(root,'write-tree',allowed_codes=(0,)).stdout.strip()


def git_stage(manager,args):
    root=manager.select(args.get('workspace')); files=args['files']
    with manager.mutation_lock(root):
        paths=[]
        for item in files:
            _,path=manager.resolve(item['path'],'file',args.get('workspace'))
            if manager.protected(path,root) or manager.ignored(path,root): raise WorkspaceError('cannot stage protected or ignored path')
            if sha256_bytes(path.read_bytes())!=item['sha256']: raise WorkspaceError(f'file changed before staging: {item["path"]}')
            paths.append(path.relative_to(root).as_posix())
        manager._git(root,'add','--',*paths,allowed_codes=(0,))
        return {'workspace':str(root),'paths':paths,'index_hash':_index_hash(manager,root)}


def git_commit(manager,args):
    root=manager.select(args.get('workspace')); message=args['message'].strip()
    if not message or len(message)>200: raise WorkspaceError('commit message must contain 1 to 200 characters')
    with manager.mutation_lock(root):
        if _index_hash(manager,root)!=args['expected_index_hash']: raise WorkspaceError('staged changes changed after approval')
        names=manager._git(root,'diff','--cached','--name-only','-z',allowed_codes=(0,)).stdout.split('\0')
        names=[name for name in names if name]
        if not names: raise WorkspaceError('nothing staged to commit')
        if any(manager.protected(root/name,root) for name in names): raise WorkspaceError('protected path is staged')
        result=manager._git(root,'commit','-m',message,timeout=60,allowed_codes=(0,))
        revision=manager._git(root,'rev-parse','--short','HEAD',allowed_codes=(0,)).stdout.strip()
    return {'workspace':str(root),'commit':revision,'output':result.stdout[-10000:]}
