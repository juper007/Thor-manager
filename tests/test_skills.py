import tempfile
import unittest
from pathlib import Path

from agent.runtime import AgentRuntime
from agent.skills import SkillCatalog,SkillPrompt,parse_skill
from agent_tools import DEFAULT_REGISTRY,load_skill_instructions,parse_tool_calls
from tools.base import ToolSpec
from tools.registry import ToolRegistry


ROOT=Path(__file__).resolve().parents[1]
ALL_SKILLS={'code-interpreter','codebase-analysis','local-time','safe-calculator','thor-system-status','web-research','code-review','debugging','test-and-fix','git-workflow','docker-deployment','systemd-service','jetson-optimization','mcp-integration'}
KNOWN_TOOLS={item['name']:item['risk_level'] for item in DEFAULT_REGISTRY.model_catalog()}


class SkillCatalogTests(unittest.TestCase):
    def test_all_skills_are_discovered_and_fully_specified(self):
        catalog=SkillCatalog.discover(ROOT,KNOWN_TOOLS)
        found={skill.name:skill for skill in catalog.skills}
        self.assertEqual(set(found),ALL_SKILLS)
        for name in ALL_SKILLS:
            with self.subTest(skill=name):
                skill=found[name]
                self.assertTrue(skill.allowed_tools)
                self.assertIn(skill.max_risk,{'read','safe_write','elevated','destructive'})
                self.assertTrue(skill.triggers and skill.preflight and skill.postflight and skill.completion and skill.failure)

    def test_request_selects_only_matching_extended_skills(self):
        prompt=load_skill_instructions(ROOT,[{'role':'user','content':'Docker 배포 문제를 디버깅해줘'}])
        self.assertIn('[docker-deployment]',prompt)
        self.assertIn('[debugging]',prompt)
        self.assertNotIn('[git-workflow]',prompt)
        self.assertIn('Allowed tools:',prompt)
        self.assertIn('Maximum risk:',prompt)
        self.assertEqual(prompt.allowed_tools,{'file_list','file_patch','file_read','file_search','git_diff','git_status','shell_execute','system_status','test_run','workspace_open'})

    def test_only_latest_user_request_selects_skills(self):
        prompt=load_skill_instructions(ROOT,[{'role':'user','content':'git commit 해줘'},{'role':'assistant','content':'done'},{'role':'user','content':'코드리뷰 해줘'}])
        self.assertIn('[code-review]',prompt)
        self.assertNotIn('[git-workflow]',prompt)
        self.assertNotIn('- git_commit ',prompt)
        self.assertIn('- file_read ',prompt)

    def test_original_skills_use_extended_metadata_and_selection(self):
        skill=parse_skill(ROOT/'skills'/'safe-calculator'/'SKILL.md',KNOWN_TOOLS)
        self.assertEqual(skill.name,'safe-calculator')
        self.assertEqual(skill.allowed_tools,('calculator',))
        prompt=load_skill_instructions(ROOT,[{'role':'user','content':'현재 시간을 알려줘'}])
        self.assertIn('[local-time]',prompt)
        self.assertNotIn('[safe-calculator]',prompt)
        self.assertEqual(prompt.allowed_tools,{'current_time'})

    def test_ascii_triggers_require_word_boundaries(self):
        prompt=load_skill_instructions(ROOT,[{'role':'user','content':'Research digital image formats'}])
        self.assertNotIn('[git-workflow]',prompt)
        expected={name for name,risk in KNOWN_TOOLS.items() if risk=='read'}
        self.assertEqual(prompt.allowed_tools,expected)
        self.assertIn('- web_search ',prompt)
        self.assertNotIn('- git_commit ',prompt)
        prompt=load_skill_instructions(ROOT,[{'role':'user','content':'Run git status'}])
        self.assertIn('[git-workflow]',prompt)

    def test_invalid_extended_metadata_and_unknown_tools_fail_closed(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'broken'; path.mkdir(); skill_file=path/'SKILL.md'
            skill_file.write_text('---\nname: broken\ndescription: bad\nallowed-tools: ["missing"]\nmax-risk: read\ntriggers: ["bad"]\npreflight: ["check"]\npostflight: ["check"]\ncompletion: ["done"]\nfailure: ["stop"]\n---\nBody',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'unknown allowed tools'): parse_skill(skill_file,KNOWN_TOOLS)

    def test_tool_risk_cannot_exceed_declared_maximum(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'risky'; path.mkdir(); skill_file=path/'SKILL.md'
            skill_file.write_text('---\nname: risky\ndescription: bad risk\nallowed-tools: ["git_commit"]\nmax-risk: read\ntriggers: ["risk"]\npreflight: ["check"]\npostflight: ["check"]\ncompletion: ["done"]\nfailure: ["stop"]\n---\nBody',encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'tools exceed max-risk'): parse_skill(skill_file,KNOWN_TOOLS)

    def test_runtime_blocks_tools_outside_active_skill_allowlist(self):
        calls=[]; registry=ToolRegistry()
        registry.register(ToolSpec('calculator','test',{'type':'object','properties':{},'additionalProperties':False},lambda _:calls.append(True)))
        replies=iter(['<tool_call>{"name":"calculator","arguments":{}}</tool_call>','blocked safely'])
        loader=lambda root,messages:SkillPrompt('review policy',{'file_read'})
        runtime=AgentRuntime(ROOT,lambda _:next(replies),registry,parse_tool_calls,loader,lambda text:text)
        _,_,events,_=runtime.run_chat([{'role':'user','content':'review this'}],'skill-policy')
        self.assertFalse(calls)
        self.assertEqual(events[0]['error_code'],'skill_tool_not_allowed')

    def test_unmatched_request_runtime_blocks_non_read_tool(self):
        prompt=load_skill_instructions(ROOT,[{'role':'user','content':'도와줘'}])
        calls=[]; registry=ToolRegistry()
        registry.register(ToolSpec('git_commit','test',{'type':'object','properties':{},'additionalProperties':False},lambda _:calls.append(True)))
        replies=iter(['<tool_call>{"name":"git_commit","arguments":{}}</tool_call>','blocked safely'])
        runtime=AgentRuntime(ROOT,lambda _:next(replies),registry,parse_tool_calls,lambda root,messages:prompt,lambda text:text)
        _,_,events,_=runtime.run_chat([{'role':'user','content':'도와줘'}],'default-skill-policy')
        self.assertFalse(calls)
        self.assertEqual(events[0]['error_code'],'skill_tool_not_allowed')


if __name__=='__main__': unittest.main()
