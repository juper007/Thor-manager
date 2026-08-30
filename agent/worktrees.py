"""Git worktree isolation with strict workspace containment."""
import re
import subprocess
from pathlib import Path


SAFE_NAME=re.compile(r'^[A-Za-z0-9._-]{1,80}$')


class WorktreeManager:
    def __init__(self,repository,container_dir=None):
        self.repository=Path(repository).resolve(); self.container=Path(container_dir or self.repository/'.thor-worktrees').resolve()
        self.container.relative_to(self.repository); self.container.mkdir(exist_ok=True)
    def create(self,name,base='HEAD'):
        if not SAFE_NAME.fullmatch(name): raise ValueError('invalid worktree name')
        target=(self.container/name).resolve(); target.relative_to(self.container)
        if target.exists(): raise FileExistsError(target)
        branch='thor/'+name
        subprocess.run(['git','worktree','add','-b',branch,str(target),base],cwd=self.repository,check=True,capture_output=True,text=True,timeout=60)
        return {'name':name,'branch':branch,'path':str(target)}
    def list(self):
        output=subprocess.run(['git','worktree','list','--porcelain'],cwd=self.repository,check=True,capture_output=True,text=True,timeout=20).stdout
        return [line.split(' ',1)[1] for line in output.splitlines() if line.startswith('worktree ')]
    def remove(self,name):
        if not SAFE_NAME.fullmatch(name): raise ValueError('invalid worktree name')
        target=(self.container/name).resolve(); target.relative_to(self.container)
        subprocess.run(['git','worktree','remove',str(target)],cwd=self.repository,check=True,capture_output=True,text=True,timeout=60)
