"""AUTHORED_NOT_RUN: original maintenance -> subsequent frozen delivery.

Actual test-owned SQL/saved files/ZIP when eventually run. Inherited owner,
native dispatch ACK, model and observer fixtures are explicit UNIT boundaries.
No target issue, Windows, current installation or source permission is tested.
"""
from copy import deepcopy
from io import BytesIO
import hashlib
import json
from zipfile import ZipFile
import pytest
from packages.agent_sessions.work_deliveries import WorkDeliveryPrepare
from packages.agent_sessions.work_maintenance import WorkMaintenance
from packages.domain.store import Conflict
from packages.domain.tasks import digest
from packages.domain.local_workspaces import canonical
from test_original_work_maintenance import maintenance,change,packet,view
from test_original_work_deliveries import (delivery,prepare,create_packet as original_packet,
    create as original_create,download,lookup)
from test_original_work_test_cases import mapped
from test_command_observations_routes import routed
from test_opencode_command_observation import command_scene


@pytest.fixture
def subsequent(maintenance):
    s=maintenance
    s['input']=s['maintenance_input']
    s['root_sha256']=s['maintenance_root_sha256']
    s['delivery_query']={**s['delivery_query'],**s['input'],
        'root_sha256':s['root_sha256'],'version_label':'0.2 maintenance',
        'notes':'后续维护交付；仍未核实现场故障解决。'}
    return s


def create_packet(s,review,op='maintenance_delivery_1',**changes):
    return original_packet(s,review,op,**changes)


def create(s,body=None):
    return original_create(s,body or create_packet(s,prepare(s)))


def frozen(s):
    root,manifest,review,_=s['deliveries']._prepare(s['pid'],s['ident'],
        WorkDeliveryPrepare.model_validate(s['delivery_query']))
    return root,manifest,review


def archive_manifest(s,receipt):
    with ZipFile(BytesIO(download(s,receipt))) as archive:
        return json.loads(archive.read('manifest.json')),archive.namelist()


def test_empty_maintenance_head_is_frozen_so_first_later_association_is_detectable(subsequent):
    s=subsequent;_,manifest,_=frozen(s);context=manifest['evidence']['maintenance']
    assert context['revision']==0 and context['associations']==[]
    assert context['root_sha256']==s['root_sha256'] and context['project_id']==s['pid']


def test_subsequent_archive_keeps_exact_original_decision_and_delivery_refs(subsequent):
    s=subsequent;p=packet(s);r=change(s,p)['receipt'];result=create(s)
    manifest,names=archive_manifest(s,result);context=manifest['evidence']['maintenance'];a=context['associations'][0]
    assert digest(manifest)==result['manifest_sha256']
    assert a['decision_ref']=={'operation_id':p['operation_id'],'request_sha256':digest(p),
        'receipt_sha256':digest(r),'revision':1}
    assert a['delivery_ref']==s['baseline_ref'] and a['baseline_sha256']==r['baseline_sha256']
    assert context['revision']==1 and context['root_sha256']!=a['delivery_ref']['root_sha256']
    assert names==['manifest.json','READ_ME.txt','files/release.py']


def test_reason_excerpt_binds_complete_original_unicode_note_without_recursive_evidence(subsequent):
    s=subsequent;note='保留现场失败😀。'*350;r=change(s,packet(s,note=note))['receipt']
    _,manifest,_=frozen(s);context=manifest['evidence']['maintenance'];a=context['associations'][0]
    assert a['note_excerpt']==note[:240] and a['note_length']==len(note)
    assert a['note_sha256']==hashlib.sha256(note.encode('utf-8')).hexdigest()
    assert a['warnings_sha256']==digest(r['baseline']['warnings'])
    assert a['warnings_count']==len(r['baseline']['warnings'])
    assert a['warnings_excerpt']==r['baseline']['warnings'][:8]
    assert 'manifest' not in a and 'baseline' not in a and 'artifact' not in a
    assert context['source_files_written']==context['model_calls']==context['commands_started']==0
    assert context['permission_granted'] is False and context['issue_resolution']=='NOT_ASSESSED'
    assert context['current_baseline_bytes_verified'] is False


def test_metadata_snapshot_does_not_reopen_old_owner_workspace_zip_or_baseline(subsequent,monkeypatch):
    s=subsequent;change(s)
    def forbidden(*args,**kwargs):raise AssertionError('No current baseline/ZIP/native read')
    monkeypatch.setattr(WorkMaintenance,'_baseline',forbidden)
    with s['service'].store.connect() as db:
        root=s['deliveries']._prepare(s['pid'],s['ident'],WorkDeliveryPrepare.model_validate(s['delivery_query']))[0]
        context=s['maintenance'].frozen_context(db,s['pid'],s['ident'],root)
    assert context['associations'][0]['delivery_ref']==s['baseline_ref']


def test_evidence_opt_out_still_keeps_bounded_maintenance_lineage_without_old_report_text(subsequent):
    s=subsequent;change(s);s['delivery_query']['include_evidence']=False
    result=create(s);manifest,names=archive_manifest(s,result)
    assert manifest['evidence']['maintenance']['associations']
    assert manifest['evidence']['reports']=={} and 'EVIDENCE_CONTENT_NOT_INCLUDED' in manifest['warnings']
    assert not any(name.startswith('evidence/') for name in names)


def test_maintenance_change_after_review_requires_new_review_before_any_intent(subsequent):
    s=subsequent;p=create_packet(s,prepare(s));change(s)
    with pytest.raises(Conflict,match='selection or evidence changed'):create(s,p)
    with s['service'].store.connect() as db:
        assert db.execute('SELECT count(*) FROM original_work_delivery_intents WHERE root_operation_id=?',
            (s['input']['root_operation_id'],)).fetchone()[0]==0


def test_maintenance_cas_is_rechecked_inside_new_delivery_intent_transaction(subsequent,monkeypatch):
    s=subsequent;p=create_packet(s,prepare(s));original=s['deliveries']._prepare
    def change_after_review(*args,**kwargs):
        result=original(*args,**kwargs);change(s);return result
    monkeypatch.setattr(s['deliveries'],'_prepare',change_after_review)
    with pytest.raises(Conflict,match='maintenance associations changed'):create(s,p)
    with s['service'].store.connect() as db:
        assert db.execute('SELECT count(*) FROM original_work_delivery_intents WHERE root_operation_id=?',
            (s['input']['root_operation_id'],)).fetchone()[0]==0


def test_case_or_requirement_head_race_is_also_rechecked_before_new_intent(subsequent,monkeypatch):
    s=subsequent;p=create_packet(s,prepare(s));original=s['deliveries']._prepare
    from packages.agent_sessions import work_deliveries as deliveries
    head=deliveries.cases._head
    def change_after_review(*args,**kwargs):
        result=original(*args,**kwargs)
        monkeypatch.setattr(deliveries.cases,'_head',lambda *a,**k:(1,head(*a,**k)[1]))
        return result
    monkeypatch.setattr(s['deliveries'],'_prepare',change_after_review)
    with pytest.raises(Conflict,match='test-case associations changed'):create(s,p)


def test_pending_original_delivery_continues_frozen_association_after_later_removal(subsequent,monkeypatch):
    s=subsequent;change(s);p=create_packet(s,prepare(s));original=s['deliveries']._archive
    def interrupted(*args,**kwargs):raise OSError('UNIT interruption after immutable intent')
    monkeypatch.setattr(s['deliveries'],'_archive',interrupted)
    with pytest.raises(OSError):create(s,p)
    change(s,packet(s,'maintenance_remove',action='REMOVE',note='仅撤回后续关联；保留历史。'))
    assert view(s)['revision']==2 and not view(s)['active']
    monkeypatch.setattr(s['deliveries'],'_archive',original)
    result=s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    manifest,_=archive_manifest(s,result)
    assert manifest['evidence']['maintenance']['revision']==1
    assert manifest['evidence']['maintenance']['associations'][0]['delivery_ref']==s['baseline_ref']
    assert create(s,p)==result


def test_completed_archive_lookup_and_download_do_not_adopt_current_maintenance_head(subsequent):
    s=subsequent;change(s);p=create_packet(s,prepare(s));result=create(s,p);raw=download(s,result)
    change(s,packet(s,'maintenance_remove',action='REMOVE',note='保留旧包，后续不再引用。'))
    assert download(s,result)==raw and s['deliveries'].operation(s['pid'],s['ident'],lookup(s,p))==result
    recovered=s['deliveries'].recovery(s['pid'],s['ident'],lookup(s,p))
    assert recovered['original_request']==p and recovered['manifest_sha256']==result['manifest_sha256']


def test_corrupt_original_maintenance_receipt_blocks_new_snapshot_without_repair_or_delete(subsequent):
    s=subsequent;change(s)
    with s['service'].store.connect() as db:
        trigger=db.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='original_work_maintenance_operations' AND lower(sql) LIKE '%before update%'").fetchone()[0]
        db.execute('DROP TRIGGER "'+trigger+'"')
        db.execute("UPDATE original_work_maintenance_operations SET receipt_sha256=?",('f'*64,))
    with pytest.raises(Conflict,match='Maintenance record SHA differs'):prepare(s)
    with s['service'].store.connect() as db:
        assert db.execute('SELECT count(*) FROM original_work_maintenance_operations').fetchone()[0]==1


def test_removed_association_is_absent_from_new_context_but_revision_and_history_remain(subsequent):
    s=subsequent;change(s);change(s,packet(s,'maintenance_remove',action='REMOVE',note='撤回关联，不抹掉原说明。'))
    _,manifest,_=frozen(s);context=manifest['evidence']['maintenance']
    assert context['revision']==2 and context['associations']==[]
    assert len(view(s)['history'])==2


def test_legacy_intent_without_maintenance_field_continues_without_rewriting_its_manifest(subsequent):
    s=subsequent;_,manifest,review=frozen(s)
    # Test-owned immutable fixture in the previous manifest shape, not a claim
    # that this newly authored archive is an actual historical delivery.
    del manifest['evidence']['maintenance'];review=deepcopy(review)
    review['basis']['manifest_sha256']=digest(manifest)
    review['review_sha256']=digest(review['basis']);p=create_packet(s,review)
    with s['service'].store.connect() as db:
        db.execute('INSERT INTO original_work_delivery_intents VALUES (?,?,?,?,?,?,?,?,?,?,?)',
            (s['pid'],s['ident'],p['operation_id'],p['root_operation_id'],p['root_sha256'],0,
             canonical(p),digest(p),canonical(manifest),digest(manifest),'2026-11-04T00:00:00+00:00'))
    change(s)
    result=s['deliveries'].continue_original(s['pid'],s['ident'],lookup(s,p))
    actual,_=archive_manifest(s,result)
    assert actual==manifest and 'maintenance' not in actual['evidence']
