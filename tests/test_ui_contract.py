import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script=(ROOT/'ai-workspace.js').read_text(encoding='utf-8')
        cls.page=(ROOT/'ai-workspace.html').read_text(encoding='utf-8')
        cls.agent_css=(ROOT/'ai-agent.css').read_text(encoding='utf-8')

    def test_markdown_renderer_contract(self):
        required=(
            'function markdownToHtml',
            "replaceAll('&','&amp;')",
            '<pre><code>',
            '<blockquote>',
            '<div class="md-table-wrap"><table>',
            'target="_blank" rel="noopener noreferrer"',
        )
        for marker in required:
            with self.subTest(marker=marker): self.assertIn(marker,self.script)

    def test_chat_uses_rendered_markdown(self):
        self.assertIn('ai.innerHTML=markdownToHtml(full)',self.script)
        self.assertIn("d.textContent=text",self.script)
        self.assertIn('run_id:runId',self.script)
        self.assertIn('stream:true',self.script)
        self.assertIn("j.type==='delta'",self.script)
        self.assertIn("j.type==='final'",self.script)
        self.assertIn('requestAnimationFrame',self.script)
        self.assertIn('cancelAnimationFrame',self.script)

    def test_agent_run_controls_contract(self):
        for marker in ('id="runPanel"','id="runState"','id="runTools"','id="cancelRun"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        self.assertIn("/api/chat/runs/",self.script)
        self.assertIn("/api/chat/cancel",self.script)
        self.assertIn('function renderRunTools',self.script)
        self.assertIn('tool.error_code',self.script)
        self.assertIn('if(activeRunId!==runId)return',self.script)
        self.assertIn('resolvedApprovalIds.clear()',self.script)
        self.assertIn("setTimeout(pollRun,800)",self.script)
        self.assertIn('state-completed',self.agent_css)
        self.assertIn('state-failed',self.agent_css)
        self.assertIn('state-cancelled',self.agent_css)
        self.assertGreater(self.agent_css.index('.run-panel.state-failed'),self.agent_css.index('.run-panel.terminal .run-dot'))
        self.assertGreater(self.agent_css.index('.run-panel.state-cancelled'),self.agent_css.index('.run-panel.terminal .run-dot'))
        self.assertIn('function clearSession',self.script)
        self.assertIn("$('clearChat').disabled=true",self.script)
        self.assertIn("$('planPanel').hidden=true",self.script)
        self.assertIn("$('runTools').innerHTML=''",self.script)

    def test_agent_modes_and_plan_panel_contract(self):
        for marker in ('data-run-mode="ask"','data-run-mode="plan"','data-run-mode="agent"','id="planPanel"','id="planSteps"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        self.assertIn('mode:activeRunMode',self.script)
        self.assertIn('function renderRunPlan',self.script)
        self.assertIn("await stopRunMonitor(finalState)",self.script)
        self.assertIn("fetch(API+'/api/chat/runs/'",self.script)
        self.assertIn("event.type==='plan.created'",self.script)
        self.assertIn("event.type==='plan.step'",self.script)

    def test_file_diff_viewer_contract(self):
        for marker in ('id="diffPanel"','id="diffCount"','id="diffFiles"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        for marker in ('function splitDiffFiles','function renderRunDiffs',"event.type==='tool.completed'",'diff_truncated'):
            with self.subTest(marker=marker): self.assertIn(marker,self.script)
        for marker in ('.diff-file code .added','.diff-file code .removed','.diff-file code .hunk'):
            with self.subTest(marker=marker): self.assertIn(marker,self.agent_css)
        self.assertIn('/ai-agent.css?v=10',self.page)
        self.assertIn('/ai-workspace.js?v=11',self.page)

    def test_agent_approval_dialog_contract(self):
        for marker in ('id="approvalModal"','id="approvalScope"','id="allowApproval"','id="denyApproval"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        self.assertIn("/api/chat/approvals?run_id=",self.script)
        self.assertIn("decideApproval('allow')",self.script)
        self.assertIn("decideApproval('deny')",self.script)
        self.assertIn('resolvedApprovalIds.has',self.script)
        self.assertIn('resolvedApprovalIds.add',self.script)

    def test_image_studio_contract(self):
        for marker in ('data-image-mode="generate"','data-image-mode="edit"','id="sourceImage"','id="generateImage"','id="imageOutput"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        self.assertIn("/api/images/generations",self.script)
        self.assertIn("/api/images/edits",self.script)


if __name__=='__main__':
    unittest.main()
