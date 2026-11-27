"""Pinned optional original-plugin admission and exact immutable-history reader.

No model loop, git, present-file reconstruction, permission approval or replay.
The plugin supplies observations; only completed original tool parts with an
exact root or verified child lineage can expose historical full-file comparisons.
"""
from __future__ import annotations

import hashlib
import json
import os
import ntpath
import posixpath
from pathlib import Path
import re
import stat
import uuid

from packages.adapters.opencode_profile import COMMIT, VERSION
from packages.domain.local_workspaces import relative
from packages.domain.native_capabilities import _json
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import canonical, digest

PLUGIN_ID = 'eeas.change-review'
MAX_SIDE = 262144
_SHA = re.compile(r'[a-f0-9]{64}\Z')


def _regular(path, limit):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit or
            getattr(info, 'st_file_attributes', 0) & 0x400):
        raise Conflict('Change review history is not a bounded ordinary file')
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        with os.fdopen(fd, 'rb', closefd=False) as stream:
            held = os.fstat(fd)
            if (held.st_dev, held.st_ino, held.st_size, held.st_mtime_ns, held.st_ctime_ns) != (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns):
                raise Conflict('Change review history changed before reading')
            raw = stream.read(limit+1)
            end = os.fstat(fd)
            if ((end.st_dev, end.st_ino, end.st_size, end.st_mtime_ns, end.st_ctime_ns) !=
                    (held.st_dev, held.st_ino, held.st_size, held.st_mtime_ns, held.st_ctime_ns) or len(raw) > limit):
                raise Conflict('Change review history changed while reading')
    finally:
        os.close(fd)
    after = path.lstat()
    if ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns) !=
            (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) or len(raw) != info.st_size):
        raise Conflict('Change review history changed while reading')
    return raw


def _directory(path):
    # The App chooses this location; no model-provided path reaches this helper.
    for item in [path, *path.parents]:
        if item.exists() and (item.is_symlink() or not item.is_dir() or
                getattr(item.lstat(), 'st_file_attributes', 0) & 0x400):
            raise Conflict('Change review directory cannot contain links')
    return path


def verify_bundle(plugin_root=None):
    root = Path(plugin_root) if plugin_root else Path(__file__).resolve().parents[2]/'toolchain/opencode-plugins/change-review'
    _directory(root)
    raw = _regular(root/'manifest.json', 1048576)
    manifest = _json(raw)
    if (not isinstance(manifest, dict) or manifest.get('schema') != 'eeas.opencode-plugin-bundle.v1' or manifest.get('id') != PLUGIN_ID or
            manifest.get('version') not in {'0.1.0','0.1.1'} or manifest.get('entry') != 'index.mjs' or
            manifest.get('runtime') != {'version': VERSION, 'source_commit': COMMIT} or
            manifest.get('new_dependencies') != [] or not isinstance(manifest.get('files'), list) or
            not 1 <= len(manifest['files']) <= 32):
        raise Conflict('The pinned optional change review plugin is unavailable')
    seen = set()
    for row in manifest['files']:
        if not isinstance(row, dict) or set(row) != {'path', 'sha256', 'bytes'}:
            raise Conflict('Change review manifest is invalid')
        path = relative(row['path'])
        if path in seen or path == 'manifest.json' or type(row['bytes']) is not int or not 0 <= row['bytes'] <= 1048576:
            raise Conflict('Change review source list is invalid')
        seen.add(path); item = root/path
        _directory(item.parent)
        source = _regular(item, 1048576)
        if len(source) != row['bytes'] or hashlib.sha256(source).hexdigest() != row['sha256']:
            raise Conflict('Change review plugin source bytes changed')
    if not {'index.mjs', 'package.json'} <= seen:
        raise Conflict('Change review plugin entry is missing')
    return {'root': str(root.resolve()), 'entry': str((root/'index.mjs').resolve()),
            'bundle_sha256': hashlib.sha256(raw).hexdigest()}


def write_patterns(workspace):
    scopes = workspace.get('allowed_write_paths')
    if not isinstance(scopes, list) or not scopes or len(scopes) > 128:
        raise Conflict('WORKSPACE_WRITE requires existing explicit workspace write paths')
    result = {'*': 'deny'}
    for value in scopes:
        if value == '*':
            result['*'] = 'ask'; continue
        path = relative(value[:-1] if value.endswith('/') else value)
        # Native permission patterns treat these characters as wildcards. A
        # literal filename containing one cannot safely become a broader grant.
        if '*' in path or '?' in path:
            raise InvalidInput('Literal workspace write paths cannot become native permission globs')
        result[path+'/*' if value.endswith('/') else path] = 'ask'
    # Original code tools share permission "edit". Preserve private exclusions
    # even when the user explicitly granted the workspace root.
    for private in ['.git', '.eeas-sdk-state', '.eeas-runtime']:
        result[private+'/*'] = 'deny'
        result['*/'+private+'/*'] = 'deny'
    return result


def _put(path, value, *, replace=False):
    raw = (canonical(value)+'\n').encode()
    if not replace:
        with path.open('xb') as output:
            output.write(raw); output.flush(); os.fsync(output.fileno())
    else:
        stage = path.with_name('.scope-'+uuid.uuid4().hex)
        try:
            with stage.open('xb') as output:
                output.write(raw); output.flush(); os.fsync(output.fileno())
            os.replace(stage, path)
        finally:
            if stage.exists(): stage.unlink()
    path.chmod(0o600)


def prepare(runtime_dir, *, project_id, session_ref, generation, workspace, plugin_root=None):
    bundle = verify_bundle(plugin_root)
    history = Path(runtime_dir)/'change-review'
    _directory(history)
    history.mkdir(parents=True, exist_ok=True)
    # Bound even failed generation setup; do not silently garbage-collect old
    # snapshots, orphaned calls or unknown writes to admit another generation.
    count = 0
    with os.scandir(history) as rows:
        for total, row in enumerate(rows):
            if total > 20:
                raise Conflict('Change review admission history exceeds its metadata bound')
            if re.fullmatch(r'generation-[1-9][0-9]*', row.name):
                count += 1
    if count >= 16:
        raise Conflict('Change review admission history is full; existing snapshots are retained')
    budget = history/'budget'; _directory(budget); budget.mkdir(exist_ok=True)
    root = history/('generation-'+str(generation))
    _directory(root)
    root.mkdir(parents=True, exist_ok=False)
    ledger = root/'ledger'; ledger.mkdir()
    workspace_root = Path(workspace['root']).resolve()
    if root.resolve() == workspace_root or root.resolve().is_relative_to(workspace_root):
        raise Conflict('Change review private history must be outside the writable workspace')
    write_patterns(workspace)
    scope = {'project_id': project_id, 'session_ref': session_ref, 'generation': generation,
             'workspace_ref': {k: workspace[k] for k in ['id', 'revision', 'binding_sha256']},
             'workspace_root': workspace['root'],
             'root_identity': {'dev': str(workspace['root_identity']['device']), 'ino': str(workspace['root_identity']['inode'])},
             'allowed_write_paths': workspace['allowed_write_paths'], 'ledger_root': str(ledger.resolve()),
             'budget_root': str(budget.resolve())}
    config = {'schema_version': 1, 'scope': scope, 'scope_sha256': digest(scope), 'native_session_id': None}
    if len(canonical(config).encode()) > 524288:
        raise InvalidInput('Change review scope exceeds the bounded offline configuration size')
    path = root/'scope.json'; _put(path, config)
    return {**bundle, 'config_path': str(path.resolve()), 'config_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            'scope_sha256': config['scope_sha256'], 'scope': scope, 'native_session_id': None}


def ready(admission):
    path = Path(admission['scope']['ledger_root'])/'ready.json'
    try:
        _directory(path.parent)
        value = _json(_regular(path, 8192))
        return (isinstance(value, dict) and value.get('schema') == 'eeas.change-review.ready.v1' and
                value.get('scope_sha256') == admission['scope_sha256'] and value.get('plugin_id') == PLUGIN_ID)
    except (OSError, ValueError, Conflict):
        return False


def bind(admission, native_session_id):
    if not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}', native_session_id):
        raise Conflict('Change review requires the exact original session identity')
    path = Path(admission['config_path'])
    config = _json(_regular(path, 524288))
    if (set(config) != {'schema_version', 'scope', 'scope_sha256', 'native_session_id'} or
            config['schema_version'] != 1 or config['scope'] != admission['scope'] or
            config['scope_sha256'] != admission['scope_sha256'] or digest(config['scope']) != config['scope_sha256'] or
            config['native_session_id'] not in {None, native_session_id}):
        raise Conflict('Change review original session binding changed')
    if config['native_session_id'] is None:
        config['native_session_id'] = native_session_id; _put(path, config, replace=True)
    return {**admission, 'native_session_id': native_session_id,
            'config_sha256': hashlib.sha256(_regular(path, 524288)).hexdigest()}


def read_child_lineage(admission, target, *, expected_ref=None, ancestor=None, historical_ledger=None):
    """Exact bounded SDK-observed child ancestry under the original root grant.

    This metadata proves neither a write nor a model outcome. Only a reserved
    original tool capture publishes it, outside the writable workspace.
    """
    root = admission['native_session_id']
    native = re.compile(r'ses_[A-Za-z0-9_-]{1,100}\Z')
    if not isinstance(target,str) or not native.fullmatch(target) or not isinstance(root,str) or not native.fullmatch(root) or target==root:
        raise Conflict('An exact child session identity is required')
    scope = admission['scope']
    if digest(scope)!=admission['scope_sha256']:
        raise Conflict('Original child write scope changed')
    ident = digest({'native_session_id':target})
    ledger = historical_ledger if historical_ledger is not None else Path(scope['ledger_root'])
    _directory(ledger/'children')
    wrapper = _json(_regular(ledger/'children'/(ident+'.json'),8192))
    value = wrapper.get('value') if isinstance(wrapper,dict) else None
    if (not isinstance(value,dict) or set(wrapper)!={'value','sha256'} or digest(value)!=wrapper['sha256']
            or set(value)!={'schema','scope_sha256','root_native_session_id','native_session_id','chain','directory_sha256','source'}
            or value['schema']!='eeas.change-review.child-lineage.v1' or value['scope_sha256']!=admission['scope_sha256']
            or value['root_native_session_id']!=root or value['native_session_id']!=target
            or value['directory_sha256']!=hashlib.sha256(scope['workspace_root'].encode()).hexdigest()
            or value['source']!='ORIGINAL_SDK_SESSION_GET' or not isinstance(value['chain'],list) or not 1<=len(value['chain'])<=12):
        raise Conflict('Original child lineage proof is unavailable or changed')
    expected, seen = target, {root}
    for node in value['chain']:
        if (not isinstance(node,dict) or set(node)!={'id','parent_id'} or not isinstance(node['id'],str)
                or node['id']!=expected or node['id'] in seen or not native.fullmatch(node['id'])
                or not isinstance(node['parent_id'],str) or not native.fullmatch(node['parent_id'])):
            raise Conflict('Original child lineage has a cycle or mismatched parent')
        seen.add(node['id']); expected=node['parent_id']
    if expected!=root or ancestor is not None and ancestor not in seen:
        raise Conflict('Original child lineage does not reach the admitted parent')
    reference={'id':ident,'revision':1,'sha256':wrapper['sha256']}
    if expected_ref is not None and expected_ref!=reference:
        raise Conflict('Original child lineage reference differs from the captured write')
    return reference


def record_admission(runtime_dir, admission):
    """Called by the controller inside its existing Store transaction."""
    root = Path(runtime_dir)/'change-review'; _directory(root)
    source = admission['scope']
    index_path = root/'admissions.json'
    head = {'schema':'eeas.change-review.admissions.v1',
            **{k:source[k] for k in ['project_id','session_ref','workspace_ref']}, 'admissions':[]}
    if index_path.exists():
        wrapper = _json(_regular(index_path, 32768))
        if (not isinstance(wrapper, dict) or not isinstance(wrapper.get('value'), dict) or
                digest(wrapper.get('value')) != wrapper.get('sha256')):
            raise Conflict('Change review admission index integrity failed')
        head = wrapper['value']
        if (head.get('schema') != 'eeas.change-review.admissions.v1' or
                not isinstance(head.get('admissions'), list) or len(head['admissions']) > 16 or
                any(not isinstance(item, dict) or type(item.get('generation')) is not int for item in head['admissions']) or
                any(head.get(k) != source[k] for k in ['project_id','session_ref','workspace_ref'])):
            raise Conflict('Change review admission index belongs to another owner')
    generation = source['generation']
    path = root/('generation-'+str(generation))/'admission.json'
    wrapper = {'value':admission,'sha256':digest(admission)}
    if path.exists():
        if _json(_regular(path, 1048576)) != wrapper:
            raise Conflict('Immutable historical admission changed')
    else:
        _put(path, wrapper)
    row = {'generation':generation,'native_session_id':admission['native_session_id'],
           'admission_sha256':wrapper['sha256']}
    previous = next((item for item in head['admissions'] if item['generation'] == generation), None)
    if previous is not None:
        if previous != row:
            raise Conflict('Historical admission generation changed')
        return
    if len(head['admissions']) >= 16 or head['admissions'] and generation <= head['admissions'][-1]['generation']:
        raise Conflict('Historical admission generation cannot replace an old entry')
    head['admissions'].append(row)
    _put(index_path, {'value':head,'sha256':digest(head)}, replace=index_path.exists())


def historical_admissions(runtime_dir, observed):
    """Yield only at most sixteen exact refs, never enumerate old blobs."""
    root = Path(runtime_dir)/'change-review'; index_path = root/'admissions.json'
    if not index_path.exists():
        return
    _directory(root)
    wrapper = _json(_regular(index_path, 32768))
    if not isinstance(wrapper, dict):
        raise Conflict('Historical admission index is unavailable')
    head = wrapper.get('value')
    if (not isinstance(head,dict) or digest(head) != wrapper.get('sha256') or
            head.get('schema') != 'eeas.change-review.admissions.v1' or
            any(head.get(k) != observed[k] for k in ['project_id','session_ref','workspace_ref']) or
            not isinstance(head.get('admissions'),list) or len(head['admissions']) > 16):
        raise Conflict('Historical admission index is unavailable')
    seen = set()
    for row in head['admissions']:
        if not isinstance(row, dict):
            raise Conflict('Historical admission entry is invalid')
        generation = row.get('generation')
        if type(generation) is not int or not 1 <= generation <= observed['generation'] or generation in seen:
            raise Conflict('Historical admission generation is invalid')
        seen.add(generation)
        if row.get('native_session_id') != observed.get('root_original_session_id',observed.get('original_session_id')):
            continue
        path = root/('generation-'+str(generation))/'admission.json'; _directory(path.parent)
        item = _json(_regular(path, 1048576))
        if (not isinstance(item,dict) or not isinstance(item.get('value'),dict) or
                digest(item.get('value')) != row.get('admission_sha256') or item.get('sha256') != row.get('admission_sha256')):
            raise Conflict('Immutable historical admission failed integrity')
        value = item['value']
        if not isinstance(value.get('scope'), dict) or value['scope'].get('generation') != generation:
            raise Conflict('Historical admission source generation differs')
        yield value


def _snapshot_meta(value):
    if not isinstance(value, dict) or set(value) != {'state', 'sha256', 'bytes', 'line_count', 'blob', 'stat'}:
        raise Conflict('Historical full-file snapshot metadata is incomplete')
    if value['state'] == 'ABSENT':
        if value['sha256'] is not None or value['bytes'] != 0 or value['blob'] is not None or value['stat'] is not None:
            raise Conflict('Historical absent file metadata is inconsistent')
        return hashlib.sha256(b'').hexdigest()
    if (value['state'] != 'UTF8' or not isinstance(value['sha256'], str) or not _SHA.fullmatch(value['sha256']) or
            type(value['bytes']) is not int or not 0 <= value['bytes'] <= MAX_SIDE or
            value['blob'] != 'blobs/'+value['sha256']+'.utf8' or not isinstance(value['stat'], dict)):
        raise Conflict('Historical full-file snapshot is unavailable or oversized')
    return value['sha256']


def _snapshot(value, ledger):
    expected = _snapshot_meta(value)
    if value['state'] == 'ABSENT':
        return '', expected
    path = ledger/value['blob']; _directory(path.parent)
    raw = _regular(path, MAX_SIDE)
    if len(raw) != value['bytes'] or hashlib.sha256(raw).hexdigest() != value['sha256'] or b'\0' in raw:
        raise Conflict('Historical full-file snapshot bytes failed integrity')
    try:
        return raw.decode('utf-8', errors='strict'), value['sha256']
    except UnicodeError:
        raise Conflict('Historical snapshot is not UTF-8 text') from None


def historical_observer_paths(admission, scope, data_dir, observer, read_file):
    """Locate saved bytes in this App copy without rebinding their old scope.

    These paths belong only to historical reads, never to plugin configuration,
    permissions, a new owner or command execution.
    """
    stored_ledger, stored_config = scope['ledger_root'], admission['config_path']
    if not isinstance(stored_ledger, str) or not isinstance(stored_config, str):
        raise Conflict('Observer history paths are invalid')
    module = ntpath if ntpath.splitdrive(stored_ledger)[0] else posixpath
    parent = module.dirname(stored_ledger)
    generation = 'generation-' + str(scope['generation'])
    if (not module.isabs(stored_ledger) or not module.isabs(stored_config) or
            module.basename(stored_ledger) != 'ledger' or module.basename(stored_config) != 'scope.json' or
            module.normcase(parent) != module.normcase(module.dirname(stored_config)) or
            module.basename(parent) != generation or module.basename(module.dirname(parent)) != observer):
        raise Conflict('Observer ledger and configuration are not one admitted generation')
    root = _directory(Path(data_dir)).resolve()
    ledger = resolve_data_path(stored_ledger, root)
    config = resolve_data_path(stored_config, root)
    if ledger != config.parent / 'ledger' or config.parent.name != generation:
        raise Conflict('Observer history mapping changed its generation')
    relocated = any(module.normcase(module.normpath(old)) != module.normcase(module.normpath(str(new)))
                    for old, new in ((stored_ledger, ledger), (stored_config, config)))
    if relocated:
        # The marker supplies a location, not permission or an execution grant.
        # Native restore publishes this target and the verified snapshot digest.
        provenance = _json(read_file(root / 'restore-provenance.json', 65536))
        if (not isinstance(provenance, dict) or provenance.get('schema_version') != 'eeas.restore-provenance.v1' or
                not isinstance(provenance.get('target_data_dir'), str) or
                not Path(provenance['target_data_dir']).is_absolute() or
                os.path.normcase(os.path.normpath(provenance['target_data_dir'])) != os.path.normcase(str(root)) or
                not isinstance(provenance.get('snapshot_sha256'), str) or not _SHA.fullmatch(provenance['snapshot_sha256']) or
                provenance['snapshot_sha256'] != provenance.get('manifest_sha256')):
            raise Conflict('Observer history restore target or snapshot differs')
    return ledger, config


def read_history(admission, observed, *, data_dir):
    """Only current saved generation's admitted ledger and exact completed calls.

    Missing/crashed/changed observations remain partial. This performs no read
    of a current workspace leaf and no native/HTTP request.
    """
    entries, reasons = [], set()
    if not admission:
        return entries, ['NO_ADMITTED_FULL_FILE_HISTORY']
    try:
        scope = admission['scope']
        expected = {k: observed[k] for k in ['project_id', 'session_ref', 'generation', 'workspace_ref']}
        if (any(scope.get(k) != value for k, value in expected.items()) or
                digest(scope) != admission['scope_sha256'] or
                admission['native_session_id'] != observed.get('root_original_session_id',observed['original_session_id'])):
            raise Conflict('Historical snapshot owner or generation differs')
        ledger, config_path = historical_observer_paths(admission, scope, data_dir, 'change-review', _regular)
        # The saved generation belongs to this exact project and native session ledger.
        session_storage = (Path(data_dir).resolve() / 'agent-sessions' / expected['project_id'] /
                           expected['session_ref']['id'] / 'runtime' / 'change-review' / ('generation-' + str(scope['generation'])))
        if Path(scope['ledger_root']) != ledger and ledger.parent != session_storage:
            raise Conflict('Historical change-review ledger belongs to another session')
        _directory(ledger)
        _directory(config_path.parent)
        config_raw = _regular(config_path, 524288)
        if (hashlib.sha256(config_raw).hexdigest() != admission['config_sha256'] or
                _json(config_raw) != {'schema_version': 1, 'scope': scope, 'scope_sha256': admission['scope_sha256'],
                                     'native_session_id': observed.get('root_original_session_id',observed['original_session_id'])}):
            raise Conflict('Historical snapshot scope configuration changed')
    except (KeyError, TypeError, ValueError, OSError, Conflict, InvalidInput):
        return [], ['FULL_FILE_SCOPE_UNAVAILABLE']
    calls = 0
    identities = {}
    for message in observed['messages'][-200:]:
        if not isinstance(message, dict) or not isinstance(message.get('info'), dict) or not isinstance(message.get('parts'), list):
            continue
        info = message['info']
        if info.get('role') != 'assistant' or info.get('sessionID') != observed['original_session_id']:
            continue
        for part in message['parts'][:1024]:
            if isinstance(part, dict) and isinstance(part.get('callID'), str) and part.get('type') == 'tool':
                identities.setdefault(part['callID'], set()).add((info.get('id'), part.get('id')))
    for message in observed['messages'][-200:]:
        info = message.get('info', {}) if isinstance(message, dict) else {}
        if not isinstance(info, dict) or info.get('role') != 'assistant' or info.get('sessionID') != observed['original_session_id']:
            continue
        parts = message.get('parts', [])
        if not isinstance(parts, list):
            continue
        for part in parts[:1024]:
            if (not isinstance(part, dict) or part.get('type') != 'tool' or part.get('tool') not in {'edit', 'write', 'apply_patch'} or
                    part.get('sessionID') != observed['original_session_id'] or part.get('messageID') != info.get('id') or
                    not isinstance(part.get('callID'), str) or not isinstance(part.get('state'), dict) or
                    part['state'].get('status') != 'completed'):
                continue
            if len(identities.get(part['callID'], ())) != 1:
                reasons.add('AMBIGUOUS_ORIGINAL_CALL_ID'); continue
            calls += 1
            if calls > 128:
                reasons.add('FULL_FILE_CALL_LIMIT'); break
            key = digest({'native_session_id': observed['original_session_id'], 'call_id': part['callID']})
            path = ledger/'calls'/key/'result.json'
            if not path.exists():
                reasons.add('FULL_FILE_RECORD_NOT_OBSERVED'); continue
            try:
                _directory(path.parent)
                value = _json(_regular(path, 262144))
                if not isinstance(value, dict):
                    raise Conflict('Historical record is not a metadata object')
                record_sha = value.get('record_sha256')
                if not isinstance(record_sha, str) or digest({k:v for k,v in value.items() if k != 'record_sha256'}) != record_sha:
                    raise Conflict('Historical record hash differs')
                if (value.get('schema') != 'eeas.change-review.record.v1' or value.get('status') != 'COMPLETE' or
                        value.get('tool_returned_successfully') is not True or value.get('scope_sha256') != admission['scope_sha256'] or
                        any(value.get(k) != expected[k] for k in expected) or value.get('native_session_id') != observed['original_session_id'] or
                        value.get('call_id') != part['callID'] or value.get('tool') != part['tool'] or
                        value.get('args_sha256') != digest(part['state'].get('input')) or
                        not isinstance(value.get('files'), list) or not 1 <= len(value['files']) <= 20):
                    raise Conflict('Original completed tool and historical record do not match')
                if observed['original_session_id']!=admission['native_session_id']:
                    if not isinstance(value.get('child_lineage_ref'),dict):
                        raise Conflict('Child history requires its exact captured SDK lineage')
                    read_child_lineage(admission,observed['original_session_id'],expected_ref=value['child_lineage_ref'],historical_ledger=ledger)
                elif value.get('child_lineage_ref') is not None:
                    raise Conflict('A root tool cannot borrow a child history reference')
                for item in value['files']:
                    if not isinstance(item, dict):
                        raise Conflict('Historical full-file record entry is invalid')
                    if item.get('status') != 'COMPLETE':
                        reasons.add('FULL_FILE_PARTIAL'); continue
                    path = relative(item['path'])
                    if not any(grant == '*' or grant == path or (grant.endswith('/') and path.startswith(grant))
                               for grant in scope['allowed_write_paths']):
                        raise Conflict('Historical file is outside the original write grant')
                    before_sha = _snapshot_meta(item.get('before'))
                    after_sha = _snapshot_meta(item.get('after'))
                    if before_sha == after_sha:
                        reasons.add('NO_CONTENT_CHANGE'); continue
                    if item['before']['state'] == item['after']['state'] == 'ABSENT':
                        raise Conflict('Historical file was absent on both sides')
                    status = 'added' if item['before']['state'] == 'ABSENT' else 'deleted' if item['after']['state'] == 'ABSENT' else 'modified'
                    # Directory reads validate only bounded metadata. Blobs are
                    # read and hashed only after an explicit exact-file request.
                    snapshots = (dict(item['before']), dict(item['after']))
                    def load(values=snapshots, root=ledger):
                        before, bsha = _snapshot(values[0], root)
                        after, asha = _snapshot(values[1], root)
                        return {'before': before, 'after': after, 'before_sha256': bsha, 'after_sha256': asha,
                                'composition': 'EXACT_SAVED_BEFORE_AFTER', 'encoding': 'UTF-8'}
                    entries.append({'path': path, 'status': status,
                                    'before_sha256': before_sha, 'after_sha256': after_sha,
                                    'before_bytes': item['before']['bytes'], 'after_bytes': item['after']['bytes'],
                                    'load_content': load,
                                    'message_id': info['id'], 'part_id': part['id'], 'call_id': part['callID'],
                                    'record_sha256': record_sha,
                                    **({'source_native_session_id':observed['original_session_id'],
                                        'child_lineage_ref':value['child_lineage_ref']}
                                       if observed['original_session_id']!=admission['native_session_id'] else {})})
                    if len(entries) >= 128:
                        reasons.add('FULL_FILE_COUNT_LIMIT'); break
            except (OSError, ValueError, TypeError, KeyError, Conflict, InvalidInput):
                reasons.add('FULL_FILE_RECORD_UNAVAILABLE')
        if calls > 128 or len(entries) >= 128:
            break
    return entries, sorted(reasons)


def _windows_basic_stamp(api, handle, info):
    """Use the producer's Windows ChangeTime semantics, never Python ctime.

    Bun/Node bigint ctimeNs is ChangeTime; Python3.10 st_ctime_ns on Windows
    is CreationTime. The source-native probe binds this mapping. CreationTime
    is retained independently as an additional stability check.
    """
    import ctypes
    from packages.adapters.windows_workspace_handles import FILE_BASIC_INFO
    value = FILE_BASIC_INFO()
    if not api.get_ex(handle, 0, ctypes.byref(value), ctypes.sizeof(value)):
        api.error('GetFileInformationByHandleEx(FileBasicInfo)')
    if (value.creation != info['creation'] or value.write != info['write'] or
            value.creation <= 0 or value.write <= 0 or value.change <= 0 or value.attributes != info['attributes']):
        raise Conflict('Windows source metadata changed while observing its version')
    epoch = 116444736000000000
    return ({'dev':str(info['device']), 'ino':str(info['inode']), 'size':str(info['size']),
             'mtime_ns':str((value.write-epoch)*100), 'ctime_ns':str((value.change-epoch)*100)},
            value.creation)


def _windows_approval_source(native, binding, path, private_check):
    """Read one bounded leaf and comparable times through the same real handle.

    Reuse the existing NTFS/parent-relative/reparse/final-path/hardlink guard.
    Sharing stays READ only. This covers the observation, not the later native
    tool's approval-to-write window.
    """
    import ntpath
    from packages.adapters.windows_workspace_handles import GENERIC_READ, FILE_READ_ATTRIBUTES, FILE_SHARE_READ
    with native.open_parent(binding, path, private_check=private_check) as (parent, name):
        expected = ntpath.join(parent.path, name)
        try:
            handle = native.api.open_relative(parent.native_handle, name, GENERIC_READ | FILE_READ_ATTRIBUTES, FILE_SHARE_READ)
        except OSError as error:
            if getattr(error, 'winerror', error.errno) == 2:
                return None, None, None
            raise
        try:
            initial = native._check(handle, expected, directory=False)
            if initial['size'] > MAX_SIDE:
                raise Conflict('Windows approval source exceeds the bounded snapshot size')
            before, creation = _windows_basic_stamp(native.api, handle, initial)
            raw = bytearray()
            while True:
                part = native.api.read(handle, min(65536, MAX_SIDE+1-len(raw)))
                if not part:
                    break
                raw.extend(part)
                if len(raw) > MAX_SIDE:
                    raise Conflict('Windows approval source grew beyond its snapshot bound')
            after, final_creation = _windows_basic_stamp(native.api, handle, native._check(handle, expected, directory=False))
            # Re-open the current name under the same protected parent. Never
            # accept an old unlinked leaf as the current permission baseline.
            live = native.api.open_relative(parent.native_handle, name, FILE_READ_ATTRIBUTES, FILE_SHARE_READ)
            try:
                current, current_creation = _windows_basic_stamp(native.api, live, native._check(live, expected, directory=False))
                held, held_creation = _windows_basic_stamp(native.api, handle, native._check(handle, expected, directory=False))
                if (before != after or current != after or held != after or
                        creation != final_creation or creation != current_creation or creation != held_creation or
                        len(raw) != initial['size']):
                    raise Conflict('Windows source version changed while reading its approval baseline')
            finally:
                native.api.close(live)
            return bytes(raw), before, after
        finally:
            native.api.close(handle)


def approve_guard(admission, observed, native, message, *, workspaces, binding):
    """Recheck a pending original write before sending a single "once" reply.

    This is a pre-approval drift guard, not an atomic compare-and-write. The
    unmodified native tool still has a window after approval and before write.
    Rejecting a request never needs this positive-approval guard.
    """
    try:
        if not admission or native.get('permission') != 'edit' or native.get('sessionID') != observed['original_session_id']:
            raise ValueError()
        scope = admission['scope']
        expected = {key: observed[key] for key in ['project_id', 'session_ref', 'generation', 'workspace_ref']}
        if (digest(scope) != admission['scope_sha256'] or any(scope.get(k) != value for k,value in expected.items()) or
                admission['native_session_id'] != observed.get('root_original_session_id',native['sessionID'])):
            raise ValueError()
        tool = native['tool']; call_id = tool['callID']
        if (not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', call_id) or
                message.get('info', {}).get('id') != tool['messageID'] or message['info'].get('role') != 'assistant' or
                message['info'].get('sessionID') != native['sessionID']):
            raise ValueError()
        parts = [part for part in message['parts'] if part.get('type') == 'tool' and part.get('callID') == call_id]
        if len(parts) != 1 or parts[0].get('sessionID') != native['sessionID'] or parts[0].get('messageID') != tool['messageID']:
            raise ValueError()
        part = parts[0]
        if part.get('tool') not in {'edit', 'write', 'apply_patch'} or part['state'].get('status') not in {'pending', 'running'}:
            raise ValueError()
        ledger = Path(scope['ledger_root']); _directory(ledger)
        key = digest({'native_session_id': native['sessionID'], 'call_id': call_id})
        call_dir = ledger/'calls'/key; _directory(call_dir)
        if (call_dir/'result.json').exists():
            raise ValueError()
        before = _json(_regular(call_dir/'before.json', 262144))
        before_sha = before.get('before_sha256')
        if (digest({k:v for k,v in before.items() if k != 'before_sha256'}) != before_sha or
                before.get('schema') != 'eeas.change-review.before.v1' or before.get('status') != 'BEFORE_CAPTURED' or
                before.get('reason_codes') != [] or before.get('scope_sha256') != admission['scope_sha256'] or
                any(before.get(k) != value for k,value in expected.items()) or
                before.get('native_session_id') != native['sessionID'] or before.get('call_id') != call_id or
                before.get('tool') != part['tool'] or before.get('args_sha256') != digest(part['state'].get('input')) or
                not isinstance(before.get('files'), list) or not 1 <= len(before['files']) <= 20):
            raise ValueError()
        if native['sessionID']!=admission['native_session_id']:
            if not isinstance(before.get('child_lineage_ref'),dict):
                raise ValueError()
            read_child_lineage(admission,native['sessionID'],expected_ref=before['child_lineage_ref'],ancestor=observed.get('active_native_session_id'))
        elif before.get('child_lineage_ref') is not None:
            raise ValueError()
        paths = [relative(row['path']) for row in before['files']]
        if len(paths) != len(set(paths)) or any(not any(grant == '*' or grant == p or grant.endswith('/') and p.startswith(grant)
                for grant in binding['allowed_write_paths']) for p in paths):
            raise ValueError()
        from packages.adapters.opencode_permission_paths import permission_paths
        patterns = permission_paths(native, message, binding['root'])
        if set(patterns) != set(paths) or any(any(c in p for c in '*?') for p in patterns):
            raise ValueError()
        metadata = native.get('metadata', {})
        patch = metadata.get('diff')
        if not isinstance(patch, str) or not patch or len(patch.encode()) > 262144:
            raise ValueError()
        if part['tool'] != 'apply_patch' and metadata.get('filepath') is not None:
            candidate = str(Path(metadata['filepath']))
            if len(paths) != 1 or os.path.normcase(os.path.abspath(candidate)) != os.path.normcase(os.path.join(binding['root'], paths[0])):
                raise ValueError()
        if part['tool'] == 'apply_patch':
            baselines = {path: row['before']['state'] for path, row in zip(paths, before['files'])}
            for item in metadata['files']:
                source = os.path.relpath(item['filePath'], binding['root']).replace(os.sep, '/')
                if baselines.get(source) != ('ABSENT' if item['type'] == 'add' else 'UTF8'):
                    raise ValueError()
                if item['type'] == 'move':
                    destination = os.path.relpath(item['movePath'], binding['root']).replace(os.sep, '/')
                    if baselines.get(destination) != 'ABSENT':
                        raise ValueError()
        budget = Path(scope['budget_root']); _directory(budget)
        conflicts = budget/'conflicts'
        if conflicts.exists():
            _directory(conflicts)
            # Metadata names only; no all-blob or workspace directory scan.
            with os.scandir(conflicts) as rows:
                for count, entry in enumerate(rows):
                    if count >= 16384 or key in entry.name.split('.'):
                        raise ValueError()
        reviewed = []
        root = Path(binding['root'])
        windows_handles = workspaces._native_handles()
        for row, path in zip(before['files'], paths):
            workspaces._private(binding, path)
            _directory(budget/'claims')
            claim = _json(_regular(budget/'claims'/(hashlib.sha256(path.encode()).hexdigest()+'.json'), 8192))
            if claim.get('schema') != 'eeas.change-review.claim.v1' or claim.get('scope_sha256') != admission['scope_sha256'] or claim.get('call_key') != key:
                raise ValueError()
            captured = row['before']; _snapshot_meta(captured)
            target = root.joinpath(*path.split('/'))
            allowed_parents = {os.path.normcase(str(p)) for p in [target.parent, *target.parent.parents]}
            parents = row.get('parents')
            if not isinstance(parents, list) or not parents or len(parents) > 256:
                raise ValueError()
            seen_root = False
            def check_parents():
                nonlocal seen_root
                for parent in parents:
                    filename = Path(parent['path'])
                    if os.path.normcase(str(filename)) not in allowed_parents:
                        raise ValueError()
                    info = filename.lstat()
                    if (not stat.S_ISDIR(info.st_mode) or filename.is_symlink() or getattr(info, 'st_file_attributes', 0) & 0x400 or
                            str(info.st_dev) != parent['dev'] or str(info.st_ino) != parent['ino']):
                        raise ValueError()
                    if os.path.normcase(str(filename)) == os.path.normcase(str(root)):
                        seen_root = True
                if not seen_root:
                    raise ValueError()
            check_parents()
            if windows_handles is not None:
                raw, live_before, windows_after = _windows_approval_source(windows_handles, binding, path, workspaces._private)
            else:
                try:
                    live_before = target.lstat()
                    if not stat.S_ISREG(live_before.st_mode) or live_before.st_nlink != 1 or live_before.st_size > MAX_SIDE:
                        raise ValueError()
                except FileNotFoundError:
                    live_before = None
                try:
                    raw = workspaces._raw(binding, path, missing=True)
                except Exception as error:
                    from packages.domain.store import NotFound
                    if isinstance(error, NotFound): raw = None
                    else: raise
            if captured['state'] == 'ABSENT':
                if raw is not None or live_before is not None:
                    raise ValueError()
            else:
                if raw is None or hashlib.sha256(raw).hexdigest() != captured['sha256'] or len(raw) != captured['bytes']:
                    raise ValueError()
                if windows_handles is not None:
                    if live_before != captured['stat'] or windows_after != captured['stat']:
                        raise ValueError()
                else:
                    after = target.lstat()
                    stamp = lambda s: {'dev':str(s.st_dev),'ino':str(s.st_ino),'size':str(s.st_size),
                                       'mtime_ns':str(s.st_mtime_ns),'ctime_ns':str(s.st_ctime_ns)}
                    if live_before is None or stamp(live_before) != captured['stat'] or stamp(after) != captured['stat']:
                        raise ValueError()
            check_parents()
            reviewed.append({'path':path,'state':captured['state'],'sha256':captured['sha256']})
        return {'before_record_sha256': before_sha, 'pending_request_sha256': digest(native),
                'permission_diff_sha256': hashlib.sha256(patch.encode()).hexdigest(), 'files': reviewed,
                'atomic_write_guaranteed': False,
                **({'original_session_id':native['sessionID'],'root_native_session_id':admission['native_session_id'],
                    'child_lineage_ref':before['child_lineage_ref']} if native['sessionID']!=admission['native_session_id'] else {})}
    except (OSError, ValueError, TypeError, KeyError, AttributeError, Conflict, InvalidInput):
        raise Conflict('WORKSPACE_SOURCE_CHANGED: saved files or review evidence changed; reject this pending request and review a new edit') from None
