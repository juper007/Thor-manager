import subprocess
import shutil
import unittest
from unittest import mock

import agent_tools
import server
from tools import python as python_tool


SANDBOX_IMAGE='nvcr.io/nvidia/pytorch:26.05-py3'


def docker_sandbox_available():
    if not shutil.which('docker'): return False
    try:
        return subprocess.run(['docker','image','inspect',SANDBOX_IMAGE],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=10).returncode==0
    except (OSError,subprocess.SubprocessError):
        return False


class AgentRuntimeBaselineTests(unittest.TestCase):
    def test_agent_executes_tool_once_then_answers(self):
        replies=['<tool_call>{"name":"calculator","arguments":{"expression":"2+2"}}</tool_call>','결과는 4입니다.']
        event={'name':'calculator','arguments':{'expression':'2+2'},'result':{'result':4},'error':None,'seconds':0.01}
        with mock.patch.object(server,'edge_chat',side_effect=replies),mock.patch.object(server.runtime.registry,'execute',return_value=event):
            answer,events,_=server.agent_chat([{'role':'user','content':'2+2'}])
        self.assertEqual(answer,'결과는 4입니다.')
        self.assertEqual(len(events),1)

    def test_python_execute_success_and_error_contract(self):
        success=subprocess.CompletedProcess([],0,'4\n','')
        failure=subprocess.CompletedProcess([],1,'','ValueError: bad\n')
        with mock.patch.object(python_tool.subprocess,'run',return_value=success):
            result=agent_tools.python_execute({'code':'print(2+2)'})
        self.assertEqual(result['return_code'],0)
        self.assertEqual(result['stdout'],'4\n')
        with mock.patch.object(python_tool.subprocess,'run',return_value=failure):
            result=agent_tools.python_execute({'code':'raise ValueError("bad")'})
        self.assertEqual(result['return_code'],1)
        self.assertIn('ValueError',result['stderr'])

    def test_python_execute_timeout_contract(self):
        timeout=subprocess.TimeoutExpired(['docker'],30)
        with mock.patch.object(python_tool.subprocess,'run',side_effect=[timeout,subprocess.CompletedProcess([],0,'','')]):
            result=agent_tools.python_execute({'code':'while True: pass'})
        self.assertEqual(result['return_code'],124)
        self.assertIn('timed out',result['stderr'])

    def test_python_timeout_survives_cleanup_failure(self):
        timeout=subprocess.TimeoutExpired(['docker'],30)
        cleanup_timeout=subprocess.TimeoutExpired(['docker','rm'],10)
        with mock.patch.object(python_tool.subprocess,'run',side_effect=[timeout,cleanup_timeout]):
            result=agent_tools.python_execute({'code':'while True: pass'})
        self.assertEqual(result['return_code'],124)

    @unittest.skipUnless(docker_sandbox_available(),'Docker sandbox image is not available')
    def test_python_execute_real_sandbox(self):
        result=agent_tools.python_execute({'code':'print(sum(range(101)))'})
        self.assertEqual(result['return_code'],0,result['stderr'])
        self.assertEqual(result['stdout'].strip(),'5050')
        self.assertEqual(result['sandbox']['network'],'disabled')


if __name__=='__main__':
    unittest.main()
