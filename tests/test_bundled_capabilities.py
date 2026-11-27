"""Reviewed actual skills, real registry files and restart/CAS boundaries."""
import hashlib
import json
from pathlib import Path
import shutil

import pytest

import packages.domain.bundled_capabilities as bundled
from packages.domain.bundled_capabilities import BUNDLED_SKILLS, bundled_manifest, ensure_bundled_skills
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict, Store

ROOT=Path(__file__).resolve().parents[1]


@pytest.fixture
def registry(tmp_path):
    return NativeCapabilities(Store(tmp_path/'eeas.sqlite3'),tmp_path)


@pytest.fixture
def original_source(tmp_path):
    root=tmp_path/'actual-original-skill-copy';root.mkdir()
    for entry in bundled_manifest()['skills']:
        target=root/entry['path'];target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(ROOT/entry['path'],target)
    return root


def row(receipt,ident):
    return next(item for item in receipt['skills'] if item['id']==ident)


def import_manual(registry,ident,*,enabled=False):
    content='---\nname: '+ident+'\ndescription: Original human replacement fixture.\n---\nHuman replacement remains authoritative. 中文🙂\n'
    saved=registry.import_skill({'operation_id':'manual-'+ident,'id':ident,'expected_revision':0,
        'content':content,'enabled':enabled})
    return saved,content


def test_first_import_is_exact_original_eleven_native_paths_no_execution(registry,original_source,monkeypatch):
    monkeypatch.setattr(registry,'probe_mcp',lambda *_args:pytest.fail('No process-start probe permitted by seeding'))
    receipt=ensure_bundled_skills(registry,original_source)
    assert receipt['status']=='REGISTERED' and receipt['imported_count']==11 and receipt['preserved_count']==0
    assert not receipt['model_started'] and not receipt['mcp_started'] and not receipt['permission_granted']
    assert {item['id'] for item in receipt['skills']}=={item[0] for item in BUNDLED_SKILLS}
    binding=registry.native_binding_snapshot()['value'];paths=binding['native_config_fragment']['skills']['paths']
    assert len(paths)==11 and not binding['permission_granted'] and not binding['loaded_in_original_session']
    assert binding['native_config_fragment']['mcp']=={}
    originals={item['id']:item for item in bundled_manifest()['skills']}
    for folder in paths:
        path=Path(folder)/'SKILL.md';ident=path.parent.parent.name;entry=originals[ident]
        assert path.read_bytes()==(original_source/entry['path']).read_bytes()
        assert hashlib.sha256(path.read_bytes()).hexdigest()==entry['sha256']
        assert registry.skill_content(ident)['enabled'] is True
    reference=receipt['installation_receipt_ref'];raw=Path(reference['path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==reference['sha256']
    stored=json.loads(raw);assert stored['value']['bundle_version']==receipt['bundle_version']


def test_restart_keeps_config_refs_and_immutable_first_receipt_without_import(registry,original_source,monkeypatch):
    first=ensure_bundled_skills(registry,original_source);binding=registry.native_binding_snapshot()
    original_receipt=Path(first['installation_receipt_ref']['path']).read_bytes()
    revived=NativeCapabilities(Store(registry.store.database),registry.data_dir)
    monkeypatch.setattr(revived,'import_skill',lambda *_args:pytest.fail('Existing skills cannot be re-imported'))
    second=ensure_bundled_skills(revived,original_source)
    assert second['status']=='REGISTERED' and second['imported_count']==0 and second['preserved_count']==11
    assert revived.native_binding_snapshot()==binding
    assert Path(first['installation_receipt_ref']['path']).read_bytes()==original_receipt
    assert second['installation_receipt_ref']['path']!=first['installation_receipt_ref']['path']


def test_disabled_existing_skill_is_never_enabled_or_revised(registry,original_source):
    ensure_bundled_skills(registry,original_source);ident=BUNDLED_SKILLS[0][0]
    disabled=registry.set_enabled('skills',ident,{'operation_id':'human-disable','expected_revision':1,'enabled':False})
    result=ensure_bundled_skills(registry,original_source)
    assert row(result,ident)['state']=='PRESERVED_EXISTING' and row(result,ident)['enabled'] is False
    assert row(result,ident)['config_ref']==disabled['config_ref']
    assert len(registry.native_binding_snapshot()['value']['native_config_fragment']['skills']['paths'])==10


def test_manual_same_identity_wins_without_byte_or_enabled_changes(registry,original_source):
    ident=BUNDLED_SKILLS[0][0];saved,content=import_manual(registry,ident)
    result=ensure_bundled_skills(registry,original_source)
    assert result['imported_count']==10 and result['preserved_count']==1
    current=row(result,ident);assert current['state']=='PRESERVED_EXISTING' and not current['bundle_source_matches_current']
    assert current['enabled'] is False and current['config_ref']==saved['config_ref']
    assert registry.skill_content(ident)['content']==content
    assert len(registry.native_binding_snapshot()['value']['native_config_fragment']['skills']['paths'])==10


def test_reviewed_source_tamper_is_explicit_gap_not_new_trusted_hash(registry,original_source):
    entry=bundled_manifest()['skills'][0];path=original_source/entry['path'];path.write_bytes(path.read_bytes()+b' changed')
    receipt=ensure_bundled_skills(registry,original_source)
    assert receipt['status']=='REGISTERED_WITH_GAPS' and receipt['imported_count']==10
    assert row(receipt,entry['id'])['state']=='SOURCE_UNAVAILABLE'
    assert entry['id'] not in {item['id'] for item in registry.get()['skills']}
    assert len(registry.native_binding_snapshot()['value']['native_config_fragment']['skills']['paths'])==10


def test_existing_native_file_tamper_preserved_reported_and_not_bound(registry,original_source):
    ensure_bundled_skills(registry,original_source);ident=BUNDLED_SKILLS[0][0]
    path=registry._path('skill-files',ident,'0000000001','SKILL.md');path.write_bytes(b'Original corrupt native source fixture')
    receipt=ensure_bundled_skills(registry,original_source)
    assert receipt['status']=='REGISTERED_WITH_GAPS'
    assert row(receipt,ident)['gap']=='EXISTING_SKILL_INTEGRITY_ERROR'
    assert path.read_bytes()==b'Original corrupt native source fixture'
    with pytest.raises(Conflict):registry.native_binding_snapshot()


def test_interrupted_pointer_replays_same_import_operation_on_restart(registry,original_source,monkeypatch):
    original=registry._write;failed=False
    def interrupted(path,value,**kwargs):
        nonlocal failed
        if path.name=='index.json' and not failed:
            failed=True;raise OSError('Original interrupted registry pointer write')
        return original(path,value,**kwargs)
    monkeypatch.setattr(registry,'_write',interrupted)
    first=ensure_bundled_skills(registry,original_source)
    assert first['status']=='REGISTERED_WITH_GAPS' and first['imported_count']==10
    revived=NativeCapabilities(Store(registry.store.database),registry.data_dir)
    second=ensure_bundled_skills(revived,original_source)
    assert second['status']=='REGISTERED' and second['imported_count']==1
    assert len(revived.native_binding_snapshot()['value']['native_config_fragment']['skills']['paths'])==11
    assert all(item['config_ref']['revision']==1 for item in revived.get()['skills'])


def test_corrupt_receipt_blocks_automatic_changes_and_preserves_unknown_state(registry,original_source):
    first=ensure_bundled_skills(registry,original_source);ref=Path(first['installation_receipt_ref']['path'])
    ref.write_bytes(ref.read_bytes().replace(b'REGISTERED',b'TAMPERED!!'))
    result=ensure_bundled_skills(registry,original_source)
    assert result['status']=='REGISTRY_OR_INSTALLATION_RECEIPT_UNAVAILABLE'
    assert result['installation_receipt_ref'] is None
    assert len(registry.native_binding_snapshot()['value']['native_config_fragment']['skills']['paths'])==11


def test_future_reviewed_version_does_not_silently_update_existing_ids(registry,original_source,monkeypatch):
    first=ensure_bundled_skills(registry,original_source);before=registry.native_binding_snapshot()
    ident,folder,_size,_sha=BUNDLED_SKILLS[0];path=original_source/'packages/planning/skills'/folder/'SKILL.md'
    raw=path.read_bytes()+b'\nOriginal explicit future upgrade fixture.\n';path.write_bytes(raw)
    replacement=((ident,folder,len(raw),hashlib.sha256(raw).hexdigest()),*BUNDLED_SKILLS[1:])
    monkeypatch.setattr(bundled,'BUNDLED_SKILLS',replacement);monkeypatch.setattr(bundled,'BUNDLE_VERSION','eeas.planning-skills.v2-original-fixture')
    upgraded=ensure_bundled_skills(registry,original_source)
    assert upgraded['bundle_manifest_sha256']!=first['bundle_manifest_sha256']
    assert upgraded['imported_count']==0 and upgraded['preserved_count']==11
    assert row(upgraded,ident)['bundle_source_matches_current'] is False
    assert registry.native_binding_snapshot()==before


def test_source_directory_link_cannot_supply_bundled_skill(registry,original_source,tmp_path):
    entry=bundled_manifest()['skills'][0];directory=(original_source/entry['path']).parent
    moved=tmp_path/'original-linked-directory';directory.rename(moved);directory.symlink_to(moved,target_is_directory=True)
    receipt=ensure_bundled_skills(registry,original_source)
    assert receipt['status']=='REGISTERED_WITH_GAPS' and row(receipt,entry['id'])['source_integrity']=='FAILED'


def test_concurrent_human_import_is_preserved_by_real_registry_cas(registry,original_source,monkeypatch):
    original=registry.import_skill;ident=BUNDLED_SKILLS[0][0];injected=False
    def collide(body):
        nonlocal injected
        if body['id']==ident and not injected:
            injected=True
            original({'operation_id':'original-human-race','id':ident,'expected_revision':0,'enabled':False,
                'content':'---\nname: '+ident+'\ndescription: Original human race.\n---\nHuman source wins.\n'})
        return original(body)
    monkeypatch.setattr(registry,'import_skill',collide)
    receipt=ensure_bundled_skills(registry,original_source)
    assert row(receipt,ident)['state']=='PRESERVED_EXISTING' and row(receipt,ident)['enabled'] is False
    assert registry.skill_content(ident)['content'].endswith('Human source wins.\n')


def test_previous_bundle_gaps_can_fill_without_replacing_user_choices(registry,original_source,monkeypatch):
    old_pins = {
        'eeas-acceptance-review': (582, 'fb3a8a7a7648ed40852d41340fed509c2b9ef4938f357d4497afa73d380a8cbb'),
        'engineering-requirements': (1055, '3f67e6958f4724102704d5e6a990925fb9728e2996b0e797a39a5d418f5fefde'),
        'eeas-task-decomposition': (649, '66f26596cb850530fa79d4e71a1210db002c99ccb2f808282b2a2c08f286b48f'),
    }
    human, content = import_manual(registry,'eeas-acceptance-review')
    with monkeypatch.context() as old:
        old.setattr(bundled,'BUNDLE_VERSION','eeas.planning-skills.v1')
        old.setattr(bundled,'BUNDLED_SKILLS',tuple((ident,folder,*old_pins.get(ident,(size,sha)))
                                                    for ident,folder,size,sha in BUNDLED_SKILLS))
        prior = ensure_bundled_skills(registry,original_source)
    assert prior['bundle_manifest_sha256'] == bundled.V1_MANIFEST_SHA256
    assert prior['imported_count'] == 8 and prior['status'] == 'REGISTERED_WITH_GAPS'
    missing = 'diagram-evidence'
    index = registry._index()
    del index['skills'][missing]
    registry._write(registry._path('index.json'),index,replace=True)
    for imported in (2,0):
        upgraded = ensure_bundled_skills(registry,original_source)
        assert upgraded['imported_count'] == imported
        assert row(upgraded,missing)['state'] == 'PREVIOUSLY_INSTALLED_NOW_MISSING'
        assert registry.skill_content('eeas-acceptance-review')['content'] == content
        preserved = row(upgraded,'eeas-acceptance-review')
        assert not preserved['enabled'] and preserved['config_ref'] == human['config_ref']
        assert all(registry.skill_content(k)['enabled'] for k in ('engineering-requirements','eeas-task-decomposition'))


def test_new_project_offers_all_reviewed_skills_and_pins_only_selected_on_node(client):
    from test_stage_workflow_runs import ROOT as workflow_root, action, define, node, ok, scope, start
    from packages.dynamic_workflow.kernel_settings import selected_capabilities
    owner = scope(client)
    catalog = ok(client.get('/api/workflow-kernels'))['capabilities']['skills']
    assert {item['id'] for item in catalog} == {item[0] for item in BUNDLED_SKILLS}
    selected = ['eeas-acceptance-review','engineering-requirements','eeas-task-decomposition']
    define(client,owner,[node('first'),node('second')])
    run = start(client,owner)
    grants = {'skills':selected,'mcp':[],'tools':[]}
    run = ok(action(client,owner,run,'choose-skills','configure',{'capabilities':grants}))
    run = ok(client.get(workflow_root+'/runs/'+run['id'],params=owner))
    first,second = run['stages']
    ids = set(selected)
    assert set(first['policy_pin']['capability_refs']['skills']) == ids
    assert not second['config']['capabilities']['skills']
    texts,servers,tools,refs = selected_capabilities(client.app.state.settings.data_dir,first['config']['capabilities'])
    assert refs == first['policy_pin']['capability_refs'] and set(texts) == ids
    assert not servers and not tools
    for item in bundled_manifest()['skills']:
        if item['id'] in ids:
            assert hashlib.sha256(texts[item['id']].encode()).hexdigest() == item['sha256']
