import threading
import time
from dataclasses import dataclass,field
from enum import Enum


class RunState(str,Enum):
    ANALYZING='analyzing'
    PLANNING='planning'
    AWAITING_APPROVAL='awaiting_approval'
    EXECUTING='executing'
    OBSERVING='observing'
    VERIFYING='verifying'
    COMPLETED='completed'
    FAILED='failed'
    CANCELLED='cancelled'


TERMINAL_STATES={RunState.COMPLETED,RunState.FAILED,RunState.CANCELLED}
TRANSITIONS={
    RunState.ANALYZING:{RunState.PLANNING,RunState.FAILED,RunState.CANCELLED},
    RunState.PLANNING:{RunState.EXECUTING,RunState.VERIFYING,RunState.FAILED,RunState.CANCELLED},
    RunState.AWAITING_APPROVAL:{RunState.EXECUTING,RunState.CANCELLED,RunState.FAILED},
    RunState.EXECUTING:{RunState.OBSERVING,RunState.FAILED,RunState.CANCELLED},
    RunState.OBSERVING:{RunState.PLANNING,RunState.VERIFYING,RunState.FAILED,RunState.CANCELLED},
    RunState.VERIFYING:{RunState.COMPLETED,RunState.FAILED,RunState.CANCELLED},
    RunState.COMPLETED:set(),RunState.FAILED:set(),RunState.CANCELLED:set(),
}


@dataclass
class AgentEvent:
    sequence:int
    timestamp:float
    type:str
    state:str
    payload:dict=field(default_factory=dict)

    def as_dict(self):
        return {'sequence':self.sequence,'timestamp':self.timestamp,'type':self.type,'state':self.state,'payload':self.payload}


@dataclass
class AgentRun:
    run_id:str
    state:RunState=RunState.ANALYZING
    created_at:float=field(default_factory=time.time)
    updated_at:float=field(default_factory=time.time)
    iterations:int=0
    tool_calls:int=0
    error:str|None=None
    events:list=field(default_factory=list)
    _cancel:threading.Event=field(default_factory=threading.Event,repr=False)
    _lock:threading.Lock=field(default_factory=threading.Lock,repr=False)

    def emit(self,event_type,payload=None):
        with self._lock:
            event=AgentEvent(len(self.events)+1,time.time(),event_type,self.state.value,payload or {})
            self.events.append(event); self.updated_at=event.timestamp
            return event

    def transition(self,new_state,reason=None):
        if isinstance(new_state,str): new_state=RunState(new_state)
        with self._lock:
            if new_state not in TRANSITIONS[self.state]:
                raise ValueError(f'invalid run transition: {self.state.value} -> {new_state.value}')
            previous=self.state; self.state=new_state; self.updated_at=time.time()
            payload={'from':previous.value,'to':new_state.value}
            if reason: payload['reason']=reason
            event=AgentEvent(len(self.events)+1,self.updated_at,'run.state',self.state.value,payload)
            self.events.append(event)
            return event

    def cancel(self):
        self._cancel.set(); self.emit('run.cancel_requested')

    def is_cancelled(self): return self._cancel.is_set()
    def is_terminal(self): return self.state in TERMINAL_STATES

    def snapshot(self,include_events=True):
        with self._lock:
            result={
                'run_id':self.run_id,'state':self.state.value,'created_at':self.created_at,'updated_at':self.updated_at,
                'iterations':self.iterations,'tool_calls':self.tool_calls,'error':self.error,
            }
            if include_events: result['events']=[event.as_dict() for event in self.events]
            return result
