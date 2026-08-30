import queue
import threading
import time

from tools.base import ToolResult,ToolSpec,ToolTimeoutError,ToolValidationError,limit_output,thaw,validate_schema


class ToolRegistry:
    def __init__(self): self._tools={}

    def register(self,spec:ToolSpec):
        if spec.name in self._tools: raise ValueError(f'duplicate tool: {spec.name}')
        self._tools[spec.name]=spec
        return spec

    def get(self,name): return self._tools.get(name)

    def require(self,name):
        spec=self.get(name)
        if spec is None: raise ToolValidationError(f'unknown tool: {name}')
        return spec

    def names(self): return tuple(self._tools)

    def handlers(self): return {name:spec.handler for name,spec in self._tools.items()}

    def _invoke(self,spec,arguments):
        completed=queue.Queue(maxsize=1)
        def run():
            try: completed.put((True,spec.handler(arguments)))
            except Exception as exc: completed.put((False,exc))
        threading.Thread(target=run,daemon=True,name=f'tool-{spec.name}').start()
        try: succeeded,value=completed.get(timeout=spec.timeout_seconds)
        except queue.Empty: raise ToolTimeoutError(f'{spec.name} timed out after {spec.timeout_seconds} seconds')
        if not succeeded: raise value
        return value

    def execute(self,name,arguments):
        started=time.monotonic(); result=None; error=None; error_code=None; truncated=False; status='success'
        try:
            spec=self.require(name)
            validate_schema(arguments,spec.input_schema)
            result=self._invoke(spec,arguments)
            result,truncated=limit_output(result,spec.output_limit)
        except ToolValidationError as exc:
            status='error'; error_code='validation_error'; error=str(exc)
        except ToolTimeoutError as exc:
            status='error'; error_code='timeout'; error=str(exc)
        except Exception as exc:
            status='error'; error_code='execution_error'; error=str(exc)
        return ToolResult(name,arguments,status,result,error,error_code,round(time.monotonic()-started,2),truncated).as_dict()

    def model_catalog(self):
        return [{
            'name':spec.name,
            'description':spec.description,
            'input_schema':thaw(spec.input_schema),
            'risk_level':spec.risk_level.value,
        } for spec in self._tools.values()]

    def prompt_catalog(self,allowed_names=None):
        allowed=None if allowed_names is None else set(allowed_names)
        lines=[]
        for spec in self._tools.values():
            if allowed is not None and spec.name not in allowed: continue
            properties=spec.input_schema.get('properties',{})
            args=', '.join(f'{name}:{item.get("type","any")}' for name,item in properties.items()) or 'no arguments'
            lines.append(f'- {spec.name} ({args}): {spec.description}')
        return '\n'.join(lines)
