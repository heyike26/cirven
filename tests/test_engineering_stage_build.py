"""Real regular-file capture plus a controlled engine process for OCI adapter tests."""
from copy import deepcopy
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import subprocess
import sys
from zipfile import ZipFile

import pytest

from packages.dynamic_workflow import stage_suite as suite
from packages.dynamic_workflow.stage_assertions import action_digest
from packages.dynamic_workflow.stage_build_output import regular_bytes
from packages.dynamic_workflow.stage_contracts import validate_artifact
from packages.dynamic_workflow.stage_workspace import file_write, file_write_bytes, operation_receipts, read_evidence_file
from test_engineering_stage_contracts import APPROVED, scenario

IMAGE = 'sha256:'+'4'*64


@pytest.fixture
def builder(tmp_path,monkeypatch):
    exe=tmp_path/'podman'
    exe.write_text('controlled entry')
    exe.chmod(0o700)
    driver=tmp_path/'controlled.py'
    driver.write_text("""
import csv,importlib.util,json,sys
from pathlib import Path
cmd=sys.argv[1:]
if cmd[0]=='image':print(json.dumps([{'Id':cmd[-1],'Os':'linux','Config':{}}]))
elif cmd[0]=='run':
    assert '--network=none' in cmd and '--user=65534:65534' in cmd and '--http-proxy=false' in cmd
    mounts={}
    for i,key in enumerate(cmd):
        if key!='--mount':continue
        fields=next(csv.reader([cmd[i+1]]))
        assert 'readonly' in fields
        source=next(v[7:] for v in fields if v.startswith('source='))
        target=next(v[7:] for v in fields if v.startswith('target='))
        mounts[target]=Path(source)
    assert set(mounts)=={'/input','/cirven/collector.py','/cirven/request.json'}
    spec=importlib.util.spec_from_file_location('collector', mounts['/cirven/collector.py'])
    collector=importlib.util.module_from_spec(spec);spec.loader.exec_module(collector)
    cfg=json.loads(mounts['/cirven/request.json'].read_text())
    assert cfg['argv'][:3]==[sys.executable,'-B','build.py']  # test-owned command only
    try:collector.collect_build(cfg,mounts['/input'],Path.cwd()/'project')
    except (ValueError,OSError) as exc:
        collector.publish({'exit_code':None,'limit':'collection_error','detail':type(exc).__name__,
                           'output_truncated':False,'files':[]},[])
        sys.exit(1)
""")
    original=subprocess.Popen
    def substitute(argv,**kw):
        assert argv[0]==str(exe) and kw['shell'] is False
        return original([sys.executable,'-B',str(driver),*argv[1:]],**kw)
    monkeypatch.setattr(suite.subprocess,'Popen',substitute)
    monkeypatch.setenv('CIRVEN_WORKFLOW_CONTAINER_EXE',str(exe))
    monkeypatch.setenv('CIRVEN_WORKFLOW_CONTAINER_IMAGE',IMAGE)
    build={"argv":[sys.executable,"-B","build.py"],"image_id":IMAGE,"timeout_seconds":10,
           "outputs":["dist/firmware.bin","dist/test.zip"]}
    ctx={"_data_dir":tmp_path/'data',"capabilities":{"tools":["build_artifacts"]},"project_id":"p",
         "run_id":"r","stage_session_id":"stage","operation_id":"op",
         "approved_by_kind":{"design":{"items":[{"id":"DS-1","build":build}]}}}
    return ctx,build


def test_declared_outputs_are_real_binary_with_retained_prior_version(builder):
    ctx,build=builder
    file_write(ctx,'build.py',"""from pathlib import Path
from zipfile import ZipFile
p=Path('dist');p.mkdir(exist_ok=True)
assert not (p/'firmware.bin').exists()
data=bytes(range(256))
(p/'firmware.bin').write_bytes(data)
with ZipFile(p/'test.zip','w') as z:z.writestr('firmware.bin',data)
""",'')
    old=file_write_bytes(ctx,'dist/firmware.bin',b'old-bytes','')
    result=suite.build_artifacts(ctx,'DS-1')
    receipt=result['build']
    assert receipt['status']=='passed' and receipt['exit_code']==0
    assert receipt['descriptor_sha256']==action_digest(build)
    assert receipt['source_files']==[['build.py',operation_receipts(ctx)[0]['sha256']]]
    assert [r['path'] for r in receipt['outputs']]==build['outputs']
    assert receipt['file_refs']==[r['id'] for r in result['files']]
    assert read_evidence_file(ctx['_data_dir'],old)[1]==b'old-bytes'
    raw=read_evidence_file(ctx['_data_dir'],result['files'][0])[1]
    archive=read_evidence_file(ctx['_data_dir'],result['files'][1])[1]
    assert raw==bytes(range(256)) and receipt['outputs'][0]['sha256']==sha256(raw).hexdigest()
    with ZipFile(io.BytesIO(archive)) as z:assert z.read('firmware.bin')==raw


def test_no_partial_publish_missing_symlinked_and_stale_outputs(builder):
    ctx,build=builder
    ctx['approved_by_kind']['design']['items'][0]['build']=build
    script=file_write(ctx,'build.py',"""from pathlib import Path
p=Path('dist');p.mkdir(exist_ok=True)
(p/'firmware.bin').write_bytes(b'first')
(p/'test.zip').symlink_to('../build.py')
""",'')
    old=file_write_bytes(ctx,'dist/test.zip',b'old','')
    result=suite.build_artifacts(ctx,'DS-1')
    assert result['build']['status']=='failed' and result['build']['exit_code']==0 and result['files']==[]
    assert len([r for r in operation_receipts(ctx) if r['kind']=='file'])==2
    assert read_evidence_file(ctx['_data_dir'],old)[1]==b'old'
    file_write(ctx,'build.py','print("no files built")',script['sha256'])
    assert suite.build_artifacts(ctx,'DS-1')['build']['status']=='failed'


def test_nofollow_regular_file_read_rejects_parent_links_fifo_and_hard_links(tmp_path):
    root=tmp_path/'tree'
    root.mkdir()
    outside=tmp_path/'secret';outside.mkdir()
    (outside/'firmware.bin').write_bytes(b'never read')
    (root/'alias').symlink_to(outside,target_is_directory=True)
    os.mkfifo(root/'pipe')
    (root/'a').write_bytes(b'a')
    os.link(root/'a',root/'hard')
    fd=os.open(root,os.O_RDONLY|os.O_DIRECTORY)
    try:
        for name in ('alias/firmware.bin','../secret/firmware.bin','pipe','hard','a'):
            with pytest.raises((ValueError,OSError)):regular_bytes(fd,name,100)
    finally:os.close(fd)


def test_build_descriptor_and_cas_validation(builder):
    ctx,build=builder
    design=deepcopy(APPROVED['design'])
    design['items'][0].update(build=build,behavior_scenarios=[
        scenario('S-1','OR-1','observe'),scenario('S-2','OR-2','observe')])
    assert not validate_artifact('design',design,APPROVED)
    for outputs in (['../x'],['out','OUT'],['src','src/x'],['.private/file'],[]):
        design['items'][0]['build']={**build,'outputs':outputs}
        assert validate_artifact('design',design,APPROVED)
    with pytest.raises(ValueError):suite.build_artifacts({**ctx,'capabilities':{'tools':[]}},'DS-1')
    with pytest.raises(ValueError):suite.build_artifacts(ctx,'other')


def test_concurrent_second_output_change_prevents_entire_binary_batch(builder,monkeypatch):
    ctx,_=builder
    file_write(ctx,'build.py',"""from pathlib import Path
p=Path('dist');p.mkdir()
(p/'firmware.bin').write_bytes(b'new-firmware')
(p/'test.zip').write_bytes(b'new-second')
""",'')
    old=file_write_bytes(ctx,'dist/test.zip',b'original','')
    original=suite._unpack_build
    def after_build(*args,**kwargs):
        result=original(*args,**kwargs)
        file_write_bytes({**ctx,'operation_id':'concurrent'},'dist/test.zip',b'manual-save',old['sha256'])
        return result
    monkeypatch.setattr(suite,'_unpack_build',after_build)
    result=suite.build_artifacts(ctx,'DS-1')
    assert result['build']['status']=='failed' and result['build']['limit']=='output_conflict'
    assert not result['files'] and 'dist/firmware.bin' not in {r.get('path') for r in operation_receipts(ctx)}
    latest=operation_receipts({**ctx,'operation_id':'concurrent'})[0]
    assert read_evidence_file(ctx['_data_dir'],latest)[1]==b'manual-save'


def test_transit_hash_mismatch_and_trailing_bytes_refused():
    from packages.dynamic_workflow.stage_build_output import MAGIC
    raw=b'firmware bytes'
    header=json.dumps({'files':[{'path':'app.bin','size':len(raw),'sha256':sha256(raw).hexdigest()}],
                       'detail':'','output_truncated':False,'limit':None,'exit_code':0}).encode()
    prefix=MAGIC+len(header).to_bytes(4,'big')+header
    for contents in (prefix+b'mismatching!!!',prefix+raw+b'other',prefix+raw[:-1]):
        with pytest.raises(ValueError):suite._unpack_build(contents,0,None,['app.bin'])
    with pytest.raises(ValueError):suite._unpack_build(prefix+raw,0,None,['other.bin'])
