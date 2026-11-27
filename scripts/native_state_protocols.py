"""Versioned durable negative-authority formats for program/data compatibility.

Declarations bind actual packaged readers. They are file-format support facts,
not native qualification, permission grants, execution or migration instructions.
"""
import os
import re

SCHEMA = 'cirven.durable-state-protocols.v1'
SOURCE = 'native-experience-source.v1'
PAUSE = 'native-experience-pause.v1'
READERS = {
    SOURCE: ('packages/domain/experience_provenance.py',
             'packages/domain/native_capabilities.py',
             'packages/agent_sessions/experience_source_policy.py',
             'packages/agent_sessions/experience_activation.py'),
    PAUSE: ('packages/domain/experience_controls.py',
            'packages/domain/native_capabilities.py',
            'packages/agent_sessions/experience_pending.py'),
}


class StateProtocolError(ValueError):
    pass


def require(value, message):
    if not value:
        raise StateProtocolError(message)


def requirements(entries):
    """Derive requirements from actual snapshot members, including old backups.

    Empty folders and similarly named project documents do not grant or require
    a protocol. The recovery inventory already validates paths and row kinds.
    """
    found = set()
    for name, row in entries.items():
        if row.get('kind') != 'file':
            continue
        key = name.casefold()
        if key.startswith('native-capabilities/experience-provenance/'):
            found.add(SOURCE)
        if key.startswith('native-capabilities/experience-controls/'):
            found.update((SOURCE, PAUSE))
        if re.match(r'^agent-sessions/[^/]+/[^/]+/experience-candidates/(source-policy|source-authority)/', key):
            found.add(SOURCE)
    return sorted(found)


def metadata(files):
    """A packager declares each present format only with its complete readers."""
    indexed = {row['path']: row for row in files}
    protocols = {}
    for protocol, names in READERS.items():
        if names[0] not in indexed:
            continue
        require(all(name in indexed for name in names),
                'Incomplete packaged state protocol readers: ' + protocol)
        pins = {}
        for name in names:
            checksum = indexed[name].get('sha256')
            require(isinstance(checksum, str) and re.fullmatch('[a-f0-9]{64}', checksum),
                    'Invalid packaged state protocol pin: ' + name)
            pins[name] = checksum
        protocols[protocol] = {'readers': pins}
    return {'schema': SCHEMA, 'protocols': protocols}


def check_target(manifest, required, hash_reader):
    """Fail before publication if the target cannot enforce saved authority."""
    if not required:
        return
    declared = manifest.get('durable_state_protocols')
    require(isinstance(declared, dict) and set(declared) == {'schema', 'protocols'}
            and declared.get('schema') == SCHEMA and isinstance(declared.get('protocols'), dict),
            'Target program has no supported state protocol declaration')
    indexed = {row['path']: row for row in manifest['files']}
    for protocol in required:
        require(protocol in READERS, 'Unknown required state protocol: ' + protocol)
        item = declared['protocols'].get(protocol)
        require(isinstance(item, dict) and set(item) == {'readers'}
                and isinstance(item.get('readers'), dict)
                and set(item['readers']) == set(READERS[protocol]),
                'Target program does not support required state protocol: ' + protocol)
        for name, checksum in item['readers'].items():
            require(isinstance(checksum, str) and re.fullmatch('[a-f0-9]{64}', checksum)
                    and indexed.get(name, {}).get('sha256') == checksum,
                    'Target state protocol is not bound to its program manifest: ' + name)
            require(hash_reader(name) == checksum,
                    'Target state protocol reader bytes differ: ' + name)


# A workflow session's temporary HOME can contain provider credentials and
# scratch files. Only these kernel settings, ledger, and pinned binaries are
# durable across restarts. Paths are relative to the application data root.
KERNEL_DATABASES = ('workflow-kernels/settings.sqlite3', 'workflow-kernels/artifacts.sqlite3')
_KERNEL_VERSION = re.compile(r'workflow-kernels/versions/(opencode|claude-code)/([a-f0-9]{64})/(kernel(?:\.exe)?)')
_KERNEL_DIRECTORY = re.compile(r'workflow-kernels(?:/versions(?:/(?:opencode|claude-code)(?:/[a-f0-9]{64})?)?)?')


def kernel_binary_pin(name):
    match = _KERNEL_VERSION.fullmatch(name)
    return match.group(2) if match else None


def kernel_private_member(name):
    if _KERNEL_DIRECTORY.fullmatch(name) or kernel_binary_pin(name):
        return True
    return any(name == database + suffix for database in KERNEL_DATABASES for suffix in ('', '-wal', '-journal'))


def restore_kernel_mode(path, name, sha256):
    expected = kernel_binary_pin(name)
    if expected:
        require(sha256 == expected, 'Managed workflow kernel differs from its content address')
        if os.name != 'nt' and name.endswith('/kernel'):
            path.chmod(0o700)
