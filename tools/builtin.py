from tools.base import RiskLevel,ToolSpec
from tools.calculation import calculator,current_time
from tools.python import python_execute
from tools.registry import ToolRegistry
from tools.system import system_status
from tools.web import read_webpage,web_search
from tools.workspace import WorkspaceManager,file_list,file_read,file_search,git_diff,git_status,workspace_open


OBJECT={'type':'object','additionalProperties':False}


def build_registry(workspace=None):
    workspace=workspace or WorkspaceManager.from_environment()
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
    registry.register(ToolSpec(
        'workspace_open','Select a pre-registered workspace and return its root and project instruction files.',
        {**OBJECT,'properties':{'path':{'type':'string','maxLength':4096}}},
        lambda args:workspace_open(workspace,args),RiskLevel.READ,5,80_000,
    ))
    registry.register(ToolSpec(
        'file_list','List non-ignored files and directories inside the active workspace.',
        {**OBJECT,'properties':{'path':{'type':'string','maxLength':4096},'max_depth':{'type':'integer','minimum':0,'maximum':5},'max_entries':{'type':'integer','minimum':1,'maximum':500},'include_hidden':{'type':'boolean'}}},
        lambda args:file_list(workspace,args),RiskLevel.READ,10,50_000,
    ))
    registry.register(ToolSpec(
        'file_read','Read a bounded UTF-8 text file inside the active workspace.',
        {**OBJECT,'properties':{'path':{'type':'string','minLength':1,'maxLength':4096},'start_line':{'type':'integer','minimum':1,'maximum':1000000},'line_count':{'type':'integer','minimum':1,'maximum':1000}},'required':['path']},
        lambda args:file_read(workspace,args),RiskLevel.READ,5,80_000,
    ))
    registry.register(ToolSpec(
        'file_search','Search workspace text with ripgrep while respecting Git ignore and hidden-file defaults.',
        {**OBJECT,'properties':{'query':{'type':'string','minLength':1,'maxLength':500},'path':{'type':'string','maxLength':4096},'glob':{'type':'string','maxLength':200},'fixed_strings':{'type':'boolean'},'case_sensitive':{'type':'boolean'},'max_results':{'type':'integer','minimum':1,'maximum':200}},'required':['query']},
        lambda args:file_search(workspace,args),RiskLevel.READ,20,80_000,
    ))
    registry.register(ToolSpec(
        'git_status','Read the active workspace Git branch and working-tree status.',
        {**OBJECT,'properties':{}},lambda args:git_status(workspace,args),RiskLevel.READ,10,30_000,
    ))
    registry.register(ToolSpec(
        'git_diff','Read unstaged or staged Git diff, optionally limited to one workspace path.',
        {**OBJECT,'properties':{'staged':{'type':'boolean'},'path':{'type':'string','maxLength':4096}}},
        lambda args:git_diff(workspace,args),RiskLevel.READ,20,100_000,
    ))
    return registry
