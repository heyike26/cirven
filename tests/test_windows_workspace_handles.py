"""Win32 ABI/policy counters plus executable native-Windows acceptance cases.

Injected handle tests do not qualify a Windows kernel. Native cases are marked
NOT_RUN on POSIX and must be executed on the release Windows device.
"""
import copy
import ctypes
import ntpath
import os
from pathlib import Path

import pytest

from packages.adapters.windows_workspace_handles import (
    BY_HANDLE_FILE_INFORMATION, FILE_ID_BOTH_DIR_INFO, FILE_RENAME_INFO, FILE_SHARE_READ,
    FILE_SHARE_WRITE, FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_ATTRIBUTE_READONLY,
    GENERIC_READ, GENERIC_WRITE, OPEN_ALWAYS, CREATE_NEW, WindowsWorkspaceHandles,
    normalized_root, relative_path, OBJECT_ATTRIBUTES, UNICODE_STRING, IO_STATUS_BLOCK, Win32Kernel,
    validate_file_metadata, file_metadata_sha256,
)
from packages.domain.store import Conflict, InvalidInput, NotFound


def failure(code):
    error = OSError(code, "Original injected Win32 failure"); error.winerror = code; return error


class OriginalHandleCounter:
    """A deterministic Win32 API state counter, never a real-Windows success fixture."""
    def __init__(self):
        self.nodes = {}; self.handles = {}; self.calls = []; self.sequence = 0; self.next_inode = 1
        self.fs = "NTFS"; self.copy_denied = False; self.partial_write = False; self.read_drift = False; self.rename_denied = False; self.restore_failure=False
        for path in ["C:\\", "C:\\selected", "C:\\selected\\nested"]: self.add(path, directory=True)
        self.add("C:\\selected\\original.txt", raw=b"original actual byte counter")
        self.add("C:\\selected\\nested\\source.txt", raw=b"nested byte counter")

    def add(self, path, *, directory=False, raw=b"", attributes=0, final=None, links=1):
        self.nodes[path.casefold()] = {"path": path, "final": final or path, "raw": raw,
            "device": 17, "inode": self.next_inode, "attributes": attributes | (FILE_ATTRIBUTE_DIRECTORY if directory else 0),
            "links": links, "creation": 10, "write": 20, "access": 30, "security": "original-explicit-protected-owner-group-dacl"}
        self.next_inode += 1

    def open(self, path, access, share, disposition=3, *, directory=False, through=False):
        self.calls.append(("open", path, access, share, disposition, directory, through))
        key = path.casefold()
        if disposition == CREATE_NEW and key in self.nodes: raise failure(80)
        if key not in self.nodes:
            if disposition not in {CREATE_NEW, OPEN_ALWAYS}: raise failure(2)
            if ntpath.dirname(path).casefold() not in self.nodes: raise failure(3)
            self.add(path)
        self.sequence += 1; self.handles[self.sequence] = {"node": self.nodes[key], "share": share, "access": access, "offset": 0}
        return self.sequence

    def close(self, handle): self.calls.append(("close", handle)); self.handles.pop(handle)
    def open_relative(self, parent, leaf, access, share, disposition=3, *, directory=False, through=False, reject_reparse=True):
        self.calls.append(("relative", parent, leaf, reject_reparse))
        path = ntpath.join(self.handles[parent]["node"]["path"], leaf)
        if directory is True and disposition == OPEN_ALWAYS and path.casefold() not in self.nodes:
            self.add(path, directory=True)
        return self.open(path, access, share, disposition, directory=directory, through=through)
    def info(self, handle):
        if not self.handles[handle]['access'] & (GENERIC_READ | 0x80):
            raise failure(5)
        node = self.handles[handle]["node"]
        return {key: node[key] for key in ("device", "inode", "attributes", "links", "creation", "access", "write")} | {"size": len(node["raw"])}
    def final_path(self, handle): return self.handles[handle]["node"]["final"]
    def filesystem(self, handle): return self.fs
    def make_directory(self, path):
        self.calls.append(("mkdir", path))
        if path.casefold() not in self.nodes: self.add(path, directory=True)
    def read(self, handle, amount):
        selected = self.handles[handle]; offset = selected["offset"]; raw = selected["node"]["raw"][offset:offset + amount]
        selected["offset"] += len(raw)
        if self.read_drift: selected["node"]["write"] += 1
        return raw
    def write(self, handle, raw):
        if self.partial_write: raw = raw[:max(1, len(raw) // 2)]
        selected = self.handles[handle]; offset = selected['offset']; old = selected['node']['raw']
        selected['node']['raw'] = old[:offset] + raw + old[offset + len(raw):]
        selected['offset'] += len(raw)
        self.calls.append(("write", handle, len(raw))); return len(raw)
    def flush(self, handle): self.calls.append(("flush", handle))
    def seek_start(self, handle): self.handles[handle]['offset'] = 0
    def truncate_here(self, handle):
        selected = self.handles[handle]; selected['node']['raw'] = selected['node']['raw'][:selected['offset']]
    def copy_security(self, source, target):
        if self.copy_denied: raise failure(5)
        self.calls.append(("security", source, target))
        self.handles[target]["node"]["security"] = self.handles[source]["node"]["security"]
    def attributes(self, handle, attributes): self.handles[handle]["node"]["attributes"] = attributes
    def capture_metadata(self,handle,original_info=None):
        node=self.handles[handle]['node'];info=original_info or self.info(handle)
        if node.get('named_stream'):raise InvalidInput('UNIT named stream must retain original file')
        return validate_file_metadata({'schema':'eeas.ntfs-file-metadata.v1','attributes':info['attributes'],
            **{key:str(info[key]) for key in ('creation','access','write')},'security_sddl':node['security']})
    def freeze_access_write_times(self,handle): self.calls.append(('freeze_times',handle))
    def restore_metadata(self,handle,material):
        self.calls.append(('restore_metadata',handle));node=self.handles[handle]['node']
        if self.restore_failure or node.get('restore_denied'):raise failure(5)
        node.update(attributes=material['attributes'],security=material['security_sddl'],
            **{key:int(material[key]) for key in ('creation','access','write')})
    def rename(self, source, parent, leaf):
        if self.rename_denied: raise failure(5)
        self.calls.append(("rename", source, parent, leaf))
        node = self.handles[source]["node"]; self.nodes.pop(node["path"].casefold())
        path = ntpath.join(self.handles[parent]["node"]["path"], leaf)
        node["path"] = node["final"] = path; self.nodes[path.casefold()] = node
    def delete(self, handle):
        self.calls.append(("delete", handle)); self.nodes.pop(self.handles[handle]["node"]["path"].casefold(), None)
    def directory_entries(self, handle):
        path = self.handles[handle]["node"]["path"].casefold()
        return [(ntpath.basename(node["path"]), node["inode"]) for node in list(self.nodes.values())
                if ntpath.dirname(node["path"]).casefold() == path and node["path"].casefold() != path]
    def lock(self, handle): self.calls.append(("lock", handle)); return object()
    def unlock(self, handle, overlap): self.calls.append(("unlock", handle))
    def external_rename(self, path):
        for selected in self.handles.values():
            if selected["node"]["path"].casefold() == path.casefold() and not selected["share"] & 4: raise failure(32)
        raise AssertionError("Original namespace counter was not pinned")

    def external_reparse_change(self, path):
        for selected in self.handles.values():
            if selected["node"]["path"].casefold() == path.casefold():
                if not selected["share"] & FILE_SHARE_WRITE: raise failure(32)
                selected['node']['attributes'] |= FILE_ATTRIBUTE_REPARSE_POINT
                return
        raise AssertionError("Original in-place reparse counter was not pinned")


@pytest.fixture
def selected():
    api = OriginalHandleCounter(); adapter = WindowsWorkspaceHandles(api)
    root, identity = adapter.root_identity("C:/selected")
    return api, adapter, {"root": root, "root_identity": identity}


def test_exact_delete_counter_holds_parent_and_leaf_compares_bytes_and_closes_handles(selected):
    """AUTHORED_NOT_RUN. Deterministic API boundary; not an NTFS kernel claim."""
    api, adapter, binding = selected
    original = copy.deepcopy(api.nodes['c:\\selected\\original.txt'])
    parent = {key: str(api.nodes['c:\\selected'][key]) for key in ('device', 'inode')}
    result = adapter.compare_delete(binding, 'original.txt', expected_raw=original['raw'], expected_parent=parent,
        persist_recovery=lambda material:{'id':'a'*64,'sha256':'b'*64})
    assert result['kind'] == 'WINDOWS_EXCLUSIVE_HANDLE_COMPARE_DELETE'
    assert 'c:\\selected\\original.txt' not in api.nodes and not api.handles
    leaf = next(call for call in api.calls if call[0] == 'open' and call[1].endswith('original.txt'))
    assert leaf[2] & 0x10000 and not leaf[3] & (FILE_SHARE_WRITE | 4)
    assert result['deleted_identity']['inode'] == str(original['inode'])


@pytest.mark.parametrize('fault', ['different_bytes', 'parent_replaced', 'hardlink', 'reparse', 'readonly'])
def test_exact_delete_counter_rejects_drift_before_disposition(selected, fault):
    """AUTHORED_NOT_RUN."""
    api, adapter, binding = selected
    node = api.nodes['c:\\selected\\original.txt']; before = node['raw']
    parent = {key: str(api.nodes['c:\\selected'][key]) for key in ('device', 'inode')}
    if fault == 'different_bytes': node['raw'] = b'USER_MANUAL_CONTENT'
    elif fault == 'parent_replaced': parent['inode'] = str(int(parent['inode'])+1)
    elif fault == 'hardlink': node['links'] = 2
    elif fault == 'reparse': node['attributes'] |= FILE_ATTRIBUTE_REPARSE_POINT
    else: node['attributes'] |= FILE_ATTRIBUTE_READONLY
    with pytest.raises((Conflict, InvalidInput)):
        adapter.compare_delete(binding, 'original.txt', expected_raw=before, expected_parent=parent,
            persist_recovery=lambda material:{'id':'a'*64,'sha256':'b'*64})
    assert 'c:\\selected\\original.txt' in api.nodes
    assert not any(call[0] == 'delete' for call in api.calls) and not api.handles


def test_exclusive_create_counter_checks_parent_and_never_overwrites_existing_leaf(selected):
    """AUTHORED_NOT_RUN."""
    api, adapter, binding = selected
    parent = {key: str(api.nodes['c:\\selected'][key]) for key in ('device', 'inode')}
    original = copy.deepcopy(api.nodes['c:\\selected\\original.txt'])
    with pytest.raises(Conflict):
        adapter.compare_write(binding, 'original.txt', b'MUST_NOT_OVERWRITE', expected_raw=None, expected_parent=parent)
    assert api.nodes['c:\\selected\\original.txt'] == original
    with pytest.raises(Conflict):
        adapter.compare_write(binding, 'absent.txt', b'MUST_NOT_CREATE', expected_raw=None, expected_parent={**parent, 'inode': '987654321'})
    assert 'c:\\selected\\absent.txt' not in api.nodes and not api.handles


def test_metadata_counter_records_before_delete_and_restores_exact_supported_metadata(selected):
    """AUTHORED_NOT_RUN: injected API state, not native Windows qualification."""
    api,adapter,binding=selected;node=api.nodes['c:\\selected\\original.txt']
    node.update(attributes=34,creation=132537600000000000,access=132537600100000000,write=132537600200000000,security='UNIT_EXPLICIT_PROTECTED_OWNER_GROUP_DACL')
    original=copy.deepcopy(node);parent={key:str(api.nodes['c:\\selected'][key]) for key in ('device','inode')};captured=[]
    def persisted(material):
        assert 'c:\\selected\\original.txt' in api.nodes and not any(call[0]=='delete' for call in api.calls)
        captured.append(copy.deepcopy(material));return {'id':'a'*64,'sha256':'b'*64}
    deleted=adapter.compare_delete(binding,'original.txt',expected_raw=original['raw'],expected_parent=parent,persist_recovery=persisted)
    created=adapter.compare_write(binding,'original.txt',original['raw'],expected_raw=None,expected_parent=parent,
        restore_metadata=captured[0]['metadata'],record_created=lambda material:{'id':'c'*64,'sha256':'d'*64})
    restored=api.nodes['c:\\selected\\original.txt']
    assert {key:restored[key] for key in ('attributes','creation','access','write','security','raw')}=={key:original[key] for key in ('attributes','creation','access','write','security','raw')}
    assert restored['inode']!=original['inode'] and created['metadata_restored'] is True
    assert deleted['metadata_recovery_ref']=={'id':'a'*64,'sha256':'b'*64} and not api.handles


@pytest.mark.parametrize('fault',['persist_failed','security_after_capture','named_stream','special_attribute','stale_metadata','missing_recorder'])
def test_metadata_or_recovery_failure_refuses_delete_and_keeps_original(selected,fault):
    """AUTHORED_NOT_RUN."""
    api,adapter,binding=selected;node=api.nodes['c:\\selected\\original.txt'];before=node['raw']
    parent={key:str(api.nodes['c:\\selected'][key]) for key in ('device','inode')}
    def persisted(material):
        if fault=='persist_failed':raise RuntimeError('UNIT recovery storage failed')
        if fault=='security_after_capture':node['security']='UNIT_USER_PERMISSION_CHANGE'
        return {'id':'a'*64,'sha256':'b'*64}
    if fault=='named_stream':node['named_stream']=True
    if fault=='special_attribute':node['attributes']|=0x4000  # EFS must not lose metadata.
    with pytest.raises((Conflict,InvalidInput,RuntimeError)):
        adapter.compare_delete(binding,'original.txt',expected_raw=before,expected_parent=parent,
            expected_metadata_sha256='0'*64 if fault=='stale_metadata' else None,
            persist_recovery=None if fault=='missing_recorder' else persisted)
    assert api.nodes['c:\\selected\\original.txt']['raw']==before
    assert not any(call[0]=='delete' for call in api.calls) and not api.handles


def test_file_metadata_uses_decimal_times_and_last_access_never_changes_drift_digest():
    """AUTHORED_NOT_RUN."""
    value={'schema':'eeas.ntfs-file-metadata.v1','attributes':34,'creation':'132537600000000001',
        'access':'132537600000000002','write':'132537600000000003','security_sddl':'UNIT_DACL'}
    assert file_metadata_sha256(value)==file_metadata_sha256({**value,'access':'132537699999999999'})
    assert file_metadata_sha256(value)!=file_metadata_sha256({**value,'security_sddl':'UNIT_CHANGED_DACL'})
    with pytest.raises(InvalidInput):validate_file_metadata({**value,'creation':132537600000000001})
    with pytest.raises(InvalidInput):validate_file_metadata({**value,'attributes':0x800})


def test_metadata_restore_denial_keeps_new_bytes_and_never_reports_success_or_deletes_a_partial_result(selected):
    """AUTHORED_NOT_RUN."""
    api,adapter,binding=selected;metadata=adapter.metadata_regular(binding,'original.txt')['metadata'];api.restore_failure=True
    parent={key:str(api.nodes['c:\\selected'][key]) for key in ('device','inode')}
    with pytest.raises(Conflict,match='UNKNOWN_OUTCOME'):
        adapter.compare_write(binding,'restored.txt',b'ORIGINAL_RECOVERABLE_BYTES',expected_raw=None,expected_parent=parent,restore_metadata=metadata)
    assert api.nodes['c:\\selected\\restored.txt']['raw']==b'ORIGINAL_RECOVERABLE_BYTES'
    assert not any(call[0]=='delete' for call in api.calls) and not api.handles


def test_win32_structures_use_fixed_windows_widths_on_author_host():
    assert ctypes.sizeof(BY_HANDLE_FILE_INFORMATION) == 52
    assert FILE_ID_BOTH_DIR_INFO.file_id.offset == 96 and FILE_ID_BOTH_DIR_INFO.name.offset == 104
    assert FILE_RENAME_INFO.name.offset == (20 if ctypes.sizeof(ctypes.c_void_p) == 8 else 12)
    assert ctypes.sizeof(UNICODE_STRING) == (16 if ctypes.sizeof(ctypes.c_void_p) == 8 else 8)
    assert ctypes.sizeof(OBJECT_ATTRIBUTES) == (48 if ctypes.sizeof(ctypes.c_void_p) == 8 else 24)
    assert ctypes.sizeof(IO_STATUS_BLOCK) == 2 * ctypes.sizeof(ctypes.c_void_p)


def test_actual_ctypes_relative_call_layout_preserves_unicode_parent_handle_and_no_reparse_flag():
    api = Win32Kernel.__new__(Win32Kernel); captured = {}
    def original_nt_call(result, access, attrs, status, allocation, file_attrs, sharing, disposition, options, ea, ea_size):
        value = ctypes.cast(attrs, ctypes.POINTER(OBJECT_ATTRIBUTES)).contents
        text = value.name.contents
        captured.update({"root": value.root, "name": ctypes.string_at(text.buffer, text.length).decode("utf-16-le"),
            "flags": value.attributes, "access": access, "options": options, "disposition": disposition})
        ctypes.cast(result, ctypes.POINTER(ctypes.c_void_p))[0] = 1234
        return 0
    api.nt_create = original_nt_call
    parent = 0x123456789 if ctypes.sizeof(ctypes.c_void_p) == 8 else 0x12345678
    assert api.open_relative(parent, "原创文件.txt", GENERIC_READ, FILE_SHARE_READ) == 1234
    assert captured["root"] == parent and captured["name"] == "原创文件.txt"
    assert captured["flags"] & 0x1000 and captured["options"] & 0x00200000 and captured["access"] & 0x00100000


def test_native_relative_reparse_error_has_no_absolute_open_fallback():
    api = Win32Kernel.__new__(Win32Kernel)
    api.nt_create = lambda *_: ctypes.c_int32(0xC000050B).value
    api.status_error = lambda _: 4392
    with pytest.raises(OSError) as caught: api.open_relative(123, "original.txt", GENERIC_READ, FILE_SHARE_READ)
    assert caught.value.winerror == 4392


@pytest.mark.parametrize('leaf', ['原创文件.txt', '图😀.md'])
def test_native_rename_uses_pinned_parent_and_exact_utf16_without_absolute_fallback(leaf):
    api=Win32Kernel.__new__(Win32Kernel);captured={}
    parent=0x123456789 if ctypes.sizeof(ctypes.c_void_p)==8 else 0x12345678
    def original_call(handle,status,buffer,size,information_class):
        value=FILE_RENAME_INFO.from_buffer(buffer)
        captured.update(handle=handle,parent=value.root_directory,kind=information_class,
            leaf=ctypes.string_at(ctypes.addressof(buffer)+FILE_RENAME_INFO.name.offset,
                value.name_length).decode('utf-16-le'),replace=value.flags)
        assert size >= ctypes.sizeof(FILE_RENAME_INFO)+value.name_length
        return 0
    api.nt_set_information=original_call
    api.rename(77,parent,leaf)
    assert captured == {'handle':77,'parent':parent,'kind':10,'leaf':leaf,'replace':1}
    api.nt_set_information=lambda *_: ctypes.c_int32(0xC0000043).value
    api.status_error=lambda _:32
    with pytest.raises(OSError) as caught: api.rename(77,parent,leaf)
    assert caught.value.winerror==32


@pytest.mark.parametrize("value", ["C:relative", "\\\\server\\share", "\\\\?\\C:\\selected", "C:\\selected\\..\\outside", "C:\\selected\\.",
    "C:\\selected\\file:stream", "C:\\selected\\NUL.txt", "C:\\selected\\COM¹.log", "C:\\selected\\trailing.", "C:\\selected\\trailing ",
    "C:\\selected\\two\\\\parts", "C:\\selected\\wild*", "C:\\selected\\control\n", "C:\\selected\\\ud800"])
def test_windows_root_rejects_namespace_stream_and_alias_traps(value):
    with pytest.raises(InvalidInput): normalized_root(value)


@pytest.mark.parametrize("value", ["../outside", "/absolute", "C:/outside", "nested\\source.txt", "source.txt:stream", ".git/config", ".eeas-runtime/cache", ".eeas-write-original", "NUL", "file. "])
def test_windows_relative_paths_cannot_expand_selected_scope(value):
    with pytest.raises(InvalidInput): relative_path(value)


def test_counter_reads_frozen_bytes_holds_ancestors_without_delete_sharing_and_closes_every_handle(selected):
    api, adapter, binding = selected
    with adapter.open_parent(binding, "nested/source.txt") as (parent, leaf):
        assert parent.path == "C:\\selected\\nested" and leaf == "source.txt"
        with pytest.raises(OSError): api.external_rename("C:\\selected")
        with pytest.raises(OSError): api.external_rename(parent.path)
    assert adapter.read_regular(binding, "nested/source.txt") == b"nested byte counter"
    assert api.handles == {}
    assert all(not call[3] & 4 for call in api.calls if call[0] == "open")
    assert all(call[3] & FILE_SHARE_WRITE for call in api.calls if call[0] == "open" and call[5])


def test_parent_metadata_change_is_rejected_after_read_instead_of_claiming_write_share_blocks_it(selected):
    api, adapter, binding=selected
    with pytest.raises(InvalidInput,match='Reparse'):
        with adapter.open_parent(binding,'nested/source.txt') as (parent,_):
            api.external_reparse_change(parent.path)
    assert api.handles=={}


@pytest.mark.parametrize("fault", ["root-identity", "root-reparse", "parent-junction", "file-reparse", "final-outside", "hardlink", "read-drift", "filesystem"])
def test_counter_rejects_handle_or_source_drift_before_trusting_bytes(selected, fault):
    api, adapter, binding = selected
    if fault == "root-identity": binding["root_identity"]["inode"] += 100
    elif fault == "root-reparse": api.nodes["c:\\selected"]["attributes"] |= FILE_ATTRIBUTE_REPARSE_POINT
    elif fault == "parent-junction": api.nodes["c:\\selected\\nested"]["attributes"] |= FILE_ATTRIBUTE_REPARSE_POINT
    elif fault == "file-reparse": api.nodes["c:\\selected\\nested\\source.txt"]["attributes"] |= FILE_ATTRIBUTE_REPARSE_POINT
    elif fault == "final-outside": api.nodes["c:\\selected\\nested\\source.txt"]["final"] = "C:\\outside\\secret.txt"
    elif fault == "hardlink": api.nodes["c:\\selected\\nested\\source.txt"]["links"] = 2
    elif fault == "read-drift": api.read_drift = True
    elif fault == "filesystem": api.fs = "FAT32"
    with pytest.raises((Conflict, InvalidInput)): adapter.read_regular(binding, "nested/source.txt")
    assert api.handles == {}


def test_missing_true_only_admits_missing_leaf_and_never_a_missing_parent(selected):
    api, adapter, binding = selected
    assert adapter.read_regular(binding, "missing.txt", missing=True) is None
    with pytest.raises(NotFound): adapter.read_regular(binding, "missing.txt")
    with pytest.raises(NotFound): adapter.read_regular(binding, "absent/missing.txt", missing=True)
    assert api.handles == {}


def test_counter_atomic_write_uses_parent_handle_preserves_security_and_handles_partial_writes(selected):
    api, adapter, binding = selected; api.partial_write = True
    original = copy.deepcopy(api.nodes["c:\\selected\\original.txt"])
    adapter.replace_atomic(binding, "original.txt", b"updated original bytes")
    updated = api.nodes["c:\\selected\\original.txt"]
    assert updated["raw"] == b"updated original bytes" and updated["security"] == original["security"]
    assert updated["inode"] != original["inode"] and not any(".eeas-write-" in path for path in api.nodes)
    calls = [call[0] for call in api.calls]
    assert calls.index("security") < calls.index("rename") and calls.count("flush") == 2
    assert api.handles == {}


@pytest.mark.parametrize("fault", ["readonly", "security-denied", "rename-denied", "symlink-destination", "oversize"])
def test_counter_write_failure_preserves_original_and_cleans_only_owned_temporary(selected, fault):
    api, adapter, binding = selected; before = api.nodes["c:\\selected\\original.txt"]["raw"]
    if fault == "readonly": api.nodes["c:\\selected\\original.txt"]["attributes"] |= FILE_ATTRIBUTE_READONLY
    elif fault == "security-denied": api.copy_denied = True
    elif fault == "rename-denied": api.rename_denied = True
    elif fault == "symlink-destination": api.nodes["c:\\selected\\original.txt"]["attributes"] |= FILE_ATTRIBUTE_REPARSE_POINT
    raw = b"x" * (1024 * 1024 + 1) if fault == "oversize" else b"replacement"
    with pytest.raises(InvalidInput): adapter.replace_atomic(binding, "original.txt", raw)
    assert api.nodes["c:\\selected\\original.txt"]["raw"] == before and not any(".eeas-write-" in path for path in api.nodes)
    assert api.handles == {}


def test_counter_tree_reports_junction_unreadable_and_excludes_private_state(selected):
    api, adapter, binding = selected
    api.add("C:\\selected\\junction", directory=True, attributes=FILE_ATTRIBUTE_REPARSE_POINT)
    api.add("C:\\selected\\.git", directory=True)
    api.add("C:\\selected\\private-controller", directory=True)
    def private_check(_binding, path):
        if path == "private-controller": raise InvalidInput("Original controller subtree")
    rows = adapter.list_directory(binding, private_check=private_check)
    assert not any(row["name"] in {".git", "private-controller"} for row in rows)
    junction = next(row for row in rows if row["name"] == "junction")
    assert junction["type"] == "symlink" and junction["readable"] is False
    assert api.handles == {}


def test_counter_material_lock_is_real_api_lock_unlock_and_cleans_handles(selected):
    api, adapter, _ = selected
    with pytest.raises(RuntimeError):
        with adapter.lock("C:\\controller\\materials\\operation\\lock"):
            assert any(call[0] == "lock" for call in api.calls)
            raise RuntimeError("Original deliberate guarded operation failure")
    calls = [call[0] for call in api.calls]
    assert calls.index("lock") < calls.index("unlock") and api.handles == {}


def test_handle_discovery_never_walks_junction_or_private_controller_subtrees(selected):
    api, adapter, binding = selected
    api.add("C:\\selected\\junction", directory=True, attributes=FILE_ATTRIBUTE_REPARSE_POINT)
    api.add("C:\\selected\\junction\\outside.txt", raw=b"outside counter must not be discovered")
    api.add("C:\\selected\\.eeas-runtime", directory=True)
    api.add("C:\\selected\\.eeas-runtime\\private.txt", raw=b"private counter must not be discovered")
    assert sorted(adapter.walk_files(binding)) == ["nested/source.txt", "original.txt"]
    assert api.handles == {}


def test_confirmed_export_directories_are_created_under_pinned_root_and_never_follow_junction(selected):
    api, adapter, binding = selected
    adapter.create_directories(binding, "eeas-harness/original-proposal/v1-original")
    adapter.replace_atomic(binding, "eeas-harness/original-proposal/v1-original/prompt.txt", b"original confirmed prompt bytes")
    assert adapter.read_regular(binding, "eeas-harness/original-proposal/v1-original/prompt.txt") == b"original confirmed prompt bytes"
    api.add("C:\\selected\\junction", directory=True, attributes=FILE_ATTRIBUTE_REPARSE_POINT)
    with pytest.raises(InvalidInput): adapter.create_directories(binding, "junction/must-not-create")
    assert "c:\\selected\\junction\\must-not-create" not in api.nodes and api.handles == {}


def test_export_directory_creation_rechecks_root_identity_before_any_create(selected):
    api, adapter, binding = selected; binding["root_identity"]["inode"] += 100
    before = copy.deepcopy(api.nodes)
    with pytest.raises(Conflict): adapter.create_directories(binding, "eeas-harness/original")
    assert api.nodes == before and api.handles == {}


@pytest.mark.skipif(os.name != "nt", reason="NOT_RUN: native Windows kernel/NTFS acceptance requires a Windows host")
def test_native_windows_ntfs_read_tree_write_identity_and_lock(tmp_path):
    root = tmp_path / "实际目录 空格"; root.mkdir()
    original = root / "original.txt"; original.write_bytes(b"original-native-windows-bytes")
    adapter = WindowsWorkspaceHandles(); normalized, identity = adapter.root_identity(str(root))
    binding = {"root": normalized, "root_identity": identity}
    assert adapter.read_regular(binding, "original.txt") == b"original-native-windows-bytes"
    assert any(row["name"] == "original.txt" and row["readable"] for row in adapter.list_directory(binding))
    adapter.replace_atomic(binding, "original.txt", b"updated-native-windows-bytes")
    assert original.read_bytes() == adapter.read_regular(binding, "original.txt") == b"updated-native-windows-bytes"
    adapter.create_directories(binding, "eeas-harness/original-proposal/v1-native")
    adapter.replace_atomic(binding, "eeas-harness/original-proposal/v1-native/prompt.txt", b"original-confirmed-native-prompt")
    assert (root / "eeas-harness/original-proposal/v1-native/prompt.txt").read_bytes() == b"original-confirmed-native-prompt"
    with adapter.lock(tmp_path / "controller" / "material" / "lock"):
        assert (tmp_path / "controller" / "material" / "lock").is_file()
    other = tmp_path / "outside"; other.mkdir(); (other / "source.txt").write_bytes(b"outside must not be read")
    junction = root / "junction"
    import subprocess
    created = subprocess.run(["cmd", "/c", "mklink", "/J", str(junction), str(other)], capture_output=True, check=False)
    assert created.returncode == 0, created.stderr.decode(errors="replace")
    try:
        with pytest.raises(InvalidInput): adapter.read_regular(binding, "junction/source.txt")
    finally: os.rmdir(junction)


def _native_lock_child(path, connection):
    connection.send("starting")
    with WindowsWorkspaceHandles().lock(path): connection.send("acquired")
    connection.close()


@pytest.mark.skipif(os.name != 'nt', reason='AUTHORED_NOT_RUN: actual NTFS lifecycle requires Windows')
def test_native_windows_exclusive_create_delete_and_exact_recreation(tmp_path):
    root=tmp_path/'native lifecycle';root.mkdir()
    adapter=WindowsWorkspaceHandles();normalized,identity=adapter.root_identity(str(root))
    binding={'root':normalized,'root_identity':identity};parent={key:str(value) for key,value in identity.items()}
    raw=b'EXACT ORIGINAL USER FILE\r\n'
    adapter.compare_write(binding,'owned.txt',raw,expected_raw=None,expected_parent=parent)
    assert (root/'owned.txt').read_bytes()==raw
    with pytest.raises(Conflict):
        adapter.compare_write(binding,'owned.txt',b'MUST_NOT_OVERWRITE',expected_raw=None,expected_parent=parent)
    with pytest.raises(Conflict):
        adapter.compare_delete(binding,'owned.txt',expected_raw=b'STALE_BYTES',expected_parent=parent,
            persist_recovery=lambda material:{'id':'a'*64,'sha256':'b'*64})
    assert (root/'owned.txt').read_bytes()==raw
    captured=[]
    def persist(material):
        captured.append(material)
        with (tmp_path/'original-recovery.json').open('x',encoding='utf-8') as stream:
            stream.write(__import__('json').dumps(material));stream.flush();os.fsync(stream.fileno())
        return {'id':'a'*64,'sha256':'b'*64}
    deleted=adapter.compare_delete(binding,'owned.txt',expected_raw=raw,expected_parent=parent,persist_recovery=persist)
    assert deleted['kind']=='WINDOWS_EXCLUSIVE_HANDLE_COMPARE_DELETE' and not (root/'owned.txt').exists()
    adapter.compare_write(binding,'owned.txt',raw,expected_raw=None,expected_parent=parent,restore_metadata=captured[0]['metadata'])
    assert (root/'owned.txt').read_bytes()==raw


@pytest.mark.skipif(os.name!='nt',reason='AUTHORED_NOT_RUN: actual NTFS permissions/FILETIME round trip requires Windows')
def test_native_ntfs_hidden_file_owner_group_protected_dacl_and_times_survive_exact_recreation(tmp_path):
    import json
    root=tmp_path/'native metadata';root.mkdir();adapter=WindowsWorkspaceHandles()
    normalized,identity=adapter.root_identity(str(root));binding={'root':normalized,'root_identity':identity}
    parent={key:str(value) for key,value in identity.items()};raw=b'EXACT METADATA RECREATION\r\n'
    adapter.compare_write(binding,'prototype.txt',raw,expected_raw=None,expected_parent=parent)
    desired=adapter.metadata_regular(binding,'prototype.txt')['metadata']
    desired={**desired,'attributes':34,'creation':'132537600000000000','access':'132537600100000000','write':'132537600200000000'}
    if 'D:P' not in desired['security_sddl']:desired['security_sddl']=desired['security_sddl'].replace('D:','D:P',1)
    adapter.compare_write(binding,'owned.txt',raw,expected_raw=None,expected_parent=parent,restore_metadata=desired)
    original=adapter.metadata_regular(binding,'owned.txt');assert original['metadata']==desired
    material=[]
    def persist(value):
        material.append(value)
        with (tmp_path/'metadata-recovery.json').open('x',encoding='utf-8') as stream:
            stream.write(json.dumps(value));stream.flush();os.fsync(stream.fileno())
        return {'id':'a'*64,'sha256':'b'*64}
    adapter.compare_delete(binding,'owned.txt',expected_raw=raw,expected_parent=parent,
        expected_metadata_sha256=original['sha256'],expected_identity=original['identity'],persist_recovery=persist)
    adapter.compare_write(binding,'owned.txt',raw,expected_raw=None,expected_parent=parent,restore_metadata=material[0]['metadata'])
    actual=adapter.metadata_regular(binding,'owned.txt')
    assert actual['metadata']==desired and actual['identity']!=original['identity']
    assert (root/'owned.txt').read_bytes()==raw


@pytest.mark.skipif(os.name != "nt", reason="NOT_RUN: native Windows cross-process LockFileEx requires a Windows host")
def test_native_windows_material_lock_serializes_two_real_processes(tmp_path):
    import multiprocessing
    spawn = multiprocessing.get_context("spawn")
    parent, child = spawn.Pipe(duplex=False)
    adapter = WindowsWorkspaceHandles(); path = tmp_path / "controller" / "material" / "lock"
    process = spawn.Process(target=_native_lock_child, args=(str(path), child))
    try:
        with adapter.lock(path):
            process.start(); child.close()
            assert parent.poll(10) and parent.recv() == "starting"
            assert not parent.poll(.3), "Child acquired the same material lock before its real owner released it"
        assert parent.poll(10) and parent.recv() == "acquired"
        process.join(timeout=10); assert process.exitcode == 0
    finally:
        if process.is_alive(): process.terminate(); process.join(timeout=5)
        parent.close()
