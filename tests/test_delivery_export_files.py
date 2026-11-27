"""AUTHORED_NOT_RUN: actual export adapter; injected Win32 counter is UNIT.

POSIX files and the separately marked real-Windows case are also NOT_RUN.
No injected handle result qualifies a Windows kernel or native Save dialog.
"""
import hashlib
from io import BytesIO
import os
from zipfile import ZipFile, ZIP_DEFLATED
import pytest
from packages.adapters.delivery_export_files import DeliveryExportFiles,ExportWriteFailure,MAX_EXPORT_BYTES
from packages.adapters.windows_workspace_handles import WindowsWorkspaceHandles,FILE_SHARE_READ
from packages.domain.local_workspaces import digest
from packages.domain.store import Conflict,InvalidInput
from test_windows_workspace_handles import OriginalHandleCounter


@pytest.fixture
def counter():
    api=OriginalHandleCounter();files=DeliveryExportFiles(native=WindowsWorkspaceHandles(api))
    return api,files,files.capture('C:/selected/output.zip')


def test_windows_counter_creation_proof_precedes_first_write_and_receipt_keeps_original_handle(counter):
    api,files,d=counter;proofs=[];raw=b'UNIT bounded original archive bytes'
    def created(v):
        assert not any(c[0]=='write' for c in api.calls)
        assert api.nodes[d['path'].casefold()]['raw']==b''
        assert v['bytes_written_before_record']==0
        proofs.append(v)
    def verified(v):
        assert api.handles and v['file_identity']==proofs[0]['file_identity']
        assert all(h['share']==FILE_SHARE_READ for h in api.handles.values() if h['node']['path']==d['path'])
        assert v['sha256']==hashlib.sha256(raw).hexdigest()
        assert v['external_writers_and_delete_excluded'] is True
    files.create(d,raw,record_created=created,record_verified=verified)
    assert not api.handles
    assert not any(c[0] in {'rename','delete','mkdir','security','restore_metadata'} for c in api.calls)


def test_existing_destination_is_not_opened_for_overwrite_or_removed(counter):
    api,files,d=counter;api.add(d['path'],raw=b'manual file must survive')
    with pytest.raises(ExportWriteFailure) as e:files.create(d,b'new ZIP',record_created=lambda _:None,record_verified=lambda _:None)
    assert e.value.effect=='EXISTING_FILE_UNTOUCHED'
    assert api.nodes[d['path'].casefold()]['raw']==b'manual file must survive'
    assert not api.handles and not any(c[0] in {'write','delete','rename'} for c in api.calls)


def test_native_access_denial_is_unknown_and_never_claims_an_existing_file(counter, monkeypatch):
    from test_windows_workspace_handles import failure
    api,files,d=counter
    original=api.open_relative
    def denied(parent, leaf, access, share, disposition=3, **options):
        if leaf==d['name'] and disposition==1:raise failure(5)
        return original(parent,leaf,access,share,disposition,**options)
    monkeypatch.setattr(api,'open_relative',denied)
    with pytest.raises(ExportWriteFailure) as error:
        files.create(d,b'new ZIP',record_created=lambda _:None,record_verified=lambda _:None)
    assert error.value.effect=='UNKNOWN' and d['path'].casefold() not in api.nodes
    assert not api.handles and not any(c[0] in {'write','delete','rename'} for c in api.calls)


def test_creation_journal_collision_remains_unknown_after_the_file_was_created(counter):
    from test_windows_workspace_handles import failure
    api,files,d=counter
    def collision(_):raise failure(80)
    with pytest.raises(ExportWriteFailure) as error:
        files.create(d,b'new ZIP',record_created=collision,record_verified=lambda _:None)
    assert error.value.effect=='UNKNOWN' and api.nodes[d['path'].casefold()]['raw']==b''
    assert not api.handles and not any(c[0] in {'write','delete','rename'} for c in api.calls)


def test_creation_receipt_failure_keeps_empty_file_and_writes_no_archive_bytes(counter):
    api,files,d=counter
    def fail(_):raise OSError('UNIT original creation journal failed')
    with pytest.raises(ExportWriteFailure) as e:files.create(d,b'original ZIP',record_created=fail,record_verified=lambda _:None)
    assert e.value.effect=='UNKNOWN' and api.nodes[d['path'].casefold()]['raw']==b''
    assert not any(c[0] in {'write','delete','rename'} for c in api.calls) and not api.handles


def test_ack_failure_preserves_complete_original_file_for_readonly_birth_identity_inspection(counter):
    api,files,d=counter;created=[];raw=b'UNIT verified original ZIP'
    def fail(_):raise OSError('UNIT original ACK commit failed')
    with pytest.raises(ExportWriteFailure):files.create(d,raw,record_created=created.append,record_verified=fail)
    before=bytes(api.nodes[d['path'].casefold()]['raw']);writes=len([c for c in api.calls if c[0]=='write'])
    v=files.observe(d,created[0],hashlib.sha256(raw).hexdigest(),len(raw))
    assert v['status']=='MATCHING_CAPTURED_OUTPUT' and v['birth_identity_available'] is True
    assert v['contents_writer_attribution']=='NOT_PROVEN' and api.nodes[d['path'].casefold()]['raw']==before
    assert len([c for c in api.calls if c[0]=='write'])==writes and not api.handles


def test_partial_native_writes_are_bounded_and_verified_under_the_same_handle(counter):
    api,files,d=counter;api.partial_write=True;receipts=[];raw=b'0123456789abcdef'
    files.create(d,raw,record_created=lambda _:None,record_verified=receipts.append)
    assert api.nodes[d['path'].casefold()]['raw']==raw
    assert receipts[0]['byte_length']==len(raw) and len([c for c in api.calls if c[0]=='write'])>1


def test_parent_and_file_identifiers_above_javascript_integer_precision_remain_decimal_strings(counter):
    api,files,_=counter;api.nodes['c:\\selected']['inode']=2**60+5;api.next_inode=2**60+9
    d=files.capture('C:/selected/large id.zip');created=[];verified=[]
    files.create(d,b'ZIP',record_created=created.append,record_verified=verified.append)
    assert d['parent_identity']['inode']==str(2**60+5)
    assert created[0]['file_identity']['inode']==str(2**60+9)
    assert verified[0]['file_identity']==created[0]['file_identity']


@pytest.mark.parametrize('field',['inode','creation'])
def test_changed_file_id_or_birth_never_reuses_matching_bytes_as_original_creation(counter,field):
    api,files,d=counter;created=[];raw=b'original ZIP'
    files.create(d,raw,record_created=created.append,record_verified=lambda _:None)
    api.nodes[d['path'].casefold()][field]+=1
    assert files.observe(d,created[0],hashlib.sha256(raw).hexdigest(),len(raw))['status']=='FILE_IDENTITY_CHANGED'
    assert not any(c[0]=='delete' for c in api.calls)


def test_external_bytes_change_is_observed_without_repair_or_rewrite(counter):
    api,files,d=counter;created=[];raw=b'original ZIP'
    files.create(d,raw,record_created=created.append,record_verified=lambda _:None)
    api.nodes[d['path'].casefold()]['raw']=b'external ZIP'
    v=files.observe(d,created[0],hashlib.sha256(raw).hexdigest(),len(raw))
    assert v['status']=='FILE_BYTES_CHANGED' and api.nodes[d['path'].casefold()]['raw']==b'external ZIP'


def test_parent_replacement_refuses_any_file_creation(counter):
    api,files,d=counter;api.nodes[d['directory'].casefold()]['inode']+=1
    with pytest.raises(ExportWriteFailure) as e:files.create(d,b'ZIP',record_created=lambda _:None,record_verified=lambda _:None)
    assert e.value.effect=='NOT_INVOKED' and d['path'].casefold() not in api.nodes


@pytest.mark.parametrize('value',['C:/selected/CON.zip','C:/selected/thing.txt','C:/selected/a?.zip',r'\\server\share\a.zip'])
def test_unsupported_filename_or_namespace_never_creates_destination(counter,value):
    api,files,_=counter;before=set(api.nodes)
    with pytest.raises((InvalidInput,Conflict,OSError)):files.capture(value)
    assert set(api.nodes)==before


def test_24mib_limit_is_separate_from_existing_edit_limit_and_rejects_oversized_payload_before_open(counter):
    api,files,d=counter;calls=len(api.calls)
    with pytest.raises(InvalidInput):files.create(d,b'x'*(MAX_EXPORT_BYTES+1),record_created=lambda _:None,record_verified=lambda _:None)
    assert len(api.calls)==calls and d['path'].casefold() not in api.nodes


@pytest.mark.skipif(os.name!='posix',reason='POSIX dirfd source boundary only, not native Windows acceptance')
def test_posix_saved_bytes_are_verified_and_manual_existing_output_is_untouched(tmp_path):
    files=DeliveryExportFiles();target=tmp_path/'actual output.zip';d=files.capture(str(target));created=[];verified=[];raw=b'POSIX original archive'
    files.create(d,raw,record_created=created.append,record_verified=verified.append)
    assert target.read_bytes()==raw and verified[0]['external_writers_and_delete_excluded'] is False
    with pytest.raises(ExportWriteFailure):files.create(d,b'no overwrite',record_created=lambda _:None,record_verified=lambda _:None)
    assert target.read_bytes()==raw


@pytest.mark.skipif(os.name!='posix',reason='POSIX nofollow source case, native reparse handling is separate')
def test_posix_symlink_parent_is_not_followed_and_target_is_not_created(tmp_path):
    real=tmp_path/'real';real.mkdir();alias=tmp_path/'alias';alias.symlink_to(real,target_is_directory=True)
    with pytest.raises((OSError,InvalidInput)):DeliveryExportFiles().capture(str(alias/'keep.zip'))
    assert not (real/'keep.zip').exists()


@pytest.mark.skipif(os.name!='nt',reason='AUTHORED_NOT_RUN: requires actual Windows NTFS kernel and an internal test output root')
def test_native_windows_export_creates_one_verified_zip_and_never_overwrites(tmp_path):
    stream=BytesIO()
    with ZipFile(stream,'w',compression=ZIP_DEFLATED) as package:package.writestr('files/actual.txt',b'native kernel payload')
    raw=stream.getvalue();files=DeliveryExportFiles();path=tmp_path/'native original save.zip';d=files.capture(str(path));created=[];receipts=[]
    files.create(d,raw,record_created=created.append,record_verified=receipts.append)
    assert path.read_bytes()==raw and receipts[0]['external_writers_and_delete_excluded'] is True
    assert files.observe(d,created[0],hashlib.sha256(raw).hexdigest(),len(raw))['status']=='MATCHING_CAPTURED_OUTPUT'
    with ZipFile(path) as package:assert package.read('files/actual.txt')==b'native kernel payload'
    with pytest.raises(ExportWriteFailure):files.create(d,b'must not overwrite',record_created=lambda _:None,record_verified=lambda _:None)
    assert path.read_bytes()==raw
