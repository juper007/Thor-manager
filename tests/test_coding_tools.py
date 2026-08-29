import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tools.coding import file_patch,file_write,git_commit,sha256_bytes,shell_execute,test_run
from tools.workspace import WorkspaceError,WorkspaceManager


class CodingToolTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)/'repo'; self.root.mkdir()
        self.path=self.root/'app.py'; self.path.write_text('value = 1\n',encoding='utf-8')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        subprocess.run(['git','-C',str(self.root),'config','user.email','test@example.com'],check=True)
        subprocess.run(['git','-C',str(self.root),'config','user.name','Test'],check=True)
        subprocess.run(['git','-C',str(self.root),'add','app.py'],check=True)
        subprocess.run(['git','-C',str(self.root),'commit','-qm','initial'],check=True)
        self.manager=WorkspaceManager([self.root]); self.digest=sha256_bytes(self.path.read_bytes())

    def tearDown(self): self.temp.cleanup()

    def test_patch_preview_then_atomic_apply(self):
        args={'path':'app.py','expected_sha256':self.digest,'old_text':'value = 1','new_text':'value = 2'}
        preview=file_patch(self.manager,args); self.assertFalse(preview['applied']); self.assertIn('+value = 2',preview['diff'])
        self.assertEqual(self.path.read_text(),'value = 1\n')
        applied=file_patch(self.manager,{**args,'apply':True}); self.assertTrue(applied['applied']); self.assertEqual(self.path.read_text(),'value = 2\n')

    def test_patch_rejects_stale_hash_and_ambiguous_match(self):
        with self.assertRaises(WorkspaceError): file_patch(self.manager,{'path':'app.py','expected_sha256':'0'*64,'old_text':'value','new_text':'x','apply':True})
        self.path.write_text('value value\n')
        digest=sha256_bytes(self.path.read_bytes())
        with self.assertRaises(WorkspaceError): file_patch(self.manager,{'path':'app.py','expected_sha256':digest,'old_text':'value','new_text':'x','apply':True})

    def test_write_requires_hash_for_existing_and_can_create(self):
        with self.assertRaises(WorkspaceError): file_write(self.manager,{'path':'app.py','content':'changed','apply':True})
        result=file_write(self.manager,{'path':'new.txt','content':'new\n','apply':True})
        self.assertTrue(result['applied']); self.assertEqual((self.root/'new.txt').read_text(),'new\n')
        with self.assertRaises(WorkspaceError): file_write(self.manager,{'path':'../escape.txt','content':'x','apply':True})

    def test_shell_blocks_dangerous_commands_and_uses_limits(self):
        with self.assertRaises(WorkspaceError): shell_execute(self.manager,{'command':'rm -rf /workspace'})
        completed=subprocess.CompletedProcess([],0,'ok\n','')
        with mock.patch('tools.coding.subprocess.run',return_value=completed) as run:
            result=test_run(self.manager,{'command':'python -m unittest','timeout_seconds':30})
        command=run.call_args.args[0]
        self.assertEqual(result['return_code'],0); self.assertIn('--network',command); self.assertIn('none',command)
        self.assertIn('--pids-limit',command); self.assertIn(':rw',command[command.index('-v')+1])

    def test_git_commit_uses_only_staged_changes_and_does_not_push(self):
        self.path.write_text('value = 3\n'); subprocess.run(['git','-C',str(self.root),'add','app.py'],check=True)
        result=git_commit(self.manager,{'message':'update value'})
        self.assertTrue(result['commit']); self.assertNotIn('push',result['output'].lower())
        self.assertEqual(subprocess.run(['git','-C',str(self.root),'log','-1','--pretty=%s'],capture_output=True,text=True).stdout.strip(),'update value')


if __name__=='__main__': unittest.main()
