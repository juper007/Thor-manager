import os
import fnmatch
import re
import shutil
import subprocess
import threading
from pathlib import Path


MAX_READ_BYTES=256_000
MAX_INSTRUCTION_BYTES=64_000
INSTRUCTION_NAMES=('AGENTS.md','CLAUDE.md','.github/copilot-instructions.md')
BLOCKED_PARTS={'.git','data','generated','__pycache__','.pytest_cache','.codex-deps','node_modules'}
BLOCKED_NAMES={'thor-password.txt'}
BLOCKED_SUFFIXES=('.env','.db','.db-wal','.db-shm','.pem','.key')


class WorkspaceError(ValueError):
    pass


def _inside(path,root):
    try: path.relative_to(root); return True
    except ValueError: return False


class WorkspaceManager:
    def __init__(self,allowed_roots,default=None):
        roots=[]
        for value in allowed_roots:
            root=Path(value).expanduser().resolve(strict=True)
            if not root.is_dir(): raise WorkspaceError(f'workspace root is not a directory: {root}')
            if root not in roots: roots.append(root)
        if not roots: raise WorkspaceError('at least one workspace root is required')
        selected=Path(default).expanduser().resolve(strict=True) if default else roots[0]
        if selected not in roots: raise WorkspaceError('default workspace is not in allowed roots')
        self.allowed_roots=tuple(roots); self._current=selected; self._lock=threading.RLock()

    @classmethod
    def from_environment(cls,default=None):
        fallback=Path(default or Path.cwd()).resolve()
        configured=os.environ.get('THOR_WORKSPACE_ROOTS','')
        roots=[item for item in configured.split(os.pathsep) if item] if configured else [fallback]
        return cls(roots,fallback if fallback in [Path(item).expanduser().resolve() for item in roots] else roots[0])

    @property
    def current(self):
        with self._lock: return self._current

    def open(self,value=None):
        with self._lock:
            if value in (None,'','.'):
                selected=self._current
            else:
                matches=[root for root in self.allowed_roots if str(root)==value or root.name==value]
                if len(matches)!=1: raise WorkspaceError('workspace is not registered or its name is ambiguous')
                selected=matches[0]
            self._current=selected
        return self.describe(selected)

    def resolve(self,value='.',kind=None):
        root=self.current
        raw=Path(value or '.')
        candidate=(raw if raw.is_absolute() else root/raw).resolve(strict=True)
        if not _inside(candidate,root): raise WorkspaceError('path escapes the active workspace')
        if kind=='file' and not candidate.is_file(): raise WorkspaceError('path is not a file')
        if kind=='dir' and not candidate.is_dir(): raise WorkspaceError('path is not a directory')
        return root,candidate

    def relative(self,path): return path.relative_to(self.current).as_posix() or '.'

    def _git(self,*args,timeout=10):
        try:
            result=subprocess.run(['git','-C',str(self.current),*args],capture_output=True,text=True,timeout=timeout,encoding='utf-8',errors='replace')
        except (OSError,subprocess.TimeoutExpired) as exc: raise WorkspaceError(f'git command failed: {exc}')
        if result.returncode not in (0,1): raise WorkspaceError((result.stderr or 'git command failed').strip())
        return result

    def ignored(self,path):
        relative=path.relative_to(self.current).as_posix()
        if self.protected(path): return True
        try: result=subprocess.run(['git','-C',str(self.current),'check-ignore','--quiet','--',relative],capture_output=True,timeout=3)
        except (OSError,subprocess.TimeoutExpired): return False
        if result.returncode==0: return True
        return self._gitignore_match(relative,path.is_dir())

    def protected(self,path):
        relative=path.relative_to(self.current)
        if any(part.startswith('.') for part in relative.parts): return True
        if any(part in BLOCKED_PARTS for part in relative.parts): return True
        name=relative.name.lower()
        return name in BLOCKED_NAMES or name.endswith(BLOCKED_SUFFIXES)

    def _gitignore_match(self,relative,is_dir=False):
        ignore_file=self.current/'.gitignore'
        try: patterns=ignore_file.read_text(encoding='utf-8').splitlines()
        except OSError: return False
        ignored=False; parts=Path(relative).parts
        for raw in patterns:
            pattern=raw.strip()
            if not pattern or pattern.startswith('#'): continue
            negated=pattern.startswith('!'); pattern=pattern[1:] if negated else pattern
            directory_only=pattern.endswith('/'); pattern=pattern.rstrip('/')
            if not pattern: continue
            if '/' in pattern:
                matched=fnmatch.fnmatch(relative,pattern) or relative.startswith(pattern+'/')
            else:
                matched=any(fnmatch.fnmatch(part,pattern) for part in parts)
            if directory_only and not matched:
                matched=any(fnmatch.fnmatch(part,pattern) for part in parts[:-1] if parts)
            if matched: ignored=not negated
        return ignored

    def instructions(self,root=None):
        root=root or self.current; found=[]; used=0
        for name in INSTRUCTION_NAMES:
            path=root/name
            if not path.is_file(): continue
            resolved=path.resolve()
            if not _inside(resolved,root): continue
            size=resolved.stat().st_size
            if size>MAX_INSTRUCTION_BYTES-used:
                found.append({'path':name,'content':'','error':'instruction file exceeds remaining size limit'}); continue
            try: content=resolved.read_text(encoding='utf-8')
            except (OSError,UnicodeError) as exc: found.append({'path':name,'content':'','error':str(exc)}); continue
            used+=len(content.encode('utf-8')); found.append({'path':name,'content':content})
        return found

    def describe(self,root=None):
        root=root or self.current
        return {'name':root.name,'root':str(root),'registered':[{'name':item.name,'root':str(item)} for item in self.allowed_roots],'instructions':self.instructions(root)}


def _hidden(relative): return any(part.startswith('.') for part in relative.parts)


def workspace_open(manager,arguments): return manager.open(arguments.get('path'))


def file_list(manager,arguments):
    root,start=manager.resolve(arguments.get('path','.'),'dir')
    max_depth=arguments.get('max_depth',2); max_entries=arguments.get('max_entries',200)
    include_hidden=arguments.get('include_hidden',False); entries=[]; truncated=False
    def walk(directory,depth):
        nonlocal truncated
        try: children=sorted(directory.iterdir(),key=lambda item:(not item.is_dir(),item.name.lower()))
        except OSError as exc: raise WorkspaceError(str(exc))
        for child in children:
            relative=child.relative_to(root)
            if (not include_hidden and _hidden(relative)) or manager.ignored(child): continue
            resolved=child.resolve()
            if not _inside(resolved,root): continue
            if len(entries)>=max_entries: truncated=True; return
            is_dir=child.is_dir(); entries.append({'path':relative.as_posix(),'type':'directory' if is_dir else 'file','size':None if is_dir else child.stat().st_size})
            if is_dir and depth<max_depth: walk(child,depth+1)
            if truncated: return
    walk(start,0)
    return {'workspace':str(root),'path':start.relative_to(root).as_posix() or '.','entries':entries,'truncated':truncated}


def file_read(manager,arguments):
    root,path=manager.resolve(arguments['path'],'file'); size=path.stat().st_size
    if manager.ignored(path): raise WorkspaceError('file is hidden, ignored, or protected')
    if size>MAX_READ_BYTES: raise WorkspaceError(f'file exceeds {MAX_READ_BYTES} byte limit')
    data=path.read_bytes()
    if b'\x00' in data: raise WorkspaceError('binary files cannot be read')
    try: text=data.decode('utf-8')
    except UnicodeDecodeError: raise WorkspaceError('file is not valid UTF-8 text')
    lines=text.splitlines(); start=arguments.get('start_line',1); count=arguments.get('line_count',400)
    selected=lines[start-1:start-1+count]
    return {'workspace':str(root),'path':path.relative_to(root).as_posix(),'size':size,'start_line':start,'end_line':start+len(selected)-1 if selected else start-1,'total_lines':len(lines),'content':'\n'.join(selected),'truncated':start-1+len(selected)<len(lines)}


def file_search(manager,arguments):
    root,start=manager.resolve(arguments.get('path','.'),'dir'); query=arguments['query']; max_results=arguments.get('max_results',100)
    if shutil.which('rg') is None: return _python_search(manager,root,start,arguments)
    command=['rg','--line-number','--column','--no-heading','--color','never','--max-count',str(max_results)]
    for excluded in ('.git/**','data/**','generated/**','**/__pycache__/**','**/.pytest_cache/**','**/.codex-deps/**','**/node_modules/**','**/*.env','**/*.db','**/*.db-wal','**/*.db-shm','**/*.pem','**/*.key','**/thor-password.txt'):
        command.extend(['--glob',f'!{excluded}'])
    if arguments.get('fixed_strings'): command.append('--fixed-strings')
    if not arguments.get('case_sensitive'): command.append('--ignore-case')
    if arguments.get('glob'): command.extend(['--glob',arguments['glob']])
    search_path=start.relative_to(root).as_posix() or '.'
    command.extend(['--',query,search_path])
    try: result=subprocess.run(command,cwd=root,capture_output=True,text=True,timeout=15,encoding='utf-8',errors='replace')
    except FileNotFoundError: raise WorkspaceError('rg is not installed')
    except subprocess.TimeoutExpired: raise WorkspaceError('file search timed out')
    if result.returncode not in (0,1): raise WorkspaceError((result.stderr or 'file search failed').strip())
    matches=[]
    for line in result.stdout.splitlines()[:max_results]:
        parts=line.split(':',3)
        if len(parts)!=4: continue
        file_name,line_number,column,text=parts
        path=(root/file_name).resolve()
        if not _inside(path,root) or manager.ignored(path): continue
        matches.append({'path':path.relative_to(root).as_posix(),'line':int(line_number),'column':int(column),'text':text[:1000]})
    return {'workspace':str(root),'query':query,'matches':matches,'truncated':len(result.stdout.splitlines())>max_results}


def _python_search(manager,root,start,arguments):
    query=arguments['query']; max_results=arguments.get('max_results',100); fixed=arguments.get('fixed_strings',False)
    flags=0 if arguments.get('case_sensitive') else re.IGNORECASE
    try: pattern=re.compile(re.escape(query) if fixed else query,flags)
    except re.error as exc: raise WorkspaceError(f'invalid search pattern: {exc}')
    glob=arguments.get('glob'); matches=[]; truncated=False
    for path in sorted(start.rglob('*')):
        relative=path.relative_to(root)
        if not path.is_file() or _hidden(relative) or manager.ignored(path): continue
        if glob and not relative.match(glob): continue
        resolved=path.resolve()
        if not _inside(resolved,root): continue
        try:
            if path.stat().st_size>MAX_READ_BYTES: continue
            data=path.read_bytes()
            if b'\x00' in data: continue
            text=data.decode('utf-8')
        except (OSError,UnicodeError): continue
        for line_number,line in enumerate(text.splitlines(),1):
            match=pattern.search(line)
            if match:
                if len(matches)>=max_results: truncated=True; break
                matches.append({'path':relative.as_posix(),'line':line_number,'column':match.start()+1,'text':line[:1000]})
        if truncated: break
    return {'workspace':str(root),'query':query,'matches':matches,'truncated':truncated,'engine':'python-fallback'}


def git_status(manager,arguments):
    root=manager.current; result=manager._git('status','--short','--branch')
    return {'workspace':str(root),'output':result.stdout,'clean':not any(line and not line.startswith('##') for line in result.stdout.splitlines())}


def git_diff(manager,arguments):
    root=manager.current; command=['diff','--no-ext-diff','--no-color']
    if arguments.get('staged'): command.append('--cached')
    path_value=arguments.get('path')
    if path_value:
        _,path=manager.resolve(path_value)
        command.extend(['--',path.relative_to(root).as_posix()])
    result=manager._git(*command,timeout=15)
    return {'workspace':str(root),'staged':arguments.get('staged',False),'path':path_value,'diff':result.stdout}
