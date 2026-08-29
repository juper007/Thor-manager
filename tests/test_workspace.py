import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.workspace import MAX_READ_BYTES,WorkspaceError,WorkspaceManager,file_list,file_read,file_search,git_diff,git_status,workspace_open


class WorkspaceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.root=Path(self.temp.name)/'project'; self.root.mkdir()
        (self.root/'src').mkdir(); (self.root/'src'/'app.py').write_text('def answer():\n    return 42\n',encoding='utf-8')
        (self.root/'README.md').write_text('# Demo\n',encoding='utf-8')
        (self.root/'AGENTS.md').write_text('Read-only project guidance.\n',encoding='utf-8')
        (self.root/'.secret').write_text('hidden',encoding='utf-8')
        subprocess.run(['git','init','-q',str(self.root)],check=True)
        (self.root/'.gitignore').write_text('ignored.txt\n',encoding='utf-8'); (self.root/'ignored.txt').write_text('ignore me',encoding='utf-8')
        (self.root/'local.env').write_text('PASSWORD=secret',encoding='utf-8')
        (self.root/'data').mkdir(); (self.root/'data'/'sessions.db').write_bytes(b'secret-db')
        subprocess.run(['git','-C',str(self.root),'add','.'],check=True)
        self.manager=WorkspaceManager([self.root])

    def tearDown(self): self.temp.cleanup()

    def test_workspace_open_lists_registration_and_instructions(self):
        result=workspace_open(self.manager,{})
        self.assertEqual(result['root'],str(self.root.resolve()))
        self.assertEqual(result['instructions'][0]['path'],'AGENTS.md')
        self.assertIn('Read-only',result['instructions'][0]['content'])

    def test_unregistered_workspace_is_rejected(self):
        with self.assertRaises(WorkspaceError): workspace_open(self.manager,{'path':str(self.root.parent)})

    def test_file_list_respects_hidden_and_gitignore(self):
        result=file_list(self.manager,{'max_depth':2})
        paths=[item['path'] for item in result['entries']]
        self.assertIn('src/app.py',paths); self.assertNotIn('.secret',paths); self.assertNotIn('ignored.txt',paths); self.assertNotIn('.git',paths)
        self.assertNotIn('local.env',paths); self.assertNotIn('data',paths)

    def test_file_read_is_bounded_and_rejects_binary(self):
        result=file_read(self.manager,{'path':'src/app.py','start_line':2,'line_count':1})
        self.assertEqual(result['content'],'    return 42'); self.assertEqual(result['start_line'],2)
        (self.root/'binary.bin').write_bytes(b'a\x00b')
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'binary.bin'})
        (self.root/'large.txt').write_bytes(b'x'*(MAX_READ_BYTES+1))
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'large.txt'})
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'local.env'})
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'data/sessions.db'})
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'.secret'})

    def test_parent_traversal_is_rejected(self):
        outside=self.root.parent/'outside.txt'; outside.write_text('private',encoding='utf-8')
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'../outside.txt'})

    @unittest.skipUnless(hasattr(os,'symlink'),'symlinks unavailable')
    def test_symlink_escape_is_rejected(self):
        outside=self.root.parent/'outside.txt'; outside.write_text('private',encoding='utf-8')
        link=self.root/'escape.txt'
        try: link.symlink_to(outside)
        except OSError: self.skipTest('symlink creation is not permitted')
        with self.assertRaises(WorkspaceError): file_read(self.manager,{'path':'escape.txt'})

    def test_file_search_returns_relative_line_evidence(self):
        result=file_search(self.manager,{'query':'return 42','fixed_strings':True})
        self.assertEqual(result['matches'][0]['path'],'src/app.py'); self.assertEqual(result['matches'][0]['line'],2)

    def test_file_search_falls_back_without_ripgrep(self):
        original=shutil.which
        try:
            shutil.which=lambda _:None
            result=file_search(self.manager,{'query':'return 42','fixed_strings':True})
        finally: shutil.which=original
        self.assertEqual(result['engine'],'python-fallback'); self.assertEqual(result['matches'][0]['line'],2)

    def test_search_does_not_expose_protected_or_ignored_content(self):
        result=file_search(self.manager,{'query':'secret','fixed_strings':True})
        self.assertEqual(result['matches'],[])

    def test_git_status_and_diff_are_read_only(self):
        before={path.relative_to(self.root).as_posix():path.stat().st_mtime_ns for path in self.root.rglob('*') if path.is_file() and '.git' not in path.parts}
        (self.root/'src'/'app.py').write_text('def answer():\n    return 43\n',encoding='utf-8')
        status=git_status(self.manager,{}); diff=git_diff(self.manager,{'path':'src/app.py'})
        self.assertFalse(status['clean']); self.assertIn('return 43',diff['diff'])
        after={path.relative_to(self.root).as_posix():path.stat().st_mtime_ns for path in self.root.rglob('*') if path.is_file() and '.git' not in path.parts}
        changed={key for key in after if before.get(key)!=after[key]}
        self.assertEqual(changed,{'src/app.py'})


if __name__=='__main__': unittest.main()
