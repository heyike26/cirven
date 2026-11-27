"""Explicit saved-prompt changes into original proposal fields, never an AI merge."""
from copy import deepcopy
from contextlib import contextmanager
from contextvars import ContextVar
import difflib
from itertools import islice
import os
from pathlib import Path
import re
import uuid

from packages.domain.local_materials import LocalMaterials
from packages.domain.local_workspaces import FileSource, canonical, digest, relative, sha
from packages.domain.paths import resolve_data_path
from packages.domain.planning_schema import ManualCaptureInput, ManualCaptureRef, ManualExportRef, HarnessExportInput
from packages.domain.store import Conflict, InvalidInput, NotFound

MAX_FILES, MAX_BYTES, MAX_TOTAL = 32, 1024 * 1024, 8 * 1024 * 1024
MAX_LINES, MAX_WORK, MAX_HUNKS, MAX_DIFF, MAX_OPERATIONS = 2000, 2_000_000, 200, 256 * 1024, 200
MAX_METADATA=16*1024*1024
_READ_PROOF_SCOPE = ContextVar("cirven_manual_read_proof_scope", default=None)


class PendingExport(Conflict):
    def __init__(self,pending):
        super().__init__('Registered export has no completed result; inspect its original operation')
        self.pending=pending


class ManualChangesConflict(Conflict):
    def __init__(self, code, detail, message='旧提示词有待核人工修改；请在原任务中明确采用、改写或舍弃。'):
        super().__init__(message)
        self.code, self.detail = code, {'manual_changes': detail}


def conflict_response(error):
    return {'error': str(error), 'code': error.code, 'detail': error.detail}


def ref(value):
    return {key: value[key] for key in ('id', 'revision', 'sha256')}


def wording(text):
    # Formatting only: never include SequenceMatcher context as adopted text.
    return '\n'.join(line[2:] if line.startswith('- ') else line for line in text.splitlines()).strip()


class HarnessManual:
    def __init__(self, proposals, workspaces):
        self.proposals, self.ws, self.store = proposals, workspaces, proposals.store

    @staticmethod
    def read_budget():
        return {'limit_bytes':MAX_TOTAL,'charged_bytes':0,'known_raw_bytes':0,'failed_read_upper_bound':0,
                'scope':'MANUAL_REGISTERED_HARNESS_INPUT_PROOF_NOT_GENERATION_OUTPUT_IO','reads':[],
                'metadata':{'limit_bytes':MAX_METADATA,'charged_bytes':0,'failed_read_upper_bound':0,'reads':[]},
                'combined_input_upper_bound_bytes':MAX_TOTAL+MAX_METADATA}

    @contextmanager
    def proof_scope(self, pid, workspace, budget):
        """Share protected proof bytes only inside this owning synchronous chain.

        Linux keeps fresh reads and the original limit. It is not a substitute
        for mandatory Windows write/delete denial. Runtime handles never enter
        the serializable budget or its audit copies.
        """
        current = _READ_PROOF_SCOPE.get()
        binding = self._binding(pid, workspace)
        if current is not None:
            if current['authority'] is not self or current['budget'] is not budget or current['binding'] != binding:
                raise Conflict('Read-proof operation/workspace budget scope changed')
            yield
            return
        native = self.ws._native_handles()
        if native is None:
            yield
            return
        with native.read_proof_scope(binding, private_check=self.ws._private) as lease:
            token = _READ_PROOF_SCOPE.set({'authority': self, 'budget': budget, 'binding': binding, 'lease': lease})
            try: yield
            finally: _READ_PROOF_SCOPE.reset(token)

    @staticmethod
    def _charge_metadata(budget,amount,path,*,known=True):
        value=budget['metadata']
        if amount<0 or value['charged_bytes']+amount>MAX_METADATA:raise InvalidInput('Manual metadata aggregate budget exceeded')
        value['charged_bytes']+=amount
        if not known:value['failed_read_upper_bound']+=amount
        value['reads'].append({'record':str(path),'bytes':amount,'exact':known})

    def _metadata(self,path,budget):
        remaining=MAX_METADATA-budget['metadata']['charged_bytes']
        if remaining<1:raise InvalidInput('Manual metadata budget exhausted')
        cap=min(4*1024*1024,remaining-1);receipt={'metadata_bytes_read':0}
        try:value=self.ws._bounded_json(path,max_bytes=cap,_read_receipt=receipt)
        except Exception:
            self._charge_metadata(budget,cap+1,path.name,known=False);raise
        self._charge_metadata(budget,receipt['metadata_bytes_read'],path.name)
        return value

    @staticmethod
    def _charge(budget,amount,kind,path,*,known=True):
        if amount<0 or budget['charged_bytes']+amount>MAX_TOTAL:raise InvalidInput('Manual aggregate raw-byte budget exceeded')
        budget['charged_bytes']+=amount
        budget['known_raw_bytes'if known else'failed_read_upper_bound']+=amount
        budget['reads'].append({'path':path,'kind':kind,'bytes':amount,'exact':known})

    def _live(self,binding,path,budget):
        scope = _READ_PROOF_SCOPE.get()
        if scope is not None and scope['authority'] is self and scope['budget'] is not budget:
            raise Conflict('Read-proof operation cannot replace its shared budget')
        if scope is not None and scope['authority'] is self and scope['budget'] is budget:
            if scope['binding'] != binding: raise Conflict('Read-proof workspace scope changed')
            lease = scope['lease']; cached = lease.contains(path)
            remaining = MAX_TOTAL-budget['charged_bytes']
            if not cached and remaining < 1: raise InvalidInput('Manual aggregate raw-byte budget exhausted')
            cap = MAX_BYTES if cached else min(MAX_BYTES, remaining-1)
            try: raw, reused = lease.read_regular(path, max_bytes=cap)
            except Exception:
                self._charge(budget,0 if cached else cap+1,'FAILED_HELD_READ_PROOF' if cached else 'FAILED_SAVED_READ_RESERVED',path,known=False)
                raise
            if reused != cached: raise Conflict('Read-proof ownership changed during lookup')
            self._charge(budget,0 if reused else len(raw),'HELD_READ_PROOF_REUSED' if reused else 'SAVED_READ',path)
            if reused:budget['reads'][-1]['verified_byte_length']=len(raw)
            return raw
        remaining=MAX_TOTAL-budget['charged_bytes']
        if remaining<1:raise InvalidInput('Manual aggregate raw-byte budget exhausted')
        cap=min(MAX_BYTES,remaining-1)
        try:raw=self.ws._raw(binding,path,max_bytes=cap)
        except Exception:
            self._charge(budget,cap+1,'FAILED_SAVED_READ_RESERVED',path,known=False);raise
        self._charge(budget,len(raw),'SAVED_READ',path)
        return raw

    def _register(self,pid,binding,path,budget):
        remaining=MAX_TOTAL-budget['charged_bytes']
        if remaining<2:raise InvalidInput('Manual registration has no live/head read budget')
        # read() may read live bytes and the previous immutable head. Reserve
        # a growth sentinel for each before entering its original transaction.
        cap=min(MAX_BYTES,(remaining-2)//2)
        metadata_remaining=MAX_METADATA-budget['metadata']['charged_bytes']
        if metadata_remaining<1:raise InvalidInput('Manual metadata registration budget exhausted')
        metacap=min(4*1024*1024,metadata_remaining-1)
        try:value=self.ws.read(pid,binding['id'],path,max_bytes=cap,_metadata_max_bytes=metacap)
        except Exception:
            self._charge(budget,2*(cap+1),'FAILED_LIVE_HEAD_READ_RESERVED',path,known=False)
            self._charge_metadata(budget,metacap+1,'LOCAL_SOURCE_HEAD_METADATA',known=False);raise
        receipt=value['read_receipt']
        self._charge(budget,receipt['live_bytes_read'],'CAPTURE_LIVE_READ',path)
        self._charge(budget,receipt['head_bytes_read'],'CAPTURE_HEAD_READ',path)
        self._charge_metadata(budget,receipt['metadata_bytes_read'],'LOCAL_SOURCE_HEAD_METADATA')
        return value

    def _path(self, pid, ident, *parts):
        self.store.project(pid)
        if any(not isinstance(v, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', v) for v in (pid, ident)):
            raise InvalidInput('Unsafe manual-change identity')
        return resolve_data_path('/'.join(('harness-manual', pid, ident, *parts)), self.ws.data_dir)

    def _read(self, path,read_budget=None):
        value = self._metadata(path,read_budget if read_budget is not None else self.read_budget())
        if set(value) != {'payload', 'sha256'} or digest(value['payload']) != value['sha256']:
            raise Conflict('Manual-change record integrity failed')
        return value['payload']

    def _stage(self, path, payload):
        raw = (canonical({'payload': payload, 'sha256': digest(payload)}) + '\n').encode()
        if len(raw) > 4 * 1024 * 1024: raise InvalidInput('Manual-change record exceeds 4 MiB')
        with path.open('xb') as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())

    def _publish(self, path, payload,*,read_budget=None):
        if path.exists():
            if digest(self._read(path,read_budget)) != digest(payload): raise Conflict('Immutable manual-change record differs')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        stage = path.with_name('.pending-' + path.name + '-' + uuid.uuid4().hex)
        self._stage(stage, payload)
        try: os.link(stage, path)
        except FileExistsError:
            if digest(self._read(path,read_budget)) != digest(payload): raise Conflict('Manual-change record appeared with different bytes') from None
        stage.unlink()
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)

    def publish_export(self, path, payload):
        """Keep the legacy plain JSON format while publishing complete bytes."""
        if path.exists():
            if self.ws._json(path) != payload: raise Conflict('Immutable export metadata differs')
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        stage = path.with_name('.pending-' + path.name + '-' + uuid.uuid4().hex)
        self.ws._write(stage, payload)
        try: os.link(stage, path)
        except FileExistsError:
            if self.ws._json(path) != payload: raise Conflict('Export record appeared with different bytes') from None
        stage.unlink()
        if os.name == 'posix':
            fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(fd)
            finally: os.close(fd)

    def _binding(self, pid, workspace):
        value = self.ws.get(pid, workspace['id'], workspace['revision'])
        if value['binding_sha256'] != workspace['binding_sha256']:
            raise Conflict('Manual-change workspace binding changed')
        return value

    def _proposal(self, pid, ident, target=None):
        value = self.proposals.get(pid, ident, target['revision'] if target else None)
        if target and (ref(value) != target or value['current_revision'] != target['revision']):
            raise ManualChangesConflict('SOURCE_DRIFT', {'target_proposal_ref': target, 'status': 'STALE'}, '任务版本已改变，请核对当前版本。')
        if value['review_status'] != 'CURRENT':
            raise ManualChangesConflict('SOURCE_DRIFT', {'target_proposal_ref': ref(value), 'status': 'STALE'}, '来源已改变，请先核对当前资料。')
        return value

    def _old_proposal(self,pid,prior,budget):
        # Read immutable SQL metadata, not the historical view's current source
        # checks. Check byte length inside one read transaction before loading.
        with self.store.connect()as connection:
            connection.execute('BEGIN')
            row=connection.execute('SELECT length(CAST(payload AS BLOB)) AS n FROM planning_proposal_versions WHERE proposal_id=? AND revision=?',
                                   (prior['id'],prior['revision'])).fetchone()
            if row is None:raise Conflict('Registered immutable proposal is missing')
            size=row['n'];remaining=MAX_METADATA-budget['metadata']['charged_bytes']
            if type(size)is not int or size>4*1024*1024 or size>remaining:raise InvalidInput('Registered proposal metadata exceeds the remaining budget')
            try:old=self.proposals._version(connection,pid,prior['id'],prior['revision'])
            except Exception:
                self._charge_metadata(budget,size,'FAILED_IMMUTABLE_PROPOSAL_METADATA',known=False);raise
            self._charge_metadata(budget,size,'IMMUTABLE_PROPOSAL_VERSION')
        if ref(old)!=prior:raise Conflict('Registered export proposal SHA differs')
        return {node['id']for node in old['payload']['candidate']['nodes']}

    def _exports(self, pid, ident, binding, target, predecessor=None,*,read_budget=None,_active_export=None):
        read_budget=read_budget if read_budget is not None else self.read_budget()
        folder = self.ws._data(pid, binding['id'], 'planning-exports')
        inherited = [ManualExportRef.model_validate(r) for m in target['payload'].get('manual_inheritance', [])
                     for r in m.get('predecessor_export_refs', [])]
        extras = [r.model_dump() for r in inherited] + ([predecessor] if predecessor else [])
        found, count = [], 0
        # One scan may contain several exports of the same immutable version.
        # Reuse only its verified value here; the next inspect starts fresh.
        old_versions = {}
        def old_nodes(prior):
            key = canonical(prior)
            if key not in old_versions:
                old_versions[key] = self._old_proposal(pid, prior, read_budget)
            return old_versions[key]
        if folder.exists():
            for entry in folder.iterdir():
                count += 1
                if count > MAX_OPERATIONS: raise InvalidInput('Manual coverage exceeds 200 export operations')
                if entry.is_symlink() or not entry.is_dir() or not re.fullmatch(r'[0-9a-f]{64}', entry.name):
                    raise Conflict('Export registry identity is invalid')
                claim=resolve_data_path(entry/'request.json',self.ws.data_dir)
                completed=resolve_data_path(entry/'result.json',self.ws.data_dir)
                if not claim.exists() and not completed.exists():continue
                request=self._metadata(claim,read_budget)
                if set(request)!={'request_sha256','request'} or digest(request['request'])!=request['request_sha256']:
                    raise Conflict('Registered export request integrity failed')
                requested=request['request']
                if not completed.exists():
                    if requested.get('proposal_id')!=ident:continue
                    checked=HarnessExportInput.model_validate({'operation_id':'pending',**{k:v for k,v in requested.items()if k!='proposal_id'}})
                    if (digest({'proposal_id':ident,**checked.model_dump(exclude={'operation_id'})})!=request['request_sha256'] or
                            checked.workspace_ref.model_dump()!={k:binding[k]for k in('id','revision','binding_sha256')} or
                            checked.expected_revision>target['revision']):raise Conflict('Pending export scope differs')
                    prior={'id':ident,'revision':checked.expected_revision,'sha256':checked.proposal_sha256}
                    old_nodes(prior)
                    # Only PlanningExports' internally held current operation
                    # can omit its own exact request in later write checks.
                    if _active_export=={'id':entry.name,'request_sha256':request['request_sha256']}:continue
                    raise PendingExport({'operation_hash':entry.name,'request_sha256':request['request_sha256'],
                        'proposal_ref':prior,'recovery':'ORIGINAL_OPERATION_ONLY','status':'PENDING_EXPORT_RESULT'})
                result=self._metadata(completed,read_budget)
                if (set(request) != {'request_sha256', 'request'} or digest(request['request']) != request['request_sha256'] or
                        not isinstance(result.get('proposal_ref'), dict)):
                    raise Conflict('Registered export request/result integrity failed')
                export_ref = {'id': entry.name, 'request_sha256': request['request_sha256'], 'result_sha256': digest(result)}
                prior = result['proposal_ref']; requested = request['request']
                same = prior.get('id') == ident and type(prior.get('revision')) is int and prior['revision'] <= target['revision']
                selected = export_ref in extras
                if not (same or selected): continue
                if (type(prior.get('revision')) is not int or prior['revision'] < 1 or
                        requested.get('proposal_id') != prior.get('id') or requested.get('expected_revision') != prior['revision'] or
                        type(requested.get('expected_revision')) is not int or requested.get('proposal_sha256') != prior.get('sha256') or
                        result.get('workspace_ref') != requested.get('workspace_ref') or
                        result['workspace_ref'] != {k: binding[k] for k in ('id', 'revision', 'binding_sha256')} or
                        result.get('status') != 'EXPORTED_CONFIRMED_PLAN' or result.get('execution_started') is not False):
                    raise Conflict('Registered export scope/basis differs')
                node_ids=old_nodes(prior);seen=set()
                for item in result['files']:
                    relative(item['path'])
                    if item['path'] in seen or not item['path'].startswith(result['version_directory'] + '/'):
                        raise Conflict('Registered export file scope differs')
                    seen.add(item['path'])
                    if type(item['byte_length']) is not int or item['byte_length'] < 0 or not re.fullmatch(r'[0-9a-f]{64}', item['sha256']):
                        raise Conflict('Registered export file identity differs')
                found.append((export_ref,{'files':result['files'],'version_directory':result['version_directory']},node_ids))
        if any(extra not in [row[0] for row in found] for extra in extras):
            raise Conflict('Explicit predecessor is not an exact registered export in this project/workspace')
        return found, count

    def _basis(self, pid, binding, path, expected,budget=None):
        budget=budget if budget is not None else self.read_budget()
        directory = self.ws._versions(binding, path); matched = None; count = 0
        if directory.exists():
            for entry in directory.iterdir():
                count += 1
                if count > 200: raise InvalidInput('Basis lookup exceeds 200 saved versions for one registered path')
                if not re.fullmatch(r'[0-9]{10}', entry.name): continue
                metadata = self._metadata(resolve_data_path(entry / 'metadata.json', self.ws.data_dir),budget)
                value = metadata.get('version')
                if not isinstance(value, dict) or digest(value) != metadata.get('sha256'):
                    raise Conflict('Stored harness source metadata integrity failed')
                if value.get('sha256') != expected: continue
                if type(value.get('byte_length')) is not int or value['byte_length'] > MAX_BYTES:
                    raise InvalidInput('Generated basis exceeds the 1 MiB diff bound')
                blob = resolve_data_path(entry / 'bytes', self.ws.data_dir)
                if not blob.is_file():raise Conflict('Stored generated basis bytes are missing')
                remaining=MAX_TOTAL-budget['charged_bytes'];cap=min(MAX_BYTES,remaining-1)
                if cap<0 or blob.stat().st_size!=value['byte_length'] or blob.stat().st_size>cap:
                    raise InvalidInput('Stored basis byte size differs or exceeds the diff bound')
                allowance=min(value['byte_length'],cap)+1
                try:
                    with blob.open('rb',buffering=0)as stream:raw=self.ws._read_limit(stream,allowance-1)
                except Exception:
                    self._charge(budget,allowance,'FAILED_BASIS_READ_RESERVED',path,known=False);raise
                self._charge(budget,len(raw),'FROZEN_BASIS_READ',path)
                if (value.get('project_id')!=pid or value.get('workspace_id')!=binding['id'] or value.get('path')!=path or
                        type(value.get('revision'))is not int or value['revision']!=int(entry.name) or
                        value.get('binding_revision')!=binding['revision'] or value.get('binding_sha256')!=binding['binding_sha256'] or
                        len(raw)!=value['byte_length'] or len(raw)>MAX_BYTES or sha(raw)!=expected):
                    raise Conflict('Stored basis bytes/scope differ')
                source=FileSource.model_validate(self.ws._ref(value)).model_dump()
                matched = source, raw
                break
        if matched is None: raise Conflict('No exact registered generated-byte history is available')
        return matched

    def _snapshot(self, pid, ident, capture_ref,read_budget=None):
        capture_ref = ManualCaptureRef.model_validate(capture_ref)
        value = self._read(self._path(pid, ident, 'captures', capture_ref.id, '1.json'),read_budget)
        if digest(value) != capture_ref.sha256 or value['project_id'] != pid or value['target_proposal_ref']['id'] != ident:
            raise Conflict('Manual capture scope/SHA differs')
        return value

    @staticmethod
    def _marker_valid(marker, candidate):
        nodes = {n['id']: n for n in candidate['nodes']}
        return all(t['node_id'] in nodes and digest(nodes[t['node_id']][t['field']]) == t['after_value_sha256']
                   for t in marker['targets'])

    def _adopted(self, proposal, path,read_budget=None,*,_snapshots=None):
        snapshots = _snapshots if _snapshots is not None else {}
        for marker in reversed(proposal['payload'].get('manual_inheritance', [])):
            key = canonical(marker['capture_ref'])
            if key not in snapshots:
                snapshots[key] = self._snapshot(proposal['project_id'], proposal['id'], marker['capture_ref'],read_budget)
            snapshot = snapshots[key]
            if marker['edited_sources'] != [f['edited_source_ref'] for f in snapshot['files']]:
                raise Conflict('Inherited manual sources differ from immutable capture')
            for item in marker['edited_sources']:
                if item['path'] == path:
                    # A superseding decision that became invalid cannot fall
                    # back to an older matching field and clear its delta.
                    return (marker, item) if self._marker_valid(marker, proposal['payload']['candidate']) else None
        return None

    def inspect(self, pid, ident, workspace, *, target=None, predecessor=None,read_budget=None,_active_export=None):
        read_budget=read_budget if read_budget is not None else self.read_budget()
        proposal = self._proposal(pid, ident, target); binding = self._binding(pid, workspace)
        if proposal['payload']['workspace_ref'] not in (None, workspace): raise Conflict('Manual scope differs from proposal workspace')
        detail = {'target_proposal_ref': ref(proposal), 'workspace_ref': workspace, 'status': 'CLEAR', 'files': [],
                  'predecessor_export_refs': [], 'coverage': {'complete': True, 'total_registered_files': 0,
                  'checked_files': 0, 'scanned_export_count': 0, 'unverified': []}}
        try:
            exports, count = self._exports(pid, ident, binding, proposal, predecessor,read_budget=read_budget,_active_export=_active_export)
            detail['coverage']['scanned_export_count'] = count
            detail['predecessor_export_refs'] = [r[0] for r in exports]
            registered = {}
            for export_ref, result, node_ids in exports:
                for item in result['files']:
                    path = item['path']; known = registered.get(path)
                    if known and known['basis_sha256'] != item['sha256']: raise Conflict('Registered basis hashes disagree')
                    name = Path(path).name
                    node = next((key for key in node_ids if name == 'task-' + key + '.md'), None)
                    registered[path] = {'export_ref': export_ref, 'path': path, 'basis_sha256': item['sha256'],
                        'byte_length': item['byte_length'], 'node_id': node, 'kind': 'PROMPT' if node else 'OTHER'}
            detail['coverage']['total_registered_files'] = len(registered); total = 0
            detail['coverage']['raw_bytes_verified']=0;checked_paths=[]
            # Capture records are immutable input to this one inspection, not
            # cached CLEAR decisions. Later gates reread metadata and live files.
            snapshots = {}
            for row in registered.values():
                path = row['path']
                try:
                    if row['byte_length'] > MAX_BYTES: raise InvalidInput('Registered file coverage exceeds bounded scope')
                    # Reserve one possible growth sentinel before each native
                    # read, including the final failed read in the 8 MiB bound.
                    raw = self._live(binding,path,read_budget);total+=len(raw)
                    detail['coverage']['raw_bytes_verified']=total
                    detail['coverage']['checked_files'] += 1
                    checked_paths.append(path)
                    adopted = self._adopted(proposal, path,read_budget,_snapshots=snapshots)
                    comparison = adopted[1]['sha256'] if adopted else row['basis_sha256']
                    if sha(raw) == comparison: continue
                    modified = {k: v for k, v in row.items() if k != 'byte_length'}
                    modified.update(edited_sha256=sha(raw), status='MODIFIED')
                    if row['kind'] != 'PROMPT': raise InvalidInput('Changed non-prompt output needs explicit external review')
                    if len(detail['files'])>=MAX_FILES:
                        detail['coverage']['limit']={'kind':'CHANGED_PROMPT_FILES','max':MAX_FILES,'observed_at_least':MAX_FILES+1}
                        raise InvalidInput('Changed prompts exceed the 32-file capture boundary')
                    detail['files'].append(modified)
                except (Conflict, InvalidInput, NotFound, OSError, ValueError) as error:
                    detail['coverage']['unverified'].append({'path': path, 'reason': type(error).__name__})
                    if getattr(error,'code',None)=='BUSY':detail['coverage']['unverified'][-1]['code']='BUSY'
                    # Failed reads may have consumed the reserved sentinel;
                    # stop instead of guessing their actual consumption.
                    break
            if detail['coverage']['checked_files']!=len(registered) and not detail['coverage']['unverified']:
                detail['coverage']['unverified'].append({'reason':'INCOMPLETE_REGISTERED_COVERAGE'})
            unchecked=[p for p in registered if p not in checked_paths]
            detail['coverage']['checked_paths']=checked_paths[:256]
            detail['coverage']['unchecked_paths']=unchecked[:256]
            detail['coverage']['unchecked_path_count']=len(unchecked)
            detail['coverage']['unlisted_checked_path_count']=max(0,len(checked_paths)-256)
            detail['coverage']['unlisted_unchecked_path_count']=max(0,len(unchecked)-256)
            if detail['coverage']['unverified']:
                detail['coverage']['complete'] = False; detail['status'] = 'UNVERIFIED'
            elif detail['files']: detail['status'] = 'REVIEW_REQUIRED'
        except (Conflict, InvalidInput, NotFound, OSError, KeyError, TypeError, ValueError) as error:
            detail['coverage']['complete'] = False; detail['status'] = 'UNVERIFIED'
            detail['coverage']['unverified'].append({'reason': type(error).__name__})
            if isinstance(error,PendingExport):detail['coverage']['pending_exports']=[error.pending]
        detail['coverage']['read_receipt']=deepcopy(read_budget)
        return detail

    def require_clear(self, pid, ident, workspace, *, target=None,read_budget=None,_active_export=None):
        detail = self.inspect(pid, ident, workspace, target=target,read_budget=read_budget,_active_export=_active_export)
        if detail['status'] != 'CLEAR':
            if detail['status']=='UNVERIFIED':
                busy=any(row.get('code')=='BUSY' for row in detail['coverage'].get('unverified',[]))
                message=('旧导出文件正被其他程序占用；请结束写入后核对原操作。' if busy else
                         '旧导出文件尚未完成核验；请保留原操作并查看未核验原因。')
                raise ManualChangesConflict('UNVERIFIED',detail,message)
            raise ManualChangesConflict('MANUAL_REVIEW_REQUIRED',detail)
        return detail

    def _hunks(self, path, before, after, budget):
        try: a, b = before.decode('utf-8'), after.decode('utf-8')
        except UnicodeError: raise InvalidInput('Manual prompt changes require saved UTF-8 text') from None
        if '\x00' in a + b: raise InvalidInput('Binary NUL is not a prompt change')
        old, new = a.splitlines(keepends=True), b.splitlines(keepends=True)
        budget['work'] += max(1, len(old)) * max(1, len(new))
        if max(len(old), len(new)) > MAX_LINES or budget['work'] > MAX_WORK: raise InvalidInput('Manual diff line/work budget exceeded')
        result = []
        for kind, i, j, k, end in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
            if kind == 'equal': continue
            core = {'path': path, 'kind': {'insert': 'INSERT', 'replace': 'REPLACE', 'delete': 'DELETE'}[kind],
                    'before_range': {'start': i, 'end': j}, 'after_range': {'start': k, 'end': end},
                    'removed_text': ''.join(old[i:j]), 'added_text': ''.join(new[k:end]),
                    'basis_sha256': sha(before), 'edited_sha256': sha(after)}
            hunk = {'id': digest(core), **core, 'keep_text': wording(core['added_text'])}
            hunk['sha256'] = digest(hunk); budget['hunks'] += 1; budget['bytes'] += len(canonical(hunk).encode())
            if budget['hunks'] > MAX_HUNKS or budget['bytes'] > MAX_DIFF: raise InvalidInput('Manual diff hunk/output budget exceeded')
            result.append(hunk)
        return result

    @staticmethod
    def _capture_matches(pid,body,snapshot):
        if (snapshot.get('schema')!='cirven.harness-manual-capture.v1' or snapshot.get('project_id')!=pid or
                digest(snapshot.get('target_proposal_ref'))!=digest(body.target_proposal_ref.model_dump()) or
                digest(snapshot.get('workspace_ref'))!=digest(body.workspace_ref.model_dump()) or
                digest([f['file_ref']for f in snapshot['files']])!=digest([f.model_dump()for f in body.files]) or
                (body.predecessor_export_ref is not None and body.predecessor_export_ref.model_dump()not in snapshot['predecessor_export_refs'])):
            raise Conflict('Capture prepared/result basis differs from original request')

    def capture(self, pid, ident, body):
        body = ManualCaptureInput.model_validate(body)
        if body.target_proposal_ref.id != ident: raise InvalidInput('Capture target differs from route')
        request = body.model_dump(exclude={'operation_id'}); hashed = digest(request)
        ledger=self.read_budget()
        operation = self._path(pid, ident, 'operations', digest(body.operation_id))
        with LocalMaterials(self.ws)._lock(operation / 'lock'):
            claims = operation / 'claims'; claims.mkdir(exist_ok=True)
            entries = list(islice(claims.iterdir(),2))
            if entries and (len(entries) != 1 or entries[0].name != hashed or not entries[0].is_dir() or any(entries[0].iterdir())):
                raise Conflict('IDEMPOTENCY_CONFLICT: capture binds another request')
            (claims / hashed).mkdir(exist_ok=True)
            self._publish(operation / 'request.json', {'request_sha256': hashed, 'input': request},read_budget=ledger)
            if (operation / 'result.json').exists():
                saved = self.operation(pid,ident,body.operation_id,hashed,_read_budget=ledger)
                return {**self.get(pid, ident, saved['capture_ref'],read_budget=ledger), 'replayed': True}
            prepared = operation / 'prepared.json'
            if prepared.exists():
                snapshot = self._read(prepared,ledger)
                self._capture_matches(pid,body,snapshot)
            else:
                detail = self.inspect(pid, ident, body.workspace_ref.model_dump(), target=body.target_proposal_ref.model_dump(),
                                      predecessor=body.predecessor_export_ref.model_dump() if body.predecessor_export_ref else None,read_budget=ledger)
                if detail['status']=='UNVERIFIED':raise ManualChangesConflict('UNVERIFIED',detail)
                known = {f['path']: f for f in detail['files']}
                binding = self._binding(pid, body.workspace_ref.model_dump()); proposal = self._proposal(pid, ident, body.target_proposal_ref.model_dump())
                files, budget, total = [], {'work': 0, 'hunks': 0, 'bytes': 0}, 0
                for chosen in body.files:
                    item = chosen.model_dump(); row = known.get(chosen.path)
                    if row is None or any(row[key] != item[key] for key in item):
                        raise ManualChangesConflict('SOURCE_DRIFT', detail, '选中的已登记改文或保存版本已改变。')
                    adopted = self._adopted(proposal, chosen.path,ledger)
                    comparison = adopted[1]['sha256'] if adopted else chosen.basis_sha256
                    try:
                        comparison_ref, before = self._basis(pid, binding, chosen.path, comparison,ledger)
                        generated_ref=comparison_ref if comparison==chosen.basis_sha256 else self._basis(pid,binding,chosen.path,chosen.basis_sha256,ledger)[0]
                    except (Conflict,InvalidInput,NotFound,OSError)as error:
                        detail['status']='UNVERIFIED';detail['coverage']['complete']=False
                        detail['coverage']['unverified'].append({'path':chosen.path,'reason':'BASIS_'+type(error).__name__})
                        detail['coverage']['read_receipt']=deepcopy(ledger)
                        raise ManualChangesConflict('UNVERIFIED',detail,'旧生成依据无法核验；原文件和捕获请求保留。')from error
                    # Explicit capture registers only the chosen exact saved bytes, outside any outer SQL transaction.
                    edited = self._register(pid,binding,chosen.path,ledger)
                    if edited['source_ref']['sha256'] != chosen.edited_sha256:
                        raise ManualChangesConflict('SOURCE_DRIFT', detail, '捕获期间旧提示词又被修改。')
                    after = edited['content'].encode() if edited['encoding'] == 'utf-8' else b'\x00'
                    total += len(before) + len(after)
                    if max(len(before), len(after)) > MAX_BYTES or total > MAX_TOTAL: raise InvalidInput('Capture byte budget exceeded')
                    try:hunks=self._hunks(chosen.path,before,after,budget)
                    except InvalidInput as error:
                        detail['status']='UNVERIFIED';detail['coverage']['complete']=False
                        detail['coverage']['unverified'].append({'path':chosen.path,'reason':'DIFF_BUDGET_OR_ENCODING','message':str(error)})
                        detail['coverage']['read_receipt']=deepcopy(ledger)
                        raise ManualChangesConflict('UNVERIFIED',detail,'差异超出本次有界核验范围；没有截断或自动采用。')from error
                    files.append({'file_ref': item, 'node_id': row['node_id'], 'basis_source_ref': generated_ref,
                        'comparison_source_ref': comparison_ref, 'edited_source_ref': edited['source_ref'],
                        'hunks':hunks})
                selected = {f.path for f in body.files}
                coverage = deepcopy(detail['coverage']); coverage['uncovered_changed_paths'] = sorted(set(known) - selected)
                coverage['complete'] = coverage['complete'] and not coverage['uncovered_changed_paths']
                snapshot = {'schema': 'cirven.harness-manual-capture.v1', 'project_id': pid,
                    'target_proposal_ref': body.target_proposal_ref.model_dump(), 'workspace_ref': body.workspace_ref.model_dump(),
                    'predecessor_export_refs': detail['predecessor_export_refs'], 'files': files, 'coverage': coverage,
                    'diff_budget': budget,'raw_read_receipt':deepcopy(ledger), 'authority': 'EXPLICIT_SAVED_TEXT_NOT_PROJECT_FACT_OR_EXECUTION_AUTHORIZATION'}
                self._publish(prepared, snapshot,read_budget=ledger)
            capture_ref = {'id': digest({'operation_id': body.operation_id, 'request_sha256': hashed}), 'revision': 1, 'sha256': digest(snapshot)}
            self._publish(self._path(pid, ident, 'captures', capture_ref['id'], '1.json'), snapshot,read_budget=ledger)
            value=self.get(pid,ident,capture_ref,read_budget=ledger)
            self._publish(operation / 'result.json', {'capture_ref': capture_ref, 'request_sha256': hashed},read_budget=ledger)
            return {**value, 'replayed': False,'raw_read_receipt':deepcopy(ledger)}

    def get(self, pid, ident, capture_ref,*,read_budget=None):
        read_budget=read_budget if read_budget is not None else self.read_budget()
        snapshot = self._snapshot(pid, ident, capture_ref,read_budget); current = self.proposals.get(pid, ident)
        status, pending = 'APPLICABLE', []
        marker = next((m for m in current['payload'].get('manual_inheritance', []) if m['capture_ref'] == capture_ref), None)
        try:
            binding = self._binding(pid, snapshot['workspace_ref'])
            for item in snapshot['files']:
                if sha(self._live(binding,item['file_ref']['path'],read_budget)) != item['edited_source_ref']['sha256']:
                    pending.append({'path': item['file_ref']['path'], 'reason': 'SAVED_BYTES_CHANGED'})
            if marker and self._marker_valid(marker, current['payload']['candidate']) and not pending: status = 'APPLIED'
            elif ref(current) != snapshot['target_proposal_ref'] or pending: status = 'STALE'
            elif not snapshot['coverage']['complete']: status = 'UNVERIFIED'
        except (Conflict, InvalidInput, NotFound, OSError): status = 'UNVERIFIED'
        return {'capture_ref': ManualCaptureRef.model_validate(capture_ref).model_dump(), 'snapshot': snapshot,
                'current': {'status': status, 'pending': pending, 'target_proposal_ref': ref(current)},'raw_read_receipt':deepcopy(read_budget)}

    def operation(self, pid, ident, operation_id, request_sha256,*,_read_budget=None):
        ledger=_read_budget if _read_budget is not None else self.read_budget()
        if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', operation_id) or not re.fullmatch(r'[0-9a-f]{64}', request_sha256):
            raise InvalidInput('Original capture key/SHA required')
        directory = self._path(pid, ident, 'operations', digest(operation_id))
        value = {'operation_hash': digest(operation_id), 'request_sha256': request_sha256, 'status': 'NOT_FOUND', 'capture_ref': None}
        if not directory.exists(): return value
        claims = directory / 'claims'
        entries = list(islice(claims.iterdir(),2)) if claims.is_dir() else []
        if (len(entries) != 1 or entries[0].name != request_sha256 or not entries[0].is_dir() or
                entries[0].is_symlink() or any(entries[0].iterdir())): raise Conflict('Capture intent does not match the original request')
        if not (directory / 'request.json').exists(): return {**value, 'status': 'INTENT_ONLY'}
        record = self._read(directory / 'request.json',ledger)
        body = ManualCaptureInput.model_validate({'operation_id': operation_id, **record['input']})
        if (record['request_sha256'] != request_sha256 or digest(record['input']) != request_sha256 or
                digest(body.model_dump(exclude={'operation_id'})) != request_sha256 or body.target_proposal_ref.id != ident):
            raise Conflict('Capture original request/schema/scope differs')
        if not (directory / 'result.json').exists(): return {**value, 'status': 'PENDING'}
        result = self._read(directory / 'result.json',ledger)
        if (set(result)!={'request_sha256','capture_ref'} or result['request_sha256']!=request_sha256 or
                result['capture_ref']['id']!=digest({'operation_id':operation_id,'request_sha256':request_sha256})):
            raise Conflict('Capture result differs from original request')
        snapshot = self._snapshot(pid, ident, result['capture_ref'],ledger)
        self._capture_matches(pid,body,snapshot)
        return {**value, 'status': 'COMPLETED', 'capture_ref': result['capture_ref']}

    def prepare_edit(self, pid, ident, old, body):
        if body.manual_review is None: return None
        ledger=self.read_budget()
        review = body.manual_review; snapshot = self._snapshot(pid, ident, review.change_ref.model_dump(),ledger)
        if ref(old) != snapshot['target_proposal_ref']: raise Conflict('Captured target changed before manual adoption')
        binding = self._binding(pid, snapshot['workspace_ref'])
        for item in snapshot['files']:
            if sha(self._live(binding,item['file_ref']['path'],ledger)) != item['edited_source_ref']['sha256']:
                raise Conflict('SOURCE_DRIFT: captured saved text changed before adoption')
        hunks = {h['id']: h for f in snapshot['files'] for h in f['hunks']}
        if set(hunks) != {d.hunk_id for d in review.decisions}: raise InvalidInput('Every captured hunk needs exactly one explicit decision')
        before = {n['id']: n for n in old['payload']['candidate']['nodes']}
        expected = deepcopy(before); touched = {}; removed_indexes = {}; occupied = set()
        for decision in review.decisions:
            hunk = hunks[decision.hunk_id]
            if hunk['sha256'] != decision.hunk_sha256: raise Conflict('Manual decision hunk SHA differs')
            if decision.mode == 'DISCARD': continue
            target = decision.target; key = (target.node_id, target.field)
            if target.node_id not in before or digest(before[target.node_id][target.field]) != target.before_value_sha256:
                raise Conflict('Current task field differs from the explicitly selected adoption basis')
            text = decision.text
            if decision.mode == 'KEEP' and target.edit_action != 'REMOVE' and text != hunk['keep_text']:
                raise InvalidInput('KEEP may adopt only the actual changed fragment, without diff context')
            value = expected[target.node_id][target.field]
            if isinstance(value, str):
                if target.edit_action != 'REPLACE' or target.item_index is not None or not text:
                    raise InvalidInput('A nonempty task description requires explicit replacement')
                if key in occupied: raise InvalidInput('Manual decisions cannot overwrite the same description twice')
                occupied.add(key)
                expected[target.node_id][target.field] = text
            else:
                index = target.item_index
                if target.edit_action == 'ADD':
                    if index is not None:raise InvalidInput('ADD appends only; original item locators belong to REPLACE/REMOVE')
                    value.append(text)
                else:
                    original = before[target.node_id][target.field]
                    if index is None or index >= len(original): raise InvalidInput('List replacement/removal needs an exact original item locator')
                    slot = (*key, index)
                    if slot in occupied: raise InvalidInput('Manual decisions cannot overwrite or remove the same original list item twice')
                    occupied.add(slot)
                    actual_index = index - sum(i < index for i in removed_indexes.get(key, []))
                    if target.edit_action == 'REMOVE':
                        if hunk['added_text'] or wording(hunk['removed_text']) != original[index]:
                            raise InvalidInput('REMOVE requires the actual deleted exact list item')
                        if index in removed_indexes.get(key, []): raise InvalidInput('The same list item cannot be removed twice')
                        value.pop(actual_index); removed_indexes.setdefault(key, []).append(index)
                    else: value[actual_index] = text
            touched[key] = {'node_id': target.node_id, 'field': target.field}
        after = {n.id: n.model_dump() for n in body.candidate.nodes}
        targets = []
        for key, target in touched.items():
            node, field = key
            if node not in after or after[node][field] != expected[node][field]:
                raise InvalidInput('Accepted manual wording must actually appear in the submitted candidate field')
            targets.append({**target, 'before_value_sha256': digest(before[node][field]), 'after_value_sha256': digest(after[node][field])})
        return {'capture_ref': review.change_ref.model_dump(), 'basis_proposal_ref': ref(old),
            'decisions': [d.model_dump() for d in review.decisions], 'targets': targets,
            'edited_sources': [f['edited_source_ref'] for f in snapshot['files']],
            'predecessor_export_refs': snapshot['predecessor_export_refs'], 'coverage': snapshot['coverage'],'read_receipt':ledger}

    def check_edit_sources(self, pid, marker):
        if marker is None: return
        for source in marker['edited_sources']:
            source = FileSource.model_validate(source)
            binding = self._binding(pid, {'id': source.workspace_id, 'revision': source.binding_revision,
                                         'binding_sha256': source.binding_sha256})
            if sha(self._live(binding,source.path,marker['read_receipt'])) != source.sha256:
                raise Conflict('SOURCE_DRIFT: saved text changed at proposal commit')
