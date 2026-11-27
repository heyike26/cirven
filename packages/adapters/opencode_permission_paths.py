"""Exact v1.19 tool/worktree permission paths, without native-request rewriting.

The pure mapper can be checked for Windows paths on another OS. It does not
prove filesystem identity: callers must use the selected workspace's live
identity/handles, and writes still require the original before-ledger guard.
"""
import ntpath
import os
from pathlib import Path
import posixpath
import re
import stat

from packages.domain.local_workspaces import relative
from packages.domain.store import InvalidInput


def _literal(value, *, empty=False):
    if (not isinstance(value, str) or not value and not empty or len(value) > 4096 or
            any(ord(c) < 32 or ord(c) == 127 or c in '*?[]' for c in value)):
        raise ValueError('A bounded literal native path is required')
    return value


def _absolute(value, windows):
    _literal(value)
    normalized = value.replace('\\', '/') if windows else value
    if windows:
        if not re.match(r'^[A-Za-z]:/', normalized):
            raise ValueError('Only an explicit local drive-absolute path is supported')
        tail = normalized[3:]
    else:
        if not normalized.startswith('/') or normalized.startswith('//') or '\\' in normalized:
            raise ValueError('An explicit POSIX absolute path is required')
        tail = normalized[1:]
    parts = tail.split('/') if tail else []
    if any(not p or p in {'.', '..'} or windows and (p != p.rstrip(' .') or
            any(c in p for c in ':<>"|') or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?', p, re.I))
            for p in parts):
        raise ValueError('Native path aliases and traversal are outside the scope')
    return ntpath.normpath(value) if windows else posixpath.normpath(value)


def _inside(parent, child, module):
    try:
        return module.normcase(module.commonpath([parent, child])) == module.normcase(parent)
    except ValueError:
        return False


def _location(workspace_root, location):
    windows = bool(re.match(r'^[A-Za-z]:', workspace_root)) if isinstance(workspace_root, str) else False
    module = ntpath if windows else posixpath
    root = _absolute(workspace_root, windows)
    cwd = _absolute(location.get('cwd'), windows)
    if module.normcase(cwd) != module.normcase(root):
        raise ValueError('The native cwd differs from the selected workspace')
    value = location.get('root')
    worktree = module.splitdrive(cwd)[0] + '\\' if windows and value == '/' else _absolute(value, windows)
    if not _inside(worktree, cwd, module):
        raise ValueError('The observed worktree is not an ancestor of the bound cwd')
    return windows, module, root, cwd, worktree


def native_write_policy(workspace, native_location):
    """Translate existing grants only after the original /path observation.

    No new grant is created. An explicit whole-workspace '*' retains its old
    meaning; when worktree is wider, even that ask is restricted by its prefix.
    """
    from packages.adapters.opencode_change_review import write_patterns
    original = write_patterns(workspace)
    windows, module, root, _cwd, worktree = _location(workspace['root'], {
        'cwd': native_location.get('directory'), 'root': native_location.get('worktree')})
    prefix = _pattern(root, worktree, module)
    if prefix:
        relative(prefix)
    policy = {'*': 'deny'}
    for pattern, action in original.items():
        if action == 'ask':
            policy[(prefix+'/' if prefix else '')+pattern] = 'ask'
    for pattern, action in original.items():
        if action == 'deny' and pattern != '*':
            policy[pattern] = 'deny'
    return policy


def _target(value, cwd, root, module, windows, *, directory=False):
    raw = _literal(value)
    if module.isabs(raw):
        target = _absolute(raw, windows)
    else:
        local = raw.replace('\\', '/') if windows else raw
        if local == '.' and directory:
            local = ''
        elif local.startswith('./'):
            local = local[2:]
        relative(local, empty=directory)
        target = _absolute(module.join(cwd, *local.split('/')) if local else cwd, windows)
    if not _inside(root, target, module):
        raise ValueError('The original tool target is outside the selected workspace')
    member = module.relpath(target, root).replace('\\', '/')
    member = '' if member == '.' else member
    relative(member, empty=directory)
    return target, member


def _pattern(target, worktree, module):
    value = module.relpath(target, worktree).replace('\\', '/')
    return '' if value == '.' else value


def _patterns(native, expected, module, windows):
    patterns = native.get('patterns')
    if not isinstance(patterns, list) or len(patterns) != len(expected):
        raise ValueError('Original literal permission targets differ')
    for raw, wanted in zip(patterns, expected):
        value = _literal(raw, empty=not wanted)
        if not windows and '\\' in value:
            raise ValueError('A POSIX pattern cannot use a Windows separator')
        value = value.replace('\\', '/') if windows else value
        relative(value, empty=not wanted)
        if module.normcase(value) != module.normcase(wanted):
            raise ValueError('The literal permission does not match the original worktree/input')


def _patch_targets(native, args, cwd, root, worktree, module, windows):
    # This is the already-supported observer subset of the fixed patch grammar.
    # The original before-ledger separately binds raw args SHA and every side.
    patch = args.get('patchText')
    if (set(args) != {'patchText'} or not isinstance(patch, str) or
            len(patch.encode('utf-8')) > 262144):
        raise ValueError('Bounded original patch input is required')
    lines = patch.replace('\r\n', '\n').split('\n')
    if lines and lines[-1] == '':
        lines.pop()
    if len(lines) < 3 or lines.pop(0) != '*** Begin Patch' or lines.pop() != '*** End Patch':
        raise ValueError('The original patch envelope differs')
    changes = []
    body_started = False
    for line in lines:
        match = re.fullmatch(r'\*\*\* (Add|Update|Delete) File: (.+)', line)
        if match:
            target, member = _target(match[2], cwd, root, module, windows)
            changes.append({'type': match[1].lower(), 'target': target, 'member': member})
            body_started = False
        elif line.startswith('*** Move to: '):
            if not changes or changes[-1]['type'] != 'update' or body_started or 'move' in changes[-1]:
                raise ValueError('The original patch move declaration differs')
            target, member = _target(line[13:], cwd, root, module, windows)
            changes[-1].update(type='move', move=target, moved_member=member)
        elif not changes or line.startswith('*** ') and line != '*** End of File':
            raise ValueError('Unsupported original patch syntax')
        else:
            body_started = True
    metadata = native.get('metadata', {})
    files = metadata.get('files')
    if not 1 <= len(changes) <= 20 or not isinstance(files, list) or len(files) != len(changes):
        raise ValueError('Original patch targets require exact bounded file metadata')
    paths, expected = [], []
    for change, item in zip(changes, files):
        if not isinstance(item, dict) or item.get('type') != change['type']:
            raise ValueError('Original patch file types differ')
        if module.normcase(_absolute(item.get('filePath'), windows)) != module.normcase(change['target']):
            raise ValueError('Original patch source differs')
        if change['type'] == 'move':
            if module.normcase(_absolute(item.get('movePath'), windows)) != module.normcase(change['move']):
                raise ValueError('Original patch destination differs')
        elif item.get('movePath') is not None:
            raise ValueError('Unexpected original patch destination')
        displayed = _pattern(change.get('move', change['target']), worktree, module)
        if item.get('relativePath') != displayed:
            raise ValueError('Original patch display path differs')
        expected.append(_pattern(change['target'], worktree, module))
        paths.extend([change['member']] + ([change['moved_member']] if 'move' in change else []))
    if len(paths) > 20 or len({module.normcase(p) for p in paths}) != len(paths):
        raise ValueError('Patch targets must be distinct within the ledger capacity')
    _patterns(native, expected, module, windows)
    # This upstream field is a joined worktree-relative display summary, not an
    # absolute filepath. Keep it exact instead of feeding it to a path resolver.
    if metadata.get('filepath') != ', '.join(expected):
        raise ValueError('Original patch summary differs')
    return paths


def permission_paths(native, message, workspace_root):
    """Map exact original read/edit/write/patch targets to workspace members.

    v1.19 emits path.relative(instance.worktree, target), not a path relative to
    cwd. For Windows global/non-Git root '/', only the already-bound cwd's
    explicit drive supplies that root. Never infer it from an untrusted target.
    """
    if not isinstance(native, dict) or not isinstance(message, dict):
        raise ValueError('An exact original permission and message are required')
    info, tool = message.get('info', {}), native.get('tool', {})
    session, message_id, call_id = native.get('sessionID'), tool.get('messageID'), tool.get('callID')
    if (not isinstance(session, str) or not re.fullmatch(r'ses_[A-Za-z0-9_-]{1,100}', session) or
            not isinstance(message_id, str) or not re.fullmatch(r'msg_[A-Za-z0-9_-]{1,100}', message_id) or
            not isinstance(call_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', call_id) or
            info.get('id') != message_id or info.get('role') != 'assistant' or info.get('sessionID') != session):
        raise ValueError('The original native tool identity differs')
    parts = message.get('parts')
    if not isinstance(parts, list) or len(parts) > 1024:
        raise ValueError('Bounded original parts are required')
    matches = [p for p in parts if isinstance(p, dict) and p.get('type') == 'tool' and p.get('callID') == call_id]
    if len(matches) != 1:
        raise ValueError('The original call must be unique')
    part = matches[0]
    expected = {'read': {'read'}, 'edit': {'edit', 'write', 'apply_patch'},
                'glob': {'glob'}, 'grep': {'grep'}}.get(native.get('permission'), set())
    state = part.get('state', {})
    if (part.get('tool') not in expected or part.get('sessionID') != session or
            part.get('messageID') != message_id or state.get('status') not in {'pending', 'running'}):
        raise ValueError('This pending path permission has no exact supported tool')
    windows, module, root, cwd, worktree = _location(workspace_root, info.get('path', {}))
    args = state.get('input')
    if not isinstance(args, dict):
        raise ValueError('Original tool input is required')
    if part['tool'] in {'glob', 'grep'}:
        query = args.get('pattern')
        metadata = native.get('metadata', {})
        if (not isinstance(query, str) or not query or len(query) > 4096 or
                any(ord(c) < 32 or ord(c) == 127 for c in query) or
                native.get('patterns') != [query] or metadata.get('pattern') != query or
                set(args) - ({'pattern', 'path', 'include'} if part['tool'] == 'grep' else {'pattern', 'path'}) or
                metadata.get('path') != args.get('path') or metadata.get('include') != args.get('include') or
                'path' in args and not isinstance(args['path'], str) or
                'include' in args and (not isinstance(args['include'], str) or len(args['include']) > 4096 or
                    any(ord(c) < 32 or ord(c) == 127 for c in args['include']))):
            raise ValueError('Original search query/metadata and directory input differ')
        # Query syntax is not a path. The selected search root is resolved by
        # the same exact cwd evidence and checked as an ordinary directory/file.
        _absolute_target, member = _target(args.get('path', '.'), cwd, root, module, windows, directory=True)
        return [member]
    if part['tool'] == 'apply_patch':
        return _patch_targets(native, args, cwd, root, worktree, module, windows)
    target, member = _target(args.get('filePath'), cwd, root, module, windows, directory=part['tool'] == 'read')
    _patterns(native, [_pattern(target, worktree, module)], module, windows)
    if part['tool'] in {'edit', 'write'} and native.get('metadata', {}).get('filepath') is not None:
        metadata_path = _absolute(native['metadata']['filepath'], windows)
        if module.normcase(metadata_path) != module.normcase(target):
            raise ValueError('Original write metadata and tool input differ')
    return [member]


def check_read_target(workspaces, binding, path):
    """Metadata only through the existing pinned parent; never read file bytes."""
    native = workspaces._native_handles()
    if not path:
        with workspaces._parent(binding, '', directory=True):
            return 'directory'
    with workspaces._parent(binding, path) as (parent, name):
        if native is None:
            info = os.stat(name, dir_fd=parent, follow_symlinks=False)
            if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode) and info.st_nlink == 1):
                raise InvalidInput('Read target must be an ordinary file or directory')
            return 'directory' if stat.S_ISDIR(info.st_mode) else 'file'
        else:
            from packages.adapters.windows_workspace_handles import FILE_READ_ATTRIBUTES, FILE_SHARE_READ, FILE_SHARE_WRITE
            info = (Path(binding['root']) / path).lstat()
            directory = stat.S_ISDIR(info.st_mode)
            if stat.S_ISLNK(info.st_mode) or not (directory or stat.S_ISREG(info.st_mode)):
                raise InvalidInput('Read target must be ordinary')
            handle = native.api.open_relative(parent.native_handle, name, FILE_READ_ATTRIBUTES,
                FILE_SHARE_READ | FILE_SHARE_WRITE, directory=directory)
            try:
                native._check(handle, ntpath.join(parent.path, name), directory=directory)
            finally:
                native.api.close(handle)
            return 'directory' if directory else 'file'


def check_search_target(workspaces, binding, path, tool):
    kind = check_read_target(workspaces, binding, path)
    if kind == 'file':
        if tool != 'grep':
            raise InvalidInput('The original glob tool requires a directory')
        # Fixed v1.19 grep(file) actually passes dirname(file) to ripgrep.
        # Validate that effective search directory too, rather than suggesting
        # a single-file authorization which the original tool does not enforce.
        check_read_target(workspaces, binding, posixpath.dirname(path))
