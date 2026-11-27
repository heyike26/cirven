"""Exact recorded discussion targets. A navigation hint grants no execution."""
from copy import deepcopy

from packages.domain.discovery_schema import RevisionRef
from packages.domain.local_workspaces import FileSource
from packages.domain.planning_schema import WorkspaceRef
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest


def _ref(value):
    return RevisionRef.model_validate(value).model_dump()


def _readonly(nav, reason):
    if nav['status'] == 'AVAILABLE':
        nav.update(status='READONLY', reason_code=reason)


def _workspace(app, nav, value):
    if value is None:
        return None
    ref = WorkspaceRef.model_validate(value).model_dump()
    try:
        binding = app.state.local_workspaces.get(nav.get('conversation_project_id', nav['project_id']), ref['id'], ref['revision'])
        if binding['binding_sha256'] != ref['binding_sha256']:
            raise Conflict('Original workspace binding changed')
    except (Conflict, NotFound):
        _readonly(nav, 'WORKSPACE_REFERENCE_UNAVAILABLE')
    return ref


def _file(app, nav, value):
    if value is None:
        return None
    ref = FileSource.model_validate(value)
    if ref.project_id != nav.get('conversation_project_id', nav['project_id']):
        raise Conflict('Original file belongs to another project')
    nav['workspace_ref'] = _workspace(app, nav, {'id': ref.workspace_id,
        'revision': ref.binding_revision, 'binding_sha256': ref.binding_sha256})
    try:
        if app.state.local_workspaces.check_sources(ref.project_id, [ref])[0]['status'] != 'CURRENT':
            _readonly(nav, 'FILE_SOURCE_CHANGED')
    except (Conflict, NotFound):
        _readonly(nav, 'FILE_REFERENCE_UNAVAILABLE')
    return ref.model_dump()


def _object(app, nav, value, kind, expected_id=None):
    if not isinstance(value, dict) or not all(k in value for k in ('id', 'revision', 'sha256', 'payload')):
        nav.update(status='UNAVAILABLE', reason_code='NO_RECORDED_OBJECT_RESULT')
        return None, None
    ref = _ref({k: value[k] for k in ('id', 'revision', 'sha256')})
    if value.get('project_id') != nav['project_id'] or expected_id is not None and ref['id'] != expected_id:
        raise Conflict('Recorded result belongs to another project or object')
    if digest(value['payload']) != ref['sha256']:
        raise Conflict('Recorded object payload differs from its immutable reference')
    service = app.state.discovery if kind == 'discovery' else app.state.planning_proposals
    try:
        actual = service.get(nav['project_id'], ref['id'], ref['revision'])
    except NotFound:
        nav.update(status='UNAVAILABLE', reason_code='OBJECT_REVISION_UNAVAILABLE')
        return ref, None
    if ({k: actual[k] for k in ('id', 'revision', 'sha256')} != ref or actual['payload'] != value['payload']):
        raise Conflict('Recorded result differs from the exact saved object revision')
    if actual['current_revision'] != ref['revision']:
        _readonly(nav, 'HISTORICAL_OBJECT_REVISION')
    elif actual.get('source_current') is False:
        _readonly(nav, 'OBJECT_SOURCE_CHANGED')
    return ref, actual


def navigation(app, task, *, native_record=None, native_result=None,
               session=None, operation=None, operation_row=None, object_result=None):
    key = task['key']
    kind = {'native_message': 'native', 'original_session': 'original',
            'discovery': 'discovery', 'planning': 'planning'}.get(key['kind'])
    if kind is None:
        return None
    nav = {'version': 1, 'kind': kind, 'status': 'AVAILABLE', 'reason_code': None,
        'task_key': deepcopy(key), 'source_ref': _ref(task['source_ref']),
        'project_id': key['project_id'], 'operation_id': key['operation_id'],
        'automatic_retry': False, 'execution_authorized': False}
    if kind == 'native':
        from apps.api.native_messages import NativeQuestionInput, FileLinkRef, question_project
        request = NativeQuestionInput.model_validate(native_record['request']).request_dict()
        if (digest(native_record['request']) != native_record['request_sha256'] or
                request['operation_id'] != key['object_id'] or request['operation_id'] != key['operation_id'] or
                request['project_id'] != nav['project_id'] or nav['source_ref'] != {
                    'id': key['operation_id'], 'revision': 1,
                    'sha256': digest({'request_sha256': native_record['request_sha256'], 'status': task['source_status']})}):
            raise Conflict('Original native navigation request basis differs')
        origin = 'CURRENT_HOST' if native_record['generation'] == app.state.native_messages.generation else 'HISTORICAL_READONLY'
        nav.update(conversation_id=request['conversation_id'], request_sha256=native_record['request_sha256'],
            request_project_id=request['project_id'], conversation_project_id=question_project(request),
            provider_profile_ref=deepcopy(request['provider_profile_ref']), workspace_ref=None,
            file_source_ref=None, file_link_ref=None, record_origin=origin)
        if request['conversation_id'] is None:
            nav.update(status='UNAVAILABLE', reason_code='ORIGINAL_CONVERSATION_ID_UNAVAILABLE')
        elif origin == 'HISTORICAL_READONLY':
            _readonly(nav, 'HISTORICAL_NATIVE_OPERATION')
        if question_project(request) != request['project_id'] and nav['status'] != 'UNAVAILABLE':
            nav.update(status='READONLY', reason_code='LEGACY_PROJECT_SCOPE_MISMATCH')
        if request.get('native_review') is not None:
            nav.update(status='UNAVAILABLE', reason_code='NATIVE_REVIEW_NAVIGATION_NOT_ADAPTED')
        nav['file_source_ref'] = _file(app, nav, request['file_source_ref'])
        if request['file_link_ref'] is not None:
            link = FileLinkRef.model_validate(request['file_link_ref']).model_dump()
            if link['project_id'] != nav['conversation_project_id']:
                raise Conflict('Original linked file belongs to another project')
            nav['file_link_ref'] = link
            try:
                saved = app.state.file_continuity.get(link)
            except (Conflict, NotFound):
                _readonly(nav, 'FILE_LINK_UNAVAILABLE')
            else:
                _file(app, nav, saved.get('baseline_file_source_ref'))
        return nav
    if kind == 'original':
        ref = _ref(session['session_ref'])
        if ref != nav['source_ref'] or ref['id'] != key['object_id'] or session['project_id'] != nav['project_id']:
            raise Conflict('Original session navigation owner differs')
        request, request_sha = operation.get('request', {}), operation.get('request_sha256')
        if (operation.get('id') != key['operation_id'] or request.get('generation') != key['generation']):
            raise Conflict('Original session navigation operation differs')
        nav.update(session_ref=ref, generation=key['generation'], current_generation=session['generation'],
            request_sha256=request_sha, provider_profile_ref=_ref(session['provider_profile_ref']) if session.get('provider_profile_ref') else None,
            workspace_ref=_workspace(app, nav, session.get('workspace_ref')))
        if request_sha is None or operation.get('session_ref') is None:
            _readonly(nav, 'ORIGINAL_REQUEST_IDENTITY_UNAVAILABLE')
        elif (request_sha != digest(request) or operation['session_ref'] != ref or request.get('session_ref') != ref or
              request.get('operation_id') != key['operation_id'] or operation.get('operation_ref') != {
                  'id': key['operation_id'], 'request_sha256': request_sha}):
            raise Conflict('Original session navigation request checksum differs')
        if session['generation'] != key['generation']:
            _readonly(nav, 'HISTORICAL_SESSION_GENERATION')
        elif not session.get('connected_owner') or not session.get('configuration_current'):
            _readonly(nav, 'ORIGINAL_SESSION_NOT_READY')
        return nav
    row = dict(operation_row)
    column = 'session_id' if kind == 'discovery' else 'proposal_id'
    if (row['project_id'] != nav['project_id'] or row['operation_id'] != key['operation_id'] or
            row[column] != key['object_id'] or object_result and digest(object_result) != row['result_sha256']):
        raise Conflict('Recorded operation result scope differs')
    if nav['source_ref'] != {'id': key['operation_id'], 'revision': 1, 'sha256': digest({
            'request_sha256': row['request_sha256'], 'result_sha256': row['result_sha256'], 'status': task['source_status']})}:
        raise Conflict('Recorded task result navigation basis differs')
    nav.update(object_ref=None, source_session_ref=None, workspace_ref=None, proposal_ref=None)
    source = object_result or {}
    wrapper = kind == 'discovery' and isinstance(source, dict) and 'session' in source
    if wrapper and row['kind'] != 'PLAN':
        raise Conflict('Only a recorded PLAN operation can link a PLAN result wrapper')
    value = source.get('session') if wrapper else source
    ref, actual = _object(app, nav, value, kind, key['object_id'])
    nav['object_ref'] = ref
    if actual is None:
        return nav
    nav['workspace_ref'] = _workspace(app, nav, actual['payload'].get('workspace_ref'))
    if kind == 'discovery':
        nav['source_session_ref'] = ref
        if wrapper:
            proposed_ref = _ref(source.get('proposal_ref'))
            proposal_ref, _ = _object(app, nav, source.get('proposal'), 'planning', proposed_ref['id'])
            if proposal_ref != proposed_ref or actual['payload'].get('proposal_ref') != proposed_ref:
                raise Conflict('Recorded PLAN wrapper proposal references differ')
            nav['proposal_ref'] = proposal_ref
    elif actual['payload'].get('discovery_ref') is not None:
        source_ref = _ref(actual['payload']['discovery_ref'])
        try:
            source_session = app.state.discovery.get(nav['project_id'], source_ref['id'], source_ref['revision'])
        except NotFound:
            _readonly(nav, 'SOURCE_SESSION_UNAVAILABLE')
        else:
            if {k: source_session[k] for k in ('id', 'revision', 'sha256')} != source_ref:
                raise Conflict('Recorded planning source session differs')
            nav['source_session_ref'] = source_ref
    return nav
