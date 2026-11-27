"""Explicit metadata acknowledgement of a pending configuration in current scope.

The original request/proof/model/workspace/tools/budget stay immutable. This
service records organization authority only; continuation uses the old journal.
"""
from copy import deepcopy
from datetime import datetime,timezone
import json
from packages.domain.conversation_threads import ConversationRef
from packages.domain.questions import Id,Hash,RequestModel
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import canonical,digest
from apps.api.conversation_configuration import ConfigurationContinuation


class AdoptionPreparation(RequestModel):
    original: ConfigurationContinuation
    conversation_ref: ConversationRef
    project_id: Id | None = None


class AdoptConfiguration(RequestModel):
    operation_id: Id
    preparation: AdoptionPreparation
    preparation_sha256: Hash
    confirmation_sha256: Hash


def confirmation_value(value):
    return {k:v for k,v in value.items() if k!='confirmation_sha256'}


class ConfigurationAdoptions:
    def __init__(self,canonical_service):
        self.c=canonical_service

    def _original(self, original, cid):
        c=self.c;row=c._read(c._path('connections',[original['project_id'],original['operation_id']]))
        proof=row.get('configuration_confirmation')
        if (row['conversation_ref']['id']!=cid or not proof or row['request_sha256']!=original['request_sha256']
                or proof['confirmation_sha256']!=original['confirmation_sha256']):
            raise Conflict('Adopt the exact original configuration operation and confirmation')
        return row

    def _record(self, connection, cid, key):
        row=connection.execute('SELECT * FROM configuration_adoptions WHERE conversation_id=? AND operation_id=?',(cid,key)).fetchone()
        if row is None:raise NotFound('No original configuration adoption was committed')
        request=json.loads(row['request_json']);authority=json.loads(row['authority_json'])
        if (digest(request)!=row['request_sha256'] or digest(authority)!=row['authority_sha256']
                or AdoptConfiguration.model_validate(request).model_dump()!=request
                or request['preparation_sha256']!=row['authority_sha256']
                or request['confirmation_sha256']!=digest(confirmation_value(request))
                or request['preparation']['conversation_ref']['id']!=cid or request['operation_id']!=key
                or request['preparation']['original']!=authority['original']):
            raise Conflict('The configuration adoption record integrity differs')
        return {**dict(row),'request':request,'authority':authority}

    def _pointer(self, connection, cid, original):
        row=connection.execute('SELECT adoption_operation_id FROM configuration_adopted_scopes '
            'WHERE conversation_id=? AND original_project_key=? AND original_operation_id=?',
            (cid,original['project_id'] or '',original['operation_id'])).fetchone()
        return self._record(connection,cid,row['adoption_operation_id']) if row else None

    @staticmethod
    def reference(row):
        return {'operation_id':row['request']['operation_id'],'project_id':row['request']['project_id'],
            'request_sha256':row['request_sha256'],'confirmation_sha256':row['configuration_confirmation']['confirmation_sha256']}

    def admission(self,row):
        """Verified saved acknowledgement. Caller checks its current scope CAS."""
        original=self.reference(row);cid=row['conversation_ref']['id']
        with self.c.threads.store.connect() as connection:saved=self._pointer(connection,cid,original)
        if saved is None:return None
        authority=saved['authority'];proof=row['configuration_confirmation'];request=row['request']
        if (authority['original']!=original or authority['original_request_sha256']!=digest(request)
                or authority['configuration_proof_sha256']!=digest(proof)
                or authority['execution_project_id']!=proof['execution_project_id']
                or authority['original_source']!=proof['authority']['original_source']
                or authority['conversation_ref']!=saved['request']['preparation']['conversation_ref']
                or authority['project_id']!=saved['request']['preparation']['project_id']
                or authority['conversation_ref']['id']!=cid):
            raise Conflict('The adopted scope cannot change the original execution authority')
        return {'operation_id':saved['operation_id'],'conversation_ref':deepcopy(authority['conversation_ref']),
            'project_id':authority['project_id'],'authority_sha256':saved['authority_sha256'],
            'execution_project_id':authority['execution_project_id']}

    def metadata(self,row):
        admitted=self.admission(row)
        if admitted is None:return None
        try:
            current=self.c.threads.view(row['conversation_ref']['id'])
            valid=current['conversation_ref']==admitted['conversation_ref'] and current['project_id']==admitted['project_id']
        except (Conflict,NotFound,OSError):valid=False
        try:
            head=self.c._owner_head(row['conversation_ref']['id'])
            if head is not None and head['request']!=row['request']:valid=False
        except (Conflict,NotFound,OSError):valid=False
        return {**admitted,'current':valid}

    def prepare(self,body):
        body=AdoptionPreparation.model_validate(body);request=body.model_dump();c=self.c;cid=body.conversation_ref.id
        row=self._original(request['original'],cid);proof=row['configuration_confirmation']
        scope=c._scope(request['conversation_ref'])
        if scope['project_id']!=body.project_id:raise Conflict('The current configuration adoption project changed')
        head=c._owner_head(cid)
        observed=c.open_operation(request['original']['operation_id'],request['original']['project_id'])
        allowed=observed.get('status') in {'FAILED_NOT_STARTED','CANDIDATE_READY','CANDIDATE_CLOSED'}
        reason=None
        if observed.get('conversation_bound'):allowed=False;reason='ALREADY_CURRENT'
        if head is not None and head['request']!=row['request']:allowed=False;reason='OTHER_CONFIGURATION'
        if request['conversation_ref']==row['conversation_ref'] and body.project_id==row['request']['project_id']:
            allowed=False;reason='ORIGINAL_SCOPE_CURRENT'
        source=proof['authority']['original_source'];source_authority=None;source_view=None
        if source is not None:
            source_view=c._view(source)
            if c._source(source_view)!=source:allowed=False;reason='ORIGINAL_GENERATION_CHANGED'
            manifest,_=c.sessions._manifest(source['project_id'],source['session_ref']['id'])
            source_authority=c.sessions.context_authority.saved(manifest,source_view)
            if source_authority!=proof['authority']['source_authority']:
                allowed=False;reason='SOURCE_AUTHORITY_CHANGED'
        current=c.current_owner(scope)
        candidate=observed.get('session')
        if current['status']=='SELECTED' and (not candidate or any(
                current['original_source'][k]!=candidate[k] for k in ('project_id','session_ref'))):
            allowed=False;reason='OTHER_ACTOR_SELECTED'
        with c.threads.store.connect() as connection:previous=self._pointer(connection,cid,request['original'])
        candidate_metadata=None
        if candidate:
            state=c.sessions._state(candidate['project_id'],candidate['session_ref']['id'])
            candidate_metadata={'source':c._source(candidate),'state_sha256':digest({key:state.get(key) for key in (
                'generation','owner_status','native_status','original_session_id','active_operation_id','native_turns_started',
                'model_calls_reserved','pending_questions','pending_permissions','process_identity','controller_identity','native_initialization')})}
        authority={'version':1,'original':request['original'],'original_request_sha256':digest(row['request']),
            'configuration_proof_sha256':digest(proof),'record_basis_sha256':digest(row),
            'original_conversation_ref':row['conversation_ref'],'original_project_id':row['request']['project_id'],
            'conversation_ref':scope['conversation_ref'],'project_id':scope['project_id'],
            'execution_project_id':proof['execution_project_id'],'original_source':deepcopy(source),
            'source_authority':source_authority,'candidate':candidate_metadata,
            'head_sha256':digest(head) if head else None,'status':observed.get('status'),
            'prior_adoption_operation_id':previous['operation_id'] if previous else None}
        declaration=row['request']
        summary={k:deepcopy(declaration[k]) for k in ('provider_profile_ref','workspace_ref','allowed_effects','command_allowlist',
            'mcp_tool_allowlist','ttl_seconds','max_calls','max_output_tokens','network_mode')}
        def project_label(pid):return c.threads.store.project(pid)['name'] if pid is not None else None
        summary.update(original_project_label=project_label(row['request']['project_id']),current_project_label=project_label(scope['project_id']),
            execution_project_label=project_label(proof['execution_project_id']))
        return {'kind':'CONFIGURATION_ADOPTION_PREPARATION','preparation':request,'authority':authority,
            'preparation_sha256':digest(authority),'adoption_allowed':allowed,'reason':reason,'summary':summary,
            'read_only':True,'model_calls':0,'owner_started':False,'permission_granted':False,'automatic_message_replay':False}

    async def adopt(self,body):
        body=AdoptConfiguration.model_validate(body);request=body.model_dump();cid=body.preparation.conversation_ref.id;c=self.c
        if body.confirmation_sha256!=digest(confirmation_value(request)):
            raise Conflict('Confirm the exact original configuration adoption in the current scope')
        async with c._lock(('conversation',cid)):
            with c.threads.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                exists=connection.execute('SELECT 1 FROM configuration_adoptions WHERE conversation_id=? AND operation_id=?',(cid,body.operation_id)).fetchone()
                if exists:
                    saved=self._record(connection,cid,body.operation_id)
                    if saved['request']!=request:raise Conflict('IDEMPOTENCY_CONFLICT: configuration adoption changed')
                    return self._result(connection,saved)
                c._available();prepared=self.prepare(body.preparation)
                if not prepared['adoption_allowed'] or prepared['preparation_sha256']!=body.preparation_sha256:
                    raise Conflict('Configuration adoption preparation changed; input and original evidence retained')
                authority=prepared['authority'];original=request['preparation']['original']
                connection.execute('INSERT INTO configuration_adoptions VALUES (?,?,?,?,?,?,?,?,?)',
                    (cid,body.operation_id,original['project_id'] or '',original['operation_id'],canonical(request),digest(request),
                     canonical(authority),digest(authority),datetime.now(timezone.utc).isoformat()))
                connection.execute('INSERT INTO configuration_adopted_scopes VALUES (?,?,?,?) '
                    'ON CONFLICT(conversation_id,original_project_key,original_operation_id) DO UPDATE SET adoption_operation_id=excluded.adoption_operation_id',
                    (cid,original['project_id'] or '',original['operation_id'],body.operation_id))
                return self._result(connection,self._record(connection,cid,body.operation_id))

    def _result(self,connection,row):
        request=row['request'];preparation=request['preparation'];cid=preparation['conversation_ref']['id']
        pointer=self._pointer(connection,cid,preparation['original']);scope=None
        try:scope=self.c.threads.view(cid)
        except (Conflict,NotFound,OSError):pass
        head=None
        try:head=self.c._owner_head(cid)
        except (Conflict,NotFound,OSError):scope=None
        if head is not None and digest(head['request'])!=row['authority']['original_request_sha256']:scope=None
        active=bool(pointer and pointer['operation_id']==row['operation_id'] and scope
            and scope['conversation_ref']==preparation['conversation_ref'] and scope['project_id']==preparation['project_id'])
        return {'kind':'CONFIGURATION_ADOPTION','operation_id':row['operation_id'],'request_sha256':row['request_sha256'],
            'original':preparation['original'],'conversation_ref':preparation['conversation_ref'],'project_id':preparation['project_id'],
            'preparation_sha256':request['preparation_sha256'],'confirmation_sha256':request['confirmation_sha256'],
            'accepted':True,'current':active,'model_calls':0,'owner_started':False,'permission_granted':False,'automatic_message_replay':False}

    def operation(self,cid,key):
        with self.c.threads.store.connect() as connection:return self._result(connection,self._record(connection,cid,key))


def register_configuration_adoptions(app,canonical_service):
    service=ConfigurationAdoptions(canonical_service);canonical_service.configuration_adoptions=service
    app.state.configuration_adoptions=service

    @app.post('/api/conversations/{cid}/configuration-adoption/prepare')
    def prepare(cid: str, body: AdoptionPreparation):
        if body.conversation_ref.id!=cid:raise Conflict('Configuration adoption belongs to another conversation')
        return service.prepare(body)

    @app.post('/api/conversations/{cid}/configuration-adoption')
    async def adopt(cid: str, body: AdoptConfiguration):
        if body.preparation.conversation_ref.id!=cid:raise Conflict('Configuration adoption belongs to another conversation')
        return await service.adopt(body)

    @app.get('/api/conversations/{cid}/configuration-adoption-operations/{key}')
    def operation(cid: str,key: str):return service.operation(cid,key)

    return service
