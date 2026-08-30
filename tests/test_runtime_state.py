import threading
import time
import unittest

from agent.runtime import AgentRuntime,RunCancelled,RunLimitError,RunTimeout,ServiceBusy,tool_completion_payload,validate_run_id,validate_run_mode
from agent.state import AgentRun,RunState


class FakeRegistry:
    def __init__(self): self.calls=[]
    def execute(self,name,arguments):
        self.calls.append((name,arguments))
        return {'name':name,'arguments':arguments,'status':'success','result':{'value':len(self.calls)},'error':None,'error_code':None,'seconds':0}


def parser(text):
    if not text.startswith('tool:'): return []
    return [{'name':'fake','arguments':{'value':text[5:]}}]


def make_runtime(replies,**limits):
    iterator=iter(replies)
    return AgentRuntime('.',lambda _:next(iterator),FakeRegistry(),parser,lambda _:'skill',lambda text:text,**limits)


class AgentStateTests(unittest.TestCase):
    def test_transition_events_and_invalid_transition(self):
        run=AgentRun('one'); run.emit('run.created'); run.transition(RunState.PLANNING)
        self.assertEqual(run.snapshot()['events'][-1]['payload']['to'],'planning')
        with self.assertRaises(ValueError): run.transition(RunState.COMPLETED)

    def test_simple_run_completes_with_events(self):
        runtime=make_runtime(['answer'])
        run,answer,events,_=runtime.run_chat([{'role':'user','content':'hello'}],'simple')
        self.assertEqual((answer,events),('answer',[])); self.assertEqual(run.state,RunState.COMPLETED)
        types=[event['type'] for event in run.snapshot()['events']]
        self.assertIn('model.started',types); self.assertIn('run.completed',types)

    def test_run_modes_emit_plan_progress_and_block_tools_outside_agent(self):
        for index,mode in enumerate(('ask','plan')):
            with self.subTest(mode=mode):
                runtime=make_runtime(['tool:x','direct response'])
                run,answer,events,_=runtime.run_chat([{'role':'user','content':'hello'}],f'mode-{index}',mode=mode)
                self.assertEqual(answer,'direct response'); self.assertEqual(events,[]); self.assertEqual(runtime.registry.calls,[])
                snapshot=run.snapshot(); types=[event['type'] for event in snapshot['events']]
                self.assertIn('run.mode',types); self.assertIn('plan.created',types)
                plan=[event for event in snapshot['events'] if event['type']=='plan.step']
                self.assertEqual([(event['payload']['position'],event['payload']['status']) for event in plan][-2:],[(3,'in_progress'),(3,'completed')])

    def test_invalid_run_mode_is_rejected(self):
        with self.assertRaises(ValueError): validate_run_mode('unsafe')
        self.assertEqual(validate_run_mode(None),'agent')

    def test_tool_run_records_execution_and_prevents_duplicate(self):
        runtime=make_runtime(['tool:x','tool:x','final'])
        run,answer,events,_=runtime.run_chat([{'role':'user','content':'go'}],'tools')
        self.assertEqual(answer,'final'); self.assertEqual(len(events),1); self.assertEqual(run.tool_calls,1)
        self.assertIn('tool.reused',[event['type'] for event in run.snapshot()['events']])

    def test_diff_tool_completion_exposes_bounded_preview(self):
        runtime=make_runtime(['tool:x','done'])
        runtime.parse_calls=lambda text: [{'name':'git_diff','arguments':{}}] if text.startswith('tool:') else []
        runtime.registry.execute=lambda name,arguments:{'name':name,'arguments':arguments,'status':'success','result':{'path':'app.py','diff':'+api_key=secret\n'+'x'*60_000},'error':None,'error_code':None,'seconds':0}
        run,_,_,_=runtime.run_chat([{'role':'user','content':'go'}],'diff-preview')
        payload=[event['payload'] for event in run.snapshot()['events'] if event['type']=='tool.completed'][0]
        self.assertEqual(payload['path'],'app.py'); self.assertLessEqual(len(payload['diff']),50_000); self.assertTrue(payload['diff_truncated'])
        self.assertNotIn('secret',payload['diff']); self.assertIn('[REDACTED]',payload['diff'])

    def test_test_run_completion_exposes_redacted_result(self):
        runtime=make_runtime(['tool:x','done'])
        runtime.parse_calls=lambda text: [{'name':'test_run','arguments':{'command':'token=command-secret pytest'}}] if text.startswith('tool:') else []
        runtime.registry.execute=lambda name,arguments:{'name':name,'arguments':arguments,'status':'success','result':{'return_code':1,'stdout':'1 failed\n','stderr':'api_key=result-secret'},'error':None,'error_code':None,'seconds':1.25,'truncated':False}
        run,_,_,_=runtime.run_chat([{'role':'user','content':'go'}],'test-result')
        payload=[event['payload'] for event in run.snapshot()['events'] if event['type']=='tool.completed'][0]
        result=payload['test_result']; self.assertEqual((result['return_code'],payload['seconds']),(1,1.25))
        self.assertNotIn('command-secret',result['command']); self.assertNotIn('result-secret',result['stderr'])
        truncated_event={'status':'success','result':{'preview':'token=preview-secret','truncated':True},'error_code':None,'seconds':2,'truncated':True}
        truncated=tool_completion_payload({'name':'test_run','arguments':{'command':'pytest'}},truncated_event)['test_result']
        self.assertIsNone(truncated['return_code']); self.assertTrue(truncated['truncated']); self.assertIn('[REDACTED]',truncated['stdout'])

    def test_multiple_tools_run_sequentially(self):
        runtime=make_runtime(['tool:a','tool:b','done'])
        run,answer,events,_=runtime.run_chat([{'role':'user','content':'go'}],'sequential')
        self.assertEqual(answer,'done'); self.assertEqual(run.tool_calls,2)
        self.assertEqual([event['arguments']['value'] for event in events],['a','b'])

    def test_cancel_during_tool_prevents_the_next_tool(self):
        entered=threading.Event(); release=threading.Event(); caught=[]
        runtime=make_runtime(['ignored'])
        runtime.parse_calls=lambda _:[{'name':'first','arguments':{}},{'name':'second','arguments':{}}]
        def execute(name,arguments):
            runtime.registry.calls.append((name,arguments))
            if name=='first': entered.set(); release.wait(2)
            return {'name':name,'arguments':arguments,'status':'success','result':{},'error':None,'error_code':None,'seconds':0}
        runtime.registry.execute=execute
        def run():
            try: runtime.run_chat([{'role':'user','content':'go'}],'cancel-tool')
            except Exception as exc: caught.append(exc)
        thread=threading.Thread(target=run); thread.start(); self.assertTrue(entered.wait(1))
        runtime.cancel('cancel-tool'); release.set(); thread.join(1)
        self.assertEqual([name for name,_ in runtime.registry.calls],['first'])
        self.assertIsInstance(caught[0],RunCancelled)
        self.assertEqual(runtime.get_run('cancel-tool').state,RunState.CANCELLED)

    def test_tool_call_limit_fails_run(self):
        runtime=make_runtime(['tool:x'],max_tool_calls=1)
        runtime.parse_calls=lambda _:[{'name':'fake','arguments':{'value':'1'}},{'name':'fake','arguments':{'value':'2'}}]
        with self.assertRaises(RunLimitError): runtime.run_chat([{'role':'user','content':'go'}],'limited')
        self.assertEqual(runtime.get_run('limited').state,RunState.FAILED)

    def test_total_timeout_fails_run(self):
        runtime=make_runtime([],total_timeout=.05)
        runtime.model_call=lambda _:time.sleep(1)
        with self.assertRaises(RunTimeout): runtime.run_chat([{'role':'user','content':'go'}],'timeout')
        self.assertEqual(runtime.get_run('timeout').state,RunState.FAILED)
        failed=[event for event in runtime.get_run('timeout').snapshot()['events'] if event['type']=='plan.step' and event['payload']['status']=='failed']
        self.assertTrue(failed)

    def test_cancellation_stops_waiting_for_model(self):
        started=threading.Event(); release=threading.Event(); caught=[]
        runtime=make_runtime([])
        def model(_): started.set(); release.wait(2); return 'late'
        runtime.model_call=model
        def run():
            try: runtime.run_chat([{'role':'user','content':'go'}],'cancel-me')
            except Exception as exc: caught.append(exc)
        thread=threading.Thread(target=run); thread.start(); self.assertTrue(started.wait(1))
        snapshot=runtime.cancel('cancel-me'); thread.join(1); release.set()
        self.assertFalse(thread.is_alive()); self.assertIsInstance(caught[0],RunCancelled)
        self.assertEqual(runtime.get_run('cancel-me').state,RunState.CANCELLED)
        self.assertEqual(snapshot['run_id'],'cancel-me')

    def test_run_id_validation_and_recent_run_bound(self):
        with self.assertRaises(ValueError): validate_run_id('../bad')
        runtime=make_runtime(['a','b'],recent_run_limit=1)
        runtime.run_chat([{'role':'user','content':'a'}],'a')
        runtime.run_chat([{'role':'user','content':'b'}],'b')
        self.assertIsNone(runtime.get_run('a')); self.assertIsNotNone(runtime.get_run('b'))

    def test_active_run_is_not_evicted(self):
        runtime=make_runtime([],recent_run_limit=1)
        active=runtime.create_run('active')
        completed=runtime.create_run('newer')
        completed.transition(RunState.PLANNING); completed.transition(RunState.VERIFYING); completed.transition(RunState.COMPLETED)
        runtime._prune_runs()
        self.assertIs(runtime.get_run('active'),active)
        self.assertIsNotNone(runtime.get_run('newer'))

    def test_cancelled_model_holds_concurrency_until_worker_finishes(self):
        entered=threading.Event(); release=threading.Event(); caught=[]
        runtime=make_runtime([])
        def model(_): entered.set(); release.wait(2); return 'late'
        runtime.model_call=model
        thread=threading.Thread(target=lambda:self._capture(runtime,caught,'first'))
        thread.start(); self.assertTrue(entered.wait(1)); runtime.cancel('first'); thread.join(1)
        with self.assertRaises(ServiceBusy): runtime.run_chat([{'role':'user','content':'next'}],'second')
        release.set(); deadline=time.time()+1; acquired=False
        while time.time()<deadline and not acquired:
            acquired=runtime.gate.acquire(blocking=False)
            if not acquired: time.sleep(.01)
        self.assertTrue(acquired,'AI capacity was not released after model worker exited')
        if acquired: runtime.gate.release()
        self.assertIsInstance(caught[0],RunCancelled)

    def _capture(self,runtime,caught,run_id):
        try: runtime.run_chat([{'role':'user','content':'go'}],run_id)
        except Exception as exc: caught.append(exc)

    def test_recovery_tool_call_is_reported_as_failure(self):
        runtime=make_runtime(['tool:x','tool:x','tool:x'])
        with self.assertRaises(RunLimitError): runtime.run_chat([{'role':'user','content':'go'}],'looping')
        self.assertEqual(runtime.get_run('looping').state,RunState.FAILED)

    def test_cancel_wins_before_terminal_transition(self):
        runtime=make_runtime(['answer']); original=runtime.strip_tool_calls
        def cancel_during_verify(answer):
            runtime.cancel('finish-race'); return original(answer)
        runtime.strip_tool_calls=cancel_during_verify
        with self.assertRaises(RunCancelled): runtime.run_chat([{'role':'user','content':'go'}],'finish-race')
        self.assertEqual(runtime.get_run('finish-race').state,RunState.CANCELLED)

    def test_streaming_emits_answer_deltas(self):
        runtime=make_runtime([]); chunks=[]
        def stream(_,callback):
            for value in ('안','녕','하세요'): callback(value)
            return '안녕하세요'
        runtime.stream_model_call=stream
        _,answer,_,_=runtime.run_chat([{'role':'user','content':'hello'}],'stream',on_delta=chunks.append)
        self.assertEqual(answer,'안녕하세요'); self.assertEqual(''.join(chunks),'안녕하세요')

    def test_streaming_hides_tool_call_markup(self):
        runtime=make_runtime([]); chunks=[]; replies=iter(['<tool_call>{"name":"fake","arguments":{"value":"x"}}</tool_call>','완료'])
        runtime.parse_calls=lambda value: [{'name':'fake','arguments':{'value':'x'}}] if value.startswith('<tool_call>') else []
        def stream(_,callback):
            value=next(replies)
            for part in value: callback(part)
            return value
        runtime.stream_model_call=stream
        _,answer,_,_=runtime.run_chat([{'role':'user','content':'go'}],'stream-tool',on_delta=chunks.append)
        self.assertEqual(answer,'완료'); self.assertEqual(''.join(chunks),'완료'); self.assertNotIn('tool_call',''.join(chunks))

    def test_streaming_hides_tool_call_after_preamble(self):
        runtime=make_runtime([]); chunks=[]
        replies=iter(['확인합니다.\n<tool_call>{"name":"fake","arguments":{"value":"x"}}</tool_call>','최종 답변'])
        runtime.parse_calls=lambda value: [{'name':'fake','arguments':{'value':'x'}}] if '<tool_call>' in value else []
        def stream(_,callback):
            value=next(replies)
            for part in value: callback(part)
            return value
        runtime.stream_model_call=stream
        _,answer,_,_=runtime.run_chat([{'role':'user','content':'go'}],'stream-preamble',on_delta=chunks.append)
        self.assertEqual(answer,'최종 답변'); self.assertNotIn('tool_call',''.join(chunks)); self.assertNotIn('{"name"',''.join(chunks))

    def test_streaming_callback_runs_on_request_thread_and_stops_after_cancel(self):
        produced=threading.Event(); delivered=threading.Event(); release=threading.Event(); chunks=[]; caught=[]; callback_threads=[]
        runtime=make_runtime([])
        def stream(_,callback):
            callback('첫'); produced.set(); release.wait(2); callback('늦은 토큰'); return '첫늦은 토큰'
        runtime.stream_model_call=stream
        def collect(value): chunks.append(value); callback_threads.append(threading.current_thread().name); delivered.set()
        request_thread=threading.Thread(target=lambda:self._capture_stream(runtime,caught,collect),name='request-thread')
        request_thread.start(); self.assertTrue(produced.wait(1)); self.assertTrue(delivered.wait(1)); runtime.cancel('stream-cancel'); request_thread.join(1); release.set()
        self.assertFalse(request_thread.is_alive()); self.assertIsInstance(caught[0],RunCancelled)
        self.assertEqual(''.join(chunks),'첫'); self.assertEqual(callback_threads,['request-thread'])

    def _capture_stream(self,runtime,caught,callback):
        try: runtime.run_chat([{'role':'user','content':'go'}],'stream-cancel',on_delta=callback)
        except Exception as exc: caught.append(exc)


if __name__=='__main__': unittest.main()
