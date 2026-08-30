_manager=None


def configure(manager):
    global _manager
    _manager=manager


def mcp_list(arguments):
    if _manager is None: return {'servers':[]}
    servers=[]
    for server in _manager.status():
        item={'name':server['name'],'connected':server['connected'],'enabled':server['enabled'],'tools':[]}
        if server['connected']:
            try: item['tools']=_manager.connect(server['name']).list_tools()
            except Exception as exc: item['error']=str(exc)
        servers.append(item)
    return {'servers':servers}


def mcp_call(arguments):
    if _manager is None: raise RuntimeError('MCP manager is not configured')
    return _manager.connect(arguments['server']).call_tool(arguments['tool'],arguments.get('arguments',{}))
