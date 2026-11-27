"""Bounded historical original-session patch views, never disk/buffer comparisons.

The pinned original FileDiff stores a unified patch, not full before/after
files. Even a patch spanning a small file is not promoted to a byte-exact file:
the original edit tool's trimDiff may normalize indentation. All text here is
explicitly a historical patch excerpt. No filesystem or model calls occur.
"""
from __future__ import annotations

from copy import deepcopy
import ntpath
import posixpath
import re

from packages.domain.local_workspaces import relative
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest

MAX_FILES = 128
MAX_PATCH_BYTES = 131072
MAX_HUNKS = 128
MAX_CONTENT_BYTES = 524288
_ID = re.compile(r"[A-Za-z0-9_-]{1,200}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_HUNK = re.compile(r"@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(?:[^\r\n]*)\r?\n?\Z")


def _id(value):
    return isinstance(value, str) and bool(_ID.fullmatch(value))


def _path(value, root):
    """Only a literal historical path in this registered workspace namespace.

    This does not resolve a current leaf, follow links, or confer write rights.
    Native Windows paths are accepted only with a Windows workspace root; UNC,
    WSL URIs, commands, traversal and private application paths are excluded.
    """
    if (not isinstance(value, str) or not value or len(value) > 4096 or
            any(ord(c) < 32 or c == '\x7f' for c in value) or
            value.startswith(('\\\\', '//')) or '://' in value):
        raise InvalidInput('Historical diff path is outside the selected workspace')
    windows = bool(re.match(r'^[A-Za-z]:[\\/]', root))
    flavor = ntpath if windows else posixpath
    if windows:
        if value.startswith(('/', '\\')) or (':' in value and not re.match(r'^[A-Za-z]:[\\/]', value)):
            raise InvalidInput('Historical diff path is not a local workspace path')
        normalized = value.replace('\\', '/')
    else:
        if '\\' in value or ':' in value:
            raise InvalidInput('Historical diff path is not a local workspace path')
        normalized = value
    # Do not normalize traversal away, including inside an absolute tool path.
    if any(part in {'.', '..'} for part in normalized.split('/')):
        raise InvalidInput('Historical diff path contains traversal')
    if flavor.isabs(value):
        try:
            if flavor.normcase(flavor.commonpath([root, value])) != flavor.normcase(flavor.normpath(root)):
                raise ValueError()
            value = flavor.relpath(value, root)
        except ValueError:
            raise InvalidInput('Historical diff path is outside the selected workspace') from None
    value = value.replace('\\', '/')
    return relative(value)


def parse_hunks(patch):
    """Validate original unified hunk counts; do not apply or guess missing text."""
    lines = patch.splitlines(keepends=True)
    hunks, cursor = [], 0
    while cursor < len(lines) and not lines[cursor].startswith('@@ '):
        if not lines[cursor].startswith(('Index: ', '===', '--- ', '+++ ', 'diff ', 'index ', 'new file ', 'deleted file ')):
            raise ValueError('Unsupported patch preamble')
        cursor += 1
    while cursor < len(lines):
        match = _HUNK.fullmatch(lines[cursor])
        if not match or len(hunks) >= MAX_HUNKS:
            raise ValueError('Invalid or excessive unified hunks')
        old_start, old_count, new_start, new_count = (
            int(match[1]), int(match[2]) if match[2] is not None else 1,
            int(match[3]), int(match[4]) if match[4] is not None else 1)
        if max(old_start, old_count, new_start, new_count) > 10000000:
            raise ValueError('Excessive hunk coordinates')
        cursor += 1
        before, after, old_seen, new_seen, last = [], [], 0, 0, None
        while cursor < len(lines) and not lines[cursor].startswith('@@ '):
            line = lines[cursor]
            if line.rstrip('\r\n') == '\\ No newline at end of file':
                if last is None:
                    raise ValueError('Orphan newline marker')
                if last in {' ', '-'} and before:
                    before[-1] = before[-1].rstrip('\r\n')
                if last in {' ', '+'} and after:
                    after[-1] = after[-1].rstrip('\r\n')
                last = None
            elif line and line[0] in ' +-':
                last = line[0]
                if last in {' ', '-'}:
                    before.append(line[1:]); old_seen += 1
                if last in {' ', '+'}:
                    after.append(line[1:]); new_seen += 1
            else:
                raise ValueError('Invalid unified patch line')
            cursor += 1
        if (old_seen, new_seen) != (old_count, new_count):
            raise ValueError('Truncated or inconsistent unified patch')
        hunks.append({'old_start': old_start, 'old_lines': old_count,
                      'new_start': new_start, 'new_lines': new_count,
                      'before': ''.join(before), 'after': ''.join(after)})
    if not hunks:
        raise ValueError('No textual unified hunks')
    return hunks


def _content(patch):
    if not isinstance(patch, str) or not patch:
        return 'UNAVAILABLE', 'NO_HISTORICAL_PATCH', None
    if len(patch.encode('utf-8')) > MAX_PATCH_BYTES:
        return 'TOO_LARGE', 'PATCH_BYTE_LIMIT', None
    if '\x00' in patch or patch.startswith(('GIT binary patch', 'Binary files ')):
        return 'BINARY', 'BINARY_PATCH_NOT_TEXT', None
    if '[REDACTED_RUNTIME_CREDENTIAL]' in patch:
        return 'UNAVAILABLE', 'REDACTED_PATCH_NOT_ORIGINAL_TEXT', None
    try:
        hunks = parse_hunks(patch)
    except ValueError:
        return 'UNAVAILABLE', 'UNSUPPORTED_OR_INCOMPLETE_PATCH', None
    def compose(side):
        return '\n\n'.join(
            '@@ Historical ' + side + ' lines ' + str(h['old_start' if side == 'before' else 'new_start']) +
            ',' + str(h['old_lines' if side == 'before' else 'new_lines']) + ' @@\n' + h[side]
            for h in hunks)
    detail = {'composition': 'LOCATION_MARKED_HUNK_EXCERPTS', 'before': compose('before'),
              'after': compose('after'), 'patch': patch, 'hunks': hunks}
    if len(canonical(detail).encode()) > MAX_CONTENT_BYTES - 8192:
        return 'TOO_LARGE', 'CONTENT_BYTE_LIMIT', None
    return 'AVAILABLE', None, detail


def project_diffs(observed, binding, *, path_checker=None, full_entries=(), full_reason_codes=()):
    """Project one checked native source from the original root or child owner."""
    scope = {name: deepcopy(observed[name]) for name in ('project_id', 'session_ref', 'generation', 'workspace_ref')}
    native = observed.get('original_session_id')
    messages = observed.get('messages')
    if not isinstance(messages, list) or (not _id(native) and messages):
        raise Conflict('Original diff observation has no exact root messages')
    files, content, seen, reasons = [], {}, {}, set()
    reasons.update(full_reason_codes)
    if not _id(native):
        reasons.add('ORIGINAL_SESSION_NOT_OBSERVED')
    available_sources = 0
    out_of_scope = 0
    full_calls = set()
    for item in full_entries[:MAX_FILES]:
        try:
            path = relative(item['path'])
            if path_checker:
                path_checker(binding, path)
            evidence = {key: item[key] for key in ['message_id', 'part_id', 'call_id']}
            if not all(_id(value) for value in evidence.values()):
                raise ValueError()
            if (evidence['call_id'], path) in full_calls:
                continue
            payload = {**scope, 'path': path, 'source': 'AUTHORIZED_IMMUTABLE_FILE_SNAPSHOTS',
                       'evidence': evidence, 'record_sha256': item['record_sha256'],
                       'before_sha256': item['before_sha256'], 'after_sha256': item['after_sha256'],
                       'source_generation':item['source_generation'], 'source_scope':item['source_scope']}
            child_evidence = ({'source_native_session_id':item['source_native_session_id'],
                'child_lineage_ref':deepcopy(item['child_lineage_ref'])} if item.get('child_lineage_ref') else {})
            payload.update(child_evidence)
            reference = {'id': digest({'source': payload['source'], 'evidence': evidence, 'path': path}),
                         'revision': 1, 'sha256': digest(payload)}
            row = {'diff_ref': reference, 'path': path, 'status': item['status'], 'source': payload['source'],
                   **evidence, 'additions': None, 'deletions': None, 'content_scope': 'FULL_FILE',
                   'availability': 'AVAILABLE', 'reason_code': None,
                   'source_generation':item['source_generation'], 'source_scope':deepcopy(item['source_scope'])}
            row.update(child_evidence)
            if item['before_bytes'] + item['after_bytes'] > MAX_CONTENT_BYTES - 8192:
                row.update(availability='TOO_LARGE', content_scope='UNAVAILABLE', reason_code='CONTENT_BYTE_LIMIT')
            files.append(row); available_sources += 1
            full_calls.add((evidence['call_id'], path))
            if row['availability'] == 'AVAILABLE':
                content[reference['id']] = {**row, '_load': item['load_content']}
        except (KeyError, ValueError, TypeError, InvalidInput):
            reasons.add('FULL_FILE_RECORD_UNAVAILABLE')

    def add(raw, source, evidence):
        nonlocal available_sources, out_of_scope
        available_sources += 1
        if not isinstance(raw, dict):
            reasons.add('INVALID_ORIGINAL_FILEDIFF'); return
        try:
            path = _path(raw.get('file'), binding['root'])
            # Reuse the workspace authority's private-root boundary, without
            # reading any present file or claiming historical physical identity.
            if path_checker is not None:
                path_checker(binding, path)
        except (InvalidInput, ValueError, TypeError):
            out_of_scope += 1; reasons.add('PATH_OUTSIDE_WORKSPACE'); return
        identity = digest({'source': source, 'evidence': evidence, 'path': path})
        original = {k: raw.get(k) for k in ('file', 'patch', 'additions', 'deletions', 'status')}
        original_sha = digest(original)
        if identity in seen:
            if seen[identity] != original_sha:
                raise Conflict('Original diff evidence collided with different historical bytes')
            return
        seen[identity] = original_sha
        if len(files) >= MAX_FILES:
            reasons.add('FILE_COUNT_LIMIT'); return
        availability, reason, detail = _content(raw.get('patch'))
        additions, deletions = raw.get('additions'), raw.get('deletions')
        if any(type(v) is not int or v < 0 or v > 10000000 for v in (additions, deletions)):
            additions = deletions = None
        status = raw.get('status') if raw.get('status') in {'added', 'deleted', 'modified'} else 'UNKNOWN'
        payload = {**scope, 'path': path, 'source': source, 'evidence': evidence,
                   'original_filediff_sha256': original_sha}
        reference = {'id': identity, 'revision': 1, 'sha256': digest(payload)}
        row = {'diff_ref': reference, 'path': path, 'status': status, 'source': source,
               **evidence, 'additions': additions, 'deletions': deletions,
               'content_scope': 'HUNK_EXCERPTS' if detail else 'UNAVAILABLE',
               'availability': availability, 'reason_code': reason}
        files.append(row)
        if reason:
            reasons.add(reason)
        if detail:
            # Keep the already-owned raw patch reference, not three expanded
            # copies of every file. Materialize only the explicitly opened one.
            content[identity] = {**row, '_load': lambda patch=raw['patch']: _content(patch)[2]}

    for message in messages[-200:]:
        if not isinstance(message, dict):
            reasons.add('INVALID_MESSAGE'); continue
        info = message.get('info', {})
        if not isinstance(info, dict):
            reasons.add('INVALID_MESSAGE'); continue
        if info.get('sessionID') != native or not _id(info.get('id')):
            reasons.add('FOREIGN_MESSAGE_EXCLUDED'); continue
        if info.get('role') == 'user':
            summary = info.get('summary', {})
            diffs = summary.get('diffs', []) if isinstance(summary, dict) else []
            if isinstance(diffs, list):
                for index, raw in enumerate(diffs[:1024]):
                    add(raw, 'ORIGINAL_MESSAGE_SUMMARY', {'message_id': info['id'], 'summary_index': index})
                if len(diffs) > 1024:
                    reasons.add('SOURCE_DIFF_COUNT_LIMIT')
        if info.get('role') != 'assistant':
            continue
        parts = message.get('parts', [])
        if not isinstance(parts, list):
            reasons.add('INVALID_MESSAGE_PARTS'); continue
        if len(parts) > 1024:
            reasons.add('SOURCE_PART_COUNT_LIMIT')
        for part in parts[:1024]:
            if (not isinstance(part, dict) or part.get('type') != 'tool' or
                    part.get('tool') not in {'edit', 'write'}):
                continue
            if (part.get('sessionID') != native or part.get('messageID') != info['id'] or
                    not _id(part.get('id')) or not _id(part.get('callID'))):
                reasons.add('FOREIGN_TOOL_EXCLUDED'); continue
            state = part.get('state', {})
            if not isinstance(state, dict) or state.get('status') != 'completed':
                reasons.add('INCOMPLETE_TOOL_NOT_A_DIFF'); continue
            metadata = state.get('metadata', {})
            if not isinstance(metadata, dict):
                continue
            evidence = {'message_id': info['id'], 'part_id': part['id'], 'call_id': part['callID']}
            if isinstance(metadata.get('filediff'), dict):
                try:
                    if (part['callID'], _path(metadata['filediff'].get('file'), binding['root'])) in full_calls:
                        continue
                except (InvalidInput, TypeError):
                    pass
                add(metadata['filediff'], 'ORIGINAL_TOOL_FILEDIFF', evidence)
            elif part['tool'] == 'write' and isinstance(metadata.get('filepath'), str):
                try:
                    if (part['callID'], _path(metadata['filepath'], binding['root'])) in full_calls:
                        continue
                except (InvalidInput, TypeError):
                    pass
                add({'file': metadata['filepath']}, 'ORIGINAL_TOOL_METADATA', evidence)
            else:
                reasons.add('NO_HISTORICAL_PATCH')
    if not files:
        reasons.add('NO_HISTORICAL_DIFF')
    coverage = {'source': 'SAVED_ORIGINAL_ROOT_MESSAGE_OBSERVATION', 'complete': False,
                'messages_returned': min(len(messages), 200), 'messages_limit': 200,
                'historical_generations': 'NATIVE_SESSION_HISTORY_NOT_TURN_GENERATION_ATTRIBUTED',
                'available': available_sources, 'returned': len(files),
                'unavailable': sum(item['availability'] != 'AVAILABLE' for item in files),
                'excluded_paths': out_of_scope,
                'truncated': bool({'FILE_COUNT_LIMIT', 'SOURCE_DIFF_COUNT_LIMIT', 'SOURCE_PART_COUNT_LIMIT'} & reasons) or len(messages) > 200,
                'reason_codes': sorted(reasons), 'snapshot_enabled': False,
                'commands_and_children': 'NOT_OBSERVED', 'rename_detection': 'NOT_AVAILABLE'}
    snapshot = {'scope': scope, 'files': files, 'coverage': coverage}
    reference = {'id': digest(scope), 'revision': 1, 'sha256': digest(snapshot)}
    scopes = {row['content_scope'] for row in files if row['availability'] == 'AVAILABLE'}
    result = {'scope': scope, 'snapshot_ref': reference, 'files': files, 'coverage': coverage,
              'observation_at': observed.get('observation_at'), 'read_only': True,
              'content_scope': next(iter(scopes)) if len(scopes) == 1 else 'MIXED' if scopes else 'UNAVAILABLE',
              'file_hashes': 'PER_FILE_IN_CONTENT' if 'FULL_FILE' in scopes else 'NOT_OBSERVED',
              'current_disk_compared': False, 'editor_dirty_state': 'NOT_OBSERVED', 'model_calls': 0}
    return result, content


def file_detail(directory, content, snapshot_ref, diff_ref):
    if snapshot_ref != directory['snapshot_ref']:
        raise Conflict('DIFF_CHANGED: historical diff catalog changed; refresh without replaying the task')
    if (not isinstance(diff_ref, dict) or set(diff_ref) != {'id', 'revision', 'sha256'} or
            diff_ref.get('revision') != 1 or not _SHA.fullmatch(str(diff_ref.get('id', ''))) or
            not _SHA.fullmatch(str(diff_ref.get('sha256', '')))):
        raise InvalidInput('Exact historical diff reference required')
    row = next((item for item in directory['files'] if item['diff_ref']['id'] == diff_ref['id']), None)
    if row is None:
        raise NotFound('Historical diff is not available in this exact observation')
    if row['diff_ref'] != diff_ref:
        raise Conflict('DIFF_CHANGED: historical diff bytes changed')
    if row['availability'] != 'AVAILABLE':
        raise Conflict('DIFF_UNAVAILABLE: original historical text is not available')
    item = content[diff_ref['id']]
    detail = {k: deepcopy(v) for k,v in item.items() if k != '_load'}
    if '_load' in item:
        detail.update(item['_load']())
    result = {**detail, 'scope': deepcopy(directory['scope']),
              'snapshot_ref': deepcopy(snapshot_ref), 'read_only': True, 'model_calls': 0,
              'current_disk_compared': False, 'editor_dirty_state': 'NOT_OBSERVED',
              'file_hashes': 'UTF8_FULL_FILE_SNAPSHOT_SHA256' if row['content_scope'] == 'FULL_FILE' else 'NOT_OBSERVED'}
    if len(canonical(result).encode()) > MAX_CONTENT_BYTES:
        raise InvalidInput('DIFF_TOO_LARGE: historical content exceeds its response bound')
    return result
