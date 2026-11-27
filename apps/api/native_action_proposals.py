"""Original-turn action drafts. No input, foreground handoff or approval."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib

from pydantic import Field, model_validator

from apps.api.native_append import AppendPrepareInput, AppendSpan
from packages.domain.discovery_schema import RevisionRef
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict
from packages.domain.tasks import digest


class NativeAppendProposalInput(RequestModel):
    start: int = Field(strict=True, ge=0)
    end: int = Field(strict=True, gt=0)
    quote: str = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def literal_single_line(self):
        if self.end <= self.start or any(ord(c) < 32 or ord(c) == 127 for c in self.quote):
            raise ValueError('A nonempty exact single-line span is required')
        try:
            units = len(self.quote.encode('utf-16-le')) // 2
        except UnicodeEncodeError:
            raise ValueError('Complete Unicode is required') from None
        if units > 256:
            raise ValueError('At most 256 UTF16 units may be proposed')
        return self


DESCRIPTOR = {'id': 'propose_native_append', 'route': 'ORIGINAL_MESSAGE_LITERAL_APPEND_PROPOSAL',
    'readonly': True, 'effects': [], 'input_schema': {'type': 'object', 'additionalProperties': False,
        'required': ['start', 'end', 'quote'], 'properties': {
            'start': {'type': 'integer', 'minimum': 0}, 'end': {'type': 'integer', 'minimum': 1},
            'quote': {'type': 'string', 'minLength': 1, 'maxLength': 256}}},
    'coverage': 'EXACT_ORIGINAL_USER_LITERAL_CODEPOINT_SPAN_SINGLE_CLASSIC_NOTEPAD_APPEND_DRAFT_NO_INPUT_OR_PERMISSION',
    'qualification': 'IMPLEMENTED', 'cost': {'latency': 'LOCAL_READ_AND_ONE_IMMUTABLE_DRAFT',
        'tokens': 'BOUNDED_LITERAL_DRAFT', 'measured': False},
    'execution': {'can_background': True, 'background_qualification': 'QUALIFIED_LOCAL_READONLY',
        'requires_foreground_input': False, 'focus_effect': 'NONE'}}


def _time(value):
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise Conflict('Original action draft deadline is not timezone-bound')
    return result


def _source(record):
    body = record['request']
    return {'kind': 'NATIVE_MESSAGE', 'operation_id': body['operation_id'],
        'request_sha256': record['request_sha256'], 'project_id': body.get('project_id'),
        'conversation_id': body.get('conversation_id'), 'context_ref': deepcopy(body['context_ref']),
        'provider_profile_ref': deepcopy(body['provider_profile_ref']),
        'raw_text_sha256': hashlib.sha256(body['raw_text'].encode('utf-8')).hexdigest()}


class NativeActionProposals:
    def __init__(self, native):
        self.native, self.ws = native, native.ws

    def path(self, parent):
        return self.ws._path('native-action-proposals', parent + '.json')

    def _admission(self, record, request=None, binding=None):
        native = self.native
        if record.get('native_message_ref') is not None:
            ordinary = getattr(native, 'ordinary', None)
            if ordinary is None:
                raise Conflict('Original ordinary owner is unavailable')
            a = ordinary.validate_admission(record['native_message_ref'])
            if (a['body_sha256'] != record['request_sha256'] or a['body'] != record['request'] or
                    request is not None and a['request'] != request):
                raise Conflict('Action proposal differs from the original ordinary admission')
            owner = record.get('owner') or {}
            cohort = native._ordinary_cohort(record, record.get('ordinary_cohort'))
            if cohort['owner_ref'] != owner.get('session_ref') or cohort['generation'] != owner.get('generation'):
                raise Conflict('Action proposal differs from the original cohort owner')
            if binding is not None:
                bound_cohort = native._ordinary_cohort(record, binding.get('native_cohort'))
                context = ordinary.sessions.active.get((owner.get('session_ref') or {}).get('id'))
                if (binding.get('native_message_ref') != record['native_message_ref'] or
                        bound_cohort != cohort or binding.get('owner_ref') != owner.get('session_ref') or
                        binding.get('generation') != owner.get('generation') or
                        binding.get('opencode_session_id') != cohort['native_session_id'] or
                        not context or context.get('closing') or context['generation'] != binding.get('generation') or
                        context['session_id'] != binding.get('opencode_session_id')):
                    raise Conflict('Action proposal belongs to another original cohort')
                manifest, _ = ordinary.sessions._manifest(owner['project_id'], owner['session_ref']['id'], owner['session_ref'])
                ordinary._current(manifest)
            return {'kind': 'ORDINARY', 'reference': deepcopy(record['native_message_ref']),
                'cohort_sha256': cohort['cohort_sha256'],
                'deadline': a['absolute_deadline']}
        identity = record.get('execution')
        if not identity:
            raise Conflict('Original execution admission is unavailable')
        row = native.transport.inspect(identity['project_id'], identity['id'])
        import json
        actual = json.loads(row['request']) if isinstance(row['request'], str) else row['request']
        if (row['request_hash'] != digest(actual) or binding is not None and row['execution_status'] != 'RUNNING' or
                request is not None and actual != request or
                binding is not None and binding.get('execution_ref') != {'project_id': row['project_id'],
                    'execution_id': row['id'], 'request_sha256': row['request_hash']}):
            raise Conflict('Action proposal belongs to another original execution')
        return {'kind': 'EXECUTION', 'reference': {'project_id': row['project_id'], 'execution_id': row['id'],
            'request_sha256': row['request_hash']}, 'cohort_sha256': None,
            'deadline': (_time(row['created_at']) + timedelta(seconds=actual['budget']['timeout_seconds'])).isoformat()}

    def propose(self, arguments, binding):
        span = NativeAppendProposalInput.model_validate(arguments).model_dump()
        request = binding['request']
        with self.ws.lock:
            if self.native.closed:
                raise Conflict('Original native owner is closed')
            self.ws.check_host(self.ws.token)
            record = self.ws._read(self.native.path(request['operation_id']), authority=True)
            body = record['request']
            effective_project = (body.get('project_id') or (body.get('file_source_ref') or {}).get('project_id')
                or (body.get('file_link_ref') or {}).get('project_id'))
            request_project_matches = (request['project_id'] == effective_project or
                effective_project is None and record.get('native_message_ref') is None and
                request['project_id'] == (record.get('execution') or {}).get('project_id'))
            if (record['generation'] != self.native.generation or record['request_sha256'] != digest(body) or
                    record['status'] != 'PROCESSING' or body.get('native_review') is not None or
                    any(body.get(key) is not None for key in ('file_source_ref', 'file_link_ref', 'native_feedback_ref')) or
                    'propose_native_append' not in request.get('method_ids', []) or
                    request['mode'] != 'OBSERVE' or request['prompt'] != body['raw_text'] or
                    request['desktop_intent'] != {'user_message_ref': record['message_ref'], 'context_ref': body['context_ref']} or
                    request['provider_profile_ref'] != body['provider_profile_ref'] or body['context_ref'] is None or
                    not request_project_matches):
                raise Conflict('Action proposal needs the exact current original request')
            self.native.transport._source(request)
            admission = self._admission(record, request, binding)
            if _time(admission['deadline']) <= datetime.now(timezone.utc):
                raise Conflict('Original task deadline expired; no proposal replay')
            text = body['raw_text']
            if span['end'] > len(text) or text[span['start']:span['end']] != span['quote']:
                raise Conflict('Action proposal span differs from the original user text')
            child = 'native-append-' + digest([body['operation_id'], record['request_sha256'], span])[:48]
            prepare = AppendPrepareInput(operation_id=child, context_ref=body['context_ref'], raw_text=text,
                payload_source={'kind': 'RAW_INSTRUCTION', 'span': AppendSpan.model_validate(span).model_dump(), 'reply_ref': None},
                provider_profile_ref=body['provider_profile_ref']).model_dump()
            payload = {'version': 1, 'kind': 'CLASSIC_NOTEPAD_APPEND', 'source': _source(record),
                'request': prepare, 'host_generation': record['generation'], 'admission': admission}
            sha = digest(payload)
            value = {'proposal_ref': {'id': sha, 'revision': 1, 'sha256': sha}, 'payload': payload}
            path = self.path(body['operation_id'])
            if path.exists():
                if self.ws._read(path, authority=True) != value:
                    raise Conflict('The original parent already has a different action proposal')
            else:
                self.ws._write(path, value)
            return {'kind': 'CLASSIC_NOTEPAD_APPEND', 'proposal_ref': value['proposal_ref'],
                'parent_operation_id': body['operation_id'], 'parent_request_sha256': record['request_sha256'],
                'status': 'RECORDED_DRAFT', 'execution_authorized': False, 'automatic_retry': False, 'model_calls': 0}

    def project(self, record, result):
        path = self.path(record['request']['operation_id'])
        if not path.exists():
            return
        value = {'kind': 'CLASSIC_NOTEPAD_APPEND', 'parent_operation_id': record['request']['operation_id'],
            'parent_request_sha256': record['request_sha256'], 'proposal_ref': None, 'status': 'READONLY',
            'reason_code': 'PROPOSAL_REFERENCE_UNAVAILABLE', 'automatic_retry': False,
            'execution_authorized': False, 'source': _source(record), 'request': None}
        result['native_action_proposals'] = [value]
        try:
            saved = self.ws._read(path, authority=False)
            ref = RevisionRef.model_validate(saved['proposal_ref']).model_dump()
            value['proposal_ref'] = ref
            payload = saved['payload']
            if ref != {'id': digest(payload), 'revision': 1, 'sha256': digest(payload)}:
                raise Conflict('Action proposal checksum changed')
            value['reason_code'] = 'PROPOSAL_PROVENANCE_CHANGED'
            if (payload['source'] != _source(record) or digest(record['request']) != record['request_sha256'] or
                    payload['request']['raw_text'] != record['request']['raw_text']):
                return
            value['reason_code'] = 'HISTORICAL_HOST'
            if payload['host_generation'] != self.native.generation or record['generation'] != self.native.generation:
                return
            value['reason_code'] = 'WAITING_PARENT_RESULT' if result['status'] == 'PROCESSING' else 'PARENT_NOT_RETURNED'
            if result['status'] != 'RETURNED':
                return
            value['reason_code'] = 'PROPOSAL_ADMISSION_CHANGED'
            if self._admission(record) != payload['admission']:
                return
            value['reason_code'] = 'PROPOSAL_EXPIRED'
            if _time(payload['admission']['deadline']) <= datetime.now(timezone.utc):
                return
            value['reason_code'] = 'PROPOSAL_CONTEXT_UNAVAILABLE'
            self.native.read_context(payload['source']['context_ref'])
            value.update(status='PROPOSED', reason_code=None, request=deepcopy(payload['request']))
        except Exception:
            return
