"""Real SQLite plan/evidence durability, user correction and no execution loop."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import sqlite3
import pytest
from packages.dynamic_workflow import DynamicWorkflowStore, WorkflowConflict, WorkflowInvalidInput, WorkflowNotFound, WorkflowIntegrityError
from packages.domain.store import Store


def check(value, message='dynamic workflow acceptance failed'):
    if not value: raise AssertionError(message)


def reference(ident='evidence', revision=1): return {'id': ident, 'revision': revision, 'sha256': 'a' * 64}
def node(ident, deps=()): return {'id': ident, 'title': '任务 ' + ident, 'description': '保留真实来源', 'depends_on': list(deps), 'acceptance': [{'id': 'check', 'text': '实际核对输出并记录依据'}]}
def plan(): return {'title': '通用任务链', 'goal': '依据任务与证据动态完善工作', 'nodes': [node('a'), node('b', ['a']), node('c')], 'questions': [], 'external_refs': [reference('existing_harness')]}
def create(store, value=None, op='create', actor='MODEL'): return store.create('project', value or plan(), operation_id=op, actor=actor)
def projected(value): return {item['node_id']: item for item in value['node_projection']}
def observation(session='session_a', status='RETURNED', stamp='2026-10-15T08:00:00+00:00'):
    return {'owner': {'project_id': 'project', 'session_id': session, 'operation_id': 'existing_operation', 'generation': 4}, 'status': status,
            'observed_at': stamp, 'source_ref': reference('original_operation'), 'coverage': 'EXACT_OPERATION'}
def validate(store, wid, nid, basis, op): return store.record_validation('project', wid, nid, 'check', 'PASSED', plan_ref=basis, evidence_refs=[reference('real_test_report')], operation_id=op, actor='VERIFIER')


def test_create_standalone_and_existing_store_reopen_preserve_exact_versions_and_external_refs(tmp_path):
    for database in [tmp_path/'standalone.sqlite', Store(tmp_path/'app.sqlite')]:
        pid='project'
        if isinstance(database, Store): pid=database.create_project('真实通用工程','原创夹具')['id']
        store=DynamicWorkflowStore(database); source=plan();saved=store.create(pid, source, operation_id='initial')
        source['nodes'][0]['title']='mutated outside';reopened=DynamicWorkflowStore(database).get(pid,saved['id'])
        check(reopened['plan']['nodes'][0]['title']=='任务 a' and reopened['workflow_ref']==saved['workflow_ref'])
        check(reopened['plan']['external_refs']==[reference('existing_harness')] and reopened['read_only_execution'] and reopened['dispatch_performed'] is False)
        check(reopened['ready_node_ids']==['a','c'] and projected(reopened)['b']['status']=='WAITING_DEPENDENCIES')


def test_dag_rejects_cycles_missing_dependencies_duplicates_and_unknown_fields_without_writes(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite')
    invalid=[]
    value=plan();value['nodes'][0]['depends_on']=['b'];invalid.append(value)
    value=plan();value['nodes'][0]['depends_on']=['unknown'];invalid.append(value)
    value=plan();value['nodes'].append(deepcopy(value['nodes'][0]));invalid.append(value)
    value=plan();value['nodes'][0]['status']='PASSED';invalid.append(value)
    value=plan();value['nodes'][0]['depends_on']=['a'];invalid.append(value)
    value=plan();value['nodes'][0]['question_ids']=['not_defined'];invalid.append(value)
    for index,value in enumerate(invalid):
        with pytest.raises(WorkflowInvalidInput):create(store,value,'bad_'+str(index))
    check(store.list('project')==[])


def test_cas_real_two_writers_one_wins_and_wrong_sha_preserves_original(tmp_path):
    first=DynamicWorkflowStore(tmp_path/'db.sqlite');saved=create(first);second=DynamicWorkflowStore(tmp_path/'db.sqlite')
    def update(args):
        store,label=args;value=plan();value['nodes'][2]['title']=label
        try:return store.replan('project',saved['id'],value,expected_ref=saved['workflow_ref'],operation_id=label,actor='MODEL',reason='新证据')
        except WorkflowConflict:return 'CONFLICT'
    with ThreadPoolExecutor(2) as pool:results=list(pool.map(update,[(first,'first'),(second,'second')]))
    check(sum(item=='CONFLICT' for item in results)==1 and first.get('project',saved['id'])['revision']==2)
    current=first.get('project',saved['id'])
    with pytest.raises(WorkflowConflict):first.replan('project',saved['id'],plan(),expected_ref={**current['workflow_ref'],'sha256':'b'*64},operation_id='wrong_sha',reason='不能覆盖')
    check(first.get('project',saved['id'])['workflow_ref']==current['workflow_ref'])


def test_idempotency_replays_original_receipt_after_new_head_and_unknown_only_readable(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');one=create(store);two=store.replan('project',one['id'],plan(),expected_ref=one['workflow_ref'],operation_id='replan',reason='补充')
    repeated=create(store)
    check(repeated['workflow_ref']==one['workflow_ref'] and repeated['replayed'] and two['revision']==2)
    check(store.operation('project',None,'create')['workflow_ref']==one['workflow_ref'])
    with pytest.raises(WorkflowNotFound):store.operation('project',one['id'],'unobserved')
    bad=plan();bad['goal']='different'
    with pytest.raises(WorkflowConflict):create(store,bad)
    check(store.get('project',one['id'])['revision']==2)


def test_human_field_locks_and_independent_model_depth_pins_cannot_be_overridden_by_model(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');value=plan()
    value['nodes'][0]['agent_policy']={'model':{'mode':'MANUAL','value':'approved_route_A'},'depth':{'mode':'AUTO','value':None}}
    value['nodes'][1]['agent_policy']={'model':{'mode':'AUTO','value':None},'depth':{'mode':'MANUAL','value':'provider_specific_variant'}}
    with pytest.raises(WorkflowConflict):create(store,value)
    initial=create(store,value,actor='USER');wid=initial['id']
    locked=store.set_locks('project',wid,'c',['description'],expected_ref=initial['workflow_ref'],operation_id='lock')
    for mutate in [lambda p:p['nodes'][0]['agent_policy']['model'].update(mode='AUTO',value=None),lambda p:p['nodes'][1]['agent_policy']['depth'].update(value='another'),lambda p:p['nodes'][2].update(description='model replacement'),lambda p:p['nodes'].pop(0)]:
        changed=deepcopy(locked['plan']);mutate(changed)
        if len(changed['nodes'])==2:changed['nodes'][0]['depends_on']=[]
        with pytest.raises(WorkflowConflict):store.replan('project',wid,changed,expected_ref=locked['workflow_ref'],operation_id='denied',reason='自动重排')
    changed=deepcopy(locked['plan']);changed['nodes'][0]['agent_policy']['model']['value']='approved_route_B'
    corrected=store.replan('project',wid,changed,expected_ref=locked['workflow_ref'],operation_id='user_correction',actor='USER',reason='用户明确修改指定模型')
    check(corrected['plan']['nodes'][0]['agent_policy']['model']['value']=='approved_route_B')
    check(corrected['plan']['nodes'][1]['agent_policy']['depth']['value']=='provider_specific_variant')
    with pytest.raises(WorkflowConflict):store.set_locks('project',wid,'c',[],expected_ref=corrected['workflow_ref'],operation_id='model_unlock',actor='MODEL')


def test_question_confirmation_is_user_only_changes_version_and_changed_wording_preserves_old_answer(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');value=plan();value['questions']=[{'id':'scope','text':'设备约束尚未明确','blocking':True}];value['nodes'][0]['question_ids']=['scope']
    initial=create(store,value);wid=initial['id'];check(projected(store.get('project',wid))['a']['status']=='NEEDS_CONFIRMATION')
    with pytest.raises(WorkflowConflict):store.answer('project',wid,'scope','model guesses',expected_ref=initial['workflow_ref'],operation_id='bad_answer',actor='MODEL')
    answered=store.answer('project',wid,'scope','用户提供真实约束',expected_ref=initial['workflow_ref'],operation_id='answer')
    check(projected(store.get('project',wid))['a']['status']=='READY_FOR_EXPLICIT_ACTION')
    changed=deepcopy(answered['plan']);changed['questions'][0]['text']='新的设备环境是否相同'
    revised=store.replan('project',wid,changed,expected_ref=answered['workflow_ref'],operation_id='new_question',reason='来源变化')
    check(revised['answers']=={} and store.get('project',wid,answered['revision'])['answers']['scope']['answer']=='用户提供真实约束')
    check(set(revised['change']['affected_node_ids'])=={'a','b'})


def test_changed_sources_invalidate_only_changed_nodes_and_successors_preserving_unaffected_verification(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store);wid=initial['id'];basis=initial['workflow_ref']
    for nid in ('a','b','c'):validate(store,wid,nid,basis,'verify_'+nid)
    check(all(item['validation_status']=='PASSED' for item in store.get('project',wid)['node_projection']))
    changed=deepcopy(initial['plan']);changed['nodes'][0]['source_refs']=[reference('new_material',2)]
    revised=store.replan('project',wid,changed,expected_ref=basis,operation_id='replan_sources',reason='用户补充原始资料')
    current=store.get('project',wid);states=projected(current)
    check(revised['change']['changed_node_ids']==['a'] and revised['change']['affected_node_ids']==['a','b'])
    check(states['a']['validation_status']=='NOT_OBSERVED' and states['b']['validation_status']=='NOT_OBSERVED' and states['c']['validation_status']=='PASSED')
    check([record['basis_status'] for record in current['actual_records'] if record['node_id']=='c']==['CURRENT_BASIS'])
    check(store.get('project',wid,1)['workflow_ref']==basis)


def test_execution_return_and_model_claim_never_verify_or_unlock_dependency(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store);wid=initial['id'];basis=initial['workflow_ref']
    store.record_claim('project',wid,'a','模型说全部完成并通过',plan_ref=basis,operation_id='claim')
    linked=store.attach_execution('project',wid,'a',observation(),plan_ref=basis,operation_id='observe')
    states=projected(store.get('project',wid))
    check(states['a']['execution_status']=='RETURNED' and states['a']['validation_status']=='NOT_OBSERVED' and states['b']['status']=='WAITING_DEPENDENCIES')
    check(linked['read_only_execution'] and store.get('project',wid)['coverage']=='EXPLICIT_LINKED_RECORDS_ONLY')
    with pytest.raises(WorkflowConflict):store.record_validation('project',wid,'a','check','PASSED',plan_ref=basis,evidence_refs=[reference()],operation_id='model_pass',actor='MODEL')
    with pytest.raises(WorkflowInvalidInput):store.attach_execution('project',wid,'a',{**observation(),'validation_status':'PASSED'},plan_ref=basis,operation_id='smuggle')
    validate(store,wid,'a',basis,'real_check');check(projected(store.get('project',wid))['b']['status']=='READY_FOR_EXPLICIT_ACTION')


def test_pending_confirmation_and_acceptance_are_required_even_if_verifier_records_early_pass(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');value=plan();value['questions']=[{'id':'global','text':'关键约束未明确'}]
    initial=create(store,value);validate(store,initial['id'],'a',initial['workflow_ref'],'early')
    current=projected(store.get('project',initial['id']));check(current['a']['status']=='NEEDS_CONFIRMATION' and current['a']['validation_status']=='NOT_OBSERVED')
    empty=plan();empty['nodes'][0]['acceptance']=[];another=create(store,empty,op='undefined_acceptance');check(projected(store.get('project',another['id']))['a']['status']=='NEEDS_ACCEPTANCE')


def test_cross_session_links_preserve_owners_and_old_late_observation_cannot_downgrade_known_return(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store);wid=initial['id'];basis=initial['workflow_ref']
    store.attach_execution('project',wid,'a',observation('root_a'),plan_ref=basis,operation_id='first_session')
    old=observation('root_a','RUNNING','2026-10-15T07:00:00+00:00');store.attach_execution('project',wid,'a',old,plan_ref=basis,operation_id='late_old')
    store.attach_execution('project',wid,'c',observation('independent_b'),plan_ref=basis,operation_id='second_session')
    reopened=DynamicWorkflowStore(tmp_path/'db.sqlite').get('project',wid)
    check(projected(reopened)['a']['execution_status']=='RETURNED')
    check({record['payload']['owner']['session_id'] for record in reopened['actual_records']}=={'root_a','independent_b'})
    check(all(record['record_ref']['sha256'] for record in reopened['actual_records']))
    with pytest.raises(WorkflowConflict):store.attach_execution('project',wid,'a',{**observation(),'owner':{**observation()['owner'],'project_id':'foreign'}},plan_ref=basis,operation_id='foreign_owner')
    with pytest.raises(WorkflowNotFound):store.get('foreign',wid)
    with pytest.raises(WorkflowNotFound):store.operation('foreign',wid,'first_session')


def test_requested_effective_unsupported_are_separate_and_metadata_is_not_proof_of_effective_model(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');value=plan();value['nodes'][0]['agent_policy']={'model':{'mode':'MANUAL','value':'approved_route'},'depth':{'mode':'AUTO','value':None}}
    initial=create(store,value,actor='USER');wid=initial['id'];basis=initial['workflow_ref'];actual=observation()
    actual['routing']={'model':{'status':'OBSERVED','effective':'provider/model','evidence_level':'TASK_METADATA_SELECTION','evidence_refs':[reference('task_selection')]}}
    with pytest.raises(WorkflowConflict):store.attach_execution('project',wid,'a',actual,plan_ref=basis,operation_id='selection_is_not_effective')
    actual['routing']['model']['evidence_level']='ASSISTANT_MESSAGE';actual['routing']['depth']={'status':'UNSUPPORTED','effective':None,'evidence_level':'NONE','evidence_refs':[]}
    store.attach_execution('project',wid,'a',actual,plan_ref=basis,operation_id='actual_model')
    current=projected(store.get('project',wid))['a'];check(current['requested']['model']['value']=='approved_route' and current['effective']['model']['effective']=='provider/model' and current['effective']['depth']['status']=='UNSUPPORTED')


def test_unknown_validation_evidence_missing_criteria_and_wrong_plan_ref_do_not_write(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store);wid=initial['id'];basis=initial['workflow_ref']
    with pytest.raises(WorkflowInvalidInput):store.record_validation('project',wid,'a','check','PASSED',plan_ref=basis,evidence_refs=[],operation_id='empty',actor='USER')
    with pytest.raises(WorkflowInvalidInput):store.record_validation('project',wid,'a','not_present','PASSED',plan_ref=basis,evidence_refs=[reference()],operation_id='criterion',actor='USER')
    with pytest.raises(WorkflowConflict):store.attach_execution('project',wid,'a',observation(),plan_ref={**basis,'id':'other'},operation_id='wrong_plan')
    check(store.get('project',wid)['actual_records']==[])


def test_invalid_policy_payload_size_and_strict_identifiers_do_not_truncate_original(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store)
    for policy in [{'model':{'mode':'AUTO','value':'not_auto'},'depth':{'mode':'AUTO','value':None}}, {'model':{'mode':'MANUAL','value':''},'depth':{'mode':'AUTO','value':None}}]:
        changed=plan();changed['nodes'][0]['agent_policy']=policy
        with pytest.raises(WorkflowInvalidInput):store.replan('project',initial['id'],changed,expected_ref=initial['workflow_ref'],operation_id='invalid',reason='test')
    for label in ['x'*8001,'\ud800','\x00']:
        changed=plan();changed['goal']=label
        with pytest.raises(WorkflowInvalidInput):create(store,changed,op='oversize')
    with pytest.raises(WorkflowInvalidInput):store.get('../project',initial['id'])
    check(store.get('project',initial['id'])['workflow_ref']==initial['workflow_ref'])


def test_corruption_fails_closed_without_overwriting_history_and_head_regression_is_detected(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store);wid=initial['id']
    latest=store.replan('project',wid,plan(),expected_ref=initial['workflow_ref'],operation_id='second',reason='历史保留')
    with sqlite3.connect(store.path) as connection:connection.execute('UPDATE dynamic_workflow_heads SET revision=1')
    with pytest.raises(WorkflowIntegrityError):store.get('project',wid)
    with sqlite3.connect(store.path) as connection:connection.execute('UPDATE dynamic_workflow_heads SET revision=2');connection.execute("UPDATE dynamic_workflow_versions SET payload='{}' WHERE revision=2")
    with pytest.raises(WorkflowIntegrityError):store.get('project',wid)
    with pytest.raises(WorkflowIntegrityError):store.replan('project',wid,plan(),expected_ref=latest['workflow_ref'],operation_id='do_not_overwrite',reason='不能覆盖损坏历史')
    with sqlite3.connect(store.path) as connection:check(connection.execute('SELECT count(*) FROM dynamic_workflow_versions').fetchone()[0]==2)


def test_failure_between_version_write_and_receipt_rolls_back_both_and_lost_return_is_read_only_recoverable(tmp_path, monkeypatch):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');initial=create(store);wid=initial['id'];save=store._save_version
    def interrupted(*args,**kwargs):
        save(*args,**kwargs)
        raise OSError('injected before atomic operation receipt')
    monkeypatch.setattr(store,'_save_version',interrupted)
    with pytest.raises(OSError):store.replan('project',wid,plan(),expected_ref=initial['workflow_ref'],operation_id='interrupted',reason='原子验证')
    reopened=DynamicWorkflowStore(store.path)
    check(reopened.get('project',wid)['workflow_ref']==initial['workflow_ref'])
    with pytest.raises(WorkflowNotFound):reopened.operation('project',wid,'interrupted')
    saved=reopened.replan('project',wid,plan(),expected_ref=initial['workflow_ref'],operation_id='returned_but_reply_lost',reason='已经真实提交')
    recovered=DynamicWorkflowStore(store.path).operation('project',wid,'returned_but_reply_lost')
    check(recovered['workflow_ref']==saved['workflow_ref'] and reopened.get('project',wid)['revision']==2)


def test_full_bound_deep_dag_uses_no_recursion_and_unchanged_canonical_order_has_no_affected_nodes(tmp_path):
    store=DynamicWorkflowStore(tmp_path/'db.sqlite');value=plan();value['nodes']=[node('n'+str(index),['n'+str(index-1)] if index else []) for index in range(512)]
    initial=create(store,value);check(store.get('project',initial['id'])['ready_node_ids']==['n0'])
    reordered=deepcopy(value);reordered['nodes'].reverse()
    revised=store.replan('project',initial['id'],reordered,expected_ref=initial['workflow_ref'],operation_id='reordered',reason='仅改变输入顺序')
    check(revised['change']['affected_node_ids']==[])
