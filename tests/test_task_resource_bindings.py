"""AUTHORED_NOT_RUN: real original-operation metadata and shared adapter ledger.

No SDK, wire listener, model, user files or native/OS resource qualification.
"""
import asyncio
from copy import deepcopy
import pytest
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.domain.task_resource_budget import TaskResourceBudget
from packages.domain.local_workspaces import LocalWorkspaces
from packages.agent_sessions.task_resources import for_operation
from test_experience_candidates import experience
from test_subscription_execution_meter import metered,plan,hop,call


def test_followup_of_exact_original_work_uses_same_resource_identity(experience):
    s=experience;o=s['sessions'];manifest,_=o._manifest(s['pid'],s['sid']);root=o.operation(s['pid'],s['sid'],s['scope']['root_operation_id'])
    initial=for_operation(o,manifest,root)
    request={**s['scope'],'operation_id':'followup','raw_text':'继续此原工作','original_work_ref':{'root_operation_id':s['scope']['root_operation_id'],'root_sha256':s['root']['sha256']}}
    request.pop('workspace_ref');request.pop('root_operation_id')
    followup,_=o._claim(s['pid'],s['sid'],'followup','MESSAGE',request)
    same=for_operation(o,manifest,followup)
    assert same.id==initial.id and same.authority==initial.authority
    assert not same.path.exists(), 'Resolving a reference is not allocating or dispatching'


def test_forged_original_work_sha_cannot_reset_or_borrow_budget(experience):
    s=experience;o=s['sessions'];manifest,_=o._manifest(s['pid'],s['sid'])
    request={'session_ref':s['scope']['session_ref'],'generation':1,'operation_id':'bad_work','raw_text':'Preserve draft','original_work_ref':{'root_operation_id':s['scope']['root_operation_id'],'root_sha256':'f'*64}}
    operation,_=o._claim(s['pid'],s['sid'],'bad_work','MESSAGE',request)
    with pytest.raises(Conflict):for_operation(o,manifest,operation)
    assert not (s['data']/'task-resource-budgets').exists()


def test_private_adapter_hop_must_obey_shared_work_model_limit_in_addition_to_local_limit(metered):
    s=metered;m=s['meter'];p=plan(s);ws=LocalWorkspaces(s['store'],s['context']['workspace']/'resource-data')
    limits={'source_bytes':1048576,'source_reads':128,'model_attempts':1,'retained_output_bytes':1048576}
    budget=TaskResourceBudget(ws,{'project_id':s['pid'],'kind':'ADAPTER_EXECUTION_WORK','root_execution_id':s['id'],'request_sha256':digest(s['request'])},limits=limits);budget.allocate();m.task_budget=budget
    assert call(s,hop(s,p,request_bytes=128))['reserved']==1
    with pytest.raises(Conflict):call(s,hop(s,p,2,request_bytes=128))
    assert m.summary()['http_reservations']==1 and budget.inspect()['used']['model_attempts']==1


def test_legacy_page_review_uses_original_operation_mapping_and_rejects_foreign_generation(tmp_path):
    from test_native_review_coordinator import setup
    from test_native_review import TOKEN
    async def run():
        f,native,reviews,body=setup(tmp_path)
        try:
            await native.submit(body,TOKEN)
            record=reviews._record(body['operation_id']);manifest,_=f.owner._manifest(f.pid,f.sid)
            operation=f.owner.operation(f.pid,f.sid,record['native_operation_id'])
            budget=for_operation(f.owner,manifest,operation)
            assert budget.authority['root_operation_id']==body['operation_id']
            assert budget.authority['request_sha256']==record['request_sha256']
            assert budget.inspect()['used']['model_attempts']==0
            key=digest([f.sid,operation['id']]);mapping=reviews.read('operations',key)
            reviews.write('operations',key,{**mapping,'generation':mapping['generation']+1})
            assert reviews.read('operations',key)['generation']==mapping['generation']+1
            with pytest.raises(Conflict):for_operation(f.owner,manifest,operation)
            assert f.owner.operation(f.pid,f.sid,operation['id'])==operation
        finally:
            await native.close();await f.owner.close();await f.close()
    asyncio.run(run())
