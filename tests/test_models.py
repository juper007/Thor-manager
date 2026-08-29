import io
import unittest
from unittest import mock

from agent import models


class StreamingModelTests(unittest.TestCase):
    def response(self,*lines):
        return io.BytesIO(('\n'.join(lines)+'\n').encode())

    def test_streaming_surfaces_upstream_error_event(self):
        response=self.response('data: {"error":{"message":"engine failed"}}','data: [DONE]')
        with mock.patch.object(models.urllib.request,'urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,'engine failed'):
                models.edge_chat([{'role':'user','content':'hello'}],on_delta=lambda _:None)

    def test_streaming_requires_done_event(self):
        response=self.response('data: {"choices":[{"delta":{"content":"partial"}}]}')
        with mock.patch.object(models.urllib.request,'urlopen',return_value=response):
            with self.assertRaisesRegex(RuntimeError,r'before \[DONE\]'):
                models.edge_chat([{'role':'user','content':'hello'}],on_delta=lambda _:None)

    def test_streaming_returns_and_emits_complete_text(self):
        response=self.response('data: {"choices":[{"delta":{"content":"안"}}]}','data: {"choices":[{"delta":{"content":"녕"}}]}','data: [DONE]')
        chunks=[]
        with mock.patch.object(models.urllib.request,'urlopen',return_value=response):
            result=models.edge_chat([{'role':'user','content':'hello'}],on_delta=chunks.append)
        self.assertEqual(result,'안녕'); self.assertEqual(chunks,['안','녕'])


if __name__=='__main__': unittest.main()
