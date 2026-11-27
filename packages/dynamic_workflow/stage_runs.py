"""Transactional state and review boundaries for project-owned engineering workflows."""
from copy import deepcopy
import hashlib
import json
import os
import re
import sqlite3
import uuid

import psutil

from packages.domain.conversation_threads import ConversationThreads
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.dynamic_workflow.stage_assertions import (
    CHECK_KINDS, MANUAL_METHOD, DevelopmentEvidence, TestEvidence, action_digest, apply_manual_checks,
    passing_check, planned_design_files,
)
from packages.dynamic_workflow.stage_contracts import (
    CONTRACT_VERSION, DEFAULT_STAGES, LEGACY_CONTRACT_VERSION, RUNTIME_TARGET_CONTRACT_VERSIONS,
    TRACE_CONTRACT_VERSIONS, container_target_issues, source_urs, validate_artifact,
)
from packages.dynamic_workflow.stage_kernel import TerminationUnconfirmed
from packages.dynamic_workflow.stage_manual_files import read_manual_file, save_manual_files


ID = re.compile(r'^[a-zA-Z0-9_-]{1,100}$')
CAPABILITY_KEYS = ('skills', 'tools', 'mcp')
KERNELS = ('opencode', 'claude-code')
IMPROVEMENT_DESTINATIONS = (
    ' FROM stage_workflow_improvements i '
    'JOIN stage_workflow_operations o ON o.operation_id=i.operation_id '
    'JOIN stage_workflow_runs r ON r.id=i.run_id WHERE r.project_id=?'
)
DEFAULT_TOOLS = {
    'development': ['workspace_list', 'workspace_read', 'workspace_write', 'build_artifacts'],
    'testing': ['workspace_list', 'workspace_read', 'run_check'],
    'delivery': ['workspace_list', 'workspace_read', 'package_files'],
}


def _text(value, label, limit=8000):
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise InvalidInput(f'{label}为空或超出长度限制')
    return value.strip()


def _id(value):
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise InvalidInput('节点、工作流或操作编号无效')
    return value


def _json(value, limit=1024 * 1024):
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > limit:
            raise ValueError()
    except (UnicodeError, ValueError, TypeError, RecursionError):
        raise InvalidInput('工作流内容超出支持范围') from None
    return value


def _fields(obj, allowed):
    if not isinstance(obj, dict) or set(obj) - set(allowed):
        raise InvalidInput('工作流字段无效')


def _manual_bench(value, firmware_files):
    _fields(value, ('devices','host','fixture'))
    devices = value.get('devices', [])
    if not isinstance(devices, list) or len(devices) > 6:
        raise InvalidInput('实测工位最多可登记六台设备')
    bench = {'devices': []}
    for field, label in (('host','上位机版本'),('fixture','夹具与接线')):
        if field in value:
            bench[field] = _text(value[field],label,300)
    for device in devices:
        _fields(device, ('label','identity','link','firmware_label','firmware_id'))
        item = {'label':_text(device.get('label'),'设备名称和板卡版本',160)}
        for field, label in (('identity','设备编号'),('link','连接方式'),('firmware_label','现场报告的固件版本')):
            if field in device:
                item[field] = _text(device[field],label,160)
        if 'firmware_id' in device:
            firmware = firmware_files.get(_id(device['firmware_id']))
            if not firmware:
                raise InvalidInput('所选固件不在当前测试的已审核开发输入中')
            item['firmware'] = firmware
        bench['devices'].append(item)
    if not bench['devices'] and len(bench) == 1:
        raise InvalidInput('工位信息为空')
    return bench


def _strings(values, *, limit=128):
    if not isinstance(values, list) or len(values) > limit:
        raise InvalidInput('清单项数无效')
    result = [_text(v, '清单项', 300) for v in values]
    if len(set(result)) != len(result):
        raise InvalidInput('清单项重复')
    return result


def _config(value):
    caps = value.get('capabilities', {})
    _fields(caps, CAPABILITY_KEYS)
    caps = {key: _strings(caps.get(key, [])) for key in CAPABILITY_KEYS}
    kernel = value.get('kernel', 'opencode')
    count = value.get('max_subagents', 0)
    if kernel not in KERNELS or type(count) is not int or not 0 <= count <= 32:
        raise InvalidInput('执行内核或子 Agent 上限无效')
    return {'capabilities': caps, 'kernel': kernel, 'max_subagents': count}


def _definition(value):
    _fields(value, ('id', 'name', 'nodes', 'revision'))
    name = _text(value.get('name'), '工作流名称', 100)
    if not isinstance(value.get('nodes'), list) or not 1 <= len(value['nodes']) <= 64:
        raise InvalidInput('工作流需要 1 至 64 个节点')
    kinds = {x['kind'] for x in DEFAULT_STAGES} | {'custom'}
    result, seen = [], set()
    for node in value['nodes']:
        _fields(node, ('id','name','kind','instructions','inputs','capabilities','kernel','max_subagents'))
        ident = _id(node.get('id'))
        kind = node.get('kind')
        reads = _strings(node.get('inputs', []), limit=63)
        if ident in seen or kind not in kinds or not set(reads) <= seen:
            raise InvalidInput('节点编号重复、类型无效或依赖未排在当前节点之前')
        instructions = node.get('instructions', '')
        if not isinstance(instructions, str) or len(instructions) > 16000:
            raise InvalidInput('节点执行说明过长')
        result.append({'id': ident, 'kind': kind, 'name': _text(node.get('name'), '节点名称', 100),
                       'instructions': instructions, 'inputs': reads, **_config(node)})
        seen.add(ident)
    return {'id': _id(value.get('id')), 'name': name, 'nodes': result}


def _default(data_dir):
    from packages.dynamic_workflow.kernel_settings import company_required
    company = company_required(data_dir)
    return {**_definition({'id':'default', 'name':'默认','nodes':[
        {'id':s['kind'], 'kind':s['kind'], 'name':s['title'],'inputs':list(s['reads']),
         'kernel':'claude-code' if company else 'opencode',
         'capabilities':{'tools':DEFAULT_TOOLS.get(s['kind'], [])}} for s in DEFAULT_STAGES]}), 'revision':4 if company else 3}


def _checked(row):
    if row is None:
        raise NotFound('找不到此项目的工作流记录')
    payload = json.loads(row['body'])
    if digest(payload) != row['sha256']:
        raise Conflict('工作流记录校验失败')
    return payload


def _runtime_owner():
    p = psutil.Process(os.getpid())
    return {'pid': p.pid, 'birth': p.create_time()}


def _alive(owner):
    try:
        return owner and psutil.Process(owner['pid']).create_time() == owner['birth']
    except psutil.NoSuchProcess:
        return False
    except psutil.AccessDenied:
        return True


def _engine_fingerprint(context):
    from packages.dynamic_workflow.kernel_settings import freeze_policy, runtime
    root = context['_data_dir']
    pin = freeze_policy(root,context)
    if pin != context['policy_pin']:
        return None
    _, route, revision = runtime(root,context['kernel'])
    if revision != pin['kernel_revision']:
        return None
    return {'kernel':context['kernel'],'revision':revision,'model':route['model'],'endpoint':route['endpoint'],
            'binary_sha256':pin['executable_sha256'],'capability_refs':pin['capability_refs']}


def planning_key(context):
    if (context['kind'] not in {'init','requirements','alternatives','cases','design'}
            or context['capabilities']['tools'] or context['capabilities']['mcp'] or context['max_subagents']
            or context['prior_artifact'] or context['annotations'] or context['approved_evidence']
            or context['prior_evidence'] or context.get('rework_request')):
        return None
    from packages.dynamic_workflow.stage_kernel import prepare_stage_prompt
    try:
        engine = _engine_fingerprint(context)
        if not engine:
            return None
        _, prompt = prepare_stage_prompt(context)
        prompt_sha = hashlib.sha256(prompt.encode('utf-8')).hexdigest()
    except (OSError,ValueError,RuntimeError,InvalidInput,Conflict,NotFound):
        return None
    return digest({'contract_version':context.get('contract_version',CONTRACT_VERSION),'engine':engine,'policy_pin':context['policy_pin'],
                   'prompt_sha256':prompt_sha,'inputs':{k:digest(v) for k,v in context['inputs'].items()}}),prompt_sha


def _execution_start(stage, operation):
    return next((event for event in reversed(stage['log']) if event['event']=='started'
                 and event.get('operation_id')==operation),{})


def _execution_kernel(stage, start):
    engine = start.get('kernel')
    if engine is None and start.get('config_sha256') and start['config_sha256']==digest(stage['config']):
        engine = stage['config']['kernel']
    return engine if engine in KERNELS else None


def _show_failure_origin(stage):
    # Project the original failure, leaving the stored stage and its snapshots intact.
    operation = stage.get('operation_id')
    if stage['status']!='failed' or not operation:
        return
    start = _execution_start(stage,operation)
    ended = next((event for event in reversed(stage['log'])
                  if (event['event']=='finished' and event.get('operation_id')==operation)
                  or (event['event']=='recover' and event.get('original_operation_id')==operation)),{})
    stage['execution_origin'] = {'operation_id':operation,'kernel':_execution_kernel(stage,start),
                                 'at':(ended or start).get('at')}


def _receipt_observation(item):
    if item.get('method')=='mcp_response' and item.get('hardware_readback'):
        return {key:item[key] for key in ('hardware_readback','readback_issue') if key in item}
    control = item.get('control')
    if item.get('method')!='container_suite' or not isinstance(control,dict):
        return {}
    fields = ('exit_code','expected_exit_code','output_truncated','output_matches','limit','subject',
              'expected_output_sha256','observed_output_sha256','input_sha256')
    observed = control.get('detail','')
    return {'control':{**{key:control[key] for key in fields if key in control},
                       'output_preview':observed[:1000],'output_preview_partial':len(observed)>1000}}


class StageRuns:
    def __init__(self, store, data_dir, create_child, executor):
        self.store, self.data_dir = store, data_dir
        self.create_child, self.executor = create_child, executor
        self.dialogue_executor = None

    def _scope(self, db, pid, conversation, *, write=False):
        row = ConversationThreads._entity(db, conversation)
        if row['project_id'] != pid or row['deleted_at'] or pid is None:
            raise NotFound('当前项目中找不到此会话')
        if write and row['archived']:
            raise Conflict('此会话已归档')
        child = db.execute('SELECT r.conversation_id,r.id FROM stage_workflow_children c '
                           'JOIN stage_workflow_runs r ON c.run_id=r.id '
                           'WHERE c.conversation_id=? AND r.project_id=?',(conversation,pid)).fetchone()
        if child:
            parent = ConversationThreads._entity(db, child['conversation_id'])
            if parent['project_id'] != pid or parent['deleted_at'] or write and parent['archived']:
                raise Conflict('工作流所属会话已移动、删除或归档')
            return child['conversation_id'], child['id']
        return conversation, None

    def _definition(self, db, pid, ident, revision=None):
        if ident == 'default':
            return _default(self.data_dir)
        query = 'SELECT * FROM stage_workflow_definitions WHERE project_id=? AND id=?'
        if revision is not None:
            return _checked(db.execute(query+' AND revision=?',(pid,ident,revision)).fetchone())
        return _checked(db.execute(query+' ORDER BY revision DESC LIMIT 1',(pid,ident)).fetchone())

    def _run(self, db, pid, owner, ident):
        return _checked(db.execute('SELECT * FROM stage_workflow_runs WHERE id=? AND project_id=? AND conversation_id=?',
                                   (ident,pid,owner)).fetchone())

    def attempts(self, pid, conversation, ident, node_id, operation=None):
        run = self.get(pid,conversation,ident)
        if not any(s['node_id']==node_id for s in run['stages']):
            raise NotFound('节点不属于此工作流')
        with self.store.connect() as db:
            query = "SELECT * FROM stage_workflow_attempts WHERE run_id=? AND node_id=? AND coalesce(json_extract(body,'$.kind'),'')!='stage-dialogue'"
            if operation:
                return _checked(db.execute(query+' AND operation_id=?',(ident,node_id,operation)).fetchone())
            return [{'operation_id':r['operation_id'],'sha256':r['sha256']} for r in
                    db.execute(query+' ORDER BY rowid DESC',(ident,node_id))]

    def execution_record(self, pid, conversation, ident, node_id, operation):
        from packages.dynamic_workflow.stage_workspace import operation_receipts
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请从原阶段会话查看执行记录')
            run = self._run(db,pid,owner,ident)
            stage, historical = self._trace_stage(db,run,conversation,node_id)
            start = _execution_start(stage,operation)
            row = db.execute('SELECT * FROM stage_workflow_attempts WHERE run_id=? AND node_id=? AND operation_id=?',
                             (ident,node_id,operation)).fetchone()
            if row is not None:
                record = _checked(row)
                if record.get('kind')=='stage-dialogue' or record.get('stage_session_id')!=conversation:
                    raise NotFound('此执行记录不属于当前阶段版本')
            else:
                if historical or not start or stage['status']!='running' or stage['operation_id']!=operation:
                    raise NotFound('此执行记录不属于当前阶段版本')
                context = {'_data_dir':self.data_dir,'project_id':pid,'run_id':ident,
                           'stage_session_id':conversation,'operation_id':operation}
                status = ('interrupted_unknown' if not _alive(stage.get('worker')) else
                          'stopping' if stage.get('stop_request') else 'running')
                record = {'status':status,'at':start['at'],
                          'verified_evidence':operation_receipts(context,read_only=True)}
            kernel = record.get('kernel') or _execution_kernel(stage,start)
            fields = ('id','kind','path','case_id','action_id','design_id','status','server','tool','recorded_at',
                      'previous_sha256','sha256','size','exit_code','source_manifest_sha256',
                      'expected_output_sha256','observed_output_sha256','output_matches','output_truncated','input_sha256','subject')
            evidence = []
            for item in record.get('verified_evidence',[]):
                entry = {key:item[key] for key in fields if key in item}
                if 'expected_output_sha256' in entry:
                    observed = item.get('detail','')
                    entry.update(output_preview=observed[:1000],output_preview_partial=len(observed)>1000)
                entry.update(_receipt_observation(item))
                evidence.append(entry)
            visible = self._trace_visibility(db,run)
            inputs = [{'node_id':key,**value} for key,value in start.get('input_versions',{}).items()
                      if visible(value['stage_session_id'])]
            return {'operation_id':operation,'status':record['status'],'at':record['at'],
                    'live':row is None,'started_at':start.get('at'),'inputs':inputs,
                    'kernel':kernel if kernel in KERNELS else None,
                    'annotation_count':len(start.get('annotation_ids',[])), 'evidence':evidence}

    def file_record(self, pid, conversation, ident, evidence_id):
        from packages.dynamic_workflow.stage_workspace import file_receipt_origin, stage_scope

        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child and child != ident:
                raise NotFound('节点不属于此工作流')
            run = self._run(db,pid,owner,ident)
            file = file_receipt_origin(self.data_dir,evidence_id)
            if file:
                record, scope, operation = file
                row = db.execute('SELECT * FROM stage_workflow_attempts WHERE run_id=? AND operation_id=?',
                                 (ident,operation)).fetchone()
                if row:
                    attempt = _checked(row)
                    session = attempt.get('stage_session_id')
                    if (session and scope == stage_scope(pid,ident,session) and
                            record in attempt.get('verified_evidence',[]) and self._trace_visibility(db,run)(session)):
                        return record
        raise NotFound('此工作流没有可读取的文件回执')

    @staticmethod
    def _approval_snapshot(db, ident, stage):
        approval = _checked(db.execute('SELECT * FROM stage_workflow_approvals '
            'WHERE run_id=? AND node_id=? AND sha256=?',
            (ident,stage['node_id'],stage['approval_sha256'])).fetchone())
        if (approval['stage_session_id']!=stage['stage_session_id'] or
                approval['artifact']!=stage['artifact']):
            raise Conflict('审核快照与阶段记录不一致。')
        return approval

    def _delivery_version(self, db, pid, conversation, ident, node_id, sha):
        owner, child = self._scope(db,pid,conversation)
        if child != ident:
            raise NotFound('请从原交付会话打开这一版凭证')
        run = self._run(db,pid,owner,ident)
        stage, _ = self._trace_stage(db,run,conversation,node_id)
        node = next(n for n in run['definition']['nodes'] if n['id']==node_id)
        if node['kind']!='delivery' or stage['status']!='approved' or stage.get('approval_sha256')!=sha:
            raise NotFound('找不到这一版已审核的交付成果')
        return run,stage,self._approval_snapshot(db,ident,stage)

    def delivery_file_record(self, pid, conversation, ident, node_id, approval_sha256, item_id):
        from packages.dynamic_workflow.stage_delivery import approved_delivery_files
        with self.store.connect() as db:
            db.execute('BEGIN')
            _,_,approval = self._delivery_version(db,pid,conversation,ident,node_id,approval_sha256)
        row = next((item for item in approval['artifact']['items'] if item['id']==item_id),None)
        record = approved_delivery_files(approval).get((row['path'],row['sha256'])) if row else None
        if record is None:
            raise NotFound('这一版交付中没有对应的已审核文件')
        return record

    def delivery_receipt(self, pid, conversation, ident, node_id, approval_sha256, *, archive=None):
        from packages.dynamic_workflow.stage_coverage import StageTrace, _start
        from packages.dynamic_workflow.stage_delivery import delivery_receipt

        with self.store.connect() as db:
            db.execute('BEGIN')
            run,stage,delivery = self._delivery_version(db,pid,conversation,ident,node_id,approval_sha256)
            trace = StageTrace(run,self._archived_stages(db,ident),digest,self._trace_visibility(db,run))
            snapshots = {}

            def collect(source):
                sha = source['approval_sha256']
                if sha in snapshots:
                    return
                node = trace.nodes[source['node_id']]
                if not trace.visible(source['stage_session_id']):
                    raise Conflict('此交付版本引用的审核会话已不可访问。')
                approval = delivery if source is stage else self._approval_snapshot(db,ident,source)
                inputs = trace.inputs(source)
                if any(inputs.get(key) is None for key in node['inputs']):
                    raise Conflict('此交付版本有无法唯一核对的上游审核记录。')
                start = _start(source) or {}
                binding = ('none' if not inputs else
                           'pinned' if 'input_versions' in start else 'legacy_artifact_digest')
                for parent in inputs.values():
                    collect(parent)
                snapshots[sha] = source,approval,node['kind'],list(inputs.values()),binding

            collect(stage)
        return delivery_receipt(self.data_dir,run,stage,list(snapshots.values()),self._latest_checks,archive=archive)

    def _operation(self, db, op, checksum):
        _id(op)
        row = db.execute('SELECT * FROM stage_workflow_operations WHERE operation_id=?',(op,)).fetchone()
        if row and row['request_sha256'] != checksum:
            raise Conflict('此操作编号已绑定其他请求')
        return row

    @staticmethod
    def _record_transition(db, run, op, kind, actor, source_node, source_session, previous):
        names = {n['id']:n['name'] for n in run['definition']['nodes']}
        active = run['active_node_id']
        target = next((s for s in run['stages'] if s['node_id']==active),None)
        receipt = {
            'operation_id':op,'kind':kind,'project_id':run['project_id'],'request_conversation_id':actor,
            'run_id':run['id'],'run_revision':run['revision'],'source_node_id':source_node,
            'source_stage_session_id':source_session,'previous':previous,
            'definition_id':run['definition']['id'],'definition_revision':run['definition']['revision'],
            'target':{'node_id':active,'stage_session_id':target['stage_session_id'],
                      'stage_name':names[active]} if target else None,
            'at':run['updated_at'],
        }
        db.execute('INSERT INTO stage_workflow_transitions VALUES (?,?,?,?)',
                   (op,run['id'],canonical(receipt),digest(receipt)))

    def transition_receipt(self, pid, actor, op):
        _id(op)
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,actor)
            receipt = _checked(db.execute('SELECT t.* FROM stage_workflow_transitions t '
                'JOIN stage_workflow_runs r ON r.id=t.run_id '
                'WHERE t.operation_id=? AND r.project_id=? AND r.conversation_id=?',(op,pid,owner)).fetchone())
            if receipt['request_conversation_id'] != actor or child and child != receipt['run_id']:
                raise NotFound('找不到此会话的原操作回执')
            run = self._run(db,pid,owner,receipt['run_id'])
            target = receipt['target']
            historical = False
            if target is None:
                current = run['status']=='completed' and run['revision']==receipt['run_revision']
            else:
                current = False
                session = target['stage_session_id']
                try:
                    if self._scope(db,pid,session)==(owner,run['id']):
                        stage, archived = self._trace_stage(db,run,session,target['node_id'])
                        if stage['stage_session_id']==session and stage['node_id']==target['node_id']:
                            current, historical = not archived, archived
                except (Conflict,NotFound):
                    pass
            return {**receipt,'target_current':current,'target_historical':historical}

    def list(self, pid, conversation):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            defs = db.execute('SELECT d.* FROM stage_workflow_definitions d '
                              'JOIN (SELECT id,max(revision) rev FROM stage_workflow_definitions '
                              'WHERE project_id=? GROUP BY id) h ON d.id=h.id AND d.revision=h.rev '
                              'WHERE d.project_id=? ORDER BY d.id',(pid,pid))
            if child:
                runs = [self._run(db,pid,owner,child)]
            else:
                rows = db.execute('SELECT * FROM stage_workflow_runs WHERE conversation_id=? AND project_id=? ORDER BY rowid DESC',
                                  (owner,pid))
                runs = [_checked(x) for x in rows]
            past = db.execute('SELECT * FROM stage_workflow_stage_history WHERE conversation_id=?',
                              (conversation,)).fetchone() if child else None
            for run in runs:
                for stage in run['stages']:
                    self._read_live_stage(db,run['id'],stage)
                self._run_view(db,run)
            historical = _checked(past) if past else None
            if historical:
                self._read_dialogue(db,child,historical)
                _show_failure_origin(historical)
            improved = [row['entity_id'] for row in db.execute(
                'SELECT DISTINCT o.entity_id'+IMPROVEMENT_DESTINATIONS+' ORDER BY o.entity_id',(pid,))]
            return {'owner_conversation_id':owner,'definitions':[_default(self.data_dir), *(_checked(x) for x in defs)],
                    'improved_definition_ids':improved,'runs':runs,'historical_node_id':past['node_id'] if past else None,
                    'historical_stage':historical,
                    'historical_inputs':self._trace_inputs(db,runs[0],historical) if historical else []}

    def get(self, pid, conversation, ident):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child and ident != child:
                raise NotFound('节点不属于此工作流')
            run = self._run(db,pid,owner,ident)
            for stage in run['stages']:
                self._read_live_stage(db,ident,stage)
            return self._run_view(db,run)

    def _read_live_stage(self, db, ident, stage):
        self._read_dialogue(db,ident,stage)
        if stage['status']=='running':
            stage['execution_state'] = ('interrupted' if not _alive(stage.get('worker')) else
                                        'stopping' if stage.get('stop_request') else 'running')

    @staticmethod
    def _archived_stages(db, ident):
        return [_checked(row) for row in db.execute(
            'SELECT * FROM stage_workflow_stage_history WHERE run_id=? ORDER BY rowid',(ident,))]

    def _trace_visibility(self, db, run):
        visible = {}
        def allowed(session):
            if session not in visible:
                try:
                    _, child = self._scope(db,run['project_id'],session)
                    visible[session] = child==run['id']
                except (Conflict,NotFound):
                    visible[session] = False
            return visible[session]
        return allowed

    def _trace_inputs(self, db, run, stage):
        from packages.dynamic_workflow.stage_coverage import StageTrace
        allowed = self._trace_visibility(db,run)
        trace = StageTrace(run,self._archived_stages(db,run['id']),digest)
        return [{key:input_stage[key] for key in ('node_id','stage_session_id','status','artifact')}
                for input_stage in trace.inputs(stage).values()
                if input_stage and allowed(input_stage['stage_session_id'])]

    @staticmethod
    def _trace_stage(db, run, conversation, node_id):
        stage = next((s for s in run['stages'] if s['node_id']==node_id and
                      s['stage_session_id']==conversation),None)
        if stage is not None:
            return stage,False
        archived = _checked(db.execute('SELECT * FROM stage_workflow_stage_history '
            'WHERE conversation_id=? AND run_id=? AND node_id=?',(conversation,run['id'],node_id)).fetchone())
        return archived,True

    def revision_difference(self, pid, conversation, ident, node_id, revision, *, group=None, row_id=None, offset=0):
        from packages.dynamic_workflow.stage_revision_diff import compare

        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child!=ident:
                raise NotFound('请从成果所属阶段会话打开修订对比')
            run = self._run(db,pid,owner,ident)
            if run['revision']!=revision:
                raise Conflict('工作流记录已更新，请返回成果后重新打开对比')
            stage, _ = self._trace_stage(db,run,conversation,node_id)
            if stage['status'] not in ('review','approved') or not stage.get('artifact') or stage.get('error'):
                raise Conflict('本版尚未生成可审核成果')
            previous = next((entry['previous_session_id'] for entry in reversed(stage['log'])
                             if entry['event'] in ('revised','rework') and entry.get('previous_session_id')),None)
            if previous is None:
                raise NotFound('此阶段没有上一版成果')
            previous_owner, previous_run = self._scope(db,pid,previous)
            if previous_owner!=owner or previous_run!=ident:
                raise NotFound('上一版不属于此工作流')
            prior, archived = self._trace_stage(db,run,previous,node_id)
            if not archived or not prior.get('artifact'):
                raise NotFound('找不到上一版留存成果')
            before = self._approval_snapshot(db,ident,prior)['artifact'] if prior['status']=='approved' else prior['artifact']
            after = self._approval_snapshot(db,ident,stage)['artifact'] if stage['status']=='approved' else stage['artifact']
            result = compare(before,after,group=group,row_id=row_id,offset=offset)
            if group is not None:
                return {'fields':result}
            return {**result,'previous_status':prior['status'],'current_status':stage['status']}

    def coverage(self, pid, conversation, ident, node_id, selector, row_id):
        from packages.dynamic_workflow.stage_coverage import requirement_coverage

        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请从需求阶段会话查看覆盖关系')
            run = self._run(db,pid,owner,ident)
            self._refresh_active_test_inputs(db,run)
            if not any(n['id']==node_id and n['kind']=='requirements' for n in run['definition']['nodes']):
                raise NotFound('此工作流中找不到该需求节点')
            stage,historical = self._trace_stage(db,run,conversation,node_id)
            return requirement_coverage(run,stage,selector,row_id,historical=historical,
                                        digest=digest,evidence_issues=self._evidence_issues,
                                        archives=self._archived_stages(db,ident),
                                        visible=self._trace_visibility(db,run))

    def coverage_overview(self, pid, conversation, ident):
        from packages.dynamic_workflow.stage_coverage import coverage_overview

        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child and child!=ident:
                raise NotFound('节点不属于此工作流')
            run = self._run(db,pid,owner,ident)
            self._refresh_active_test_inputs(db,run)
            return coverage_overview(run,archives=self._archived_stages(db,ident),
                                     digest=digest,evidence_issues=self._evidence_issues,
                                     visible=self._trace_visibility(db,run))

    def coverage_record(self, pid, conversation, ident, node_id, operation, row_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('此成果不属于所选工作流')
            run = self._run(db,pid,owner,ident)
            self._refresh_active_test_inputs(db,run)
            stage,historical = self._trace_stage(db,run,conversation,node_id)
            if stage.get('operation_id')!=operation:
                raise Conflict('目标成果已有新版本，请重读需求覆盖')
            matches = [row for row in (stage.get('artifact') or {}).get('items',[]) if row['id']==row_id]
            if len(matches)!=1:
                raise NotFound('该版本中找不到唯一的成果条目')
            self._read_dialogue(db,ident,stage)
            _show_failure_origin(stage)
            return {'stage':stage,'historical':historical,'inputs':self._trace_inputs(db,run,stage)}

    @staticmethod
    def _read_dialogue(db, ident, stage):
        for item in stage.get('dialogue', []):
            worker = item.pop('worker',None)
            if item['status']!='pending':
                continue
            late = db.execute('SELECT * FROM stage_workflow_attempts WHERE run_id=? AND node_id=? AND operation_id=?',
                              (ident,stage['node_id'],item['id'])).fetchone()
            if late:
                item.update(_checked(late)['outcome'])
            elif not _alive(worker):
                item.update(status='failed',error='上一次咨询已中断')

    def define(self, pid, conversation, op, expected, value):
        definition = _definition(value)
        ident = definition['id']
        if ident == 'default':
            raise Conflict('请复制默认工作流后保存自定义版本')
        checksum = digest(['define',pid,conversation,definition,expected])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            self._scope(db,pid,conversation,write=True)
            old = self._operation(db,op,checksum)
            if old:
                return self._definition(db,pid,ident,old['definition_revision'])
            self._save_definition(db,pid,definition,expected,op,checksum)
        return definition

    @staticmethod
    def _save_definition(db, pid, definition, expected, operation, checksum):
        ident = definition['id']
        revision = db.execute('SELECT coalesce(max(revision),0) FROM stage_workflow_definitions WHERE project_id=? AND id=?',
                              (pid,ident)).fetchone()[0]
        if revision != expected:
            raise Conflict('工作流定义已更新，请重新打开配置')
        definition['revision'] = revision+1
        db.execute('INSERT INTO stage_workflow_definitions VALUES (?,?,?,?,?)',
                   (pid,ident,revision+1,canonical(definition),digest(definition)))
        db.execute('INSERT INTO stage_workflow_operations VALUES (?,?,?,?,1)',(operation,checksum,ident,revision+1))

    def _improvement_source(self, db, pid, conversation, ident, node_id, sha, *, write=False):
        owner, child = self._scope(db,pid,conversation,write=write)
        if child != ident:
            raise NotFound('请从经验所属的原阶段会话打开改进记录')
        run = self._run(db,pid,owner,ident)
        stage, _ = self._trace_stage(db,run,conversation,node_id)
        if stage['status']!='approved' or stage.get('approval_sha256')!=sha:
            raise NotFound('找不到这一版已审核的阶段经验')
        return run,self._approval_snapshot(db,ident,stage)

    def improvements(self, pid, conversation, ident, node_id, sha):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self._improvement_source(db,pid,conversation,ident,node_id,sha)
            return [_checked(row) for row in db.execute(
                'SELECT * FROM stage_workflow_improvements WHERE run_id=? AND node_id=? '
                'AND conversation_id=? AND approval_sha256=? ORDER BY rowid DESC LIMIT 40',
                (ident,node_id,conversation,sha))]

    def definition_improvements(self, pid, conversation, ident, revision, *, before=None, operation=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            self._scope(db,pid,conversation)
            definition = self._definition(db,pid,ident,revision)
            if definition['revision']!=revision:
                raise NotFound('找不到所选工作流定义版本')
            query = ('SELECT i.*,o.definition_revision'+IMPROVEMENT_DESTINATIONS+
                     ' AND o.entity_id=? AND o.definition_revision<=?')
            args = (pid,ident,revision)
            if operation is not None:
                row = db.execute(query+' AND i.operation_id=?',(*args,operation)).fetchone()
                receipt = _checked(row)
                if (receipt['definition']['id']!=ident or receipt['definition']['revision']!=row['definition_revision']
                        or receipt['operation_id']!=row['operation_id']):
                    raise Conflict('改进回执与工作流定义不一致')
                source = receipt['source']
                if any(source[key]!=row[column] for key,column in (
                        ('run_id','run_id'),('node_id','node_id'),('conversation_id','conversation_id'),
                        ('approval_sha256','approval_sha256'))):
                    raise Conflict('改进回执与原审核来源不一致')
                run, approval = self._improvement_source(db,pid,source['conversation_id'],source['run_id'],
                                                          source['node_id'],source['approval_sha256'])
                quotes = approval['artifact'].get(source['field'],[])
                index = source['index']
                if not 0<=index<len(quotes) or digest(quotes[index])!=source['text_sha256']:
                    raise Conflict('改进回执中的原文校验失败')
                node = next(n for n in run['definition']['nodes'] if n['id']==source['node_id'])
                historical = not any(s['stage_session_id']==source['conversation_id'] for s in run['stages'])
                return {'run_id':run['id'],'node_id':node['id'],'stage_name':node['name'],'stage_session_id':source['conversation_id'],
                        'approval_sha256':source['approval_sha256'],'approved_at':approval['at'],'historical':historical,
                        'field':source['field'],'quote':quotes[index]}

            if before is not None:
                query += ' AND o.definition_revision<?'
                args += (before,)
            limit = 20
            rows = db.execute(query+' ORDER BY o.definition_revision DESC LIMIT ?',(*args,limit+1)).fetchall()
            nodes = {node['id']:node for node in definition['nodes']}
            records = []
            for row in rows[:limit]:
                receipt = _checked(row)
                saved = receipt['definition']
                if saved['id']!=ident or saved['revision']!=row['definition_revision'] or receipt['operation_id']!=row['operation_id']:
                    raise Conflict('改进回执与工作流定义不一致')
                node = nodes.get(receipt['target_node_id'])
                state = 'node_removed'
                if node is not None:
                    present = receipt['capability_id'] in node['capabilities'][receipt['category']]
                    state = 'matches' if present==(receipt['change']=='add') else 'changed'
                fields = ('operation_id','at','target_name','category','change','capability_id')
                records.append({**{field:receipt[field] for field in fields},'revision':saved['revision'],'state':state})
            return {'records':records,'next_before':records[-1]['revision'] if len(rows)>limit else None}

    def apply_improvement(self, pid, conversation, ident, node_id, operation, request):
        from packages.dynamic_workflow.kernel_settings import selected_capabilities
        sha = request['approval_sha256']
        checksum = digest(['workflow-improvement',pid,conversation,ident,node_id,request])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run,approval = self._improvement_source(db,pid,conversation,ident,node_id,sha,write=True)
            previous = self._operation(db,operation,checksum)
            if previous:
                receipt = _checked(db.execute('SELECT * FROM stage_workflow_improvements WHERE operation_id=?',
                                              (operation,)).fetchone())
                definition = self._definition(db,pid,previous['entity_id'],previous['definition_revision'])
                return {'receipt':receipt,'definition':definition}
            key,index = request['source_field'],request['source_index']
            suggestions = approval['artifact'].get(key,[])
            if index>=len(suggestions):
                raise NotFound('这一版成果中找不到所选的经验或建议')
            basis = run['definition']
            definition = _definition(basis)
            target = next((node for node in definition['nodes'] if node['id']==request['target_node_id']),None)
            if target is None:
                raise NotFound('原工作流定义中找不到目标节点')
            category,change = request['category'],request['change']
            capability = _text(request['capability_id'],'能力编号',300)
            allowed = target['capabilities'][category]
            ref = None
            if change=='add':
                if capability in allowed:
                    raise Conflict('目标节点已经允许此能力')
                try:
                    _,_,_,refs = selected_capabilities(self.data_dir,{category:[capability]})
                except (ValueError,NotFound,Conflict) as exc:
                    raise Conflict('所选能力已变化或停用，请重新选择。') from exc
                ref = refs.get(category,{}).get(capability)
                if (ref or {}).get('sha256')!=request.get('capability_sha256'):
                    raise Conflict('所选能力版本已改变，请重新载入能力目录。')
                target['capabilities'][category] = _strings([*allowed,capability])
            else:
                if capability not in allowed:
                    raise Conflict('目标节点未允许此能力')
                target['capabilities'][category] = [item for item in allowed if item!=capability]
            if request['mode']=='copy':
                definition['id'] = 'wf-'+digest(['stage-improvement',operation])[:32]
                definition['name'] = _text(request.get('copy_name'),'工作流副本名称',100)
                expected = 0
            else:
                if basis['id']=='default':
                    raise Conflict('默认工作流请另存副本')
                expected = basis['revision']
            self._save_definition(db,pid,definition,expected,operation,checksum)
            receipt = {'operation_id':operation,'at':now(),
                'source':{'run_id':ident,'node_id':node_id,'conversation_id':conversation,
                          'approval_sha256':sha,'field':key,'index':index,'text_sha256':digest(suggestions[index])},
                'base_definition':{'id':basis['id'],'revision':basis['revision']},
                'definition':{'id':definition['id'],'name':definition['name'],'revision':definition['revision']},
                'target_node_id':target['id'],'target_name':target['name'],'category':category,'change':change,
                'capability_id':capability,'capability_ref':ref}
            db.execute('INSERT INTO stage_workflow_improvements VALUES (?,?,?,?,?,?,?)',
                       (operation,ident,node_id,conversation,sha,canonical(receipt),digest(receipt)))
        return {'receipt':receipt,'definition':definition}

    def _pin(self, config):
        from packages.dynamic_workflow.kernel_settings import freeze_policy
        try:
            return freeze_policy(self.data_dir,config)
        except (ValueError, RuntimeError) as exc:
            raise InvalidInput(str(exc)) from None

    def _reuse(self, context, key):
        pid, owner = context['project_id'],context['conversation_id']
        with self.store.connect() as db:
            db.execute('BEGIN')
            row = db.execute('SELECT * FROM stage_workflow_planning_reuse WHERE project_id=? AND '
                             'conversation_id=? AND input_sha256=?',(pid,owner,key)).fetchone()
            if row is None or row['source_run_id']==context['run_id']:
                return None
            source_id, node_id = row['source_run_id'], row['source_node_id']
            try:
                source = self._run(db,pid,owner,source_id)
                approval = _checked(db.execute('SELECT * FROM stage_workflow_approvals WHERE run_id=? AND '
                    'node_id=? AND sha256=?',(source_id,node_id,row['approval_sha256'])).fetchone())
                session = approval['stage_session_id']
                if self._scope(db,pid,session,write=True) != (owner, source_id):
                    return None
                stage, _ = self._trace_stage(db,source,session,node_id)
            except (Conflict,NotFound):
                return None
            # A revision creates a successor session. The cache still names
            # the distinct approval of the original planning input.
            if (stage['status']!='approved' or stage.get('approval_sha256')!=row['approval_sha256']
                    or stage.get('planning_input_sha256')!=key or stage['artifact']!=approval['artifact']):
                return None
            return {'artifact':approval['artifact'],'verified_evidence':[],
                    'reused_from':{'run_id':source_id,'node_id':node_id,
                                   'stage_session_id':session,'approval_sha256':row['approval_sha256']}}

    def planning_source(self, pid, conversation, ident, node_id, operation):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请从复用成果所属的原阶段会话打开审批依据')
            destination = self._run(db,pid,owner,ident)
            stage, _ = self._trace_stage(db,destination,conversation,node_id)
            if stage.get('operation_id') != operation:
                raise Conflict('当前成果版本已变化，请重新打开原审批')
            origin = stage.get('reused_from')
            if not origin or stage['status'] not in {'review','approved'}:
                raise NotFound('该版本没有已保存的复用依据')
            source_owner, source_child = self._scope(db,pid,origin['stage_session_id'])
            if source_owner != owner or source_child != origin['run_id'] or source_child == ident:
                raise NotFound('找不到本会话内的原审批')
            source = self._run(db,pid,owner,origin['run_id'])
            approved, historical = self._trace_stage(db,source,origin['stage_session_id'],origin['node_id'])
            if approved['status'] != 'approved' or approved.get('approval_sha256') != origin['approval_sha256']:
                raise NotFound('找不到所选版本的原审批')
            approval = self._approval_snapshot(db,source['id'],approved)
            if approval['artifact'] != stage['artifact']:
                raise Conflict('原审批与这一版复用成果不一致')
            node = next(n for n in source['definition']['nodes'] if n['id']==approved['node_id'])
            return {**origin,'operation_id':operation,'stage_kind':node['kind'],'stage_name':node['name'],
                    'approved_at':approval['at'],'historical':historical,
                    'artifact':approval['artifact'],'source_urs':approved.get('source_urs') or {}}

    def _activate(self, db, run, index):
        stage = run['stages'][index]
        if stage['stage_session_id']:
            run['active_node_id'] = stage['node_id']
            return
        node = run['definition']['nodes'][index]
        version = 1 + sum(entry['event'] in ('rework','revised') for entry in stage['log'])
        title = node['name'] if version == 1 else f"{node['name']} · v{version}"
        child = self.create_child(db, run['project_id'], title)
        child_id = child['conversation_ref']['id']
        db.execute('INSERT INTO stage_workflow_children VALUES (?,?,?)',(child_id,run['id'],node['id']))
        stage.update(stage_session_id=child_id,status='ready')
        stage['log'].append({'event':'activated','at':now()})
        run['active_node_id'] = node['id']

    def _save(self, db, run):
        run['revision'] += 1
        run['updated_at'] = now()
        db.execute('UPDATE stage_workflow_runs SET body=?,sha256=? WHERE id=?',(canonical(run), digest(run),run['id']))

    def start(self, pid, conversation, op, definition_id, expected_definition_revision=None):
        checksum = digest(['start',pid,conversation,definition_id,expected_definition_revision])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owner, child = self._scope(db,pid,conversation,write=True)
            if child:
                raise Conflict('请在所属会话中创建另一次工作流')
            old = self._operation(db,op,checksum)
            if old:
                return self._run_view(db,self._run(db,pid,owner,old['entity_id']))
            definition = self._definition(db,pid,definition_id)
            if expected_definition_revision is not None and expected_definition_revision != definition['revision']:
                raise Conflict('当前选择的工作流定义已更新')
            ident, stamp = 'wfr_'+uuid.uuid4().hex, now()
            run = {'id':ident,'contract_version':CONTRACT_VERSION,'revision':0,'status':'active','project_id':pid,'conversation_id':owner,
                   'created_at':stamp,'updated_at':stamp,'active_node_id':None,'definition':definition,
                   'stages':[{'node_id':n['id'],'stage_session_id':None,'status':'pending','artifact':None,
                              'verified_evidence':[],'questions':[],'annotations':[],'notes':[],'log':[],
                              'user_input':{},'template':None,'config':_config(n),'error':None} for n in definition['nodes']]}
            pins = {}
            for stage in run['stages']:
                key = digest(stage['config'])
                if key not in pins:
                    pins[key] = self._pin(stage['config'])
                stage['policy_pin'] = pins[key]
            db.execute('INSERT INTO stage_workflow_runs VALUES (?,?,?,?,?)',(ident,pid,owner,canonical(run),digest(run)))
            self._activate(db,run,0)
            self._save(db,run)
            db.execute('INSERT INTO stage_workflow_operations VALUES (?,?,?,NULL,1)',(op,checksum,ident))
            self._record_transition(db,run,op,'start',conversation,None,None,definition['name'])
            return run

    def save_template(self, pid, conversation, template, raw: bytes | None = None):
        _json(template, 2*1024*1024)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owner, _ = self._scope(db,pid,conversation,write=True)
            ident = digest(template)
            template = {**template, 'id':ident}
            if raw is not None:
                from packages.dynamic_workflow.stage_template_export import store_original
                store_original(db,pid,owner,template,raw)
            db.execute('INSERT OR IGNORE INTO stage_workflow_templates VALUES (?,?,?,?,?)',
                       (pid,owner,ident,canonical(template),digest(template)))
        return template

    def _approved(self, db, run, node, *, subjects=None):
        inputs, by_kind, evidence = {}, {}, {}
        nodes = {n['id']:n for n in run['definition']['nodes']}
        visible = self._trace_visibility(db,run) if subjects is not None else None
        for ident in node['inputs']:
            source = next(s for s in run['stages'] if s['node_id'] == ident)
            if source['status'] != 'approved' or not source.get('approval_sha256'):
                raise Conflict('节点依赖尚未完成本轮审核')
            record = _checked(db.execute('SELECT * FROM stage_workflow_approvals WHERE run_id=? AND node_id=? AND sha256=?',
                                         (run['id'],ident,source['approval_sha256'])).fetchone())
            artifact = record['artifact']
            inputs[ident] = artifact
            evidence.update({r['id']:r for r in record['verified_evidence']})
            kind = nodes[ident]['kind']
            if kind=='development' and visible and visible(source['stage_session_id']):
                self._collect_subjects(source,record,subjects,
                    pin_targets=run.get('contract_version') in RUNTIME_TARGET_CONTRACT_VERSIONS)
            if kind not in by_kind:
                by_kind[kind] = deepcopy(artifact)
            else:
                by_kind[kind]['items'].extend(deepcopy(artifact['items']))
                by_kind[kind]['summary'] += '\n'+artifact['summary']
        return inputs,by_kind,list(evidence.values())

    @staticmethod
    def _collect_subjects(source, approval, subjects, *, pin_targets=False):
        if pin_targets:
            files = set(approval.get('implementation_subject_receipts',[]))
        else:
            refs = {ref for row in approval['artifact']['items'] if row.get('status')=='done'
                    for ref in row.get('evidence_refs',[])}
            files = {ref for record in approval['verified_evidence'] if record.get('kind')=='design_item'
                     and record['id'] in refs and record.get('status')!='failed'
                     for ref in record.get('file_refs',[])}
        for record in approval['verified_evidence']:
            if record.get('kind')!='file' or record['id'] not in files:
                continue
            path = record['path']
            identity = {'path':path,'sha256':record['sha256'],'file_receipt_id':record['id'],
                        'node_id':source['node_id'],'stage_session_id':source['stage_session_id'],
                        'approval_sha256':source['approval_sha256']}
            subjects[path] = identity if path not in subjects else None

    @staticmethod
    def _requires_runtime_target(run, node):
        return run.get('contract_version') in RUNTIME_TARGET_CONTRACT_VERSIONS and any(
            source['id'] in node['inputs'] and source['kind']=='development' for source in run['definition']['nodes'])

    @staticmethod
    def _input_versions(run, inputs):
        return {source['node_id']:{'stage_session_id':source['stage_session_id'],
                                   'approval_sha256':source['approval_sha256']}
                for source in run['stages'] if source['node_id'] in inputs}

    @staticmethod
    def _execution_input(stage, follow_ups):
        user_input = deepcopy(stage['user_input'])
        instructions = [item['text'] for item in follow_ups if not item.get('source_id')]
        requirements = {item['source_id']:item['text'] for item in follow_ups if item.get('source_id')}
        if instructions:
            user_input['stage_follow_ups'] = instructions
        if requirements:
            user_input['stage_urs'] = requirements
        return user_input

    def _pending_connector_retry(self, db, run, stage):
        selected = set(stage['config']['capabilities']['mcp'])
        if not selected:
            return []
        acknowledged = {ident for event in stage['log'] if event['event']=='started'
                        for ident in event.get('connector_retry_ack', {}).get('evidence_ids', [])}
        def pending(source):
            return [record for record in source['verified_evidence']
                    if record.get('kind') in ('connector_call','check') and record.get('status')=='unconfirmed'
                    and record.get('server') in selected and record.get('tool') and record['id'] not in acknowledged]

        fields = ('tool','case_id','recorded_at')
        entries = {}
        previous = {event['previous_session_id'] for event in stage['log']
                    if event['event'] in ('rework','revised') and event.get('previous_session_id')}
        if previous:
            archives = db.execute(
                "SELECT h.* FROM json_each(?) AS parent JOIN stage_workflow_stage_history h ON h.conversation_id=parent.value "
                "WHERE h.run_id=? AND h.node_id=? AND EXISTS (SELECT 1 FROM json_each(h.body,'$.verified_evidence') AS e "
                "WHERE json_extract(e.value,'$.status')='unconfirmed') ORDER BY h.rowid",
                (canonical(sorted(previous)),run['id'],stage['node_id']))
            visible = self._trace_visibility(db,run)
            for archived in archives:
                records = pending(_checked(archived))
                if not records:
                    continue
                allowed = visible(archived['conversation_id'])
                for record in records:
                    entries.setdefault(record['id'], {'id':record['id'],'server':record['server'],'previous':True,
                        **{key:record[key] for key in fields if allowed and key in record}})
        for record in pending(stage):
            entries.setdefault(record['id'], {'id':record['id'],'server':record['server'],'previous':False,
                **{key:record[key] for key in fields if key in record}})
        return list(entries.values())

    def _run_view(self, db, run):
        # Response-only projection. Old evidence stays with the original attempt.
        self._refresh_active_test_inputs(db,run)
        for item in run['stages']:
            _show_failure_origin(item)
        stage = next((item for item in run['stages'] if item['node_id']==run['active_node_id']),None)
        if stage and stage['status'] in ('ready','needs_input','review','failed'):
            pending = self._pending_connector_retry(db,run,stage)
            if pending:
                stage['connector_retry'] = {'operation_id':stage.get('operation_id'),'evidence':pending}
        return run

    def _prepare(self, db, run, index, payload, op):
        _fields(payload, ('user_input','template','annotation_ids','connector_retry_ack'))
        stage, node = run['stages'][index],run['definition']['nodes'][index]
        pending = {record['id'] for record in self._pending_connector_retry(db,run,stage)}
        acknowledgement = None
        if pending or 'connector_retry_ack' in payload:
            supplied = payload.get('connector_retry_ack', {})
            _fields(supplied, ('operation_id','evidence_ids'))
            evidence_ids = _strings(supplied.get('evidence_ids', []),limit=4096)
            if (not pending or 'operation_id' not in supplied or supplied['operation_id'] != stage.get('operation_id')
                    or set(evidence_ids) != pending):
                raise Conflict('请核对当前阶段未确认的连接器调用后，再确认执行')
            acknowledgement = {'operation_id':stage.get('operation_id'),'evidence_ids':sorted(pending)}
        additions = payload.get('user_input', {})
        if not isinstance(additions, dict):
            raise InvalidInput('阶段输入需要是表单字段')
        if additions.keys() & {'stage_urs','stage_follow_ups'}:
            raise InvalidInput('请从阶段对话提交新增需求或说明')
        stage['user_input'] = _json({**stage['user_input'], **additions})
        if 'template' in payload:
            template = payload['template']
            if template is not None:
                if not isinstance(template,dict) or not isinstance(template.get('id'),str):
                    raise InvalidInput('请选择已解析的模板')
                template = _checked(db.execute('SELECT * FROM stage_workflow_templates WHERE project_id=? '
                    'AND conversation_id=? AND id=?',(run['project_id'],run['conversation_id'],template['id'])).fetchone())
            stage['template'] = template
        ids = _strings(payload.get('annotation_ids',[]))
        annotations = [a for a in stage['annotations'] if a['id'] in ids and a['status']=='open']
        if len(annotations) != len(ids):
            raise Conflict('选中的批注已变化')
        subjects = {} if node['kind']=='testing' else None
        inputs,by_kind,evidence = self._approved(db,run,node,subjects=subjects)
        if stage.get('rework_basis'):
            basis = _checked(db.execute('SELECT * FROM stage_workflow_approvals WHERE run_id=? AND node_id=? AND sha256=?',
                                        (run['id'],node['id'],stage['rework_basis'])).fetchone())
            evidence = [*evidence, *basis['verified_evidence']]
        follow_ups = [item for item in stage.get('follow_ups', [])
                      if not item.get('withdrawn_at') and (not ids or item.get('processed_by'))]
        user_input = self._execution_input(stage,follow_ups)
        context = {**stage['config'],'run_id':run['id'],'node_id':node['id'],'kind':node['kind'],
                   'project_id':run['project_id'],'conversation_id':run['conversation_id'],
                   'stage_session_id':stage['stage_session_id'],'operation_id':op,'inputs':inputs,
                   'contract_version':run.get('contract_version',LEGACY_CONTRACT_VERSION),
                   'approved_by_kind':by_kind,'approved_evidence':evidence,'prior_evidence':stage['verified_evidence'],'instructions':node['instructions'],
                   'user_input':user_input,'template':stage['template'],'annotations':annotations,
                   'prior_artifact':stage['artifact'],'rework_request':stage.get('rework_request'),'policy_pin':stage['policy_pin'],'_data_dir':self.data_dir}
        if subjects:
            context['approved_subjects'] = subjects
        if node['kind']=='testing' and self._requires_runtime_target(run,node):
            context['require_runtime_target'] = True
        if context['contract_version'] in TRACE_CONTRACT_VERSIONS and stage.get('approval_issues'):
            context['approval_feedback'] = stage['approval_issues']
        context['_follow_up_ids'] = {item['id'] for item in follow_ups if not item.get('processed_by')}
        context['_database'] = str(self.store.database)
        context['_owner'] = _runtime_owner()
        stage.update(status='running',error=None,operation_id=op,worker=context['_owner'])
        input_versions = self._input_versions(run, inputs)
        started = {'event':'started','at':now(),'operation_id':op,'kernel':context['kernel'],'annotation_ids':ids,
                   'follow_up_ids':sorted(context['_follow_up_ids']),
                   'inputs':{k:digest(v) for k,v in inputs.items()},'input_versions':input_versions,
                   'config_sha256':digest(stage['config'])}
        if acknowledgement:
            started['connector_retry_ack'] = acknowledgement
            context['connector_retry_ack'] = acknowledgement
        stage['log'].append(started)
        return context

    def _progress(self, ident, op, node_id, completed, total, message):
        if (type(total) is not int or type(completed) is not int or
                not 0 <= completed <= total or total < 1):
            raise ValueError('无效的执行进度')
        event = {'completed':completed,'total':total,'message':_text(message,'进度',500),'at':now()}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = _checked(db.execute('SELECT * FROM stage_workflow_runs WHERE id=?',(ident,)).fetchone())
            stage = next(s for s in run['stages'] if s['node_id'] == node_id)
            if stage.get('operation_id') != op or stage['status'] != 'running':
                raise Conflict('原阶段执行已结束')
            stage['progress'] = event
            stage['log'].append({'event':'progress','operation_id':op,**event})
            self._save(db,run)

    def _stop_requested(self, ident, op):
        with self.store.connect() as db:
            return db.execute("SELECT 1 FROM stage_workflow_runs r, json_each(r.body,'$.stages') s "
                "WHERE r.id=? AND json_extract(s.value,'$.operation_id')=? "
                "AND json_type(s.value,'$.stop_request')='object'",(ident,op)).fetchone() is not None

    @staticmethod
    def _execution_evidence(context):
        from packages.dynamic_workflow.stage_workspace import operation_receipts
        try:
            return {'verified_evidence':operation_receipts(context)}
        except (OSError, RuntimeError, sqlite3.Error):
            return {}

    @staticmethod
    def _approved_records(stage, upstream=()):
        files = {(r['path'],r['sha256']) for r in stage['artifact']['items'] if 'path' in r and 'sha256' in r}
        delivered = [r for r in upstream if r.get('kind')=='file' and (r['path'],r['sha256']) in files]
        refs = {ref for item in stage['artifact']['items'] for ref in item.get('evidence_refs',[])}
        design_ids = {ident for row in stage['artifact']['items'] for ident in row.get('design_ids',[])}
        builds = {r['design_id']:r for r in stage['verified_evidence'] if r.get('kind')=='build'}
        for ident in design_ids:
            if ident in builds:
                refs.add(builds[ident]['id'])
        for record in stage['verified_evidence']:
            if record['id'] in refs and record.get('kind') == 'design_item':
                refs.update(record.get('file_refs',[]))
        # The audit ledger retains drafts. Other stages see only versions that
        # belong to the reviewed items, including the files those items close.
        return [*delivered, *(r for r in stage['verified_evidence'] if r['id'] in refs or
                              r.get('kind')=='file' and (r['path'],r['sha256']) in files)]

    @staticmethod
    def _latest_checks(records):
        checks = {}
        for record in records:
            if record.get('kind') not in CHECK_KINDS:
                continue
            if record.get('action_sha256'):
                identity = (record.get('case_id'),record['action_sha256'])
            else:
                identity = tuple(record.get(f) for f in ('case_id','path','method','server','tool','pointer',
                                         'arguments_sha256','expected_sha256','argv_sha256','image_id'))
            checks[identity] = record
        return list(checks.values())

    @staticmethod
    def _evidence_issues(kind, artifact, records, *, designs=None):
        by_id = {r['id']:r for r in records}
        development = DevelopmentEvidence(records) if kind=='development' else None
        checks = StageRuns._latest_checks(records) if kind=='testing' else []
        for item in artifact['items']:
            refs = set(item.get('evidence_refs', []))
            if refs - by_id.keys():
                return [f"{item['id']} 引用了未经核实的回执"]
            if kind == 'development':
                for design_id in item['design_ids']:
                    issue = development.completion_issue(design_id,refs,design=(designs or {}).get(design_id))
                    if issue:
                        return [issue]
            elif kind == 'testing':
                case_id = item['case_id']
                results = [r for r in checks if r.get('case_id') == case_id]
                if not results or any(r['id'] not in refs or not passing_check(r) for r in results):
                    return [f"{case_id} 尚缺该用例全部检查的最新通过回执"]
            elif kind == 'delivery':
                if not any(r.get('kind')=='file' and r.get('path')==item['path'] and
                           r.get('sha256')==item['sha256'] for r in records):
                    return [f"{item['id']} 尚缺对应文件及内容摘要的执行回执"]
        return []

    @staticmethod
    def _build_issues(approved, records, upstream=()):
        recipes = {row['id']:row['build'] for row in approved.get('design',{}).get('items',[]) if row.get('build')}
        if not recipes:
            return []
        evidence = DevelopmentEvidence(records,upstream)
        for ident, recipe in recipes.items():
            issue = evidence.build_issue(ident,recipe)
            if issue:
                return [issue]
        return []

    def _test_input_issues(self, run, stage, by_kind, upstream, subjects):
        from packages.dynamic_workflow.stage_workspace import file_list
        ctx = {'_data_dir':self.data_dir,'project_id':run['project_id'],'run_id':run['id'],
               'stage_session_id':stage['stage_session_id'],'approved_evidence':upstream}
        evidence = TestEvidence(stage['verified_evidence'],upstream,lambda: file_list(ctx),subjects)
        issues = evidence.stale_cases(by_kind.get('cases', {}).get('items', []))
        if issues:
            stage['test_input_issues'] = issues
        else:
            stage.pop('test_input_issues',None)
        return [issue for messages in issues.values() for issue in messages]

    def _refresh_active_test_inputs(self, db, run):
        for node,stage in zip(run['definition']['nodes'],run['stages']):
            if (node['id']==run['active_node_id'] and node['kind']=='testing' and stage.get('artifact')
                    and stage['status'] in ('review','failed','needs_input')):
                if not any(row.get('kind')=='check' and passing_check(row) for row in stage['verified_evidence']):
                    return
                prior = {issue for messages in stage.get('test_input_issues', {}).values() for issue in messages}
                subjects = {}
                _,by_kind,upstream = self._approved(db,run,node,subjects=subjects)
                current = self._test_input_issues(run,stage,by_kind,upstream,subjects)
                other = [issue for issue in stage.get('approval_issues', []) if issue not in prior]
                stage['approval_issues'] = current + other
                break

    def _issues(self, db, run, index, inputs=None, *, archive_members=None):
        node, stage = run['definition']['nodes'][index], run['stages'][index]
        subjects = {} if node['kind']=='testing' else None
        approved_inputs,by_kind,upstream = self._approved(db,run,node,subjects=subjects)
        if inputs is None:
            follow_ups = [item for item in stage.get('follow_ups', []) if not item.get('withdrawn_at')]
            inputs = self._execution_input(stage,follow_ups)
        issues = validate_artifact(node['kind'],stage['artifact'],by_kind,inputs=inputs,template=stage['template'],
                                   version=run.get('contract_version',LEGACY_CONTRACT_VERSION))
        if not issues and node['kind']=='cases' and any(
                target['kind']=='testing' and node['id'] in target['inputs'] and self._requires_runtime_target(run,target)
                for target in run['definition']['nodes']):
            issues.extend(container_target_issues(stage['artifact']['items']))
        if node['kind']=='testing' and self._requires_runtime_target(run,node):
            issues.extend(container_target_issues(by_kind.get('cases',{}).get('items',[]),
                {path for path, identity in subjects.items() if identity}))
        if not issues:
            records = stage['verified_evidence']
            if node['kind'] == 'testing':
                pinned = self._input_versions(run, approved_inputs)
                records = [r for r in records if r.get('kind') != 'manual_check' or
                           (r.get('stage_session_id') == stage['stage_session_id'] and r.get('input_versions') == pinned)]
            if node['kind']=='delivery':
                records = [*records,*upstream]
            designs = None
            if node['kind']=='development' and run.get('contract_version') in TRACE_CONTRACT_VERSIONS:
                designs = {row['id']:row for row in by_kind.get('design',{}).get('items',[])}
            issues.extend(self._evidence_issues(node['kind'],stage['artifact'],records,designs=designs))
            if node['kind']=='delivery' and not issues:
                from packages.dynamic_workflow.stage_delivery import review_delivery_members
                try:
                    members = review_delivery_members(self.data_dir,stage['artifact'],stage['verified_evidence'],upstream)
                except ValueError as exc:
                    issues.append(str(exc))
                if not issues and archive_members is not None:
                    archive_members.update(members)
            if node['kind'] == 'development':
                issues.extend(self._build_issues(by_kind,records,upstream))
            if node['kind'] == 'testing':
                from packages.dynamic_workflow.stage_contracts import case_receipt_issues
                issues.extend(case_receipt_issues(stage['artifact'],by_kind,records))
        if node['kind'] == 'testing':
            return self._test_input_issues(run,stage,by_kind,upstream,subjects) + issues
        return issues

    @staticmethod
    def _implementation_subject_receipts(records, designs):
        planned = {row['id']:set(planned_design_files(row)) for row in designs}
        files = {row['id']:row['path'] for row in records if row['kind']=='file'}
        return sorted({ref for row in records if row['kind']=='design_item'
            for ref in row['file_refs'] if files.get(ref) in planned.get(row['design_id'],())})

    @staticmethod
    def _manual_case(approved, case_id, action_id):
        cases = approved.get('cases', {}).get('items', [])
        if len({case['id'] for case in cases}) != len(cases):
            raise Conflict('测试用例编号重复，无法唯一归属人工记录')
        case = next((item for item in cases if item['id'] == case_id), None)
        action = next((item for item in (case or {}).get('actions', [])
                       if item['id'] == action_id and item['method'] == MANUAL_METHOD), None)
        if action is None:
            raise InvalidInput('请选择已审核的人工实测动作')
        return cases, case, action

    def _record_manual_check(self, db, run, index, op, payload):
        _fields(payload, ('case_id','action_id','status','environment','observation','bench','attachments'))
        node, stage = run['definition']['nodes'][index], run['stages'][index]
        if node['kind'] != 'testing' or stage['status'] not in ('ready','review'):
            raise Conflict('只能在当前待执行或待审核的测试阶段记录人工实测')
        if payload.get('status') not in ('passed','failed','blocked'):
            raise InvalidInput('请选择通过、失败或受阻')
        case_id = _text(payload.get('case_id'),'用例编号',100)
        action_id = _text(payload.get('action_id'),'验收动作编号',100)
        environment = _text(payload.get('environment'),'实测环境',500)
        observation = _text(payload.get('observation'),'实测观察',4000)
        inputs, approved, _ = self._approved(db,run,node)
        cases, case, action = self._manual_case(approved,case_id,action_id)
        at = now()
        pinned = self._input_versions(run, inputs)
        record = {'id':'manual_'+uuid.uuid4().hex,'kind':'manual_check','source':'local_user',
                  'case_id':case_id,'action_id':action_id,'action_sha256':action_digest(action),
                  'case_sha256':action_digest(case),'status':payload['status'],'environment':environment,
                  'observation':observation,'at':at,'operation_id':op,'stage_session_id':stage['stage_session_id'],
                  'input_versions':pinned}
        if 'bench' in payload:
            record['bench'] = _manual_bench(payload['bench'],self._bench_firmware(db,run,node))
        attachments = save_manual_files(db,run['id'],payload.get('attachments',[]))
        if attachments:
            record['attachments'] = attachments
        if stage['status'] == 'ready':
            artifact = {'summary':'按已审核测试用例登记的实测结果。','items':[
                {'id':item['id'],'title':item['title'],'case_id':item['id'],'status':'not_run',
                 'evidence_refs':[],'rework':[]} for item in cases]}
            stage.update(artifact=artifact,status='review',operation_id=op,error=None)
            stage['log'].append({'event':'manual_results_started','at':at,'operation_id':op,
                                 'inputs':{key:digest(value) for key,value in inputs.items()},'input_versions':pinned})
        stage['verified_evidence'].append(record)
        apply_manual_checks(stage['artifact'],approved,stage['verified_evidence'])
        stage.pop('progress',None)
        stage['approval_issues'] = self._issues(db,run,index)
        stage['log'].append({'event':'manual_check_recorded','at':at,'operation_id':op,
                             'case_id':case_id,'action_id':action_id,'status':record['status']})

    def manual_file(self, pid, conversation, ident, node_id, receipt_id, file_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请从原测试会话查看实测证据')
            run = self._run(db,pid,owner,ident)
            stage, _ = self._trace_stage(db,run,conversation,node_id)
            receipt = next((item for item in stage['verified_evidence']
                            if item['kind']=='manual_check' and item['id']==receipt_id and
                            item['stage_session_id']==conversation),None)
            file = next((item for item in (receipt or {}).get('attachments',[])
                         if item['id']==file_id),None)
            if not file:
                raise NotFound('此实测回执没有对应的原文件')
            return read_manual_file(db,ident,file)

    def _bench_firmware(self, db, run, node):
        files = {}
        visible = self._trace_visibility(db,run)
        for source_node, source_stage in zip(run['definition']['nodes'],run['stages']):
            source_id = source_node['id']
            session = source_stage['stage_session_id']
            if (source_id not in node['inputs'] or source_node['kind'] != 'development' or
                    source_stage['status'] != 'approved' or not visible(session)):
                continue
            for entry in self._approval_snapshot(db,run['id'],source_stage)['verified_evidence']:
                if entry['kind'] == 'file':
                    files[entry['id']] = {'id':entry['id'],'path':entry['path'],'sha256':entry['sha256'],
                                          'node_id':source_id,'stage_session_id':session,
                                          'approval_sha256':source_stage['approval_sha256']}
        return files

    def _previous_test_bench(self, db, run, stage, node, case_id, action_id):
        transition = next((entry for entry in reversed(stage['log'])
                           if entry['event'] in ('rework','revised')),None)
        if not transition or transition['event'] != 'rework':
            return None
        previous = transition['previous_session_id']
        selected = transition.get('case_ids')
        if selected is None:
            # Existing runs store the selected cases on the target development stage.
            request = next((item['rework_request'] for item in run['stages']
                            if item.get('rework_request',{}).get('from_session_id') == previous and
                            item['rework_request']['at'] == transition['at']),{})
            selected = request.get('case_ids',[])
        if case_id not in selected or not self._trace_visibility(db,run)(previous):
            return None
        prior, historical = self._trace_stage(db,run,previous,node['id'])
        if not historical:
            return None
        record = next((entry for entry in reversed(prior['verified_evidence'])
                       if entry['kind']=='manual_check' and entry['case_id']==case_id and
                       entry['action_id']==action_id),None)
        if (not record or not record.get('bench') or record['status'] not in ('failed','blocked') or
                record.get('stage_session_id') != previous):
            return None
        _, approved, _ = self._approved(db,run,node)
        _, case, action = self._manual_case(approved,case_id,action_id)
        if record.get('case_sha256') != action_digest(case) or record.get('action_sha256') != action_digest(action):
            return None
        bench = record['bench']
        setup = {key:bench[key] for key in ('host','fixture') if key in bench}
        setup['devices'] = [{key:item[key] for key in ('label','identity','link') if key in item}
                            for item in bench['devices']]
        return {'at':record['at'],'status':record['status'],'setup':setup}

    def bench_firmware(self, pid, conversation, ident, node_id, case_action=None):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请在当前测试阶段查看工位固件')
            run = self._run(db,pid,owner,ident)
            stage, historical = self._trace_stage(db,run,conversation,node_id)
            node = next(n for n in run['definition']['nodes'] if n['id'] == stage['node_id'])
            if historical or node['kind'] != 'testing':
                raise NotFound('请在当前测试阶段查看工位固件')
            response = {'files':list(self._bench_firmware(db,run,node).values())}
            if case_action and stage['status'] in ('ready','review'):
                previous = self._previous_test_bench(db,run,stage,node,*case_action)
                if previous:
                    response['previous_bench'] = previous
            return response

    def _annotate(self, stage, payload, op):
        _fields(payload, ('target','comment','anchor'))
        target = _text(payload.get('target'),'批注对象',300)
        if target != 'summary' and target not in {r.get('id') for r in stage['artifact']['items']}:
            raise InvalidInput('批注对象不存在')
        anchor = payload.get('anchor',{})
        if not isinstance(anchor, dict):
            raise InvalidInput('批注定位无效')
        field = anchor.get('field')
        if field is not None:
            source = stage['artifact'] if target == 'summary' else next(r for r in stage['artifact']['items'] if r['id']==target)
            if not isinstance(field,str) or field not in source:
                raise InvalidInput('批注字段不存在')
        stage['annotations'].append({'id':'ann_'+uuid.uuid4().hex,'target':target,
            'comment':_text(payload.get('comment'),'批注内容'),'anchor':_json(anchor,64000),
            'status':'open','at':now(),'artifact_sha256':digest(stage['artifact']),
            'created_operation_id':op})

    def _rework_cases(self, source, source_node):
        if source_node['kind'] != 'testing':
            raise Conflict('请从测试阶段发起开发返工')
        failed = {r['case_id'] for r in (source['artifact'] or {}).get('items', [])
                  if r.get('status') in ('failed','blocked')}
        observed = [r for r in self._latest_checks(source['verified_evidence'])
                    if r.get('status')=='failed' or r.get('kind')=='manual_check' and r.get('status')=='blocked']
        failed.update(r['case_id'] for r in observed)
        if not failed and source['status'] != 'failed':
            raise Conflict('尚无失败或受阻的测试需要返工')
        return failed, observed

    @staticmethod
    def _rework_targets(run, index):
        nodes, stages = run['definition']['nodes'], run['stages']
        targets = {}
        for i, node in enumerate(nodes[:index]):
            if node['kind'] != 'development' or stages[i]['status'] != 'approved' or not stages[i]['stage_session_id']:
                continue
            affected = {node['id']}
            for later in nodes[i+1:index+1]:
                if affected.intersection(later['inputs']):
                    affected.add(later['id'])
            if nodes[index]['id'] in affected:
                targets[node['id']] = (i, [j for j in range(i,index+1)
                    if nodes[j]['id'] in affected and stages[j]['stage_session_id']])
        return targets

    def rework_preview(self, pid, conversation, ident, node_id):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请从原测试阶段会话核对返工范围')
            run = self._run(db,pid,owner,ident)
            nodes = run['definition']['nodes']
            index, source = self._rework_source(run,conversation,node_id)
            failed, _ = self._rework_cases(source,nodes[index])
            targets = [{'id':target_id,'name':nodes[target]['name'],
                        'affected':[{'id':nodes[j]['id'],'name':nodes[j]['name']} for j in affected]}
                       for target_id,(target,affected) in self._rework_targets(run,index).items()]
            return {'revision':run['revision'],'case_ids':sorted(failed),'targets':targets}

    @staticmethod
    def _rework_source(run, conversation, node_id):
        index = next((i for i,stage in enumerate(run['stages']) if stage['node_id']==node_id and
                      stage['stage_session_id']==conversation),None)
        if index is None or run['active_node_id']!=node_id or run['status']!='active':
            raise Conflict('当前测试阶段已变化，请打开当前版本')
        source = run['stages'][index]
        if source['status']=='running':
            raise Conflict('请等待当前节点执行结束')
        return index, source

    def rework_evidence(self, pid, conversation, ident, node_id, revision, offset):
        with self.store.connect() as db:
            db.execute('BEGIN')
            owner, child = self._scope(db,pid,conversation)
            if child != ident:
                raise NotFound('请从测试或返工开发会话查看证据')
            run = self._run(db,pid,owner,ident)
            stage, _ = self._trace_stage(db,run,conversation,node_id)
            node = next(item for item in run['definition']['nodes'] if item['id']==stage['node_id'])
            if node['kind']=='testing':
                if revision!=run['revision']:
                    raise Conflict('返工范围已有变化，请重新打开返工表单')
                _, source = self._rework_source(run,conversation,node_id)
                _, records = self._rework_cases(source,node)
            else:
                request = stage.get('rework_request')
                if node['kind']!='development' or not request or request.get('target_node_id')!=node_id:
                    raise NotFound('当前阶段没有对应的返工证据')
                origin = request['from_session_id']
                if not self._trace_visibility(db,run)(origin):
                    raise NotFound('原测试证据暂不可读取')
                source, historical = self._trace_stage(db,run,origin,request['from_node_id'])
                if not historical:
                    raise NotFound('找不到返工时的原测试记录')
                by_identity = {(item['id'],item.get('case_id')):item for item in source['verified_evidence']}
                records = [by_identity[key] for item in request.get('observations',[])
                           if (key := (item['id'],item['case_id'])) in by_identity]
            fields = ('id','kind','case_id','action_id','status','method','path','exit_code','detail','subject',
                      'environment','observation','at','recorded_at','attachments','bench','server','tool')
            end = offset + 12
            return {'node_id':source['node_id'],'stage_session_id':source['stage_session_id'],
                    'error':source.get('error'),'total':len(records),'offset':offset,
                    'next_offset':end if end<len(records) else None,
                    'records':[{**{key:item[key] for key in fields if key in item},**_receipt_observation(item)}
                               for item in records[offset:end]]}

    def _rework(self, db, run, index, payload):
        _fields(payload, ('target_node_id','reason','case_ids'))
        nodes, stages = run['definition']['nodes'], run['stages']
        source, source_node = stages[index], nodes[index]
        failed, observed = self._rework_cases(source,source_node)
        case_ids = _strings(payload['case_ids']) if 'case_ids' in payload else sorted(failed)
        if not set(case_ids) <= failed:
            raise Conflict('所选用例没有失败或受阻记录')
        reason = _text(payload.get('reason'),'返工原因')
        target_id = _id(payload.get('target_node_id'))
        plan = self._rework_targets(run,index).get(target_id)
        if plan is None:
            raise Conflict('请选择当前测试所依赖的已审开发节点')
        target, affected = plan
        basis = stages[target]['approval_sha256']
        request = {'from_node_id':source_node['id'],'from_session_id':source['stage_session_id'],
                   'target_node_id':target_id,'reason':reason,'at':now(),'case_ids':case_ids,
                   'observations':[{**{k:r[k] for k in ('id','case_id','action_id','status','method','path','exit_code','subject',
                                                     'detail','source','environment','observation') if k in r},
                                    **_receipt_observation(r)}
                                   for r in observed if r['case_id'] in case_ids]}
        for i in affected:
            stage = stages[i]
            snapshot = deepcopy(stage)
            prior_id = snapshot['stage_session_id']
            db.execute('INSERT INTO stage_workflow_stage_history VALUES (?,?,?,?,?)',
                       (prior_id,run['id'],stage['node_id'],canonical(snapshot),digest(snapshot)))
            event = {'event':'rework','at':request['at'],'message':reason,'previous_session_id':prior_id}
            if i == index:
                event['case_ids'] = case_ids
            stage['log'].append(event)
            stage.update(status='pending',stage_session_id=None,verified_evidence=[],annotations=[],
                         questions=[],error=None,approval_issues=[],artifact=None,dialogue=[])
            for key in ('approval_sha256','rework_basis','operation_id','progress','rework_request','stop_request','test_input_issues'):
                stage.pop(key,None)
            for follow_up in stage.get('follow_ups', []):
                follow_up.pop('processed_by', None)
            if i==target:
                stage.update(artifact=snapshot['artifact'],verified_evidence=self._approved_records(snapshot),
                             rework_basis=basis,rework_request=request)
        self._activate(db,run,target)

    def _follow_up_entry(self, stage, node, op, payload):
        _fields(payload, ('text','source_type'))
        source_type = payload.get('source_type','instruction')
        if source_type not in ('instruction','requirement') or (source_type=='requirement' and node['kind']!='requirements'):
            raise InvalidInput('仅需求阶段可登记新增用户需求')
        follow_ups = stage.get('follow_ups', [])
        if len(follow_ups) >= 64:
            raise Conflict('本阶段补充要求已达 64 条')
        entry = {'id':op,'text':_text(payload.get('text'),'补充要求',4000),'at':now()}
        if source_type=='requirement':
            number = 1 + sum(bool(item.get('source_id')) for item in follow_ups)
            entry['source_id'] = f'UR-A{number:03d}'
        return entry

    def _revise(self, db, run, index, op, payload):
        stages = run['stages']
        target = stages[index]
        if target['status']!='approved' or any(s['status']=='running' for s in stages):
            raise Conflict('仅可在当前执行结束后从已审核节点创建修订')
        new_follow_up = self._follow_up_entry(target,run['definition']['nodes'][index],op,payload)
        affected = {target['node_id']}
        for node in run['definition']['nodes'][index+1:]:
            if affected.intersection(node['inputs']):
                affected.add(node['id'])
        stamp = now()
        base_artifact, base_approval = target['artifact'], target['approval_sha256']
        for stage in stages[index:]:
            if stage['node_id'] not in affected or not stage['stage_session_id']:
                continue
            previous_id = stage['stage_session_id']
            db.execute('INSERT INTO stage_workflow_stage_history VALUES (?,?,?,?,?)',
                       (previous_id, run['id'], stage['node_id'], canonical(stage), digest(stage)))
            stage.update(status='pending',stage_session_id=None,artifact=None,
                         verified_evidence=[],questions=[],annotations=[],error=None,approval_issues=[],dialogue=[])
            for key in ('approval_sha256','operation_id','progress','rework_basis','rework_request','stop_request',
                        'planning_input_sha256','reused_from','test_input_issues'):
                stage.pop(key,None)
            for follow_up in stage.get('follow_ups', []):
                follow_up.pop('processed_by',None)
            stage['log'].append({'event':'revised','at':stamp,'operation_id':op,'previous_session_id':previous_id})
        target['artifact'] = base_artifact
        if run['definition']['nodes'][index]['kind']=='development':
            target['rework_basis'] = base_approval
        target.setdefault('follow_ups', []).append(new_follow_up)
        run['status'] = 'active'
        self._activate(db,run,index)

    def _action(self, db, run, index, op, action, payload):
        stage = run['stages'][index]
        if action == 'record_manual_check':
            self._record_manual_check(db,run,index,op,payload)
            return None
        if action == 'revise':
            self._revise(db,run,index,op,payload)
            return None
        if action == 'supplement':
            follow_up = self._follow_up_entry(stage,run['definition']['nodes'][index],op,payload)
            stage.setdefault('follow_ups', []).append(follow_up)
        elif action == 'withdraw_supplement':
            _fields(payload, ('id',))
            item = next((item for item in stage.get('follow_ups', []) if item['id']==payload.get('id')),None)
            if not item or item.get('processed_by') or item.get('withdrawn_at') or stage['status']=='running':
                raise Conflict('等本次执行结束后，只可撤回仍未处理的要求')
            item['withdrawn_at'] = now()
            if item.get('source_id') and stage['status']=='review':
                stage['approval_issues'] = self._issues(db,run,index)
        elif action == 'recover':
            _fields(payload, ())
            if stage['status'] != 'running' or _alive(stage.get('worker')):
                raise Conflict('原执行进程仍在运行，或此阶段无需恢复')
            from packages.dynamic_workflow.stage_workspace import operation_receipts
            prior_op = stage['operation_id']
            receipts = operation_receipts({'_data_dir':self.data_dir,'project_id':run['project_id'],
                'run_id':run['id'],'stage_session_id':stage['stage_session_id'],'operation_id':prior_op})
            records = {r['id']:r for r in stage['verified_evidence']}
            records.update({r['id']:r for r in receipts})
            stage.update(status='failed',error='原执行进程已退出，结果未确认。请先核对产物后再决定是否重新执行。',
                         verified_evidence=list(records.values()))
            stamp = now()
            interrupted = {'operation_id':prior_op,'node_id':stage['node_id'],'stage_session_id':stage['stage_session_id'],
                           'status':'interrupted_unknown','at':stamp,'error':stage['error'],'verified_evidence':receipts}
            kernel = _execution_kernel(stage,_execution_start(stage,prior_op))
            if kernel:
                interrupted['kernel'] = kernel
            db.execute('INSERT INTO stage_workflow_attempts VALUES (?,?,?,?,?)',
                       (run['id'],stage['node_id'],prior_op,canonical(interrupted),digest(interrupted)))
            db.execute('UPDATE stage_workflow_operations SET completed=1 WHERE operation_id=?',(prior_op,))
            stage.pop('worker',None)
            if stage.get('stop_request'):
                stage['stop_request']['status'] = 'unconfirmed'
            stage['log'].append({'event':action,'at':stamp,'operation_id':op,'original_operation_id':prior_op})
            return None
        elif stage['status'] == 'running':
            raise Conflict('请等待当前节点执行结束')
        elif action == 'rework':
            self._rework(db,run,index,payload)
            return None
        elif action == 'configure':
            _fields(payload, ('kernel','max_subagents','capabilities'))
            stage['config'] = _config({**stage['config'],**payload})
            stage['policy_pin'] = self._pin(stage['config'])
        elif action == 'note':
            _fields(payload, ('text',))
            stage['notes'].append({'id':'note_'+uuid.uuid4().hex,'text':_text(payload.get('text'),'阶段笔记'),'at':now()})
        elif action == 'annotate':
            if not stage['artifact'] or stage['status'] != 'review':
                raise Conflict('请先生成本节点的待审核成果')
            self._annotate(stage,payload,op)
        elif action == 'approve':
            _fields(payload, ())
            if stage['status'] != 'review' or not stage['artifact']:
                raise Conflict('本阶段成果尚未就绪')
            if any(a['status']=='open' for a in stage['annotations']):
                raise Conflict('本阶段仍有待处理批注')
            if any(not item.get('processed_by') and not item.get('withdrawn_at') for item in stage.get('follow_ups', [])):
                raise Conflict('本阶段仍有待处理的补充要求')
            archive_members = {}
            issues = self._issues(db,run,index,archive_members=archive_members)
            if issues:
                raise Conflict('；'.join(issues)[:1200])
            upstream = []
            if run['definition']['nodes'][index]['kind']=='delivery':
                _,_,upstream=self._approved(db,run,run['definition']['nodes'][index])
            approval = {'artifact':stage['artifact'],'verified_evidence':self._approved_records(stage,upstream),
                        'stage_session_id':stage['stage_session_id'],'run_revision':run['revision'],'at':now()}
            node = run['definition']['nodes'][index]
            if node['kind']=='development' and run.get('contract_version') in RUNTIME_TARGET_CONTRACT_VERSIONS:
                _, by_kind, _ = self._approved(db,run,node)
                approval['implementation_subject_receipts'] = self._implementation_subject_receipts(
                    approval['verified_evidence'],by_kind.get('design',{}).get('items',[]))
            if archive_members:
                approval['archive_members'] = archive_members
            db.execute('INSERT INTO stage_workflow_approvals VALUES (?,?,?,?)',
                       (run['id'],stage['node_id'],canonical(approval),digest(approval)))
            stage.update(status='approved',approval_sha256=digest(approval))
            key = stage.get('planning_input_sha256')
            if key and not stage.get('reused_from') and not stage['annotations'] and not stage['verified_evidence']:
                db.execute('INSERT INTO stage_workflow_planning_reuse VALUES (?,?,?,?,?,?) '
                    'ON CONFLICT(project_id,conversation_id,input_sha256) DO UPDATE SET '
                    'source_run_id=excluded.source_run_id,source_node_id=excluded.source_node_id,'
                    'approval_sha256=excluded.approval_sha256',
                    (run['project_id'],run['conversation_id'],key,run['id'],stage['node_id'],stage['approval_sha256']))
            next_index = next((i for i in range(index+1,len(run['stages'])) if run['stages'][i]['status']!='approved'),None)
            if next_index is not None:
                self._activate(db,run,next_index)
            else:
                run.update(status='completed',active_node_id=None)
        elif action == 'execute':
            stage.pop('progress',None)
            stage.pop('stop_request',None)
            return self._prepare(db,run,index,payload,op)
        else:
            raise InvalidInput('未知工作流动作')
        stage['log'].append({'event':action,'at':now(),'operation_id':op})
        return None

    @staticmethod
    def _follow_up_reference(stage, follow_up_id):
        item = next((entry for entry in stage.get('follow_ups', []) if entry['id']==follow_up_id),None)
        if not item:
            raise NotFound('此阶段版本没有对应的补充要求')
        reference = {key:item[key] for key in ('id','text','at','source_id') if key in item}
        if item.get('withdrawn_at'):
            return {**reference,'status':'withdrawn','withdrawn_at':item['withdrawn_at']}
        if operation := item.get('processed_by'):
            result = next((event for event in reversed(stage['log'])
                           if event['event']=='finished' and event['operation_id']==operation),{})
            return {**reference,'status':'processed','execution_summary':result.get('reply')}
        start = next((event for event in reversed(stage['log'])
                      if event['event']=='started' and event['operation_id']==stage.get('operation_id')),{})
        running = stage['status']=='running' and follow_up_id in start.get('follow_up_ids',[])
        return {**reference,'status':'running' if running else 'queued'}

    def ask(self, pid, conversation, ident, node_id, op, question, annotation_id=None, follow_up_id=None):
        question = _text(question,'阶段问题',4000)
        if annotation_id and follow_up_id:
            raise InvalidInput('每个问题可引用一条批注或一条补充要求')
        identity = ['stage-question',pid,conversation,ident,node_id,question,annotation_id]
        if follow_up_id:
            identity.append(follow_up_id)
        checksum = digest(identity)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owner, child = self._scope(db,pid,conversation,write=True)
            if child!=ident:
                raise NotFound('请在此流程的阶段会话中提问')
            run = self._run(db,pid,owner,ident)
            old = self._operation(db,op,checksum)
            if old:
                return self._run_view(db,run)
            node = next((n for n in run['definition']['nodes'] if n['id']==node_id),None)
            stage = next((s for s in run['stages'] if s['node_id']==node_id and s['stage_session_id']==conversation),None)
            if not node or not stage:
                raise Conflict('当前阶段版本已变更')
            annotation = next((a for a in stage['annotations'] if a['id']==annotation_id),None)
            if annotation_id and not annotation:
                raise NotFound('此阶段版本没有对应的批注')
            follow_up = self._follow_up_reference(stage,follow_up_id) if follow_up_id else None
            dialogue = stage.setdefault('dialogue', [])
            for entry in dialogue:
                if entry['status']=='pending':
                    if _alive(entry.get('worker')):
                        raise Conflict('请等待本阶段正在回复的问题')
                    entry.update(status='failed',error='上一次咨询已中断')
                    entry.pop('worker',None)
                    db.execute('UPDATE stage_workflow_operations SET completed=1 WHERE operation_id=?',(entry['id'],))
            history = [{'question':entry['question'],'answer':entry['answer']} for entry in dialogue[-8:] if entry['status']=='answered']
            _, approved, _ = self._approved(db,run,node)
            current = {'id':op,'question':question,'at':now(),'status':'pending','worker':_runtime_owner(),
                       'artifact_sha256':digest(stage['artifact'])}
            if annotation:
                current['annotation_id'] = annotation_id
            if follow_up:
                current['follow_up_id'] = follow_up_id
                current['follow_up_status'] = follow_up['status']
            dialogue.append(current)
            self._save(db,run)
            db.execute('INSERT INTO stage_workflow_operations VALUES (?,?,?,NULL,0)',(op,checksum,ident))
            context = {**stage['config'],'policy_pin':stage['policy_pin'],'_data_dir':self.data_dir,
                       'stage_title':node['name'],'artifact':stage['artifact'],'approved_inputs':approved,
                       'history':history,'question':question,'annotation':annotation,'follow_up':follow_up}
        try:
            response = _text(self.dialogue_executor(context),'答复',12_000)
        except (ValueError,RuntimeError,InvalidInput) as exc:
            outcome = {'status':'failed','error':str(exc)[:500]}
        except Exception:
            outcome = {'status':'failed','error':'阶段咨询中断，未取得答复'}
        else:
            outcome = {'status':'answered','answer':response}
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db,pid,owner,ident)
            stage = next((s for s in run['stages'] if s['node_id']==node_id and s['stage_session_id']==conversation),None)
            if stage:
                entry = next(item for item in stage['dialogue'] if item['id']==op)
                entry.update(**outcome)
                entry.pop('worker',None)
                self._save(db,run)
            else:
                row = db.execute('SELECT * FROM stage_workflow_stage_history WHERE conversation_id=? AND run_id=?',
                                 (conversation,ident)).fetchone()
                historic = _checked(row)
                if not any(item['id']==op for item in historic['dialogue']):
                    raise Conflict('找不到原阶段的咨询记录')
                late = {'kind':'stage-dialogue','outcome':outcome,'stage_session_id':conversation}
                db.execute('INSERT INTO stage_workflow_attempts VALUES (?,?,?,?,?)',
                           (ident,node_id,op,canonical(late),digest(late)))
            db.execute('UPDATE stage_workflow_operations SET completed=1 WHERE operation_id=?',(op,))
            db.commit()
            return self._run_view(db,run)

    def action(self, pid, conversation, ident, op, expected, node_id, action, payload):
        append = action == 'supplement'
        amend = action == 'revise'
        stop = action == 'stop'
        # An append can race progress and the execution receipt. The exact
        # child identity fences the write even if rework reopens the same node.
        checksum = digest(['action',pid,conversation,ident,None if append or amend or stop else expected,node_id,action,payload])
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            owner, child = self._scope(db,pid,conversation,write=True)
            if child and child != ident:
                raise NotFound('节点不属于此工作流')
            run = self._run(db,pid,owner,ident)
            old = self._operation(db,op,checksum)
            if old:
                return self._run_view(db,run)
            active = node_id == run['active_node_id'] and run['status']=='active'
            if (not append and not stop and expected != run['revision']) or (not amend and not active):
                raise Conflict('页面的阶段或版本已变化，请重新读取工作流')
            index = next((i for i,s in enumerate(run['stages']) if s['node_id']==node_id),None)
            if index is None:
                raise NotFound('此工作流没有对应的阶段')
            stage = run['stages'][index]
            if not stage['stage_session_id']:
                raise Conflict('此阶段尚未进入')
            stage_owner, child_run = self._scope(db,pid,stage['stage_session_id'],write=True)
            if stage_owner != owner or child_run != ident:
                raise Conflict('节点会话身份已变化')
            if (child or append or amend or stop or action == 'record_manual_check') and conversation != stage['stage_session_id']:
                raise Conflict('此子会话属于其他阶段')
            source_session = stage['stage_session_id']
            if stop:
                _fields(payload, ('operation_id',))
                target = _id(payload.get('operation_id'))
                if stage['status']!='running' or stage.get('operation_id')!=target or not _alive(stage.get('worker')):
                    raise Conflict('该次执行已经结束或中断，请核对当前状态')
                if stage.get('stop_request'):
                    return run
                stamp = now()
                stage['stop_request'] = {'status':'requested','operation_id':target,'request_id':op,'at':stamp}
                stage['log'].append({'event':'stop_requested','at':stamp,'operation_id':target,'request_id':op})
                context = None
            else:
                context = self._action(db,run,index,op,action,payload)
            self._save(db,run)
            db.execute('INSERT INTO stage_workflow_operations VALUES (?,?,?,NULL,?)',(op,checksum,ident,int(context is None)))
            if action in ('approve','rework','revise'):
                self._record_transition(db,run,op,action,conversation,node_id,source_session,
                                        run['definition']['nodes'][index]['name'])
            if context is None:
                db.commit()
                return self._run_view(db,run)
        context['_progress'] = lambda completed,total,message: self._progress(ident,op,node_id,completed,total,message)
        context['_stop_requested'] = lambda: self._stop_requested(ident,op)
        key = None
        prompt_sha = None
        origin = None
        termination_unconfirmed = False
        try:
            basis = planning_key(context)
            if basis:
                key, prompt_sha = basis
            output = self._reuse(context,key) if key else None
            if output is not None:
                origin = output['reused_from']
            if output is None:
                output = self.executor(context)
                if key and (planning_key(context) != basis or output.get('prompt_sha256',prompt_sha)!=prompt_sha):
                    key = None
            _json(output,4*1024*1024)
            if not isinstance(output,dict):
                raise ValueError('执行内核未提供阶段输出')
            evidence = output.get('verified_evidence', [])
            if not isinstance(evidence,list) or any(not isinstance(r,dict) or not isinstance(r.get('id'),str) for r in evidence):
                raise ValueError('执行回执缺少真实编号')
            if any(r.get('kind') == 'manual_check' for r in evidence):
                raise ValueError('执行内核不能登记人工实测')
            if 'questions' in output:
                questions = output['questions']
                if not isinstance(questions,list) or not questions or len(questions)>100:
                    raise ValueError('澄清问题格式无效')
                questions = [_text(q,'问题') for q in questions]
            elif not isinstance(output.get('artifact'),dict):
                raise ValueError('内核未提供可审核的成果')
            error = None
        except (ValueError,RuntimeError,InvalidInput) as exc:
            output, error = {}, str(exc)[:800] or '执行未完成'
        except TerminationUnconfirmed:
            termination_unconfirmed = True
            output, error = {}, '无法确认本机执行已完全停止。请先核对记录。'
        except Exception:
            # A process/tool may have acted before losing its response. Never
            # repeat it on reconnect or return an exception with private data.
            output, error = {}, '执行中断，原操作结果未确认。请先检查记录。'
        if error:
            # A tool can finish before its model/transport fails. Preserve real
            # receipts (including failed checks) on the original attempt.
            output = self._execution_evidence(context)
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            run = self._run(db,pid,owner,ident)
            stage = run['stages'][index]
            if stage['status']!='running' or stage.get('operation_id')!=op:
                raise Conflict('原执行已被恢复，返回结果未采用')
            stop_request = stage.get('stop_request')
            if stop_request:
                stop_request['status'] = 'unconfirmed' if termination_unconfirmed else 'stopped'
                if not termination_unconfirmed:
                    error = '已停止本次执行。已保存的文件和工具记录仍可查看。'
                if 'artifact' in output or not output.get('verified_evidence'):
                    output = self._execution_evidence(context)
                key = origin = None
            event = {'event':'finished','at':now(),'operation_id':op}
            stage['planning_input_sha256'] = key
            stage['reused_from'] = origin
            records = {r['id']:r for r in stage['verified_evidence']}
            for record in output.get('verified_evidence',[]):
                old = records.get(record['id'])
                if old is not None and old != record:
                    error = '执行回执编号对应的内容已变化'
                    break
                records[record['id']] = record
            stage['verified_evidence'] = list(records.values())
            if error:
                stage.update(status='failed',error=error)
            elif 'questions' in output:
                stage.update(status='needs_input',questions=questions,error=None)
            else:
                if self._result(db,run,index,op,output,context):
                    event['artifact_written'] = True
                if stage['status']=='review' and not stage['error']:
                    referenced = {source for row in stage['artifact']['items'] for source in row.get('source_ur', [])}
                    for item in stage.get('follow_ups', []):
                        if item['id'] in context['_follow_up_ids'] and (not item.get('source_id') or item['source_id'] in referenced):
                            item['processed_by'] = op
            attempt = {'operation_id':op,'node_id':node_id,'stage_session_id':stage['stage_session_id'],
                       'status':stage['status'],'at':event['at'],'kernel':context['kernel'],
                       'error':stage['error'],'policy_pin':context['policy_pin'],
                       'follow_up_ids':sorted(context['_follow_up_ids']),
                       'prompt_sha256':output.get('prompt_sha256') or prompt_sha,'contract_version':context['contract_version'],
                       'planning_input_sha256':key,'reused_from':stage['reused_from'],
                       'input_sha256':digest({'instructions':context['instructions'],'inputs':context['inputs'],
                            'user_input':context['user_input'],'template':context['template'],
                            'annotations':context['annotations'],'prior_artifact':context['prior_artifact']}),
                       'artifact':output.get('artifact'),'questions':output.get('questions',[]),
                       'verified_evidence':output.get('verified_evidence',[]),
                       'approval_issues':stage.get('approval_issues',[])}
            if stop_request:
                attempt.update(stop_request=stop_request,
                               status='interrupted_unknown' if termination_unconfirmed else 'stopped')
            if 'connector_retry_ack' in context:
                attempt['connector_retry_ack'] = context['connector_retry_ack']
            db.execute('INSERT INTO stage_workflow_attempts VALUES (?,?,?,?,?)',
                       (ident,node_id,op,canonical(attempt),digest(attempt)))
            event.update(status=attempt['status'],error=stage['error'],attempt_sha256=digest(attempt),
                         reused_from=stage['reused_from'],
                         checks=[{'case_id':r.get('case_id'),'status':r.get('status'),'id':r['id']}
                                 for r in attempt['verified_evidence'] if r.get('kind')=='check'])
            if stage['status']=='needs_input':
                event['questions'] = stage['questions']
            elif stage['status']=='review' and not stage['error']:
                event['reply'] = str(stage['artifact'].get('summary',''))[:4000]
            stage['log'].append(event)
            stage.pop('worker',None)
            self._save(db,run)
            db.execute('UPDATE stage_workflow_operations SET completed=1 WHERE operation_id=?',(op,))
            db.commit()
            return self._run_view(db,run)

    def _result(self, db, run, index, op, output, context) -> bool:
        stage = run['stages'][index]
        artifact = output['artifact']
        # Malformed field types must never reach a renderer. A well-shaped but
        # partial plan or failed case remains visible with its approval blockers.
        from jsonschema import Draft202012Validator
        from packages.dynamic_workflow.stage_contracts import stage_schema
        if not Draft202012Validator(stage_schema(context['kind'],version=context.get('contract_version',CONTRACT_VERSION))).is_valid(artifact):
            stage.update(status='failed',error='执行结果的字段不符合节点合同，已保留上一份成果')
            return False
        if context['kind'] == 'testing':
            artifact = deepcopy(artifact)
            apply_manual_checks(artifact,context['approved_by_kind'],stage['verified_evidence'])
        previous = stage.get('artifact')
        targets = {a['target'] for a in context['annotations']}
        old_rows = {r['id']:r for r in previous['items']} if previous else {}
        new_rows = {r['id']:r for r in artifact['items']}
        if targets and 'summary' not in targets:
            unrelated = set(old_rows) | set(new_rows)
            if any(old_rows.get(r) != new_rows.get(r) for r in unrelated - targets):
                stage.update(status='review',error='本次返工修改了所选批注之外的条目，当前成果保留')
                return False
        stage.update(artifact=artifact,status='review',error=None,questions=[])
        if context['kind']=='requirements':
            stage['source_urs'] = source_urs(context['user_input'])
        if artifact.get('questions'):
            stage.update(status='needs_input',questions=artifact['questions'])
        stage['approval_issues'] = self._issues(db,run,index,context['user_input'])
        selected = {a['id'] for a in context['annotations']}
        if selected and previous != artifact and stage['status']=='review':
            for annotation in stage['annotations']:
                target = annotation['target']
                before = previous if target=='summary' else old_rows.get(target)
                after = artifact if target=='summary' else new_rows.get(target)
                field = annotation.get('anchor',{}).get('field')
                updated = (before != after if field is None else
                           (before or {}).get(field) != (after or {}).get(field))
                if annotation['id'] in selected and updated:
                    annotation.update(status='applied',applied_at=now(),operation_id=op)
        for item in artifact.get('lessons',[]):
            if not any(n.get('text')==item for n in stage['notes']):
                stage['notes'].append({'id':'note_'+uuid.uuid4().hex,'text':item,'at':now(),'operation_id':op})
        return True
