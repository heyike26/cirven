"""Actual SQLite/source/API evidence resolution; only external model is replaced.

Pure resolver cases use a real Store/intake source receipt. No Windows/model
call or original-CLI qualification is inferred from these tests.
"""
from copy import deepcopy
from pathlib import Path

from pydantic import ValidationError
import pytest

from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.discovery_evidence import resolve_graph_evidence
from packages.domain.discovery_schema import DiscoveryCandidate
from packages.domain.intake import IntakeInput, canonical, digest
from packages.domain.local_workspaces import sha
from packages.domain.planning_proposals import PlanningProposals
from packages.domain.store import Conflict, InvalidInput, Store

ROOT = Path(__file__).resolve().parents[1]


def check(value, message='discovery evidence requirement failed'):
    if not value:
        raise AssertionError(message)


def anchors(sources, count=1):
    document = next(d for d in sources['documents'] if d['filename']=='资料.txt')
    chunk = document['chunks'][0]
    return [{'document_id':document['document_id'],'revision':document['revision'],
        'raw_sha256':document['raw_sha256'],'chunk_index':chunk['index'],'chunk_sha256':chunk['sha256'],
        'char_start':chunk['char_start']+i,'char_end':chunk['char_start']+i+1,
        'quote_sha256':sha(chunk['content'][i:i+1].encode())} for i in range(count)]


def candidate(sources):
    anchor=anchors(sources)[0]
    return {'title':'原创证据图','phase':'SPECIFY','summary':'事实节点只引用精确条目。',
        'claims':[{'id':'fact1','text':'可核对的材料条目一','category':'REQUIREMENT','origin':'SOURCE_FACT',
            'source_anchors':[deepcopy(anchor)],'acceptance':['核对来源和范围']},
            {'id':'fact2','text':'同一来源的另一事实条目','category':'CONSTRAINT','origin':'SOURCE_FACT',
                'source_anchors':[deepcopy(anchor)]},
            {'id':'suggestion','text':'尚未确认的建议','category':'ASSUMPTION','origin':'MODEL_SUGGESTION'}],
        'questions':[],'graphs':[{'id':'flow','kind':'FLOW','title':'有证据的候选流程',
            'nodes':[{'id':'a','label':'输入','description':'对应已有事实','origin':'SOURCE_FACT',
                'claim_ids':['fact1','fact2'],'source_anchors':[]},
                {'id':'b','label':'输出','description':'对应已有事实','origin':'SOURCE_FACT','claim_ids':['fact1']},
                {'id':'m','label':'建议','description':'模型建议保留为建议','origin':'MODEL_SUGGESTION',
                    'claim_ids':['fact1','suggestion']}],
            'edges':[{'id':'ab','source':'a','target':'b','relation':'已有流程','origin':'SOURCE_FACT','claim_ids':['fact2']},
                {'id':'bm','source':'b','target':'m','relation':'拟议流程','origin':'MODEL_SUGGESTION',
                    'claim_ids':['fact1','suggestion']}]}]}


@pytest.fixture(scope='module')
def real_sources(tmp_path_factory):
    data=tmp_path_factory.mktemp('evidence-store');store=Store(data/'eeas.sqlite3')
    pid=store.create_project('原创来源验证','')['id']
    document=store.save_document(pid,'资料.txt',('原创🙂资料明确已有流程与来源。'*12).encode(),'original-evidence-fixture')
    proposals=PlanningProposals(store,ROOT)
    intake=proposals.intakes.save(pid,IntakeInput.model_validate({'idempotency_key':'source','materials':[
        {'document_id':document['id'],'revision':document['revision'],'sha256':document['sha256']}]}))
    return proposals._sources(intake)


def test_exact_fact_claim_inheritance_is_immutable_deduplicated_and_hash_bound(real_sources):
    raw=candidate(real_sources);original=canonical(raw)
    with pytest.raises(ValidationError):DiscoveryCandidate.model_validate(raw)
    resolved,receipt=resolve_graph_evidence(raw,real_sources)
    strict=DiscoveryCandidate.model_validate(resolved);DiscoveryWorkbench._validate(strict,real_sources)
    check(canonical(raw)==original and resolved is not raw)
    check(len(receipt['elements'])==3 and all(e['inherited_anchor_count']==1 for e in receipt['elements']))
    check(receipt['raw_candidate_sha256']==digest(raw) and receipt['resolved_candidate_sha256']==digest(resolved))
    check(receipt['source_receipt_sha256']==real_sources['sha256'])
    check(receipt['semantic_verification']=='HUMAN_REVIEW_REQUIRED' and not receipt['execution_authorized'])
    for derived in receipt['elements']:
        graph=raw['graphs'][0];collection='nodes' if derived['element_kind']=='NODE' else 'edges'
        old=next(e for e in graph[collection] if e['id']==derived['element_id'])
        new=next(e for e in resolved['graphs'][0][collection] if e['id']==derived['element_id'])
        check(derived['raw_element_sha256']==digest(old) and derived['resolved_element_sha256']==digest(new))
        for claim in derived['source_claims']:
            original_claim=next(c for c in raw['claims'] if c['id']==claim['claim_id'])
            check(claim['claim_sha256']==digest(original_claim))
    check(resolved['graphs'][0]['nodes'][2]==raw['graphs'][0]['nodes'][2])
    check(resolved['graphs'][0]['edges'][1]==raw['graphs'][0]['edges'][1])
    repeated,again=resolve_graph_evidence(resolved,real_sources)
    check(repeated==resolved and again['status']=='NOT_REQUIRED')


@pytest.mark.parametrize('fault',['unknown','mixed-model','user-statement','missing-claim-anchor',
    'wrong-quote','wrong-document','duplicate-claim','empty-ids','too-many-anchors','receipt-integrity'])
def test_invalid_or_ambiguous_evidence_never_acquires_factual_anchors(real_sources,fault):
    raw=candidate(real_sources);sources=deepcopy(real_sources)
    if fault=='unknown':raw['graphs'][0]['nodes'][0]['claim_ids'].append('unknown')
    elif fault=='mixed-model':raw['graphs'][0]['nodes'][0]['claim_ids'].append('suggestion')
    elif fault=='user-statement':raw['claims'][1]['origin']='USER_STATEMENT'
    elif fault=='missing-claim-anchor':raw['claims'][0]['source_anchors']=[]
    elif fault=='wrong-quote':raw['claims'][0]['source_anchors'][0]['quote_sha256']='f'*64
    elif fault=='wrong-document':raw['claims'][0]['source_anchors'][0]['document_id']='other-project-source'
    elif fault=='duplicate-claim':raw['claims'].append(deepcopy(raw['claims'][0]))
    elif fault=='empty-ids':raw['graphs'][0]['nodes'][0]['claim_ids']=[]
    elif fault=='too-many-anchors':
        values=anchors(sources,33);raw['claims'][0]['source_anchors']=values[:32];raw['claims'][1]['source_anchors']=values[32:]
    elif fault=='receipt-integrity':sources['sha256']='e'*64
    before=canonical(raw)
    with pytest.raises((InvalidInput,Conflict,ValidationError)):
        resolve_graph_evidence(raw,sources)
    check(canonical(raw)==before)


def test_existing_direct_invalid_anchors_are_not_replaced_and_32_anchor_boundary(real_sources):
    raw=candidate(real_sources)
    raw['graphs'][0]['nodes'][0]['source_anchors']=[{**anchors(real_sources)[0],'quote_sha256':'f'*64}]
    resolved,_=resolve_graph_evidence(raw,real_sources)
    check(resolved['graphs'][0]['nodes'][0]['source_anchors']==raw['graphs'][0]['nodes'][0]['source_anchors'])
    with pytest.raises(InvalidInput):DiscoveryWorkbench._validate(DiscoveryCandidate.model_validate(resolved),real_sources)
    raw=candidate(real_sources);values=anchors(real_sources,32)
    raw['claims'][0]['source_anchors']=values;raw['claims'][1]['source_anchors']=[values[-1]]
    resolved,_=resolve_graph_evidence(raw,real_sources)
    check(len(resolved['graphs'][0]['nodes'][0]['source_anchors'])==32)
    DiscoveryWorkbench._validate(DiscoveryCandidate.model_validate(resolved),real_sources)


class ExternalModelBoundary:
    """External reasoning response only; API/Store/source authority remain real."""
    def __init__(self):self.calls=[];self.raw=None;self.mixed=False
    async def propose(self,request):
        self.calls.append(deepcopy(request));raw=candidate(request['source_receipt'])
        if self.mixed:raw['graphs'][0]['nodes'][0]['claim_ids'].append('suggestion')
        self.raw={'status':'RETURNED','candidate_json':raw,'source_receipt_sha256':request['source_receipt']['sha256'],
            'qualification':'EXTERNAL_MODEL_RESPONSE_FIXTURE_NOT_ORIGINAL_CLI'}
        return self.raw


def scene(client,tmp_path):
    pid=client.post('/api/projects',json={'name':'证据图实际API'}).json()['id']
    response=client.post(f'/api/projects/{pid}/documents',files={'file':('资料.txt',('真实原创资料🙂已有流程。'*12).encode())},data={'source':'original-fixture'})
    check(response.status_code==201,response.text);document=response.json()
    # The selected user folder is separate from controller-owned data.
    folder=tmp_path.with_name(tmp_path.name+'-workspace');folder.mkdir()
    bound=client.post(f'/api/projects/{pid}/local-workspaces',json={'idempotency_key':'bind','root':str(folder)})
    check(bound.status_code==201,bound.text);binding=bound.json()
    workspace={k:binding[k] for k in ('id','revision','binding_sha256')}
    view=client.post(f'/api/projects/{pid}/discovery/sessions',json={'operation_id':'create','entry_mode':'REQUIREMENTS',
        'title':'逐条核对','statement':'保留来源，用户确认前不执行。','workspace_ref':workspace,
        'materials':[{'document_id':document['id'],'revision':document['revision'],'sha256':document['sha256']}]}).json()
    boundary=ExternalModelBoundary();client.app.state.discovery.transport=boundary
    return pid,view,boundary,workspace


def test_real_api_persists_host_receipt_raw_result_replay_and_restart_then_edit_invalidates_current_proof(client,tmp_path):
    pid,view,boundary,workspace=scene(client,tmp_path);base=f'/api/projects/{pid}/discovery/sessions/{view["id"]}'
    body={'operation_id':'advance','expected_revision':1,'message':'建立有明确来源的图。','workspace_ref':workspace}
    response=client.post(base+'/advance',json=body);check(response.status_code==200,response.text)
    value=response.json();receipt=value['payload']['evidence_derivation_receipt']
    check(receipt['validated_candidate_sha256']==digest(value['payload']['candidate']))
    check(value['payload']['execution']==boundary.raw and boundary.raw['candidate_json']['graphs'][0]['nodes'][0]['source_anchors']==[])
    check(value['payload']['status']=='DRAFT' and not value['readiness']['execution_authorized'])
    check(client.post(base+'/advance',json=body).json()==value and len(boundary.calls)==1)
    restored=DiscoveryWorkbench(client.app.state.store,client.app.state.planning_proposals)
    check(restored.get(pid,view['id'])==value)
    edited=client.put(base,json={'operation_id':'manual-edit','expected_revision':2,'candidate':value['payload']['candidate'],
        'change_reason':'用户明确重新审阅，不继承宿主原返回派生的当前证明。'})
    check(edited.status_code==200,edited.text)
    check(edited.json()['payload']['evidence_derivation_receipt'] is None)
    check(client.get(base+'?revision=2').json()['payload']['evidence_derivation_receipt']==receipt)
    with client.app.state.store.connect() as db:
        check(db.execute('SELECT count(*) FROM task_runs').fetchone()[0]==0)


def test_failed_mixed_operation_stays_failed_after_explicit_corrected_edit_and_get_only_recovery(client,tmp_path):
    pid,view,boundary,workspace=scene(client,tmp_path);base=f'/api/projects/{pid}/discovery/sessions/{view["id"]}'
    boundary.mixed=True
    body={'operation_id':'failed','expected_revision':1,'message':'边界反例','workspace_ref':workspace}
    failed=client.post(base+'/advance',json=body);check(failed.status_code==422,failed.text)
    check(failed.json()['status']=='FAILED' and len(boundary.calls)==1)
    check(client.post(base+'/advance',json=body).json()==failed.json() and len(boundary.calls)==1)
    operation_url=f'/api/projects/{pid}/discovery/operations/failed'
    original_failure=client.get(operation_url).json()
    check(original_failure['status']=='FAILED' and original_failure['result']==failed.json())
    raw=deepcopy(boundary.raw['candidate_json']);raw['graphs'][0]['nodes'][0]['claim_ids'].remove('suggestion')
    resolved,_=resolve_graph_evidence(raw,boundary.calls[0]['source_receipt'])
    corrected=DiscoveryCandidate.model_validate(resolved).model_dump()
    accepted=client.put(base,json={'operation_id':'explicit-reviewed-adoption','expected_revision':1,'candidate':corrected,
        'change_reason':'用户显式审阅修正后采用；原模型失败保留，不再请求模型。'})
    check(accepted.status_code==200,accepted.text)
    check(client.get(operation_url).json()==original_failure)
    check(len(boundary.calls)==1 and accepted.json()['payload']['status']=='DRAFT')
