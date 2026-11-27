"""Win32 handle/ABI boundary tests; no real Windows or model qualification.

The actual generic WindowsWorkspaceHandles policy runs against its existing
kernel counter. Only kernel calls are injected; the mapped time fields,
handle-relative namespace checks and bounded reads are production code.
"""
import ctypes

import pytest

from packages.adapters.opencode_change_review import MAX_SIDE, _windows_approval_source
from packages.adapters.windows_workspace_handles import FILE_BASIC_INFO, FILE_SHARE_READ, WindowsWorkspaceHandles
from packages.domain.store import Conflict, InvalidInput
from test_windows_workspace_handles import OriginalHandleCounter, failure

EPOCH = 116444736000000000


def check(value, message='Windows change-review version mapping failed'):
    if not value:raise AssertionError(message)


class BasicInfoKernel(OriginalHandleCounter):
    def __init__(self, mutation=None):
        super().__init__();self.mutation=mutation;self.mutated=False
        self.leaf=r'C:\selected\original.txt';self.basic_calls=[];self.read_handles=[]
        for node in self.nodes.values():
            node.update(creation=EPOCH+1000,write=EPOCH+7000,change=EPOCH+9000)

    def get_ex(self, handle, kind, pointer, size):
        check(kind==0 and size==40 and size==ctypes.sizeof(FILE_BASIC_INFO))
        node=self.handles[handle]['node'];self.basic_calls.append(handle)
        if self.mutation=='basic_failure':return False
        target=ctypes.cast(pointer,ctypes.POINTER(FILE_BASIC_INFO)).contents
        target.creation=node['creation'];target.write=node['write'];target.change=node['change'];target.attributes=node['attributes']
        return True

    def error(self, _):raise failure(5)

    def read(self, handle, amount):
        self.read_handles.append(handle)
        raw=super().read(handle,amount)
        if raw and not self.mutated:
            self.mutated=True;node=self.handles[handle]['node']
            if self.mutation in {'change','creation','write'}:node[self.mutation]+=1
            if self.mutation=='growth':node['raw']+=b'changed'
            if self.mutation=='replace':
                old=dict(node);self.add(self.leaf,raw=node['raw']);self.nodes[self.leaf.casefold()].update({k:old[k] for k in ['creation','write','change']})
        return raw


def selected(mutation=None):
    api=BasicInfoKernel(mutation);adapter=WindowsWorkspaceHandles(api)
    root,identity=adapter.root_identity('C:/selected')
    return api,adapter,{'root':root,'root_identity':identity}


def test_actual_handle_reader_maps_change_time_not_python_creation_time():
    api,adapter,binding=selected()
    raw,before,after=_windows_approval_source(adapter,binding,'original.txt',None)
    check(raw==b'original actual byte counter' and before==after)
    check(before['ctime_ns']=='900000' and before['ctime_ns']!='100000')
    check(before['mtime_ns']=='700000' and before['dev']=='17')
    check(len(set(api.read_handles))==1 and set(api.read_handles)<=set(api.basic_calls))
    leaf_opens=[c for c in api.calls if c[0]=='open' and c[1]==api.leaf]
    check(len(leaf_opens)==2 and all(c[3]==FILE_SHARE_READ for c in leaf_opens))
    check(any(c[0]=='relative' and c[2]=='original.txt' and c[3] is True for c in api.calls))
    check(not api.handles and not any(c[0] in {'write','rename','delete'} for c in api.calls))


@pytest.mark.parametrize('mutation',['change','creation','write','growth','replace'])
def test_same_bytes_or_replaced_name_cannot_hide_windows_version_drift(mutation):
    api,adapter,binding=selected(mutation)
    with pytest.raises(Conflict):_windows_approval_source(adapter,binding,'original.txt',None)
    check(not api.handles)


@pytest.mark.parametrize('kind',['basic_failure','too_large','reparse','hardlink'])
def test_metadata_failure_size_or_namespace_rejection_has_no_fallback(kind):
    api,adapter,binding=selected(kind)
    leaf=api.nodes[api.leaf.casefold()]
    if kind=='too_large':leaf['raw']=b'x'*(MAX_SIDE+1)
    if kind=='reparse':leaf['attributes']|=0x400
    if kind=='hardlink':leaf['links']=2
    with pytest.raises((Conflict,InvalidInput)):_windows_approval_source(adapter,binding,'original.txt',None)
    check(not api.handles and not api.read_handles)


def test_absent_new_leaf_is_explicit_and_parents_are_still_held_and_released():
    api,adapter,binding=selected()
    result=_windows_approval_source(adapter,binding,'new-file.py',None)
    check(result==(None,None,None) and not api.handles)
    check(any(c[0]=='relative' and c[2]=='new-file.py' for c in api.calls))
