"""Taskbook-bound DAGs over existing TaskSpecs; reuse the only Runner/Verifier."""
import json
from typing import Literal
import uuid

from pydantic import Field, model_validator

from packages.domain.decisions import intake_current, task_identity_current, task_sources_in_intake
from packages.domain.intake import canonical, digest
from packages.domain.questions import AffectedTask, Hash, Id, knowledge_gate, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.taskbooks import BookModel, MAX_REVISION, TaskBookService


class BookRef(BookModel):
    taskbook_id: Id
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    sha256: Hash


class HarnessTaskRef(AffectedTask):
    revision: int = Field(ge=1, le=MAX_REVISION, strict=True)


class HarnessNode(BookModel):
    node_id: Id
    task_ref: HarnessTaskRef
    entry_ids: list[Id] = Field(min_length=1, max_length=100)
    acceptance_entry_ids: list[Id] = Field(min_length=1, max_length=100)
    role: Id
    tool_ids: list[Id] = Field(min_length=1, max_length=20)
    expected_outputs: list[str] = Field(min_length=1, max_length=20)
    checkpoint_ids: list[Id] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_maps(self):
        for name in ('entry_ids', 'acceptance_entry_ids', 'tool_ids', 'expected_outputs', 'checkpoint_ids'):
            values = getattr(self, name)
            if len(set(values)) != len(values):
                raise ValueError('Harness mapping lists must be unique')
        return self


class HarnessInput(BookModel):
    idempotency_key: Id
    expected_revision: int = Field(default=0, ge=0, lt=MAX_REVISION, strict=True)
    taskbook_ref: BookRef
    title: str = Field(min_length=1, max_length=200)
    nodes: list[HarnessNode] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def unique_nodes(self):
        if len({n.node_id for n in self.nodes}) != len(self.nodes) or len({n.task_ref.task_id for n in self.nodes}) != len(self.nodes):
            raise ValueError('Harness nodes and TaskSpec bindings must be unique')
        return self


class HarnessStart(BookModel):
    idempotency_key: Id
    expected_revision: int = Field(ge=1, le=MAX_REVISION, strict=True)
    harness_sha256: Hash
    intent: Literal['USER_START']
    user_ready: bool = Field(strict=True)
    scope_reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def explicit_start(self):
        if self.user_ready is not True:
            raise ValueError('Starting this exact harness node requires explicit user readiness')
        return self


def topological_order(nodes):
    remaining = {n['node_id']: set(n['dependency_node_ids']) for n in nodes}
    if any(deps-set(remaining) for deps in remaining.values()):
        raise InvalidInput('Harness dependencies must all be inside the same frozen DAG')
    order = []
    while remaining:
        ready = sorted(key for key, deps in remaining.items() if not deps)
        if not ready:
            raise InvalidInput('Harness dependencies contain a cycle')
        order.extend(ready)
        for key in ready:
            del remaining[key]
        for deps in remaining.values():
            deps.difference_update(ready)
    return order


class HarnessService:
    def __init__(self, store, verifier=None):
        self.store, self.verifier = store, verifier

    @staticmethod
    def _version(connection, project_id, harness_id, revision=None):
        head = connection.execute('SELECT * FROM project_harnesses WHERE id=? AND project_id=?',
                                  (harness_id, project_id)).fetchone()
        if not head:
            raise NotFound('Harness does not exist in this project')
        revision = head['current_revision'] if revision is None else revision
        row = connection.execute('SELECT * FROM project_harness_versions WHERE harness_id=? AND revision=?',
                                 (harness_id, revision)).fetchone()
        if not row:
            raise NotFound('Harness revision does not exist')
        payload = json.loads(row['payload'])
        if digest(payload) != row['sha256'] or (payload['project_id'], payload['harness_id'], payload['revision']) != (project_id, harness_id, revision):
            raise Conflict('Stored harness integrity check failed')
        return {'id': harness_id, 'project_id': project_id, 'revision': revision,
                'current_revision': head['current_revision'], 'sha256': row['sha256'],
                'payload': payload, 'created_at': row['created_at']}

    def _current(self, connection, value):
        if value['revision'] != value['current_revision']:
            raise Conflict('Harness revision changed; review the current revision')
        payload = value['payload']; project_id = value['project_id']; ref = payload['taskbook_ref']
        book = TaskBookService._version(connection, project_id, ref['taskbook_id'], ref['revision'])
        if book['sha256'] != ref['sha256'] or book['current_revision'] != ref['revision'] or book['review_status'] != 'CURRENT':
            raise Conflict('Taskbook or sources changed; create a reviewed harness revision')
        intake = intake_current(connection, project_id, book['payload']['intake_ref'])
        for node in payload['nodes']:
            spec = task_identity_current(connection, project_id, node['task_ref'])
            task_sources_in_intake(spec, intake)
            task = self.store.task(project_id, node['task_ref']['task_id'], node['task_ref']['revision'])
            if task['review_status'] != 'CURRENT':
                raise Conflict('Task code, source or component combination changed')
        return book

    def get(self, project_id, harness_id, revision=None):
        with self.store.connect() as connection:
            result = self._version(connection, project_id, harness_id, revision)
            try:
                self._current(connection, result)
                result['review_status'] = 'CURRENT'
            except (Conflict, NotFound):
                result['review_status'] = 'NEEDS_REVIEW'
            return result

    def list(self, project_id):
        self.store.project(project_id)
        with self.store.connect() as connection:
            ids = [r[0] for r in connection.execute('SELECT id FROM project_harnesses WHERE project_id=? ORDER BY id', (project_id,))]
        return [self.get(project_id, id) for id in ids]

    def save(self, project_id, body: HarnessInput, harness_id=None, caller_id=None):
        self.store.project(project_id)
        if bool(harness_id) != bool(body.expected_revision):
            raise Conflict('Harness edits require the exact head; new harnesses require zero')
        request_hash = digest({'harness_id': harness_id, 'body': body.model_dump(exclude={'idempotency_key'}), 'caller_id': caller_id})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = connection.execute('SELECT * FROM project_harness_operations WHERE project_id=? AND idempotency_key=?',
                                        (project_id, body.idempotency_key)).fetchone()
            if replay:
                if replay['payload_sha256'] != request_hash:
                    raise Conflict('IDEMPOTENCY_CONFLICT: harness key is bound to another request')
                result = json.loads(replay['result'])
                if digest(result) != replay['sha256']:
                    raise Conflict('Harness operation integrity check failed')
                return result
            if harness_id and self._version(connection, project_id, harness_id)['current_revision'] != body.expected_revision:
                raise Conflict('Harness changed; reload its current revision')
            book = TaskBookService._version(connection, project_id, body.taskbook_ref.taskbook_id, body.taskbook_ref.revision)
            if book['sha256'] != body.taskbook_ref.sha256 or book['revision'] != book['current_revision'] or book['review_status'] != 'CURRENT':
                raise Conflict('Taskbook binding is stale or changed')
            intake = intake_current(connection, project_id, book['payload']['intake_ref'])
            entry_ids = {e['entry_id'] for s in book['payload']['sections'] for e in s['entries']}
            acceptance_ids = {e['entry_id'] for s in book['payload']['sections'] if s['kind'] == 'ACCEPTANCE' for e in s['entries']}
            by_task = {n.task_ref.task_id: n for n in body.nodes}
            nodes = []
            for node in body.nodes:
                spec = task_identity_current(connection, project_id, node.task_ref.model_dump())
                task_sources_in_intake(spec, intake)
                task = self.store.task(project_id, node.task_ref.task_id, node.task_ref.revision)
                if task['review_status'] != 'CURRENT':
                    raise Conflict('Task code, source or component combination changed')
                if set(node.entry_ids)-entry_ids or set(node.acceptance_entry_ids)-acceptance_ids:
                    raise InvalidInput('Harness entries and acceptance mapping must exist in the exact taskbook revision')
                if set(node.tool_ids) != set(spec['required_capabilities']) or node.expected_outputs != spec['expected_artifacts']:
                    raise InvalidInput('Harness must map the exact frozen task tool scope and output paths')
                dependencies = []
                for dependency in spec['dependencies']:
                    parent = by_task.get(dependency['task_id'])
                    if parent is None or parent.task_ref.revision != dependency['task_revision']:
                        raise InvalidInput('Every exact TaskSpec dependency must be included in the harness')
                    dependencies.append(parent.node_id)
                nodes.append({**node.model_dump(), 'dependency_node_ids': sorted(dependencies),
                              'budget': spec['budget'], 'acceptance_ref': spec['acceptance_ref'],
                              'acceptance_hash': spec['acceptance_hash'], 'verification_profile': spec['verification_profile'],
                              'approval_required': spec['approval_required'], 'role_application': 'DECLARED_TASK_MAPPING'})
            order = topological_order(nodes)
            harness_id = harness_id or str(uuid.uuid4()); revision = body.expected_revision+1
            payload = {'schema_version': 'eeas.project-harness.v1', 'project_id': project_id,
                       'harness_id': harness_id, 'revision': revision, 'title': body.title,
                       'taskbook_ref': body.taskbook_ref.model_dump(), 'nodes': nodes, 'topological_order': order,
                       'execution_engine': 'EXISTING_TASK_RUNNER_VERIFIER', 'model_calls': 0,
                       'implementation_started': False, 'created_by': {'kind': 'LOCAL_SESSION', 'id': caller_id}}
            if revision == 1:
                connection.execute('INSERT INTO project_harnesses VALUES (?,?,?)', (harness_id, project_id, revision))
            else:
                connection.execute('UPDATE project_harnesses SET current_revision=? WHERE id=? AND current_revision=?',
                                   (revision, harness_id, body.expected_revision))
            connection.execute('INSERT INTO project_harness_versions VALUES (?,?,?,?,?)',
                               (harness_id, revision, canonical(payload), digest(payload), stamp()))
            result = self._version(connection, project_id, harness_id, revision); result['review_status'] = 'CURRENT'
            connection.execute('INSERT INTO project_harness_operations VALUES (?,?,?,?,?)',
                               (project_id, body.idempotency_key, request_hash, canonical(result), digest(result)))
            return result

    def _node_gate(self, connection, project_id, node):
        ref = node['task_ref']
        gate = knowledge_gate(connection, project_id, ref['task_id'], ref['revision'], ref['spec_hash'])
        if gate['status'] != 'READY':
            return {'status': 'BLOCKED_ON_INFORMATION', 'knowledge': gate}
        spec = task_identity_current(connection, project_id, ref)
        for parent in spec['dependencies']:
            run = connection.execute('''SELECT * FROM task_runs WHERE project_id=? AND task_id=? AND task_revision=?
                ORDER BY attempt_id DESC LIMIT 1''', (project_id, parent['task_id'], parent['task_revision'])).fetchone()
            if not run or run['verification_status'] != 'PASSED':
                return {'status': 'BLOCKED_ON_DEPENDENCY', 'task_id': parent['task_id']}
            proof = connection.execute('SELECT id FROM verification_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1', (run['id'],)).fetchone()
            if self.verifier is None or not proof or self.verifier.evidence(project_id, proof['id'])['association_status'] != 'CURRENT':
                return {'status': 'BLOCKED_ON_DEPENDENCY_EVIDENCE', 'task_id': parent['task_id']}
        return {'status': 'READY', 'approval_required': spec['approval_required'], 'knowledge': gate}

    def state(self, project_id, harness_id, revision=None):
        with self.store.connect() as connection:
            value = self._version(connection, project_id, harness_id, revision)
            try:
                self._current(connection, value)
            except (Conflict, NotFound):
                return {'harness_id': harness_id, 'revision': value['revision'], 'sha256': value['sha256'], 'status': 'NEEDS_REVIEW', 'nodes': []}
            nodes = []
            for node in value['payload']['nodes']:
                actual = connection.execute('''SELECT r.id,r.execution_status,r.verification_status FROM task_runs r
                    JOIN project_harness_node_starts s ON s.run_id=r.id
                    WHERE s.project_id=? AND s.harness_id=? AND s.harness_revision=? AND s.node_id=?
                    ORDER BY r.attempt_id DESC LIMIT 1''', (project_id, harness_id, value['revision'], node['node_id'])).fetchone()
                nodes.append({'node_id': node['node_id'], 'task_ref': node['task_ref'],
                              **self._node_gate(connection, project_id, node),
                              'latest_run': dict(actual) if actual else None})
            return {'harness_id': harness_id, 'revision': value['revision'], 'sha256': value['sha256'],
                    'status': 'CURRENT', 'nodes': nodes}

    def start_node(self, project_id, harness_id, node_id, body: HarnessStart, caller_id):
        request_hash = digest({'harness_id': harness_id, 'node_id': node_id, 'body': body.model_dump(), 'caller_id': caller_id})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = connection.execute('SELECT * FROM project_harness_node_starts WHERE project_id=? AND idempotency_key=?',
                                        (project_id, body.idempotency_key)).fetchone()
            if replay:
                if replay['payload_sha256'] != request_hash:
                    raise Conflict('IDEMPOTENCY_CONFLICT: harness start key is bound to another scope')
                payload = json.loads(replay['payload'])
                if digest(payload) != replay['sha256']:
                    raise Conflict('Harness start receipt integrity check failed')
                return payload
            value = self._version(connection, project_id, harness_id)
            if value['revision'] != body.expected_revision or value['sha256'] != body.harness_sha256:
                raise Conflict('Harness start requires its exact current revision/SHA')
            self._current(connection, value)
            node = next((n for n in value['payload']['nodes'] if n['node_id'] == node_id), None)
            if node is None:
                raise NotFound('Harness node does not exist')
            gate = self._node_gate(connection, project_id, node)
            if gate['status'] != 'READY':
                raise Conflict(gate['status']+': this exact node is not ready')
            active = connection.execute('''SELECT r.execution_status FROM task_runs r
                JOIN project_harness_node_starts s ON s.run_id=r.id
                WHERE s.project_id=? AND s.harness_id=? AND s.harness_revision=? AND s.node_id=?
                AND (r.execution_status IN ('QUEUED','AWAITING_APPROVAL','RUNNING','CANCEL_REQUESTED','UNKNOWN_OUTCOME')
                    OR (r.execution_status='RETURNED' AND r.verification_status IN ('NOT_RUN','RUNNING'))) LIMIT 1''',
                (project_id, harness_id, value['revision'], node_id)).fetchone()
            if active:
                raise Conflict('Harness node has an active, unverified or unknown run; inspect its existing result first')
            operation_id = 'harness-'+request_hash
            if connection.execute('SELECT 1 FROM task_runs WHERE project_id=? AND operation_id=?', (project_id, operation_id)).fetchone():
                raise Conflict('Harness operation has an unassociated existing run; inspect before retrying')
            ref = node['task_ref']
            run = self.store.start_run(project_id, ref['task_id'], ref['revision'], operation_id, transaction=connection)
            created = stamp(); receipt_id = str(uuid.uuid4())
            payload = {'schema_version': 'eeas.harness-node-start.v1', 'id': receipt_id, 'project_id': project_id,
                       'harness_id': harness_id, 'harness_revision': value['revision'], 'harness_sha256': value['sha256'],
                       'taskbook_ref': value['payload']['taskbook_ref'], 'node_id': node_id,
                       'task_ref': ref, 'user_ready': True, 'intent': body.intent, 'scope_reason': body.scope_reason,
                       'caller': {'kind': 'LOCAL_SESSION', 'id': caller_id}, 'run_id': run['id'],
                       'run': run, 'knowledge': gate['knowledge'], 'created_at': created}
            connection.execute('INSERT INTO project_harness_node_starts VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                               (receipt_id, project_id, harness_id, value['revision'], value['sha256'], node_id,
                                body.idempotency_key, request_hash, run['id'], canonical(payload), digest(payload), created))
            return payload
