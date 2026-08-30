"""Independent final-answer verifier."""
import json


class VerificationAgent:
    def __init__(self,model_call,max_characters=20_000): self.model_call=model_call; self.max_characters=max_characters
    def verify(self,request,answer,evidence):
        prompt='''Act only as an independent verifier. Return strict JSON with keys passed (boolean), issues (array of strings), and summary (string). Check whether the answer satisfies the request and is supported by the evidence. Do not propose or call tools.\n'''
        payload={'request':request[-8000:],'answer':answer[-8000:],'evidence':evidence[-self.max_characters:]}
        raw=self.model_call([{'role':'system','content':prompt},{'role':'user','content':json.dumps(payload,ensure_ascii=False)}])
        try:
            result=json.loads(raw)
            if not isinstance(result.get('passed'),bool) or not isinstance(result.get('issues'),list): raise ValueError
            return result
        except (ValueError,TypeError,json.JSONDecodeError): return {'passed':False,'issues':['verifier returned invalid JSON'],'summary':raw[:1000]}
