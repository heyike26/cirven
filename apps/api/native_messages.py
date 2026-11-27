"""Durable companion questions through the original OpenCode and official MCP."""
import asyncio
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from typing import Literal
import uuid

from fastapi import Depends, Header, Query
from pydantic import Field, model_validator
from packages.adapters.opencode_desktop import OpenCodeDesktopTransport
from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.adapters.native_frame_view import VIEW_SCHEMA, frame_view
from packages.adapters.native_file_view import selected_file_bytes, selected_file_view
from packages.domain.desktop_intents import DesktopIntents, DesktopMessageInput
from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import FileSource, canonical, digest
from packages.domain.linked_requirements import FrozenLinkedRequirements
from packages.domain.native_rework import MappingInput, NativePlanInput, NativeApplyInput
from packages.domain.native_feedback import NativeFeedbackInput, Selection
from packages.domain.feedback_rework import merge_scoped
from packages.domain.questions import Id, RequestModel
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.agent_sessions.models import (NativeReviewInput, NativeCapabilityTaskInput, ReviewGuidanceInput,
    ReviewPermissionInput, ReviewQuestionInput, ReviewQuestionRejectInput, OrdinaryGuidanceInput)
from packages.domain.conversation_threads import ConversationRef
from packages.domain.questions import Hash
from packages.domain.native_input_attachments import NativeInputAttachment, NativeInputSources, CATALOG_METHOD, READ_METHOD


class FileLinkRef(RevisionRef):
    project_id: Id
    id: str = Field(pattern=r'^[a-f0-9]{64}$')


class NativeQuestionInput(DesktopMessageInput):
    project_id: Id | None = None
    provider_profile_ref: RevisionRef | None = None
    file_source_ref: FileSource | None = None
    file_link_ref: FileLinkRef | None = None
    conversation_id: Id | None = None
    conversation_ref: ConversationRef | None = None
    native_review: NativeReviewInput | NativeCapabilityTaskInput | None = None
    native_feedback_ref: RevisionRef | None = None
    input_attachments: list[NativeInputAttachment] = Field(default_factory=list, max_length=20)
    context_selection_id: Hash | None = None

    def request_dict(self):
        # Absent optional review data must not change historical ordinary hashes.
        return self.model_dump(exclude={key for key in ('native_review', 'native_feedback_ref', 'input_attachments', 'conversation_ref', 'context_selection_id')
            if getattr(self, key) is None or key == 'input_attachments' and not self.input_attachments})

    @model_validator(mode='after')
    def same_file_project(self):
        if len(canonical([item.model_dump() for item in self.input_attachments]).encode()) > 96 * 1024:
            raise ValueError('Selected attachment metadata exceeds the shared 96 KiB input budget')
        if self.input_attachments and self.context_ref is not None and self.context_ref != self.input_attachments[0].context_ref:
            raise ValueError('The optional primary page must be the first explicitly selected input')
        if self.file_source_ref is not None and self.file_link_ref is not None:
            raise ValueError('Choose one exact file source for this question')
        if self.native_feedback_ref is not None and (self.file_source_ref is None or self.native_review is not None):
            raise ValueError('Exact native feedback needs its saved FileSource and the original ordinary loop')
        file_project = (self.file_source_ref.project_id if self.file_source_ref else
                        self.file_link_ref.project_id if self.file_link_ref else None)
        if self.project_id is not None and file_project is not None and self.project_id != file_project:
            raise ValueError('File continuity belongs to another project')
        return self


def question_project(body):
    return (body.get('project_id') or (body.get('file_source_ref') or {}).get('project_id')
            or (body.get('file_link_ref') or {}).get('project_id'))


ANSWER_SCHEMA = {'type': 'object', 'additionalProperties': False, 'required': ['text', 'questions'],
    'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 24000},
        'questions': {'type': 'array', 'maxItems': 20, 'items': {'type': 'string', 'maxLength': 4000}}}}

IDE_CANDIDATE_LIMIT = 256
IDE_FILE_TEXT_BYTES = 196608


class IdeFeedbackInput(RequestModel):
    operation_id: Id
    file_source_ref: FileSource
    comment: str = Field(min_length=1, max_length=16000)
    selection: Selection


class IdeApplyInput(NativeApplyInput):
    user_approved: bool = Field(strict=True)

    @model_validator(mode='after')
    def explicitly_approved(self):
        if self.user_approved is not True:
            raise ValueError('Explicit approval of this exact plan is required')
        return self


class _QuestionScope:
    """A new message scope never adopts a previous host's input authority."""
    def __init__(self, ws):
        self.ws, self.lock = ws, ws.lock
        self.generation = uuid.uuid4().hex

    def _path(self, *parts): return self.ws._path('question-scopes', self.generation, *parts)
    def _read(self, *args, **kwargs): return self.ws._read(*args, **kwargs)
    def _write(self, *args, **kwargs): return self.ws._write(*args, **kwargs)
    def check_host(self, *args, **kwargs): return self.ws.check_host(*args, **kwargs)


class NativeMessages:
    def __init__(self, app, authority, context_rpc):
        self.app, self.ws, self.context = app, authority, context_rpc
        self.generation = uuid.uuid4().hex
        self.tasks = {}
        self.closed = False
        self.intents = DesktopIntents(_QuestionScope(authority)) if authority.enabled else None
        profiles = app.state.provider_settings
        source_root = Path(__file__).resolve().parents[2]
        packaged_mcp = source_root / 'scripts/native_package_bootstrap.py'
        if (source_root / 'PACKAGE_MANIFEST.json').exists() and not packaged_mcp.is_file():
            raise Conflict('Packaged Python MCP bootstrap is missing')
        self.transport = OpenCodeDesktopTransport(app.state.store, app.state.settings.data_dir,
            provider_registry=OpenCodeProviderRegistry(profiles.configuration, profiles.secret),
            message_reader=self.read_message, context_reader=self.read_context,
            context_checker=self.check_snapshot,
            mcp_script=packaged_mcp if (source_root / 'PACKAGE_MANIFEST.json').exists() else None)
        self.input_sources = NativeInputSources(self)
        self.transport.register_method(CATALOG_METHOD, self.input_sources.catalog)
        self.transport.register_method(READ_METHOD, self.input_sources.read)
        self.transport.register_method({'id': 'read_project_context', 'route': 'FROZEN_CONVERSATION_PROJECT',
            'readonly': True, 'effects': [],
            'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'coverage': 'EXACT_ADMITTED_PROJECT_NAME_AND_BOUNDED_DESCRIPTION_NO_FILES',
            'qualification': 'IMPLEMENTED',
            'cost': {'latency': 'LOCAL_RECORD', 'tokens': 'AT_MOST_16384_UTF8_DESCRIPTION_BYTES', 'measured': False},
            'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input': False, 'focus_effect': 'NONE'}}, self.read_project_context)
        self.transport.register_method({'id': 'read_history', 'route': 'LOCAL_RECORD', 'readonly': True,
            'effects': [], 'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'coverage': 'BOUNDED_PRIOR_QUESTIONS_AND_ORIGINAL_RESULTS_NO_AUTHORITY', 'qualification': 'IMPLEMENTED',
            'cost': {'latency': 'LOCAL_READ', 'tokens': 'BOUNDED_RECENT_MESSAGES', 'measured': False},
            'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input': False, 'focus_effect': 'NONE'}}, self.read_history)
        self.transport.register_method({'id': 'read_reviewed_context', 'route': 'PINNED_CONTEXT_SELECTION',
            'readonly': True, 'effects': [],
            'input_schema': {'type': 'object', 'properties': {}, 'additionalProperties': False},
            'coverage': 'EXPLICIT_SELECTION_CURRENT_SOURCE_CHECK_NO_EXTRA_EXECUTION_AUTHORITY',
            'qualification': 'IMPLEMENTED',
            'cost': {'latency': 'LOCAL_READ', 'tokens': 'AT_MOST_SELECTION_CHARACTER_BUDGET', 'measured': False},
            'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input': False, 'focus_effect': 'NONE'}}, self.read_reviewed_context)
        from apps.api.native_action_proposals import NativeActionProposals, DESCRIPTOR
        self.action_proposals = NativeActionProposals(self)
        self.transport.register_method(DESCRIPTOR, self.action_proposals.propose)
        self.transport.register_method({'id': 'read_visual', 'route': 'FROZEN_FRAME', 'readonly': True,
            'effects': [], 'input_schema': VIEW_SCHEMA, 'coverage': 'EXACT_REGISTERED_FRAME_REGION_NOT_WHOLE_PAGE',
            'qualification': 'IMPLEMENTED', 'cost': {'latency': 'LOCAL_READ', 'tokens': 'AT_MOST_1280_PIXEL_VIEW', 'measured': False},
            'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input': False, 'focus_effect': 'NONE'}}, self.read_visual)
        self.transport.register_method({'id':'read_selected_file','route':'EXPLICIT_FILESOURCE','readonly':True,
            'effects':[],'input_schema':{'type':'object','properties':{},'additionalProperties':False},
            'coverage':'EXACT_SELECTED_FILE_BOUNDED_CONTENT_NOT_NATIVE_WINDOW_IDENTITY','qualification':'IMPLEMENTED',
            'cost':{'latency':'LOCAL_READ','tokens':'AT_MOST_64000_CHARACTERS_OR_1280_PIXEL_IMAGE','measured':False},
            'execution':{'can_background':True,'background_qualification':'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input':False,'focus_effect':'NONE'}},self.read_selected_file)
        self.transport.register_method({'id':'read_linked_file','route':'FROZEN_REGISTERED_FILE','readonly':True,
            'effects':[],'input_schema':{'type':'object','properties':{
                'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':8000}},
                'additionalProperties':False},
            'coverage':'EXACT_PROJECT_LINK_SAVED_FILE_SNAPSHOT_NOT_UNSAVED_NATIVE_BUFFER',
            'qualification':'IMPLEMENTED_READONLY','cost':{'latency':'LOCAL_READ',
                'tokens':'DEFAULT_4000_TEXT_PLUS_BOUNDED_COMMENTS','measured':False},
            'execution':{'can_background':True,'background_qualification':'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input':False,'focus_effect':'NONE'}},self.read_linked_file)
        self.transport.register_method({'id':'read_linked_requirements','route':'EXACT_REPORT_ORIGIN','readonly':True,
            'effects':[],'input_schema':{'type':'object','properties':{
                'section':{'type':'string','enum':['requirements','questions','sources']},
                'offset':{'type':'integer','minimum':0},'limit':{'type':'integer','minimum':1,'maximum':8000}},
                'additionalProperties':False},
            'coverage':'ORIGINAL_LINKED_REPORT_GENERATION_REQUIREMENTS_ANSWERS_AND_SELECTED_SOURCE_CHUNKS_NOT_LATEST',
            'qualification':'IMPLEMENTED_READONLY','cost':{'latency':'LOCAL_VERIFIED_ARTIFACT_READ',
                'tokens':'ON_DEMAND_SECTION_DEFAULT_4000_MAX_8000_CODEPOINTS_NO_IMAGE_BASE64','measured':False},
            'execution':{'can_background':True,'background_qualification':'QUALIFIED_LOCAL_READONLY',
                'requires_foreground_input':False,'focus_effect':'NONE'}},self.read_linked_requirements)
        self.rework = getattr(app.state, 'native_rework', None)
        self.docx_revisions = getattr(app.state, 'docx_revisions', None)
        if self.docx_revisions is not None:
            from packages.domain.docx_revisions import DOCX_PROPOSAL_SCHEMA
            proposal_schema = copy.deepcopy(DOCX_PROPOSAL_SCHEMA)
            proposal_schema['properties'] = {'patches': proposal_schema['properties']['patches']}
            proposal_schema['required'] = ['patches']
            context_schema = {'type': 'object', 'additionalProperties': False, 'properties': {
                'offset': {'type': 'integer', 'minimum': 0},
                'limit': {'type': 'integer', 'minimum': 1, 'maximum': 20}}}
            locate_schema = {'type': 'object', 'additionalProperties': False,
                'required': ['paragraph_path', 'quote'], 'properties': {
                    'part': {'type': 'string', 'enum': ['word/document.xml']},
                    'paragraph_path': {'type': 'string', 'minLength': 1, 'maxLength': 1000},
                    'quote': {'type': 'string', 'minLength': 1, 'maxLength': 16000}}}
            for name, schema, callback in (
                ('read_docx_revision_context', context_schema, self.read_docx_revision_context),
                ('locate_docx_revision', locate_schema, self.locate_docx_revision),
                ('propose_docx_revision', proposal_schema, self.propose_docx_revision),
            ):
                self.transport.register_method({'id': name, 'route': 'FROZEN_DOCX_REVISION_PROPOSAL',
                    'readonly': True, 'effects': [], 'input_schema': schema,
                    'coverage': 'EXACT_FROZEN_DOCX_READ_AND_HOST_PROPOSAL_METADATA_ONLY_NO_SOURCE_OR_ARTIFACT_WRITE; '
                        'USE_HOST_ANCHORS_WITHOUT_COMPUTING_HASHES; PROPOSE_ONLY_FOR_REQUESTED_EDITS; '
                        'HOST_CREATES_A_NEW_TRACKED_COPY_AFTER_RETURNED; NEVER_CLAIM_THE_ORIGINAL_WAS_CHANGED',
                    'qualification': 'IMPLEMENTED',
                    'cost': {'latency': 'LOCAL_READ_AND_INTERNAL_RECORD',
                        'tokens': 'BOUNDED_PARAGRAPHS_OR_PROPOSAL_REFERENCE', 'measured': False},
                    'execution': {'can_background': True,
                        'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                        'requires_foreground_input': False, 'focus_effect': 'NONE'}}, callback)
        if self.rework is not None:
            mapping_schema = MappingInput.model_json_schema()
            mapping_schema['properties'] = {key: value for key, value in mapping_schema['properties'].items()
                if key in {'native_feedback_ref', 'proposal_ref', 'locator', 'mapping_context_ref'}}
            mapping_schema['required'] = ['native_feedback_ref', 'proposal_ref']
            plan_schema = NativePlanInput.model_json_schema()
            plan_schema['properties'].pop('operation_id', None)
            plan_schema['properties'].pop('classification_origin', None)
            plan_schema['required'] = [key for key in plan_schema['required']
                if key not in {'operation_id', 'classification_origin'}]
            plan_schema['properties']['proposed_value_text']['description'] = (
                'Prefer original JSON text for JSON_POINTER values, especially large integers/exponents; '
                'use exact replacement text for UTF8/TEXT_RANGE. Supply this or proposed_value, never both. '
                'The host parses JSON and preserves the original text; this prepares a preview only, not a file write.')
            plan_schema['properties']['contracts']['description'] = (
                'Omit or supply [] to use host-verified mapped source versions only: '
                'MANUAL_REVIEW for mapped tasks, all change kinds invalidating, other tasks UNKNOWN. '
                'This default does not confirm business dependencies, independence or acceptance. '
                'Nonempty explicit contracts must match the exact bindings and value hashes.')
            for name, schema, callback in (
                ('read_rework_context', {'type': 'object', 'properties': {}, 'additionalProperties': False}, self.read_rework_context),
                ('resolve_native_feedback', mapping_schema, self.resolve_native_feedback),
                ('preview_native_rework', plan_schema, self.preview_native_rework),
            ):
                self.transport.register_method({'id': name, 'route': 'SELECTED_FILE_REWORK_PREVIEW',
                    'readonly': True, 'effects': [], 'input_schema': schema,
                    'coverage': 'SOURCE_BOUND_INTERNAL_RECORDS_AND_PREVIEW_NO_PROJECT_FILE_WRITE',
                    'qualification': 'IMPLEMENTED',
                    'cost': {'latency': 'LOCAL_READ_AND_INTERNAL_RECORD', 'tokens': 'BOUNDED_REWORK_RECEIPT', 'measured': False},
                    'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
                        'requires_foreground_input': False, 'focus_effect': 'NONE'}}, callback)

    def path(self, operation_id):
        return self.ws._path('native-messages', operation_id + '.json')

    def read_message(self, ref):
        if self.intents is None: raise Conflict('Native message authority is not configured')
        return self.intents.message(ref)

    def _ide_record(self, path):
        if path.is_symlink() or not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
            raise Conflict('Native discussion record is not bounded independent data')
        saved = self.ws._read(path, authority=False)
        if saved['generation'] == self.generation:
            self.ws._read(path, authority=True)
        question = NativeQuestionInput.model_validate(saved['request']).request_dict()
        if path != self.path(question['operation_id']) or digest(saved['request']) != saved['request_sha256']:
            raise Conflict('Native discussion request integrity differs')
        return saved, question

    def _ide_window(self, project_id, conversation_id=None):
        if project_id is not None:
            self.app.state.store.project(project_id)
        rows, truncated = [], False
        for index, path in enumerate(self.ws._path('native-messages').glob('*.json')):
            if index >= IDE_CANDIDATE_LIMIT:
                truncated = True
                break
            saved, question = self._ide_record(path)
            if (question_project(question) == project_id and question.get('conversation_id') is not None
                    and (conversation_id is None or question['conversation_id'] == conversation_id)):
                rows.append((path.stat().st_mtime_ns, question['operation_id'], saved, question))
        rows.sort(key=lambda row: (row[0], row[1]))
        return rows, truncated

    def ide_operation(self, project_id, conversation_id, operation_id):
        if project_id is not None:
            self.app.state.store.project(project_id)
        path = self.path(operation_id)
        if not path.exists(): raise NotFound('Native discussion operation does not exist')
        saved, question = self._ide_record(path)
        if question_project(question) != project_id or question.get('conversation_id') != conversation_id:
            raise Conflict('Native operation belongs to another discussion')
        result = self.get(operation_id)
        capabilities = result.get('control_capabilities') or {}
        value = {'kind': 'NATIVE_MESSAGE', 'project_id': project_id, 'conversation_id': conversation_id,
            'operation_id': operation_id, 'status': result['status'], 'request_sha256': saved['request_sha256'],
            'record_origin': 'CURRENT_HOST' if saved['generation'] == self.generation else 'HISTORICAL_READONLY',
            'input': {'raw_text': question['raw_text'], 'origin': 'ORIGINAL_NATIVE_REQUEST'},
            'provider_profile_ref': question.get('provider_profile_ref'),
            'native_message_ref': result.get('native_message_ref'),
            'file_source_ref': question.get('file_source_ref'), 'file_link_ref': question.get('file_link_ref'),
            'control_capabilities': {key: capabilities.get(key) is True for key in
                ('can_guide', 'can_status', 'can_cancel')}, 'automatic_retry': False}
        for key in ('text', 'summary', 'questions', 'native_message_phase', 'native_execution','native_context_issue','native_context_recovery'):
            if key in result: value[key] = copy.deepcopy(result[key])
        value['rework_previews'] = [{'kind': 'PLAN', 'record_ref': item['record_ref'],
            **{key: item['payload'][key] for key in
                ('mapping_ref', 'native_feedback_ref', 'proposal_ref')}, 'status': item['status']}
            for item in result.get('rework_previews', []) if item['kind'] == 'PLAN']
        if question.get('native_feedback_ref') is not None:
            value['native_feedback_ref'] = question['native_feedback_ref']
        if result.get('error'):
            value['error'] = {'code': 'ORIGINAL_OPERATION_ERROR',
                'message': '原操作未完成，请在桌面核查原记录；未自动重试。'}
        if len(canonical(value).encode('utf-8')) > 512 * 1024:
            raise Conflict('Native discussion message exceeds the IDE projection budget')
        return value

    def _ide_source(self, pid, cid, parent):
        if not self.path(parent).exists():
            raise NotFound('Original native operation does not exist')
        saved, question = self._ide_record(self.path(parent))
        if question_project(question) != pid or question.get('conversation_id') != cid:
            raise Conflict('Native file belongs to another original discussion')
        source = question.get('file_source_ref')
        if source is None:
            raise Conflict('This original message has no registered native FileSource')
        return saved, source

    @staticmethod
    def _ide_envelope(kind, pid, cid, parent, **value):
        return {'kind': kind, 'project_id': pid, 'conversation_id': cid,
            'parent_operation_id': parent, **value}

    def ide_file(self, pid, cid, parent, version='CURRENT_SAVED'):
        _, baseline = self._ide_source(pid, cid, parent)
        if version not in {'CURRENT_SAVED', 'ORIGINAL_SAVED'}:
            raise InvalidInput('Choose the current or original saved snapshot')
        read = self.app.state.local_workspaces.read_saved_snapshot(pid, baseline['workspace_id'],
            baseline['path'], baseline['revision'] if version == 'ORIGINAL_SAVED' else None)
        if version not in {'CURRENT_SAVED', 'ORIGINAL_SAVED'} or not self._same_file(read['source_ref'], baseline):
            raise Conflict('Native file binding changed; no automatic reassociation')
        available = ('BINARY' if read['encoding'] != 'utf-8' else
            'TOO_LARGE' if read['byte_length'] > IDE_FILE_TEXT_BYTES else 'AVAILABLE')
        return self._ide_envelope('NATIVE_FILE', pid, cid, parent,
            baseline_file_source_ref=baseline, file_source_ref=read['source_ref'],
            availability=available, encoding='utf-8' if available == 'AVAILABLE' else 'UNAVAILABLE',
            **({'content': read['content']} if available == 'AVAILABLE' else {}),
            byte_length=read['byte_length'], current=read['current'], live_sha256=read['live_sha256'],
            read_only=True, model_calls=0)

    def _ide_plan(self, pid, cid, parent, plan_ref):
        saved, source = self._ide_source(pid, cid, parent)
        if {'project_id': pid, 'record_ref': plan_ref} not in saved.get('rework_records', []):
            raise Conflict('Plan is not a persisted preview of this original native operation')
        plan = self.rework.get(pid, plan_ref['id'])
        if plan['kind'] != 'PLAN' or plan['record_ref'] != plan_ref:
            raise Conflict('Exact native plan reference changed')
        mapping = self.rework.get(pid, plan['payload']['mapping_ref']['id'])
        if (mapping['kind'] != 'MAPPING' or mapping['record_ref'] != plan['payload']['mapping_ref']
                or mapping['status'] != 'MAPPED'
                or not self._same_file(mapping['payload']['baseline_file_source_ref'], source)
                or mapping['payload']['proposal_ref'] != plan['payload']['proposal_ref']
                or mapping['payload']['native_feedback_ref'] != plan['payload']['native_feedback_ref']):
            raise Conflict('Native plan/mapping/source lineage differs')
        feedback = self.app.state.native_feedback.get(pid, plan['payload']['native_feedback_ref']['id'])
        if (feedback['feedback_ref'] != plan['payload']['native_feedback_ref'] or
                feedback['payload']['baseline_file_source_ref'] != mapping['payload']['baseline_file_source_ref']):
            raise Conflict('Native preview feedback differs from its exact baseline')
        return plan, mapping, feedback

    def _ide_applied(self, pid, plan):
        # Exact engine PLAN lineage; do not enumerate other projects' files or
        # infer application from a proposed value/current disk coincidence.
        engine = plan['payload']['engine_plan_ref']
        with self.app.state.store.connect() as connection:
            row = connection.execute("""SELECT count(*) FROM feedback_rework_records
                WHERE project_id=? AND kind='APPLY'
                AND json_extract(payload,'$.plan_ref.id')=?
                AND json_extract(payload,'$.plan_ref.sha256')=?
                AND json_extract(payload,'$.status')='APPLIED'""",
                (pid, engine['id'], engine['sha256'])).fetchone()
        return row[0] > 0

    def ide_preview(self, pid, cid, parent, plan_ref):
        plan, mapping, feedback = self._ide_plan(pid, cid, parent, plan_ref)
        p, m = plan['payload'], mapping['payload']
        binding = self.rework.rework.resolve(pid, m['binding_ref'], 'BINDING')
        baseline = m['baseline_file_source_ref']
        if binding['file_source_ref'] != baseline or binding['locator'] != m['locator']:
            raise Conflict('Preview object binding differs from its saved source')
        raw, content = self.app.state.native_feedback._baseline(pid, FileSource.model_validate(baseline))
        if content['encoding'] != 'utf-8' or len(raw) > IDE_FILE_TEXT_BYTES:
            raise InvalidInput('Native IDE diff requires bounded saved UTF-8 snapshots')
        after = merge_scoped(content['content'], content['content'], m['locator'],
            binding['selected_value'], p['proposed_value'], allow_rebase=False)
        if len(after.encode('utf-8')) > IDE_FILE_TEXT_BYTES:
            raise InvalidInput('Native IDE proposed snapshot exceeds the diff bound')
        scope = [{'workspace_id': baseline['workspace_id'], 'path': baseline['path']}]
        if p['write_scope'] != scope:
            raise Conflict('Native IDE preview must have exactly its bound file write scope')
        applied = self._ide_applied(pid, plan)
        return self._ide_envelope('NATIVE_REWORK_PREVIEW', pid, cid, parent,
            status=plan['status'], plan_ref=plan['record_ref'], mapping_ref=mapping['record_ref'],
            native_feedback_ref=feedback['feedback_ref'], proposal_ref=p['proposal_ref'],
            file_source_ref=baseline, locator=m['locator'], write_scope=scope,
            current_basis_status=plan['current_basis_check']['status'],
            already_applied=applied, can_apply=not applied and plan['status'] == 'PROPOSED'
                and plan['current_basis_check']['status'] == 'CURRENT_TASK_REQUIREMENT_BASIS',
            diff={'before': content['content'], 'after': after,
                'before_sha256': hashlib.sha256(raw).hexdigest(),
                'after_sha256': hashlib.sha256(after.encode('utf-8')).hexdigest(),
                'encoding': 'UTF-8', 'content_scope': 'FULL_FILE', 'read_only': True}, model_calls=0)

    def ide_feedback(self, pid, cid, parent, body):
        _, baseline = self._ide_source(pid, cid, parent)
        if body.selection.kind not in {'TEXT_RANGE', 'EXACT_QUOTE'} or not self._same_file(body.file_source_ref.model_dump(), baseline):
            raise Conflict('IDE feedback needs this original file and an exact text selection')
        existing = self.app.state.native_feedback._path(pid, 'operations', digest(body.operation_id), 'request.json')
        if existing.exists():
            result = self.ide_feedback_operation(pid, cid, parent, body.operation_id)
            if result['request_sha256'] != digest(body.model_dump()):
                raise Conflict('Original feedback operation is bound to different input')
            return result
        current = self.ide_file(pid, cid, parent)
        if current['availability'] != 'AVAILABLE' or current['file_source_ref'] != body.file_source_ref.model_dump():
            raise Conflict('Saved editor source changed before feedback; inspect its current version')
        request = NativeFeedbackInput(**body.model_dump(), mode='SUBMITTED', context_ref={
            'kind': 'NATIVE_IDE_FEEDBACK', 'conversation_id': cid, 'parent_operation_id': parent,
            'feedback_operation_id': body.operation_id})
        self.app.state.native_feedback.record(pid, request)
        return self.ide_feedback_operation(pid, cid, parent, body.operation_id)

    def ide_feedback_operation(self, pid, cid, parent, op):
        _, baseline = self._ide_source(pid, cid, parent)
        service = self.app.state.native_feedback
        path = service._path(pid, 'operations', digest(op), 'request.json')
        if not path.exists(): raise NotFound('Original feedback operation does not exist')
        request = service._read(path)
        raw = request['input']
        if (request['kind'] != 'FEEDBACK' or request['operation_id'] != op
                or request['request_sha256'] != digest({key: request[key] for key in
                    ('project_id', 'operation_id', 'kind', 'input')})
                or raw.get('context_ref') != {'kind': 'NATIVE_IDE_FEEDBACK',
                    'conversation_id': cid, 'parent_operation_id': parent, 'feedback_operation_id': op}
                or not self._same_file(raw['file_source_ref'], baseline)):
            raise Conflict('Feedback operation belongs to another native discussion or input')
        public = {key: raw[key] for key in ('operation_id', 'file_source_ref', 'comment', 'selection')}
        result = service.operation(pid, op)
        value = self._ide_envelope('NATIVE_FEEDBACK', pid, cid, parent, operation_id=op,
            status=result['status'], request=public, request_sha256=digest(public),
            file_source_ref=raw['file_source_ref'], automatic_retry=False)
        if result.get('feedback_ref'):
            p = result['payload']
            value.update(feedback_ref=result['feedback_ref'], selection_verification=p['selection_verification'],
                file_selection=p['file_selection'])
            if 'requested_selection' in p: value['requested_selection'] = p['requested_selection']
        return value

    def ide_apply(self, pid, cid, parent, body):
        plan, _, _ = self._ide_plan(pid, cid, parent, body.plan_ref.model_dump())
        existing = self.rework._path(pid, 'operations', digest(body.operation_id), 'request.json')
        if existing.exists():
            result = self.ide_apply_operation(pid, cid, parent, body.operation_id)
            if result['request_sha256'] != digest(body.model_dump(exclude={'user_approved'})):
                raise Conflict('Original apply operation is bound to different input')
            return result
        if self._ide_applied(pid, plan):
            raise Conflict('This exact plan already has an actual apply receipt; inspect it instead')
        sources = body.expected_current_sources
        current = self.ide_file(pid, cid, parent)
        if len(sources) != 1 or sources[0].model_dump() != current['file_source_ref'] or not current['current']:
            raise Conflict('Explicit apply needs exactly the freshly inspected saved source')
        self.rework.apply(pid, body.plan_ref.id,
            NativeApplyInput.model_validate(body.model_dump(exclude={'user_approved'})))
        return self.ide_apply_operation(pid, cid, parent, body.operation_id)

    def ide_apply_operation(self, pid, cid, parent, op):
        path = self.rework._path(pid, 'operations', digest(op), 'request.json')
        if not path.exists(): raise NotFound('Original apply operation does not exist')
        request = self.rework._read(path)
        raw = request['input']
        if (request['kind'] != 'APPLY' or request['operation_id'] != op or request['request_sha256'] !=
                digest({key: request[key] for key in ('project_id', 'operation_id', 'kind', 'input')})):
            raise Conflict('Original rework apply input is inconsistent')
        self._ide_plan(pid, cid, parent, raw['plan_ref'])
        result = self.rework.operation(pid, op)
        value = self._ide_envelope('NATIVE_REWORK_APPLY', pid, cid, parent,
            operation_id=op, status=result['status'], request=raw, request_sha256=digest(raw),
            plan_ref=raw['plan_ref'], automatic_retry=False, outcomes=[])
        if result.get('record_ref'):
            p = result['payload']
            if result['kind'] != 'APPLY' or p['plan_ref'] != raw['plan_ref']:
                raise Conflict('Apply receipt differs from the exact original plan')
            value['receipt_ref'] = result['record_ref']
            value['outcomes'] = [{key: row[key] for key in ('path', 'status', 'source_ref') if key in row}
                for row in p['engine_receipt']['payload']['outcomes']]
        return value

    def _ide_title(self, project_id, conversation_id):
        """Optional readonly naming metadata; never an execution-scope rewrite."""
        owner = getattr(self.app.state, 'conversation_threads', None)
        if owner is None:return {}
        try:
            organization = owner.overlay({'kind':'native','project_id':project_id,'conversation_id':conversation_id})
        except (Conflict, NotFound, OSError, sqlite3.Error):
            return {}  # History remains readable if optional naming metadata is unavailable.
        title = organization.get('display_title') if organization and not organization.get('deleted') else None
        return {'title':title} if isinstance(title, str) and title.strip() and len(title) <= 300 else {}

    def ide_conversations(self, project_id, limit):
        rows, truncated = self._ide_window(project_id)
        groups = {}
        for _, operation_id, _, question in rows:
            cid = question['conversation_id']
            item = groups.setdefault(cid, {'kind': 'NATIVE_CONVERSATION', 'project_id': project_id,
                'conversation_id': cid, 'message_count_observed': 0})
            item['message_count_observed'] += 1
            item['last_operation_id'] = operation_id
            groups[cid] = groups.pop(cid)
        items = list(reversed(list(groups.values())))
        more = truncated or len(items) > limit
        # One metadata lookup per visible discussion, not once per message.
        for item in items[:limit]:item.update(self._ide_title(project_id, item['conversation_id']))
        return {'kind': 'NATIVE_CONVERSATION_DIRECTORY', 'project_id': project_id, 'conversations': items[:limit],
            'coverage': {'candidate_limit': IDE_CANDIDATE_LIMIT, 'candidate_window_truncated': truncated,
                'returned': min(len(items), limit), 'has_more': more, 'complete': not more,
                'scope_filter': 'EXPLICIT_UNBOUND_ONLY' if project_id is None else 'EXPLICIT_PROJECT_ONLY',
                'unbound_messages_included': project_id is None,
                'order_basis': 'RECORD_MODIFIED_AT'}, 'model_calls': 0}

    def ide_conversation(self, project_id, conversation_id, limit):
        rows, truncated = self._ide_window(project_id, conversation_id)
        if not rows:
            if truncated: raise Conflict('Native discussion is outside the bounded observed directory')
            raise NotFound('Native discussion does not exist')
        messages, byte_count = [], 0
        for row in reversed(rows[-limit:]):
            message = self.ide_operation(project_id, conversation_id, row[1])
            size = len(canonical(message).encode('utf-8'))
            if byte_count + size > 512 * 1024: break
            messages.insert(0, message)
            byte_count += size
        more = truncated or len(rows) > len(messages)
        # Old records have no immutable send timestamp. A finishing older task
        # can change mtime; it must not silently choose a different model.
        profile = rows[-1][3].get('provider_profile_ref')
        if truncated or any(row[3].get('provider_profile_ref') != profile for row in rows):
            profile = None
        return {'kind': 'NATIVE_CONVERSATION', 'project_id': project_id, 'conversation_id': conversation_id,
            **self._ide_title(project_id, conversation_id), 'messages': messages, 'latest_provider_profile_ref': profile,
            'coverage': {'candidate_limit': IDE_CANDIDATE_LIMIT,
                'candidate_window_truncated': truncated, 'observed_messages': len(rows), 'returned': len(messages),
                'message_bytes_limit': 512 * 1024,
                'has_more': more, 'complete': not more,
                'scope_filter': 'EXPLICIT_UNBOUND_ONLY' if project_id is None else 'EXPLICIT_PROJECT_ONLY',
                'unbound_messages_included': project_id is None, 'order_basis': 'RECORD_MODIFIED_AT'}, 'model_calls': 0}

    def conversation_scope(self, body):
        owner = getattr(self.app.state, 'conversation_threads', None)
        if owner is None:
            if body.get('conversation_ref') is not None:
                raise Conflict('Organized conversation routing is unavailable')
            return None
        return owner.admission_scope(body)

    def read_project_context(self, _arguments, binding):
        operation_id = binding['request']['operation_id']
        record = self.ws._read(self.path(operation_id), authority=True)
        request = record['request']
        if request.get('operation_id') != operation_id or digest(request) != record['request_sha256']:
            raise Conflict('Original conversation input integrity differs')
        scope = record.get('conversation_scope')
        if scope is None or digest(scope) != record.get('conversation_scope_sha256'):
            raise Conflict('Frozen conversation project context is unavailable or changed')
        if question_project(request) != scope['project_id'] or (request.get('conversation_ref') is not None
                and request['conversation_ref'] != scope['conversation_ref']):
            raise Conflict('Frozen conversation project differs from the admitted question')
        context = scope.get('project_context')
        if context is None or digest(context['snapshot']) != context['snapshot_sha256']:
            raise Conflict('Frozen project metadata integrity differs')
        return {**context, 'conversation_ref': scope['conversation_ref'],
            'frozen_at': 'MESSAGE_ADMISSION_NOT_CURRENT_PROJECT_LOOKUP'}

    def read_history(self, _arguments, binding):
        operation_id = binding['request']['operation_id']
        record = self.ws._read(self.path(operation_id), authority=False)
        if record['request'].get('operation_id') != operation_id:
            raise Conflict('Current history question belongs to another operation')
        from apps.api.conversation_history import ConversationHistory
        reader = getattr(self.app.state, 'conversation_history', None) or ConversationHistory(self.app, native=self)
        return reader.for_model(record)

    def validate_reviewed_context(self, body, conversation_scope=None, *, admitted=False):
        selection_id = body.get('context_selection_id')
        if selection_id is None:
            return None
        graph = getattr(self.app.state, 'context_graph', None)
        if graph is None:
            raise Conflict('Reviewed context service is unavailable; the input was retained')
        pid = conversation_scope['project_id'] if conversation_scope is not None else question_project(body)
        cid = conversation_scope['conversation_ref']['id'] if conversation_scope is not None else None
        return graph.materialize(selection_id, pid, conversation_id=cid,
            frozen_conversation_scope=conversation_scope if admitted else None)

    def admit_reviewed_context(self, body, conversation_scope=None):
        if body.get('context_selection_id'):
            value = self.validate_reviewed_context(body, conversation_scope)
            return {'selection_id': value['selection_id'], 'selection_receipt_id': value['selection_id'],
                'mode': 'MANUAL', 'status': 'SELECTED' if value['items'] else 'EMPTY',
                'project_id': conversation_scope['project_id'] if conversation_scope is not None else question_project(body),
                'conversation_id': conversation_scope['conversation_ref']['id'] if conversation_scope is not None else None,
                'scope_id': value['scope_id'], 'characters': value['characters'],
                'selected_count': len(value['items']), 'omitted_count': 0, 'model_calls': 0}
        from apps.api.context_graph import automatic_input_context, context_selection_summary
        value = automatic_input_context(self.app, body,
            project_id=conversation_scope['project_id'] if conversation_scope is not None else question_project(body),
            conversation_id=conversation_scope['conversation_ref']['id'] if conversation_scope is not None else None,
            namespace='NATIVE_INPUT')
        if value['selection_status'] in {'NO_CONFIGURED_CONTEXT', 'UNAVAILABLE'}:
            return None
        return context_selection_summary(value)

    def capture_input_file(self, record):
        from apps.api.context_graph import capture_context_file_event
        body, scope = record['request'], record.get('conversation_scope')
        return capture_context_file_event(self.app, body,
            project_id=scope['project_id'] if scope is not None else question_project(body),
            conversation_id=scope['conversation_ref']['id'] if scope is not None else None,
            namespace='NATIVE_INPUT', input_sha256=record['request_sha256'],
            selected_scope_id=(record.get('reviewed_context') or {}).get('scope_id'),
            observed_file=record.get('file_observation'))

    def capture_input_discussion(self, record):
        from apps.api.context_graph import capture_context_conversation_event
        scope = record.get('conversation_scope')
        if scope is None:
            return None
        return capture_context_conversation_event(self.app, record['request'], project_id=scope['project_id'],
            conversation_id=scope['conversation_ref']['id'], namespace='NATIVE_INPUT',
            input_sha256=record['request_sha256'], selected_scope_id=(record.get('reviewed_context') or {}).get('scope_id'))

    @staticmethod
    def reviewed_selection_id(record):
        explicit = record['request'].get('context_selection_id')
        choice = record.get('reviewed_context')
        if choice is not None:
            if digest(choice) != record.get('reviewed_context_sha256'):
                raise Conflict('Frozen reviewed context decision integrity differs')
            selected = choice.get('selection_id')
            if explicit is not None and selected != explicit:
                raise Conflict('Frozen context differs from the user-selected reference')
            return selected
        if record.get('reviewed_context_sha256') is not None:
            raise Conflict('Frozen context decision is missing')
        return explicit

    def track_context_authority(self,manifest,context,record):
        selection=self.reviewed_selection_id(record)
        if not selection:return
        scope=record.get('conversation_scope')
        if scope is not None and digest(scope)!=record.get('conversation_scope_sha256'):
            raise Conflict('Frozen context authority scope integrity differs')
        self.app.state.agent_sessions.context_authority.track(manifest,context,selection_id=selection,
            project_id=scope['project_id'] if scope is not None else question_project(record['request']),
            conversation_id=scope['conversation_ref']['id'] if scope is not None else None,
            frozen_conversation_scope=scope)

    def read_reviewed_context(self, _arguments, binding):
        operation_id = binding['request']['operation_id']
        record = self.ws._read(self.path(operation_id), authority=True)
        request, scope = record['request'], record.get('conversation_scope')
        if request.get('operation_id') != operation_id or digest(request) != record['request_sha256']:
            raise Conflict('Reviewed context original input integrity differs')
        if scope is not None and digest(scope) != record.get('conversation_scope_sha256'):
            raise Conflict('Reviewed context frozen conversation scope differs')
        selection_id = self.reviewed_selection_id(record)
        if not selection_id:
            raise Conflict('This input did not select reviewed context')
        if binding.get('reviewed_context_sha256') is not None and binding['reviewed_context_sha256'] != record.get('reviewed_context_sha256'):
            raise Conflict('Context differs from the original frozen admission')
        if record.get('native_message_ref') is not None:
            admission = self.ordinary.validate_admission(record['native_message_ref'])
            if admission.get('reviewed_context_sha256') != record.get('reviewed_context_sha256'):
                raise Conflict('Context decision differs from the original ordinary admission')
        if record.get('review_ref') is not None:
            frozen = self.reviews._frozen(record['review_ref'])
            if frozen.get('reviewed_context_sha256') != record.get('reviewed_context_sha256'):
                raise Conflict('Context decision differs from the original capability admission')
        return self.validate_reviewed_context({**request, 'context_selection_id': selection_id}, scope, admitted=True)

    def read_context(self, ref):
        # Explanations use the frozen question snapshot. User changing foreground
        # during a background answer does not replace it or grant mutation.
        return self.context.lookup(ref)['observation']

    def freeze_capability_context(self, body, profile_ref):
        from apps.api.native_capability_context import freeze_capability_context
        return freeze_capability_context(self, body, profile_ref)

    def review_context(self, body):
        if body.get('input_attachments'):
            return self.input_sources.lookup(body['context_ref'])['observation']
        return self.read_context(body['context_ref'])

    def read_capability_image(self, visual):
        from apps.api.native_capability_context import read_capability_image
        return read_capability_image(self, visual)

    def read_selected_file(self, _arguments, binding):
        request=binding['request']
        with self.ws.lock:
            record=self.ws._read(self.path(request['operation_id']),authority=True)
            if record['generation']!=self.generation or digest(record['request'])!=record['request_sha256']:
                raise Conflict('Selected file question source integrity changed')
            ref=record['request'].get('file_source_ref')
            if ref is None: raise Conflict('No explicit selected file is bound to this question')
            if record['request']['raw_text']!=request['prompt']:
                raise Conflict('Selected file question differs from the exact original execution')
        return selected_file_view(self.app.state.local_workspaces,ref,
            supports_images=self.transport.provider_registry.resolve(request['provider_profile_ref']).supports_images)

    def read_linked_file(self, arguments, binding):
        if set(arguments) - {'offset','limit'}:
            raise Conflict('Linked-file tool cannot select another document')
        offset, limit = arguments.get('offset', 0), arguments.get('limit', 4000)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 8000:
            raise InvalidInput('Linked-file excerpt needs a nonnegative integer offset and a limit of 1–8000')
        request = binding['request']
        with self.ws.lock:
            record = self.ws._read(self.path(request['operation_id']), authority=True)
            if (record['generation'] != self.generation or digest(record['request']) != record['request_sha256']
                    or record['request']['raw_text'] != request['prompt']
                    or question_project(record['request']) != request['project_id']):
                raise Conflict('Linked document differs from this exact question and project')
            observation = record.get('file_observation')
            if (not observation or observation['link_ref'] != record['request'].get('file_link_ref')
                    or observation['snapshot_ref']['project_id'] != request['project_id']):
                raise Conflict('No exact saved document snapshot is bound to this question')
        excerpt = self.app.state.file_continuity.read_text(observation['snapshot_ref'],
            offset=offset, limit=limit)
        return {**observation, **excerpt, 'scope':'EXACT_FROZEN_REGISTERED_FILE_SNAPSHOT',
            'automatic_input_authorized':False, 'file_mutation_adapter':'NOT_ENABLED_BY_READONLY_QUESTION'}

    def read_linked_requirements(self, arguments, binding):
        if not isinstance(arguments, dict) or set(arguments) - {'section','offset','limit'}:
            raise Conflict('Original requirements tool cannot select another project, path or report')
        request = binding['request']
        with self.ws.lock:
            if self.closed:
                raise Conflict('Native question service closed')
            self.ws.check_host(self.ws.token)
            record = self.ws._read(self.path(request['operation_id']), authority=True)
            if (record['generation'] != self.generation or digest(record['request']) != record['request_sha256']
                    or record['request']['raw_text'] != request['prompt']
                    or question_project(record['request']) != request['project_id']):
                raise Conflict('Original requirements differ from this exact question owner and project')
            observation = record.get('file_observation')
            ref = record['request'].get('file_link_ref')
            if (not ref or not observation or observation['link_ref'] != ref
                    or observation['snapshot_ref']['project_id'] != request['project_id']):
                raise Conflict('No exact linked document is bound to this original requirements question')
        return FrozenLinkedRequirements(self.app.state.file_continuity, self.app.state.planning_reports).read(
            ref, **arguments)

    @staticmethod
    def _docx_owner(record):
        return {'operation_id': record['request']['operation_id'], 'generation': record['generation'],
            'conversation_id': record['request'].get('conversation_id')}

    def docx_owner_current(self, project_id, owner):
        try:
            with self.ws.lock:
                if self.closed or not isinstance(owner, dict) or owner.get('generation') != self.generation:
                    return False
                self.ws.check_host(self.ws.token)
                record = self.ws._read(self.path(owner['operation_id']), authority=True)
                return (record['generation'] == self.generation
                    and digest(record['request']) == record['request_sha256']
                    and question_project(record['request']) == project_id
                    and self._docx_owner(record) == owner
                    and not record.get('docx_publication_cancel_requested', False)
                    and record['status'] in {'PROCESSING', 'RETURNED'})
        except (Conflict, NotFound, KeyError, TypeError, ValueError):
            return False

    def _docx_scope(self, binding):
        request = binding['request']
        with self.ws.lock:
            record = self.ws._read(self.path(request['operation_id']), authority=True)
            owner = self._docx_owner(record)
            pid = question_project(record['request'])
            ref, observation = record['request'].get('file_link_ref'), record.get('file_observation')
            if (self.docx_revisions is None or not self.docx_owner_current(pid, owner)
                    or pid != request['project_id'] or record['request']['raw_text'] != request['prompt']
                    or not ref or not observation or observation['link_ref'] != ref
                    or observation['snapshot_ref']['project_id'] != pid
                    or observation['parsed'].get('format', '').lower() != 'docx'):
                raise Conflict('DOCX revision proposal needs this exact question and frozen linked document')
            if record.get('native_message_ref') is not None:
                cohort = self._ordinary_cohort(record, binding.get('native_cohort'))
                if record.get('ordinary_cohort') != cohort:
                    raise Conflict('DOCX feedback belongs to an older ordinary task cohort')
            return pid, copy.deepcopy(ref), copy.deepcopy(observation['snapshot_ref']), owner

    def read_docx_revision_context(self, arguments, binding):
        if not isinstance(arguments, dict) or set(arguments) - {'offset', 'limit'}:
            raise Conflict('DOCX context cannot choose another document or owner')
        pid, link, snapshot, _owner = self._docx_scope(binding)
        return self.docx_revisions.context(pid, link, snapshot, **arguments)

    def locate_docx_revision(self, arguments, binding):
        if (not isinstance(arguments, dict) or set(arguments) - {'part', 'paragraph_path', 'quote'}
                or not {'paragraph_path', 'quote'} <= set(arguments)):
            raise Conflict('DOCX locator needs an exact paragraph and quote from the bound snapshot')
        pid, link, snapshot, _owner = self._docx_scope(binding)
        if arguments.get('part', 'word/document.xml') != 'word/document.xml':
            raise Conflict('DOCX locator cannot select another document part')
        return self.docx_revisions.locate(pid, link, snapshot,
            paragraph_path=arguments['paragraph_path'], quote=arguments['quote'])

    def propose_docx_revision(self, arguments, binding):
        if not isinstance(arguments, dict) or set(arguments) != {'patches'}:
            raise Conflict('DOCX proposal cannot choose another project, source, operation or owner')
        from packages.domain.docx_revisions import DocxRevisionInput
        pid, link, snapshot, owner = self._docx_scope(binding)
        cohort = binding.get('native_cohort')
        operation_basis = {'project_id': pid, 'native_operation_id': owner['operation_id']}
        if cohort is not None:
            operation_basis['native_cohort_sha256'] = cohort['cohort_sha256']
        operation_suffix = digest(operation_basis)[:40]
        with self.ws.lock:
            record = self.ws._read(self.path(owner['operation_id']), authority=True)
            if self.reviewed_selection_id(record):
                self.read_reviewed_context({}, {'request': record['request']})
            if record.get('native_message_ref') is not None:
                self._ordinary_cohort(record, cohort)
                if record.get('ordinary_cohort') != cohort:
                    raise Conflict('An older native cohort cannot prepare a document proposal')
            proposal = self.docx_revisions.prepare(pid, DocxRevisionInput.model_validate({
                'operation_id': 'docx-propose-' + operation_suffix, 'owner': owner,
                'file_link_ref': link, 'snapshot_ref': snapshot, 'patches': arguments['patches']}))
            record = self.ws._read(self.path(owner['operation_id']), authority=True)
            entry = {'proposal_ref': proposal['proposal_ref'], 'operation_id': 'docx-publish-' + operation_suffix,
                'proposal_status': proposal['status'], 'owner': owner}
            if cohort is not None:
                self._ordinary_cohort(record, cohort)
                if record.get('ordinary_cohort') != cohort:
                    raise Conflict('An older native cohort cannot propose a current document revision')
                entry['native_cohort_sha256'] = cohort['cohort_sha256']
            if record.get('docx_revision') not in (None, entry):
                raise Conflict('This question already has a different immutable DOCX proposal')
            record['docx_revision'] = entry
            self.ws._write(self.path(owner['operation_id']), record)
            return proposal

    def _publish_docx_revision(self, record):
        entry = record.get('docx_revision')
        if entry is None or self.docx_revisions is None:
            return
        if record.get('docx_publication_cancel_requested', False):
            return
        if entry['proposal_status'] != 'PROPOSED':
            record['artifact_error'] = {'message': '这段内容需要进一步确认，尚未生成修订文件。'}
            return
        if self.reviewed_selection_id(record):
            try:
                self.read_reviewed_context({}, {'request': record['request']})
            except (Conflict, NotFound):
                record['artifact_error'] = {'code': 'CONTEXT_CHANGED_DURING_TASK', 'message':
                    '引用的资料或记忆已经变化，修订提案保留，尚未发布新文件；请核对当前依据后继续。'}
                raise Conflict('Reviewed context changed during the task; publication held') from None
        if record.get('native_message_ref') is not None:
            cohort = self._ordinary_cohort(record, record.get('ordinary_cohort'))
            if entry.get('native_cohort_sha256') != cohort['cohort_sha256']:
                raise Conflict('An older ordinary proposal cannot be automatically published')
            latest = self.app.state.file_continuity.observe(record['request']['file_link_ref'])
            frozen = record.get('file_observation') or {}
            if (latest.get('current_sha256') != frozen.get('current_sha256')
                    or latest.get('unsaved_changes') is True):
                record['artifact_error'] = {'code': 'SOURCE_CHANGED_DURING_TASK', 'message':
                    '文件在任务进行期间有新改动；请基于最新文件继续。原文件和修订提案已保留。'}
                raise Conflict('Saved document changed during this ordinary task; publication held')
        pid = question_project(record['request'])
        if not self.docx_owner_current(pid, entry['owner']):
            raise Conflict('DOCX publication owner is no longer current; proposal retained')
        self.docx_revisions.publish(pid, entry['proposal_ref'],
            operation_id=entry['operation_id'], owner=entry['owner'])

    def _project_docx_revision(self, record, result):
        entry = record.get('docx_revision')
        if entry is None or self.docx_revisions is None:
            return
        result['docx_revision_proposal_ref'] = copy.deepcopy(entry['proposal_ref'])
        cancelled = record.get('docx_publication_cancel_requested', False)
        if result['status'] == 'PROCESSING' and not cancelled:
            result['artifact_status'] = 'PREPARING'
            return
        try:
            operation = self.docx_revisions.operation(question_project(record['request']), entry['operation_id'])
            if operation.get('status') == 'NEW_ARTIFACT':
                if operation.get('owner') != entry['owner'] or operation.get('proposal_ref') != entry['proposal_ref']:
                    raise Conflict('Saved DOCX artifact differs from this question and proposal')
                result['artifacts'] = [copy.deepcopy(operation['artifact'])]
                result['artifact_status'] = 'NEW_ARTIFACT'
                result['execution_effects'] = 'CREATE_NEW_DERIVED_DOCX'
                result['source_files_modified'] = False
            elif operation.get('status') == 'UNKNOWN_OUTCOME':
                result['artifact_status'] = 'PUBLICATION_UNKNOWN'
                result['artifact_error'] = {'message':
                    '修订文件的生成结果尚未确认；请核查原操作，勿重新生成。'}
            elif cancelled:
                result['artifact_status'] = 'CANCELLED_NOT_PUBLISHED'
                result['artifact_error'] = {'message': '已停止生成修订文件；原回复、原文件和提案保留。'}
            else:
                result['artifact_status'] = ('NEEDS_CLARIFICATION' if entry['proposal_status'] != 'PROPOSED'
                    else 'PROPOSED_NOT_PUBLISHED')
        except (Conflict, NotFound, KeyError, ValueError):
            result['artifact_status'] = 'UNAVAILABLE'
            result['artifact_error'] = {'message': '修订文件暂不可用，原回复与提案保留；请核查原操作，勿重复发送。'}
        if (record.get('artifact_error') and result.get('artifact_status')
                not in {'NEW_ARTIFACT', 'PUBLICATION_UNKNOWN', 'CANCELLED_NOT_PUBLISHED'}):
            result['artifact_error'] = copy.deepcopy(record['artifact_error'])

    @staticmethod
    def _ordinary_cohort(record, cohort):
        fields = {'native_message_ref', 'parent_operation_id', 'owner_ref', 'generation',
            'native_operation_id', 'native_user_message_id', 'native_session_id', 'sequence', 'cohort_sha256'}
        if (not isinstance(cohort, dict) or set(cohort) != fields
                or cohort['native_message_ref'] != record.get('native_message_ref')
                or cohort['parent_operation_id'] != record['request']['operation_id']
                or type(cohort['generation']) is not int or cohort['generation'] < 1
                or type(cohort['sequence']) is not int or not 1 <= cohort['sequence'] <= 8
                or digest({k: v for k, v in cohort.items() if k != 'cohort_sha256'}) != cohort['cohort_sha256']):
            raise Conflict('Ordinary document feedback must bind its exact native cohort')
        return cohort

    def ordinary_cohort_changed(self, record, cohort):
        self._ordinary_cohort(record, cohort)
        current = self.ws._read(self.path(cohort['parent_operation_id']), authority=True)
        previous = current.get('ordinary_cohort')
        if (current.get('native_message_ref') != record.get('native_message_ref')
                or current.get('docx_publication_cancel_requested', False)
                or current['generation'] != self.generation
                or (previous and cohort['sequence'] != previous['sequence'] + 1)):
            raise Conflict('Ordinary publication cohort is no longer dispatchable')
        if previous is None and cohort['sequence'] != 1:
            raise Conflict('Ordinary initial cohort must precede its followups')
        entry = current.get('docx_revision')
        history = copy.deepcopy(current.get('docx_revision_history', []))
        record.pop('docx_revision', None)
        record.pop('artifact_error', None)
        if entry:
            history.append(copy.deepcopy(entry))
        if history:
            record['docx_revision_history'] = history[-8:]
        record['ordinary_cohort'] = copy.deepcopy(cohort)
        self.ws._write(self.path(cohort['parent_operation_id']), record)

    def ordinary_terminal(self, record, cohort, status):
        self._ordinary_cohort(record, cohort)
        current = self.ws._read(self.path(cohort['parent_operation_id']), authority=True)
        if (current.get('ordinary_cohort') != cohort
                or current.get('native_message_ref') != record.get('native_message_ref')
                or current['generation'] != self.generation):
            raise Conflict('An older native cohort cannot publish an ordinary result')
        if current.get('docx_publication_cancel_requested', False):
            record['docx_publication_cancel_requested'] = True
        for key in ('docx_revision', 'docx_revision_history'):
            if key in current:
                record[key] = copy.deepcopy(current[key])
            else:
                record.pop(key, None)
        record['status'] = status
        self.ws._write(self.path(cohort['parent_operation_id']), record)
        if status == 'RETURNED' and not record.get('docx_publication_cancel_requested', False):
            try:
                self._publish_docx_revision(record)
            except Exception:
                if not record.get('artifact_error'):
                    record['artifact_error'] = {'message': '回复已收到，修订文件未完成生成；原文件和提案保留，请核查原操作。'}
                self.ws._write(self.path(cohort['parent_operation_id']), record)

    async def control(self, parent, body):
        with self.ws.lock:
            record = self.ws._read(self.path(parent), authority=False)
        if record['request'].get('native_review') is not None:
            if not isinstance(body, ReviewGuidanceInput):
                raise Conflict('This control belongs to another native task type')
            return await self.reviews.control(parent, body.model_dump(), kind='GUIDANCE')
        if record.get('native_message_ref') is not None:
            if not isinstance(body, OrdinaryGuidanceInput):
                raise Conflict('Ordinary guidance needs its exact parent reference')
            return await self.ordinary.control(parent, body.model_dump(), kind='GUIDANCE')
        raise Conflict('This historical task does not admit new running guidance')

    def control_get(self, parent, operation_id):
        with self.ws.lock:
            record = self.ws._read(self.path(parent), authority=False)
        if record['request'].get('native_review') is not None:
            return self.reviews.control_get(parent, operation_id)
        if record.get('native_message_ref') is not None:
            return self.ordinary.control_get(parent, operation_id)
        raise Conflict('This historical task has no ordinary control receipt')

    def _rework_scope(self, binding):
        """The model cannot select another project/file or reuse a prior host grant."""
        request = binding['request']
        with self.ws.lock:
            if self.closed:
                raise Conflict('Native question service closed')
            self.ws.check_host(self.ws.token)
            record = self.ws._read(self.path(request['operation_id']), authority=True)
            if (record['generation'] != self.generation or digest(record['request']) != record['request_sha256']
                    or record['request']['raw_text'] != request['prompt']):
                raise Conflict('Rework preview differs from the exact bound native question')
            ref = record['request'].get('file_source_ref')
            if ref is None or self.rework is None:
                raise Conflict('Rework preview needs an explicit selected file and configured service')
        return ref['project_id'], ref

    @staticmethod
    def _same_file(a, b):
        return all(a[key] == b[key] for key in
            ('project_id', 'workspace_id', 'binding_revision', 'binding_sha256', 'path'))

    @staticmethod
    def _guard_rework_selection(feedback, locator):
        """A model preview cannot widen the user's verified or unresolved quote."""
        payload = feedback['payload']
        if payload.get('selection_verification') == 'TEXT_RANGE_HASH_VERIFIED':
            selected = payload.get('file_selection')
            if not isinstance(selected, dict) or selected.get('kind') != 'TEXT_RANGE':
                raise Conflict('Verified feedback is missing its canonical text range')
            if locator is not None and digest(locator) != digest(selected):
                raise Conflict('QUOTE_SCOPE_CHANGED: model preview must use the exact verified text range')
        elif (payload.get('requested_selection') or {}).get('kind') == 'EXACT_QUOTE':
            raise Conflict('QUOTE_SCOPE_REQUIRES_CLARIFICATION: the requested quote has no unique verified range')

    def read_rework_context(self, _arguments, binding):
        pid, ref = self._rework_scope(binding)
        context = self.rework.context(pid, FileSource.model_validate(ref))
        requested = self.ws._read(self.path(binding['request']['operation_id']), authority=True)['request'].get('native_feedback_ref')
        if requested is not None:
            context = {**context, 'feedback': [item for item in context['feedback']
                if item['feedback_ref'] == requested], 'mappings': [item for item in context['mappings']
                if item['payload']['native_feedback_ref'] == requested]}
        feedback = [{'feedback_ref': item['feedback_ref'],
            'baseline_file_source_ref': item['payload']['baseline_file_source_ref'],
            'comment': item['payload']['comment'][:4000],
            'comment_original_length': len(item['payload']['comment']),
            'comment_sha256': hashlib.sha256(item['payload']['comment'].encode('utf-8')).hexdigest(),
            'comment_truncated': len(item['payload']['comment']) > 4000,
            'selection': item['payload']['file_selection'],
            'selection_verification': item['payload']['selection_verification'],
            **({'requested_selection': item['payload']['requested_selection']}
                if 'requested_selection' in item['payload'] else {}),
            'current_file_check': item['current_file_check']}
            for item in sorted(context['feedback'], key=lambda item: item['payload'].get('created_at', ''))[-20:]]
        mappings = [{'mapping_ref': item['mapping_ref'], 'status': item['status'],
            'baseline_file_source_ref': item['payload']['baseline_file_source_ref'],
            'task_ids': item['payload'].get('task_ids', []),
            'binding_ref': item['payload'].get('binding_ref'),
            'selected_value_sha256': item['payload'].get('selected_value_sha256'),
            'locator': item['payload'].get('locator')}
            for item in sorted(context['mappings'], key=lambda item: item['payload'].get('created_at', ''))[-20:]]
        result = {**context, 'feedback': feedback, 'mappings': mappings,
            'confirmed_proposals': context['confirmed_proposals'][-10:],
            'coverage': 'BOUNDED_SELECTED_FILE_LINEAGE_PREVIEW_ONLY',
            'default_preview_contract_policy': {
                'when': 'CONTRACTS_OMITTED_OR_EMPTY', 'mapped_tasks_only': True,
                'condition': 'MANUAL_REVIEW',
                'invalidating_kinds': ['EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN'],
                'business_dependencies_confirmed': False, 'other_task_impact': 'UNKNOWN'},
            'total_feedback': len(context['feedback']), 'total_mappings': len(context['mappings']),
            'total_proposals': len(context['confirmed_proposals'])}
        while len(canonical(result)) > 48000:
            collection = max(('feedback', 'mappings', 'confirmed_proposals'),
                key=lambda name: len(canonical(result[name])))
            if not result[collection]:
                raise Conflict('Rework context metadata exceeds the bounded preview budget')
            result[collection].pop(0)
        result['truncated'] = any(item['comment_truncated'] for item in result['feedback']) or any(len(result[key]) != result[total] for key, total in
            [('feedback', 'total_feedback'), ('mappings', 'total_mappings'), ('confirmed_proposals', 'total_proposals')])
        return result

    def resolve_native_feedback(self, arguments, binding):
        pid, ref = self._rework_scope(binding)
        requested = self.ws._read(self.path(binding['request']['operation_id']), authority=True)['request'].get('native_feedback_ref')
        if requested is not None and arguments['native_feedback_ref'] != requested:
            raise Conflict('Typed native feedback cannot switch to another recorded selection')
        feedback = self.app.state.native_feedback.get(pid, arguments['native_feedback_ref']['id'])
        if (feedback['feedback_ref'] != arguments['native_feedback_ref']
                or not self._same_file(feedback['payload']['baseline_file_source_ref'], ref)):
            raise Conflict('Feedback belongs to another source or differs from its exact record')
        self._guard_rework_selection(feedback, arguments.get('locator'))
        operation = 'native-map-' + digest([binding['request']['operation_id'], arguments])
        result = self.rework.map(pid, MappingInput.model_validate({**arguments,
            'operation_id': operation, 'user_confirmed': False}))
        self._remember_rework(binding, pid, result)
        return result

    def preview_native_rework(self, arguments, binding):
        pid, ref = self._rework_scope(binding)
        mapping = self.rework.get(pid, arguments['mapping_ref']['id'])
        if (mapping['record_ref'] != arguments['mapping_ref'] or mapping['kind'] != 'MAPPING'
                or not self._same_file(mapping['payload']['baseline_file_source_ref'], ref)):
            raise Conflict('Rework mapping belongs to another file or differs from its exact record')
        feedback_ref = mapping['payload']['native_feedback_ref']
        requested = self.ws._read(self.path(binding['request']['operation_id']), authority=True)['request'].get('native_feedback_ref')
        if requested is not None and feedback_ref != requested:
            raise Conflict('Typed native preview cannot switch to another recorded feedback')
        feedback = self.app.state.native_feedback.get(pid, feedback_ref['id'])
        if (feedback['feedback_ref'] != feedback_ref
                or not self._same_file(feedback['payload']['baseline_file_source_ref'], ref)):
            raise Conflict('Mapping feedback belongs to another source or differs from its exact record')
        self._guard_rework_selection(feedback, mapping['payload'].get('locator'))
        operation = 'native-preview-' + digest([binding['request']['operation_id'], arguments])
        body = NativePlanInput.model_validate({**arguments,
            'operation_id': operation, 'classification_origin': 'OPENCODE_PROPOSAL'})
        if mapping['status'] == 'MAPPED' and not body.contracts:
            selected = mapping['payload']
            # Immutable source provenance is host evidence, not a business input
            # declaration. MANUAL_REVIEW leaves every task's impact UNKNOWN.
            body = NativePlanInput.model_validate({**body.model_dump(), 'contracts': [
                {'task_id': task, 'inputs': [{
                    'binding_ref': selected['binding_ref'],
                    'input_value_sha256': selected['selected_value_sha256'],
                    'condition': 'MANUAL_REVIEW',
                    'invalidating_kinds': ['EXPRESSION', 'DATA', 'CONSTRAINT', 'UNKNOWN'],
                    'verification_note': 'HOST_SOURCE_VERSION_ONLY: the host verified only the selected mapped source version. '
                        'Business dependencies, independence, semantic correctness and acceptance are unconfirmed; manual review is required.',
                    'verification_refs': [selected['binding_ref']]}]}
                for task in selected['task_ids']]})
        result = self.rework.plan(pid, body)
        self._remember_rework(binding, pid, result)
        return result

    def _remember_rework(self, binding, pid, result):
        """Keep real preview references with the question; never persist model authority."""
        with self.ws.lock:
            self._rework_scope(binding)
            path = self.path(binding['request']['operation_id'])
            record = self.ws._read(path, authority=True)
            entry = {'project_id': pid, 'record_ref': result['record_ref']}
            references = record.get('rework_records', [])
            if entry not in references:
                record['rework_records'] = [*references, entry][-16:]
                self.ws._write(path, record)

    def check_snapshot(self, observation):
        try:
            return (observation.get('context_ref') is not None
                and observation.get('automatic_input_authorized') is False
                and self.read_context(observation['context_ref']) == observation)
        except Exception:
            return False

    def read_visual(self, arguments, binding):
        request = binding['request']
        if not self.transport.provider_registry.resolve(request['provider_profile_ref']).supports_images:
            raise Conflict('This provider has not declared image input support')
        reference = request['desktop_intent']['context_ref']
        if reference is None: raise Conflict('No frozen current-page frame is bound')
        snapshot = self.context.lookup(reference)
        frame = snapshot['observation'].get('observations', {}).get('visual')
        preview = snapshot.get('preview', {})
        if not frame or preview.get('status') != 'AVAILABLE': raise Conflict('No exact registered frame is available')
        image = preview['image_ref']
        raw = self.context.image({key: image[key] for key in ('id', 'revision', 'sha256')})
        return frame_view(raw, context_ref=reference, frame_ref=frame['frame_ref'],
            image_ref=image, pixel_source=preview.get('pixel_source', 'UNKNOWN'), region=arguments.get('region'))

    def get(self, operation_id):
        path = self.path(operation_id)
        if not path.exists(): raise NotFound('Native question operation does not exist')
        with self.ws.lock:
            record = self.ws._read(path, authority=False)
            if record['request'].get('native_review') is not None:
                reviews = getattr(self, 'reviews', None)
                if reviews is None:
                    raise Conflict('Native review service is unavailable; original request retained')
                result = reviews.project(record)
                if self.reviewed_selection_id(record):
                    result['context_selection_id'] = self.reviewed_selection_id(record)
                recovery=getattr(self.app.state,'native_context_recovery',None)
                return recovery.project(record,result) if recovery is not None else result
            if record['generation'] == self.generation: self.ws._read(path, authority=True)
            if digest(record['request']) != record['request_sha256']:
                raise Conflict('Native question source integrity failed')
            if record['generation'] != self.generation and record['status'] == 'PROCESSING':
                record['status'] = 'UNKNOWN_OUTCOME'
                self.ws._write(path, record)
            result = {'operation_id': operation_id, 'status': record['status'], 'automatic_retry': False,
                'request_sha256': record['request_sha256'],
                'request_project_id': record['request'].get('project_id'),
                'provider_profile_ref': record['request'].get('provider_profile_ref'),
                'automatic_input_authorized': False, 'artifacts': [], 'events': [],
                'changes_applied': False, 'execution_effects': 'READONLY',
                'record_origin': 'CURRENT_HOST' if record['generation'] == self.generation else 'HISTORICAL_READONLY',
                'context_ref': record['request']['context_ref'],
                'conversation_id': record['request'].get('conversation_id')}
            if self.reviewed_selection_id(record):
                result['context_selection_id'] = self.reviewed_selection_id(record)
            if record.get('conversation_scope'):
                result['conversation_ref'] = copy.deepcopy(record['conversation_scope']['conversation_ref'])
            if record.get('reviewed_context') is not None:
                result['context_summary'] = copy.deepcopy(record['reviewed_context'])
            if record['request'].get('input_attachments'):
                result['input_attachments'] = copy.deepcopy(record['request']['input_attachments'])
            result['file_source_ref']=record['request'].get('file_source_ref')
            result['file_link_ref']=record['request'].get('file_link_ref')
            result['project_id']=question_project(record['request'])
            if record.get('file_observation'):
                result['file_observation'] = record['file_observation']
            result['rework_previews'] = []
            if self.rework is not None and result['file_source_ref'] is not None:
                for entry in record.get('rework_records', []):
                    if entry['project_id'] != result['file_source_ref']['project_id']:
                        raise Conflict('Saved rework preview changed its bound project')
                    preview = self.rework.get(entry['project_id'], entry['record_ref']['id'])
                    if (preview['record_ref'] != entry['record_ref']
                            or not self._same_file(preview['payload']['baseline_file_source_ref']
                                if preview['kind'] == 'MAPPING' else
                                self.rework.get(entry['project_id'], preview['payload']['mapping_ref']['id'])['payload']['baseline_file_source_ref'],
                                result['file_source_ref'])):
                        raise Conflict('Saved rework preview changed its exact source reference')
                    result['rework_previews'].append(preview)
            if result['file_source_ref'] is not None:
                try:
                    selected_file_bytes(self.app.state.local_workspaces,result['file_source_ref'])
                    result['file_review_status']='CURRENT'
                except (Conflict,NotFound): result['file_review_status']='STALE_OR_UNAVAILABLE'
                result['file_content_scope']='EXPLICIT_SELECTED_FILE_SEPARATE_FROM_PAGE'
                result['file_mutation_adapter']='NOT_ENABLED_BY_READONLY_QUESTION'
            if record.get('error'): result['error'] = copy.deepcopy(record['error'])
            if record.get('native_message_ref') is not None:
                ordinary = getattr(self, 'ordinary', None)
                if ordinary is None:
                    raise Conflict('Original ordinary-session service is unavailable; record retained')
                result = ordinary.project(record, result)
                self._project_docx_revision(record, result)
                self.action_proposals.project(record, result)
                recovery=getattr(self.app.state,'native_context_recovery',None)
                return recovery.project(record,result) if recovery is not None else result
            if record.get('execution'):
                identity = record['execution']
                row = self.transport.inspect(identity['project_id'], identity['id'])
                result['execution'] = {k: row[k] for k in ('id', 'session_id', 'execution_status')}
                result['events'] = self.transport.stream_events(identity['project_id'], identity['id'])[-200:]
                status = row['execution_status']
                result['status'] = 'PROCESSING' if status in {'QUEUED', 'RUNNING'} else status
                if record['status'] == 'UNKNOWN_OUTCOME': result['status'] = 'UNKNOWN_OUTCOME'
                if status == 'RETURNED' and row['result']:
                    native = json.loads(row['result'])
                    answer = native.get('candidate_json') or {}
                    result['text'] = answer.get('text', '')
                    result['questions'] = answer.get('questions', [])
                    result['usage'] = native.get('usage')
                if (record['status'] == 'PROCESSING' and record.get('docx_revision')
                        and not record.get('docx_publication_cancel_requested', False)
                        and result['status'] == 'RETURNED'):
                    result['status'] = 'PROCESSING'
            self._project_docx_revision(record, result)
            self.action_proposals.project(record, result)
            return result

    async def submit(self, body, host_token, *, deferred=False):
        body = NativeQuestionInput.model_validate(body).request_dict()
        if self.closed: raise Conflict('Native question service is closed')
        if self.intents is None: raise Conflict('Native message authority is not configured')
        if body.get('native_feedback_ref') is not None:
            feedback = self.app.state.native_feedback.get(question_project(body), body['native_feedback_ref']['id'])
            context = feedback['payload'].get('screen_annotations', {}).get('context_ref') or {}
            if (feedback['feedback_ref'] != body['native_feedback_ref']
                    or context.get('kind') != 'NATIVE_IDE_FEEDBACK'
                    or context.get('conversation_id') != body['conversation_id']
                    or any(not isinstance(context.get(key), str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', context[key])
                        for key in ('parent_operation_id', 'feedback_operation_id'))
                    or feedback['payload']['baseline_file_source_ref'] != body['file_source_ref']):
                raise Conflict('Typed native feedback belongs to another exact discussion/file')
            self._ide_source(question_project(body), body['conversation_id'], context.get('parent_operation_id'))
            original = self.ide_feedback_operation(question_project(body), body['conversation_id'],
                context['parent_operation_id'], context.get('feedback_operation_id'))
            if original.get('feedback_ref') != body['native_feedback_ref']:
                raise Conflict('Typed feedback has no matching immutable original operation receipt')
            self._guard_rework_selection(feedback, None)
        if body.get('native_review') is not None:
            reviews = getattr(self, 'reviews', None)
            if reviews is None: raise Conflict('Native review service is unavailable; no prompt dispatched')
            return await reviews.submit(body, host_token)
        ordinary = getattr(self, 'ordinary', None)
        if ordinary is not None:
            return await ordinary.submit(body, host_token, deferred=deferred)
        record, request = self._prepare_ordinary(body, host_token)
        if request is None:
            return self.get(body['operation_id'])
        path = self.path(body['operation_id'])
        try:
            row = self.transport.prepare(request)
            with self.ws.lock:
                record['execution'] = {'project_id': row['project_id'], 'id': row['id']}
                self.ws._write(path, record)
            task = asyncio.create_task(self.run(body['operation_id'], row))
            self.tasks[body['operation_id']] = task
        except Conflict:
            with self.ws.lock:
                record.update(status='UNAVAILABLE', error={'message': '当前模型、上下文或固定 OpenCode 环境不可用；请求未自动重试。'})
                self.ws._write(path, record)
        return self.get(body['operation_id'])

    def _prepare_ordinary(self, body, host_token):
        body = NativeQuestionInput.model_validate(body).request_dict()
        if self.closed or self.intents is None:
            raise Conflict('Native question authority is unavailable')
        if body.get('native_review') is not None:
            raise InvalidInput('Ordinary admission cannot adopt a native review')
        path = self.path(body['operation_id'])
        with self.ws.lock:
            if path.exists():
                prior = self.ws._read(path, authority=False)
                if NativeQuestionInput.model_validate(prior['request']).request_dict() != body: raise Conflict('Operation is already bound to another native question')
                return prior, None
            conversation_scope = self.conversation_scope(body)
            reviewed_context = self.admit_reviewed_context(body, conversation_scope)
            if body['context_ref'] is not None: self.read_context(body['context_ref'])
            attachment_sources = self.input_sources.validate(body.get('input_attachments', []))
            if body['file_source_ref'] is not None: selected_file_bytes(self.app.state.local_workspaces,body['file_source_ref'])
            if question_project(body) is not None:
                self.app.state.store.project(question_project(body))
            observation = None
            if body['file_link_ref'] is not None:
                source = self.app.state.file_continuity.observe(body['file_link_ref'])
                parsed = source['parsed']
                # Keep the file version and bounded annotation metadata, not a
                # second copy of the full document in every message record.
                comments, size = [], 0
                for note in parsed.get('comments', []):
                    text = str(note.get('text',''))[:1000]
                    if len(comments) >= 16 or size + len(text) > 6000:
                        break
                    comments.append({**note,'text':text});size += len(text)
                observation = {key:source[key] for key in ('link_ref','snapshot_ref','status',
                    'baseline_sha256','current_sha256','byte_length','observed_at','unsaved_changes','coverage')}
                observation['parsed'] = {key:parsed[key] for key in ('status','format','structure')}
                observation['comments'] = comments
                observation['comments_total'] = parsed.get('comments_total',len(comments))
                observation['comments_truncated'] = (parsed.get('comments_truncated',False) or
                    len(comments) < observation['comments_total'] or any(len(str(note.get('text',''))) > 1000
                        for note in parsed.get('comments',[])[:len(comments)]))
                observation['warnings'] = parsed.get('warnings',[])[:16]
            recorded = self.intents.record_message({k: body[k] for k in ('operation_id', 'raw_text', 'context_ref')}, host_token=host_token)
            record = {'request': body, 'request_sha256': digest(body), 'message_ref': recorded['message_ref'],
                'created_at': datetime.now(timezone.utc).isoformat(),
                'generation': self.generation, 'status': 'PROCESSING', 'execution': None}
            if reviewed_context is not None:
                record.update(reviewed_context=reviewed_context, reviewed_context_sha256=digest(reviewed_context))
            if conversation_scope is not None:
                record.update(conversation_scope=conversation_scope, conversation_scope_sha256=digest(conversation_scope))
            if body.get('input_attachments'):
                record['input_attachment_sources'] = attachment_sources
            if observation is not None:
                record['file_observation'] = observation
            file_proposal = self.capture_input_file(record)
            if file_proposal is not None:
                record['context_file_proposal'] = file_proposal
            discussion_proposal = self.capture_input_discussion(record)
            if discussion_proposal is not None:
                record['context_discussion_proposal'] = discussion_proposal
            if body['provider_profile_ref'] is None:
                record.update(status='NOT_CONFIGURED', error={'message': '请先配置本机或内网模型；未启动模型，也未使用付费 API 替代。'})
            self.ws._write(path, record)
        if record['status'] != 'PROCESSING': return record, None
        request = {'project_id': question_project(body), 'operation_id': body['operation_id'], 'prompt': body['raw_text'],
            'mode': 'OBSERVE', 'desktop_intent': {'user_message_ref': recorded['message_ref'], 'context_ref': body['context_ref']},
            'provider_profile_ref': body['provider_profile_ref'],
            'budget': {'max_calls': 8, 'timeout_seconds': 180, 'max_output_tokens': 4096, 'max_tool_calls': 8},
            'output_schema': ANSWER_SCHEMA, 'method_ids': ['read_message', 'read_context', 'read_history']}
        if conversation_scope is not None:
            request['method_ids'].append('read_project_context')
        if self.reviewed_selection_id(record):
            request['method_ids'].append('read_reviewed_context')
        if body.get('input_attachments'):
            request['method_ids'].extend(['list_input_attachments', 'read_input_attachments'])
        if (body['context_ref'] is not None and not any(body.get(key) is not None for key in
                ('file_source_ref', 'file_link_ref', 'native_feedback_ref', 'native_review'))
                and (not body.get('input_attachments') or len(body['input_attachments']) == 1 and body['input_attachments'][0]['kind'] == 'CURRENT_PAGE')):
            request['method_ids'].append('propose_native_append')
        if body['file_source_ref'] is not None:
            request['method_ids'].append('read_selected_file')
            if self.rework is not None:
                request['method_ids'].extend(['read_rework_context', 'resolve_native_feedback', 'preview_native_rework'])
        if body['file_link_ref'] is not None:
            request['method_ids'].append('read_linked_file')
            if (self.docx_revisions is not None and observation is not None
                    and observation['parsed'].get('format', '').lower() == 'docx'):
                request['method_ids'].extend(['read_docx_revision_context', 'locate_docx_revision',
                    'propose_docx_revision'])
            link = self.app.state.file_continuity.get(body['file_link_ref'])
            if FrozenLinkedRequirements.eligible(link):
                request['method_ids'].append('read_linked_requirements')
        try:
            supports_images = self.transport.provider_registry.resolve(body['provider_profile_ref']).supports_images
            if any(item['kind'] == 'SCREENSHOT' for item in body.get('input_attachments', [])) and not supports_images:
                raise Conflict('Selected model does not support attached screenshots; no model was started')
            if supports_images:
                request['method_ids'].append('read_visual')
        except Conflict:
            with self.ws.lock:
                unsupported_image = locals().get('supports_images') is False and any(
                    item['kind'] == 'SCREENSHOT' for item in body.get('input_attachments', []))
                record.update(status='UNAVAILABLE', error={'message':
                    '所选模型未声明支持截图输入；请选择支持图片的模型，原文字和附件保留。' if unsupported_image else
                    '当前模型、上下文或固定环境不可用；请求未自动重试。'})
                self.ws._write(path, record)
            return record, None
        if conversation_scope is not None:
            try:
                source = {'kind': 'native', 'project_id': question_project(body), 'conversation_id': body['conversation_id']}
                record['conversation_organization'] = self.app.state.conversation_threads.note_message(
                    conversation_scope, source, body['operation_id'], record['request_sha256'], created_at=record['created_at'])
                self.ws._write(path, record)
            except (Conflict, NotFound, OSError, sqlite3.Error):
                record.update(status='UNAVAILABLE', error={'code': 'CONVERSATION_ADMISSION_UNAVAILABLE',
                    'message': '会话信息暂未保存，本条未启动；文字和附件保留。'})
                self.ws._write(path, record)
                return record, None
        return record, request

    async def run(self, operation_id, row):
        try:
            value = await self.transport.start(row['project_id'], row['id'])
            status = value['execution_status']
        except Exception:
            status = 'UNKNOWN_OUTCOME'
        finally:
            with self.ws.lock:
                record = self.ws._read(self.path(operation_id), authority=True)
                if locals().get('status') == 'RETURNED' and record.get('docx_revision'):
                    try:
                        self._publish_docx_revision(record)
                    except Exception:
                        record['artifact_error'] = {'message':
                            '回复已收到，修订文件未完成生成；原文件和提案保留，请核查原操作，勿自动重发。'}
                record['status'] = locals().get('status', 'UNKNOWN_OUTCOME')
                self.ws._write(self.path(operation_id), record)
            observer = getattr(self.app.state, 'global_supervisor', None)
            if observer is not None:
                try:
                    observer.wake('OWNER_CHANGED')
                except Exception:
                    pass  # A committed task result is independent of reminders.
            self.tasks.pop(operation_id, None)

    async def cancel(self, operation_id):
        self.get(operation_id)
        existing = self.ws._read(self.path(operation_id), authority=False)
        if existing['request'].get('native_review') is not None:
            return await self.reviews.cancel(operation_id)
        if existing.get('native_message_ref') is not None:
            ordinary = getattr(self, 'ordinary', None)
            if ordinary is None:
                raise Conflict('Original ordinary-session service is unavailable; record retained')
            return await ordinary.cancel(operation_id)
        with self.ws.lock:
            record = self.ws._read(self.path(operation_id), authority=False)
            observation = record.get('file_observation') or {}
            if (record['generation'] == self.generation and not self.closed
                    and observation.get('parsed', {}).get('format', '').lower() == 'docx'
                    and record['status'] in {'PROCESSING', 'RETURNED'}):
                self.ws._read(self.path(operation_id), authority=True)
                # Native inference can already have returned while its owned
                # transport is still cleaning up. Persist the user's intent
                # before awaiting that transport; its terminal cancel is a
                # no-op and must not grant publication after this point.
                projection = {'status': 'RETURNED'}
                self._project_docx_revision(record, projection)
                if projection.get('artifact_status') != 'NEW_ARTIFACT':
                    record['docx_publication_cancel_requested'] = True
                    self.ws._write(self.path(operation_id), record)
        if record['execution']:
            row = record['execution']
            await self.transport.request_cancel(row['project_id'], row['id'])
        return self.get(operation_id)

    async def close(self):
        self.closed = True
        recovery=getattr(self.app.state,'native_context_recovery',None)
        if recovery is not None:await recovery.close()
        if getattr(self, 'ordinary', None) is not None:
            await self.ordinary.close()
        if getattr(self, 'reviews', None) is not None:
            await self.reviews.close()
        await self.transport.close()
        await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)


def register_native_message_routes(app, *, authority, context_rpc):
    value = NativeMessages(app, authority, context_rpc)
    app.state.native_messages = value
    app.state.desktop_transport = value.transport
    from apps.api.native_context_recovery import NativeContextRecovery,NativeContextPreparation,NativeContextRestart
    recovery=NativeContextRecovery(value);app.state.native_context_recovery=recovery
    def host(x_eeas_desktop_host: str | None = Header(None)):
        authority.check_host(x_eeas_desktop_host)
        return x_eeas_desktop_host

    @app.post('/api/desktop-host/native-messages/{parent}/context-recovery/prepare')
    def prepare_context_recovery(parent:Id,body:NativeContextPreparation,_host=Depends(host)):
        return recovery.prepare(parent,body)

    @app.post('/api/desktop-host/native-messages/{parent}/context-recovery')
    async def restart_context(parent:Id,body:NativeContextRestart,_host=Depends(host)):
        return await recovery.restart(parent,body)

    @app.get('/api/desktop-host/native-messages/{parent}/context-recovery/{operation_id}')
    def inspect_context_recovery(parent:Id,operation_id:Id,_host=Depends(host)):
        return recovery.operation(parent,operation_id)

    @app.post('/api/desktop-host/native-messages')
    async def send(body: NativeQuestionInput, token=Depends(host)):
        return await value.submit(body.model_dump(), token, deferred=True)

    @app.get('/api/desktop-host/native-conversations')
    def conversations(project_id: Id | None = None, limit: int = Query(default=20, ge=1, le=30), _host=Depends(host)):
        return value.ide_conversations(project_id, limit)

    @app.get('/api/desktop-host/native-conversations/{conversation_id}')
    def conversation(conversation_id: Id, project_id: Id | None = None,
                     limit: int = Query(default=20, ge=1, le=40), _host=Depends(host)):
        return value.ide_conversation(project_id, conversation_id, limit)

    @app.get('/api/desktop-host/native-conversations/{conversation_id}/operations/{operation_id}')
    def conversation_operation(conversation_id: Id, operation_id: Id, project_id: Id | None = None, _host=Depends(host)):
        return value.ide_operation(project_id, conversation_id, operation_id)

    file_base = '/api/desktop-host/native-conversations/{conversation_id}/operations/{parent}'

    @app.get(file_base + '/file')
    def ide_file(conversation_id: Id, parent: Id, project_id: Id,
                 version: Literal['CURRENT_SAVED', 'ORIGINAL_SAVED'] = 'CURRENT_SAVED', _host=Depends(host)):
        return value.ide_file(project_id, conversation_id, parent, version)

    @app.get(file_base + '/rework-preview')
    def ide_preview(conversation_id: Id, parent: Id, project_id: Id,
                    plan_ref: str = Query(max_length=1024), _host=Depends(host)):
        try:
            ref = RevisionRef.model_validate_json(plan_ref).model_dump()
        except ValueError:
            raise InvalidInput('An exact persisted plan reference is required') from None
        return value.ide_preview(project_id, conversation_id, parent, ref)

    @app.post(file_base + '/feedback')
    def ide_feedback(conversation_id: Id, parent: Id, project_id: Id,
                     body: IdeFeedbackInput, _host=Depends(host)):
        return value.ide_feedback(project_id, conversation_id, parent, body)

    @app.get(file_base + '/feedback-operations/{operation_id}')
    def ide_feedback_operation(conversation_id: Id, parent: Id, project_id: Id,
                               operation_id: Id, _host=Depends(host)):
        return value.ide_feedback_operation(project_id, conversation_id, parent, operation_id)

    @app.post(file_base + '/rework-apply')
    def ide_apply(conversation_id: Id, parent: Id, project_id: Id,
                  body: IdeApplyInput, _host=Depends(host)):
        return value.ide_apply(project_id, conversation_id, parent, body)

    @app.get(file_base + '/rework-operations/{operation_id}')
    def ide_apply_operation(conversation_id: Id, parent: Id, project_id: Id,
                            operation_id: Id, _host=Depends(host)):
        return value.ide_apply_operation(project_id, conversation_id, parent, operation_id)

    @app.get('/api/desktop-host/native-messages/review-capabilities')
    def review_capabilities(conversation_id: Id, provider_profile_ref: str, project_id: Id | None = None,
                            file_link_ref: str | None = None, selection: str | None = None,
                            kind: Literal['CAPABILITY_TASK'] | None = None, _host=Depends(host)):
        from packages.domain.native_capabilities import _json
        def decoded(raw, limit=8192):
            if raw is None: return None
            if len(raw.encode('utf-8')) > limit:
                raise InvalidInput('Review catalogue input exceeds 8 KiB' if limit == 8192
                    else 'Capability catalogue selection exceeds 96 KiB')
            try: return _json(raw)
            except (ValueError, TypeError): raise InvalidInput('Review catalogue input is not finite JSON') from None
        reviews = getattr(value, 'reviews', None)
        if reviews is None: raise Conflict('Native review service is unavailable')
        arguments = {'project_id': project_id, 'conversation_id': conversation_id,
            'provider_profile_ref': decoded(provider_profile_ref), 'file_link_ref': decoded(file_link_ref),
            'selection': decoded(selection, 96 * 1024 if kind == 'CAPABILITY_TASK' else 8192)}
        if kind is not None:
            arguments['kind'] = kind
        return reviews.catalog(**arguments)

    @app.post('/api/desktop-host/native-messages/{parent}/guidance')
    async def review_guidance(parent: Id, body: ReviewGuidanceInput | OrdinaryGuidanceInput, _host=Depends(host)):
        return await value.control(parent, body)

    @app.post('/api/desktop-host/native-messages/{parent}/permissions/{request_id}/reply')
    async def review_permission(parent: Id, request_id: str, body: ReviewPermissionInput, _host=Depends(host)):
        return await value.reviews.control(parent, body.model_dump(), kind='PERMISSION', request_id=request_id)

    @app.post('/api/desktop-host/native-messages/{parent}/questions/{request_id}/reply')
    async def review_question(parent: Id, request_id: str, body: ReviewQuestionInput, _host=Depends(host)):
        return await value.reviews.control(parent, body.model_dump(), kind='QUESTION', request_id=request_id)

    @app.post('/api/desktop-host/native-messages/{parent}/questions/{request_id}/reject')
    async def review_question_reject(parent: Id, request_id: str, body: ReviewQuestionRejectInput, _host=Depends(host)):
        return await value.reviews.control(parent, body.model_dump(), kind='QUESTION_REJECT', request_id=request_id)

    @app.get('/api/desktop-host/native-messages/{parent}/controls/{operation_id}')
    def review_control(parent: Id, operation_id: Id, _host=Depends(host)):
        return value.control_get(parent, operation_id)

    @app.get('/api/desktop-host/native-messages/{operation_id}')
    def get(operation_id: Id, _host=Depends(host)):
        return value.get(operation_id)

    @app.post('/api/desktop-host/native-messages/{operation_id}/cancel')
    async def cancel(operation_id: Id, _host=Depends(host)):
        return await value.cancel(operation_id)
    return value
