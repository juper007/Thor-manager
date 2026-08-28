import unittest

import agent_tools


class ToolCallParsingTests(unittest.TestCase):
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


if __name__ == '__main__':
    unittest.main()
