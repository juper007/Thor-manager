import inspect
import unittest
from pathlib import Path
from unittest import mock

import agent_tools
import server
from agent import models
from agent.runtime import AgentRuntime
from tools import calculation,python as python_tool,system,web


class ModuleBoundaryTests(unittest.TestCase):
    def test_tool_facade_delegates_to_feature_modules(self):
        self.assertIs(agent_tools.calculator,calculation.calculator)
        self.assertIs(agent_tools.current_time,calculation.current_time)
        self.assertIs(agent_tools.python_execute,python_tool.python_execute)
        self.assertIs(agent_tools.system_status,system.system_status)
        self.assertIs(agent_tools.web_search,web.web_search)
        self.assertIs(agent_tools.read_webpage,web.read_webpage)

    def test_server_delegates_model_call(self):
        with mock.patch.object(models,'edge_chat',return_value='model-result') as call:
            result=server.edge_chat([{'role':'user','content':'hello'}],max_tokens=123)
        self.assertEqual(result,'model-result')
        call.assert_called_once_with([{'role':'user','content':'hello'}],max_tokens=123)

    def test_agent_loop_is_not_implemented_in_server(self):
        self.assertIsInstance(server.runtime,AgentRuntime)
        self.assertNotIn('for _ in range(3)',inspect.getsource(server))
        self.assertIn('for _ in range(3)',inspect.getsource(AgentRuntime))
        self.assertNotIn('import agent_tools',inspect.getsource(__import__('agent.runtime',fromlist=['AgentRuntime'])))

    def test_service_environment_file_matches_documented_example(self):
        root=Path(__file__).resolve().parents[1]
        unit=(root/'thor-monitor.service').read_text(encoding='utf-8')
        self.assertIn('EnvironmentFile=/home/juper007/thor-monitor/thor-monitor.env',unit)
        self.assertTrue((root/'thor-monitor.env.example').is_file())


if __name__=='__main__':
    unittest.main()
