"""Private, same-user desktop state backup and restore; never starts an Agent.

Backups are local directories, separate from distributable program packages.
Restore only publishes a new user-data directory in a stopped compatible install.
Opaque histories, drafts and UNKNOWN outcomes are copied without interpretation.
"""
from __future__ import annotations
import argparse
from contextlib import closing
import base64
import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import uuid

_protocol_spec = importlib.util.spec_from_file_location('cirven_recovery_state_protocols',
    Path(__file__).with_name('native_state_protocols.py'))
_protocols = importlib.util.module_from_spec(_protocol_spec)
_protocol_spec.loader.exec_module(_protocols)

SCHEMA = 'cirven.private-user-state.v1'
MANIFEST = 'USER_STATE.private.json'
MAX_FILES = 100_000
MAX_BYTES = 32 * 1024 ** 3
RESERVE_BYTES = 1024 ** 3
COMPATIBILITY = ('runtime/electron/version', 'runtime/opencode/opencode.exe')
EXCLUDED = ('tmp', 'cache', 'crashes', 'logs', 'controller-boot.log', 'controller-port.json',
            'local-access.token', 'worker.lock', 'local-appdata/Microsoft/Edge', 'appdata/Microsoft/Edge',
            'home/appdata/Local/Microsoft/Windows/INetCache',
            'home/appdata/locallow/SogouPY/mmkv/engine_0',
            'home/appdata/locallow/SogouPY/mmkv/engine_0.crc')

# Missing manifest policy means the original global-home exclusions only.
LEGACY_CACHE_EXCLUSION_POLICY = 'global-only'
AGENT_HOME_CACHE_EXCLUSION_POLICY = 'root-and-agent-home-cache-v1'
SHADER_CACHE_EXCLUSION_POLICY = 'root-and-agent-home-shader-cache-v2'
WORKFLOW_CACHE_EXCLUSION_POLICY = 'root-agent-home-shader-and-workflow-runtime-cache-v3'
CURRENT_CACHE_EXCLUSION_POLICY = WORKFLOW_CACHE_EXCLUSION_POLICY
CACHE_EXCLUSION_POLICIES = (LEGACY_CACHE_EXCLUSION_POLICY, AGENT_HOME_CACHE_EXCLUSION_POLICY,
                            SHADER_CACHE_EXCLUSION_POLICY, WORKFLOW_CACHE_EXCLUSION_POLICY)
_AGENT_HOME_CACHE = re.compile(
    r'agent-sessions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/'
    r'[0-9a-f]{32}/runtime/home/appdata/local/microsoft/windows/inetcache(?:/.*)?')
# Only v2 excludes driver-generated shader bytes. Older snapshots may contain
# ordinary files here and must still verify and restore them without filtering.
_HOME_SHADER_CACHE = re.compile(
    r'(?:agent-sessions/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/'
    r'[0-9a-f]{32}/runtime/)?home/appdata/locallow/nvidia/perdriverversion/dxcache(?:/.*)?')


class RecoveryError(ValueError):
    pass


def _require(condition, message):
    if not condition:
        raise RecoveryError(message)


def _no_links(path):
    path = Path(path).absolute()
    for p in (path, *path.parents):
        if os.path.lexists(p):
            s = p.lstat()
            _require(not stat.S_ISLNK(s.st_mode) and not getattr(s, 'st_file_attributes', 0) & 0x400,
                     'Linked/reparse paths are not supported')
    return path


def _local(path):
    path = _no_links(path)
    if os.name == 'nt':
        _require(not str(path).startswith('\\\\') and bool(path.drive), 'Use a local fixed drive')
        _require(ctypes.windll.kernel32.GetDriveTypeW(str(path.anchor)) == 3, 'Use a local fixed drive')
    return path.resolve()


def _name(name):
    _require(isinstance(name, str) and 0 < len(name) <= 4096 and '\\' not in name,
             'Invalid backup member')
    rel = PurePosixPath(name)
    _require(not rel.is_absolute() and str(rel) == name, 'Invalid backup member')
    for part in rel.parts:
        _require(part not in {'.', '..'} and not any(ord(c) < 32 or c in ':<>"|?*' for c in part)
                 and part.rstrip(' .') == part, 'Invalid backup member')
        _require(not re.fullmatch(r'(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', part),
                 'Reserved Windows backup member')
    return rel


def _open(path):
    path = _no_links(path)
    fd = os.open(path, os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0))
    try:
        s = os.fstat(fd)
        _require(stat.S_ISREG(s.st_mode) and not getattr(s, 'st_file_attributes', 0) & 0x400,
                 'Expected an ordinary file')
        return os.fdopen(fd, 'rb')
    except BaseException:
        os.close(fd)
        raise


def _hash(path):
    digest = hashlib.sha256()
    with _open(path) as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _read(path, maximum=16 * 1024 * 1024):
    with _open(path) as f:
        raw = f.read(maximum + 1)
    _require(len(raw) <= maximum, 'Metadata exceeds its bound')
    try:
        return json.loads(raw)
    except (ValueError, UnicodeError) as error:
        raise RecoveryError('Invalid recovery metadata') from error


def _write(path, value):
    with path.open('x', encoding='utf-8') as f:
        json.dump(value, f, ensure_ascii=False, sort_keys=True, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def _powershell(code):
    encoded = base64.b64encode(code.encode('utf-16-le')).decode('ascii')
    result = subprocess.run([str(Path(os.environ['SystemRoot']) / 'System32/WindowsPowerShell/v1.0/powershell.exe'),
        '-NoLogo', '-NoProfile', '-NonInteractive', '-EncodedCommand', encoded],
        capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
    _require(result.returncode == 0, 'Windows private-directory protection failed')
    return result.stdout.decode('utf-8-sig').strip()


def _owner():
    if os.name == 'nt':
        return 'nt:' + _powershell("[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false);[Security.Principal.WindowsIdentity]::GetCurrent().User.Value")
    return 'posix:' + str(os.getuid())


def _private(path):
    path.mkdir(mode=0o700)
    if os.name == 'nt':
        literal = "'" + str(path).replace("'", "''") + "'"
        _powershell("$ErrorActionPreference='Stop';$p=" + literal + ";"
            "$a=[Security.AccessControl.DirectorySecurity]::new();$a.SetAccessRuleProtection($true,$false);"
            "$u=[Security.Principal.WindowsIdentity]::GetCurrent().User;$s=[Security.Principal.SecurityIdentifier]::new('S-1-5-18');"
            "foreach($id in @($u,$s)){$a.AddAccessRule([Security.AccessControl.FileSystemAccessRule]::new($id,'FullControl','ContainerInherit,ObjectInherit','None','Allow'))};"
            "Set-Acl -LiteralPath $p -AclObject $a;if(-not (Get-Acl -LiteralPath $p).AreAccessRulesProtected){throw 'ACL not protected'}")
    else:
        path.chmod(0o700)


def _publish(stage, destination):
    # os.rename never replaces on Windows; Linux needs RENAME_NOREPLACE.
    _no_links(destination)
    if os.name == 'nt':
        os.rename(stage, destination)
    else:
        libc = ctypes.CDLL(None, use_errno=True)
        rename = getattr(libc, 'renameat2', None)
        _require(rename is not None, 'Atomic no-replace publication is unavailable')
        rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
        rename.restype = ctypes.c_int
        if rename(-100, os.fsencode(stage), -100, os.fsencode(destination), 1):
            raise OSError(ctypes.get_errno(), 'Recovery destination publication refused')


def _installation(root):
    root = _local(root)
    manifest = _read(root / 'PACKAGE_MANIFEST.json')
    _require(manifest.get('schema') == 'eeas.native-companion-package.v1', 'Unsupported program package')
    rows = {r['path']: r for r in manifest['files']}
    required = [*COMPATIBILITY, *[n for n in rows if n.startswith('packages/domain/migrations/') and n.endswith('.sql')]]
    _require(len(required) > len(COMPATIBILITY), 'Program migration sources are missing')
    for name in required:
        _name(name)
        _require(name in rows and _hash(root / name) == rows[name]['sha256'], 'Program compatibility bytes differ')
    return root, rows


def _stopped(root):
    import psutil
    data = root / 'user-data'
    for process in psutil.process_iter(['exe']):
        if process.pid == os.getpid():
            continue
        try:
            exe = Path(process.info['exe']) if process.info['exe'] else None
            if exe and exe.is_relative_to(root):
                raise RecoveryError('Exit this installation before backup or restore')
            # Detect a separately selected Python API using this same data root.
            if exe and exe.name.casefold() in {'python.exe', 'pythonw.exe', 'python', 'python3'}:
                if any(str(data).casefold() == arg.casefold() for arg in process.cmdline()):
                    raise RecoveryError('This installation data is in use')
        except (psutil.AccessDenied, psutil.NoSuchProcess):
            continue


def _cache_policy(value):
    _require(isinstance(value, str) and value in CACHE_EXCLUSION_POLICIES,
             'Unsupported cache exclusion policy')
    return value


def _excluded(name, *, cache_exclusion_policy=CURRENT_CACHE_EXCLUSION_POLICY):
    policy = _cache_policy(cache_exclusion_policy)
    n = name.casefold()
    return (any(n == p.casefold() or n.startswith(p.casefold() + '/') for p in EXCLUDED)
            or policy in (AGENT_HOME_CACHE_EXCLUSION_POLICY, SHADER_CACHE_EXCLUSION_POLICY, WORKFLOW_CACHE_EXCLUSION_POLICY)
            and _AGENT_HOME_CACHE.fullmatch(n) is not None
            or policy in (SHADER_CACHE_EXCLUSION_POLICY, WORKFLOW_CACHE_EXCLUSION_POLICY)
            and _HOME_SHADER_CACHE.fullmatch(n) is not None
            or policy == WORKFLOW_CACHE_EXCLUSION_POLICY and n.startswith('workflow-kernels/')
            and not _protocols.kernel_private_member(name))


def _inventory(root, *, exclude=True, cache_exclusion_policy=CURRENT_CACHE_EXCLUSION_POLICY):
    policy = _cache_policy(cache_exclusion_policy)
    result = {}; total = 0; todo = [root]
    while todo:
        directory = todo.pop()
        _no_links(directory)
        for path in sorted(directory.iterdir()):
            name = path.relative_to(root).as_posix()
            if exclude and _excluded(name, cache_exclusion_policy=policy):
                continue
            _name(name); _no_links(path)
            s = path.lstat()
            if stat.S_ISDIR(s.st_mode):
                result[name] = {'kind': 'directory'}; todo.append(path)
            else:
                _require(stat.S_ISREG(s.st_mode), 'Unsupported user-state object')
                total += s.st_size
                _require(total <= MAX_BYTES, 'User state exceeds the backup byte limit')
                result[name] = {'kind': 'file', 'bytes': s.st_size, 'sha256': _hash(path)}
            _require(len(result) <= MAX_FILES, 'User state exceeds the backup entry limit')
    return result, total


def _database(root):
    p = root / 'eeas.sqlite3'; _no_links(p)
    connection = sqlite3.connect(p.as_uri() + '?mode=ro', uri=True)
    try:
        _require(connection.execute('PRAGMA integrity_check').fetchall() == [('ok',)], 'Database integrity check failed')
        _require(connection.execute('PRAGMA foreign_key_check').fetchone() is None, 'Database foreign keys are invalid')
        return [r[0] for r in connection.execute('SELECT version FROM schema_migrations ORDER BY version')]
    finally:
        connection.close()


def _copy(root, target, rows):
    for name, row in sorted(rows.items(), key=lambda item: (item[0].count('/'), item[0])):
        _name(name)
        destination = target / name
        _no_links(destination)
        if row['kind'] == 'directory':
            destination.mkdir(exist_ok=True)
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        h = hashlib.sha256(); count = 0
        with _open(root / name) as source, destination.open('xb') as output:
            for block in iter(lambda: source.read(1024 * 1024), b''):
                count += len(block)
                _require(count <= row['bytes'], 'A source file changed while copying')
                output.write(block); h.update(block)
        _require(count == row['bytes'] and h.hexdigest() == row['sha256'] and _hash(destination) == row['sha256'],
                 'A source file changed or its copy is incomplete')
        _require(destination.stat().st_nlink == 1, 'Recovery must make independent copies')


def _renderer_origin(data):
    file = data / 'controller-port.json'
    if not file.exists():
        return None
    value = _read(file, maximum=1024)
    _require(isinstance(value, dict) and set(value) == {'schema', 'port'}
             and value['schema'] == 'eeas.controller-port.v1'
             and type(value['port']) is int and 1024 <= value['port'] <= 65535,
             'Invalid saved renderer origin')
    return 'http://127.0.0.1:' + str(value['port'])


def _no_live_workflow(data):
    # A server outside the installation's interpreter directory can hold a
    # stage operation without a WorkerLock. A dead owner's UNKNOWN record is
    # preserved for explicit reconciliation after restore.
    import psutil
    path = data / 'eeas.sqlite3'
    _no_links(path)
    with closing(sqlite3.connect(path.as_uri()+'?mode=ro',uri=True)) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='stage_workflow_operations'").fetchone():
            for (body,) in db.execute('SELECT r.body FROM stage_workflow_runs r JOIN stage_workflow_operations o '
                                      'ON o.entity_id=r.id WHERE o.completed=0'):
                stages = json.loads(body)['stages']
                for stage in stages:
                    if stage['status'] != 'running':
                        continue
                    worker = stage.get('worker') or {}
                    try:
                        if psutil.Process(worker['pid']).create_time() != worker['birth']:
                            continue
                    except psutil.NoSuchProcess:
                        continue
                    except (KeyError,TypeError,psutil.AccessDenied):
                        pass
                    raise RecoveryError('A workflow stage is executing; wait for it to stop before backup')


def backup(installation, destination):
    root, package = _installation(installation); _stopped(root)
    data = _local(root / 'user-data'); destination = _local(destination)
    _require(data.is_dir() and not os.path.lexists(destination), 'Choose an absent backup directory')
    _require(not destination.is_relative_to(root), 'Keep private backups outside the program directory')
    renderer_origin = _renderer_origin(data)
    policy = _cache_policy(CURRENT_CACHE_EXCLUSION_POLICY)
    _no_live_workflow(data)
    versions = _database(data); rows, total = _inventory(data, cache_exclusion_policy=policy)
    migration_pins = {}
    for version in versions:
        _name(version); name = 'packages/domain/migrations/' + version
        _require(name in package, 'An applied migration is missing from this program')
        migration_pins[version] = package[name]['sha256']
    destination.parent.mkdir(parents=True, exist_ok=True); _no_links(destination.parent)
    _require(shutil.disk_usage(destination.parent).free >= total + RESERVE_BYTES, 'Insufficient free space for a private backup')
    stage = destination.with_name(destination.name + '.partial-' + uuid.uuid4().hex)
    _private(stage); (stage / 'data').mkdir()
    _copy(data, stage / 'data', rows)
    _require(_inventory(data, cache_exclusion_policy=policy)[0] == rows and _database(stage / 'data') == versions
             and _renderer_origin(data) == renderer_origin, 'Source state changed during backup')
    _stopped(root)
    value = {'schema': SCHEMA, 'owner': _owner(), 'source_installation': str(root), 'source_data': str(data),
        'program_manifest_sha256': _hash(root / 'PACKAGE_MANIFEST.json'), 'entries': rows, 'bytes': total,
        'renderer_origin': renderer_origin,
        'required_state_protocols': _protocols.requirements(rows),
        'migrations': migration_pins, 'compatibility': {n: package[n]['sha256'] for n in COMPATIBILITY},
        'excluded': list(EXCLUDED), 'cache_exclusion_policy': policy,
        'unknown_outcomes': 'PRESERVED_OPAQUE_NEVER_REPLAYED',
        'scope': 'Private local same-user durable desktop state; not a public distribution or portable credentials export'}
    _write(stage / MANIFEST, value)
    digest = _hash(stage / MANIFEST)
    (stage / (MANIFEST + '.sha256')).write_text(digest + '\n', encoding='ascii')
    verify(stage, digest)
    _stopped(root)
    _no_live_workflow(data)
    _publish(stage, destination)
    return {'status': 'BACKUP_COMPLETE', 'path': str(destination), 'manifest_sha256': digest,
        'files': sum(r['kind'] == 'file' for r in rows.values()), 'bytes': total,
        'program_manifest_sha256': value['program_manifest_sha256']}


def verify(backup_directory, expected_sha256=None):
    root = _local(backup_directory)
    digest = _hash(root / MANIFEST)
    with _open(root / (MANIFEST + '.sha256')) as f:
        sidecar = f.read(128).decode('ascii').strip()
    _require(re.fullmatch('[0-9a-f]{64}', sidecar) and digest == sidecar
             and (expected_sha256 is None or digest == expected_sha256), 'Backup manifest hash differs')
    value = _read(root / MANIFEST)
    _require(isinstance(value, dict), 'Invalid backup metadata')
    _require(value.get('schema') == SCHEMA and value.get('owner') == _owner(), 'This backup belongs to a different OS user')
    policy = _cache_policy(value.get('cache_exclusion_policy', LEGACY_CACHE_EXCLUSION_POLICY))
    _require(isinstance(value, dict), 'Invalid backup metadata')
    _require(isinstance(value.get('migrations'), dict) and 0 < len(value['migrations']) <= 1000, 'Invalid migration pins')
    _require(isinstance(value.get('compatibility'), dict) and set(value['compatibility']) == set(COMPATIBILITY), 'Invalid runtime pins')
    _require(isinstance(value.get('source_data'), str) and '\x00' not in value['source_data'] and Path(value['source_data']).is_absolute(), 'Invalid saved data root')
    origin = value.get('renderer_origin')
    _require(origin is None or isinstance(origin, str)
             and re.fullmatch(r'http://127\.0\.0\.1:[1-9][0-9]{3,4}', origin)
             and 1024 <= int(origin.rsplit(':', 1)[1]) <= 65535, 'Invalid saved renderer origin')
    for pin in [*value['compatibility'].values(), *value['migrations'].values()]:
        _require(isinstance(pin, str) and re.fullmatch('[0-9a-f]{64}', pin), 'Invalid compatibility pin')
    for version in value['migrations']:
        _name(version)
    rows = value.get('entries'); _require(isinstance(rows, dict) and len(rows) <= MAX_FILES, 'Invalid entry count')
    _require(len(rows) == len({n.casefold() for n in rows}), 'Case-colliding backup entries')
    total = 0
    for name, row in rows.items():
        _name(name)
        _require(not _excluded(name, cache_exclusion_policy=policy) and row.get('kind') in {'file', 'directory'}, 'Invalid private backup member')
        if row['kind'] == 'file':
            _require(type(row.get('bytes')) is int and row['bytes'] >= 0 and re.fullmatch('[0-9a-f]{64}', row.get('sha256', '')), 'Invalid file pin')
            if policy == WORKFLOW_CACHE_EXCLUSION_POLICY:
                expected = _protocols.kernel_binary_pin(name)
                _require(not expected or expected == row['sha256'], 'Managed workflow kernel differs from its content address')
            total += row['bytes']
    _require(type(value.get('bytes')) is int and total == value['bytes'] and total <= MAX_BYTES, 'Invalid backup byte limit')
    actual, actual_bytes = _inventory(root / 'data', exclude=False)
    _require(actual == rows and actual_bytes == total, 'Backup files are missing, changed or unexpected')
    _require(_database(root / 'data') == list(value['migrations']), 'Backup migration records differ')
    required = _protocols.requirements(rows)
    _require('required_state_protocols' not in value or value['required_state_protocols'] == required,
             'Backup state protocol requirements differ from its actual files')
    return value, digest


def _restored_directory_bindings(stage, source_roots):
    """Bind a copied account directory without editing its immutable metadata.

    Explicit same-user restore authorizes this new filesystem identity only;
    credentials stay opaque and no OAuth authority or model access is granted.
    """
    path = stage / 'subscription-account/binding.json'
    if not path.exists():
        return {}
    record = _read(path, 1024 * 1024)
    _require(isinstance(record, dict) and set(record) == {'value', 'sha256'}, 'Invalid saved account binding')
    value = record['value']
    _require(isinstance(value, dict), 'Invalid saved account binding')
    digest = hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(',', ':'), allow_nan=False).encode()).hexdigest()
    _require(record['sha256'] == digest and value.get('schema') == 'eeas.native-subscription-account.v1',
             'Saved account binding integrity differs')
    ident, revision = value.get('id'), value.get('revision')
    _require(isinstance(ident, str) and re.fullmatch('[A-Za-z0-9_-]{1,100}', ident)
             and type(revision) is int and revision > 0, 'Invalid saved account reference')
    identity = value.get('directory_identity')
    _require(isinstance(identity, list) and len(identity) == 2
             and all(type(n) is int and n >= 0 for n in identity), 'Invalid saved account identity')
    saved = value.get('account_data_home')
    relative = 'subscription-account/native-data'
    _require(isinstance(saved, str) and Path(saved).is_absolute()
             and any(Path(saved) == Path(root) / relative for root in source_roots),
             'Saved account directory is outside the restored data roots')
    directory = _no_links(stage / relative)
    _require(directory.is_dir(), 'Saved account directory is missing')
    actual = directory.stat()
    return {relative: {'reference': {'id': ident, 'revision': revision, 'sha256': digest},
        'saved_account_data_home': saved, 'saved_directory_identity': identity,
        'restored_directory_identity': [actual.st_dev, actual.st_ino]}}


def _restored_managed_file_bindings(stage, destination, snapshot_sha256, snapshot_id):
    """Bind copied managed parents while preserving every immutable link record.

    This authority is emitted only by an explicit verified restore. The small
    provenance marker pins a separate bounded index so its existing size and
    account/path contracts do not grow with the number of file associations.
    """
    links = stage / 'file-continuity/links'
    if not links.exists():
        return None
    _require(_no_links(links).is_dir(), 'Invalid saved continuity links directory')
    bindings = {}
    managed = {'REPORT_WORKING_COPY': 'report', 'ARTIFACT_WORKING_COPY': 'artifact',
               'DOCX_REVISION_WORKING_COPY': 'artifact'}
    paths = list(links.iterdir())
    _require(len(paths) <= 1000, 'Too many saved continuity links')
    for path in paths:
        _require(re.fullmatch('[a-f0-9]{64}\\.json', path.name) is not None,
                 'Invalid saved continuity link name')
        wrapped = _read(path, 20 * 1024 * 1024)
        value = wrapped.get('payload')
        _require(isinstance(value, dict), 'Invalid saved continuity link')
        canonical = lambda v: json.dumps(v, ensure_ascii=False, sort_keys=True,
            separators=(',', ':'), allow_nan=False).encode()
        digest = hashlib.sha256(canonical(value)).hexdigest()
        _require(wrapped.get('sha256') == digest and value.get('id') == path.stem
                 and value.get('schema_version') == 'eeas.file-continuity-link.v1'
                 and value.get('revision') == 1, 'Saved continuity link integrity differs')
        if value.get('kind') not in managed:
            continue  # External workspace roots are never rebound by a data restore.
        request = value.get('request')
        _require(isinstance(request, dict) and request.get('kind') == value['kind']
                 and hashlib.sha256(canonical({k: v for k, v in request.items()
                     if k != 'parent_identity'})).hexdigest() == path.stem,
                 'Saved managed link request differs')
        project = value.get('project_id')
        part = request.get(managed[value['kind']], {})
        _require(isinstance(project, str) and re.fullmatch('[A-Za-z0-9_-]{1,100}', project)
                 and request.get('project_id') == project and isinstance(part, dict)
                 and isinstance(part.get('artifact_id'), str)
                 and re.fullmatch('[a-f0-9]{64}', part['artifact_id'])
                 and part.get('kind') in {'docx', 'pending_markdown', 'pending_json',
                     'source_snapshot', 'artifact_pptx', 'docx_revision'},
                 'Invalid saved managed parent scope')
        saved = request.get('parent_identity')
        _require(isinstance(saved, dict) and set(saved) == {'device', 'inode'}
                 and all(type(n) is int and n >= 0 for n in saved.values()),
                 'Invalid saved managed parent identity')
        relative = '/'.join(('native-working-copies', project, part['artifact_id'], part['kind']))
        parent = _no_links(stage / relative)
        if not parent.exists():
            continue  # A missing old working copy stays missing, with history retained.
        _require(parent.is_dir(), 'Saved managed parent is not a directory')
        actual = parent.stat()
        bindings[path.stem] = {'reference': {'project_id': project, 'id': path.stem,
            'revision': 1, 'sha256': digest}, 'relative_parent': relative,
            'saved_parent_identity': saved,
            'restored_parent_identity': {'device': actual.st_dev, 'inode': actual.st_ino}}
    if not bindings:
        return None
    relative = 'file-continuity/restored-bindings/' + snapshot_id + '.json'
    index = stage / relative
    index.parent.mkdir(parents=True, exist_ok=True)
    _no_links(index.parent)
    _write(index, {'schema_version': 'eeas.restored-managed-file-bindings.v1',
        'snapshot_id': snapshot_id, 'snapshot_sha256': snapshot_sha256,
        'target_data_dir': str(destination), 'bindings': bindings})
    _require(index.stat().st_size <= 2 * 1024 * 1024, 'Restored managed bindings exceed their limit')
    return {'path': relative, 'bytes': index.stat().st_size, 'sha256': _hash(index)}


def _restore_plan(backup_directory, installation, expected_sha256=None, expected_program_sha256=None):
    root = _local(installation)
    manifest_sha256 = _hash(root / 'PACKAGE_MANIFEST.json')
    _require(expected_program_sha256 is None or manifest_sha256 == expected_program_sha256,
             'Target program differs from the explicitly checked version')
    root, package = _installation(root); _stopped(root)
    _require(_hash(root / 'PACKAGE_MANIFEST.json') == manifest_sha256, 'Target program changed during compatibility check')
    destination = _local(root / 'user-data')
    _require(not os.path.lexists(destination), 'Restore requires a fresh installation with no user-data directory')
    value, digest = verify(backup_directory, expected_sha256)
    _require(all(package.get(n, {}).get('sha256') == pin for n, pin in value['compatibility'].items())
             and set(value['compatibility']) == set(COMPATIBILITY), 'Runtime compatibility differs')
    for version, pin in value['migrations'].items():
        _name(version)
        _require(package.get('packages/domain/migrations/' + version, {}).get('sha256') == pin,
                 'Target cannot interpret the saved migration; no reverse migration is performed')
    required = _protocols.requirements(value['entries'])
    _check_state_protocols(root, required)
    _require(_hash(root / 'PACKAGE_MANIFEST.json') == manifest_sha256, 'Target program changed during compatibility check')
    return root, value, digest, manifest_sha256, required


def _check_state_protocols(root, required):
    try:
        _protocols.check_target(_read(root / 'PACKAGE_MANIFEST.json'), required,
                               lambda name: _hash(root / _name(name)))
    except _protocols.StateProtocolError as error:
        raise RecoveryError(str(error)) from error


def check_target(backup_directory, installation, expected_sha256=None, *, expected_program_sha256=None):
    root, value, digest, program_sha, required = _restore_plan(
        backup_directory, installation, expected_sha256, expected_program_sha256)
    return {'status': 'RESTORE_TARGET_COMPATIBLE', 'path': str(root), 'manifest_sha256': digest,
            'program_manifest_sha256': program_sha, 'required_state_protocols': required,
            'user_data_written': False, 'model_or_agent_started': False,
            'application_start_validation': 'NOT_PERFORMED'}


def restore(backup_directory, installation, expected_sha256=None, *, expected_program_sha256=None):
    root, value, digest, program_sha, required = _restore_plan(
        backup_directory, installation, expected_sha256, expected_program_sha256)
    destination = _local(root / 'user-data')
    _require(shutil.disk_usage(root).free >= value['bytes'] + RESERVE_BYTES, 'Insufficient free space for restoration')
    stage = root / ('user-data.partial-' + uuid.uuid4().hex); _private(stage)
    _copy(Path(backup_directory) / 'data', stage, value['entries'])
    if value.get('cache_exclusion_policy') == WORKFLOW_CACHE_EXCLUSION_POLICY:
        for name, row in value['entries'].items():
            if row['kind'] == 'file':
                _protocols.restore_kernel_mode(stage / name, name, row['sha256'])
    _require(_database(stage) == list(value['migrations']), 'Restored database differs')
    # Immutable historical paths map into this restored data root, never back into a live installation.
    previous = _read(stage / 'restore-provenance.json') if (stage / 'restore-provenance.json').exists() else None
    roots = [value['source_data']]
    if previous:
        _require(previous.get('schema_version') == 'eeas.restore-provenance.v1', 'Invalid old path provenance')
        roots.extend(previous['allowed_source_data_dirs'])
    roots = list(dict.fromkeys(roots)); _require(len(roots) <= 50 and all(isinstance(p, str) and p and '\x00' not in p for p in roots), 'Invalid historical data roots')
    bindings = _restored_directory_bindings(stage, roots)
    if previous:
        (stage / 'restore-provenance.json').unlink()
    snapshot_id = uuid.uuid4().hex
    managed = _restored_managed_file_bindings(stage, destination, digest, snapshot_id)
    provenance = {'schema_version': 'eeas.restore-provenance.v1',
        'allowed_source_data_dirs': roots, 'snapshot_sha256': digest, 'snapshot_id': snapshot_id,
        'manifest_sha256': digest, 'target_data_dir': str(destination),
        'directory_bindings': bindings, 'renderer_source_origin': value.get('renderer_origin')}
    if managed is not None:
        provenance['managed_file_bindings_ref'] = managed
    _write(stage / 'restore-provenance.json', provenance)
    _require(_inventory(Path(backup_directory) / 'data', exclude=False)[0] == value['entries'], 'Backup changed during restore')
    _stopped(root)
    _require(_hash(root / 'PACKAGE_MANIFEST.json') == program_sha, 'Target program changed before restore publication')
    _check_state_protocols(root, required)
    _publish(stage, destination)
    return {'status': 'RESTORE_COMPLETE', 'path': str(destination), 'manifest_sha256': digest,
        'program_manifest_sha256': program_sha, 'required_state_protocols': required,
        'files': sum(r['kind'] == 'file' for r in value['entries'].values()), 'bytes': value['bytes'],
        'model_or_agent_started': False, 'credentials': 'SAME_OS_USER_ONLY_NOT_REAUTHORIZED',
        'unknown_outcomes': 'UNCHANGED_NOT_REPLAYED', 'application_start_validation': 'NOT_PERFORMED'}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Cirven 本机私有状态备份与恢复（先从托盘正常退出 App）。')
    parser.add_argument('action', choices=['backup', 'verify', 'restore', 'check-target'])
    parser.add_argument('--installation', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--backup', type=Path, required=True, help='新的私有备份目录，或已有备份目录')
    parser.add_argument('--expected-sha256')
    parser.add_argument('--expected-program-sha256')
    parser.add_argument('--json', action='store_true', help='输出简要自动化回执，不含文件内容')
    args = parser.parse_args(argv)
    if args.expected_program_sha256 is not None and args.action not in {'restore', 'check-target'}:
        parser.error('--expected-program-sha256 is only valid for restore or check-target')
    try:
        # The bundled interpreter starts stdlib-only. Verify before importing its runtime dependencies.
        path = args.installation / 'scripts/native_package_bootstrap.py'
        spec = importlib.util.spec_from_file_location('recovery_package_boot', path)
        boot = importlib.util.module_from_spec(spec); spec.loader.exec_module(boot)
        manifest = boot.verify_package(args.installation)
        boot.activate_python(args.installation, manifest)
        if args.action == 'backup':
            result = backup(args.installation, args.backup)
        elif args.action == 'restore':
            result = restore(args.backup, args.installation, args.expected_sha256,
                             expected_program_sha256=args.expected_program_sha256)
        elif args.action == 'check-target':
            result = check_target(args.backup, args.installation, args.expected_sha256,
                                  expected_program_sha256=args.expected_program_sha256)
        else:
            value, digest = verify(args.backup, args.expected_sha256)
            result = {'status': 'BACKUP_VERIFIED', 'manifest_sha256': digest, 'bytes': value['bytes'],
                'files': sum(r['kind'] == 'file' for r in value['entries'].values())}
        if args.json:
            print(json.dumps(result, ensure_ascii=True))
        else:
            print({'BACKUP_COMPLETE': '备份完成', 'RESTORE_COMPLETE': '恢复完成', 'BACKUP_VERIFIED': '备份校验通过',
                   'RESTORE_TARGET_COMPATIBLE': '程序与备份兼容，尚未恢复或启动'}[result['status']])
            print(result.get('path', str(args.backup)))
            print('未启动模型或重发原任务。恢复后首次启动的业务状态仍需检查。')
        return 0
    except (OSError, ValueError, KeyError, sqlite3.Error) as error:
        print('未完成：' + str(error), file=sys.stderr)
        print('原安装和已有备份不覆盖；如有 .partial 目录请保留供检查。', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
