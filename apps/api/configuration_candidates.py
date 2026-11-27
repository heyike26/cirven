"""Recover an existing committed configuration actor; never allocate another.

Observation is saved metadata only. Explicit continuation may use the existing
LOAD journal and fresh original inspect, then publish this exact candidate.
"""
from copy import deepcopy
from datetime import datetime, timezone
from packages.agent_sessions.models import LoadSession,OpenConversation
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import digest


READY='CANDIDATE_READY'
CLOSED='CANDIDATE_CLOSED'


class ConfigurationCandidates:
    def __init__(self, canonical):
        self.c=canonical
        self.sessions=canonical.sessions

    def _task_running(self, row):
        r=row['request'];task=self.c.inflight.get(('connection',r['project_id'],r['operation_id']))
        return task is not None and not task.done()

    def _candidate(self, row, receipt):
        proof=row.get('configuration_confirmation');r=row['request'];value=receipt.get('session')
        if not proof or not value or receipt.get('request_sha256')!=row['request_sha256']:
            raise Conflict('The committed configuration candidate identity is unavailable')
        pid=value['project_id'];ref=value['session_ref']
        if proof['execution_project_id'] is not None and pid!=proof['execution_project_id']:
            raise Conflict('The candidate execution project differs from its confirmation')
        manifest,actual=self.sessions._manifest(pid,ref['id'])
        expected={'operation_id':r['operation_id'],'project_id':r['project_id'],
            'confirmation_sha256':proof['confirmation_sha256'],'proof_sha256':digest(proof)}
        native={k:v for k,v in r.items() if k not in {'operation_id','project_id'}}
        if (actual!=ref or manifest.get('request')!=native or manifest.get('canonical_execution_claim')!=expected
                or manifest.get('native_message_admission') or manifest.get('native_review_admission')):
            raise Conflict('The committed candidate is not the exact original confirmed public actor')
        state=self.sessions._state(pid,ref['id'])
        self.sessions._admit_native_initialization(manifest,state)
        if state['generation']==0 and state.get('original_session_id') is not None:
            raise Conflict('A generation-zero candidate cannot already carry a native session identity')
        if state.get('generation')!=value['generation']:
            raise Conflict('The committed candidate generation changed during observation')
        return manifest,state,value

    def _unused(self, view, state, row=None):
        if (view.get('active_operation_id') is not None or view.get('pending_questions') or view.get('pending_permissions')
                or state.get('active_operation_id') is not None or state.get('pending_questions') or state.get('pending_permissions')
                or state.get('native_turns_started',0)!=0 or state.get('model_calls_reserved') not in {None,0}
                or state.get('work_mode_message') or state.get('children')
                or any(m.get('info',{}).get('role')=='user' for m in state.get('messages',[]))):
            raise Conflict('The candidate has original user work; inspect that work instead of continuing configuration')
        folder=self.sessions._path(view['project_id'],view['session_ref']['id'],'operations')
        adopted=[]
        if not folder.exists():return adopted
        for count,path in enumerate(folder.iterdir(),1):
            if count>256:raise Conflict('Candidate original operation proof exceeds its bound')
            op=self.sessions._read(path)
            linked_load=bool(row and view.get('connected_owner') is True and view.get('owner_status')=='READY'
                and view.get('observation_is_current') is True and op.get('kind')=='LOAD'
                and any(step.get('load_operation_id')==op.get('id') and step.get('candidate',{}).get('session_ref')==view['session_ref']
                    and step.get('candidate',{}).get('generation',-2)+1==state['generation']
                    for step in row.get('candidate_continuations',[])))
            if (op.get('session_ref')!=view['session_ref'] or op.get('request_sha256')!=digest(op.get('request'))
                    or op.get('status') not in {'RETURNED','FAILED','CANCELLED'} and not linked_load
                    or op.get('kind') in {'MESSAGE','GUIDANCE'}):
                raise Conflict('A candidate original operation is unknown, unfinished or already used')
            if linked_load and op.get('status') not in {'RETURNED','FAILED','CANCELLED'}:
                adopted.append({'operation_id':op['id'],'request_sha256':op['request_sha256'],
                    'observed_generation':state['generation'],'original_session_id':state.get('original_session_id')})
        return adopted

    def _head(self, row):
        head=self.c._owner_head(row['conversation_ref']['id'])
        if head is not None and head['request']!=row['request']:
            raise Conflict('Another configuration is pending or current; do not reactivate a historical actor')
        return head

    def observe(self, row, receipt):
        """No SDK refresh/start/bind or mutation, including failure reporting."""
        if self._task_running(row):return {'status':'PROCESSING'}
        try:
            self._head(row);manifest,state,view=self._candidate(row,receipt);self._unused(view,state,row)
            if not view.get('configuration_current'):
                raise Conflict('The exact candidate configuration is no longer current')
            self.sessions.context_authority.saved(manifest,state)
            if (view.get('connected_owner') is True and view.get('owner_status')=='READY'
                    and view.get('observation_is_current') is True and view.get('native_status')=='idle'
                    and state.get('original_session_id') and state['generation']>=1):
                return {'status':READY,'candidate_generation':state['generation']}
            if (view.get('connected_owner') is False and view.get('owner_status') in {'DISCONNECTED','EXPIRED','BLOCKED'}
                    and state.get('owner_status') in {'DISCONNECTED','EXPIRED','BLOCKED'}
                    and state.get('process_identity') is None and state.get('controller_identity') is None
                    and not state.get('observation_error') and not state.get('native_messages_error')
                    and (state['generation']==0 and state.get('original_session_id') is None
                         or bool(state.get('original_session_id'))
                         or state.get('native_initialization')=={'generation':state['generation'],
                             'manifest_sha256':digest(manifest),'stage':'NATIVE_NOT_ATTEMPTED','original_session_id':None})):
                return {'status':CLOSED,'candidate_generation':state['generation']}
        except (Conflict,NotFound,OSError,KeyError,TypeError):
            pass
        return {'status':'UNKNOWN_OUTCOME'}

    def _receipt(self, row):
        proof=row['configuration_confirmation']
        return self.sessions.open_operation(row['request']['operation_id'],proof['execution_project_id'])

    def _closed_previous(self, row):
        proof=row['configuration_confirmation'];source=proof['authority']['original_source']
        if source is None:return
        if row.get('closed_previous_owner')!={'original_source':source,'owner_closed':True,'process_identity':None,'controller_identity':None}:
            raise Conflict('The exact previous owner closure has not been established')
        view=self.c._view(source)
        if self.c._source(view)!=source:raise Conflict('The previous original generation changed')
        if view.get('connected_owner') is not False or view.get('process_identity') is not None or view.get('controller_identity') is not None:
            raise Conflict('The previous exact original owner is no longer confirmed closed')
        self.c._known_idle_owner(view,require_auth_failure=False,allow_configuration_change=True)
        manifest,_=self.sessions._manifest(source['project_id'],source['session_ref']['id'])
        if self.sessions.context_authority.saved(manifest,view)!=proof['authority']['source_authority']:
            raise Conflict('SOURCE_CONTEXT_CHANGED: previous saved authority changed before candidate continuation')

    async def continue_candidate(self, row, admit):
        c=self.c;r=row['request'];cid=r['conversation_ref']['id'];path=c._path('connections',[r['project_id'],r['operation_id']])
        async with c._lock(('conversation',cid)):
            row=c._read(path)
            receipt=self._receipt(row);status=self.observe(row,receipt)['status']
            if status not in {READY,CLOSED}:return c.open_operation(r['operation_id'],r['project_id'])
            c._available();scope=c.configuration_scope(row);self._head(row)
            manifest,state,view=self._candidate(row,receipt);self._closed_previous(row)
            head=self._head(row)
            if head is None:
                allowed=[c._source(view)]
                previous=row['configuration_confirmation']['authority']['original_source']
                if previous is not None:allowed.append(previous)
                if any((s.get('original_source') or {}).get('kind')=='original' and not any(
                        all(s['original_source'][key]==source[key] for key in ('project_id','session_ref')) for source in allowed)
                        for s in scope['sources']):
                    raise Conflict('Another original actor became registered; candidate continuation needs explicit current authority')
            admit(OpenConversation.model_validate(r))
            history=row.get('candidate_continuations',[])
            if len(history)>=32:raise Conflict('Candidate continuation attempt bound reached; original evidence retained')
            # A previous canonical attempt never commits a user prompt here.
            step={'candidate':c._source(view),'action':'LOAD_SAME_CANDIDATE' if status==CLOSED else 'PUBLISH_SAME_CANDIDATE',
                'requested_at':datetime.now(timezone.utc).isoformat(),'phase':'ATTEMPTED'}
            if status==CLOSED:
                step['load_operation_id']='configuration_load_'+digest([r['project_id'],r['operation_id'],view['session_ref'],view['generation'],len(history)])[:48]
            row['candidate_continuations']=[*history,step]
            c._save(path,row,replace=True)
            token=('connection',r['project_id'],r['operation_id'])
            import asyncio
            c.inflight[token]=asyncio.current_task()
            try:
                # Register only this already allocated manifest. Exact alias
                # replay is metadata only and never creates another SDK owner.
                self.sessions.conversations.bind_open(manifest)
                if status==CLOSED:
                    body=LoadSession(operation_id=step['load_operation_id'],session_ref=view['session_ref'],
                        expected_generation=view['generation'] if view['generation']>0 else None)
                    loaded=await self.sessions.load(view['project_id'],view['session_ref']['id'],body,
                        configuration_generation_zero=view['generation']==0)
                    op=loaded['operation'];expected_request=body.model_dump(exclude_none=True)
                    if (op.get('id')!=step['load_operation_id'] or op.get('kind')!='LOAD'
                            or op.get('session_ref')!=view['session_ref'] or op.get('request')!=expected_request
                            or op.get('request_sha256')!=digest(expected_request)
                            or loaded['operation']['status']!='RETURNED'
                            or loaded['operation'].get('loaded_generation')!=state['generation']+1):
                        raise Conflict('The exact candidate LOAD is not confirmed; inspect its original key')
                    step['load_receipt']={key:deepcopy(loaded['operation'][key]) for key in ('id','kind','session_ref','request_sha256','status','loaded_generation')}
                fresh=await self.sessions.inspect(view['project_id'],view['session_ref']['id'])
                if (fresh['session_ref']!=view['session_ref'] or fresh['project_id']!=view['project_id']
                        or state.get('original_session_id') and fresh.get('original_session_id')!=state['original_session_id']
                        or fresh['connected_owner'] is not True or fresh['owner_status']!='READY'
                        or fresh['observation_is_current'] is not True or fresh['native_status']!='idle'
                        or not fresh.get('original_session_id')):
                    raise Conflict('The exact original candidate is not freshly ready')
                latest=self.sessions._state(fresh['project_id'],fresh['session_ref']['id'])
                step['adopted_load_receipts']=self._unused(fresh,latest,row);self.sessions.context_authority.saved(manifest,latest)
                self._closed_previous(row);c.configuration_scope(row);head=self._head(row)
                if not c._bound(c._source(fresh),cid):raise Conflict('The original candidate alias was not registered')
                if head is None:
                    allowed_parent=row['configuration_confirmation']['authority']['original_source']
                    others=[s for s in scope['sources'] if (s.get('original_source') or {}).get('kind')=='original'
                        and (s['original_source']['project_id']!=fresh['project_id'] or s['original_source']['session_ref']!=fresh['session_ref'])
                        and not (allowed_parent is not None and all(s['original_source'][key]==allowed_parent[key] for key in ('project_id','session_ref')))]
                    if others:raise Conflict('Another original actor was registered; no candidate was selected by guess')
                    row.setdefault('provider_generation',1)
                step.update(phase='PUBLISHED',finished_at=datetime.now(timezone.utc).isoformat())
                row.update(source=c._source(fresh),phase='BOUND',finished_at=step['finished_at'])
                with c.threads.store.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE');c.configuration_scope(row);self._head(row)
                    c._save(path,row,replace=True)
                    c._save(c._path('current-owners',cid),{**row,'kind':'PROVIDER_GENERATION'},replace=head is not None)
            except BaseException as error:
                step.update(phase='UNKNOWN',error_type=type(error).__name__,finished_at=datetime.now(timezone.utc).isoformat())
                c._save(path,row,replace=True)
                raise
            finally:c.inflight.pop(token,None)
            return c.open_operation(r['operation_id'],r['project_id'])
