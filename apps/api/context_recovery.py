"""Explicit source-context recovery: stop the old owner, create metadata only.

The destination is a new empty conversation. Old SDK history, pending work,
source selections, file authority and composer drafts are never transferred.
An uncertain stop is inspected by its original key, never dispatched twice.
"""
from copy import deepcopy
import re
from typing import Literal

from pydantic import Field

from apps.api.conversation_creation import CreateConversation
from packages.agent_sessions.models import SessionControl,SessionRef
from packages.domain.conversation_threads import ConversationRef
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now


class ContextIssue(RequestModel):
    code: Literal['SOURCE_CONTEXT_CHANGED','SOURCE_CONTEXT_UNAVAILABLE','SOURCE_CONTEXT_LEGACY']
    generation: int = Field(ge=1, strict=True)
    operation_id: Id | None
    scope: Literal['FUTURE_HOST_ADMISSIONS_AND_API_RELAY']
    original_history_retained: Literal[True]


class ContextRestart(SessionControl):
    conversation_ref: ConversationRef
    expected_issue: ContextIssue


class ContextPreparation(RequestModel):
    session_ref: SessionRef
    generation: int = Field(ge=1,strict=True)


class ContextRecovery:
    def __init__(self, app, canonical_owner):
        self.app,self.canonical=app,canonical_owner
        self.sessions=canonical_owner.sessions

    def prepare(self,pid,sid,body):
        """Read the exact current logical scope; do not enroll or reconnect it."""
        body=ContextPreparation.model_validate(body)
        manifest,reference=self.sessions._manifest(pid,sid,body.session_ref.model_dump())
        if manifest.get('native_review_admission') or manifest.get('native_message_admission'):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: use a new explicit native-page submission')
        state=self.sessions._state(pid,sid)
        if state['generation']!=body.generation:
            raise Conflict('SOURCE_CONTEXT_RECOVERY_CHANGED: original generation changed')
        issue=state.get('context_authority_issue')
        if issue is None:raise Conflict('SOURCE_CONTEXT_RECOVERY_NOT_NEEDED: no current source issue')
        issue=ContextIssue.model_validate(issue).model_dump()
        if issue['generation']!=body.generation:
            raise Conflict('SOURCE_CONTEXT_RECOVERY_CHANGED: source issue belongs to another generation')
        threads=self.canonical.threads
        with threads.store.connect() as connection:
            alias=threads._alias(connection,{'kind':'original','project_id':pid,'session_ref':reference})
            if alias is None:raise Conflict('SOURCE_CONTEXT_RECOVERY_NOT_BOUND: inspect the original discussion first')
            _,source=threads.checked_alias(alias)
            if source is None or source.get('session_ref')!=reference:
                raise Conflict('SOURCE_CONTEXT_RECOVERY_CHANGED: original source reference changed')
            row=threads._entity(connection,alias['conversation_id'])
            conversation={'id':row['conversation_id'],'revision':row['revision']}
        scope=self.canonical._scope(conversation)
        hint={'project_id':pid,'session_ref':reference,'generation':body.generation}
        selected=self.canonical._member(scope,hint)
        if selected is None or any(selected[k]!=hint[k] for k in ('project_id','session_ref')):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_CHANGED: original source is not current')
        token=digest([pid,reference,body.generation,state.get('original_session_id')])
        slot=self._path(pid,sid,['context',token]);existing=None
        if slot.exists():
            claim=self.sessions._read(slot)
            if (not isinstance(claim,dict) or set(claim)!={'operation_id','request_sha256'}
                    or not isinstance(claim.get('operation_id'),str)
                    or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',claim['operation_id'])
                    or not isinstance(claim.get('request_sha256'),str)
                    or not re.fullmatch(r'[a-f0-9]{64}',claim['request_sha256'])):
                raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: original recovery claim cannot be verified')
            try:original=self._read(pid,sid,claim['operation_id'])
            except NotFound as error:
                raise Conflict('SOURCE_CONTEXT_RECOVERY_PENDING: original recovery claim is being recorded') from error
            request=original['request']
            if (original['request_sha256']!=claim.get('request_sha256') or request['session_ref']!=reference
                    or request['generation']!=body.generation or request['conversation_ref']['id']!=conversation['id']
                    or original.get('native_session_id')!=state.get('original_session_id')):
                raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: original recovery claim differs')
            existing={'request':deepcopy(request),'request_sha256':original['request_sha256'],
                'target_project_id':original['creation_request']['project_id']}
        basis={'kind':'CONTEXT_RECOVERY_PREPARATION','scope':hint,'conversation_ref':conversation,
            'expected_issue':issue,'target_project_id':scope['project_id'],'existing_recovery':existing}
        return {**basis,'preparation_sha256':digest(basis),'read_only':True,'model_calls':0,
            'native_posts':0,'new_conversations':0,'permission_granted':False,'automatic_replay':False}

    def _path(self,pid,sid,key):
        return self.sessions._path(pid,sid,'context-recovery',digest(key)+'.json')

    def _save(self,path,value):
        if len(canonical({'value':value,'sha256':digest(value)}).encode())>32768:
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: recovery record exceeds its bound')
        self.sessions._write(path,value,replace=path.exists())

    def _read(self,pid,sid,key):
        path=self._path(pid,sid,['operation',key])
        if not path.exists() and not path.is_symlink():raise NotFound('No original context recovery was committed')
        if path.stat().st_size>32768:raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: original recovery record exceeds its bound')
        row=self.sessions._read(path)
        body=ContextRestart.model_validate(row['request'])
        if (row.get('schema')!='eeas.context-recovery.v1' or body.operation_id!=key
                or digest(row['request'])!=row.get('request_sha256')
                or row.get('project_id')!=pid or row.get('session_id')!=sid
                or row.get('phase') not in {'PREPARED','STOP_ATTEMPTED','STOP_CONFIRMED','COMPLETE'}):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: original recovery record differs')
        token=digest([pid,body.session_ref.model_dump(),body.generation,row.get('native_session_id')])
        creation=CreateConversation.model_validate(row['creation_request'])
        if (row.get('stop_operation_id')!='context_stop_'+token[:48]
                or creation.operation_id!='context_chat_'+token[:48] or creation.title is not None):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: recovery keys differ')
        return row

    @staticmethod
    def _stop_body(row):
        return SessionControl(operation_id=row['stop_operation_id'],session_ref=row['request']['session_ref'],
                              generation=row['request']['generation']).model_dump()

    def _stop(self,row):
        try:op=self.sessions.operation(row['project_id'],row['session_id'],row['stop_operation_id'])
        except NotFound:return None
        body=self._stop_body(row)
        if (op.get('kind')!='DISCONNECT' or op.get('request')!=body
                or op.get('request_sha256')!=digest(body) or op.get('session_ref')!=body['session_ref']
                or op.get('automatic_replay') is not False):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: original stop identity differs')
        return op

    def _stopped(self,row):
        state=self.sessions._state(row['project_id'],row['session_id'])
        if (state['generation']!=row['request']['generation']
                or state.get('original_session_id')!=row['native_session_id']
                or row['session_id'] in self.sessions.active
                or state.get('process_identity') is not None or state.get('controller_identity') is not None
                or state['owner_status'] not in {'DISCONNECTED','EXPIRED','CLOSED'}):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_PENDING: original owner closure is not confirmed')

    def _creation(self,row):
        body=CreateConversation.model_validate(row['creation_request'])
        try:value=self.app.state.conversation_creation.operation(body.operation_id)
        except NotFound:return None
        if value['request_sha256']!=digest(body.model_dump()) or value['project_id']!=body.project_id:
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: destination creation differs')
        return value

    def _public(self,row):
        stop=self._stop(row)
        confirmed=bool(stop and stop['status']=='RETURNED' and stop.get('exact_owned_process_stop_confirmed') is True)
        if confirmed and row['phase']!='COMPLETE':
            try:self._stopped(row)
            except Conflict:confirmed=False
        created=self._creation(row) if confirmed else None
        return {'version':1,'operation_id':row['request']['operation_id'],'request_sha256':row['request_sha256'],
            'project_id':row['project_id'],'session_ref':deepcopy(row['request']['session_ref']),
            'generation':row['request']['generation'],'conversation_ref':deepcopy(row['request']['conversation_ref']),
            'phase':'COMPLETE' if created else 'STOP_CONFIRMED' if confirmed else 'STOP_PENDING',
            'stop_operation_id':row['stop_operation_id'],'stop_status':stop['status'] if stop else 'NOT_FOUND',
            'creation_request':deepcopy(row['creation_request']),'creation_receipt':created,
            'original_history_retained':True,'drafts_transferred':False,'source_data_copied':False,
            'execution_started':False,'automatic_post_replay':False,'model_calls_by_recovery':0}

    def operation(self,pid,sid,key):
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',key):raise NotFound('Unknown context recovery operation')
        self.sessions._manifest(pid,sid)
        return self._public(self._read(pid,sid,key))

    async def restart(self,pid,sid,body):
        body=ContextRestart.model_validate(body)
        manifest,_=self.sessions._manifest(pid,sid,body.session_ref.model_dump())
        if manifest.get('native_review_admission') or manifest.get('native_message_admission'):
            raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: use a new explicit native-page submission')
        cid=body.conversation_ref.id
        async with self.canonical._lock(('dispatch',cid)):
            path=self._path(pid,sid,['operation',body.operation_id])
            if path.exists():
                row=self._read(pid,sid,body.operation_id)
                if row['request']!=body.model_dump():raise Conflict('IDEMPOTENCY_CONFLICT: original recovery input differs')
            else:
                scope=self.canonical._scope(body.conversation_ref.model_dump())
                source={'project_id':pid,'session_ref':body.session_ref.model_dump(),'generation':body.generation}
                member=self.canonical._member(scope,source)
                if member is None or any(member[k]!=source[k] for k in ('project_id','session_ref')):
                    raise Conflict('SOURCE_CONTEXT_RECOVERY_UNAVAILABLE: selected owner is not this conversation')
                view=self.sessions.get(pid,sid)
                if (view['generation']!=body.generation or body.expected_issue.generation!=body.generation
                        or view.get('context_authority_issue')!=body.expected_issue.model_dump()):
                    raise Conflict('SOURCE_CONTEXT_RECOVERY_CHANGED: check the current original source issue')
                token=digest([pid,body.session_ref.model_dump(),body.generation,view.get('original_session_id')])
                row={'schema':'eeas.context-recovery.v1','project_id':pid,'session_id':sid,
                    'request':body.model_dump(),'request_sha256':digest(body.model_dump()),'phase':'PREPARED',
                    'native_session_id':view.get('original_session_id'),'created_at':now(),
                    'stop_operation_id':'context_stop_'+token[:48],
                    'creation_request':{'operation_id':'context_chat_'+token[:48],'project_id':scope['project_id'],'title':None}}
                # One durable recovery key per exact native context, even across windows.
                slot=self._path(pid,sid,['context',token])
                with self.sessions.store.connect() as connection:
                    connection.execute('BEGIN IMMEDIATE')
                    if slot.exists():
                        claimed=self.sessions._read(slot)
                        if claimed!={'operation_id':body.operation_id,'request_sha256':row['request_sha256']}:
                            raise Conflict('SOURCE_CONTEXT_RECOVERY_PENDING: another original recovery key already exists')
                    else:self._save(slot,{'operation_id':body.operation_id,'request_sha256':row['request_sha256']})
                    self._save(path,row)
            if row['phase']=='PREPARED' or row['phase']=='STOP_ATTEMPTED' and self._stop(row) is None:
                row['phase']='STOP_ATTEMPTED';self._save(path,row)
                # No new execution admission, model request, or transfer of old context.
                # An explicit retry can fill a missing original stop claim.
                # controlled_disconnect itself claims this exact key once;
                # an existing uncertain claim is never dispatched again.
                await self.sessions.controlled_disconnect(pid,sid,self._stop_body(row))
            result=self._public(row)
            if result['phase']=='COMPLETE':return result
            if result['phase']!='STOP_CONFIRMED':return result
            self._stopped(row)
            self.canonical._scope(body.conversation_ref.model_dump())
            row['phase']='STOP_CONFIRMED';self._save(path,row)
            self.app.state.conversation_creation.create(CreateConversation.model_validate(row['creation_request']))
            row['phase']='COMPLETE';row['finished_at']=now();self._save(path,row)
            return self._public(row)


def register_context_recovery(app,canonical_owner):
    service=ContextRecovery(app,canonical_owner)
    app.state.context_recovery=service
    root='/api/projects/{project_id}/agent-sessions/{session_id}/context-recovery'

    @app.post(root)
    async def restart(project_id:str,session_id:str,body:ContextRestart):
        return await service.restart(project_id,session_id,body)

    @app.post(root+'/prepare')
    def prepare(project_id:str,session_id:str,body:ContextPreparation):
        return service.prepare(project_id,session_id,body)

    @app.get(root+'/{operation_id}')
    def operation(project_id:str,session_id:str,operation_id:str):
        return service.operation(project_id,session_id,operation_id)

    return service
