"""Canonical entry operations over the existing public AgentSessions owner.

Organization never substitutes for the execution project. Journals bind each
entry request to its exact original operation; queries cannot open or replay.
"""
import asyncio
from copy import deepcopy
import threading
from weakref import WeakValueDictionary

from pydantic import Field

from packages.agent_sessions.models import (ConfigurationRef, InterruptSession, LoadSession,
    OpenConversation, SendMessage, SessionControl, SessionRef, OriginalWorkRef, operation_request)
from packages.agent_sessions.guidance import GuidanceInput, _local_status
from packages.agent_sessions.work_context import continuation_parent
from packages.agent_sessions.outcomes import original_auth_rejection, AUTH_REJECTION_TEXT
from packages.domain.conversation_threads import ConversationRef
from packages.domain.questions import Hash, RequestModel
from packages.domain.native_input_attachments import NativeInputAttachment
from packages.domain.conversation_file_input import ConversationFileInput
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.domain.original_work_input_fingerprint import original_work_input_fingerprint


class OriginalOwner(RequestModel):
    project_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    session_ref: SessionRef
    generation: int = Field(ge=0, strict=True)


def _original_work_status_text(work):
    labels = {'ACCEPTED':'已接收','DISPATCHING':'正在发送','RUNNING':'正在执行',
              'PROCESSING':'正在处理','RETURNED':'已返回','COMPLETED':'已结束',
              'SUCCEEDED':'已成功返回','FAILED':'执行失败','STOPPED':'已停止',
              'UNKNOWN':'状态待核查','UNKNOWN_OUTCOME':'结果未确认'}
    summary = '所选工作上次保存状态：' + labels.get(work.get('status'), '状态待核查') + '。'
    if work.get('active_relation') == 'OTHER_OR_UNVERIFIED_WORK':
        summary += '当前另有活动执行，与所选工作分开显示。'
    if work.get('status') != work.get('root_status'):
        summary += '原根任务保存状态：' + labels.get(work.get('root_status'), '状态待核查') + '。'
    return summary + '这里只读取已保存记录，不代表实时进度或整项工作验收通过。'


class ConversationMessage(RequestModel):
    operation_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    conversation_ref: ConversationRef
    provider_profile_ref: ConfigurationRef
    raw_text: str = Field(min_length=1, max_length=16000)
    agent: str = Field(default='build', pattern=r'^(plan|build|workflow)$')
    original_source: OriginalOwner | None = None
    input_attachments: list[NativeInputAttachment] = Field(default_factory=list, max_length=20)
    file_input: ConversationFileInput | None = None
    context_selection_id: Hash | None = None
    original_work_ref: OriginalWorkRef | None = None


class ConversationContinuation(RequestModel):
    operation_id: str = Field(min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')
    expected_message_ref: SessionRef
    raw_text: str = Field(min_length=1, max_length=16000)


class CanonicalConversations:
    def __init__(self, app):
        self.app = app
        self.sessions = app.state.agent_sessions
        self.threads = app.state.conversation_threads
        self.runtime = app.state.conversation_runtime
        self.locks = WeakValueDictionary()
        self.lock_guard = threading.Lock()
        self.inflight = {}

    def _lock(self, key):
        with self.lock_guard:
            lock = self.locks.get(key)
            if lock is None:
                lock = self.locks[key] = asyncio.Lock()
            return lock

    @property
    def owned_tasks(self):
        return tuple(self.inflight.values())

    def _path(self, kind, key):
        return resolve_data_path('conversation-runtime/' + kind + '/' + digest(key) + '.json', self.sessions.data_dir)

    def _save(self, path, record, *, replace=False):
        if len(canonical(record).encode()) > 262144:
            raise Conflict('Canonical entry record exceeds its bound')
        self.sessions._write(path, record, replace=replace)

    def _read(self, path):
        if not path.exists():
            raise NotFound('No canonical entry operation was committed')
        record = self.sessions._read(path)
        if digest(record['request']) != record['body_sha256']:
            raise Conflict('Canonical entry request integrity differs')
        proof=record.get('configuration_confirmation')
        if proof is not None and record.get('configuration_confirmation_sha256')!=digest(proof):
            raise Conflict('Execution configuration confirmation journal integrity differs')
        return record

    def _scope(self, reference):
        scope = self.threads.view(reference['id'])
        if scope['conversation_ref'] != reference:
            raise Conflict('Conversation changed; input retained without dispatch')
        return scope

    def configuration_scope(self, row):
        """Current acknowledgement only; original request/proof remain frozen."""
        registry=getattr(self,'configuration_adoptions',None)
        admitted=registry.admission(row) if registry is not None and row.get('configuration_confirmation') else None
        scope=self._scope(admitted['conversation_ref'] if admitted else row['conversation_ref'])
        expected=admitted['project_id'] if admitted else row['request']['project_id']
        if scope['project_id']!=expected:raise Conflict('Configuration organization acknowledgement changed')
        return scope

    @staticmethod
    def _source(view):
        return {key: deepcopy(view[key]) for key in ('project_id', 'session_ref', 'generation')}

    def _member(self, scope, hint=None):
        sources = [row['original_source'] for row in scope['sources'] if (row.get('original_source') or {}).get('kind')=='original']
        current = self.current_owner(scope)
        if current['status'] in {'PENDING','SELECTION_REQUIRED'}:
            raise Conflict('Provider connection needs its original operation checked before sending')
        if current['status'] in {'CURRENT','SELECTED'}:
            selected = current['original_source']
            if hint is not None and any(selected[key] != hint[key] for key in ('project_id', 'session_ref')):
                raise Conflict('The selected original owner is historical; refresh this conversation')
            return selected
        if hint is not None:
            sources = [source for source in sources if all(source[key] == hint[key]
                       for key in ('project_id', 'session_ref'))]
            if not sources:
                raise Conflict('The selected original owner belongs to another conversation')
        if len(sources) > 1:
            raise Conflict('Choose the exact original owner; no latest-owner guess was made')
        return sources[0] if sources else None

    def _owner_head(self, cid):
        path = self._path('current-owners', cid)
        if not path.exists():
            return None
        row = self._read(path)
        if (row.get('kind') != 'PROVIDER_GENERATION' or row['request']['conversation_ref']['id'] != cid
                or type(row.get('provider_generation')) is not int or row['provider_generation'] < 1
                or row.get('phase') not in {'PREPARING', 'BOUND'}):
            raise Conflict('Current provider owner journal is unavailable; no historical fallback')
        return row

    def current_owner(self, scope):
        """Saved selection authority only; never inspect, load, connect or call a model."""
        scope = self._scope(scope['conversation_ref'])
        head = self._owner_head(scope['conversation_ref']['id'])
        if head is None:
            selection=getattr(self,'owner_selection',None)
            return (selection.current(scope) if selection is not None else None) or {'status': 'LEGACY'}
        result = {'provider_generation': head['provider_generation'],
                  'connection_operation_id': head['request']['operation_id']}
        if head['phase'] == 'PREPARING':
            return {'status': 'PENDING', **result}
        entry = self._read(self._path('connections',
            [head['request']['project_id'], head['request']['operation_id']]))
        source = head.get('source')
        if (entry['request'] != head['request'] or entry.get('provider_generation') != head['provider_generation']
                or entry['phase'] != 'BOUND' or not source or entry.get('source') != source
                or not any(row.get('original_source') and all(row['original_source'][key] == source[key]
                    for key in ('project_id', 'session_ref')) for row in scope['sources'])):
            raise Conflict('Current provider owner is not this conversation; no historical fallback')
        view = self._view(source)
        if view['provider_profile_ref'] != head['request']['provider_profile_ref']:
            raise Conflict('Current provider profile differs from its saved connection')
        return {'status': 'CURRENT', **result, 'provider_profile_ref': deepcopy(view['provider_profile_ref']),
                'original_source': self._source(view)}

    def _configuration_load_adopted(self, view, operation):
        """A published unused candidate can qualify its old LOAD observation.

        The original UNKNOWN LOAD is retained, never rewritten or replayed.
        This proof cannot qualify MESSAGE/GUIDANCE or a different native ID.
        """
        if operation.get('kind')!='LOAD':return False
        try:
            manifest,ref=self.sessions._manifest(view['project_id'],view['session_ref']['id'])
            claim=manifest.get('canonical_execution_claim')
            if ref!=view['session_ref'] or not claim:return False
            row=self._read(self._path('connections',[claim['project_id'],claim['operation_id']]))
            if (row['phase']!='BOUND' or row.get('source',{}).get('session_ref')!=view['session_ref']
                    or row.get('source',{}).get('project_id')!=view['project_id']
                    or row.get('configuration_confirmation_sha256')!=claim['proof_sha256']):return False
            return any(step.get('phase')=='PUBLISHED' and any(
                receipt.get('operation_id')==operation.get('id') and receipt.get('request_sha256')==operation.get('request_sha256')
                and receipt.get('original_session_id')==view.get('original_session_id')
                and type(receipt.get('observed_generation')) is int and 1<=receipt['observed_generation']<=view['generation']
                for receipt in step.get('adopted_load_receipts',[])) for step in row.get('candidate_continuations',[]))
        except (Conflict,NotFound,OSError,KeyError,TypeError):return False

    def _known_idle_owner(self, view, *, require_auth_failure=True, allow_configuration_change=False):
        """A stopped socket or an empty active slot alone cannot retire unknown work."""
        if (view['active_operation_id'] is not None or view['pending_questions'] or view['pending_permissions']
                or view.get('observation_error') or view.get('native_messages_error')
                or view.get('context_authority_issue') is not None and not allow_configuration_change
                or not view['configuration_current'] and not allow_configuration_change):
            raise Conflict('Current original work is not settled; keep its original operation')
        if view['connected_owner']:
            if (view['owner_status'] != 'READY' or view['native_status'] != 'idle'
                    or view.get('observation_is_current') is not True):
                raise Conflict('A fresh idle original observation is required before changing provider')
        elif (view['owner_status'] not in {'DISCONNECTED', 'EXPIRED'}
                or view.get('process_identity') is not None or view.get('controller_identity') is not None):
            raise Conflict('Original owner closure is not confirmed; no replacement was started')
        folder = self.sessions._path(view['project_id'], view['session_ref']['id'], 'operations')
        paths = []
        latest = (view.get('work_mode_message') or {}).get('operation_id')
        if not folder.exists():
            if (latest or view.get('native_turns_started',0) or view.get('model_calls_reserved') not in {None,0}
                    or any(message.get('info',{}).get('role')=='user' for message in view.get('messages',[]))):
                raise Conflict('Original operation history is missing; preserve its original outcome')
        else:
            for path in folder.iterdir():
                paths.append(path)
                if len(paths) > 256:
                    raise Conflict('Original operation history exceeds the provider-switch proof bound')
        auth_failed = False
        for path in paths:
            op = self.sessions._read(path)
            if (op.get('session_ref') != view['session_ref'] or op.get('status') not in {'RETURNED', 'FAILED', 'CANCELLED'} and not self._configuration_load_adopted(view,op)
                    or op.get('request_sha256') != digest(op.get('request'))):
                raise Conflict('An original operation is unknown or unfinished; changing provider is blocked')
            if op['kind'] in {'MESSAGE', 'GUIDANCE'}:
                rejection = original_auth_rejection(op, view.get('messages', []))
                if op['id'] == latest and rejection:
                    auth_failed = True
                if (op['status'] == 'FAILED' and op.get('native_post_attempted') is not False
                        and not op.get('individual_response_observed')
                        and not rejection):
                    raise Conflict('A dispatched original failure is not proven terminal; inspect its original key')
        if require_auth_failure and not auth_failed:
            raise Conflict('Only the latest proven original 401 permits this provider-revision recovery')

    async def _switch_provider(self, body, path, record, scope, source):
        """An explicit new revision creates an owner; immutable old owners remain sources."""
        cid = scope['conversation_ref']['id']
        async with self._lock(('dispatch', cid)):
            old = self._view(source)
            previous_profile, selected = old['provider_profile_ref'], body.provider_profile_ref.model_dump()
            proof=record.get('configuration_confirmation')
            manifest = self.sessions._manifest(old['project_id'], old['session_ref']['id'])[0]
            declared=manifest['request']
            if proof is not None:
                from apps.api.conversation_configuration import validate_confirmation
                validate_confirmation(proof,body.model_dump(),self._source(old),declared)
            elif (previous_profile==selected or previous_profile['id']==selected['id']
                    and selected['revision']<=previous_profile['revision']):
                raise Conflict('Choose another explicit connection or a newer revision of this connection')
            if proof is None and (not set(body.allowed_effects) <= set(declared['allowed_effects'])
                    or not set(body.command_allowlist) <= set(declared['command_allowlist'])
                    or body.command_observation_paths is not None
                        and body.command_observation_paths != declared.get('command_observation_paths')
                    or declared.get('command_observation_paths') is not None
                        and body.command_observation_paths is None and body.command_allowlist
                    or not set(body.mcp_tool_allowlist) <= set(declared['mcp_tool_allowlist'])
                    or body.workflow_ref is not None and body.workflow_ref.model_dump() != declared['workflow_ref']
                    or not all(ref.model_dump() in declared['workflow_provider_refs'] for ref in body.workflow_provider_refs)
                    or body.capability_binding_sha256 != declared['capability_binding_sha256']
                    or body.workspace_ref is not None and body.workspace_ref.model_dump() != declared['workspace_ref']
                    or body.mcp_tool_selection is not None and [x.model_dump() for x in body.mcp_tool_selection] != declared['mcp_tool_selection']
                    or body.max_calls > declared['max_calls'] or body.max_output_tokens > declared['max_output_tokens']
                    or body.ttl_seconds > declared['ttl_seconds']
                    or declared['network_mode'] == 'STRICT_OFFLINE' and body.network_mode != 'STRICT_OFFLINE'):
                raise Conflict('Changing provider cannot expand the original execution grants')
            await asyncio.to_thread(self.sessions.providers.resolve, selected)
            old = await self.sessions.inspect(old['project_id'], old['session_ref']['id'])
            self._known_idle_owner(old,require_auth_failure=proof is None,allow_configuration_change=proof is not None)
            if proof is not None:
                validate_confirmation(proof,body.model_dump(),self._source(old),declared)
                if self.sessions.context_authority.saved(manifest,old)!=proof['authority']['source_authority']:
                    raise Conflict('SOURCE_CONTEXT_CHANGED: saved source authority differs from the confirmed configuration')
            self.configuration_scope(record)
            execution_pid=proof['execution_project_id'] if proof is not None else body.project_id
            execution_pid=execution_pid or self.sessions.internal_project()
            kwargs={} if proof is None else {'canonical_execution_claim':{'operation_id':body.operation_id,'project_id':body.project_id,'confirmation_sha256':proof['confirmation_sha256'],'proof_sha256':digest(proof)}}
            if proof is not None:
                self.sessions.conversations.validate_open(execution_pid,body.conversation_ref.model_dump(),canonical_execution_claim=kwargs['canonical_execution_claim'],request=body.model_dump(exclude={'project_id'}))
            head_path = self._path('current-owners', cid)
            with self.threads.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                head = self._owner_head(cid)
                if head is not None and head['request']['operation_id'] == body.operation_id:
                    if head['request'] != record['request']:
                        raise Conflict('Provider-generation operation is bound to another request')
                    if head['phase'] == 'BOUND':
                        return self._view(head['source'])
                else:
                    if head is not None and (head['phase'] != 'BOUND' or any(head['source'][key] != source[key]
                            for key in ('project_id', 'session_ref'))):
                        raise Conflict('Another provider generation is pending or current; inspect its original key')
                    record.update(provider_generation=(head['provider_generation'] + 1 if head else 1),
                                  previous_source=deepcopy(source))
                    self._save(path, record, replace=True)
                    self._save(head_path, {**record, 'kind': 'PROVIDER_GENERATION', 'phase': 'PREPARING', 'source': None},
                               replace=head is not None)
            if old['connected_owner']:
                stop_key = 'provider_stop_' + digest([cid, body.operation_id])[:48]
                old = await self.sessions.controlled_disconnect(old['project_id'], old['session_ref']['id'],
                    SessionControl(operation_id=stop_key, session_ref=old['session_ref'], generation=old['generation']))
                stop = old['last_control_operation']
                if stop['status'] != 'RETURNED' or stop.get('exact_owned_process_stop_confirmed') is not True:
                    raise Conflict('Previous original owner did not close; replacement remains pending')
            self._known_idle_owner(old,require_auth_failure=proof is None,allow_configuration_change=proof is not None)
            if proof is not None:
                validate_confirmation(proof,body.model_dump(),self._source(old),declared)
                if self.sessions.context_authority.saved(manifest,old)!=proof['authority']['source_authority']:
                    raise Conflict('SOURCE_CONTEXT_CHANGED: saved source authority differs from the confirmed configuration')
            self.configuration_scope(record)
            if proof is not None:
                record['closed_previous_owner']={'original_source':self._source(old),'owner_closed':True,'process_identity':None,'controller_identity':None}
                self._save(path,record,replace=True)
            view = await self.sessions.open(execution_pid,
                                            body.model_dump(exclude={'project_id'}),**kwargs)
            self.configuration_scope(record)
            if (view['provider_profile_ref'] != selected or not view['connected_owner']
                    or view.get('observation_is_current') is not True or view['owner_status'] != 'READY'
                    or not self._bound(self._source(view), cid)):
                raise Conflict('New provider owner is not ready; inspect the original connection operation')
            record.update(source=self._source(view), phase='BOUND', finished_at=now())
            with self.threads.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                head = self._owner_head(cid)
                if head is None or head['request'] != record['request']:
                    raise Conflict('Provider-generation publication lost its exact claim')
                self._save(path, record, replace=True)
                self._save(head_path, {**record, 'kind': 'PROVIDER_GENERATION'}, replace=True)
            return view

    def _view(self, source):
        view = self.sessions.get(source['project_id'], source['session_ref']['id'])
        if view['session_ref'] != source['session_ref']:
            raise Conflict('Original owner manifest differs from its saved source')
        return view

    def _available(self):
        if self.sessions.closed:
            raise Conflict('Original owners are closing; inspect the saved entry operation')

    def _bound(self, source, cid):
        with self.threads.store.connect() as connection:
            alias = self.threads._alias(connection, {'kind': 'original', **source})
            if alias is None or alias['conversation_id'] != cid:
                return False
            _, checked = self.threads.checked_alias(alias)
            return checked['session_ref'] == source['session_ref']

    async def open(self, body, *, configuration_confirmation=None, continue_not_started=False):
        body = OpenConversation.model_validate(body)
        if body.conversation_ref is None:
            raise Conflict('Canonical connections need an exact conversation reference')
        request = body.model_dump()
        path = self._path('connections', [body.project_id, body.operation_id])
        cid = body.conversation_ref.id
        async with self._lock(('conversation', cid)):
            continuing = None
            if path.exists():
                old = self._read(path)
                if old['request'] != request:
                    raise Conflict('IDEMPOTENCY_CONFLICT: canonical connection input changed')
                if old.get('configuration_confirmation')!=configuration_confirmation:
                    raise Conflict('IDEMPOTENCY_CONFLICT: explicit configuration confirmation changed')
                if configuration_confirmation is not None and not continue_not_started:
                    # A repeated configure request only observes its original
                    # result. Starting after a failed attempt needs the separate
                    # explicit continuation admission, even for a provider head.
                    return self.open_operation(body.operation_id, body.project_id)['session']
                if continue_not_started:
                    if configuration_confirmation is None:
                        raise Conflict('Only an explicit configuration operation can be continued')
                    observed = self.open_operation(body.operation_id, body.project_id)
                    if observed.get('configuration_disposition') is not None or observed.get('status') != 'FAILED_NOT_STARTED':
                        return observed['session']
                    attempts = old.get('continuation_attempts', [])
                    if len(attempts) >= 32:
                        raise Conflict('Configuration continuation attempt bound reached; original evidence retained')
                    old['continuation_attempts'] = [*attempts, {
                        'prior_phase': old['phase'], 'prior_error_type': old.get('error_type'),
                        'prior_finished_at': old.get('finished_at'), 'requested_at': now()}]
                    # Clearing only the current failure marker makes an abrupt
                    # later loss UNKNOWN. Prior failure evidence stays above.
                    old.pop('error_type', None)
                    old.pop('finished_at', None)
                    old['phase'] = 'PREPARING'
                    continuing = old
                if old.get('provider_generation'):
                    head = self._owner_head(cid)
                    if old['phase'] == 'BOUND' and old.get('source') and not (
                            head and head['phase'] == 'PREPARING' and head['request'] == request):
                        return self._view(old['source'])
                    if head and head['phase'] == 'BOUND' and head['request'] == request:
                        return self._view(head['source'])
                    if continuing is None:
                        return await self._switch_provider(body, path, old,
                            self.configuration_scope(old), old['previous_source'])
                if continuing is None:
                    return self.open_operation(body.operation_id, body.project_id)['session']
            elif continue_not_started:
                raise Conflict('The original configuration journal is required')
            self._available()
            scope = self.configuration_scope(continuing) if continuing is not None else self._scope(body.conversation_ref.model_dump())
            if continuing is None and scope['project_id'] != body.project_id:
                raise Conflict('Connection organization differs from its conversation')
            source = continuing['previous_source'] if continuing and continuing.get('provider_generation') else self._member(scope)
            if configuration_confirmation is not None:
                from apps.api.conversation_configuration import validate_confirmation
                original=self._view(source) if source else None
                declared=self.sessions._manifest(original['project_id'],original['session_ref']['id'])[0]['request'] if original else None
                validate_confirmation(configuration_confirmation,request,self._source(original) if original else None,declared)
            # This hash deliberately follows the existing OriginalConnection
            # contract; organization is separately bound in the full body/key.
            native_request = body.model_dump(exclude={'project_id', 'operation_id'})
            record = continuing or {'kind': 'CONNECTION', 'request': request, 'body_sha256': digest(request),
                'request_sha256': digest(native_request), 'phase': 'PREPARING', 'source': None,
                'conversation_ref': scope['conversation_ref'], 'created_at': now()}
            if configuration_confirmation is not None:
                record.update(configuration_confirmation=deepcopy(configuration_confirmation),configuration_confirmation_sha256=digest(configuration_confirmation))
            self._save(path, record, replace=continuing is not None)
            token = ('connection', body.project_id, body.operation_id)
            self.inflight[token] = asyncio.current_task()
            try:
                if source is None:
                    execution_pid=configuration_confirmation['execution_project_id'] if configuration_confirmation is not None else body.project_id
                    pid = execution_pid or self.sessions.internal_project()
                    kwargs={} if configuration_confirmation is None else {'canonical_execution_claim':{'operation_id':body.operation_id,'project_id':body.project_id,'confirmation_sha256':configuration_confirmation['confirmation_sha256'],'proof_sha256':digest(configuration_confirmation)}}
                    view = await self.sessions.open(pid, body.model_dump(exclude={'project_id'}),**kwargs)
                else:
                    view = self._view(source)
                    if configuration_confirmation is not None or view['provider_profile_ref'] != body.provider_profile_ref.model_dump():
                        return await self._switch_provider(body, path, record, scope, source)
                    if not view['configuration_current']:
                        raise Conflict('Original configuration changed; input retained')
                    manifest, _ = self.sessions._manifest(view['project_id'], view['session_ref']['id'])
                    declared = manifest['request']
                    if (body.workspace_ref is not None and body.workspace_ref.model_dump() != view['workspace_ref']
                            or not set(body.allowed_effects) <= set(declared['allowed_effects'])
                            or not set(body.command_allowlist) <= set(declared['command_allowlist'])
                            or not set(body.mcp_tool_allowlist) <= set(declared['mcp_tool_allowlist'])
                            or body.workflow_ref is not None and body.workflow_ref.model_dump() != view['workflow_ref']
                            or body.network_mode == 'STRICT_OFFLINE' and declared['network_mode'] != 'STRICT_OFFLINE'):
                        raise Conflict('Requested scope would change the existing original owner')
                    if not view['connected_owner']:
                        # This is an explicit connection/new-message action,
                        # never a query or replay of an old user message.
                        loaded = await self.sessions.load(view['project_id'], view['session_ref']['id'],
                            LoadSession(operation_id=body.operation_id, session_ref=view['session_ref']))
                        view = loaded['session']
                record.update(source=self._source(view), phase='BOUND', finished_at=now())
                self._save(path, record, replace=True)
                return view
            except BaseException as error:
                record.update(phase='UNKNOWN', error_type=type(error).__name__, finished_at=now())
                self._save(path, record, replace=True)
                raise
            finally:
                self.inflight.pop(token, None)

    def open_operation(self, operation_id, project_id=None):
        row = self._read(self._path('connections', [project_id, operation_id]))
        source = row['source']
        proof=row.get('configuration_confirmation')
        confirmation_metadata={'execution_confirmation_sha256':proof['confirmation_sha256'],'execution_project_id':proof['execution_project_id'],'configuration_original_source':deepcopy(proof['authority']['original_source'])} if proof is not None else {}
        if proof is not None:
            from apps.api.configuration_disposition import configuration_disposition
            disposition=configuration_disposition(self,row)
            if disposition is not None:confirmation_metadata['configuration_disposition']=disposition
        registry=getattr(self,'configuration_adoptions',None)
        if proof is not None and registry is not None:
            admitted=registry.metadata(row)
            if admitted is not None:confirmation_metadata['configuration_adoption']=admitted
        if source is None:
            # The raw original open may have committed before this entry lost
            # its acknowledgement. Observation cannot bind, load or retry it.
            try:
                saved = self.sessions.open_operation(operation_id, proof['execution_project_id'] if proof is not None else project_id)
            except NotFound:
                task=self.inflight.get(('connection',project_id,operation_id))
                running=task is not None and not task.done()
                return {**confirmation_metadata,'version':1,'operation_id':operation_id,'request_sha256':row['request_sha256'],
                    'project_id':None,'organization_project_id':project_id,'conversation_ref':row['conversation_ref'],
                    'conversation_bound':False,'session':None,
                    'status':'PROCESSING' if running else 'FAILED_NOT_STARTED' if row.get('error_type') else 'UNKNOWN_OUTCOME',
                    'owner_started_by_query':False,'model_calls_by_query':0,
                    'source_changed_by_query':False,'automatic_post_replay':False}
            if saved['request_sha256'] != row['request_sha256']:
                raise Conflict('Original open differs from the canonical entry')
            candidate_metadata={}
            if proof is not None:
                from apps.api.configuration_candidates import ConfigurationCandidates
                candidate_metadata=ConfigurationCandidates(self).observe(row,saved)
            return {**saved, **confirmation_metadata, **candidate_metadata, **({'conversation_bound': False} if proof is not None or row.get('provider_generation') else {}),
                    'organization_project_id': project_id}
        view = self._view(source)
        current = self.current_owner(self.threads.view(row['conversation_ref']['id']))
        bound = self._bound(source, row['conversation_ref']['id'])
        if row.get('provider_generation'):
            bound = (current['status'] == 'CURRENT' and
                     current['connection_operation_id'] == row['request']['operation_id'])
        candidate_metadata={}
        if proof is not None and not bound:
            from apps.api.configuration_candidates import ConfigurationCandidates
            candidate_metadata=ConfigurationCandidates(self).observe(row,{'session':view,'request_sha256':row['request_sha256']})
        return {**confirmation_metadata,**candidate_metadata,'version': 1, 'operation_id': operation_id, 'request_sha256': row['request_sha256'],
            'project_id': source['project_id'], 'organization_project_id': project_id,
            'conversation_ref': row['conversation_ref'],
            'conversation_bound': bound, 'session': view,
            'owner_started_by_query': False, 'model_calls_by_query': 0,
            'source_changed_by_query': False, 'automatic_post_replay': False}

    def has_open_operation(self, operation_id, project_id=None):
        return self._path('connections', [project_id, operation_id]).exists()

    def original_admission(self, manifest, operation):
        """Internal proof, never accepted as a caller-supplied grant or flag."""
        reference = operation['request'].get('conversation_ref')
        if reference is None:
            return None
        path = self._path('messages', [reference['id'], operation['id']])
        if not path.exists():
            return None  # Legacy original sends retain their existing guard.
        row = self._read(path)
        source = row['source']
        if (source is None or source['project_id'] != manifest['project_id']
                or source['session_ref'] != {'id': manifest['id'], 'revision': 1, 'sha256': digest(manifest)}
                or source['generation'] != operation['request']['generation']
                or row['request']['conversation_ref'] != reference
                or row['request']['raw_text'] != operation['request']['raw_text']
                or row['dispatch_request_sha256'] != operation['request_sha256']
                or row['phase'] not in {'DISPATCHING', 'OBSERVED', 'UNKNOWN'}
                or not self._bound(source, reference['id'])):
            raise Conflict('Canonical entry does not authorize this exact original operation')
        return {'entry_request_sha256': row['body_sha256'], 'source': deepcopy(source),
            'conversation_ref': deepcopy(reference),
            'policy': 'READONLY_ORGANIZATION_CONTEXT_EXISTING_EXECUTION_PERMISSIONS_UNCHANGED'}

    async def continuation(self, cid, parent_id, value):
        body = ConversationContinuation.model_validate(value)
        reference = body.expected_message_ref.model_dump()
        if reference['id'] != parent_id or reference['revision'] != 1 or not body.raw_text.strip():
            raise Conflict('Exact original entry and nonempty continuation are required')
        saved = self._path('messages', [cid, body.operation_id])
        if saved.exists():
            request = self._read(saved)['request']
            if request.get('parent_message_ref') != reference or request['raw_text'] != body.raw_text:
                raise Conflict('IDEMPOTENCY_CONFLICT: continuation input changed')
            return self.operation(cid, body.operation_id)
        parent = self._read(self._path('messages', [cid, parent_id]))
        if parent['body_sha256'] != reference['sha256'] or parent['source'] is None:
            raise Conflict('Original entry reference differs; keep the continuation')
        source = parent['source']
        original = self.sessions.operation(source['project_id'], source['session_ref']['id'], parent_id)
        if original['request_sha256'] != parent['dispatch_request_sha256']:
            raise Conflict('Original continuation target differs from its saved dispatch')
        from packages.agent_sessions.task_resources import for_operation
        from packages.agent_sessions.canonical_resources import resource_value
        original_manifest=self.sessions._manifest(source['project_id'],source['session_ref']['id'])[0]
        resource=resource_value(for_operation(self.sessions,original_manifest,original))
        scope = self.threads.view(cid)
        request = parent['request']
        selected=self._member(scope)
        if selected is not None and any(selected[k]!=source[k] for k in ('project_id','session_ref')):
            # Explicit new input follows only a confirmed configuration chain.
            # Old attachments/file grants are not resubmitted to the new owner.
            from packages.agent_sessions.canonical_resources import replacement_path
            target=self._view(selected)
            replacement_path(self,self._source(target),source,cid)
            if target.get('active_operation_id') is not None:
                raise Conflict('Current owner is working on another operation; continuation retained')
            return await self.send(cid,ConversationMessage(operation_id=body.operation_id,
                conversation_ref=scope['conversation_ref'],provider_profile_ref=target['provider_profile_ref'],
                raw_text=body.raw_text,agent=request['agent'],original_source=self._source(target)),
                parent={'reference':reference,'row':parent,'original':original,'replacement':True,'resource':resource})
        return await self.send(cid, ConversationMessage(operation_id=body.operation_id,
            conversation_ref=scope['conversation_ref'], provider_profile_ref=request['provider_profile_ref'],
            raw_text=body.raw_text, agent=request['agent'], original_source=source,
            input_attachments=request['input_attachments'], file_input=request.get('file_input')),
            parent={'reference': reference, 'row': parent, 'original': original,'resource':resource})

    async def send(self, cid, value, *, parent=None):
        body = ConversationMessage.model_validate(value)
        if body.conversation_ref.id != cid or not body.raw_text.strip():
            raise Conflict('Exact conversation and nonempty input are required')
        request = body.model_dump()
        if request.get('file_input') is None:
            request.pop('file_input', None)  # Preserve legacy idempotency bodies.
        if request.get('context_selection_id') is None:
            request.pop('context_selection_id', None)
        if request.get('original_work_ref') is None:
            request.pop('original_work_ref', None)
        if parent is not None:
            request['parent_message_ref'] = deepcopy(parent['reference'])
        path = self._path('messages', [cid, body.operation_id])
        async with self._lock(('message', cid, body.operation_id)):
            if path.exists():
                old = self._read(path)
                if old['request'] != request:
                    raise Conflict('IDEMPOTENCY_CONFLICT: canonical message input changed')
                return self.operation(cid, body.operation_id)
            self._available()
            scope = self._scope(body.conversation_ref.model_dump())
            work_status=body.original_work_ref is not None and _local_status(body.raw_text)
            hint = body.original_source.model_dump() if body.original_source else None
            member = self._member(scope,hint) if body.original_work_ref is not None else None
            if body.original_work_ref is not None and member is None:
                raise Conflict('An existing original owner is required for a selected work; no owner was started')
            if not work_status:
                self.runtime.native.input_sources.validate(body.model_dump()['input_attachments'])
                profile = await asyncio.to_thread(self.sessions.providers.resolve, body.provider_profile_ref.model_dump())
                if any(item.kind == 'SCREENSHOT' for item in body.input_attachments) and not profile.supports_images:
                    raise Conflict('The selected model does not support screenshot input')
            if body.original_work_ref is None:member = self._member(scope, hint)
            if hint is not None and self._view(member)['generation'] != hint['generation']:
                raise Conflict('The selected original generation changed; input retained')
            row = {'kind': 'MESSAGE', 'request': request, 'body_sha256': digest(request),
                'conversation_ref': scope['conversation_ref'], 'phase': 'PREPARING',
                'source': None, 'dispatch_request_sha256': None, 'created_at': now()}
            if parent is not None:
                row['work_resource_lineage']={'version':1,'parent_message_ref':deepcopy(parent['reference']),
                    'resource':deepcopy(parent['resource'])}
            self._save(path, row)
            token = ('message', cid, body.operation_id)
            self.inflight[token] = asyncio.current_task()
            try:
                if member is None:
                    # Connection admission takes conversation -> dispatch locks.
                    # Never enter it while holding dispatch (including a first-
                    # connection race that selects another provider revision).
                    await self.open(OpenConversation(operation_id='connect_' + digest([cid, body.operation_id])[:48],
                        project_id=scope['project_id'], conversation_ref=body.conversation_ref,
                        provider_profile_ref=body.provider_profile_ref,
                        capability_binding_sha256=self.sessions.configuration()['capability_binding_sha256']))
                async with self._lock(('dispatch', cid)):
                    # A switch may have completed while this message awaited its
                    # dispatch lock. Its old hint never becomes current by arrival order.
                    member = self._member(self._scope(body.conversation_ref.model_dump()), hint or member)
                    if member is None:
                        raise Conflict('Original connection is not bound; inspect its original connection key')
                    view = self._view(member)
                    if hint is not None and view['generation'] != hint['generation']:
                        raise Conflict('The selected original generation changed before admission')
                    if view['provider_profile_ref'] != body.provider_profile_ref.model_dump():
                        raise Conflict('Model selection differs from the original owner')
                    if parent is not None:
                        basis = parent['original']
                        root = continuation_parent(basis)
                        active = view['active_operation_id']
                        if (active is not None and active != root
                                or active is None and body.conversation_ref.model_dump() != parent['row']['conversation_ref']):
                            raise Conflict('Current work differs from the displayed original task; keep the input')
                        self._scope(body.conversation_ref.model_dump())
                        if not view['configuration_current']:
                            raise Conflict('The original configuration changed; keep the continuation')
                    saved_status = (parent is not None or body.original_work_ref is not None) and _local_status(body.raw_text)
                    if not view['connected_owner'] and not saved_status:
                        # Resume this exact owner for this new user input only.
                        # Existing entry keys return above without reaching load.
                        view = (await self.sessions.load(view['project_id'], view['session_ref']['id'],
                            LoadSession(operation_id='resume_' + digest([cid, body.operation_id])[:48],
                                        session_ref=view['session_ref'])))['session']
                    admitted_source = self._source(view)
                    view = self._view(admitted_source)
                    if view['generation'] != admitted_source['generation']:
                        raise Conflict('The original generation changed before dispatch; input retained')
                    self._available()
                    # Recheck after a potentially slow owner startup; a move or
                    # delete cannot silently redirect this already-entered input.
                    self._scope(body.conversation_ref.model_dump())
                    source = self._source(view)
                    if not self._bound(source, cid):
                        raise Conflict('Original owner is no longer bound to this conversation')
                    shared = {'operation_id': body.operation_id, 'session_ref': source['session_ref'],
                        'generation': source['generation'], 'raw_text': body.raw_text,
                        'conversation_ref': body.conversation_ref, 'input_attachments': body.input_attachments,
                        'file_input': body.file_input}
                    active = view['active_operation_id']
                    if body.original_work_ref is not None:shared['original_work_ref']=body.original_work_ref
                    if body.context_selection_id and not work_status:
                        if active:
                            raise Conflict('New context selection needs a new message; the current task is still active')
                        shared['context_selection_id'] = body.context_selection_id
                    if parent is not None:
                        basis = parent['original']
                        root = continuation_parent(basis)
                        if (active is not None and active != root
                                or active is None and body.conversation_ref.model_dump() != parent['row']['conversation_ref']):
                            raise Conflict('Current work differs from the displayed original task; keep the input')
                        from packages.agent_sessions.work_context import continuation_ref
                        manifest=self.sessions._manifest(source['project_id'],source['session_ref']['id'])[0]
                        inherited_work=(None if parent.get('replacement') else
                            continuation_ref(self.sessions,manifest,basis,source['generation']))
                        if inherited_work is not None:
                            if body.original_work_ref is not None and body.original_work_ref.model_dump()!=inherited_work:
                                raise Conflict('Selected work differs from the displayed original task')
                            shared['original_work_ref']=inherited_work
                    guidance = bool(active) or (parent is not None or body.original_work_ref is not None) and _local_status(body.raw_text)
                    dispatch = (GuidanceInput(**shared, intent='AUTO', expected_active_operation_id=active) if guidance
                                else SendMessage(**shared, agent=body.agent))
                    row.update(source=source, phase='DISPATCHING',
                        dispatch_request_sha256=digest(operation_request(dispatch)),
                        original_active_operation_id=active)
                    self._save(path, row, replace=True)
                    if guidance:
                        await self.sessions.guidance.submit(source['project_id'], source['session_ref']['id'], dispatch)
                    else:
                        await self.sessions.send(source['project_id'], source['session_ref']['id'], dispatch)
                    row.update(phase='OBSERVED', finished_at=now())
                    self._save(path, row, replace=True)
                    return self.operation(cid, body.operation_id)
            except BaseException as error:
                row.update(phase='UNKNOWN', error_type=type(error).__name__, finished_at=now())
                self._save(path, row, replace=True)
                raise
            finally:
                self.inflight.pop(token, None)

    async def cancel(self, cid, operation_id):
        """One explicit stop of this message, never the owner's next task."""
        path = self._path('messages', [cid, operation_id])
        async with self._lock(('message', cid, operation_id)):
            row = self._read(path)
            if row.get('cancel') is not None:
                return self.operation(cid, operation_id)
            if not self.operation(cid, operation_id).get('can_cancel'):
                raise Conflict('This original message is no longer the exact active stop target')
            self._available()
            source = row['source']
            key = 'stop_' + digest([cid, operation_id])[:48]
            row['cancel'] = {'operation_id': key, 'status': 'DISPATCHING'}
            self._save(path, row, replace=True)
            token = ('cancel', cid, operation_id)
            self.inflight[token] = asyncio.current_task()
            try:
                await self.sessions.interrupt(source['project_id'], source['session_ref']['id'],
                    InterruptSession(operation_id=key, session_ref=source['session_ref'],
                                     generation=source['generation'], expected_active_operation_id=operation_id))
            finally:
                self.inflight.pop(token, None)
            return self.operation(cid, operation_id)

    def resolve_input_file(self, organization_project_id, conversation_ref, execution_project_id, operation_id):
        """Resolve a saved canonical message's file, under its original owner."""
        cid = conversation_ref['id']
        scope = self.threads.view(cid)
        if scope['project_id'] != organization_project_id or scope['conversation_ref'] != conversation_ref:
            raise Conflict('Conversation changed before opening its input file')
        row = self._read(self._path('messages', [cid, operation_id]))
        source = row.get('source')
        if (not source or source['project_id'] != execution_project_id or not self._bound(source, cid)
                or row.get('phase') not in {'DISPATCHING', 'OBSERVED', 'UNKNOWN'}
                or row.get('conversation_ref', {}).get('id') != cid):
            raise Conflict('Input file operation belongs to another execution source')
        operation = self.sessions.operation(execution_project_id, source['session_ref']['id'], operation_id)
        request = operation.get('request')
        context = operation.get('conversation_context')
        frozen = context.get('file_input') if isinstance(context, dict) else None
        if (operation.get('id') != operation_id or operation.get('kind') != 'MESSAGE'
                or not isinstance(request, dict) or not isinstance(frozen, dict) or not request.get('file_input')
                or request['file_input'] != row['request'].get('file_input')
                or request.get('conversation_ref') != row['conversation_ref']
                or operation.get('session_ref') != source['session_ref']
                or request.get('generation') != source['generation']
                or digest(request) != row.get('dispatch_request_sha256')
                or digest(request) != operation.get('request_sha256')
                or digest(context) != operation.get('conversation_context_sha256')
                or context.get('original_request_sha256') != operation['request_sha256']
                or frozen.get('schema') != 'eeas.original-file-input.v1'
                or not isinstance(frozen.get('file_source_ref'), dict)
                or not isinstance(frozen.get('link_ref'), dict)):
            raise Conflict('Input file is not bound to the saved canonical operation')
        from packages.domain.local_workspaces import FileSource
        baseline = FileSource.model_validate(frozen['file_source_ref']).model_dump()
        if baseline['project_id'] != execution_project_id or baseline['sha256'] != frozen.get('sha256'):
            raise Conflict('Input file baseline does not match its execution source')
        resolved = self.app.state.file_continuity.resolve_open(frozen['link_ref'])
        if not isinstance(resolved.get('file_source_ref'), dict):
            raise Conflict('Input file is no longer a registered workspace file')
        latest = FileSource.model_validate(resolved['file_source_ref']).model_dump()
        keys = ('kind', 'project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path')
        if any(baseline[key] != latest[key] for key in keys) or latest['sha256'] != resolved['current_sha256']:
            raise Conflict('Input file binding changed before opening')
        after = self.threads.view(cid)
        if after['project_id'] != organization_project_id or after['conversation_ref'] != conversation_ref:
            raise Conflict('Conversation changed before opening its input file')
        return {'kind': 'CANONICAL_INPUT_FILE', 'conversation_ref': conversation_ref,
            'project_id': organization_project_id, 'operation_id': operation_id,
            'file_source_ref': latest, 'baseline_file_source_ref': baseline}

    def operation(self, cid, operation_id):
        row = self._read(self._path('messages', [cid, operation_id]))
        result = {'version': 1, 'operation_id': operation_id, 'conversation_ref': row['conversation_ref'],
            'request_sha256': row['body_sha256'], 'original_source': row['source'],
            'status': 'UNKNOWN_OUTCOME', 'operation': None, 'response': None, 'can_cancel': False,
            'model_calls_by_query': 0, 'owner_started_by_query': False, 'automatic_post_replay': False}
        work_input_sha=original_work_input_fingerprint(row['request'])
        if work_input_sha is not None:result['original_work_input_sha256']=work_input_sha
        if row['request'].get('context_selection_id'):
            result['context_selection_id'] = row['request']['context_selection_id']
        if row['source'] is None or row['dispatch_request_sha256'] is None:
            if ('message', cid, operation_id) in self.inflight:
                result['status'] = 'PROCESSING'
            elif row.get('error_type'):
                result['status'] = 'FAILED_NOT_DISPATCHED'
            return result
        source = row['source']
        try:
            op = self.sessions.operation(source['project_id'], source['session_ref']['id'], operation_id)
        except NotFound:
            return result
        if op['request_sha256'] != row['dispatch_request_sha256'] or op['request']['generation'] != source['generation']:
            raise Conflict('Original operation differs from its frozen canonical entry')
        context = op.get('conversation_context')
        if context is not None:
            if digest(context) != op.get('conversation_context_sha256'):
                raise Conflict('Original context decision integrity differs')
            if context.get('context_selection_id'):
                result['context_selection_id'] = context['context_selection_id']
            if context.get('context_summary') is not None:
                result['context_summary'] = context['context_summary']
        view = self._view(source)
        root = continuation_parent(op)
        can_continue = (view['configuration_current']
                        and (not view['connected_owner'] or view['observation_is_current'])
                        and view['generation'] == source['generation']
                        and view['active_operation_id'] in (None, root))
        try:
            current_scope = self.threads.view(cid)
            current = self.current_owner(current_scope)
            if current['status'] != 'LEGACY' and (current['status'] not in {'CURRENT','SELECTED'} or any(
                    current['original_source'][key] != source[key] for key in ('project_id', 'session_ref'))):
                can_continue = False
                if current['status'] in {'CURRENT','SELECTED'}:
                    from packages.agent_sessions.canonical_resources import replacement_path
                    target=self._view(current['original_source'])
                    replacement_path(self,self._source(target),source,cid)
                    can_continue=bool(target['configuration_current']
                        and (not target['connected_owner'] or target['observation_is_current'])
                        and target['active_operation_id'] is None
                        and (op.get('native_dispatch_acknowledged') is True or op.get('role')=='LOCAL_STATUS'))
            if current['status'] == 'PENDING':
                result['current_owner_projection'] = {'status': 'PENDING'}
            if view['active_operation_id'] is None and current_scope['conversation_ref'] != row['conversation_ref']:
                can_continue = False
        except Exception:
            # Only the optional current-head projection is degraded. The exact
            # original operation and owner were independently checked above;
            # their saved FAILED/UNKNOWN result must remain readable by its key.
            can_continue = False
            result['current_owner_projection'] = {'status': 'UNAVAILABLE', 'code': 'CURRENT_OWNER_UNAVAILABLE'}
        can_cancel = (op['kind'] == 'MESSAGE' and view['generation'] == source['generation']
                      and view['connected_owner'] and view['active_operation_id'] == operation_id)
        if row.get('cancel') is not None:
            try:
                stop = self.sessions.operation(source['project_id'], source['session_ref']['id'], row['cancel']['operation_id'])
                result['cancel'] = {'operation_id': stop['id'], 'status': stop['status'],
                    'original_abort_acknowledged': stop.get('original_abort_acknowledged', False),
                    'original_message_stop_confirmed': stop.get('original_message_stop_confirmed', False)}
            except NotFound:
                result['cancel'] = {**row['cancel'], 'status': 'UNKNOWN_OUTCOME'}
        messages = [deepcopy(item) for item in view.get('messages', [])
                    if item.get('info', {}).get('role') == 'assistant'
                    and item['info'].get('parentID') == op.get('native_message_id')]
        text = '\n\n'.join(part['text'] for item in messages for part in item.get('parts', [])
                           if part.get('type') == 'text' and isinstance(part.get('text'), str))
        status = 'PROCESSING' if op['status'] in {'ACCEPTED', 'DISPATCHING', 'PREPARED', 'OBSERVED', 'NEXT_TURN_ASSOCIATED'} else op['status']
        rejection = original_auth_rejection(op, view.get('messages', [])) if status == 'FAILED' else None
        if status == 'FAILED' and op.get('native_post_attempted') and not op.get('individual_response_observed') and not rejection:
            status = 'UNKNOWN_OUTCOME'
        response = {'text': text, 'source': 'SAVED_ORIGINAL_ROOT_MESSAGES',
                    'verification': 'MODEL_RESPONSE_NOT_INDEPENDENT_ACCEPTANCE'} if text else None
        if rejection:
            response = {'text': AUTH_REJECTION_TEXT,
                        'source': 'SAVED_ORIGINAL_AUTH_REJECTION', 'verification': 'ORIGINAL_ERROR_NOT_TASK_ACCEPTANCE'}
            result['failure'] = {'code': rejection['code'], 'http_status': 401,
                                 'automatic_retry': False, 'recovery': 'REAUTHORIZE_THEN_EXPLICIT_NEW_INTENT'}
        if not text and op.get('role') == 'LOCAL_STATUS':
            local = op.get('local_status', {})
            saved_only = local.get('observation_is_current') is False
            lead = ('当前连接不可用。以下是上次保存的状态，不能确认任务的实时进度。'
                    if saved_only else '')
            work = local.get('original_work')
            response = {'text': lead + (('上次有活动任务。' if local.get('active_operation_id') else '上次没有活动任务。')
                        if saved_only else ('当前任务仍在执行。' if local.get('active_operation_id') else '当前没有活动任务。'))
                        + '待回答 ' + str(local.get('pending_questions', 0)) + ' 项，待授权 '
                        + str(local.get('pending_permissions', 0)) + ' 项。',
                        'source': 'ORIGINAL_LOCAL_STATUS', 'verification': 'LOCAL_OBSERVATION_NOT_INDEPENDENT_ACCEPTANCE'}
            if isinstance(work, dict):
                response['text'] = _original_work_status_text(work)
            elif isinstance(local.get('canonical_work'),dict):
                response['text']='所续接工作上次保存状态：'+str(local['canonical_work']['status'])+'。这里只读取原操作记录，不代表实时进度或整项工作验收通过。'
        return {**result, 'status': status, 'operation': op, 'can_cancel': bool(can_cancel),
            'canonical_message_ref': {'id': operation_id, 'revision': 1, 'sha256': row['body_sha256']},
            'can_continue': bool(can_continue),
            'response': response}


def register_canonical_conversations(app):
    service = CanonicalConversations(app)
    app.state.canonical_conversations = service
    from apps.api.context_recovery import register_context_recovery
    register_context_recovery(app,service)
    from apps.api.conversation_configuration import register_conversation_configuration
    register_conversation_configuration(app,service)
    from apps.api.conversation_owner_selection import register_owner_selection
    register_owner_selection(app,service)
    from apps.api.configuration_adoptions import register_configuration_adoptions
    register_configuration_adoptions(app,service)

    @app.post('/api/conversations/{conversation_id}/messages')
    async def send(conversation_id: str, body: ConversationMessage):
        return await service.send(conversation_id, body)

    @app.get('/api/conversations/{conversation_id}/messages/{operation_id}')
    def operation(conversation_id: str, operation_id: str):
        return service.operation(conversation_id, operation_id)

    @app.post('/api/conversations/{conversation_id}/messages/{operation_id}/cancel')
    async def cancel(conversation_id: str, operation_id: str):
        return await service.cancel(conversation_id, operation_id)

    @app.post('/api/conversations/{conversation_id}/messages/{parent_id}/continuations')
    async def continuation(conversation_id: str, parent_id: str, body: ConversationContinuation):
        return await service.continuation(conversation_id, parent_id, body)

    return service
