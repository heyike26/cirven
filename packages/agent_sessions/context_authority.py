"""Current authority for selected context already admitted to an original owner.

Only bounded selection identities are retained. Checks do not reread source
bodies, redact model history, revoke past disclosures or start another owner.
"""
from copy import deepcopy
import re
import threading

from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest

SCHEMA='eeas.original-context-authority.v1'
MAX_SELECTIONS=64
MAX_BYTES=131072


class ContextAuthority:
    def __init__(self,owner):self.owner=owner;self.graph=None;self.lock=threading.RLock()

    def _path(self,manifest,context):
        native=context.get('session_id')
        if not isinstance(native,str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}',native):
            raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: original native context identity is unavailable')
        return self.owner._path(manifest['project_id'],manifest['id'],'context-authority',digest(native)+'.json')

    def _read(self,manifest,context):
        path=self._path(manifest,context)
        if not path.is_file() or path.stat().st_size>MAX_BYTES:
            raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: original context authority record is unavailable')
        try:
            value=self.owner._read(path)
            ref=self.owner._manifest(manifest['project_id'],manifest['id'])[1]
        except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError) as exc:
            raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: original context authority record cannot be verified') from exc
        if not isinstance(value,dict):raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: context authority record shape differs')
        basis={k:v for k,v in value.items() if k!='record_sha256'}
        if digest(basis)!=value.get('record_sha256'):raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: context authority record integrity differs')
        if (basis.get('schema')!=SCHEMA or basis.get('session_ref')!=ref
                or basis.get('native_session_id')!=context['session_id'] or type(basis.get('complete')) is not bool
                or type(basis.get('revision')) is not int or basis['revision']<1
                or not isinstance(basis.get('selections'),list) or len(basis['selections'])>MAX_SELECTIONS):
            raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: original context authority identity differs')
        for item in basis['selections']:
            if (not isinstance(item,dict) or set(item)!={'selection_id','project_id','conversation_id','frozen_conversation_scope'}
                    or not re.fullmatch(r'[a-f0-9]{64}',item.get('selection_id',''))):
                raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: original context selection identity differs')
        return basis

    def _write(self,path,value):
        result={**value,'record_sha256':digest(value)}
        # The owner's durable writer adds its existing integrity envelope.
        envelope={'value':result,'sha256':digest(result)}
        if len(canonical(envelope).encode())>MAX_BYTES:raise Conflict('SOURCE_CONTEXT_CAPACITY: context authority record is full')
        self.owner._write(path,result,replace=path.exists())

    def begin(self,manifest,context,*,loaded_existing):
        if self.graph is None:return
        with self.lock:
            path=self._path(manifest,context)
            if path.exists():self._read(manifest,context)
            else:
                self._write(path,{'schema':SCHEMA,'session_ref':self.owner._manifest(manifest['project_id'],manifest['id'])[1],
                    'native_session_id':context['session_id'],'revision':1,'complete':not loaded_existing,'selections':[]})
        context.pop('context_authority_cache',None)

    def track(self,manifest,context,*,selection_id,project_id,conversation_id,frozen_conversation_scope=None):
        if self.graph is None:raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: context authority service is not configured')
        frozen=({'project_id':frozen_conversation_scope['project_id'],'conversation_ref':deepcopy(frozen_conversation_scope['conversation_ref'])}
            if frozen_conversation_scope is not None else None)
        item={'selection_id':selection_id,'project_id':project_id,'conversation_id':conversation_id,'frozen_conversation_scope':frozen}
        proof=self.graph.authorize_selection(selection_id,project_id,conversation_id=conversation_id,frozen_conversation_scope=frozen)
        if proof['selected_count']==0:return
        with self.lock:
            value=self._read(manifest,context)
            if not value['complete']:raise Conflict('SOURCE_CONTEXT_LEGACY: original context requires an explicit fresh-context recovery')
            if item not in value['selections']:
                if len(value['selections'])>=MAX_SELECTIONS:raise Conflict('SOURCE_CONTEXT_CAPACITY: keep history and explicitly rebuild the execution context')
                value['selections'].append(item);value['revision']+=1
                self._write(self._path(manifest,context),value)
            context.pop('context_authority_cache',None)

    def _report(self,manifest,context,code):
        pid=manifest['project_id'];sid=manifest['id'];current=self.owner._state(pid,sid)
        if self.owner.active.get(sid) is not context or current.get('generation')!=context['generation']:return
        issue={'code':code,'generation':context['generation'],'operation_id':current.get('active_operation_id'),
            'scope':'FUTURE_HOST_ADMISSIONS_AND_API_RELAY','original_history_retained':True}
        if current.get('context_authority_issue')!=issue:
            self.owner._update(pid,sid,context_authority_issue=issue)
            self.owner._event(pid,sid,'CONTEXT_AUTHORITY_BLOCKED',issue)

    def _inherited(self,manifest,seen):
        if manifest.get('canonical_execution_claim') is None:return None
        runtime=self.owner.conversations
        if runtime is None:
            raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: execution configuration authority is unavailable')
        return runtime.configuration_sources(manifest,seen=seen)

    def saved(self,manifest,state,*,seen=None):
        """Fresh read-only proof for retaining a saved context in configuration.

        No live-context cache, issue/report write, source-body read or SDK
        observation is used. Unrelated epoch changes do not alter the receipt
        once all exact selections still authorize at one stable epoch.
        """
        seen=set(seen or ())
        key=(manifest['project_id'],manifest['id'])
        if key in seen or len(seen)>=64:
            raise Conflict('SOURCE_CONTEXT_CAPACITY: inherited execution authority exceeds its proof bound')
        seen.add(key)
        inherited=self._inherited(manifest,seen)
        extra={'inherited_authority_sha256':digest(inherited)} if inherited is not None else {}
        native=state.get('original_session_id')
        if not native:
            if (state.get('native_turns_started',0) or state.get('model_calls_reserved') not in {None,0} or state.get('work_mode_message')
                    or any(row.get('info',{}).get('role')=='user' for row in state.get('messages',[]))):
                raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: saved native context identity is missing')
            return {**extra,'native_context_present':False,'record_sha256':None,'source_selections_verified':0}
        if self.graph is None:
            raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: saved source authority has no metadata service')
        with self.lock:value=self._read(manifest,{'session_id':native})
        if not value['complete']:
            raise Conflict('SOURCE_CONTEXT_LEGACY: saved context requires a new empty conversation')
        for attempt in range(2):
            epoch=self.graph.authority_epoch()
            for item in value['selections']:
                if self.graph.authorize_selection(**item)['authority_epoch']!=epoch:break
            else:
                if self.graph.authority_epoch()==epoch:
                    return {**extra,'native_context_present':True,'record_sha256':digest(value),
                            'source_selections_verified':len(value['selections'])}
        raise Conflict('SOURCE_CONTEXT_CHANGED: saved source authority changed during configuration preparation')

    def admit_configuration_start(self,manifest,state):
        """Explicit start admission before SDK/MCP process creation."""
        try:
            return self.saved(manifest,state)
        except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError) as error:
            current=self.owner._state(manifest['project_id'],manifest['id'])
            if (state.get('generation',0)>=1 and state.get('original_session_id')
                    and current.get('generation')==state['generation']
                    and current.get('original_session_id')==state['original_session_id']):
                text=str(error)
                code='SOURCE_CONTEXT_LEGACY' if text.startswith('SOURCE_CONTEXT_LEGACY:') else 'SOURCE_CONTEXT_UNAVAILABLE' if isinstance(error,(NotFound,OSError,ValueError,TypeError,KeyError)) or text.startswith('SOURCE_CONTEXT_UNAVAILABLE:') else 'SOURCE_CONTEXT_CHANGED'
                issue={'code':code,'generation':state['generation'],'operation_id':current.get('active_operation_id'),
                    'scope':'FUTURE_HOST_ADMISSIONS_AND_API_RELAY','original_history_retained':True}
                if current.get('context_authority_issue')!=issue:
                    self.owner._update(manifest['project_id'],manifest['id'],context_authority_issue=issue)
                    self.owner._event(manifest['project_id'],manifest['id'],'CONTEXT_AUTHORITY_BLOCKED',issue)
            raise

    def check(self,manifest,context):
        try:
            self._inherited(manifest,{(manifest['project_id'],manifest['id'])})
        except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError) as error:
            context.pop('context_authority_cache',None)
            code='SOURCE_CONTEXT_UNAVAILABLE' if isinstance(error,(NotFound,OSError,ValueError,TypeError,KeyError)) or str(error).startswith('SOURCE_CONTEXT_UNAVAILABLE:') else 'SOURCE_CONTEXT_CHANGED'
            self._report(manifest,context,code)
            raise Conflict(code+': inherited execution source no longer admits further requests') from error
        if self.graph is None:
            context.pop('context_authority_cache',None)
            if context.get('session_id') and self._path(manifest,context).exists():
                self._report(manifest,context,'SOURCE_CONTEXT_UNAVAILABLE')
                raise Conflict('SOURCE_CONTEXT_UNAVAILABLE: recorded source authority cannot be checked without its service')
            return
        try:
            with self.lock:value=self._read(manifest,context)
            if not value['complete']:raise Conflict('SOURCE_CONTEXT_LEGACY: current authority for old context is unverified')
            marker=digest(value);epoch=self.graph.authority_epoch()
            if context.get('context_authority_cache')=={'record':marker,'epoch':epoch}:return
            for attempt in range(2):
                for item in value['selections']:
                    proof=self.graph.authorize_selection(**item)
                    if proof['authority_epoch']!=epoch:break
                else:
                    if self.graph.authority_epoch()==epoch:
                        context['context_authority_cache']={'record':marker,'epoch':epoch}
                        if self.owner._state(manifest['project_id'],manifest['id']).get('context_authority_issue'):
                            self.owner._update(manifest['project_id'],manifest['id'],context_authority_issue=None)
                        return
                epoch=self.graph.authority_epoch()
            raise Conflict('SOURCE_CONTEXT_CHANGED: source authority changed during validation')
        except (Conflict,NotFound,OSError,ValueError,TypeError,KeyError) as error:
            context.pop('context_authority_cache',None)
            code='SOURCE_CONTEXT_LEGACY' if str(error).startswith('SOURCE_CONTEXT_LEGACY:') else 'SOURCE_CONTEXT_UNAVAILABLE' if isinstance(error,(NotFound,OSError,ValueError,TypeError,KeyError)) or str(error).startswith('SOURCE_CONTEXT_UNAVAILABLE:') else 'SOURCE_CONTEXT_CHANGED'
            self._report(manifest,context,code)
            raise Conflict(code+': selected context authority no longer admits further requests; original history retained') from error
