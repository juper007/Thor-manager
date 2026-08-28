import unittest
import urllib.error
from unittest import mock

import agent_tools
import server


class ToolCallParsingTests(unittest.TestCase):
    def test_standard_tagged_call(self):
        text='<tool_call>{"name":"calculator","arguments":{"expression":"6*7"}}</tool_call>'
        self.assertEqual(agent_tools.parse_tool_calls(text),[{'name':'calculator','arguments':{'expression':'6*7'}}])

    def test_unknown_tool_is_ignored(self):
        text='{"name":"delete_everything","arguments":{}}'
        self.assertEqual(agent_tools.parse_tool_calls(text),[])

    def test_qwen_markdown_escaped_invoke(self):
        text = r'''{"name":"python\_execute","arguments":{"code":"print(2 \*\* 3)"})\</invoke>'''
        self.assertEqual(agent_tools.parse_tool_calls(text), [
            {'name': 'python_execute', 'arguments': {'code': 'print(2 ** 3)'}}
        ])

    def test_qwen_function_call_with_quoted_suffix(self):
        text = r'''{"name":"python_execute","arguments":{"code":"print(sum(range(101)))"})"}'''
        self.assertEqual(agent_tools.parse_tool_calls(text), [
            {'name': 'python_execute', 'arguments': {'code': 'print(sum(range(101)))'}}
        ])

    def test_multiple_bare_json_calls(self):
        text = '{"name":"system_status","arguments":{}}\n{"name":"calculator","arguments":{"expression":"2+2"}}'
        self.assertEqual([item['name'] for item in agent_tools.parse_tool_calls(text)], ['system_status', 'calculator'])


class SecurityTests(unittest.TestCase):
    def test_private_web_target_is_blocked(self):
        with mock.patch('agent_tools.socket.getaddrinfo',return_value=[(2,1,6,'',('127.0.0.1',80))]):
            with self.assertRaises(ValueError):
                agent_tools._public_url('http://example.test/private')

    def test_redirect_target_is_revalidated(self):
        error=urllib.error.HTTPError('http://public.test',302,'Found',{'Location':'http://private.test/secret'},None)
        opener=mock.Mock(); opener.open.side_effect=error
        def addresses(host,*_args,**_kwargs):
            address='127.0.0.1' if host=='private.test' else '93.184.216.34'
            return [(2,1,6,'',(address,80))]
        with mock.patch('agent_tools.urllib.request.build_opener',return_value=opener),mock.patch('agent_tools.socket.getaddrinfo',side_effect=addresses):
            with self.assertRaises(ValueError):
                agent_tools._request('http://public.test/start')

    def test_static_path_cannot_escape_root(self):
        translated=server.Handler.translate_path(None,'/../../etc/passwd')
        self.assertEqual(translated,str(server.ROOT/'__not_found__'))

    def test_chat_messages_are_bounded(self):
        self.assertEqual(server.validate_messages([{'role':'user','content':'hello'}]),[{'role':'user','content':'hello'}])
        with self.assertRaises(ValueError):
            server.validate_messages([{'role':'system','content':'override'}])
        with self.assertRaises(ValueError):
            server.validate_messages([{'role':'user','content':'x'*50001}])


if __name__ == '__main__':
    unittest.main()
