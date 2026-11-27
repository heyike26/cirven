"""AUTHORED_NOT_RUN: received batches/working mirrors, ledger receipts and seeds.

Real private files, SQL and adapter storage helpers; no original SDK process,
model/tool, App/browser/native Windows or full backup qualification.
"""
import asyncio
import hashlib
import json
from pathlib import Path
import uuid
import pytest
from packages.adapters.claude_sdk import ClaudeBridge
from packages.adapters import claude_transcripts as module
from packages.adapters.task_resources import bind_created_execution
from packages.domain.task_resource_budget import TaskResourceBudget,task_resource_scope
from packages.domain.context_read_budget import context_read_scope
from packages.domain.store import Conflict
from packages.domain.tasks import canonical,digest
from test_task_resource_budget import resource
from test_execution_resource_links import record


@pytest.fixture
def transcripts(resource):
    s=resource;authority={**s['authority'],'root_operation_id':'original-transcript-work'};limits={**s['limits'],'source_bytes':1048576,'source_reads':128,'retained_output_bytes':65536}
    budget=TaskResourceBudget(s['ws'],authority,limits=limits);budget.allocate();s={**s,'budget':budget,'authority':authority,'limits':limits}
    owner=object.__new__(ClaudeBridge);owner.service=s['store'];owner.data_dir=s['ws'].data_dir;owner.workspace_authority=s['ws'];owner.active={};owner.http_relays={};owner.transcript_stores={};owner._route_secrets=set()
    with task_resource_scope(budget),s['store'].connect() as db:
        value=record({**s,'tmp':Path(owner.data_dir)/'transcript-workspaces'},db)
        row=value['row'];ident=row['id'];workspace=Path(row['workspace']);workspace.mkdir(parents=True)
        bind_created_execution(owner,db,row)
    store=module.OwnedTranscriptStore(Path(owner.data_dir)/'agent-transcripts'/ident,workspace,owner=owner,execution_id=ident)
    sid=str(uuid.uuid4());key={'project_key':store.project_key,'session_id':sid};owner.transcript_stores[ident]=store
    native=workspace/'.eeas-sdk-state'/'projects'/store.project_key/(sid+'.jsonl')
    return {**s,'owner':owner,'row':row,'store':store,'key':key,'workspace':workspace,'native':native}


def entry(text='原始内容',ident=None):return {'type':'user','uuid':ident or str(uuid.uuid4()),'text':text}
def append(s,entries):return asyncio.run(s['store'].append(s['key'],entries))
def archives(s):return sorted((s['store'].root/'received-batches').glob('*.json'))


def test_received_batch_is_preserved_and_mirror_addition_counts_actual_retained_payloads(transcripts):
    s=transcripts;rows=[entry()];append(s,rows);path=s['store'].path(s['key']);files=archives(s)
    assert len(files)==1 and json.loads(files[0].read_bytes())['value']['entries']==rows
    assert s['budget'].inspect()['used']['retained_output_bytes']==files[0].stat().st_size+path.stat().st_size
    before=s['budget'].inspect()['used']['source_bytes'];assert asyncio.run(s['store'].load(s['key']))==rows
    assert s['budget'].inspect()['used']['source_bytes']>=before+path.stat().st_size and s['budget'].inspect()['used']['model_attempts']==0


def test_identical_no_uuid_deliveries_remain_distinct_even_if_the_caller_reuses_the_list(transcripts):
    s=transcripts;rows=[{'type':'queue-operation','value':'same original content'}]
    append(s,rows);append(s,rows)
    assert asyncio.run(s['store'].load(s['key']))==rows+rows and len(archives(s))==2 and not s['store'].pending


def test_conflicting_uuid_archives_new_input_and_never_overwrites_the_old_mirror(transcripts):
    s=transcripts;one=entry('first');append(s,[one]);path=s['store'].path(s['key']);before=path.read_bytes();other={**one,'text':'conflicting received content'}
    append(s,[other]);assert path.read_bytes()==before and len(archives(s))==2 and not s['store'].healthy()
    assert any(json.loads(p.read_bytes())['value']['entries']==[other] for p in archives(s))
    with pytest.raises(Conflict):asyncio.run(s['store'].load(s['key']))
    with pytest.raises(Conflict):s['budget'].consume(model_attempts=1)


def test_soft_output_threshold_keeps_already_received_batch_and_working_mirror(transcripts):
    s=transcripts;b=s['budget'];b.adjust('unit_limit','a'*64,b.inspect()['resource_ref'],{**b.limits,'retained_output_bytes':16})
    rows=[entry('received despite soft threshold')];append(s,rows)
    assert asyncio.run(s['store'].load(s['key']))==rows and b.inspect()['retention_threshold_exceeded']
    with pytest.raises(Conflict):b.consume(model_attempts=1)
    assert s['store'].healthy()


def test_local_counter_write_ack_loss_is_confirmed_by_original_receipt_without_double_debit(transcripts,monkeypatch):
    b=transcripts['budget'];original=b._save;raw=b'original received payload';operation=digest(['original artifact']);sha=hashlib.sha256(raw).hexdigest()
    def lost_ack(value,**kwargs):original(value,**kwargs);raise OSError('UNIT counter saved but acknowledgement lost')
    monkeypatch.setattr(b,'_save',lost_ack);receipt=b.retain_once(operation,sha,len(raw));before=b.path.read_bytes()
    assert receipt is not None and b.retain_once(operation,sha,len(raw))==receipt and b.path.read_bytes()==before
    assert b.inspect()['used']['retained_output_bytes']==len(raw) and not b.inspect()['accounting_unconfirmed']


def test_receipt_conflict_keeps_original_debit_and_unknown_marker_instead_of_rebinding(transcripts):
    b=transcripts['budget'];operation=digest('original receipt');assert b.retain_once(operation,'a'*64,7)
    assert b.retain_once(operation,'b'*64,8) is None and b.inspect()['used']['retained_output_bytes']==7
    assert b.retention_receipt(operation)['content_sha256']=='a'*64 and b.inspect()['accounting_unconfirmed']


def test_legacy_counter_without_artifact_extension_is_readable_without_backfill(transcripts):
    b=transcripts['budget'];before=b.path.read_bytes();assert b.retention_receipt('a'*64) is None
    assert b.path.read_bytes()==before and 'retentions' not in json.loads(before)['value']


def test_external_mirror_change_is_retained_and_new_delivery_is_archived_separately(transcripts):
    s=transcripts;append(s,[entry('old')]);path=s['store'].path(s['key']);manual=path.read_bytes()+b'{"type":"manual","text":"keep this edit"}\n';path.write_bytes(manual)
    append(s,[entry('new received batch')]);assert path.read_bytes()==manual and not s['store'].healthy() and len(archives(s))==2


def test_working_capacity_never_turns_a_stale_mirror_into_a_checkpoint(transcripts,monkeypatch):
    s=transcripts;monkeypatch.setattr(module,'MAX_TRANSCRIPT_BYTES',128);append(s,[entry('short')]);path=s['store'].path(s['key']);before=path.read_bytes()
    append(s,[entry('x'*256)]);assert path.read_bytes()==before and len(archives(s))==2 and s['store'].marker.exists()
    with s['store'].io.owner.service.connect() as db:assert db.execute('SELECT count(*) FROM agent_checkpoints').fetchone()[0]==0


def test_failed_archive_keeps_original_batch_in_memory_then_metadata_retry_only_preserves_it(transcripts,monkeypatch):
    s=transcripts;rows=[entry('received before archive failure')];original=s['store'].io.write
    def fail(*_args,**_kwargs):raise OSError('UNIT private archive unavailable')
    monkeypatch.setattr(s['store'].io,'write',fail)
    with pytest.raises(OSError):append(s,rows)
    pending=next(iter(s['store'].pending.values()));batch_id=pending['id'];assert pending['record']['entries']==rows
    monkeypatch.setattr(s['store'].io,'write',original);append(s,rows)
    assert not s['store'].pending and len(archives(s))==1 and json.loads(archives(s)[0].read_bytes())['value']['batch_id']==batch_id
    assert not s['store'].healthy() and s['budget'].inspect()['used']['model_attempts']==0


def finish(s):
    with s['store'].io.owner.service.connect() as db:
        db.execute("UPDATE agent_executions SET session_id=?,execution_status='RETURNED' WHERE id=?",(s['key']['session_id'],s['row']['id']))
        return dict(db.execute('SELECT * FROM agent_executions WHERE id=?',(s['row']['id'],)).fetchone())


def test_checkpoint_compares_actual_native_transcript_content_before_binding_current_version(transcripts):
    s=transcripts;rows=[entry('原生和镜像正文一致')];append(s,rows)
    s['native'].parent.mkdir(parents=True);s['native'].write_text(''.join(json.dumps(r)+'\n' for r in rows));row=finish(s)
    with task_resource_scope(s['budget']),context_read_scope():s['owner']._capture_checkpoint(s['authority']['project_id'],row)
    with s['store'].io.owner.service.connect() as db:
        checkpoint=dict(db.execute('SELECT * FROM agent_checkpoints').fetchone())
    assert checkpoint['transcript_sha256']==hashlib.sha256(s['store'].path(s['key']).read_bytes()).hexdigest()
    assert row['id'] not in s['owner'].transcript_stores and s['budget'].inspect()['used']['model_attempts']==0


@pytest.mark.parametrize('fault',['missing','extra_entry','different_content'])
def test_missing_or_divergent_native_transcript_never_gets_a_fresh_checkpoint(transcripts,fault):
    s=transcripts;rows=[entry('mirror version')];append(s,rows)
    if fault!='missing':
        native=rows+[entry('not mirrored')] if fault=='extra_entry' else [{**rows[0],'text':'different original native content'}]
        s['native'].parent.mkdir(parents=True);s['native'].write_text(''.join(json.dumps(r)+'\n' for r in native))
    row=finish(s)
    with task_resource_scope(s['budget']),context_read_scope():s['owner']._capture_checkpoint(s['authority']['project_id'],row)
    with s['store'].io.owner.service.connect() as db:assert db.execute('SELECT count(*) FROM agent_checkpoints').fetchone()[0]==0
    assert s['store'].path(s['key']).exists() and len(archives(s))==1


def test_two_resume_seed_copies_share_original_allowance_and_repeat_is_not_charged_again(transcripts):
    s=transcripts;rows=[entry('frozen seed')];raw=''.join(canonical(r)+'\n' for r in rows).encode()
    assert s['store'].io.write(s['native'],raw,purpose='ORIGINAL_NATIVE_RESUME_SEED')
    assert s['store'].seed(s['key'],raw,purpose='ORIGINAL_MIRROR_RESUME_SEED')
    before=s['budget'].path.read_bytes()
    assert s['store'].io.write(s['native'],raw,purpose='ORIGINAL_NATIVE_RESUME_SEED')
    assert s['store'].seed(s['key'],raw,purpose='ORIGINAL_MIRROR_RESUME_SEED')
    assert s['budget'].path.read_bytes()==before and s['budget'].inspect()['used']['retained_output_bytes']==2*len(raw)
    assert asyncio.run(s['store'].load(s['key']))==rows


def test_native_source_changed_after_checkpoint_blocks_continuation_and_preserves_both_versions(transcripts):
    s=transcripts;rows=[entry('saved version')];append(s,rows);s['native'].parent.mkdir(parents=True);s['native'].write_text(''.join(json.dumps(r)+'\n' for r in rows));row=finish(s)
    with task_resource_scope(s['budget']),context_read_scope():s['owner']._capture_checkpoint(s['authority']['project_id'],row)
    mirror=s['store'].path(s['key']);saved=mirror.read_bytes();manual=s['native'].read_bytes()+b'{"type":"manual","text":"keep newer native data"}\n';s['native'].write_bytes(manual)
    with pytest.raises(Conflict):
        with task_resource_scope(s['budget']),context_read_scope():s['owner']._checkpoint(s['authority']['project_id'],row)
    assert mirror.read_bytes()==saved and s['native'].read_bytes()==manual and s['budget'].inspect()['used']['model_attempts']==0


def test_workspace_snapshot_does_not_accept_an_incomplete_directory_after_external_addition(transcripts,monkeypatch):
    from packages.domain import context_read_budget as source_module
    s=transcripts;one=s['workspace']/'one.txt';two=s['workspace']/'manual-new.txt';one.write_bytes(b'old original');read=source_module.source_bytes;changed=False
    def external_change(path,*args,**kwargs):
        nonlocal changed
        raw=read(path,*args,**kwargs)
        if not changed:changed=True;two.write_bytes(b'new manual content')
        return raw
    monkeypatch.setattr(source_module,'source_bytes',external_change)
    with pytest.raises(Conflict):
        with task_resource_scope(s['budget']),context_read_scope():s['owner'].snapshot(s['workspace'])
    assert one.read_bytes()==b'old original' and two.read_bytes()==b'new manual content' and s['budget'].inspect()['used']['model_attempts']==0
