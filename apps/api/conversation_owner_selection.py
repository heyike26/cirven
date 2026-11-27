"""Explicit shared legacy owner choice. Metadata only; never open/load/send/stop."""
from copy import deepcopy
import json
from datetime import datetime, timezone
from apps.api.canonical_conversations import OriginalOwner
from packages.domain.conversation_threads import ConversationRef, MAX_SOURCES
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest, canonical


class OwnerPreparation(RequestModel):
    conversation_ref: ConversationRef
    project_id: Id | None = None


class SelectOriginalOwner(OwnerPreparation):
    operation_id: Id
    original_source: OriginalOwner
    expected_selection_operation_id: Id | None = None


class ConversationOwnerSelection:
    def __init__(self, canonical):
        self.canonical = canonical

    def _scope(self, request):
        scope=self.canonical._scope(request.conversation_ref.model_dump())
        if scope['project_id']!=request.project_id:
            raise Conflict('Original owner selection organization changed')
        return scope

    def _checked(self, scope, source):
        c=self.canonical
        if not any((row.get('original_source') or {}).get('kind')=='original'
                and all(row['original_source'][key]==source[key] for key in ('project_id','session_ref'))
                for row in scope['sources']):
            raise Conflict('Select an exact original owner registered in this conversation')
        manifest,ref=c.sessions._manifest(source['project_id'],source['session_ref']['id'])
        if (ref!=source['session_ref'] or manifest.get('native_review_admission')
                or manifest.get('native_message_admission')):
            raise Conflict('Private or changed execution cannot become a public owner selection')
        value=c._view(source)
        if value['generation']<1:
            raise Conflict('This original candidate has not completed its first owner admission')
        return {**value,'title':manifest['request'].get('title') or value.get('original_session_id') or value['session_ref']['id']}

    def _record(self, connection, cid, operation_id):
        row=connection.execute('SELECT * FROM conversation_owner_selections WHERE conversation_id=? AND operation_id=?',
            (cid,operation_id)).fetchone()
        if row is None:raise NotFound('No original owner selection was committed')
        request=json.loads(row['request_json'])
        if (digest(request)!=row['request_sha256'] or request['conversation_ref']['id']!=cid
                or request['operation_id']!=operation_id
                or SelectOriginalOwner.model_validate(request).model_dump()!=request):
            raise Conflict('The original owner selection record integrity differs')
        return {**dict(row),'request':request}

    def current(self, scope):
        c=self.canonical;cid=scope['conversation_ref']['id']
        with c.threads.store.connect() as connection:
            pointer=connection.execute('SELECT operation_id FROM conversation_selected_owners WHERE conversation_id=?',(cid,)).fetchone()
            if pointer is None:return None
            selected=self._record(connection,cid,pointer['operation_id'])
        try:value=self._checked(scope,selected['request']['original_source'])
        except (Conflict,NotFound,OSError):
            return {'status':'SELECTION_REQUIRED','selection_operation_id':pointer['operation_id']}
        return {'status':'SELECTED','selection_operation_id':pointer['operation_id'],
            'original_source':c._source(value),'provider_profile_ref':deepcopy(value['provider_profile_ref'])}

    def prepare(self, body):
        body=OwnerPreparation.model_validate(body);scope=self._scope(body);c=self.canonical
        current=c.current_owner(scope);candidates=[]
        sources=[row['original_source'] for row in scope['sources']
            if (row.get('original_source') or {}).get('kind')=='original']
        if len(sources)>MAX_SOURCES:raise Conflict('Original owner catalogue exceeds its bound')
        for source in sources:
            try:
                value=self._checked(scope,source)
                candidates.append({'original_source':c._source(value),'provider_profile_ref':value['provider_profile_ref'],
                    'owner_status':value['owner_status'],'configuration_current':value['configuration_current'],
                    'connected_owner':value['connected_owner'],'active_operation_id':value.get('active_operation_id'),
                    'title':str(value.get('title') or value['session_ref']['id'])[:200]})
            except (Conflict,NotFound,OSError):
                # Missing/unknown evidence remains in the source directory; it
                # is not replaced with a selectable invented generation.
                candidates.append({'original_source':{key:deepcopy(source[key]) for key in ('project_id','session_ref','generation')},
                    'unavailable':True})
        return {'kind':'ORIGINAL_OWNER_PREPARATION','conversation_ref':scope['conversation_ref'],
            'project_id':scope['project_id'],'current_owner':current,'candidates':candidates,
            'selection_allowed':c._owner_head(scope['conversation_ref']['id']) is None,
            'read_only':True,'model_calls':0,'owner_started':False,'permission_granted':False}

    async def select(self, body):
        body=SelectOriginalOwner.model_validate(body);c=self.canonical;request=body.model_dump()
        cid=body.conversation_ref.id
        async with c._lock(('conversation',cid)):
            with c.threads.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                exists=connection.execute('SELECT 1 FROM conversation_owner_selections WHERE conversation_id=? AND operation_id=?',
                    (cid,body.operation_id)).fetchone()
                if exists:
                    saved=self._record(connection,cid,body.operation_id)
                    if saved['request']!=request:raise Conflict('IDEMPOTENCY_CONFLICT: original owner selection changed')
                    return self._result(connection,saved)
                c._available()
                entity=c.threads._entity(connection,cid)
                if (entity['deleted_at'] is not None or entity['revision']!=body.conversation_ref.revision
                        or entity['project_id']!=body.project_id):
                    raise Conflict('Original owner selection scope changed')
                if c._owner_head(cid) is not None:
                    raise Conflict('A configured or pending owner has its original operation; historical selection cannot replace it')
                previous=connection.execute('SELECT operation_id FROM conversation_selected_owners WHERE conversation_id=?',(cid,)).fetchone()
                if (previous['operation_id'] if previous else None)!=body.expected_selection_operation_id:
                    raise Conflict('Another original owner choice became current; input retained')
                scope=c.threads._scope(connection,entity)
                source=body.original_source.model_dump();value=self._checked(scope,source)
                if c._source(value)!=source:raise Conflict('The selected original generation changed')
                raw=canonical(request)
                if len(raw.encode('utf-8'))>8192:raise Conflict('Original owner selection exceeds its metadata bound')
                connection.execute('INSERT INTO conversation_owner_selections VALUES (?,?,?,?,?)',
                    (cid,body.operation_id,raw,digest(request),datetime.now(timezone.utc).isoformat()))
                connection.execute('INSERT INTO conversation_selected_owners VALUES (?,?) '
                    'ON CONFLICT(conversation_id) DO UPDATE SET operation_id=excluded.operation_id',(cid,body.operation_id))
                return self._result(connection,self._record(connection,cid,body.operation_id))

    def _result(self, connection, row):
        c=self.canonical;request=row['request'];cid=request['conversation_ref']['id']
        pointer=connection.execute('SELECT operation_id FROM conversation_selected_owners WHERE conversation_id=?',(cid,)).fetchone()
        return {'kind':'ORIGINAL_OWNER_SELECTION','operation_id':request['operation_id'],'request_sha256':row['request_sha256'],
            'conversation_ref':request['conversation_ref'],'project_id':request['project_id'],
            'original_source':deepcopy(request['original_source']),'selection_saved':True,
            'selection_applied':bool(pointer and pointer['operation_id']==request['operation_id'] and c._owner_head(cid) is None),
            'model_calls':0,'owner_started':False,'permission_granted':False,'automatic_message_replay':False}

    def operation(self, cid, operation_id):
        with self.canonical.threads.store.connect() as connection:
            return self._result(connection,self._record(connection,cid,operation_id))


def register_owner_selection(app, canonical):
    service=ConversationOwnerSelection(canonical);canonical.owner_selection=service
    app.state.conversation_owner_selection=service

    @app.post('/api/conversations/{cid}/original-owner/prepare')
    def prepare(cid: str, body: OwnerPreparation):
        if body.conversation_ref.id!=cid:raise Conflict('Original owner preparation belongs to another conversation')
        return service.prepare(body)

    @app.post('/api/conversations/{cid}/original-owner')
    async def select(cid: str, body: SelectOriginalOwner):
        if body.conversation_ref.id!=cid:raise Conflict('Original owner selection belongs to another conversation')
        return await service.select(body)

    @app.get('/api/conversations/{cid}/original-owner-operations/{operation_id}')
    def operation(cid: str, operation_id: str):
        return service.operation(cid,operation_id)

    return service
