"""Admission and immutable reads for the original command observer.

No command execution, native HTTP, permission reply, current-source repair or
model call occurs here. Historical files are read through bounded ordinary-file
checks shared with the existing change-review adapter.
"""
from __future__ import annotations

import hashlib
import os
import ntpath
import posixpath
from pathlib import Path
import re

from packages.adapters.opencode_change_review import _directory, _put, _regular as _original_regular, historical_observer_paths
from packages.domain.context_read_budget import source_regular
from packages.adapters.opencode_profile import COMMIT, VERSION
from packages.domain.local_workspaces import relative
from packages.domain.native_capabilities import _json
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import digest

PLUGIN_ID = 'eeas.command-observation'
ENV_KEY = 'EEAS_COMMAND_OBSERVATION_CONFIG'
MAX_FILES = 32
MAX_SOURCE_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 65536
MAX_CALLS = 32
MAX_GENERATIONS = 16
_SHA = re.compile(r'[a-f0-9]{64}\Z')
_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}\Z')
SCOPE_KEYS = ('project_id', 'session_ref', 'generation', 'workspace_ref')


def _regular(path, maximum):
    return source_regular(_original_regular, path, maximum)


class CommandObservationCapacity(Conflict):
    """Optional history is full; no integrity or execution grant is implied."""


def _hash(value):
    return isinstance(value, str) and bool(_SHA.fullmatch(value))


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def declared_paths(values):
    if not isinstance(values, (list, tuple)) or len(values) > MAX_FILES:
        raise InvalidInput('Command observation accepts at most 32 declared files')
    paths = []
    for value in values:
        path = relative(value)
        if (any(c in path for c in '*?[]') or path.endswith('/') or
                any(p.casefold() in {'.git', '.eeas-sdk-state', '.eeas-runtime'} for p in path.split('/'))):
            raise InvalidInput('Command observation requires literal non-private file paths')
        paths.append(path)
    if len(set(paths)) != len(paths):
        raise InvalidInput('Declared command source paths must be unique')
    return paths


def verify_bundle(plugin_root=None):
    root = Path(plugin_root) if plugin_root else Path(__file__).resolve().parents[2] / 'toolchain/opencode-plugins/command-observation'
    _directory(root)
    raw = _regular(root / 'manifest.json', 65536)
    manifest = _json(raw)
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'eeas.opencode-plugin-bundle.v1' or
            manifest.get('id') != PLUGIN_ID or manifest.get('version') != '0.1.0' or
            manifest.get('entry') != 'index.mjs' or manifest.get('runtime') != {'version': VERSION, 'source_commit': COMMIT} or
            manifest.get('new_dependencies') != [] or not isinstance(manifest.get('files'), list) or
            not 1 <= len(manifest['files']) <= 16):
        raise Conflict('The pinned command observation plugin is unavailable')
    seen = set()
    for row in manifest['files']:
        if not isinstance(row, dict) or set(row) != {'path', 'bytes', 'sha256'}:
            raise Conflict('Command observation manifest is invalid')
        path = relative(row['path'])
        if path in seen or path == 'manifest.json' or type(row['bytes']) is not int or not 0 <= row['bytes'] <= 1048576:
            raise Conflict('Command observation source list is invalid')
        seen.add(path)
        _directory((root / path).parent)
        source = _regular(root / path, 1048576)
        if len(source) != row['bytes'] or hashlib.sha256(source).hexdigest() != row['sha256']:
            raise Conflict('Command observation plugin source bytes changed')
    if not {'index.mjs', 'files.mjs', 'budget.mjs', 'package.json'} <= seen:
        raise Conflict('Command observation plugin closure is missing')
    return {'root': str(root.resolve()), 'entry': str((root / 'index.mjs').resolve()),
            'bundle_sha256': hashlib.sha256(raw).hexdigest()}


def prepare(runtime_dir, *, project_id, session_ref, generation, workspace, source_paths=(), plugin_root=None):
    if type(generation) is not int or not 1 <= generation <= 1000000:
        raise InvalidInput('An exact positive original generation is required')
    paths = declared_paths(source_paths)
    bundle = verify_bundle(plugin_root)
    root = Path(runtime_dir) / 'command-observation'
    workspace_root = Path(workspace['root']).resolve()
    if (root.resolve() == workspace_root or root.resolve().is_relative_to(workspace_root) or
            workspace_root.is_relative_to(root.resolve())):
        raise Conflict('Command observation history must be outside the workspace')
    _directory(root)
    root.mkdir(parents=True, exist_ok=True)
    # Bounded metadata enumeration; never scan the user's workspace.
    with os.scandir(root) as rows:
        generations = 0
        for count, row in enumerate(rows):
            if count >= MAX_GENERATIONS + 3:
                raise CommandObservationCapacity('Command observation admission history is full')
            if re.fullmatch(r'generation-[1-9][0-9]*', row.name):
                generations += 1
    if generations >= MAX_GENERATIONS:
        raise CommandObservationCapacity('Command observation admission history is full')
    budget = root / 'budget'
    _directory(budget)
    budget.mkdir(exist_ok=True)
    generation_root = root / ('generation-' + str(generation))
    _directory(generation_root)
    generation_root.mkdir(exist_ok=False)
    ledger = generation_root / 'ledger'
    ledger.mkdir()
    scope = {'project_id': project_id, 'session_ref': session_ref, 'generation': generation,
             'workspace_ref': {k: workspace[k] for k in ['id', 'revision', 'binding_sha256']},
             'workspace_root': workspace['root'], 'root_identity': {
                 'dev': str(workspace['root_identity']['device']), 'ino': str(workspace['root_identity']['inode'])},
             'source_paths': paths, 'ledger_root': str(ledger.resolve()), 'budget_root': str(budget.resolve())}
    config = {'schema_version': 1, 'scope': scope, 'scope_sha256': digest(scope), 'native_session_id': None}
    path = generation_root / 'scope.json'
    _put(path, config)
    return {**bundle, 'scope': scope, 'scope_sha256': config['scope_sha256'], 'config_path': str(path.resolve()),
            'config_sha256': hashlib.sha256(_regular(path, 65536)).hexdigest(), 'native_session_id': None}


def ready(admission):
    try:
        ledger = Path(admission['scope']['ledger_root'])
        _directory(ledger)
        value = _json(_regular(ledger / 'ready.json', 8192))
        return (value.get('schema') == 'eeas.command-observation.ready.v1' and
                value.get('scope_sha256') == admission['scope_sha256'] and value.get('plugin_id') == PLUGIN_ID and
                value.get('native_session_id') in (None, admission['native_session_id']))
    except (KeyError, TypeError, AttributeError, OSError, ValueError, Conflict):
        return False


def bind(admission, native_session_id):
    if not isinstance(native_session_id, str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}', native_session_id):
        raise Conflict('Command observation requires an exact original session')
    path = Path(admission['config_path'])
    config = _json(_regular(path, 65536))
    if (not isinstance(config, dict) or config != {'schema_version': 1, 'scope': admission['scope'], 'scope_sha256': admission['scope_sha256'],
                   'native_session_id': config.get('native_session_id')} or
            digest(config['scope']) != config['scope_sha256'] or config['native_session_id'] not in (None, native_session_id)):
        raise Conflict('Command observation original session binding changed')
    if config['native_session_id'] is None:
        config['native_session_id'] = native_session_id
        _put(path, config, replace=True)
    return {**admission, 'native_session_id': native_session_id,
            'config_sha256': hashlib.sha256(_regular(path, 65536)).hexdigest()}


def record_admission(runtime_dir, admission):
    root = Path(runtime_dir) / 'command-observation'
    _directory(root)
    source = admission['scope']
    if not _id(admission.get('native_session_id')) or digest(source) != admission['scope_sha256']:
        raise Conflict('Command observation admission is unbound')
    head = {'schema': 'eeas.command-observation.admissions.v1',
            **{k: source[k] for k in ['project_id', 'session_ref', 'workspace_ref']}, 'admissions': []}
    index = root / 'admissions.json'
    if index.exists():
        old = _json(_regular(index, 32768))
        if (not isinstance(old, dict) or not isinstance(old.get('value'), dict) or digest(old['value']) != old.get('sha256')):
            raise Conflict('Command observation admission index integrity failed')
        head = old['value']
        if (head.get('schema') != 'eeas.command-observation.admissions.v1' or
                any(head.get(k) != source[k] for k in ['project_id', 'session_ref', 'workspace_ref']) or
                not isinstance(head.get('admissions'), list) or len(head['admissions']) > MAX_GENERATIONS or
                any(not isinstance(row, dict) or type(row.get('generation')) is not int for row in head['admissions'])):
            raise Conflict('Command observation admission index scope differs')
    wrapper = {'value': admission, 'sha256': digest(admission)}
    path = root / ('generation-' + str(source['generation'])) / 'admission.json'
    if path.exists():
        if _json(_regular(path, 262144)) != wrapper:
            raise Conflict('Immutable command admission changed')
    else:
        _put(path, wrapper)
    row = {'generation': source['generation'], 'native_session_id': admission['native_session_id'],
           'admission_sha256': wrapper['sha256']}
    previous = next((r for r in head['admissions'] if r.get('generation') == row['generation']), None)
    if previous is not None:
        if previous != row:
            raise Conflict('Immutable command generation changed')
        return
    if (len(head['admissions']) >= MAX_GENERATIONS or head['admissions'] and
            row['generation'] <= head['admissions'][-1]['generation']):
        raise Conflict('Command admission generation cannot replace history')
    head['admissions'].append(row)
    _put(index, {'value': head, 'sha256': digest(head)}, replace=index.exists())


def historical_admissions(runtime_dir, observed):
    if type(observed.get('generation')) is not int or observed['generation'] < 1:
        raise Conflict('Historical command generation is invalid')
    root = Path(runtime_dir) / 'command-observation'
    index = root / 'admissions.json'
    if not index.exists():
        return
    _directory(root)
    wrapper = _json(_regular(index, 32768))
    head = wrapper.get('value') if isinstance(wrapper, dict) else None
    if (not isinstance(head, dict) or digest(head) != wrapper.get('sha256') or
            head.get('schema') != 'eeas.command-observation.admissions.v1' or
            any(head.get(k) != observed[k] for k in ['project_id', 'session_ref', 'workspace_ref']) or
            not isinstance(head.get('admissions'), list) or len(head['admissions']) > MAX_GENERATIONS):
        raise Conflict('Historical command admission index is unavailable')
    last = 0
    for row in head['admissions']:
        generation = row.get('generation') if isinstance(row, dict) else None
        if type(generation) is not int or not last < generation <= observed['generation'] or not _hash(row.get('admission_sha256')):
            raise Conflict('Historical command generation is invalid')
        last = generation
        if row.get('native_session_id') != observed.get('original_session_id'):
            continue
        path = root / ('generation-' + str(generation)) / 'admission.json'
        _directory(path.parent)
        wrapper = _json(_regular(path, 262144))
        value = wrapper.get('value') if isinstance(wrapper, dict) else None
        if (not isinstance(value, dict) or digest(value) != row['admission_sha256'] or
                wrapper.get('sha256') != row['admission_sha256'] or value.get('scope', {}).get('generation') != generation):
            raise Conflict('Immutable historical command admission failed integrity')
        yield value


def _signed(path, field):
    value = _json(_regular(path, 262144))
    if not isinstance(value, dict) or not _hash(value.get(field)) or digest({k: v for k, v in value.items() if k != field}) != value[field]:
        raise Conflict('Immutable command record failed integrity')
    return value


def _source(value, ledger):
    if not isinstance(value, dict) or set(value) != {'state', 'sha256', 'bytes', 'line_count', 'blob', 'stat'}:
        raise Conflict('Command source snapshot is incomplete')
    if value['state'] == 'ABSENT':
        if value != {'state': 'ABSENT', 'sha256': None, 'bytes': 0, 'line_count': 0, 'blob': None, 'stat': None}:
            raise Conflict('Absent command source snapshot differs')
        return
    if (value['state'] != 'UTF8' or not _hash(value['sha256']) or type(value['bytes']) is not int or
            not 0 <= value['bytes'] <= 262144 or value['blob'] != 'blobs/' + value['sha256'] + '.utf8' or
            type(value['line_count']) is not int or value['line_count'] < 0 or
            not isinstance(value['stat'], dict) or set(value['stat']) != {'dev', 'ino', 'size', 'mtime_ns', 'ctime_ns'} or
            any(not isinstance(v, str) or not re.fullmatch(r'\d+', v) for v in value['stat'].values()) or
            value['stat']['size'] != str(value['bytes'])):
        raise Conflict('Command source snapshot is unavailable')
    path = ledger / value['blob']
    _directory(path.parent)
    raw = _regular(path, 262144)
    if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256']:
        raise Conflict('Command source snapshot bytes changed')


def _history_paths(admission, scope, data_dir):
    return historical_observer_paths(admission, scope, data_dir, 'command-observation', _regular)


def read_history(admission, observed, *, data_dir, selected_call_id=None):
    """Read only exact saved original calls, including orphaned before records.

    The reader never obtains a historical basis from today's workspace. A
    complete original tool part and the plugin result must both agree before
    an integer command exit is exposed. Output is the original merged stream.
    """
    if not admission:
        return [], ['NO_ADMITTED_COMMAND_OBSERVER']
    if selected_call_id is not None and not _id(selected_call_id):
        raise InvalidInput('An exact original command call is required')
    try:
        scope = admission['scope']
        if (any(scope.get(k) != observed[k] for k in SCOPE_KEYS) or digest(scope) != admission['scope_sha256'] or
                admission['native_session_id'] != observed['original_session_id']):
            raise Conflict('Command observation source scope differs')
        declared_paths(scope['source_paths'])
        ledger, config = _history_paths(admission, scope, data_dir)
        _directory(ledger)
        if not ledger.resolve().is_relative_to(Path(data_dir).resolve()):
            raise Conflict('Command ledger is outside App data')
        _directory(config.parent)
        raw = _regular(config, 65536)
        if (hashlib.sha256(raw).hexdigest() != admission['config_sha256'] or _json(raw) != {
                'schema_version': 1, 'scope': scope, 'scope_sha256': admission['scope_sha256'],
                'native_session_id': observed['original_session_id']}):
            raise Conflict('Command observation configuration changed')
        # MAX_CALLS belongs to this admitted generation, not the first calls
        # ever seen in the original session. Enumerate only bounded private
        # metadata, then match saved original parts against these exact keys.
        calls_root = ledger / 'calls'
        _directory(calls_root)
        captured_keys = set()
        if selected_call_id is not None:
            key = digest({'native_session_id': observed['original_session_id'], 'call_id': selected_call_id})
            _directory(calls_root / key)
            captured_keys.add(key)
        else:
            with os.scandir(calls_root) as rows:
                for count, row in enumerate(rows):
                    if count >= MAX_CALLS or not _hash(row.name) or not row.is_dir(follow_symlinks=False):
                        raise Conflict('Command observation call directory is invalid or over capacity')
                    _directory(Path(row.path))
                    captured_keys.add(row.name)
    except (KeyError, TypeError, ValueError, OSError, Conflict, InvalidInput):
        return [], ['COMMAND_SCOPE_UNAVAILABLE']
    identities, candidates = {}, []
    messages = observed.get('messages', [])
    if not isinstance(messages, list) or len(messages) > 200:
        return [], ['COMMAND_MESSAGE_COVERAGE_UNAVAILABLE']
    for message in messages:
        info = message.get('info', {}) if isinstance(message, dict) else {}
        if not isinstance(info, dict) or info.get('role') != 'assistant' or info.get('sessionID') != observed['original_session_id']:
            continue
        parts = message.get('parts', [])
        if not isinstance(parts, list) or len(parts) > 1024:
            continue
        for part in parts:
            if not isinstance(part, dict) or part.get('type') != 'tool' or part.get('tool') != 'bash' or not _id(part.get('callID')):
                continue
            if selected_call_id is not None and part['callID'] != selected_call_id:
                continue
            if digest({'native_session_id': observed['original_session_id'], 'call_id': part['callID']}) not in captured_keys:
                continue
            identities.setdefault(part['callID'], []).append((info.get('id'), part.get('id')))
            candidates.append((info, part))
    entries, reasons = [], set()
    for info, part in candidates:
        if (len(identities[part['callID']]) != 1 or part.get('messageID') != info.get('id') or
                part.get('sessionID') != observed['original_session_id'] or not _id(info.get('id')) or
                not _id(info.get('parentID')) or not _id(part.get('id')) or not isinstance(part.get('state'), dict)):
            reasons.add('AMBIGUOUS_ORIGINAL_COMMAND_CALL'); continue
        if part['state'].get('status') not in ('completed', 'error'):
            reasons.add('ORIGINAL_COMMAND_STILL_IN_PROGRESS')
            continue
        key = digest({'native_session_id': observed['original_session_id'], 'call_id': part['callID']})
        directory = ledger / 'calls' / key
        if not (directory / 'before.json').exists():
            reasons.add('COMMAND_BEFORE_NOT_OBSERVED'); continue
        try:
            _directory(directory)
            before = _signed(directory / 'before.json', 'before_sha256')
            expected = {**{k: observed[k] for k in SCOPE_KEYS}, 'scope_sha256': admission['scope_sha256'],
                        'native_session_id': observed['original_session_id'], 'call_id': part['callID'], 'tool': 'bash',
                        'plugin': {'id': PLUGIN_ID, 'version': '0.1.0'}}
            if (before.get('schema') != 'eeas.command-observation.before.v1' or
                    any(before.get(k) != v for k, v in expected.items()) or
                    before.get('args') != part['state'].get('input') or before.get('args_sha256') != digest(before['args'])):
                raise Conflict('Original command input and before record differ')
            result_path = directory / 'result.json'
            result = _signed(result_path, 'record_sha256') if result_path.exists() else None
            pre_path = directory / 'pre.json'
            pre = _signed(pre_path, 'pre_sha256') if pre_path.exists() else None
            if pre and (pre.get('schema') != 'eeas.command-observation.pre.v1' or
                    any(pre.get(k) != v for k, v in expected.items()) or
                    pre.get('before_record_sha256') != before['before_sha256'] or
                    pre.get('args_sha256') != before['args_sha256'] or not isinstance(pre.get('sources'), list) or
                    [x.get('path') for x in pre['sources']] != scope['source_paths']):
                raise Conflict('Original command pre-source scope differs')
            if pre:
                module = ntpath if re.match(r'^[A-Za-z]:', scope['workspace_root']) else posixpath
                cwd = pre.get('workdir')
                if (not isinstance(cwd, str) or not module.isabs(cwd) or
                        module.normcase(module.commonpath([scope['workspace_root'], cwd])) != module.normcase(module.normpath(scope['workspace_root']))):
                    raise Conflict('Original command pre-source working directory differs')
            if result and (result.get('schema') != 'eeas.command-observation.record.v1' or
                    any(result.get(k) != v for k, v in expected.items()) or
                    result.get('before_record_sha256') != before['before_sha256'] or
                    result.get('args_sha256') != before['args_sha256'] or
                    result.get('pre_record_sha256') != (pre['pre_sha256'] if pre else None) or
                    not isinstance(result.get('sources'), list) or [x.get('path') for x in result['sources']] != scope['source_paths'] or
                    result.get('basis_scope') != 'DECLARED_FILES_ONLY' or result.get('all_dependencies_captured') is not False):
                raise Conflict('Original command result scope differs')
            sources = result['sources'] if result else [{'path': x['path'], 'before': x['snapshot'], 'after': None} for x in pre['sources']] if pre else []
            total = {'before': 0, 'after': 0}
            for row in sources:
                if row['before'] != (next((x['snapshot'] for x in pre['sources'] if x['path'] == row['path']), None) if pre else None):
                    raise Conflict('Command result replaced the immutable pre-source')
                for side in total:
                    if row[side] is not None:
                        _source(row[side], ledger)
                        total[side] += row[side]['bytes']
            if max(total.values()) > MAX_SOURCE_BYTES:
                raise Conflict('Command source aggregate exceeds its bound')
            output = result.get('output') if result else None
            state = part['state']
            matched_return = bool(result and state.get('status') == 'completed' and result.get('tool_returned') is True)
            if matched_return:
                metadata = state.get('metadata')
                text = state.get('output')
                if (not isinstance(metadata, dict) or not isinstance(text, str) or len(text.encode()) > 1048576 or
                        not isinstance(output, dict) or output.get('original_sha256') != hashlib.sha256(text.encode()).hexdigest() or
                        output.get('original_bytes') != len(text.encode()) or output.get('original_truncated') is not metadata.get('truncated') or
                        output.get('original_output_path') != metadata.get('outputPath') or 'exit' not in metadata or
                        result.get('exit_code') != metadata['exit'] or type(result.get('exit_code')) is not type(metadata['exit']) or
                        type(result.get('exit_code')) not in (int, type(None))):
                    raise Conflict('Original command return and immutable result differ')
                raw = output['text'].encode()
                if (len(raw) > MAX_OUTPUT_BYTES or output.get('bytes') != len(raw) or
                        output.get('sha256') != hashlib.sha256(raw).hexdigest() or not text.startswith(output['text']) or
                        output.get('capture_truncated') is not (len(raw) < len(text.encode()))):
                    raise Conflict('Original command output preview failed integrity')
            why = list(result.get('reason_codes', [])) if result else ['ORPHANED_BEFORE_NO_AFTER_NO_REPLAY']
            if not scope['source_paths']:
                why.append('NO_DECLARED_SOURCE_FILES')
            # The native shell resolves an omitted/relative cwd to an absolute
            # path. That added path may contain a known credential which the
            # saved public input never exposed. Do not publish private ledger
            # context merely because the command and its output were matched.
            workdir = pre.get('workdir') if pre else None
            if workdir is not None and workdir != state['input'].get('workdir'):
                workdir = None
                why.append('COMMAND_WORKDIR_NOT_IN_OBSERVED_INPUT')
            source_status = ('UNAVAILABLE' if not sources or any(x['before'] is None or x['after'] is None for x in sources)
                             else 'CHANGED' if any(x['before'] != x['after'] for x in sources) else 'UNCHANGED')
            exit_code = result.get('exit_code') if matched_return and type(result.get('exit_code')) is int else None
            saved_sha = result['record_sha256'] if result else before['before_sha256']
            entry = {'record_ref': {'id': digest({'call_key': key, 'source_record_sha256': saved_sha}), 'revision': 1, 'sha256': saved_sha},
                     'source_scope': {k: observed[k] for k in SCOPE_KEYS}, 'source_generation': observed['generation'],
                     'native_session_id': observed['original_session_id'], 'message_id': info['id'], 'part_id': part['id'],
                     'parent_native_message_id': info['parentID'], 'call_id': part['callID'],
                     'command': before['args']['command'], 'workdir': workdir,
                     'exit_code': exit_code, 'status': 'EXITED' if exit_code is not None else 'UNKNOWN_OUTCOME',
                     'source_status': source_status, 'sources': sources,
                     'output': ({'text': output['text'], 'sha256': output['sha256'], 'bytes': output['bytes'],
                                 'truncated': output['capture_truncated'] or output['original_truncated'],
                                 'original_truncated': output['original_truncated'], 'original_output_path': output['original_output_path']}
                                if matched_return else None),
                     'reason_codes': sorted(set(why)), 'started_at': before['started_at'],
                     'finished_at': result.get('finished_at') if result else None,
                     'qualification': 'ORIGINAL_COMMAND_OBSERVATION_ONLY'}
            entries.append(entry)
        except (KeyError, TypeError, ValueError, AttributeError, OSError, Conflict, InvalidInput):
            reasons.add('COMMAND_RECORD_UNAVAILABLE')
    return entries, sorted(reasons)


def read_after_source(admission, observed, entry, path, expected_sha256, *, data_dir):
    """Read one already-associated immutable after snapshot, never a workspace file."""
    path = relative(path)
    scope = admission['scope']
    if (not _hash(expected_sha256) or entry.get('source_scope') != {k: observed[k] for k in SCOPE_KEYS}
            or entry.get('native_session_id') != observed.get('original_session_id')
            or any(scope.get(k) != observed[k] for k in SCOPE_KEYS)
            or path not in declared_paths(scope['source_paths'])):
        raise Conflict('Report snapshot differs from its admitted command source')
    sources = [row for row in entry['sources'] if row['path'] == path]
    if len(sources) != 1 or not sources[0].get('after'):
        raise Conflict('The original command has no exact after snapshot for this report')
    snapshot = sources[0]['after']
    if snapshot.get('state') != 'UTF8' or snapshot.get('sha256') != expected_sha256:
        raise Conflict('The selected command report snapshot changed')
    ledger, _ = _history_paths(admission, scope, data_dir)
    _source(snapshot, ledger)
    raw = _regular(ledger / snapshot['blob'], 262144)
    if len(raw) != snapshot['bytes'] or hashlib.sha256(raw).hexdigest() != expected_sha256:
        raise Conflict('The original command report bytes differ')
    return raw, sources[0]
