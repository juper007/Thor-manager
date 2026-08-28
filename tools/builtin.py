from tools.base import RiskLevel,ToolSpec
from tools.calculation import calculator,current_time
from tools.python import python_execute
from tools.registry import ToolRegistry
from tools.system import system_status
from tools.web import read_webpage,web_search


OBJECT={'type':'object','additionalProperties':False}


def build_registry():
    registry=ToolRegistry()
    registry.register(ToolSpec(
        'web_search','Search the current public web for recent or externally verifiable information.',
        {**OBJECT,'properties':{'query':{'type':'string','minLength':1,'maxLength':300},'max_results':{'type':'integer','minimum':1,'maximum':8}},'required':['query']},
        web_search,RiskLevel.READ,25,30_000,
    ))
    registry.register(ToolSpec(
        'read_webpage','Read text from a public HTTP or HTTPS page; private network targets are blocked.',
        {**OBJECT,'properties':{'url':{'type':'string','minLength':1,'maxLength':2048}},'required':['url']},
        read_webpage,RiskLevel.READ,25,30_000,
    ))
    registry.register(ToolSpec(
        'calculator','Evaluate a bounded arithmetic expression.',
        {**OBJECT,'properties':{'expression':{'type':'string','minLength':1,'maxLength':200}},'required':['expression']},
        calculator,RiskLevel.READ,2,10_000,
    ))
    registry.register(ToolSpec(
        'current_time','Get the current time in an optional IANA timezone.',
        {**OBJECT,'properties':{'timezone':{'type':'string','minLength':1,'maxLength':100}}},
        current_time,RiskLevel.READ,2,10_000,
    ))
    registry.register(ToolSpec(
        'system_status','Read Jetson hostname, uptime, load, memory, disk, and GPU state.',
        {**OBJECT,'properties':{}},system_status,RiskLevel.READ,5,20_000,
    ))
    registry.register(ToolSpec(
        'python_execute','Run Python in the isolated, network-disabled Docker sandbox.',
        {**OBJECT,'properties':{'code':{'type':'string','minLength':1,'maxLength':12000}},'required':['code']},
        python_execute,RiskLevel.ELEVATED,40,30_000,
    ))
    return registry
