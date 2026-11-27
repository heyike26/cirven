"""AUTHORED_NOT_RUN: real native-rework API/Store/text files; no GUI/model.

Original native window identity and native editor races are outside this fixture.
User classifications/input contracts remain declarations, not semantic proof.
"""
import copy
import hashlib
import json
import pytest
from pydantic import ValidationError
from packages.domain.native_rework_conflicts import NativeConflictInspect,NativeConflictResolve
from packages.domain.tasks import digest
from test_native_rework import scene,feedback,mapping,prepare,source,ref,call,apply_body,ok


def setup(s,*,text=False):
    if text:
        file='原页.md';original=(s['root']/file).read_text();quote='独特 A 选文';start=original.index(quote)
        locator={'kind':'TEXT_RANGE','char_start':start,'char_end':start+len(quote),
            'quote_sha256':hashlib.sha256(quote.encode()).hexdigest()}
        f=feedback(s,'text_feedback',file);m=mapping(s,f,'text_mapping',locator=locator)
    else:f=feedback(s);m=mapping(s,f)
    p=prepare(s,m,independent=not text)
    return f,m,p


def query(s,p,*,path='任务与harness.json',locator=None):
    return {'plan_ref':p['record_ref'],'current_source':source(s,path),'current_locator':locator}


def review(s,q):return call(s,'POST','/native-rework/conflicts/inspect',q)
def packet(s,q,v,op='resolve_manual',value='"人工与建议协调后的 A"'):
    return {**q,'operation_id':op,'review_sha256':v['review_sha256'],'resolved_value_text':value,
        'rationale':'用户核对原值、当前人工内容和旧建议后明确选择新预览；未声称语义通过。',
        'current_object_confirmed':True}
def resolve(s,p):return call(s,'POST','/native-rework/conflicts/resolve',p)


def test_three_way_review_is_read_only_and_pins_each_original_and_current_source(scene):
    s=scene;_,_,p=setup(s);path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('A 原已确认要求','A 人工已改要求'))
    q=query(s,p);before=path.read_bytes();v=review(s,q)
    assert v['base_text']=='"A 原已确认要求"' and v['current_text']=='"A 人工已改要求"'
    assert v['proposed_text']=='"A 本次提议要求"' and v['review_sha256']==digest(v['basis'])
    assert v['query_sha256']==digest(q) and v['basis']['current_source']==q['current_source']
    assert v['source_files_written']==v['model_calls']==0 and v['write_authorized'] is False
    assert path.read_bytes()==before


def test_manual_resolution_creates_a_new_preview_without_changing_any_file_or_old_plan(scene):
    s=scene;_,_,p=setup(s);path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('A 原已确认要求','A 当前人工'))
    q=query(s,p);v=review(s,q);before=path.read_bytes();new=resolve(s,packet(s,q,v))
    assert new['kind']=='PLAN' and new['status']=='PROPOSED' and new['record_ref']!=p['record_ref']
    assert new['payload']['manual_resolution']['parent_plan_ref']==p['record_ref']
    assert new['payload']['manual_resolution']['basis']==v['basis']
    assert new['payload']['write_authorized'] is False and path.read_bytes()==before
    old=ok(s['client'].get(s['base']+'/native-rework/plans/'+p['record_ref']['id']))
    assert old['payload']==p['payload']


def test_existing_apply_cas_writes_only_the_manually_resolved_json_value_and_keeps_other_bytes(scene):
    s=scene;_,_,p=setup(s);path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('A 原已确认要求','A 当前人工').replace('原人工备注','人工新备注 保留'))
    before=path.read_text();q=query(s,p);new=resolve(s,packet(s,q,review(s,q)))
    result=call(s,'POST','/native-rework/plans/'+new['record_ref']['id']+'/apply',apply_body(s,new,'apply_manual',False))
    assert result['status']=='APPLIED'
    assert path.read_text()==before.replace('A 当前人工','人工与建议协调后的 A')
    assert (s['root']/'独立任务B.md').read_bytes()==s['independent']


def test_current_source_changed_after_review_cannot_create_a_resolved_preview(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);v=review(s,q);path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('原人工备注','稍后又改'))
    response=s['client'].post(s['base']+'/native-rework/conflicts/resolve',json=packet(s,q,v))
    assert response.status_code==409 and '稍后又改' in path.read_text()


def test_changed_target_again_after_resolution_is_still_a_conflict_under_existing_apply(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);new=resolve(s,packet(s,q,review(s,q)));path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('A 原已确认要求','第三次人工修改'))
    before=path.read_bytes();response=s['client'].post(s['base']+'/native-rework/plans/'+new['record_ref']['id']+'/apply',json=apply_body(s,new,'apply_later',True))
    assert response.status_code==409 and response.json()['status']=='CONFLICT'
    assert path.read_bytes()==before


def test_changed_text_requires_an_exact_current_range_instead_of_guessing_old_offsets(scene):
    s=scene;_,_,p=setup(s,text=True);path=s['root']/'原页.md';path.write_text('# 原页\n人工新的 A 句子\n不动 B。\n')
    q=query(s,p,path='原页.md');v=review(s,q)
    assert v['selection_status']=='CURRENT_SELECTION_REQUIRED' and v['current_text'] is None
    assert v['basis']['current_locator'] is None and v['current_content']==path.read_text()
    response=s['client'].post(s['base']+'/native-rework/conflicts/resolve',json=packet(s,q,v,value='新 A'))
    assert response.status_code==409


def test_explicit_current_text_range_can_be_resolved_while_preserving_outside_manual_edits(scene):
    s=scene;_,_,p=setup(s,text=True);path=s['root']/'原页.md';content='# 人工标题\n人工新的 A 句子\n不动 B。\n';path.write_text(content);quote='人工新的 A 句子';start=content.index(quote)
    locator={'kind':'TEXT_RANGE','char_start':start,'char_end':start+len(quote),'quote_sha256':hashlib.sha256(quote.encode()).hexdigest()}
    q=query(s,p,path='原页.md',locator=locator);v=review(s,q);assert v['basis']['scope_relation']=='USER_CONFIRMED_CURRENT_TEXT_RANGE'
    new=resolve(s,packet(s,q,v,value='人工核对后的 A'))
    call(s,'POST','/native-rework/plans/'+new['record_ref']['id']+'/apply',
        {**apply_body(s,new,'apply_text',False),'expected_current_sources':[source(s,'原页.md')]})
    assert path.read_text()==content.replace(quote,'人工核对后的 A')


def test_unchanged_unique_text_can_be_located_after_outside_edits_without_fuzzy_matching(scene):
    s=scene;_,_,p=setup(s,text=True);path=s['root']/'原页.md';path.write_text('人工前缀\n'+path.read_text())
    v=review(s,query(s,p,path='原页.md'))
    assert v['current_text']=='独特 A 选文' and v['selection_status']=='EXACT_CURRENT_OBJECT'
    assert v['basis']['scope_relation']=='EXACT_UNCHANGED_UNIQUE_QUOTATION'


def test_duplicate_current_quotations_do_not_choose_the_first_occurrence(scene):
    s=scene;_,_,p=setup(s,text=True);path=s['root']/'原页.md';path.write_text(path.read_text()+'独特 A 选文\n')
    v=review(s,query(s,p,path='原页.md'))
    assert v['selection_status']=='CURRENT_SELECTION_REQUIRED' and v['basis']['current_locator'] is None


def test_json_resolution_cannot_switch_to_another_object_or_whole_file(scene):
    s=scene;_,_,p=setup(s)
    for locator in [{'kind':'JSON_POINTER','pointer':'/tasks/b/requirement'},{'kind':'FILE'}]:
        response=s['client'].post(s['base']+'/native-rework/conflicts/inspect',json=query(s,p,locator=locator))
        assert response.status_code in {409,422}


def test_another_file_or_changed_workspace_authority_is_not_a_current_resolution_source(scene):
    s=scene;_,_,p=setup(s);q=query(s,p,path='独立任务B.md')
    assert s['client'].post(s['base']+'/native-rework/conflicts/inspect',json=q).status_code==409
    q=query(s,p);q['current_source']['binding_sha256']='0'*64
    assert s['client'].post(s['base']+'/native-rework/conflicts/inspect',json=q).status_code==409


def test_resolution_preserves_old_consumed_input_contracts_and_proposal_confirmation(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);new=resolve(s,packet(s,q,review(s,q)))
    old_engine=p['payload']['engine_plan']['payload'];engine=new['payload']['engine_plan']['payload']
    assert engine['contracts']==old_engine['contracts']
    assert engine['classification']['kind']=='UNKNOWN' and engine['classification']['origin']=='USER_STATEMENT'
    assert engine['confirmation_preservation']==old_engine['confirmation_preservation']
    assert new['payload']['manual_resolution']['old_input_contracts_preserved'] is True
    assert engine['patches'][0]['binding_ref']!=old_engine['patches'][0]['binding_ref']


def test_original_resolve_key_returns_its_saved_preview_after_later_file_changes_without_rewriting(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);r=packet(s,q,review(s,q));new=resolve(s,r);path=s['root']/'任务与harness.json';path.write_text(path.read_text().replace('原人工备注','后来人工备注'))
    before=path.read_bytes();assert resolve(s,r)['record_ref']==new['record_ref'] and path.read_bytes()==before
    changed={**r,'resolved_value_text':'"另一值"'}
    assert s['client'].post(s['base']+'/native-rework/conflicts/resolve',json=changed).status_code==409


def test_json_value_text_keeps_large_integer_and_exponent_exact_without_js_numeric_roundtrip(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);text='{"precise":9007199254740993,"epsilon":1e-7}'
    new=resolve(s,packet(s,q,review(s,q),value=text))
    manual=new['payload']['manual_resolution'];assert manual['resolved_value_text']==text
    assert manual['resolved_text_sha256']==hashlib.sha256(text.encode()).hexdigest()
    call(s,'POST','/native-rework/plans/'+new['record_ref']['id']+'/apply',apply_body(s,new,'apply_number',False))
    result=json.loads((s['root']/'任务与harness.json').read_text())['tasks']['a']['requirement']
    assert result['precise']==9007199254740993 and result['epsilon']==1e-7


def test_invalid_or_duplicate_key_json_never_creates_a_new_binding_or_writes_source(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);v=review(s,q);before=(s['root']/'任务与harness.json').read_bytes()
    for i,text in enumerate(['{"a":1,"a":2}','NaN','1e309','{broken']):
        response=s['client'].post(s['base']+'/native-rework/conflicts/resolve',json=packet(s,q,v,'invalid_'+str(i),text))
        assert response.status_code==422
    assert (s['root']/'任务与harness.json').read_bytes()==before


def test_strict_resolution_requires_current_object_confirmation_and_rejects_execution_fields(scene):
    s=scene;_,_,p=setup(s);q=query(s,p);r=packet(s,q,review(s,q))
    for extra in [{'current_object_confirmed':False},{'command':'run'},{'write_authorized':True},{'operation_id':None}]:
        with pytest.raises(ValidationError):NativeConflictResolve.model_validate({**r,**extra})
    with pytest.raises(ValidationError):NativeConflictInspect.model_validate({**q,'allow_rebase':True})


def test_empty_manual_text_is_an_explicit_new_preview_not_an_implicit_keep_or_apply(scene):
    s=scene;_,_,p=setup(s,text=True);q=query(s,p,path='原页.md');before=(s['root']/'原页.md').read_bytes()
    new=resolve(s,packet(s,q,review(s,q),value=''))
    assert new['payload']['proposed_value']=='' and new['payload']['manual_resolution']['resolved_value_text']==''
    assert (s['root']/'原页.md').read_bytes()==before and new['payload']['write_authorized'] is False
