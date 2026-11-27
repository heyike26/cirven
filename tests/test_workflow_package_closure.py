import hashlib
import json

import pytest

from scripts.package_native_companion import add_workflow_plugin_closure, files_below, PackageError


def test_exact_vendor_manifest_closure_survives_production_test_directory_filter(tmp_path):
    root = tmp_path/'toolchain/opencode-plugins/dynamic-workflow'
    member = root/'vendor/library/src/tests/original.ts'
    member.parent.mkdir(parents=True)
    member.write_bytes(b'original reviewed vendor bytes')
    manifest = {'id':'eeas.dynamic-workflow','files':[{'path':member.relative_to(root).as_posix(),
        'bytes':member.stat().st_size,'sha256':hashlib.sha256(member.read_bytes()).hexdigest()}]}
    (root/'manifest.json').write_text(json.dumps(manifest))
    assert member not in files_below(root)
    jobs = {}
    add_workflow_plugin_closure(tmp_path, jobs, lambda path,name: jobs.update({name:path}))
    assert jobs['toolchain/opencode-plugins/dynamic-workflow/vendor/library/src/tests/original.ts'] == member
    member.write_bytes(b'changed')
    with pytest.raises(PackageError):
        add_workflow_plugin_closure(tmp_path, {}, lambda *_: None)


@pytest.mark.parametrize('name', ['../outside.py', 'vendor/A.ts', 'vendor/a.ts'])
def test_unsafe_missing_or_case_aliased_plugin_member_is_rejected(tmp_path, name):
    root=tmp_path/'toolchain/opencode-plugins/dynamic-workflow';root.mkdir(parents=True)
    (root/'manifest.json').write_text(json.dumps({'id':'eeas.dynamic-workflow','files':[
        {'path':name,'bytes':0,'sha256':'a'*64}]}))
    with pytest.raises(PackageError):
        add_workflow_plugin_closure(tmp_path, {}, lambda *_: None)
