import unittest
from pathlib import Path


ROOT=Path(__file__).resolve().parents[1]


class UIContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.script=(ROOT/'ai-workspace.js').read_text(encoding='utf-8')
        cls.page=(ROOT/'ai-workspace.html').read_text(encoding='utf-8')

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

    def test_image_studio_contract(self):
        for marker in ('data-image-mode="generate"','data-image-mode="edit"','id="sourceImage"','id="generateImage"','id="imageOutput"'):
            with self.subTest(marker=marker): self.assertIn(marker,self.page)
        self.assertIn("/api/images/generations",self.script)
        self.assertIn("/api/images/edits",self.script)


if __name__=='__main__':
    unittest.main()
