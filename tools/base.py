import json
import copy
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any,Callable


class RiskLevel(str,Enum):
    READ='read'
    SAFE_WRITE='safe_write'
    ELEVATED='elevated'
    DESTRUCTIVE='destructive'


class ToolValidationError(ValueError):
    pass


class ToolTimeoutError(TimeoutError):
    pass


JSON_TYPES={
    'object':dict,
    'array':list,
    'string':str,
    'integer':int,
    'number':(int,float),
    'boolean':bool,
    'null':type(None),
}


def validate_schema(value:Any,schema:dict,path='arguments'):
    expected=schema.get('type')
    if expected:
        python_type=JSON_TYPES.get(expected)
        if python_type is None: raise ToolValidationError(f'{path}: unsupported schema type {expected}')
        if expected in ('integer','number') and isinstance(value,bool): valid=False
        else: valid=isinstance(value,python_type)
        if not valid: raise ToolValidationError(f'{path}: expected {expected}')
    if isinstance(value,dict):
        properties=schema.get('properties',{}); required=schema.get('required',[])
        for name in required:
            if name not in value: raise ToolValidationError(f'{path}.{name}: required property is missing')
        if schema.get('additionalProperties') is False:
            unknown=sorted(set(value)-set(properties))
            if unknown: raise ToolValidationError(f'{path}: unknown properties: {", ".join(unknown)}')
        for name,item in value.items():
            if name in properties: validate_schema(item,properties[name],f'{path}.{name}')
    if isinstance(value,list) and 'items' in schema:
        for index,item in enumerate(value): validate_schema(item,schema['items'],f'{path}[{index}]')
    if isinstance(value,str):
        if len(value)<schema.get('minLength',0): raise ToolValidationError(f'{path}: string is too short')
        if 'maxLength' in schema and len(value)>schema['maxLength']: raise ToolValidationError(f'{path}: string is too long')
        if 'enum' in schema and value not in schema['enum']: raise ToolValidationError(f'{path}: value is not allowed')
    if isinstance(value,(int,float)) and not isinstance(value,bool):
        if 'minimum' in schema and value<schema['minimum']: raise ToolValidationError(f'{path}: value is below minimum')
        if 'maximum' in schema and value>schema['maximum']: raise ToolValidationError(f'{path}: value is above maximum')


@dataclass(frozen=True)
class ToolSpec:
    name:str
    description:str
    input_schema:dict
    handler:Callable[[dict],Any]
    risk_level:RiskLevel=RiskLevel.READ
    timeout_seconds:int=30
    output_limit:int=30_000

    def __post_init__(self):
        if not self.name or not self.name.replace('_','').isalnum(): raise ValueError('tool name must contain letters, numbers, or underscores')
        if self.input_schema.get('type')!='object': raise ValueError('tool input schema must describe an object')
        if self.timeout_seconds<=0: raise ValueError('tool timeout must be positive')
        if self.output_limit<=0: raise ValueError('tool output limit must be positive')
        object.__setattr__(self,'input_schema',freeze(copy.deepcopy(self.input_schema)))


@dataclass
class ToolResult:
    name:str
    arguments:Any
    status:str
    result:Any
    error:str|None
    error_code:str|None
    seconds:float
    truncated:bool=False

    def as_dict(self):
        return {
            'name':self.name,
            'arguments':self.arguments,
            'status':self.status,
            'result':self.result,
            'error':self.error,
            'error_code':self.error_code,
            'seconds':self.seconds,
            'truncated':self.truncated,
        }


def freeze(value):
    if isinstance(value,dict): return MappingProxyType({key:freeze(item) for key,item in value.items()})
    if isinstance(value,list): return tuple(freeze(item) for item in value)
    return value


def thaw(value):
    if isinstance(value,MappingProxyType): return {key:thaw(item) for key,item in value.items()}
    if isinstance(value,tuple): return [thaw(item) for item in value]
    return value


def limit_output(value:Any,limit:int):
    chunks=[]; used=0; truncated=False
    for chunk in json.JSONEncoder(ensure_ascii=False,default=str).iterencode(value):
        remaining=limit-used
        if len(chunk)>remaining:
            chunks.append(chunk[:max(0,remaining)]); truncated=True; break
        chunks.append(chunk); used+=len(chunk)
    if not truncated: return value,False
    return {'preview':''.join(chunks),'truncated':True},True
