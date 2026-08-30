import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script=(ROOT/'ai-workspace.js').read_text(encoding='utf-8')
        cls.page=(ROOT/'ai-workspace.html').read_text(encoding='utf-8')
        cls.agent_css=(ROOT/'ai-agent.css').read_text(encoding='utf-8')
        cls.accessibility_css=(ROOT/'ai-accessibility.css').read_text(encoding='utf-8')
        cls.admin_page=(ROOT/'admin.html').read_text(encoding='utf-8')
        cls.admin_script=(ROOT/'admin.js').read_text(encoding='utf-8')

    def test_admin_password_input_is_masked(self):
        self.assertIn('id="passwordDialog"',self.admin_page)
        self.assertIn('id="newPassword" type="password"',self.admin_page)
        self.assertNotIn('prompt(',self.admin_script)

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
        self.assertIn('session_id:sessionId',self.script)
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
        self.assertIn('selectedSessionId=null',self.script)
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
        self.assertIn('질문하거나 필요한 skill 도구 사용을 요청하세요',self.script)

    def test_file_diff_viewer_contract(self):
        for marker in ('id="diffPanel"','id="diffCount"','id="diffFiles"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        for marker in ('function splitDiffFiles','function renderRunDiffs',"event.type==='tool.completed'",'diff_truncated'):
            with self.subTest(marker=marker): self.assertIn(marker,self.script)
        for marker in ('.diff-file code .added','.diff-file code .removed','.diff-file code .hunk'):
            with self.subTest(marker=marker): self.assertIn(marker,self.agent_css)

    def test_test_result_panel_contract(self):
        for marker in ('id="testPanel"','id="testCount"','id="testResults"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        for marker in ('function collectTestRuns','function renderTestResults','return_code===0',"['STDOUT',run.stdout]", "['STDERR',run.stderr]"):
            with self.subTest(marker=marker): self.assertIn(marker,self.script)
        for marker in ('.test-result.passed','.test-result.failed','.test-result.truncated','.test-output.stderr'):
            with self.subTest(marker=marker): self.assertIn(marker,self.agent_css)
        self.assertIn('/ai-agent.css?v=14',self.page)
        self.assertIn('/ai-workspace.js?v=14',self.page)
        self.assertIn('/auth-ui.js?v=1',self.page)
        self.assertIn('id="authUser"',self.page); self.assertIn('id="logout"',self.page)

    def test_session_browser_and_resume_contract(self):
        for marker in ('id="sessionList"','id="refreshSessions"','id="moreSessions"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        for marker in ('function loadSessions','function openSession','function resumeStoredSession',"'/api/chat/sessions?limit=20&offset='","'/resume'"):
            with self.subTest(marker=marker): self.assertIn(marker,self.script)
        self.assertIn('renderStoredMessages',self.script)
        self.assertIn('renderRunDiffs(session.events||[])',self.script)
        self.assertIn('renderTestResults(session.events||[])',self.script)
        self.assertIn('if(!await openSession(runId))return',self.script)
        self.assertIn("history.at(-1)?.role==='assistant'",self.script)
        self.assertIn('function deleteStoredSession',self.script)
        self.assertIn("method:'DELETE'",self.script)
        self.assertIn('deletingSessionIds.has(runId)',self.script)
        self.assertNotIn('if(activeRunId||deletingSessionIds.has(runId)',self.script)
        self.assertIn("catch(error){alert('DELETE ERROR · '+error.message)}",self.script)
        self.assertIn('finally{deletingSessionIds.delete(runId)',self.script)
        self.assertIn('id="clearChat">＋ NEW SESSION</button>',self.page)
        self.assertNotIn('id="messageCount"',self.page)
        self.assertNotIn('ACTIVE SKILLS',self.page)
        self.assertIn('/ai-agent.css?v=14',self.page)

    def test_mobile_workspace_contract(self):
        for marker in ('@media(max-width:720px)','100dvh','overscroll-behavior:contain','max-width:min(78%,75ch)','min-height:44px'):
            with self.subTest(marker=marker): self.assertIn(marker,self.accessibility_css)
        self.assertIn('@media(max-width:420px)',self.accessibility_css)
        self.assertIn('.session-item .session-delete{width:44px;height:44px}',self.accessibility_css)
        self.assertIn('@media(prefers-reduced-motion:reduce)',self.accessibility_css)

    def test_keyboard_and_accessibility_contract(self):
        for marker in ('class="skip-link"','role="tablist"','role="tab"','aria-selected="true"','/ai-accessibility.css?v=14'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        for marker in ('function selectWorkspaceTab','ArrowLeft','ArrowRight',"open.className='session-open'","open.type='button'",'function trapApprovalFocus',"event.key==='Escape'","event.key!=='Tab'","document.body.classList.add('modal-open')","setAttribute('aria-busy','true')","setAttribute('aria-busy','false')"):
            with self.subTest(marker=marker): self.assertIn(marker,self.script)
        self.assertIn('filter(item=>item.offsetParent!==null)',self.script)
        self.assertIn("$('messages').tabIndex=-1",self.script)
        self.assertNotIn("row.setAttribute('role','button')",self.script)
        self.assertIn('if(!approvalDecisionPending)',self.script)
        self.assertIn('if(!currentApproval||approvalDecisionPending)return',self.script)

    def test_stage_eight_completion_contract(self):
        self.assertIn("if(activeRunId!==runId)return",self.script)
        self.assertIn("else if(currentApproval?.run_id===runId)closeApproval(true)",self.script)
        self.assertIn('renderRunPlan(session.events||[])',self.script)
        self.assertIn('requestAnimationFrame',self.script)
        self.assertIn('max-height:310px;overflow:auto',self.agent_css)

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
