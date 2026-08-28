import time

from tools.base import ToolResult,ToolSpec,ToolValidationError,limit_output,validate_schema


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

    def execute(self,name,arguments):
        started=time.monotonic(); result=None; error=None; truncated=False; status='success'
        try:
            spec=self.require(name)
            validate_schema(arguments,spec.input_schema)
            result=spec.handler(arguments)
            result,truncated=limit_output(result,spec.output_limit)
        except Exception as exc:
            status='error'; error=str(exc)
        return ToolResult(name,arguments,status,result,error,round(time.monotonic()-started,2),truncated).as_dict()

    def model_catalog(self):
        return [{
            'name':spec.name,
            'description':spec.description,
            'input_schema':spec.input_schema,
            'risk_level':spec.risk_level.value,
        } for spec in self._tools.values()]

    def prompt_catalog(self):
        lines=[]
        for spec in self._tools.values():
            properties=spec.input_schema.get('properties',{})
            args=', '.join(f'{name}:{item.get("type","any")}' for name,item in properties.items()) or 'no arguments'
            lines.append(f'- {spec.name} ({args}): {spec.description}')
        return '\n'.join(lines)
