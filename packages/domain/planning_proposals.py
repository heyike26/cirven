"""Source-bound coauthoring and task networks, backed by immutable revisions.

Native OpenCode owns its model loop. This service records proposals and human
decisions; it never launches code development or grants tool permissions.
"""
import asyncio
import hashlib
import json
import re
import uuid

from pydantic import ValidationError

from packages.domain.intake import IntakeInput, IntakeService, canonical, digest
from packages.domain.planning_schema import PlanningCandidate, QuestionResolution, CoveredModelProposal
from packages.domain import planning_coverage as coverage
from packages.domain.planning_continuity import (
    SCHEMA as CONTINUITY_SCHEMA, include_decision_impact, question_unchanged, structural_impact)
from packages.domain.questions import raw_hash, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.taskbooks import KINDS, HEADINGS, TaskBookInput, TaskBookService
from packages.planning.spec_kit import planning_prompt


class PlanningProposals:
    def __init__(self, store, source_root, *, transport=None, workspaces=None):
        self.store, self.source_root = store, source_root
        self.transport, self.workspaces = transport, workspaces
        self.discovery_authority = None
        self.competition_sources = None
        self.observation_authority = None
        self.intakes = IntakeService(store, source_root)
        from packages.domain.harness_manual import HarnessManual
        self.manual_changes = HarnessManual(self, workspaces) if workspaces is not None else None

    @staticmethod
    def _checked(row):
        value = json.loads(row['payload'])
        if digest(value) != row['sha256']:
            raise Conflict('Planning record integrity check failed')
        return value

    @staticmethod
    def _event(connection, pid, ident, op, kind, **details):
        value = {'kind': kind, 'created_at': stamp(), **details}
        connection.execute('''INSERT INTO planning_proposal_events
            (project_id,proposal_id,operation_id,payload,sha256) VALUES (?,?,?,?,?)''',
            (pid, ident, op, canonical(value), digest(value)))

    @staticmethod
    def _replay(connection, pid, op, request_hash):
        row = connection.execute('SELECT * FROM planning_proposal_operations WHERE project_id=? AND operation_id=?',
                                 (pid, op)).fetchone()
        if row is None:
            return None
        if row['request_sha256'] != request_hash:
            raise Conflict('IDEMPOTENCY_CONFLICT: planning operation binds different input')
        if row['status'] == 'PROCESSING':
            return {'id': row['proposal_id'], 'status': 'PROCESSING', 'operation_id': op,
                    'replayed': True, 'message': '已有规划请求；请查看事件。服务中断后的未知模型结果不会自动重发。'}
        result = json.loads(row['result'])
        if digest(result) != row['result_sha256']:
            raise Conflict('Planning operation result integrity check failed')
        return result

    @staticmethod
    def _claim(connection, pid, ident, op, request_hash):
        connection.execute('INSERT INTO planning_proposal_operations VALUES (?,?,?,?,?,?,?)',
                           (pid, op, request_hash, ident, 'PROCESSING', None, None))

    @staticmethod
    def _finish(connection, pid, op, result, status='COMPLETED'):
        changed = connection.execute('''UPDATE planning_proposal_operations
            SET status=?,result=?,result_sha256=? WHERE project_id=? AND operation_id=? AND status='PROCESSING' ''',
            (status, canonical(result), digest(result), pid, op)).rowcount
        if changed != 1:
            raise Conflict('Planning operation already finished')

    @classmethod
    def _version(cls, connection, pid, ident, revision=None):
        head = connection.execute('SELECT * FROM planning_proposals WHERE id=? AND project_id=?', (ident, pid)).fetchone()
        if head is None:
            raise NotFound('Task network does not exist in this project')
        revision = head['current_revision'] if revision is None else revision
        row = connection.execute('SELECT * FROM planning_proposal_versions WHERE proposal_id=? AND revision=?',
                                 (ident, revision)).fetchone()
        if row is None:
            raise NotFound('Task network revision does not exist')
        value = cls._checked(row)
        if (value['project_id'], value['proposal_id'], value['revision']) != (pid, ident, revision):
            raise Conflict('Planning record scope check failed')
        return {'id': ident, 'project_id': pid, 'revision': revision, 'current_revision': head['current_revision'],
                'sha256': row['sha256'], 'created_at': row['created_at'], 'payload': value}

    @staticmethod
    def _append(connection, pid, ident, payload):
        revision = payload['revision']
        if revision == 1:
            connection.execute('INSERT INTO planning_proposals VALUES (?,?,?)', (ident, pid, revision))
        else:
            changed = connection.execute('UPDATE planning_proposals SET current_revision=? WHERE id=? AND project_id=? AND current_revision=?',
                                         (revision, ident, pid, revision-1)).rowcount
            if changed != 1:
                raise Conflict('Task network changed; reload current revision')
        connection.execute('INSERT INTO planning_proposal_versions VALUES (?,?,?,?,?)',
                           (ident, revision, canonical(payload), digest(payload), stamp()))

    def source_current(self, pid, source_receipt):
        if digest({k: source_receipt[k] for k in ('documents', 'coverage')}) != source_receipt['sha256']:
            raise Conflict('Source receipt integrity failed')
        for source in source_receipt['documents']:
            for observation in source.get('local_ocr_observations',[]):
                if self.observation_authority is None:return False
                self.observation_authority.resolve(pid,observation['observation_ref'],observation['payload']['bitmap_ref'])
            try:
                document = self.store.document(pid, source['document_id'], source['revision'])
                if (document['current_revision'] != source['revision'] or document['sha256'] != source['raw_sha256'] or
                        raw_hash(document['content']) != source['parsed_sha256']):
                    return False
            except NotFound:
                return False
            local_ref = source.get('local_file_source_ref')
            if local_ref:
                if self.workspaces is None:
                    return False
                from packages.domain.local_workspaces import FileSource
                checks = self.workspaces.check_sources(pid,[FileSource.model_validate(local_ref)])
                if checks[0]['status'] != 'CURRENT':
                    return False
        return True

    def _workspace_current(self, pid, ref):
        if ref is None:
            return True
        if self.workspaces is None:
            raise Conflict('Local workspace service is unavailable')
        binding = self.workspaces.get(pid, ref['id'], ref['revision'])
        return binding['binding_sha256'] == ref['binding_sha256']

    def _current(self, pid, payload):
        try:
            if not self.source_current(pid,payload['source_receipt']) or not self._workspace_current(pid,payload['workspace_ref']):return False
            ref=payload.get('discovery_ref')
            if ref:
                if self.discovery_authority is None:return False
                basis=self.discovery_authority.get(pid,ref['id'],ref['revision'])
                current=self.discovery_authority.get(pid,ref['id'])
                if basis['sha256']!=ref['sha256'] or current['payload']['status']!='CONFIRMED' or not current['source_current']:return False
                # Linking the returned plan adds a session revision without changing
                # requirements. User edits/decisions must invalidate dependent plans.
                fields=['candidate','accepted_claim_ids','accepted_graph_ids','resolutions','confirmation','workspace_ref']
                if any(basis['payload'].get(k)!=current['payload'].get(k) for k in fields):return False
                if basis['payload']['source_receipt']['sha256']!=current['payload']['source_receipt']['sha256']:return False
            return True
        except (Conflict, InvalidInput, NotFound, OSError):
            return False

    def _sources(self,intake):
        documents, coverage = [], []
        for m in intake['snapshot']['materials']:
            text = ''.join(c['content'] for c in m['chunks'])
            document = {'document_id': m['document_id'], 'revision': m['revision'],
                'raw_sha256': m['raw_sha256'], 'parsed_sha256': m['content_sha256'],
                'filename': m['filename_at_snapshot'], 'text_or_selected_chunks': text,
                'chunks': m['chunks'], 'source': m['source']}
            if m['source'].startswith('local-workspace:'):
                match=re.fullmatch(r'local-workspace:([0-9a-f]{64}):([0-9a-f]{64}):r([1-9][0-9]*):([0-9a-f]{64})',m['source'])
                if match is None or self.workspaces is None:
                    raise Conflict('Selected local material authority is unavailable')
                wid,path_hash,revision,raw_sha=match.groups()
                registry=self.workspaces._data(intake['project_id'],wid,'files',path_hash,'versions',f'{int(revision):010d}','metadata.json')
                wrapper=self.workspaces._json(registry); version=wrapper['version']
                if digest(version)!=wrapper['sha256'] or digest(version['path'])!=path_hash or version['sha256']!=raw_sha or raw_sha!=m['raw_sha256']:
                    raise Conflict('Local material source binding integrity failed')
                document['local_file_source_ref']=self.workspaces._ref(version)
            if self.competition_sources is not None and m['source'].startswith('competition-source:'):
                document['competition_capture']=self.competition_sources.material_context(intake['project_id'],
                    {'document_id':m['document_id'],'revision':m['revision'],'sha256':m['raw_sha256']})
            documents.append(document)
            coverage.append({'document_id': m['document_id'], 'text_coverage': m['text_coverage'],
                'omitted_chunk_indexes': m['omitted_chunk_indexes'],
                'original_modal_coverage': m['original_modal_coverage'], 'warnings': m['warnings']})
        result = {'documents': documents, 'coverage': coverage}
        return {**result, 'sha256': digest(result)}

    @staticmethod
    def _validate_sources(candidate, sources):
        by_id = {d['document_id']: d for d in sources['documents']}
        for item in [*candidate.nodes, *candidate.questions]:
            for anchor in item.source_anchors:
                material = by_id.get(anchor.document_id)
                if material is None or material['revision'] != anchor.revision or material['raw_sha256'] != anchor.raw_sha256:
                    raise InvalidInput('Node/question source is outside the selected material revision')
                chunk = next((c for c in material['chunks'] if c['index'] == anchor.chunk_index), None)
                if chunk is None or chunk['sha256'] != anchor.chunk_sha256 or raw_hash(chunk['content']) != anchor.chunk_sha256:
                    raise Conflict('Node/question chunk hash is incorrect')
                if not chunk['char_start'] <= anchor.char_start < anchor.char_end <= chunk['char_end']:
                    raise InvalidInput('Node/question source range is outside the selected chunk')
                quote = chunk['content'][anchor.char_start-chunk['char_start']:anchor.char_end-chunk['char_start']]
                if raw_hash(quote) != anchor.quote_sha256:
                    raise InvalidInput('Node/question quote hash is incorrect')

    @staticmethod
    def _outline(prompt, sources):
        nodes = []
        for number, document in enumerate(sources['documents'], 1):
            chunks = document['chunks']
            for part, chunk in enumerate(chunks, 1):
                text = chunk['content']
                if not text.strip():
                    continue
                headings = [m.group(1).strip() for m in re.finditer(r'^#{1,6}\s+(.+)$', text, re.M)]
                title = headings[0] if headings else document['filename'] + (f' · 第{part}段' if len(chunks) > 1 else '')
                # Selected text remains completely available in the source receipt.
                description = text if len(text) <= 24000 else '请展开来源查看此完整资料段；此视图只建立资料结构，尚未推理项目任务。'
                anchor = {'document_id': document['document_id'], 'revision': document['revision'],
                    'raw_sha256': document['raw_sha256'], 'chunk_index': chunk['index'], 'chunk_sha256': chunk['sha256'],
                    'char_start': chunk['char_start'], 'char_end': chunk['char_end'], 'quote_sha256': chunk['sha256']}
                nodes.append({'id': f'source-{number}-{part}', 'title': title[:300], 'description': description,
                    'kind': 'DELIVERABLE', 'origin': 'SOURCE_OUTLINE', 'source_anchors': [anchor]})
        if not nodes:
            raise InvalidInput('Selected materials contain no readable text; provide text or choose a qualified multimodal provider')
        return PlanningCandidate.model_validate({'title': '资料结构与待澄清目标',
            'summary': '已整理资料结构，尚未进行语义任务拆解。可继续编辑计划。',
            'goals': [prompt], 'nodes': nodes, 'questions': [{
                'id': 'outline-review', 'text': '请确认项目目标、预期交付物及验收方式，并补充尚缺的资料。',
                'reason': '当前只有资料结构，不能据此宣称完整任务链已确定。', 'category': 'DECISION',
                'blocking': True, 'affects_node_ids': [n['id'] for n in nodes],
                'requested_materials': ['项目目标与验收要求', '工作边界、约束和关键依赖']}]})

    @staticmethod
    def flow(candidate, *, confirmed=False, resolved=()):
        ids = {n['id'] for n in candidate['nodes']}
        blocked = set()
        for q in candidate['questions']:
            if q['blocking'] and q['id'] not in resolved:
                blocked.update(q['affects_node_ids'] or ids)
        # Question dependencies block only the affected branch and downstream work.
        while True:
            expanded = blocked | {n['id'] for n in candidate['nodes'] if set(n['depends_on']) & blocked}
            if expanded == blocked:
                break
            blocked = expanded
        nodes, edges = [], []
        for n in candidate['nodes']:
            nodes.append({**n, 'planning_status': 'NEEDS_INPUT' if n['id'] in blocked else 'ACCEPTED_PLAN' if confirmed else 'DRAFT',
                          'execution_status': 'NOT_STARTED'})
            edges.extend({'id': 'dep-'+digest([dep, n['id']])[:20], 'source': dep, 'target': n['id'], 'kind': 'DEPENDENCY'} for dep in n['depends_on'])
            if n['parent_id']:
                edges.append({'id': 'parent-'+n['id'], 'source': n['parent_id'], 'target': n['id'], 'kind': 'CONTAINS'})
        for q in candidate['questions']:
            nodes.append({**q, 'kind': 'QUESTION', 'title': q['text'], 'description': q['reason'],
                          'planning_status': 'RESOLVED' if q['id'] in resolved else 'NEEDS_INPUT'})
            edges.extend({'id': 'question-'+digest([q['id'], target])[:20], 'source': q['id'], 'target': target,
                          'kind': 'AFFECTS'} for target in q['affects_node_ids'])
        return {'nodes': nodes, 'edges': edges, 'node_count': len(nodes), 'edge_count': len(edges),
                'render_limit': None, 'execution_started': False}

    def _view(self, connection, pid, value):
        payload = value['payload']
        answers = [self._checked(r) for r in connection.execute('SELECT payload,sha256 FROM planning_proposal_answers WHERE proposal_id=? ORDER BY id', (value['id'],))]
        resolved = [r['question_id'] for r in (payload.get('confirmation') or {}).get('resolutions', [])]
        current = self._current(pid, payload)
        result = {**value, 'review_status': 'CURRENT' if current else 'NEEDS_REVIEW',
                  'answers': answers, 'flow': self.flow(payload['candidate'], confirmed=payload['status']=='CONFIRMED', resolved=resolved)}
        if payload.get('requirement_coverage') is not None:
            result['coverage'] = coverage.project(payload['requirement_coverage'], payload['candidate'], source_current=current)
        return result

    def get(self, pid, ident, revision=None):
        with self.store.connect() as connection:
            return self._view(connection, pid, self._version(connection, pid, ident, revision))

    def list(self, pid):
        self.store.project(pid)
        with self.store.connect() as connection:
            ids = [r[0] for r in connection.execute('SELECT id FROM planning_proposals WHERE project_id=? ORDER BY id', (pid,))]
        return [self.get(pid, ident) for ident in ids]

    def events(self, pid, ident, after=0, limit=100):
        self.store.project(pid)
        with self.store.connect() as connection:
            return [{'seq': r['seq'], **self._checked(r)} for r in connection.execute('''SELECT seq,payload,sha256
                FROM planning_proposal_events WHERE project_id=? AND proposal_id=? AND seq>? ORDER BY seq LIMIT ?''',
                (pid, ident, after, limit))]

    def operation(self, pid, op):
        self.store.project(pid)
        with self.store.connect() as connection:
            row = connection.execute('SELECT * FROM planning_proposal_operations WHERE project_id=? AND operation_id=?', (pid, op)).fetchone()
            if row is None:
                raise NotFound('Planning operation does not exist')
            return self._replay(connection, pid, op, row['request_sha256'])

    def _book(self, pid, ident, payload):
        candidate = payload['candidate']
        grouped = {k: [] for k in KINDS}
        grouped['GOAL'] = candidate['goals'] or [candidate['summary']]
        grouped['SCOPE'] = [candidate['summary']]
        grouped['DELIVERABLES'] = [n['title']+': '+output for n in candidate['nodes'] for output in n['outputs']]
        grouped['DEPENDENCIES'] = [n['id']+' ← '+', '.join(n['depends_on']) for n in candidate['nodes'] if n['depends_on']]
        grouped['CONSTRAINTS'] = candidate['constraints']
        grouped['ACCEPTANCE'] = [n['title']+': '+a for n in candidate['nodes'] for a in n['acceptance']]
        grouped['NOTES'] = [n['title']+'\n'+n['description'] for n in candidate['nodes']]
        if payload.get('requirement_coverage') is not None:
            snapshot=payload['requirement_coverage']
            grouped['NOTES'] += ['已确认事项承接快照 SHA256：'+digest(snapshot)]
            links={r['claim_id']:r for r in snapshot['claim_links']}
            excluded={r['claim_id']:r for r in snapshot['user_exclusions']}
            for row in snapshot['accepted_claims']:
                claim=row['claim'];link=links.get(claim['id'])
                targets='、'.join(t['node_id'] if t['kind']=='TASK' else '全任务共享约束 '+str(t['constraint_index']) for t in link['targets']) if link else '尚未承接'
                if claim['id'] in excluded:targets='本次用户排除：'+excluded[claim['id']]['reason']
                grouped['NOTES'].append(claim['id']+'：'+claim['text']+'\n'+targets)
        grouped['RISKS'] = candidate['risks'] + [n['title']+': '+risk for n in candidate['nodes'] for risk in n['risks']]
        grouped['OPEN_QUESTIONS'] = [q['text']+'\n原因：'+q['reason'] for q in candidate['questions']]
        grouped['ASSUMPTIONS'] = candidate['assumptions']
        # A taskbook has a stricter per-section limit; report missing capacity
        # instead of dropping large-project nodes or hiding omitted content.
        if any(len(items)>100 for items in grouped.values()):
            return {'status': 'CAPACITY_REVIEW_REQUIRED', 'reason': '任务网络完整保留；任务书单节超过100项，需分册。'}
        sections = [{'section_id': k.lower(), 'kind': k, 'heading': heading,
            'entries': [{'entry_id': k.lower()+'-'+str(i), 'text': text,
                         'origin': 'USER_STATEMENT' if payload['mode']=='manual' else 'MODEL_SUGGESTION', 'source_anchors': []}
                        for i, text in enumerate(grouped[k], 1)]} for k, heading in zip(KINDS, HEADINGS)]
        body = TaskBookInput.model_validate({'idempotency_key': 'proposal-book-'+digest([ident,payload['revision']])[:40],
            'intake_ref': payload['intake_ref'], 'title': candidate['title'], 'sections': sections})
        book = TaskBookService(self.store).save(pid, body)
        return {'taskbook_id': book['id'], 'revision': book['revision'], 'sha256': book['sha256'], 'status': 'DRAFT'}

    async def create(self, pid, body):
        self.store.project(pid)
        request = body.model_dump(exclude={'operation_id'})
        if request.get('coverage_contract') is None: request.pop('coverage_contract', None)
        request_hash = digest(request); ident = str(uuid.uuid4())
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, pid, body.operation_id, request_hash)
            if replay is not None:
                return replay
            self._claim(connection, pid, ident, body.operation_id, request_hash)
            self._event(connection, pid, ident, body.operation_id, 'PLANNING_REQUESTED', mode=body.mode)
        # Never hold a database transaction across a model call or user wait.
        try:
            basis = None
            if body.discovery_ref:
                if self.discovery_authority is None:
                    raise Conflict('Discovery authority is unavailable')
                basis=self.discovery_authority.get(pid,body.discovery_ref.id,body.discovery_ref.revision)
                if (basis['sha256']!=body.discovery_ref.sha256 or
                    basis['current_revision']!=body.discovery_ref.revision or not basis['readiness']['can_plan']):
                    raise Conflict('Task decomposition requires exact current confirmed requirements')
            intake = self.intakes.save(pid, IntakeInput(idempotency_key='planning-'+digest([pid,body.operation_id])[:40],
                materials=body.materials, purpose=body.prompt))
            sources = self._sources(intake)
            workspace = body.workspace_ref.model_dump() if body.workspace_ref else None
            if not self.source_current(pid, sources) or not self._workspace_current(pid, workspace):
                raise Conflict('Planning sources or workspace changed before dispatch')
            execution = {'model_calls': 0, 'status': 'NO_MODEL_CALL', 'mode': body.mode}
            if body.mode == 'opencode':
                if self.transport is None:
                    raise Conflict('OpenCode planning is not configured; select a provider or explicitly edit a manual/source outline')
                prepared=self.intakes.context(pid,intake['id'],intake['revision'])
                prompt=planning_prompt(body.prompt,prepared)
                if body.coverage_contract:
                    seed = coverage.create(pid, basis, {'nodes': [], 'constraints': []}, [])
                    prompt += ('\n[HOST_ACCEPTED_ITEM_COVERAGE]\n'
                        'Return the supplied wrapper schema: candidate and claim_links. Link all accepted items, including all seven categories, '
                        'to actual TASK nodes or candidate.constraints entries applying to ALL_TASKS. '
                        'Do not invent exclusions or approval; omissions remain pending human review.\n' +
                        canonical(coverage.prompt_context(seed, sources)))
                request = {'project_id': pid, 'operation_id': body.operation_id, 'prompt': prompt,
                    'workspace_ref': workspace, 'source_receipt': sources,
                    'provider_profile_ref': body.provider_profile_ref, 'budget': body.budget.model_dump(),
                    'timeout_seconds':body.budget.timeout_seconds,'needs_tools':False,
                    'output_schema': (CoveredModelProposal if body.coverage_contract else PlanningCandidate).model_json_schema()}
                execution = await self.transport.propose(request)
                if execution.get('status') not in {'RETURNED', 'COMPLETED', 'SUCCEEDED', 'PROPOSED'}:
                    raise Conflict('Native planning did not return a complete structured proposal')
                if execution.get('source_receipt_sha256') != sources['sha256']:
                    raise Conflict('Native planning returned a different source receipt')
                wrapped = CoveredModelProposal.model_validate(execution['candidate_json']) if body.coverage_contract else None
                candidate = wrapped.candidate if wrapped is not None else PlanningCandidate.model_validate(execution['candidate_json'])
                for node in candidate.nodes:
                    node.origin = 'MODEL_SUGGESTION'
            elif body.mode == 'source_outline':
                candidate = self._outline(body.prompt, sources)
            else:
                candidate = body.candidate.model_copy(deep=True)
                for node in candidate.nodes:
                    node.origin = 'USER_STATEMENT'
            self._validate_sources(candidate, sources)
            payload = {'schema_version': 'eeas.planning-proposal.v1', 'project_id': pid, 'proposal_id': ident,
                'revision': 1, 'status': 'DRAFT', 'mode': body.mode, 'prompt': body.prompt,
                'candidate': candidate.model_dump(), 'source_receipt': sources,
                'workspace_ref': workspace, 'intake_ref': {'intake_id': intake['id'], 'revision': intake['revision'], 'sha256': intake['sha256']},
                'execution': execution, 'confirmation': None, 'implementation_started': False,
                'discovery_ref':body.discovery_ref.model_dump() if body.discovery_ref else None}
            if body.coverage_contract:
                payload['schema_version'] = 'eeas.planning-proposal.v2'
                links = [r.model_dump() for r in wrapped.claim_links] if body.mode == 'opencode' else []
                payload['requirement_coverage'] = coverage.create(pid, basis, payload['candidate'], links)
            payload['taskbook_ref'] = self._book(pid, ident, payload) if self._current(pid, payload) else {'status': 'SOURCE_REVIEW_REQUIRED'}
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                self._append(connection, pid, ident, payload)
                result = self._view(connection, pid, self._version(connection, pid, ident))
                self._event(connection, pid, ident, body.operation_id, 'PROPOSAL_CREATED', proposal_revision=1, proposal_sha256=result['sha256'], review_status=result['review_status'])
                self._finish(connection, pid, body.operation_id, result)
            return result
        except asyncio.CancelledError:
            result={'id':ident,'status':'UNKNOWN_OUTCOME','operation_id':body.operation_id,
                    'message':'控制器或请求中断，模型结果未知；不会自动重发。'}
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                self._finish(connection,pid,body.operation_id,result,'FAILED')
            raise
        except (Conflict, InvalidInput, NotFound, ValidationError, ValueError) as exc:
            # Record the failure without echoing provider URLs, secrets or raw
            # model text. The caller can inspect a scoped execution separately.
            result = {'id': ident, 'status': 'FAILED', 'operation_id': body.operation_id,
                      'reason': type(exc).__name__, 'message': '规划未完成，请检查资料、配置或结构化结果后使用新操作重试。'}
            with self.store.connect() as connection:
                connection.execute('BEGIN IMMEDIATE')
                self._event(connection, pid, ident, body.operation_id, 'PLANNING_FAILED', reason=result['reason'])
                self._finish(connection, pid, body.operation_id, result, 'FAILED')
            return result

    def edit(self, pid, ident, body):
        request = body.model_dump(exclude={'operation_id'})
        # Preserve every legacy candidate null/default; omit only this new,
        # unused optional field from the original operation hash.
        if request.get('manual_review') is None: request.pop('manual_review', None)
        if request.get('requirement_coverage') is None: request.pop('requirement_coverage', None)
        request_hash = digest({'proposal_id': ident, **request})
        with self.store.connect() as connection:
            replay = self._replay(connection, pid, body.operation_id, request_hash)
            if replay is not None: return replay
        prepared = None
        if body.manual_review is not None:
            if self.manual_changes is None: raise Conflict('Selected workspace authority is unavailable')
            basis = self.get(pid, ident)
            if basis['revision'] != body.expected_revision: raise Conflict('Task network changed before manual adoption')
            prepared = self.manual_changes.prepare_edit(pid, ident, basis, body)
            self.manual_changes.check_edit_sources(pid, prepared)
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, pid, body.operation_id, request_hash)
            if replay is not None:
                return replay
            old = self._version(connection, pid, ident)
            if old['current_revision'] != body.expected_revision:
                raise Conflict('Task network changed; reload current revision')
            if not self._current(pid, old['payload']):
                raise Conflict('Source changed; create a new proposal from current materials')
            self._validate_sources(body.candidate, old['payload']['source_receipt'])
            continuity = self._decision_continuity(connection, pid, old, body.candidate.model_dump())
            payload = {**old['payload'], 'revision': old['revision']+1, 'status': 'DRAFT', 'mode': 'manual',
                       'candidate': body.candidate.model_dump(), 'change_reason': body.change_reason,
                       'confirmation': None, 'decision_continuity': continuity,
                       'taskbook_ref': {'status':'REVISION_PENDING'}}
            if body.requirement_coverage is not None:
                if old['payload'].get('requirement_coverage') is None:
                    raise InvalidInput('Legacy proposals do not acquire coverage through an implicit edit')
                payload['requirement_coverage'] = coverage.edit(old['payload']['requirement_coverage'],
                    body.requirement_coverage, payload['candidate'])
            if prepared is not None:
                if prepared['basis_proposal_ref'] != {k: old[k] for k in ('id', 'revision', 'sha256')}:
                    raise Conflict('Manual adoption no longer owns the exact proposal transition')
                payload['manual_inheritance'] = [*old['payload'].get('manual_inheritance', []), prepared]
            self._claim(connection, pid, ident, body.operation_id, request_hash)
            self._append(connection, pid, ident, payload)
            result = self._view(connection, pid, self._version(connection, pid, ident))
            self._event(connection, pid, ident, body.operation_id, 'PROPOSAL_REVISED', proposal_revision=result['revision'], change_reason=body.change_reason)
            self._finish(connection, pid, body.operation_id, result)
            return result

    def answer(self, pid, ident, body):
        request_hash = digest({'proposal_id': ident, **body.model_dump(exclude={'operation_id'})})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, pid, body.operation_id, request_hash)
            if replay is not None:
                return replay
            proposal = self._version(connection, pid, ident)
            if proposal['current_revision'] != body.expected_revision:
                raise Conflict('Answer needs the current proposal revision')
            question = next((q for q in proposal['payload']['candidate']['questions'] if q['id']==body.question_id), None)
            if question is None:
                raise NotFound('Question does not exist in this task network')
            answer = {'answer_id': str(uuid.uuid4()), 'proposal_id': ident, 'proposal_revision': proposal['revision'],
                'question_id': body.question_id, 'question_sha256': digest(question), 'text': body.text,
                'created_at': stamp(), 'accepted': False, 'implementation_started': False}
            self._claim(connection, pid, ident, body.operation_id, request_hash)
            connection.execute('INSERT INTO planning_proposal_answers VALUES (?,?,?,?,?,?)',
                (answer['answer_id'], ident, proposal['revision'], body.question_id, canonical(answer), digest(answer)))
            self._event(connection, pid, ident, body.operation_id, 'ANSWER_RECORDED', answer_id=answer['answer_id'], question_id=body.question_id)
            self._finish(connection, pid, body.operation_id, answer)
            return answer

    @staticmethod
    def _plan_ref(value):
        return {key:value[key] for key in ('project_id', 'id', 'revision', 'sha256')}

    def _decision_version(self, connection, pid, ident, ref, maximum):
        if (not isinstance(ref, dict) or set(ref) != {'project_id','id','revision','sha256'}
                or ref.get('project_id') != pid or ref.get('id') != ident
                or type(ref.get('revision')) is not int or not 1 <= ref['revision'] <= maximum):
            raise Conflict('Retained decision has an invalid proposal scope')
        value = self._version(connection, pid, ident, ref['revision'])
        if value['sha256'] != ref['sha256']:
            raise Conflict('Retained decision proposal hash changed')
        return value

    def _question_scope_revisions(self, connection, pid, value):
        """Bounded current-question epochs; legacy unapproved answers need fresh review."""
        questions = {q['id'] for q in value['payload']['candidate']['questions']}
        context = value['payload'].get('decision_continuity') or {}
        epochs = context.get('question_scope_revisions')
        if epochs is not None:
            if (not isinstance(epochs, dict) or set(epochs) != questions
                    or any(type(v) is not int or not 1 <= v <= value['revision'] for v in epochs.values())):
                raise Conflict('Question structural scope revisions are invalid')
            return dict(epochs)
        # Legacy history has no exact epochs. Its current snapshot is the
        # conservative baseline; only exact already-approved records can use
        # their verified approval snapshot instead of an old answer revision.
        return {qid:value['revision'] for qid in questions}

    def _resolution(self, connection, pid, current, raw, basis_cache=None, scope_revisions=None, validated_scope_basis=None):
        """Read saved answer identity and its original structural basis again."""
        try:
            choice = QuestionResolution.model_validate({k:raw[k] for k in
                ('question_id','mode','answer_id','assumption','reason') if k in raw})
        except (ValidationError, TypeError, KeyError) as exc:
            raise Conflict('Decision record is invalid') from exc
        candidate = current['payload']['candidate']
        question = next((q for q in candidate['questions'] if q['id'] == choice.question_id), None)
        if question is None:
            raise InvalidInput('Resolution names an unknown question')
        resolution = choice.model_dump()
        if choice.answer_id:
            row = connection.execute('SELECT * FROM planning_proposal_answers WHERE id=? AND proposal_id=? AND question_id=?',
                (choice.answer_id, current['id'], choice.question_id)).fetchone()
            if row is None:
                raise InvalidInput('Resolution answer is outside this question/proposal')
            answer = self._checked(row)
            if (answer.get('answer_id') != row['id'] or answer.get('proposal_id') != current['id']
                    or answer.get('question_id') != choice.question_id
                    or type(answer.get('proposal_revision')) is not int
                    or answer['proposal_revision'] != row['proposal_revision']
                    or not 1 <= answer['proposal_revision'] <= current['revision']):
                raise Conflict('Saved answer identity or revision differs')
            epochs = (self._question_scope_revisions(connection, pid, current)
                      if scope_revisions is None else scope_revisions)
            usable_revision = answer['proposal_revision']
            if validated_scope_basis is not None:
                if (validated_scope_basis['project_id'] != pid or validated_scope_basis['id'] != current['id']
                        or not 1 <= validated_scope_basis['revision'] <= current['revision']):
                    raise Conflict('Approved answer structural basis differs')
                usable_revision = validated_scope_basis['revision']
            if usable_revision < epochs[choice.question_id]:
                raise Conflict('Saved answer predates a structural scope change; answer the current version')
            # Request-local and bounded: repeated decisions often share one large source snapshot.
            basis_cache = {} if basis_cache is None else basis_cache
            cache_key = (pid, current['id'], current['revision'], current['sha256'], answer['proposal_revision'])
            if cache_key not in basis_cache:
                original = (current if answer['proposal_revision'] == current['revision'] else
                            self._version(connection, pid, current['id'], answer['proposal_revision']))
                if len(basis_cache) >= 4:
                    basis_cache.pop(next(iter(basis_cache)))
                basis_cache[cache_key] = (original, structural_impact(original['payload']['candidate'], candidate))
            original, impact = basis_cache[cache_key]
            original_question = next((q for q in original['payload']['candidate']['questions']
                                      if q['id'] == choice.question_id), None)
            scope_basis = original if validated_scope_basis is None else validated_scope_basis
            scope_impact = impact if validated_scope_basis is None else structural_impact(scope_basis['payload']['candidate'], candidate)
            if (original_question is None or answer.get('question_sha256') != digest(original_question)
                    or answer['question_sha256'] != digest(question)
                    or original['payload']['source_receipt']['sha256'] != current['payload']['source_receipt']['sha256']
                    or scope_basis['payload']['source_receipt']['sha256'] != current['payload']['source_receipt']['sha256']
                    or not question_unchanged(scope_basis['payload']['candidate'], candidate, choice.question_id, scope_impact)):
                raise Conflict('Saved answer question or structural scope changed; answer the current version')
            resolution['answer_text'] = answer['text']
            resolution['answer_sha256'] = row['sha256']
        return resolution

    def _approval_basis(self, connection, pid, value):
        confirmation = value['payload'].get('confirmation')
        if (value['payload']['status'] != 'CONFIRMED' or not isinstance(confirmation, dict)
                or confirmation.get('user_confirmed') is not True
                or confirmation.get('execution_authorized') is not False
                or type(confirmation.get('accepted_revision')) is not int
                or confirmation['accepted_revision'] != value['revision']-1):
            raise Conflict('Retained provenance is not an explicitly confirmed immutable version')
        basis = self._version(connection, pid, value['id'], confirmation['accepted_revision'])
        if (basis['sha256'] != confirmation.get('accepted_sha256')
                or basis['payload']['candidate'] != value['payload']['candidate']):
            raise Conflict('Approved decision basis differs from its recorded plan')
        return basis

    def _verified_retained_record(self, connection, pid, current, record, approved_cache,
                                  basis_cache, scope_revisions):
        if not isinstance(record, dict) or set(record) != {'resolution','provenance'}:
            raise Conflict('Retained decision record is invalid')
        provenance = record['provenance']
        if not isinstance(provenance, dict) or set(provenance) != {'proposal_ref','question_sha256','resolution_sha256'}:
            raise Conflict('Retained decision provenance is invalid')
        ref = provenance['proposal_ref']
        if not isinstance(ref, dict):
            raise Conflict('Retained decision has no proposal reference')
        key = canonical(ref)
        if key not in approved_cache:
            approved = self._decision_version(connection, pid, current['id'], ref, current['revision']-1)
            self._approval_basis(connection, pid, approved)
            raw_resolutions = approved['payload']['confirmation'].get('resolutions', [])
            if not isinstance(raw_resolutions, list):
                raise Conflict('Approved resolutions are invalid')
            by_question = {r['question_id']:r for r in raw_resolutions}
            if len(by_question) != len(raw_resolutions):
                raise Conflict('Approved resolutions have duplicate questions')
            if len(approved_cache) >= 4:
                approved_cache.pop(next(iter(approved_cache)))
            approved_cache[key] = (approved, by_question,
                self._question_scope_revisions(connection, pid, approved))
        approved, by_question, approved_epochs = approved_cache[key]
        ident = record['resolution'].get('question_id') if isinstance(record['resolution'],dict) else None
        raw = by_question.get(ident)
        if raw is None:
            raise Conflict('Retained decision is absent from its approval')
        resolution = self._resolution(connection, pid, approved, raw, basis_cache, approved_epochs, approved)
        question = next(q for q in approved['payload']['candidate']['questions'] if q['id'] == ident)
        expected = {'resolution':resolution, 'provenance':{'proposal_ref':self._plan_ref(approved),
            'question_sha256':digest(question), 'resolution_sha256':digest(resolution)}}
        # A retained item already points to its original explicit approval. Reject
        # a later confirmation masquerading as that origin; never scan old chains.
        origin_context = approved['payload'].get('decision_continuity') or {}
        if any(r.get('resolution',{}).get('question_id') == ident
               for r in origin_context.get('retained_decisions', [])):
            raise Conflict('Retained decision reference is not its original approval')
        if (raw != resolution or record != expected
                or not question_unchanged(approved['payload']['candidate'], current['payload']['candidate'], ident)
                or self._resolution(connection, pid, current, resolution, basis_cache, scope_revisions, approved) != resolution):
            raise Conflict('Retained decision has no valid unchanged original approval scope')
        return record

    def _approved_decisions(self, connection, pid, value):
        basis = self._approval_basis(connection, pid, value)
        epochs = self._question_scope_revisions(connection, pid, value)
        context = value['payload'].get('decision_continuity')
        prior = {}
        if context is not None:
            if (not isinstance(context, dict) or context.get('schema') != CONTINUITY_SCHEMA
                    or context.get('basis_ref') != self._plan_ref(basis)
                    or not isinstance(context.get('retained_decisions'), list)):
                raise Conflict('Confirmed continuity basis is invalid')
            approved_cache, basis_cache = {}, {}
            for record in context['retained_decisions']:
                checked = self._verified_retained_record(connection, pid, value, record,
                    approved_cache, basis_cache, epochs)
                ident = checked['resolution']['question_id']
                if ident in prior:
                    raise Conflict('Confirmed continuity repeats a question')
                prior[ident] = checked
        records, basis_cache, seen = [], {}, set()
        for raw in value['payload']['confirmation'].get('resolutions', []):
            resolution = self._resolution(connection, pid, value, raw, basis_cache, epochs, value)
            ident = resolution['question_id']
            if raw != resolution or ident in seen:
                raise Conflict('Approved decision content or identity differs')
            seen.add(ident)
            question = next(q for q in value['payload']['candidate']['questions'] if q['id'] == ident)
            record = prior.get(ident)
            if record is not None:
                if record['resolution'] != resolution:
                    raise Conflict('Retained approval differs from the confirmed decision')
            else:
                record = {'resolution':resolution, 'provenance':{
                    'proposal_ref':self._plan_ref(value), 'question_sha256':digest(question),
                    'resolution_sha256':digest(resolution)}}
            records.append(record)
        if set(prior) - seen:
            raise Conflict('Confirmed continuity contains an unconfirmed decision')
        return records

    def _retained_decisions(self, connection, pid, value):
        if value['payload']['status'] == 'CONFIRMED':
            return self._approved_decisions(connection, pid, value), []
        context = value['payload'].get('decision_continuity')
        if context is None:
            return [], []
        if not isinstance(context, dict) or context.get('schema') != CONTINUITY_SCHEMA:
            raise Conflict('Retained decision context is invalid')
        basis = self._decision_version(connection, pid, value['id'], context.get('basis_ref'), value['revision']-1)
        if basis['revision'] != value['revision']-1:
            raise Conflict('Retained decision context does not name its direct predecessor')
        invalidated = context.get('invalidated_question_ids')
        records = context.get('retained_decisions')
        if (not isinstance(invalidated, list) or any(not isinstance(i,str) for i in invalidated)
                or len(invalidated) != len(set(invalidated)) or not isinstance(records, list)):
            raise Conflict('Retained decision list is invalid')
        checked, seen, approved_cache, basis_cache = [], set(), {}, {}
        epochs = self._question_scope_revisions(connection, pid, value)
        for record in records:
            valid = self._verified_retained_record(connection, pid, value, record,
                approved_cache, basis_cache, epochs)
            ident = valid['resolution']['question_id']
            if ident in seen or ident in invalidated:
                raise Conflict('Retained decision was already invalidated')
            seen.add(ident)
            checked.append(valid)
        return checked, invalidated

    def _decision_continuity(self, connection, pid, before, candidate):
        records, previous_invalidated = self._retained_decisions(connection, pid, before)
        impact = structural_impact(before['payload']['candidate'], candidate)
        retained = [r for r in records if question_unchanged(before['payload']['candidate'], candidate,
                                                            r['resolution']['question_id'], impact)]
        retained_ids = {r['resolution']['question_id'] for r in retained}
        invalidated = set(previous_invalidated) | {r['resolution']['question_id'] for r in records
                                                  if r['resolution']['question_id'] not in retained_ids}
        old_epochs = self._question_scope_revisions(connection, pid, before)
        epochs = {q['id']:(old_epochs[q['id']] if q['id'] in old_epochs
                  and question_unchanged(before['payload']['candidate'], candidate, q['id'], impact)
                  else before['revision']+1) for q in candidate['questions']}
        return {'schema':CONTINUITY_SCHEMA, 'basis_ref':self._plan_ref(before),
                'retained_decisions':retained, 'invalidated_question_ids':sorted(invalidated),
                'question_scope_revisions':epochs, 'structural_impact':impact}

    def confirm(self, pid, ident, body):
        request = body.model_dump(exclude={'operation_id'})
        for key in ('coverage_sha256', 'confirmed_exclusion_ids'):
            if request.get(key) is None: request.pop(key, None)
        request_hash = digest({'proposal_id': ident, **request})
        with self.store.connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            replay = self._replay(connection, pid, body.operation_id, request_hash)
            if replay is not None:
                return replay
            old = self._version(connection, pid, ident)
            if old['current_revision'] != body.expected_revision or old['sha256'] != body.proposal_sha256:
                raise Conflict('Confirmation needs the exact current proposal revision and SHA')
            if not self._current(pid, old['payload']):
                raise Conflict('Sources changed; review before confirming')
            reviewed = self._view(connection, pid, old)
            coverage.require_clear(reviewed)
            accepted = reviewed.get('coverage')
            if accepted is not None:
                if (body.coverage_sha256 != accepted['snapshot_sha256'] or
                        body.confirmed_exclusion_ids != [r['claim_id'] for r in accepted['user_exclusions']]):
                    raise Conflict('Confirm this exact coverage snapshot and explicit exclusion list')
            elif body.coverage_sha256 is not None or body.confirmed_exclusion_ids is not None:
                raise InvalidInput('Legacy confirmation does not accept coverage authority')
            if old['payload']['status'] == 'CONFIRMED' and not body.resolutions:
                raise Conflict('An explicit new decision is required to continue a confirmed plan')
            candidate = old['payload']['candidate']; questions = {q['id']: q for q in candidate['questions']}
            if not any(n['kind']=='TASK' for n in candidate['nodes']):
                raise Conflict('Confirmation needs at least one actual planned task')
            continuity = self._decision_continuity(connection, pid, old, candidate)
            merged = {r['resolution']['question_id']:r['resolution'] for r in continuity['retained_decisions']}
            previous = (dict(merged) if old['payload']['status'] != 'CONFIRMED' else
                        {r['question_id']:r for r in old['payload']['confirmation']['resolutions']})
            basis_cache, approved_scope_cache = {}, {}
            epochs = self._question_scope_revisions(connection, pid, old)
            retained = {r['resolution']['question_id']:r for r in continuity['retained_decisions']}
            for ref in body.resolutions:
                approved_scope = None
                prior_record = retained.get(ref.question_id)
                if (prior_record is not None and ref.answer_id is not None
                        and prior_record['resolution'].get('answer_id') == ref.answer_id):
                    approval_ref = prior_record['provenance']['proposal_ref']
                    key = canonical(approval_ref)
                    if key not in approved_scope_cache:
                        if len(approved_scope_cache) >= 4:
                            approved_scope_cache.pop(next(iter(approved_scope_cache)))
                        approved_scope_cache[key] = self._decision_version(connection, pid, ident, approval_ref, old['revision'])
                    approved_scope = approved_scope_cache[key]
                merged[ref.question_id] = self._resolution(connection, pid, old, ref.model_dump(),
                    basis_cache, epochs, approved_scope)
            if old['payload']['status'] == 'CONFIRMED':
                if merged == previous:
                    raise Conflict('No decision changed; the confirmed plan already records these choices')
            changed_decisions = {qid for qid in set(previous) | set(merged) if previous.get(qid) != merged.get(qid)}
            continuity['structural_impact'] = include_decision_impact(
                continuity['structural_impact'], candidate, changed_decisions)
            resolutions = [merged[q['id']] for q in candidate['questions'] if q['id'] in merged]
            continuity['retained_decisions'] = [r for r in continuity['retained_decisions']
                                               if r['resolution']['question_id'] not in changed_decisions]
            continuity['invalidated_question_ids'] = [q for q in continuity['invalidated_question_ids'] if q not in merged]
            missing = {q['id'] for q in questions.values() if q['blocking']} - set(merged)
            if missing:
                raise Conflict('Blocking questions need explicit accepted answers or assumptions: '+', '.join(sorted(missing)))
            missing_acceptance = [n['id'] for n in candidate['nodes'] if n['kind']=='TASK' and (not n['outputs'] or not n['acceptance'])]
            if missing_acceptance:
                raise Conflict('Task nodes need outputs and acceptance before confirmation: '+', '.join(missing_acceptance))
            if old['payload']['mode']=='source_outline':
                raise Conflict('Source outline is not a task plan; edit actual tasks or request model decomposition first')
            payload = {**old['payload'], 'revision': old['revision']+1, 'status': 'CONFIRMED',
                'decision_continuity':continuity,
                'confirmation': {'accepted_revision': old['revision'], 'accepted_sha256': old['sha256'],
                                 'resolutions': resolutions, 'created_at': stamp(), 'user_confirmed': True,
                                 'execution_authorized': False}, 'implementation_started': False}
            if accepted is not None:
                payload['confirmation'].update(coverage_sha256=accepted['snapshot_sha256'],
                    confirmed_exclusion_ids=body.confirmed_exclusion_ids)
            self._claim(connection, pid, ident, body.operation_id, request_hash)
            self._append(connection, pid, ident, payload)
            result = self._view(connection, pid, self._version(connection, pid, ident))
            self._event(connection, pid, ident, body.operation_id, 'PLAN_CONFIRMED', proposal_revision=result['revision'], execution_authorized=False)
            self._finish(connection, pid, body.operation_id, result)
            return result

    def harness(self, pid, ident, revision=None):
        value = self.get(pid, ident, revision); payload = value['payload']; candidate = payload['candidate']
        from packages.planning.harness_context import context_files as split_context
        prompts = [];context_files=[]
        claim_coverage = value.get('coverage')
        if claim_coverage is not None:
            context_files.extend(split_context('accepted-items.json', canonical(payload['requirement_coverage'])+'\n'))
        ref=payload.get('discovery_ref')
        if ref:
            if self.discovery_authority is None:raise Conflict('Discovery context authority is unavailable')
            view=self.discovery_authority.get(pid,ref['id'],ref['revision'])
            if view['sha256']!=ref['sha256']:raise Conflict('Harness requirements context changed')
            snapshot={'discovery_ref':ref,'payload':view['payload'],
                'answers':view['payload'].get('answers_snapshot',view['answers']),
                'qualification':'CONFIRMED_REQUIREMENTS_CONTEXT_NOT_EXECUTION_AUTHORIZATION'}
            context=canonical(snapshot)+'\n'
            context_files.extend(split_context('discovery-context.json',context))
            paragraphs=['# 已确认的需求与任务背景','',view['payload']['candidate']['summary'],'','## 已接受要求']
            for claim in view['payload']['candidate']['claims']:
                if claim['id'] in view['payload']['accepted_claim_ids']:
                    paragraphs+=['### '+claim['id'],claim['text'],'',*['- '+a for a in claim['acceptance']],'']
            paragraphs+=['## 竞品、图与来源','完整观察状态、图模型、原文锚点、回答与裁决见 discovery-context.json。',
                '其中模型建议/未知仍需审阅；用户确认要求不等于批准工程执行。','']
            context='\n'.join(paragraphs)
            context_files.extend(split_context('discovery-context.md',context))
        resolutions = {r['question_id']:r for r in (payload['confirmation'] or {}).get('resolutions', [])}
        for node in candidate['nodes']:
            affected = [q for q in candidate['questions'] if not q['affects_node_ids'] or node['id'] in q['affects_node_ids']]
            lines = [f'# {node["title"]}', '', f'任务节点：{node["id"]}', f'计划版本：{ident} / {value["revision"]} / {value["sha256"]}',
                '', '## 目标', node['description'], '', '## 计划背景原文快照',
                '以下为计划背景原文快照。本次导出未复核其中关于执行、产物或验收的陈述；'
                '当前计划状态以本文件版本、确认范围及实际执行/验收记录为准。',
                '', candidate['summary'], '', '## 前置依赖',
                *[f'- {dep}' for dep in node['depends_on']], '', '## 输入', *['- '+t for t in node['inputs']],
                '', '## 交付物', *['- '+t for t in node['outputs']], '', '## 验收', *['- '+t for t in node['acceptance']],
                '', '## 约束与风险', *['- '+t for t in candidate['constraints']+candidate['risks']+node['risks']],
                '', '## 能力需求', *['- '+t for t in node['capabilities']], '', '## 来源',
                *['- '+canonical(a) for a in node['source_anchors']], '', '## 问答与决定']
            for q in affected:
                lines += ['- '+q['text'], '  '+canonical(resolutions.get(q['id'], {'status':'UNRESOLVED','reason':q['reason']}))]
            lines += ['', '## 执行与恢复规则',
                '先检查来源版本、依赖产物及权限；需要的能力缺失时提出可审查的安装/适配方案。',
                '按预算调用工具，持续报告真实进度；发现冲突、资料不足或不合理之处先提出具体问题。',
                '产物可交互、内容易懂；完成后按验收项验证并保存证据。Agent 返回不等于验收通过。',
                '此提示词只接受任务计划，未批准工具权限、模型费用或自动执行。中断后从已验证的产物和记录恢复。', '']
            if context_files:lines+=['## 共用需求背景','先读取同目录 discovery-context.md 和 discovery-context.json；如为分卷索引，按列出顺序读取全部分卷并核验完整SHA。核对本任务与已确认要求、图及来源；缺口回到待确认流程。','']
            if claim_coverage is not None:
                linked = [r['claim_id'] for r in claim_coverage['claim_links'] if any(
                    t['kind']=='TASK' and t['node_id']==node['id'] for t in r['targets'])]
                shared = sorted({t['constraint_index'] for r in claim_coverage['claim_links'] for t in r['targets'] if t['kind']=='SHARED_CONSTRAINT'})
                lines += ['## 已确认事项承接', '本任务事项：'+(', '.join(linked) or '无直接映射'),
                    '全任务共享约束索引：'+canonical(shared), '共用快照：accepted-items.json；SHA256：'+claim_coverage['snapshot_sha256'], '']
            text='\n'.join(lines)
            prompts.append({'node_id':node['id'],'filename':'task-'+node['id']+'.md','content':text,'sha256':raw_hash(text)})
        return {'schema_version':'eeas.planning-harness.v1','project_id':pid,
                'proposal_ref':{'id':ident,'revision':value['revision'],'sha256':value['sha256']},
                'status':'CONFIRMED_PLAN' if payload['status']=='CONFIRMED' else 'DRAFT_NOT_EXPORTABLE',
                'review_status':value['review_status'],'source_receipt_sha256':payload['source_receipt']['sha256'],
                'nodes':candidate['nodes'],'questions':candidate['questions'],'prompts':prompts,'context_files':context_files,
                **({'requirement_coverage':payload['requirement_coverage'], 'coverage_sha256':claim_coverage['snapshot_sha256']} if claim_coverage is not None else {}),
                **({'manual_inheritance':[{'capture_ref':m['capture_ref'],'decisions_sha256':digest(m['decisions']),
                    'targets':m['targets'],'coverage':m['coverage']} for m in payload['manual_inheritance']]}
                    if payload.get('manual_inheritance') else {}),
                'execution_started':False,'execution_authorized':False}

    def pending(self, pid, ident, revision=None):
        value = self.get(pid,ident,revision); payload = value['payload']
        resolutions = {r['question_id']:r for r in (payload['confirmation'] or {}).get('resolutions', [])}
        rows = []
        for question in payload['candidate']['questions']:
            answers = [a for a in value['answers'] if a['question_id']==question['id'] and a['question_sha256']==digest(question)]
            resolution = resolutions.get(question['id'])
            rows.append({**question, 'answers':answers, 'resolution':resolution,
                         'status':'RESOLVED' if resolution else 'ANSWERED_PENDING_ACCEPTANCE' if answers else 'AWAITING_ANSWER'})
        result = {'schema_version':'eeas.planning-pending.v1','project_id':pid,
            'proposal_ref':{'id':ident,'revision':value['revision'],'sha256':value['sha256']},
            'title':payload['candidate']['title'],'plan_status':payload['status'],
            'review_status':value['review_status'],'source_receipt_sha256':payload['source_receipt']['sha256'],
            'questions':rows,'pending_count':sum(q['status']!='RESOLVED' for q in rows),
            'answer_url':f'/api/projects/{pid}/planning/proposals/{ident}/answers',
            'implementation_started':False}
        if value.get('coverage') is not None:
            result['requirement_coverage']=value['coverage']
        return {**result,'sha256':digest(result)}

    def pending_markdown(self,pid,ident,revision=None):
        value = self.pending(pid,ident,revision)
        lines = ['# 待确认事项 — '+value['title'],'',
            '计划状态：'+value['plan_status']+'；来源状态：'+value['review_status'],
            '计划引用：'+canonical(value['proposal_ref']),'',
            '未回答或未接受的事项保留在页面，可继续补充资料与回答。此文件不代表用户已确认。','']
        for q in value['questions']:
            lines += ['## '+q['id']+' · '+q['text'],'',
                '状态：'+q['status'],'原因：'+q['reason'],
                '影响节点：'+(', '.join(q['affects_node_ids']) or '整个计划'),
                '是否阻塞：'+('是' if q['blocking'] else '否'),'','### 需要补充的资料',
                *['- '+t for t in q['requested_materials']],'','### 已有回答',
                *['- '+a['text']+'（已记录，接受状态见下文）' for a in q['answers']],
                '', '### 明确决议', canonical(q['resolution']) if q['resolution'] else '尚未接受答案或假设。',
                '', '### 来源', *['- '+canonical(a) for a in q['source_anchors']], '']
        if not value['questions']:
            lines += ['当前版本没有列出的待确认问题；这不等于资料完整性已独立验证。','']
        if value.get('requirement_coverage') is not None:
            state=value['requirement_coverage']
            lines += ['## 已确认事项承接', '结构核对：'+state['status'],
                '尚未承接：'+(', '.join(state['missing_claim_ids']) or '无'),
                '快照 SHA256：'+state['snapshot_sha256'], '']
        return '\n'.join(lines)

    def taskbook(self,pid,ident,revision=None):
        value=self.get(pid,ident,revision)
        if value['review_status']!='CURRENT':
            raise Conflict('Review changed sources before generating a new taskbook')
        return self._book(pid,ident,value['payload'])
