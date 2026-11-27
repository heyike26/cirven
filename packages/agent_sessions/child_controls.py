"""User continuation of an admitted original child on its existing owner.

The original engine still owns every model/tool iteration. This adapter shares
the root operation barrier, budgets, pending controls, TTL and sole monitor.
"""
from datetime import datetime, timedelta, timezone

from pydantic import Field

from packages.adapters.subscription_runtime import is_subscription
from packages.domain.questions import RequestModel, Id, Hash
from packages.domain.store import Conflict
from packages.domain.tasks import digest, now
from .models import SessionControl
from .child_observations import ChildObservations
from .delegated_guidance import DelegatedGuidance


class ChildRef(RequestModel):
    id: Id
    root_native_session_id: Id
    sha256: Hash


class ChildMessage(SessionControl):
    child_ref: ChildRef
    raw_text: str = Field(min_length=1, max_length=16000)
    timeout_seconds: int = Field(default=300, ge=10, le=300, strict=True)
    expected_active_operation_id: Id | None = None


class ChildControls:
    def __init__(self, owner):
        self.owner = owner
        self.sources = ChildObservations(owner)
        self.delegated = DelegatedGuidance(owner)

    def target(self, pid, sid, context, operation):
        """Captured target for observation/stop; never replace a root operation."""
        if operation.get('kind') != 'CHILD_MESSAGE':
            return context['session_id']
        child = operation.get('child_ref') or {}
        request = operation.get('request') or {}
        _, actual = self.owner._manifest(pid,sid,operation.get('session_ref'))
        if (operation.get('generation') != context['generation']
                or operation.get('session_ref')!=actual
                or digest(request)!=operation.get('request_sha256') or request.get('session_ref')!=operation.get('session_ref')
                or request.get('generation')!=context['generation'] or request.get('child_ref')!=child
                or child.get('root_native_session_id') != context['session_id']
                or child.get('id') != operation.get('original_session_id')
                or child.get('id') not in context['known_sessions'] or child['id'] == context['session_id']):
            raise Conflict('Captured child operation no longer belongs to this original owner')
        return child['id']

    async def submit(self, pid, sid, body):
        from .service import _native_message_id, TERMINAL
        owner, value = self.owner, ChildMessage.model_validate(body)
        reference = value.child_ref.model_dump()
        # Recovery remains readable after child metadata changes. Fresh dispatch
        # checks that changing basis under the existing owner lock below.
        manifest, _ = owner._manifest(pid,sid,value.session_ref.model_dump())
        if manifest.get('native_review_admission') or manifest.get('native_message_admission'):
            raise Conflict('Private native tasks use their existing frozen input protocol')
        kind = 'CHILD_GUIDANCE' if value.expected_active_operation_id is not None else 'CHILD_MESSAGE'
        record, created = owner._claim(pid, sid, value.operation_id, kind, value.model_dump())
        if not created:
            return record
        reserved = False
        try:
            from .task_resources import claimed_read_scope
            with claimed_read_scope(owner,manifest,value.operation_id):
                await owner.editor_captures.broker.admit(manifest,generation=value.generation,
                    operation_id=value.operation_id,kind=kind)
                manifest, context = owner._context(pid,sid,value.session_ref.model_dump(),value.generation)
                async with context['lock']:
                    _, current = owner._context(pid,sid,value.session_ref.model_dump(),value.generation)
                    if current is not context:
                        raise Conflict('Original child dispatch owner changed while waiting')
                    self.sources._basis(pid,sid,value.session_ref.model_dump(),value.generation,reference)
                    child = await owner._http(context,'GET','/session/'+reference['id'],timeout=3)
                    if (child.get('id') != reference['id'] or child.get('parentID') != context['session_id']
                            or child.get('directory') != context['directory']):
                        raise Conflict('Original child dispatch lineage or workspace changed')
                    messages = await owner._http(context,'GET','/session/'+reference['id']+'/message',params={'limit':'40'},timeout=3)
                    self.sources._validate_messages(messages,reference['id'])
                    agents = [message['info'].get('agent') for message in messages if message['info'].get('role') == 'user']
                    agent = agents[-1] if agents else child.get('agent')
                    admitted = {'plan','build','explore',*[row['alias'] for row in context.get('workflow',{}).get('aliases',[])]}
                    if agent not in admitted:
                        raise Conflict('The original child agent configuration was not observed or admitted')
                    statuses = await owner._http(context,'GET','/session/status',timeout=3)
                    if not isinstance(statuses,dict):
                        raise Conflict('Original owner status is unavailable')
                    parent_id = value.expected_active_operation_id
                    state = owner._state(pid,sid)
                    parent = owner.operation(pid,sid,parent_id) if parent_id else None
                    delegated = None
                    if parent_id:
                        if parent.get('kind') == 'MESSAGE':
                            root_messages = await owner._http(context,'GET','/session/'+context['session_id']+'/message',
                                params={'limit':'200'},timeout=3)
                            delegated = self.delegated.basis(pid,sid,context,parent,reference,root_messages,agent)
                            users = [message for message in messages if message['info'].get('role') == 'user']
                            if not users:raise Conflict('The foreground child input was not observed')
                            delegated.update(child_user_message_id=users[-1]['info']['id'],child_user_message_sha256=digest(users[-1]))
                            existing = self.delegated.records(pid,sid,context,parent)
                            prior = [record['id'] for record in existing]
                            if (len(prior)>=32 or len({record['original_session_id'] for record in existing}|{reference['id']})>8
                                    or any(record['status'] not in {'ACCEPTED','RETURNED','COHORT_RETURNED','CANCELLED','FAILED'}
                                        or record['status']=='FAILED' and record.get('native_post_attempted') is not False
                                            and record.get('native_dispatch_acknowledged') is not True for record in existing)):
                                raise Conflict('Original delegated guidance is full or has an unresolved dispatch')
                        else:
                            if (state.get('active_operation_id') != parent_id or parent.get('kind') != 'CHILD_MESSAGE'
                                    or parent.get('status') != 'ACCEPTED' or self.target(pid,sid,context,parent) != reference['id']
                                    or parent.get('session_ref') != value.session_ref.model_dump()):
                                raise Conflict('Child guidance requires the exact acknowledged active child message')
                            prior = state.get('child_guidance_operation_ids',[]) if state.get('child_guidance_parent') == parent_id else []
                            if len(prior)>=32 or any(owner.operation(pid,sid,key)['status'] != 'ACCEPTED' for key in prior):
                                raise Conflict('Original child guidance is full or has an unresolved dispatch')
                    else:
                        if (state.get('active_operation_id') or state.get('pending_questions') or state.get('pending_permissions')
                                or any(statuses.get(key,{'type':'idle'}).get('type') != 'idle' for key in context['known_sessions'])):
                            raise Conflict('Continue an idle original child only after the current owner work is resolved')
                    native_id = _native_message_id()
                    expires = datetime.fromisoformat(context['expires_at'])
                    deadline = min(expires,datetime.now(timezone.utc)+timedelta(seconds=value.timeout_seconds)).isoformat()
                    with owner.store.connect() as db:
                        db.execute('BEGIN IMMEDIATE')
                        state = owner._state(pid,sid)
                        owner._editor_assert(manifest,connection=db)
                        if state.get('active_operation_id') != parent_id:
                            raise Conflict('Original owner work changed before child dispatch reservation')
                        count = state.get('child_turns_started',0) if state.get('child_turns_generation') == value.generation else 0
                        if count>=32:
                            raise Conflict('Original child user-turn allowance is exhausted for this generation')
                        if is_subscription(context['profile']):
                            if state.get('native_turns_started',0)>=manifest['request']['max_calls']:
                                raise Conflict('Original shared user-turn allowance is exhausted')
                            state['native_turns_started']=state.get('native_turns_started',0)+1
                        elif state['model_calls_reserved']>=manifest['request']['max_calls']:
                            raise Conflict('Original shared model-call allowance is exhausted')
                        state.update(child_turns_started=count+1,child_turns_generation=value.generation)
                        if delegated is not None:
                            state.update(delegated_guidance_parent=parent_id,delegated_guidance_ids=[*prior,value.operation_id])
                        elif parent_id:
                            state.update(child_guidance_parent=parent_id,child_guidance_operation_ids=[*prior,value.operation_id])
                        else:
                            state.update(active_operation_id=value.operation_id,child_guidance_parent=value.operation_id,
                                child_guidance_operation_ids=[],child_operation_observation=None,
                                active_child_operation={'operation_id':value.operation_id,'generation':value.generation,
                                    'child_ref':reference,'status':'DISPATCHING'})
                        owner._write(owner._path(pid,sid,'state.json'),state,replace=True)
                    reserved = True
                    owner._finish(pid,sid,value.operation_id,'DISPATCHING',generation=value.generation,
                        child_ref=reference,original_session_id=reference['id'],root_original_session_id=context['session_id'],
                        original_active_operation_id=parent_id,native_message_id=native_id,native_dispatch_acknowledged=False,
                        native_post_attempted=False,dispatch_at=now(),deadline=deadline,
                        agent=agent,model_resolution='ORIGINAL_ADMITTED_CHILD_AGENT_CONFIG',
                        **({'delegated_parent':True,'delegation_basis':delegated} if delegated is not None else {}),
                        semantic_acceptance=False,independent_verification='NOT_RUN')
                    if parent_id:
                        owner._finish(pid,sid,parent_id,'ACCEPTED',deadline=max(parent['deadline'],deadline),last_guidance_operation_id=value.operation_id)
                    from .resource_retention import dispatch_scope
                    with dispatch_scope(owner,manifest,context,owner.operation(pid,sid,value.operation_id)):
                        payload={'messageID':native_id,'agent':agent,'parts':[{'type':'text','text':value.raw_text}]}
                        owner._finish(pid,sid,value.operation_id,'DISPATCHING',native_post_attempted=True)
                        await owner._http(context,'POST','/session/'+reference['id']+'/prompt_async',payload,expected=204)
                    latest = owner._state(pid,sid)
                    if (owner.active.get(sid) is not context or context.get('closing') or latest.get('generation') != value.generation
                            or latest.get('active_operation_id') != (parent_id or value.operation_id)):
                        raise Conflict('Original child dispatch owner changed before acknowledgement')
                    result = owner._finish(pid,sid,value.operation_id,'ACCEPTED',native_dispatch_acknowledged=True)
                    summary = dict(latest.get('active_child_operation') or {})
                    if summary.get('operation_id')==(parent_id or value.operation_id):
                        summary.update(status='ACCEPTED',**({'guidance_operation_id':value.operation_id,'guidance_status':'ACCEPTED'} if parent_id else {}))
                        owner._update(pid,sid,active_child_operation=summary)
                    return result
        except BaseException as error:
            latest = owner.operation(pid,sid,value.operation_id)
            if latest['status'] not in TERMINAL:
                status = 'UNKNOWN_OUTCOME' if latest.get('native_post_attempted') is True else 'FAILED'
                owner._finish(pid,sid,value.operation_id,status,error_type=type(error).__name__,native_post_attempted=latest.get('native_post_attempted') is True)
                state = owner._state(pid,sid)
                summary = dict(state.get('active_child_operation') or {})
                if reserved and summary.get('operation_id')==(value.expected_active_operation_id or value.operation_id):
                    summary.update(**({'guidance_operation_id':value.operation_id,'guidance_status':status}
                        if kind=='CHILD_GUIDANCE' else {'status':status}))
                    owner._update(pid,sid,active_child_operation=summary)
                if reserved and status=='FAILED' and kind=='CHILD_MESSAGE':
                    state = owner._state(pid,sid)
                    if state.get('active_operation_id')==value.operation_id:
                        owner._update(pid,sid,active_operation_id=None)
            raise

    async def messages(self,pid,sid,context,statuses,pending):
        state = self.owner._state(pid,sid)
        key = state.get('active_operation_id')
        operation = self.owner.operation(pid,sid,key) if key else None
        if not operation or operation.get('kind') != 'CHILD_MESSAGE':
            return None
        target = self.target(pid,sid,context,operation)
        if statuses.get(target,{'type':'idle'}).get('type') != 'idle' and not any(row.get('sessionID')==target for row in pending):
            return None
        messages = await self.owner._http(context,'GET','/session/'+target+'/message',params={'limit':'200'},timeout=3)
        if not isinstance(messages,list) or len(messages)>200:
            raise Conflict('Active original child message window exceeds its bound')
        for start in range(0,len(messages),40):
            self.sources._validate_messages(messages[start:start+40],target)
        return messages

    async def observe(self,pid,sid,context,messages,statuses,*,quiescent,barrier,can_release_interrupt):
        owner = self.owner
        state = owner._state(pid,sid)
        key = state.get('active_operation_id')
        operation = owner.operation(pid,sid,key) if key else None
        if not operation or operation.get('kind') != 'CHILD_MESSAGE':
            return False
        target = self.target(pid,sid,context,operation)
        pending = [row for row in [*state.get('pending_questions',[]),*state.get('pending_permissions',[])]
            if row.get('native_request',{}).get('sessionID')==target]
        if operation['status']=='ACCEPTED' and quiescent:
            self._wait(pid,sid,context,operation,pending,statuses,target)
            operation = owner.operation(pid,sid,key)
        if messages is None or not quiescent or operation['status'] not in {'ACCEPTED','UNKNOWN_OUTCOME'}:
            return True
        ids = state.get('child_guidance_operation_ids',[]) if state.get('child_guidance_parent')==key else []
        records = [operation,*[owner.operation(pid,sid,ident) for ident in ids]]
        if len(records)>33 or len(set(ids))!=len(ids) or any(record.get('generation')!=context['generation'] or record.get('original_session_id')!=target
                or record.get('session_ref')!=operation['session_ref'] or digest(record.get('request'))!=record.get('request_sha256')
                or index>0 and (record.get('kind')!='CHILD_GUIDANCE' or record.get('original_active_operation_id')!=key
                    or record.get('child_ref')!=operation['child_ref'])
                or record.get('native_dispatch_acknowledged') is not True for index,record in enumerate(records)):
            return True
        for record in records:
            users = [row for row in messages if row['info'].get('id')==record.get('native_message_id') and row['info'].get('role')=='user']
            if len(users)!=1 or ''.join(part.get('text','') for part in users[0]['parts'] if part.get('type')=='text')!=record['request']['raw_text']:
                return True
        idle = all(statuses.get(key,{'type':'idle'}).get('type')=='idle' for key in context['known_sessions'])
        assistants = [row for row in messages if row['info'].get('role')=='assistant']
        if not idle or pending or any(not row['info'].get('time',{}).get('completed') or
                any(part.get('type')=='tool' and part.get('state',{}).get('status') in {'pending','running'} for part in row['parts']) for row in assistants):
            return True
        stopped = False
        if barrier and can_release_interrupt and context.get('interrupt_barrier') is barrier and barrier.get('target_operation_id')==key:
            stop = owner.operation(pid,sid,barrier['operation_id'])
            stopped = bool(stop.get('status')=='RETURNED' and stop.get('original_abort_acknowledged') is True
                and stop.get('target_native_session_id')==target and stop.get('generation')==context['generation'])
        finals = [row for row in assistants if row['info'].get('parentID')==records[-1]['native_message_id']
            and (row['info'].get('finish')=='stop' or row['info'].get('error'))]
        rejected = [row for row in assistants if row['info'].get('parentID')==records[-1]['native_message_id'] and
            any(part.get('type')=='tool' and part.get('state',{}).get('status')=='error' for part in row['parts'])]
        rejection = bool(operation.get('user_reject_acknowledged') and rejected)
        if not stopped and (operation['status']=='UNKNOWN_OUTCOME' or not finals and not rejection):
            return True
        if operation['status']=='UNKNOWN_OUTCOME':
            # Separate stop evidence releases the input barrier without relabeling
            # the original unknown dispatch as successful or safe to resend.
            owner._update(pid,sid,active_operation_id=None,active_child_operation=None,child_operation_observation={
                'operation_id':key,'status':'STOP_OBSERVED_ORIGINAL_UNKNOWN_PRESERVED','original_session_id':target,
                'stop_operation_id':barrier['operation_id'],'messages_sha256':digest(messages),'observed_at':now()})
            return True
        errors = [row['info']['error'] for row in finals if row['info'].get('error')]
        status = 'CANCELLED' if stopped or rejection and not finals or errors and all(error.get('name')=='MessageAbortedError' for error in errors) else 'FAILED' if errors else 'RETURNED'
        for record in records[1:]:
            if record['status']=='ACCEPTED':
                owner._finish(pid,sid,record['id'],status if record is records[-1] else 'COHORT_RETURNED',
                    linked_latest_operation_id=records[-1]['id'],semantic_acceptance=False,independent_verification='NOT_RUN')
        completed = owner._finish(pid,sid,key,status,native_assistant_ids=[row['info']['id'] for row in (finals or rejected)],
            continuation_guidance_ids=ids,response_evidence='EXACT_ORIGINAL_CHILD_COHORT',
            messages_sha256=digest(messages),semantic_acceptance=False,independent_verification='NOT_RUN')
        # The existing account qualifier is root-message specific. Preserve its
        # prior evidence rather than relabeling a child alias/model as that root.
        owner._update(pid,sid,active_operation_id=None,active_child_operation=None,child_operation_observation={'operation_id':key,'status':status,
            'original_session_id':target,'native_assistant_ids':completed['native_assistant_ids'],'observed_at':now()})
        owner._event(pid,sid,'ORIGINAL_CHILD_MESSAGE_SETTLED',{'operation_id':key,'status':status,'original_session_id':target})
        return True

    def _wait(self,pid,sid,context,operation,pending,statuses,target):
        if any(statuses.get(key,{'type':'idle'}).get('type')!='idle' for key in context['known_sessions'] if key!=target):
            pending = []  # End a prior pause when other owner work resumes.
        previous = operation.get('human_wait',{})
        stamp = datetime.now(timezone.utc)
        deadline = datetime.fromisoformat(operation['deadline'])
        if pending and (deadline>stamp or previous.get('status')=='WAITING_FOR_USER'):
            refs=[row['request_ref'] for row in pending]
            if previous.get('status')=='WAITING_FOR_USER' and previous.get('requests')==refs:
                return
            self.owner._finish(pid,sid,operation['id'],operation['status'],human_wait={'status':'WAITING_FOR_USER',
                'since':previous.get('since',stamp.isoformat()),'requests':refs,'coverage':'EXACT_ACTIVE_CHILD_REQUESTS',
                'deadline_before_wait':operation['deadline'],'owner_ttl_extended':False})
        elif previous.get('status')=='WAITING_FOR_USER':
            elapsed=max(0.,(stamp-datetime.fromisoformat(previous['since'])).total_seconds())
            extension=elapsed if previous.get('deadline_before_wait')==operation['deadline'] else 0.
            resumed=min(datetime.fromisoformat(context['expires_at']),deadline+timedelta(seconds=extension))
            self.owner._finish(pid,sid,operation['id'],operation['status'],deadline=resumed.isoformat(),human_wait={
                'status':'RUNNING','completed_wait_seconds':elapsed,'resumed_at':stamp.isoformat(),'owner_ttl_extended':False})
