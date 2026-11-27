"""Private native source recovery: exact owned stop and metadata-only empty chat.

No old prompt, selected source, permission, file, or draft becomes new input.
"""
import asyncio
from copy import deepcopy
import re
from apps.api.context_recovery import ContextIssue
from apps.api.conversation_creation import CreateConversation
from packages.agent_sessions.models import SessionControl,SessionRef
from packages.domain.questions import Id,Hash,RequestModel
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import canonical,digest,now


class NativeContextPreparation(RequestModel):
    expected_native_ref: SessionRef


class NativeContextRestart(NativeContextPreparation):
    operation_id: Id
    preparation_sha256: Hash


class NativeContextRecovery:
    def __init__(self,native):
        self.native=native;self.app=native.app;self.ws=native.ws
        self.tasks=set();self.closed=False

    @property
    def sessions(self):return self.app.state.agent_sessions

    def _path(self,key):
        if not re.fullmatch(r'native_recovery_[a-f0-9]{48}',key):raise NotFound('Unknown private context recovery')
        return self.ws._path('native-context-recovery',key+'.json')

    def _record(self,parent,*,live):
        row=self.ws._read(self.native.path(parent),authority=live)
        if row['request']['operation_id']!=parent or row['request_sha256']!=digest(row['request']):
            raise Conflict('The exact native question record differs')
        if live and row['generation']!=self.native.generation:raise Conflict('Historical native authority is read-only')
        return row

    def _subject(self,parent,expected):
        record=self._record(parent,live=True);question=record['request'];owner=record.get('owner');ref=record.get('native_message_ref') or record.get('review_ref')
        if not owner or ref!=expected or owner.get('generation',0)<1:raise Conflict('The exact private native owner differs')
        pid=owner['project_id'];sid=owner['session_ref']['id'];manifest,actual=self.sessions._manifest(pid,sid,owner['session_ref'])
        ordinary=question.get('native_review') is None
        if ordinary:
            admission=self.native.ordinary.read('admissions',ref['id'])
            if (manifest.get('native_message_admission')!=ref or ref!={'id':ref['id'],'revision':1,'sha256':digest(admission)}
                    or admission['parent_operation_id']!=parent or admission['body_sha256']!=record['request_sha256']
                    or digest(admission['body'])!=record['request_sha256'] or admission['source_host_generation']!=record['generation']):
                raise Conflict('The ordinary native admission differs')
        else:
            frozen=self.native.reviews._frozen(ref)
            if frozen['request']!=question:raise Conflict('The original review input differs')
            channel=self.native.reviews.read('channels',record['channel_key'])
            selected=channel.get('owner') or {};admitted=manifest.get('native_review_admission')
            if (not admitted or selected.get('admission_ref')!=admitted or any(selected.get(k)!=owner[k] for k in ('project_id','session_ref','generation'))):
                raise Conflict('The exact private review channel differs')
            admission=self.native.reviews.read('admissions',admitted['id'])
            if admitted!={'id':admitted['id'],'revision':1,'sha256':digest(admission)} or admission['source_host_generation']!=record['generation']:
                raise Conflict('The private review owner admission differs')
        if actual!=owner['session_ref']:raise Conflict('The private original manifest differs')
        state=self.sessions._state(pid,sid)
        if state['generation']!=owner['generation']:raise Conflict('The private owner generation changed')
        issue=ContextIssue.model_validate(state.get('context_authority_issue')).model_dump()
        if issue['generation']!=owner['generation']:raise Conflict('The private source issue generation differs')
        if state.get('active_operation_id') not in {None,record.get('native_operation_id')}:
            raise Conflict('Use the current native requirement to stop its shared private context')
        from apps.api.native_messages import question_project
        origin={'project_id':question_project(question),'conversation_id':question.get('conversation_id')}
        target={'project_id':origin['project_id'],'conversation_ref':None}
        threads=getattr(self.app.state,'conversation_threads',None)
        if threads is not None and origin['conversation_id']:
            with threads.store.connect() as connection:
                alias=threads._alias(connection,{'kind':'native',**origin})
                if alias is not None:
                    row=threads._entity(connection,alias['conversation_id'])
                    if row['deleted_at'] is not None:raise NotFound('The original conversation was deleted')
                    target={'project_id':row['project_id'],'conversation_ref':{'id':row['conversation_id'],'revision':row['revision']}}
        # One recovery key per exact owner generation, including a native ID
        # first becoming known while that same generation is being stopped.
        token=digest([record['generation'],pid,owner['session_ref'],owner['generation']])
        return {'parent_operation_id':parent,'native_ref':deepcopy(ref),'host_generation':record['generation'],
            'parent_request_sha256':record['request_sha256'],'owner':deepcopy(owner),'native_session_id':state.get('original_session_id'),
            'issue':issue,'origin':origin,'target':target,'token':token,'kind':'ORDINARY' if ordinary else 'REVIEW'}

    def _read(self,parent,key):
        path=self._path(key)
        if not path.exists():raise NotFound('The original private recovery record was not found')
        row=self.ws._read(path,authority=False)
        request=NativeContextRestart.model_validate(row['request']).model_dump();subject=row['subject']
        if (row.get('schema')!='eeas.native-context-recovery.v1' or row['parent_operation_id']!=parent
                or request['operation_id']!=key or row['request_sha256']!=digest(request)
                or row['subject_sha256']!=digest(subject) or request['expected_native_ref']!=subject['native_ref']
                or key!='native_recovery_'+subject['token'][:48]
                or subject['token']!=digest([subject['host_generation'],subject['owner']['project_id'],subject['owner']['session_ref'],subject['owner']['generation']])
                or row['phase'] not in {'PREPARED','STOP_ATTEMPTED','COMPLETE'}
                or row['creation_request']!={'operation_id':'native_context_chat_'+subject['token'][:48],'project_id':subject['target']['project_id'],'title':None}):
            raise Conflict('The original private recovery record differs')
        original=self._record(parent,live=False)
        if original['request_sha256']!=subject['parent_request_sha256'] or original.get('owner')!=subject['owner']:
            raise Conflict('The original private recovery subject differs')
        return row

    def _save(self,row):
        if len(canonical(row).encode())>32768:raise Conflict('Private context recovery exceeds its record bound')
        self.ws._write(self._path(row['request']['operation_id']),row)

    def _stop_body(self,row):
        owner=row['subject']['owner']
        return SessionControl(operation_id='native_context_stop_'+row['subject']['token'][:48],
            session_ref=owner['session_ref'],generation=owner['generation']).model_dump()

    def _stop(self,row):
        owner=row['subject']['owner'];request=self._stop_body(row)
        try:value=self.sessions.operation(owner['project_id'],owner['session_ref']['id'],request['operation_id'])
        except NotFound:return None
        if (value.get('kind')!='DISCONNECT' or value.get('request')!=request or value.get('request_sha256')!=digest(request)
                or value.get('session_ref')!=request['session_ref'] or value.get('automatic_replay') is not False):
            raise Conflict('The original private stop identity differs')
        return value

    def _stopped(self,row):
        subject=row['subject'];owner=subject['owner'];state=self.sessions._state(owner['project_id'],owner['session_ref']['id'])
        self.sessions._manifest(owner['project_id'],owner['session_ref']['id'],owner['session_ref'])
        return (state['generation']==owner['generation'] and (subject['native_session_id'] is None or state.get('original_session_id')==subject['native_session_id'])
            and owner['session_ref']['id'] not in self.sessions.active and state.get('process_identity') is None
            and state.get('controller_identity') is None and state['owner_status'] in {'DISCONNECTED','EXPIRED','CLOSED'})

    def _creation(self,row):
        request=CreateConversation.model_validate(row['creation_request'])
        try:value=self.app.state.conversation_creation.operation(request.operation_id)
        except NotFound:return None
        if value['request_sha256']!=digest(request.model_dump()) or value['project_id']!=request.project_id:
            raise Conflict('The original private recovery conversation differs')
        return value

    def _public(self,row):
        stop=self._stop(row);confirmed=bool(stop and stop['status']=='RETURNED' and stop.get('exact_owned_process_stop_confirmed') is True and self._stopped(row))
        created=self._creation(row) if confirmed else None
        return {'kind':'NATIVE_CONTEXT_RECOVERY','parent_operation_id':row['parent_operation_id'],'operation_id':row['request']['operation_id'],
            'request_sha256':row['request_sha256'],'expected_native_ref':deepcopy(row['request']['expected_native_ref']),
            'origin':deepcopy(row['subject']['origin']),'owner_binding_sha256':digest(row['subject']['owner']),
            'phase':'COMPLETE' if created else 'STOP_CONFIRMED' if confirmed else 'STOP_PENDING',
            'stop_operation_id':self._stop_body(row)['operation_id'],'stop_status':stop['status'] if stop else 'NOT_FOUND',
            'creation_request':deepcopy(row['creation_request']),'creation_receipt':created,
            'original_history_retained':True,'drafts_transferred':False,'source_data_copied':False,'execution_started':False,
            'external_actions_rolled_back':False,'automatic_post_replay':False,'model_calls_by_recovery':0}

    def prepare(self,parent,body):
        body=NativeContextPreparation.model_validate(body);subject=self._subject(parent,body.expected_native_ref.model_dump());key='native_recovery_'+subject['token'][:48]
        existing=None;path=self._path(key)
        if path.exists():
            saved=self.ws._read(path,authority=False);row=self._read(saved['parent_operation_id'],key)
            if row['subject']['token']!=subject['token']:raise Conflict('The private recovery context differs')
            existing={'parent_operation_id':row['parent_operation_id'],'request':deepcopy(row['request']),'request_sha256':row['request_sha256']}
        authority={'parent_operation_id':parent,'expected_native_ref':body.expected_native_ref.model_dump(),'subject_sha256':digest(subject),
            'operation_id':key,'origin':subject['origin'],'target':subject['target'],'issue':subject['issue'],'existing_recovery':existing}
        target_pid=subject['target']['project_id'];label=self.app.state.store.project(target_pid)['name'] if target_pid is not None else None
        return {'kind':'NATIVE_CONTEXT_RECOVERY_PREPARATION',**authority,'preparation_sha256':digest(authority),'target_label':label,
            'read_only':True,'model_calls':0,'native_posts':0,'new_conversations':0,'permission_granted':False,'automatic_post_replay':False}

    def operation(self,parent,key):return self._public(self._read(parent,key))

    async def restart(self,parent,body):
        body=NativeContextRestart.model_validate(body);task=asyncio.current_task();self.tasks.add(task)
        try:
            if self.closed:raise Conflict('The private recovery service is closed')
            async with self.app.state.canonical_conversations._lock(('private-context',body.operation_id)):
                path=self._path(body.operation_id)
                if path.exists():
                    row=self._read(parent,body.operation_id)
                    if row['request']!=body.model_dump():raise Conflict('IDEMPOTENCY_CONFLICT: private recovery confirmation differs')
                else:
                    prepared=self.prepare(parent,{'expected_native_ref':body.expected_native_ref.model_dump()})
                    if prepared['operation_id']!=body.operation_id or prepared['preparation_sha256']!=body.preparation_sha256:
                        raise Conflict('The private recovery preparation changed; old input is retained')
                    subject=self._subject(parent,body.expected_native_ref.model_dump())
                    if prepared['subject_sha256']!=digest(subject):raise Conflict('The exact private owner changed')
                    row={'schema':'eeas.native-context-recovery.v1','parent_operation_id':parent,'request':body.model_dump(),
                        'request_sha256':digest(body.model_dump()),'subject':subject,'subject_sha256':digest(subject),'phase':'PREPARED','created_at':now(),
                        'creation_request':{'operation_id':'native_context_chat_'+subject['token'][:48],'project_id':subject['target']['project_id'],'title':None}}
                    self._save(row)
                stop=self._stop(row)
                if stop is None:
                    # A previous host cannot create a fresh stopping authority.
                    current=self._subject(parent,row['request']['expected_native_ref'])
                    if current['token']!=row['subject']['token']:raise Conflict('The private owner generation changed before stop')
                    row['phase']='STOP_ATTEMPTED';self._save(row);owner=row['subject']['owner']
                    await self.sessions.controlled_disconnect(owner['project_id'],owner['session_ref']['id'],self._stop_body(row))
                result=self._public(row)
                if result['phase']!='STOP_CONFIRMED':return result
                # This metadata target was explicitly confirmed before stopping.
                # No old scope/content or permissions become new execution.
                if not self._stopped(row):raise Conflict('The exact private owner closure changed')
                self.app.state.conversation_creation.create(CreateConversation.model_validate(row['creation_request']))
                row['phase']='COMPLETE';row['finished_at']=now();self._save(row);return self._public(row)
        finally:self.tasks.discard(task)

    def project(self,record,result):
        owner=record.get('owner');ref=record.get('native_message_ref') or record.get('review_ref')
        if not owner or not ref:return result
        try:
            state=self.sessions._state(owner['project_id'],owner['session_ref']['id'])
            issue=ContextIssue.model_validate(state.get('context_authority_issue')).model_dump()
            if state['generation']!=owner['generation'] or issue['generation']!=owner['generation']:return result
        except (Conflict,NotFound,ValueError,TypeError,KeyError,OSError):return result
        # Projection is saved metadata, not a capability/source re-admission.
        result['native_context_issue']=issue
        result['native_context_recovery']={'parent_operation_id':record['request']['operation_id'],'expected_native_ref':deepcopy(ref),
            'record_current':record['generation']==self.native.generation,'read_only':True,'model_calls':0}
        if isinstance(result.get('control_capabilities'),dict):result['control_capabilities']['can_guide']=False
        if isinstance(result.get('review'),dict):
            result['review']['can_guide']=False;result['review']['process_permission_current']=False
        return result

    async def close(self):
        self.closed=True;pending=[t for t in self.tasks if t is not asyncio.current_task() and not t.done()]
        for task in pending:task.cancel()
        if pending:await asyncio.gather(*pending,return_exceptions=True)
