import hashlib
import json
import threading
import time
import uuid

from tools.base import RiskLevel


SCOPES={'once','session','always_tool'}
DECISIONS={'allow','deny'}


def arguments_hash(arguments):
    canonical=json.dumps(arguments,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    return hashlib.sha256(canonical.encode()).hexdigest()


def summarize(tool_name,risk_level,arguments):
    preview=json.dumps(arguments,ensure_ascii=False,sort_keys=True)
    if len(preview)>500: preview=preview[:497]+'...'
    return f'{tool_name} ({risk_level}): {preview}'


class PermissionEngine:
    def __init__(self,store=None,ttl_seconds=300):
        self.store=store; self.ttl_seconds=max(1,int(ttl_seconds))
        self._pending={}; self._denied={}; self._condition=threading.Condition()

    def _grant(self,run_id,tool_name,risk_level,owner_id='thor'):
        if self.store is None: return None
        return self.store.find_permission_grant(run_id,tool_name,risk_level,owner_id)

    def authorize(self,run,spec,arguments,cancelled=lambda:False,timeout=None,on_wait=None,on_resume=None):
        risk=spec.risk_level.value
        if spec.risk_level==RiskLevel.READ:
            return {'allowed':True,'source':'read_policy'}
        with self._condition:
            if spec.name in self._denied.get(run.run_id,set()):
                return {'allowed':False,'error_code':'permission_denied','error':'tool permission denied for this run'}
        grant=self._grant(run.run_id,spec.name,risk,run.owner_id)
        if grant: return {'allowed':True,'source':grant['scope']}
        now=time.time(); approval={
            'approval_id':uuid.uuid4().hex,'run_id':run.run_id,'tool_name':spec.name,'risk_level':risk,
            'arguments_hash':arguments_hash(arguments),'arguments':arguments,
            'summary':summarize(spec.name,risk,arguments),'status':'pending','scope':None,
            'created_at':now,'expires_at':now+self.ttl_seconds,'decided_at':None,
        }
        with self._condition: self._pending[approval['approval_id']]=approval
        if self.store is not None: self.store.create_permission_request(approval)
        run.transition('awaiting_approval',f'{spec.name} requires approval')
        run.emit('permission.requested',{key:approval[key] for key in ('approval_id','tool_name','risk_level','summary','expires_at')})
        if on_wait is not None: on_wait()
        wait_seconds=max(0,approval['expires_at']-time.time())
        if timeout is not None: wait_seconds=min(wait_seconds,max(0,timeout))
        deadline=time.monotonic()+wait_seconds
        with self._condition:
            while approval['status']=='pending':
                if cancelled():
                    approval['status']='cancelled'; break
                remaining=deadline-time.monotonic()
                if remaining<=0:
                    approval['status']='expired'; approval['decided_at']=time.time(); break
                self._condition.wait(min(.1,remaining))
        if self.store is not None and approval['status'] in ('expired','cancelled'):
            self.store.decide_permission(approval['approval_id'],approval['status'],None,approval.get('decided_at') or time.time())
        try:
            if not cancelled() and on_resume is not None: on_resume()
        finally:
            with self._condition:
                self._pending.pop(approval['approval_id'],None)
                if approval['status']=='denied': self._denied.setdefault(run.run_id,set()).add(spec.name)
        if cancelled(): return {'allowed':False,'error_code':'cancelled','error':'run cancelled while awaiting approval','approval':approval}
        run.transition('executing',f'permission {approval["status"]}')
        run.emit('permission.decided',{'approval_id':approval['approval_id'],'status':approval['status'],'scope':approval.get('scope')})
        if approval['status']!='allowed':
            return {'allowed':False,'error_code':'permission_'+approval['status'],'error':f'tool permission {approval["status"]}','approval':approval}
        if approval['arguments_hash']!=arguments_hash(arguments):
            return {'allowed':False,'error_code':'permission_arguments_changed','error':'tool arguments changed after approval','approval':approval}
        return {'allowed':True,'source':approval.get('scope') or 'once','approval':approval}

    def finish_run(self,run_id):
        with self._condition:
            self._denied.pop(run_id,None)
            stale=[key for key,value in self._pending.items() if value['run_id']==run_id]
            for key in stale: self._pending.pop(key,None)

    def decide(self,approval_id,decision,scope='once'):
        if decision not in DECISIONS: raise ValueError('decision must be allow or deny')
        if scope not in SCOPES: raise ValueError('scope must be once, session, or always_tool')
        with self._condition:
            approval=self._pending.get(approval_id)
            if approval is None: raise KeyError(approval_id)
            if approval['status']!='pending': raise ValueError('approval is no longer pending')
            if approval['expires_at']<=time.time(): raise ValueError('approval has expired')
            status='allowed' if decision=='allow' else 'denied'; selected_scope=scope if decision=='allow' else None; decided_at=time.time()
            if self.store is not None:
                session=self.store.get_session(approval['run_id']); owner_id=session.get('owner_id','thor') if session else 'thor'
                self.store.apply_permission_decision(approval_id,status,selected_scope,decided_at,
                    approval['run_id'],approval['tool_name'],approval['risk_level'],owner_id)
            approval['status']=status; approval['scope']=selected_scope; approval['decided_at']=decided_at
            self._pending[approval_id]=approval; self._condition.notify_all()
        return approval

    def list(self,run_id=None,status=None):
        if self.store is not None: return self.store.list_permission_requests(run_id,status)
        with self._condition:
            rows=list(self._pending.values())
        if run_id: rows=[row for row in rows if row['run_id']==run_id]
        if status: rows=[row for row in rows if row['status']==status]
        return rows

    def grants(self,owner_id=None): return self.store.list_permission_grants(owner_id) if self.store is not None else []

    def revoke(self,grant_id,owner_id=None):
        if self.store is None: return False
        return self.store.revoke_permission_grant(grant_id,owner_id)
