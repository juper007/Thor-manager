from tools.base import RiskLevel,ToolSpec
from tools.calculation import calculator,current_time
from tools.coding import file_patch,file_write,git_commit,git_stage,shell_execute,test_run
from tools.python import python_execute
from tools.registry import ToolRegistry
from tools.system import system_status
from tools.web import read_webpage,web_search
from tools.workspace import WorkspaceManager,file_list,file_read,file_search,git_diff,git_status,workspace_open
from tools.mcp import mcp_call,mcp_list


OBJECT={'type':'object','additionalProperties':False}
WORKSPACE={'workspace':{'type':'string','maxLength':4096,'description':'Exact registered workspace name or root returned by workspace_open. Omit when only one root is registered.'}}


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
    registry.register(ToolSpec('mcp_list','List configured MCP servers and their available tools.',
        {**OBJECT,'properties':{}},mcp_list,RiskLevel.READ,35,50_000))
    registry.register(ToolSpec('mcp_call','Call a tool on a configured MCP server. External MCP tools require approval.',
        {**OBJECT,'properties':{'server':{'type':'string','minLength':1,'maxLength':80},'tool':{'type':'string','minLength':1,'maxLength':200},'arguments':{'type':'object'}},'required':['server','tool']},
        mcp_call,RiskLevel.ELEVATED,65,80_000))
    registry.register(ToolSpec(
        'python_execute','Run Python in the isolated, network-disabled Docker sandbox.',
        {**OBJECT,'properties':{'code':{'type':'string','minLength':1,'maxLength':12000}},'required':['code']},
        python_execute,RiskLevel.ELEVATED,40,30_000,
    ))
    registry.register(ToolSpec(
        'workspace_open','Open a registered workspace by its exact name or root. Omit workspace to open the only registered root. This argument is not a file path.',
        {**OBJECT,'properties':{**WORKSPACE}},
        lambda args:workspace_open(workspace,args),RiskLevel.READ,5,80_000,
    ))
    registry.register(ToolSpec(
        'file_list','List non-ignored files and directories inside the active workspace.',
        {**OBJECT,'properties':{**WORKSPACE,'path':{'type':'string','maxLength':4096},'max_depth':{'type':'integer','minimum':0,'maximum':5},'max_entries':{'type':'integer','minimum':1,'maximum':500},'include_hidden':{'type':'boolean'}}},
        lambda args:file_list(workspace,args),RiskLevel.READ,10,50_000,
    ))
    registry.register(ToolSpec(
        'file_read','Read a bounded UTF-8 text file inside the active workspace.',
        {**OBJECT,'properties':{**WORKSPACE,'path':{'type':'string','minLength':1,'maxLength':4096},'start_line':{'type':'integer','minimum':1,'maximum':1000000},'line_count':{'type':'integer','minimum':1,'maximum':1000}},'required':['path']},
        lambda args:file_read(workspace,args),RiskLevel.READ,5,80_000,
    ))
    registry.register(ToolSpec(
        'file_search','Search text in a workspace directory or a specific file path while respecting ignore and protected-file policies.',
        {**OBJECT,'properties':{**WORKSPACE,'query':{'type':'string','minLength':1,'maxLength':500},'path':{'type':'string','maxLength':4096},'glob':{'type':'string','maxLength':200},'fixed_strings':{'type':'boolean'},'case_sensitive':{'type':'boolean'},'max_results':{'type':'integer','minimum':1,'maximum':200}},'required':['query']},
        lambda args:file_search(workspace,args),RiskLevel.READ,20,80_000,
    ))
    registry.register(ToolSpec(
        'git_status','Read the active workspace Git branch and working-tree status.',
        {**OBJECT,'properties':{**WORKSPACE}},lambda args:git_status(workspace,args),RiskLevel.READ,10,30_000,
    ))
    registry.register(ToolSpec(
        'git_diff','Read unstaged or staged Git diff, optionally limited to one workspace path.',
        {**OBJECT,'properties':{**WORKSPACE,'staged':{'type':'boolean'},'path':{'type':'string','maxLength':4096}}},
        lambda args:git_diff(workspace,args),RiskLevel.READ,20,100_000,
    ))
    change_properties={**WORKSPACE,'path':{'type':'string','minLength':1,'maxLength':4096},'expected_sha256':{'type':'string','minLength':64,'maxLength':64},'apply':{'type':'boolean'}}
    registry.register(ToolSpec(
        'file_write','Preview or atomically write a UTF-8 file. Existing files require the SHA-256 returned by file_read or a prior preview.',
        {**OBJECT,'properties':{**change_properties,'content':{'type':'string','maxLength':256000}},'required':['path','content']},
        lambda args:file_write(workspace,args),RiskLevel.SAFE_WRITE,10,100_000,
    ))
    registry.register(ToolSpec(
        'file_patch','Preview or atomically apply one exact text replacement; the current file SHA-256 is required.',
        {**OBJECT,'properties':{**change_properties,'old_text':{'type':'string','minLength':1,'maxLength':50000},'new_text':{'type':'string','maxLength':50000}},'required':['path','expected_sha256','old_text','new_text']},
        lambda args:file_patch(workspace,args),RiskLevel.SAFE_WRITE,10,100_000,
    ))
    command_schema={**OBJECT,'properties':{**WORKSPACE,'command':{'type':'string','minLength':1,'maxLength':4000},'timeout_seconds':{'type':'integer','minimum':1,'maximum':300}},'required':['command']}
    registry.register(ToolSpec('shell_execute','Run a bounded command inside a network-disabled Docker sandbox with writable workspace access.',command_schema,
        lambda args:shell_execute(workspace,args),RiskLevel.DESTRUCTIVE,310,40_000))
    registry.register(ToolSpec('test_run','Run a bounded test command with the workspace mounted read-only in a network-disabled Docker sandbox.',command_schema,
        lambda args:test_run(workspace,args),RiskLevel.ELEVATED,310,40_000))
    registry.register(ToolSpec('git_stage','Stage explicit files only when their SHA-256 values still match.',
        {**OBJECT,'properties':{**WORKSPACE,'files':{'type':'array','items':{'type':'object','properties':{'path':{'type':'string','minLength':1,'maxLength':4096},'sha256':{'type':'string','minLength':64,'maxLength':64}},'required':['path','sha256'],'additionalProperties':False}}},'required':['files']},
        lambda args:git_stage(workspace,args),RiskLevel.ELEVATED,20,30_000))
    registry.register(ToolSpec('git_commit','Create a local Git commit from already staged changes. This never pushes.',
        {**OBJECT,'properties':{**WORKSPACE,'message':{'type':'string','minLength':1,'maxLength':200},'expected_index_hash':{'type':'string','minLength':40,'maxLength':64}},'required':['message','expected_index_hash']},
        lambda args:git_commit(workspace,args),RiskLevel.DESTRUCTIVE,70,20_000))
    return registry
