"""Explicit execution configuration on the existing canonical owner lifecycle.

Preparation reads saved metadata only. A confirmed replacement retires the
settled original owner through its existing control journal, then opens one
owner with the exact selected request. It never submits a user message.
"""
from copy import deepcopy
import os
import re

from apps.api.canonical_conversations import OriginalOwner
from packages.agent_sessions.models import OpenConversation
from packages.domain.conversation_threads import ConversationRef, MAX_SOURCES
from packages.domain.questions import Hash, Id, RequestModel
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest


class ConfigurationPreparation(RequestModel):
    conversation_ref: ConversationRef
    original_source: OriginalOwner | None = None


class ConfigureConversation(RequestModel):
    request: OpenConversation
    preparation_sha256: Hash
    original_source: OriginalOwner | None = None
    execution_project_id: Id | None = None
    confirmation_sha256: Hash


class ConfigurationContinuation(RequestModel):
    operation_id: Id
    project_id: Id | None = None
    request_sha256: Hash
    confirmation_sha256: Hash


def confirmation_value(request, preparation_sha256, execution_project_id):
    return {'request': request, 'preparation_sha256': preparation_sha256,
            'execution_project_id': execution_project_id}


def validate_confirmation(proof, request, source, declared=None):
    """Internal journal proof; caller also checks its separately saved checksum."""
    if (not isinstance(proof, dict) or set(proof) != {'authority', 'preparation_sha256',
            'execution_project_id', 'request_sha256', 'confirmation_sha256'}
            or proof['preparation_sha256'] != digest(proof['authority'])
            or proof['request_sha256'] != digest(request)
            or proof['confirmation_sha256'] != digest(confirmation_value(request,
                proof['preparation_sha256'], proof['execution_project_id']))):
        raise Conflict('The explicit execution configuration confirmation changed')
    authority = proof['authority']
    expected = authority['original_source']
    if (authority['conversation_ref'] != request['conversation_ref']
            or authority['project_id'] != request['project_id']
            or authority['capability_binding_sha256'] != request['capability_binding_sha256']
            or (expected is None) != (source is None)
            or expected is not None and any(expected[key] != source[key]
                for key in ('project_id', 'session_ref', 'generation'))
            or declared is not None and authority['original_request_sha256'] != digest(declared)):
        raise Conflict('The original execution authority differs from the confirmed configuration')


class ConversationConfiguration:
    def __init__(self, canonical_service):
        self.canonical = canonical_service
        self.sessions = canonical_service.sessions

    def _admit_request(self, request, *, execution_project_id=None):
        """Current grants preflight before either initial or continued retirement."""
        from packages.agent_sessions.capability_scope import freeze_scope
        binding = self.sessions.capabilities.native_binding_snapshot(validate_mcp_ids=set())
        selected_binding, _ = freeze_scope(self.sessions.capabilities, request.model_dump(), binding)
        self.sessions._admit_open(request, selected_binding)
        if request.command_observation_paths:
            self.sessions._admit_observation_paths(request, execution_project_id)

    def prepare(self, body):
        body = ConfigurationPreparation.model_validate(body)
        c = self.canonical
        scope = c._scope(body.conversation_ref.model_dump())
        hint = body.original_source.model_dump() if body.original_source else None
        source = c._member(scope, hint)
        owner = c._view(source) if source else None
        if hint is not None and owner is not None and c._source(owner)!=hint:
            raise Conflict('The selected original generation changed during configuration preparation')
        declared = None
        source_authority=None
        if owner:
            manifest, _ = self.sessions._manifest(owner['project_id'], owner['session_ref']['id'])
            if manifest.get('native_message_admission') or manifest.get('native_review_admission'):
                raise Conflict('A private native owner cannot be reconfigured through a public chat')
            source_authority=self.sessions.context_authority.saved(manifest,owner)
            declared = deepcopy(manifest['request'])
            source = c._source(owner)
        configuration = self.sessions.configuration()
        tools = self.sessions.capabilities.tool_catalog()
        if (tools.get('read_only') is not True or tools.get('model_calls') != 0
                or tools.get('effects_applied') is not False
                or tools['capability_binding_sha256'] != configuration['capability_binding_sha256']):
            raise Conflict('The saved capability catalogue changed during preparation')
        snapshot = self.sessions.capabilities.native_binding_snapshot(validate_mcp_ids=set())
        if snapshot['sha256'] != configuration['capability_binding_sha256']:
            raise Conflict('The saved capability configuration changed during preparation')
        server_types = {key: value['type'] for key, value in
                        snapshot['value']['native_config_fragment']['mcp'].items()}
        # Export stable names and refs, never executables, environment, endpoints,
        # credentials, absolute host paths or tool schemas/content.
        servers = [{**deepcopy(row), 'type': server_types.get(row['id'])}
                   for row in tools['mcp_servers']]
        workspaces, total, truncated, unavailable = [], 0, False, 0
        projects = list(dict.fromkeys(pid for pid in
            (owner['project_id'] if owner else None, scope['project_id']) if pid is not None))
        for pid in projects:
            service=self.sessions.workspaces
            project_name=str(service.store.project(pid).get('name',''))[:120]
            folder=service._data(pid)
            preferred=declared.get('workspace_ref') if declared and owner['project_id']==pid else None
            ids=[preferred['id']] if preferred else []
            if folder.exists():
                # Inspect at most 257 directory entries; do not read every
                # registered binding just to render a small selector.
                with os.scandir(folder) as entries:
                    for index,entry in enumerate(entries):
                        if index>=256:
                            truncated=True;break
                        if re.fullmatch(r'[a-f0-9]{64}',entry.name) and entry.name not in ids:
                            ids.append(entry.name)
            ids=([preferred['id']] if preferred else [])+sorted(ident for ident in ids if not preferred or ident!=preferred['id'])
            total+=len(ids)
            for ident in ids:
                if len(workspaces)>=128:
                    truncated=True;break
                try:
                    row=service.get(pid,ident)
                except (Conflict,NotFound,OSError):
                    unavailable+=1;continue
                ref = {key: row[key] for key in ('id', 'revision', 'binding_sha256')}
                label = str(row.get('root', '')).replace('\\', '/').rstrip('/').rsplit('/', 1)[-1]
                paths = deepcopy(row.get('allowed_write_paths', []))
                workspaces.append({'project_id': pid, 'workspace_ref': ref,
                    'label':(project_name+' · '+label[:200]).strip(' ·') or 'Workspace', 'allowed_write_paths': paths})
        authority = {'version': 1, 'conversation_ref': scope['conversation_ref'],
            'project_id': scope['project_id'], 'original_source': deepcopy(source),
            'original_request_sha256': digest(declared) if declared is not None else None,
            'source_authority':source_authority,
            'capability_binding_sha256': configuration['capability_binding_sha256'],
            'workspace_catalog_sha256': digest([{k:row[k] for k in ('project_id','workspace_ref','allowed_write_paths')} for row in workspaces]), 'tools_catalog_sha256': digest(servers)}
        result = {'kind': 'CONVERSATION_CONFIGURATION_PREPARATION', 'authority': authority,
            'preparation_sha256': digest(authority), 'original_request': declared,
            'workspaces': workspaces, 'workspace_coverage': {'available': total, 'returned': len(workspaces),
                'truncated': truncated, 'unavailable':unavailable, 'complete':not truncated and unavailable==0}, 'mcp_servers': servers,
            'configuration_refs': deepcopy(configuration['configuration_refs']),
            'default_execution_project_id': owner['project_id'] if owner else scope['project_id'],
            'new_owner_capacity':max(0,MAX_SOURCES-len(scope['sources'])),
            'original_configuration_current':owner.get('configuration_current') is True if owner else False,
            'original_owner_settled': bool(owner and owner.get('active_operation_id') is None
                and not owner.get('pending_questions') and not owner.get('pending_permissions')),
            'read_only': True, 'model_calls': 0, 'owner_started': False,
            'permission_granted': False, 'automatic_message_replay': False}
        if len(canonical(result).encode('utf-8')) > 524288:
            raise Conflict('Execution configuration catalogue exceeds its response bound')
        return result

    async def configure(self, body):
        body = ConfigureConversation.model_validate(body)
        request = body.request.model_dump()
        if request['conversation_ref'] is None:
            raise Conflict('An exact conversation is required for execution configuration')
        wanted = digest(confirmation_value(request, body.preparation_sha256, body.execution_project_id))
        if wanted != body.confirmation_sha256:
            raise Conflict('Confirm the exact selected execution configuration')
        c = self.canonical
        path = c._path('connections', [request['project_id'], request['operation_id']])
        if path.exists():
            saved = c._read(path)
            proof = saved.get('configuration_confirmation')
            if (saved['request'] != request or not proof
                    or proof['preparation_sha256'] != body.preparation_sha256
                    or proof['execution_project_id'] != body.execution_project_id
                    or proof['confirmation_sha256'] != body.confirmation_sha256
                    or proof['authority']['original_source'] != (body.original_source.model_dump() if body.original_source else None)):
                raise Conflict('This connection operation belongs to another configuration confirmation')
        else:
            prepared = self.prepare({'conversation_ref': request['conversation_ref'],
                'original_source': body.original_source.model_dump() if body.original_source else None})
            if (prepared['preparation_sha256'] != body.preparation_sha256
                    or prepared['authority']['project_id'] != request['project_id']
                    or prepared['authority']['capability_binding_sha256'] != request['capability_binding_sha256']):
                raise Conflict('Execution configuration preparation is stale; input retained')
            allowed_projects = {request['project_id'], prepared['default_execution_project_id']}
            if body.execution_project_id not in allowed_projects:
                raise Conflict('The execution project is outside this explicit preparation')
            if request['workspace_ref'] is not None and not any(
                    row['project_id'] == body.execution_project_id and row['workspace_ref'] == request['workspace_ref']
                    for row in prepared['workspaces']):
                raise Conflict('Select an exact registered workspace in the chosen execution project')
            try:
                self.sessions.open_operation(request['operation_id'],body.execution_project_id)
            except NotFound:
                pass
            else:
                raise Conflict('This original connection operation already belongs to an existing owner')
            self._admit_request(body.request, execution_project_id=body.execution_project_id)
            proof = {'authority': prepared['authority'], 'preparation_sha256': body.preparation_sha256,
                'execution_project_id': body.execution_project_id, 'request_sha256': digest(request),
                'confirmation_sha256': body.confirmation_sha256}
        # One existing owner journal and loop; this never sends an old/new prompt.
        return await c.open(body.request, configuration_confirmation=proof)

    async def continue_configuration(self, conversation_id, body):
        """Explicit same-key admission; no client-supplied replacement request.

        An absent native journal plus a settled failed canonical attempt is the
        only startable case here. A committed/unknown owner is observation only.
        The canonical lock repeats that decision before any start or retirement.
        """
        body = ConfigurationContinuation.model_validate(body)
        c = self.canonical
        row = c._read(c._path('connections', [body.project_id, body.operation_id]))
        proof = row.get('configuration_confirmation')
        if (row['conversation_ref']['id'] != conversation_id or not proof
                or row['request_sha256'] != body.request_sha256
                or proof['confirmation_sha256'] != body.confirmation_sha256):
            raise Conflict('Continue the exact original configuration operation')
        observed = c.open_operation(body.operation_id, body.project_id)
        if observed.get('configuration_disposition') is not None:return observed
        if observed.get('status') == 'FAILED_NOT_STARTED':
            self._admit_request(OpenConversation.model_validate(row['request']),
                execution_project_id=proof['execution_project_id'])
            await c.open(row['request'], configuration_confirmation=proof,
                         continue_not_started=True)
        elif observed.get('status') in {'CANDIDATE_READY','CANDIDATE_CLOSED'}:
            from apps.api.configuration_candidates import ConfigurationCandidates
            return await ConfigurationCandidates(c).continue_candidate(row,
                lambda request:self._admit_request(request,execution_project_id=proof['execution_project_id']))
        return c.open_operation(body.operation_id, body.project_id)


def register_conversation_configuration(app, canonical_service):
    value = ConversationConfiguration(canonical_service)
    app.state.conversation_configuration = value

    @app.post('/api/conversations/{conversation_id}/connection-configuration/prepare')
    def prepare(conversation_id: str, body: ConfigurationPreparation):
        if body.conversation_ref.id != conversation_id:
            raise Conflict('Configuration preparation belongs to another conversation')
        return value.prepare(body)

    @app.post('/api/conversations/{conversation_id}/connection-configuration')
    async def configure(conversation_id: str, body: ConfigureConversation):
        if body.request.conversation_ref is None or body.request.conversation_ref.id != conversation_id:
            raise Conflict('Configuration confirmation belongs to another conversation')
        return await value.configure(body)

    @app.post('/api/conversations/{conversation_id}/connection-configuration/continue')
    async def continue_configuration(conversation_id: str, body: ConfigurationContinuation):
        return await value.continue_configuration(conversation_id, body)

    return value
