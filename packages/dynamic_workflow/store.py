"""Versioned dynamic task DAGs; execution remains with the original agent.

References are provenance, not authorization. The application adapter admits
projects, actor identity and real operation/evidence refs before invoking us.
"""
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sqlite3


class WorkflowConflict(RuntimeError): pass
class WorkflowInvalidInput(ValueError): pass
class WorkflowNotFound(LookupError): pass
class WorkflowIntegrityError(RuntimeError): pass


LOCKABLE = {'title', 'description', 'depends_on', 'acceptance', 'source_refs', 'question_ids', 'agent_policy'}
MAX_BYTES = 2 * 1024 * 1024


def _canonical(value):
    try: return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    except (ValueError, TypeError, UnicodeError) as error: raise WorkflowInvalidInput('Only bounded JSON is accepted') from error


def _sha(value): return hashlib.sha256(_canonical(value).encode('utf-8')).hexdigest()


def _text(value, maximum=8000, *, empty=False):
    if type(value) is not str or '\x00' in value or not empty and not value.strip(): raise WorkflowInvalidInput('Expected nonempty Unicode text')
    try: raw = value.encode('utf-8')
    except UnicodeError: raise WorkflowInvalidInput('Invalid Unicode text') from None
    if len(value) > maximum or len(raw) > maximum * 4: raise WorkflowInvalidInput('Text exceeds its bound; no truncation performed')
    return value


def _id(value):
    if type(value) is not str or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', value): raise WorkflowInvalidInput('Expected a scoped identifier')
    return value


def _shape(value, allowed, required=()):
    if type(value) is not dict or set(value) - set(allowed) or set(required) - set(value): raise WorkflowInvalidInput('Unexpected or missing fields')
    return value


def _items(value, maximum=512):
    if type(value) is not list or len(value) > maximum: raise WorkflowInvalidInput('Expected a bounded list')
    return value


def _ids(value):
    result = [_id(item) for item in _items(value)]
    if len(set(result)) != len(result): raise WorkflowInvalidInput('Duplicate identifiers are not accepted')
    return sorted(result)


def _ref(value, project_id=None):
    _shape(value, {'id', 'revision', 'sha256', 'kind', 'project_id'}, {'id', 'revision', 'sha256'})
    result = {'id': _id(value['id']), 'revision': value['revision'], 'sha256': value['sha256']}
    if type(result['revision']) is not int or not 1 <= result['revision'] <= 2147483647 or type(result['sha256']) is not str or not re.fullmatch('[a-f0-9]{64}', result['sha256']):
        raise WorkflowInvalidInput('Expected an exact version and checksum')
    if 'kind' in value: result['kind'] = _id(value['kind'])
    if 'project_id' in value:
        result['project_id'] = _id(value['project_id'])
        if project_id is not None and result['project_id'] != project_id: raise WorkflowConflict('Reference belongs to another project')
    return result


def _refs(value, project_id): return sorted((_ref(item, project_id) for item in _items(value, 128)), key=_canonical)


def _policy(value):
    if value is None: return {field: {'mode': 'AUTO', 'value': None} for field in ('model', 'depth')}
    _shape(value, {'model', 'depth'}, {'model', 'depth'})
    result = {}
    for name in ('model', 'depth'):
        item = _shape(value[name], {'mode', 'value'}, {'mode', 'value'})
        if item['mode'] not in ('AUTO', 'MANUAL'): raise WorkflowInvalidInput('Unknown agent selection mode')
        selected = item['value']
        if item['mode'] == 'AUTO' and selected is not None: raise WorkflowInvalidInput('Automatic selection has no pinned value')
        if item['mode'] == 'MANUAL': _text(selected, 300)
        result[name] = {'mode': item['mode'], 'value': selected}
    return result


def _plan(value, pid):
    _shape(value, {'title', 'goal', 'nodes', 'questions', 'external_refs'}, {'title', 'goal', 'nodes'})
    result = {'title': _text(value['title'], 300), 'goal': _text(value['goal']), 'nodes': [], 'questions': [],
              'external_refs': _refs(value.get('external_refs', []), pid)}
    for item in _items(value.get('questions', [])):
        _shape(item, {'id', 'text', 'blocking', 'source_refs'}, {'id', 'text'})
        blocking = item.get('blocking', True)
        if type(blocking) is not bool: raise WorkflowInvalidInput('Question blocking is a boolean')
        result['questions'].append({'id': _id(item['id']), 'text': _text(item['text']), 'blocking': blocking, 'source_refs': _refs(item.get('source_refs', []), pid)})
    question_ids = [item['id'] for item in result['questions']]
    if len(set(question_ids)) != len(question_ids): raise WorkflowInvalidInput('Duplicate question ids')
    for item in _items(value['nodes']):
        _shape(item, {'id', *LOCKABLE}, {'id', 'title'})
        acceptance = []
        for criterion in _items(item.get('acceptance', []), 128):
            _shape(criterion, {'id', 'text', 'method'}, {'id', 'text'})
            entry = {'id': _id(criterion['id']), 'text': _text(criterion['text'], 2000)}
            if 'method' in criterion: entry['method'] = _text(criterion['method'], 2000)
            acceptance.append(entry)
        if len({criterion['id'] for criterion in acceptance}) != len(acceptance): raise WorkflowInvalidInput('Duplicate acceptance ids')
        questions = _ids(item.get('question_ids', []))
        if set(questions) - set(question_ids): raise WorkflowInvalidInput('Node refers to an unknown question')
        result['nodes'].append({'id': _id(item['id']), 'title': _text(item['title'], 300), 'description': _text(item.get('description', ''), empty=True),
            'depends_on': _ids(item.get('depends_on', [])), 'acceptance': sorted(acceptance, key=lambda item: item['id']),
            'source_refs': _refs(item.get('source_refs', []), pid), 'question_ids': questions, 'agent_policy': _policy(item.get('agent_policy'))})
    nodes = {item['id']: item for item in result['nodes']}
    if len(nodes) != len(result['nodes']): raise WorkflowInvalidInput('Duplicate task ids')
    for node in nodes.values():
        if node['id'] in node['depends_on'] or set(node['depends_on']) - set(nodes): raise WorkflowInvalidInput('Dependency is self-referential or missing')
    # Iterative topological validation works for the full bound without recursion.
    pending = {ident: set(node['depends_on']) for ident, node in nodes.items()}
    while pending:
        ready = {ident for ident, deps in pending.items() if not deps}
        if not ready: raise WorkflowInvalidInput('Task dependencies contain a cycle')
        pending = {ident: deps - ready for ident, deps in pending.items() if ident not in ready}
    result['nodes'].sort(key=lambda item: item['id']); result['questions'].sort(key=lambda item: item['id'])
    if len(_canonical(result).encode('utf-8')) > MAX_BYTES: raise WorkflowInvalidInput('Plan exceeds 2 MiB; no input was truncated')
    return result


def _question_refs(plan, node):
    assigned = {qid for item in plan['nodes'] for qid in item['question_ids']}
    return set(node['question_ids']) | {question['id'] for question in plan['questions'] if question['blocking'] and question['id'] not in assigned}


def _local_basis(version, node):
    ids = _question_refs(version['plan'], node)
    return _sha({'node': node, 'goal': version['plan']['goal'], 'external_refs': version['plan']['external_refs'],
        'questions': [question for question in version['plan']['questions'] if question['id'] in ids],
        'answers': {qid: answer for qid, answer in version['answers'].items() if qid in ids}})


def _bases(version):
    pending = {node['id']: node for node in version['plan']['nodes']}; result = {}
    while pending:
        for ident, node in list(pending.items()):
            if all(dep in result for dep in node['depends_on']):
                result[ident] = _sha({'local': _local_basis(version, node), 'dependencies': {dep: result[dep] for dep in node['depends_on']}})
                del pending[ident]
    return result


def _impact(previous, current):
    before = {node['id']: node for node in previous['plan']['nodes']} if previous else {}
    after = {node['id']: node for node in current['plan']['nodes']}
    changed = {ident for ident in set(before) | set(after) if ident not in before or ident not in after or _local_basis(previous, before[ident]) != _local_basis(current, after[ident])}
    affected = set(changed)
    while True:
        updated = affected | {node['id'] for node in [*before.values(), *after.values()] if set(node['depends_on']) & affected}
        if updated == affected: break
        affected = updated
    return {'changed_node_ids': sorted(changed), 'affected_node_ids': sorted(affected), 'removed_node_ids': sorted(set(before) - set(after))}


class DynamicWorkflowStore:
    """Accept an existing Store.connect() owner or a standalone SQLite path."""
    def __init__(self, store_or_dbpath):
        self.store = store_or_dbpath if callable(getattr(store_or_dbpath, 'connect', None)) else None
        self.path = None if self.store else Path(store_or_dbpath)
        if self.path: self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as connection:
            connection.executescript('''CREATE TABLE IF NOT EXISTS dynamic_workflow_heads(project_id TEXT, workflow_id TEXT, revision INTEGER NOT NULL, PRIMARY KEY(project_id,workflow_id));
            CREATE TABLE IF NOT EXISTS dynamic_workflow_versions(project_id TEXT,workflow_id TEXT,revision INTEGER,payload TEXT NOT NULL,sha256 TEXT NOT NULL,PRIMARY KEY(project_id,workflow_id,revision));
            CREATE TABLE IF NOT EXISTS dynamic_workflow_operations(project_id TEXT,operation_id TEXT,workflow_id TEXT,request_sha256 TEXT,result TEXT,result_sha256 TEXT,PRIMARY KEY(project_id,operation_id));
            CREATE TABLE IF NOT EXISTS dynamic_workflow_records(project_id TEXT,workflow_id TEXT,record_id TEXT,payload TEXT,sha256 TEXT,PRIMARY KEY(project_id,workflow_id,record_id));''')

    @contextmanager
    def _db(self):
        if self.store:
            with self.store.connect() as connection: yield connection
        else:
            connection = sqlite3.connect(self.path, timeout=10); connection.row_factory = sqlite3.Row
            try:
                with connection: yield connection
            finally: connection.close()

    @staticmethod
    @contextmanager
    def _tx(connection):
        connection.execute('BEGIN IMMEDIATE')
        try: yield; connection.commit()
        except BaseException: connection.rollback(); raise

    @staticmethod
    def _checked(raw, checksum):
        try:
            value = json.loads(raw)
            if _sha(value) != checksum: raise ValueError()
            return value
        except (ValueError, TypeError, UnicodeError): raise WorkflowIntegrityError('Workflow record integrity failed; original retained') from None

    def _scope(self, pid):
        _id(pid)
        if self.store and callable(getattr(self.store, 'project', None)): self.store.project(pid)

    @staticmethod
    def _stamp(): return datetime.now(timezone.utc).isoformat()

    def _version(self, connection, pid, wid, revision=None):
        head = connection.execute('SELECT revision FROM dynamic_workflow_heads WHERE project_id=? AND workflow_id=?', (pid, wid)).fetchone()
        if head is None: raise WorkflowNotFound('Workflow not found in this project')
        maximum = connection.execute('SELECT max(revision) FROM dynamic_workflow_versions WHERE project_id=? AND workflow_id=?', (pid, wid)).fetchone()[0]
        if head['revision'] != maximum: raise WorkflowIntegrityError('Workflow head is inconsistent with immutable history')
        revision = head['revision'] if revision is None else revision
        if type(revision) is not int or revision < 1: raise WorkflowInvalidInput('Invalid workflow revision')
        row = connection.execute('SELECT payload,sha256 FROM dynamic_workflow_versions WHERE project_id=? AND workflow_id=? AND revision=?', (pid, wid, revision)).fetchone()
        if row is None: raise WorkflowNotFound('Workflow version not found')
        value = self._checked(row['payload'], row['sha256'])
        if value['project_id'] != pid or value['id'] != wid or value['revision'] != revision: raise WorkflowIntegrityError('Workflow scope integrity failed')
        return value, {'id': wid, 'revision': revision, 'sha256': row['sha256']}

    @staticmethod
    def _receipt(version, ref): return {**deepcopy(version), 'workflow_ref': ref, 'automatic_execution': False, 'read_only_execution': True}

    def _perform(self, pid, wid, operation_id, request, mutation):
        self._scope(pid); _id(wid); _id(operation_id)
        request_hash = _sha(request)
        with self._db() as connection, self._tx(connection):
            prior = connection.execute('SELECT * FROM dynamic_workflow_operations WHERE project_id=? AND operation_id=?', (pid, operation_id)).fetchone()
            if prior:
                if prior['workflow_id'] != wid or prior['request_sha256'] != request_hash: raise WorkflowConflict('Operation id already has different input')
                result = self._checked(prior['result'], prior['result_sha256']); replayed = True
            else:
                result = mutation(connection)
                connection.execute('INSERT INTO dynamic_workflow_operations VALUES(?,?,?,?,?,?)', (pid, operation_id, wid, request_hash, _canonical(result), _sha(result)))
                replayed = False
        return {**result, 'operation_id': operation_id, 'operation_status': 'COMPLETED', 'request_sha256': request_hash, 'replayed': replayed}

    def _save_version(self, connection, pid, wid, plan, locks, answers, previous, *, actor, reason):
        revision = previous['revision'] + 1 if previous else 1
        value = {'project_id': pid, 'id': wid, 'revision': revision, 'plan': plan, 'locks': locks, 'answers': answers,
                 'created_at': self._stamp(), 'change': {'actor': actor, 'reason': reason, 'base_ref': None}}
        if previous: value['change']['base_ref'] = {'id': wid, 'revision': previous['revision'], 'sha256': _sha(previous)}
        value['change'].update(_impact(previous, value))
        if len(_canonical(value).encode('utf-8')) > MAX_BYTES: raise WorkflowInvalidInput('Workflow exceeds 2 MiB; original retained')
        checksum = _sha(value)
        connection.execute('INSERT INTO dynamic_workflow_versions VALUES(?,?,?,?,?)', (pid, wid, revision, _canonical(value), checksum))
        connection.execute('INSERT INTO dynamic_workflow_heads VALUES(?,?,?) ON CONFLICT(project_id,workflow_id) DO UPDATE SET revision=excluded.revision', (pid, wid, revision))
        return self._receipt(value, {'id': wid, 'revision': revision, 'sha256': checksum})

    def create(self, project_id, plan, *, operation_id, workflow_id=None, actor='MODEL'):
        pid = _id(project_id); _id(operation_id); wid = workflow_id or _sha([pid, 'create', operation_id])[:32]
        plan = _plan(plan, pid); self._actor(actor)
        if actor == 'MODEL' and any(policy['mode'] == 'MANUAL' for node in plan['nodes'] for policy in node['agent_policy'].values()):
            raise WorkflowConflict('A model cannot create manual user pins')
        def mutation(connection):
            if connection.execute('SELECT 1 FROM dynamic_workflow_heads WHERE project_id=? AND workflow_id=?', (pid, wid)).fetchone(): raise WorkflowConflict('Workflow already exists')
            return self._save_version(connection, pid, wid, plan, {}, {}, None, actor=actor, reason='CREATE')
        return self._perform(pid, wid, operation_id, {'method': 'create', 'workflow_id': wid, 'plan': plan, 'actor': actor}, mutation)

    @staticmethod
    def _actor(actor):
        if actor not in ('MODEL', 'USER'): raise WorkflowInvalidInput('Plan actor must be MODEL or USER')

    def _base(self, connection, pid, wid, expected_ref):
        expected = _ref(expected_ref, pid); value, actual = self._version(connection, pid, wid)
        if expected != actual: raise WorkflowConflict('Workflow head changed; preserve proposal and read the current version')
        return value

    def replan(self, project_id, workflow_id, plan, *, expected_ref, operation_id, actor='MODEL', reason):
        pid, wid = _id(project_id), _id(workflow_id); plan = _plan(plan, pid); expected = _ref(expected_ref, pid); self._actor(actor); reason = _text(reason)
        def mutation(connection):
            previous = self._base(connection, pid, wid, expected); before = {node['id']: node for node in previous['plan']['nodes']}; after = {node['id']: node for node in plan['nodes']}
            if actor == 'MODEL':
                for ident, node in before.items():
                    pinned = any(item['mode'] == 'MANUAL' for item in node['agent_policy'].values())
                    if ident not in after and (pinned or previous['locks'].get(ident)): raise WorkflowConflict('Replan would remove a user-locked node')
                    if ident in after:
                        for field in previous['locks'].get(ident, []):
                            if node[field] != after[ident][field]: raise WorkflowConflict('Replan would replace a user-locked field')
                        for name, policy in node['agent_policy'].items():
                            if policy['mode'] == 'MANUAL' and policy != after[ident]['agent_policy'][name]: raise WorkflowConflict('Replan would override a manual model/depth pin')
                for ident, node in after.items():
                    for name, policy in node['agent_policy'].items():
                        if policy['mode'] == 'MANUAL' and (ident not in before or policy != before[ident]['agent_policy'][name]): raise WorkflowConflict('A model cannot create manual user pins')
            old_questions = {question['id']: question for question in previous['plan']['questions']}
            answers = {question['id']: previous['answers'][question['id']] for question in plan['questions'] if question['id'] in previous['answers'] and old_questions.get(question['id']) == question}
            locks = {ident: fields for ident, fields in previous['locks'].items() if ident in after}
            return self._save_version(connection, pid, wid, plan, locks, answers, previous, actor=actor, reason=reason)
        return self._perform(pid, wid, operation_id, {'method': 'replan', 'workflow_id': wid, 'plan': plan, 'expected_ref': expected, 'actor': actor, 'reason': reason}, mutation)

    def answer(self, project_id, workflow_id, question_id, answer, *, expected_ref, operation_id, actor='USER'):
        pid, wid, qid = _id(project_id), _id(workflow_id), _id(question_id); expected = _ref(expected_ref, pid); answer = _text(answer)
        if actor != 'USER': raise WorkflowConflict('Only an admitted explicit user answer confirms a question')
        def mutation(connection):
            previous = self._base(connection, pid, wid, expected)
            question = next((item for item in previous['plan']['questions'] if item['id'] == qid), None)
            if question is None: raise WorkflowNotFound('Question not found in this version')
            answers = {**previous['answers'], qid: {'answer': answer, 'question_sha256': _sha(question), 'actor': 'USER'}}
            return self._save_version(connection, pid, wid, previous['plan'], previous['locks'], answers, previous, actor='USER', reason='ANSWER_QUESTION')
        return self._perform(pid, wid, operation_id, {'method': 'answer', 'workflow_id': wid, 'question_id': qid, 'answer': answer, 'expected_ref': expected}, mutation)

    def set_locks(self, project_id, workflow_id, node_id, fields, *, expected_ref, operation_id, actor='USER'):
        pid, wid, nid = _id(project_id), _id(workflow_id), _id(node_id); expected = _ref(expected_ref, pid); fields = _ids(fields)
        if actor != 'USER': raise WorkflowConflict('Only the user may change manual locks')
        if set(fields) - LOCKABLE: raise WorkflowInvalidInput('Unknown lock fields')
        def mutation(connection):
            previous = self._base(connection, pid, wid, expected)
            if not any(node['id'] == nid for node in previous['plan']['nodes']): raise WorkflowNotFound('Task not found')
            locks = {**previous['locks'], nid: fields}
            return self._save_version(connection, pid, wid, previous['plan'], locks, previous['answers'], previous, actor='USER', reason='CHANGE_LOCKS')
        return self._perform(pid, wid, operation_id, {'method': 'set_locks', 'workflow_id': wid, 'node_id': nid, 'fields': fields, 'expected_ref': expected}, mutation)

    def _record(self, project_id, workflow_id, node_id, payload, *, kind, plan_ref, operation_id):
        pid, wid, nid = _id(project_id), _id(workflow_id), _id(node_id); ref = _ref(plan_ref, pid)
        if ref['id'] != wid: raise WorkflowConflict('Plan reference belongs to another workflow')
        def mutation(connection):
            version, actual = self._version(connection, pid, wid, ref['revision'])
            if actual != ref: raise WorkflowConflict('Plan version checksum changed')
            if nid not in _bases(version): raise WorkflowNotFound('Task not in selected plan version')
            if kind == 'VALIDATION' and payload['criterion_id'] not in {item['id'] for node in version['plan']['nodes'] if node['id'] == nid for item in node['acceptance']}:
                raise WorkflowInvalidInput('Validation criterion not present in selected basis')
            record = {'id': _sha([pid, wid, operation_id])[:32], 'project_id': pid, 'workflow_id': wid, 'node_id': nid,
                      'plan_ref': ref, 'node_basis_sha256': _bases(version)[nid], 'kind': kind, 'payload': payload, 'created_at': self._stamp()}
            if len(_canonical(record).encode('utf-8')) > 128 * 1024: raise WorkflowInvalidInput('Observation exceeds its bound')
            checksum = _sha(record); connection.execute('INSERT INTO dynamic_workflow_records VALUES(?,?,?,?,?)', (pid, wid, record['id'], _canonical(record), checksum))
            return {'record': record, 'record_ref': {'id': record['id'], 'revision': 1, 'sha256': checksum}, 'workflow_ref': ref, 'automatic_execution': False, 'read_only_execution': True}
        return self._perform(pid, wid, operation_id, {'method': kind, 'workflow_id': wid, 'node_id': nid, 'plan_ref': ref, 'payload': payload}, mutation)

    def attach_execution(self, project_id, workflow_id, node_id, actual_observation, *, plan_ref, operation_id):
        pid = _id(project_id); value = deepcopy(actual_observation)
        _shape(value, {'owner', 'status', 'observed_at', 'source_ref', 'coverage', 'routing'}, {'owner', 'status', 'observed_at', 'source_ref'})
        owner = _shape(value['owner'], {'project_id', 'session_id', 'operation_id', 'generation'}, {'project_id', 'session_id', 'operation_id', 'generation'})
        if owner['project_id'] != pid: raise WorkflowConflict('Observed operation belongs to another project')
        _id(owner['session_id']); _id(owner['operation_id'])
        if type(owner['generation']) is not int or not 0 <= owner['generation'] < 2 ** 48: raise WorkflowInvalidInput('Invalid observed generation')
        if value['status'] not in ('RUNNING', 'RETURNED', 'FAILED', 'CANCEL_REQUESTED', 'CANCELLED', 'TIMED_OUT', 'UNKNOWN_OUTCOME', 'UNKNOWN', 'IDLE'): raise WorkflowInvalidInput('Unknown observed execution state')
        try:
            stamp = datetime.fromisoformat(value['observed_at'].replace('Z', '+00:00'))
            if stamp.tzinfo is None: raise ValueError()
            value['observed_at'] = stamp.astimezone(timezone.utc).isoformat()
        except (AttributeError, ValueError, TypeError): raise WorkflowInvalidInput('Timezone-aware observation required') from None
        value['source_ref'] = _ref(value['source_ref'], pid)
        value['coverage'] = value.get('coverage', 'PARTIAL')
        if value['coverage'] not in ('EXACT_OPERATION', 'PARTIAL'): raise WorkflowInvalidInput('Unknown observation coverage')
        routing = value.get('routing', {})
        _shape(routing, {'model', 'depth'})
        for name in ('model', 'depth'):
            item = routing.get(name, {'status': 'UNOBSERVED', 'effective': None, 'evidence_level': 'NONE', 'evidence_refs': []})
            _shape(item, {'status', 'effective', 'evidence_level', 'evidence_refs'}, {'status', 'effective', 'evidence_level', 'evidence_refs'})
            if item['status'] not in ('OBSERVED', 'UNOBSERVED', 'UNSUPPORTED') or item['evidence_level'] not in ('ASSISTANT_MESSAGE', 'TASK_METADATA_SELECTION', 'SESSION_METADATA', 'NONE'): raise WorkflowInvalidInput('Unknown route evidence state')
            refs = _refs(item['evidence_refs'], pid)
            if item['status'] == 'OBSERVED':
                _text(item['effective'], 300)
                if item['evidence_level'] != 'ASSISTANT_MESSAGE' or not refs: raise WorkflowConflict('Effective route requires an observed original assistant message ref')
            elif item['effective'] is not None: raise WorkflowConflict('An unobserved or unsupported route is not effective')
            routing[name] = {**item, 'evidence_refs': refs}
        value['routing'] = routing
        return self._record(pid, workflow_id, node_id, value, kind='EXECUTION_OBSERVATION', plan_ref=plan_ref, operation_id=operation_id)

    def record_claim(self, project_id, workflow_id, node_id, text, *, plan_ref, operation_id, source_refs=None):
        return self._record(project_id, workflow_id, node_id, {'text': _text(text), 'source_refs': _refs(source_refs or [], project_id)}, kind='MODEL_CLAIM', plan_ref=plan_ref, operation_id=operation_id)

    def record_validation(self, project_id, workflow_id, node_id, criterion_id, outcome, *, plan_ref, evidence_refs, operation_id, actor):
        if actor not in ('USER', 'VERIFIER'): raise WorkflowConflict('Model claims cannot verify acceptance')
        if outcome not in ('PASSED', 'FAILED'): raise WorkflowInvalidInput('Validation outcome must be explicit')
        refs = _refs(evidence_refs, project_id)
        if not refs: raise WorkflowInvalidInput('Validation needs exact evidence refs')
        return self._record(project_id, workflow_id, node_id, {'criterion_id': _id(criterion_id), 'outcome': outcome, 'evidence_refs': refs, 'actor': actor}, kind='VALIDATION', plan_ref=plan_ref, operation_id=operation_id)

    def get(self, project_id, workflow_id, revision=None):
        pid, wid = _id(project_id), _id(workflow_id); self._scope(pid)
        with self._db() as connection:
            version, ref = self._version(connection, pid, wid, revision)
            records = []
            for row in connection.execute('SELECT payload,sha256 FROM dynamic_workflow_records WHERE project_id=? AND workflow_id=? ORDER BY rowid LIMIT 2049', (pid, wid)):
                record = self._checked(row['payload'], row['sha256'])
                records.append({**record, 'record_ref': {'id': record['id'], 'revision': 1, 'sha256': row['sha256']}})
            head = connection.execute('SELECT revision FROM dynamic_workflow_heads WHERE project_id=? AND workflow_id=?', (pid, wid)).fetchone()['revision']
        if len(records) > 2048: raise WorkflowConflict('Observation window exceeds v1 bound; original records retained')
        bases = _bases(version)
        for record in records: record['basis_status'] = 'CURRENT_BASIS' if record['node_basis_sha256'] == bases.get(record['node_id']) else 'OLD_BASIS'
        validations = {}
        for record in records:
            if record['kind'] == 'VALIDATION' and record['basis_status'] == 'CURRENT_BASIS': validations[(record['node_id'], record['payload']['criterion_id'])] = record['payload']['outcome']
        pending_by_node = {node['id']: [question['id'] for question in version['plan']['questions'] if question['id'] in _question_refs(version['plan'], node) and question['blocking'] and question['id'] not in version['answers']] for node in version['plan']['nodes']}
        validated = set()
        while True:
            confirmed = validated | {node['id'] for node in version['plan']['nodes'] if node['acceptance'] and not pending_by_node[node['id']] and not set(node['depends_on']) - validated and all(validations.get((node['id'], criterion['id'])) == 'PASSED' for criterion in node['acceptance'])}
            if confirmed == validated: break
            validated = confirmed
        projection = []
        for node in version['plan']['nodes']:
            pending = pending_by_node[node['id']]
            current = [record for record in records if record['node_id'] == node['id'] and record['basis_status'] == 'CURRENT_BASIS' and record['kind'] == 'EXECUTION_OBSERVATION']
            current.sort(key=lambda record: (record['payload']['observed_at'], record['created_at']))
            validation_status = 'PASSED' if node['id'] in validated else 'FAILED' if any(validations.get((node['id'], criterion['id'])) == 'FAILED' for criterion in node['acceptance']) else 'NOT_OBSERVED'
            status = ('VALIDATED_BY_RECORDED_EVIDENCE' if node['id'] in validated else 'NEEDS_CONFIRMATION' if pending else 'NEEDS_ACCEPTANCE' if not node['acceptance'] else
                      'WAITING_DEPENDENCIES' if set(node['depends_on']) - validated else 'VALIDATION_FAILED' if validation_status == 'FAILED' else 'OBSERVED_' + current[-1]['payload']['status'] if current else
                      'NEEDS_REVIEW' if any(record['node_id'] == node['id'] for record in records) else 'READY_FOR_EXPLICIT_ACTION')
            projection.append({'node_id': node['id'], 'basis_sha256': bases[node['id']], 'status': status, 'pending_question_ids': pending,
                               'validation_status': validation_status, 'execution_status': current[-1]['payload']['status'] if current else 'NOT_OBSERVED',
                               'latest_execution_ref': current[-1]['record_ref'] if current else None,
                               'requested': node['agent_policy'], 'effective': current[-1]['payload']['routing'] if current else None})
        return {**self._receipt(version, ref), 'current_revision': head, 'is_current_revision': head == ref['revision'], 'actual_records': records,
                'node_projection': projection, 'ready_node_ids': [item['node_id'] for item in projection if item['status'] == 'READY_FOR_EXPLICIT_ACTION'],
                'coverage': 'EXPLICIT_LINKED_RECORDS_ONLY', 'dispatch_performed': False}

    def list(self, project_id, limit=100):
        pid = _id(project_id); self._scope(pid)
        if type(limit) is not int or not 1 <= limit <= 512: raise WorkflowInvalidInput('Invalid list limit')
        with self._db() as connection: ids = [row['workflow_id'] for row in connection.execute('SELECT workflow_id FROM dynamic_workflow_heads WHERE project_id=? ORDER BY rowid DESC LIMIT ?', (pid, limit))]
        return [self.get(pid, wid) for wid in ids]

    def operation(self, project_id, workflow_id, operation_id):
        pid, op = _id(project_id), _id(operation_id); self._scope(pid)
        if workflow_id is not None: _id(workflow_id)
        with self._db() as connection:
            row = connection.execute('SELECT * FROM dynamic_workflow_operations WHERE project_id=? AND operation_id=?', (pid, op)).fetchone()
            if row is None or workflow_id is not None and row['workflow_id'] != workflow_id: raise WorkflowNotFound('Operation not observed; absence is not proof it did not execute')
            value = self._checked(row['result'], row['result_sha256'])
        return {**value, 'operation_id': op, 'operation_status': 'COMPLETED', 'request_sha256': row['request_sha256'], 'replayed': True}
