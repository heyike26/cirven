"""Exact packaged qualification selection; no OS/software qualification."""
import copy
import hashlib
import json
from pathlib import Path

import pytest

from scripts.native_package_bootstrap import PackageError,runtime_environment
from test_native_package import bundle


def test_optional_packaged_qualification_requires_registered_exact_bytes_and_is_relocatable(bundle,tmp_path):
    root,manifest,_=bundle
    name='toolchain/qualifications/notepad-append.json'
    path=root/name;path.parent.mkdir(parents=True,exist_ok=True)
    raw=b'original structural fixture; not a Windows qualification\n';path.write_bytes(raw)
    ref={'path':name,'sha256':hashlib.sha256(raw).hexdigest()}
    manifest['files'].append({**ref,'bytes':len(raw),'startup_critical':True})
    manifest['notepad_append_qualification']=ref
    moved=tmp_path/'移位包';root.rename(moved)
    env=runtime_environment(moved,manifest,inherited={})
    actual=json.loads(Path(env['EEAS_COLLABORATION_RUNTIME_DESCRIPTOR']).read_bytes())
    assert actual['notepad_append_qualification_ref']=={'path':str(moved/name),'sha256':ref['sha256']}
    assert str(root) not in json.dumps(actual)
    (moved/name).write_bytes(b'changed qualification')
    with pytest.raises(PackageError,match='qualification changed'):
        runtime_environment(moved,manifest,inherited={})


@pytest.mark.parametrize('bad',[
    {'path':'../outside.json','sha256':'a'*64},
    {'path':'unregistered.json','sha256':'a'*64},
    {'path':'unregistered.json','sha256':'not-sha'},
    {'path':'unregistered.json','sha256':'a'*64,'enabled':True},
])
def test_environment_boolean_or_unregistered_reference_cannot_enable_native_input(bundle,bad):
    root,manifest,_=bundle;manifest=copy.deepcopy(manifest)
    manifest['notepad_append_qualification']=bad
    with pytest.raises(PackageError):runtime_environment(root,manifest,inherited={'NATIVE_INPUT':'1'})
