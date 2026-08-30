import unittest
import threading

import agent_tools
from tools.base import RiskLevel,ToolSpec
from tools.builtin import build_registry
from tools.registry import ToolRegistry


SCHEMA={
    'type':'object',
    'properties':{'text':{'type':'string','minLength':1,'maxLength':5}},
    'required':['text'],
    'additionalProperties':False,
}


class ToolRegistryTests(unittest.TestCase):
    def test_duplicate_registration_is_rejected(self):
        registry=ToolRegistry(); spec=ToolSpec('echo','Echo text',SCHEMA,lambda args:args['text'])
        registry.register(spec)
        with self.assertRaisesRegex(ValueError,'duplicate tool'):
            registry.register(spec)

    def test_unknown_tool_returns_standard_error(self):
        result=ToolRegistry().execute('missing',{})
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['error_code'],'validation_error')
        self.assertIn('unknown tool',result['error'])
        self.assertIsNone(result['result'])

    def test_required_argument_is_validated(self):
        registry=ToolRegistry(); registry.register(ToolSpec('echo','Echo text',SCHEMA,lambda args:args['text']))
        result=registry.execute('echo',{})
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['error_code'],'validation_error')
        self.assertIn('required property',result['error'])

    def test_argument_type_and_unknown_properties_are_validated(self):
        registry=ToolRegistry(); registry.register(ToolSpec('echo','Echo text',SCHEMA,lambda args:args['text']))
        self.assertIn('expected string',registry.execute('echo',{'text':3})['error'])
        self.assertIn('unknown properties',registry.execute('echo',{'text':'ok','extra':1})['error'])

    def test_handler_exception_uses_standard_result(self):
        registry=ToolRegistry()
        def fail(_): raise RuntimeError('handler failed')
        registry.register(ToolSpec('failure','Always fails',{'type':'object','properties':{},'additionalProperties':False},fail))
        result=registry.execute('failure',{})
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['error'],'handler failed')
        self.assertEqual(result['error_code'],'execution_error')
        self.assertIn('seconds',result)

    def test_timeout_uses_standard_result(self):
        release=threading.Event(); registry=ToolRegistry()
        registry.register(ToolSpec('slow','Slow tool',{'type':'object','properties':{},'additionalProperties':False},lambda _:release.wait(.2),timeout_seconds=.01))
        result=registry.execute('slow',{})
        release.set()
        self.assertEqual(result['status'],'error')
        self.assertEqual(result['error_code'],'timeout')
        self.assertIn('timed out',result['error'])

    def test_output_is_limited(self):
        registry=ToolRegistry(); registry.register(ToolSpec('large','Large output',SCHEMA,lambda _: 'x'*100,output_limit=20))
        result=registry.execute('large',{'text':'ok'})
        self.assertEqual(result['status'],'success')
        self.assertTrue(result['truncated'])
        self.assertTrue(result['result']['truncated'])

    def test_registered_schema_is_immutable_copy(self):
        original={'type':'object','properties':{'text':{'type':'string','maxLength':5}},'required':['text'],'additionalProperties':False}
        spec=ToolSpec('echo','Echo',original,lambda args:args['text'])
        original['properties']['text']['maxLength']=100
        with self.assertRaises(TypeError):
            spec.input_schema['properties']['text']['maxLength']=100
        registry=ToolRegistry(); registry.register(spec)
        self.assertEqual(registry.execute('echo',{'text':'123456'})['error_code'],'validation_error')

    def test_builtin_registry_catalog(self):
        registry=build_registry()
        names=set(registry.names()); catalog=registry.model_catalog()
        self.assertEqual(names,{'web_search','read_webpage','calculator','current_time','system_status','python_execute','workspace_open','file_list','file_read','file_search','git_status','git_diff','file_write','file_patch','shell_execute','test_run','git_stage','git_commit'})
        python_entry=next(item for item in catalog if item['name']=='python_execute')
        self.assertEqual(python_entry['risk_level'],RiskLevel.ELEVATED.value)
        for name in ('workspace_open','file_list','file_read','file_search','git_status','git_diff'):
            self.assertIn(name,names)
            self.assertEqual(next(item for item in catalog if item['name']==name)['risk_level'],RiskLevel.READ.value)
        expected={'file_write':RiskLevel.SAFE_WRITE.value,'file_patch':RiskLevel.SAFE_WRITE.value,'test_run':RiskLevel.ELEVATED.value,'shell_execute':RiskLevel.DESTRUCTIVE.value,'git_stage':RiskLevel.ELEVATED.value,'git_commit':RiskLevel.DESTRUCTIVE.value}
        for name,risk in expected.items(): self.assertEqual(next(item for item in catalog if item['name']==name)['risk_level'],risk)
        self.assertIn('code',python_entry['input_schema']['required'])

    def test_facade_executes_through_default_registry(self):
        result=agent_tools.execute_tool({'name':'calculator','arguments':{'expression':'6*7'}})
        self.assertEqual(result['status'],'success')
        self.assertEqual(result['result']['result'],42)

    def test_generated_prompt_lists_every_tool(self):
        for name in agent_tools.DEFAULT_REGISTRY.names():
            self.assertIn(f'- {name} ',agent_tools.TOOL_GUIDE)

    def test_prompt_catalog_can_be_limited_to_active_policy(self):
        prompt=agent_tools.DEFAULT_REGISTRY.prompt_catalog({'file_read','git_status'})
        self.assertIn('- file_read ',prompt)
        self.assertIn('- git_status ',prompt)
        self.assertNotIn('- file_write ',prompt)
        self.assertNotIn('- git_commit ',prompt)


if __name__=='__main__':
    unittest.main()
