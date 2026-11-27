"""Focused publisher counterexamples; fixtures are not native product evidence."""
import base64
import csv
from io import BytesIO, StringIO
import json
from pathlib import Path
import subprocess
import sys
import tarfile
import zipfile

import pytest

HERE=Path(__file__).resolve().parents[1]/'packaging/focused'
sys.path.insert(0,str(HERE))
import focused_common as common
import focused_dependencies as deps
import build as builder
import start as launcher
import probe as portable


def dump(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(common.canonical(value));return path


def payload(tmp_path,*,selected='linux-x64-py310'):
    root=tmp_path/'payload';root.mkdir();(root/'owned').write_bytes(b'original source')
    dump(root/common.MANIFEST,{'schema_version':'eeas.focused-offline-release.v1','target':selected,
        'source_control':{'kind':'DIRECT_FILES'},'files':common.inventory(root)})
    return root


def wheel(tmp_path,*,name='sample',version='1.0',tag='py3-none-any',requires=(),bad_record=False):
    stem=f'{name}-{version}.dist-info';members={f'{name}/__init__.py':b'VALUE=1\n',
        stem+'/METADATA':('Metadata-Version: 2.4\nName: '+name+'\nVersion: '+version+'\nRequires-Python: >=3.10\n'+
            ''.join('Requires-Dist: '+item+'\n' for item in requires)).encode(),
        stem+'/WHEEL':('Wheel-Version: 1.0\nGenerator: original-fixture\nRoot-Is-Purelib: true\nTag: '+tag+'\n').encode(),
        stem+'/licenses/LICENSE.txt':b'Original fixture license'}
    stream=StringIO();writer=csv.writer(stream)
    for key,raw in members.items():
        hashed=base64.urlsafe_b64encode(bytes.fromhex(common.sha(raw))).decode().rstrip('=')
        writer.writerow([key,'sha256='+('bad' if bad_record and key.endswith('__init__.py') else hashed),str(len(raw))])
    writer.writerow([stem+'/RECORD','','']);members[stem+'/RECORD']=stream.getvalue().encode()
    output=tmp_path/f'{name}-{version}-{tag}.whl'
    with zipfile.ZipFile(output,'w') as archive:
        for key,raw in members.items():archive.writestr(key,raw)
    return output


@pytest.mark.parametrize('name',['../escape','/absolute','a/../b','a//b','a\\b','a:ads','a/./b','', 'a\x00b'])
def test_common_path_counter(name):
    with pytest.raises(common.ReleaseError):common.safe_name(name)


@pytest.mark.parametrize('name',['CON','com1.exe','LPT².txt','a.','a ','x/NUL','a?b'])
def test_windows_aliases(name):
    with pytest.raises(common.ReleaseError):common.safe_name(name,windows=True)


def test_json_duplicate_and_nonfinite(tmp_path):
    path=tmp_path/'manifest';path.write_text('{"same":1,"same":2}')
    with pytest.raises(common.ReleaseError):common.read_json(path)
    path.write_text('{"x":NaN}')
    with pytest.raises(common.ReleaseError):common.read_json(path)


@pytest.mark.parametrize('names',[['../escape'],['a','a'],['x','x/y'],['File','file']])
def test_zip_escape_duplicate_ancestor_case(tmp_path,names):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as output:
        for name in names:output.writestr(name,b'bad')
    with pytest.raises(common.ReleaseError):common.extract_zip(archive,tmp_path/'extract',windows=True)
    assert not (tmp_path/'escape').exists()


def test_zip_symlink(tmp_path):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as output:
        info=zipfile.ZipInfo('link');info.create_system=3;info.external_attr=0o120777<<16;output.writestr(info,'target')
    with pytest.raises(common.ReleaseError):common.extract_zip(archive,tmp_path/'extract',windows=False)


def tar(tmp_path,link):
    archive=tmp_path/'runtime.tar'
    with tarfile.open(archive,'w') as output:
        info=tarfile.TarInfo('python/bin/python3.10');info.size=4;info.mode=0o755;output.addfile(info,BytesIO(b'EXEC'))
        info=tarfile.TarInfo('python/bin/python3');info.type=tarfile.SYMTYPE;info.linkname=link;output.addfile(info)
    return archive


def test_stock_link_materialized_without_os_link(tmp_path):
    archive=tar(tmp_path,'python3.10');output=tmp_path/'runtime'
    common.extract_runtime_tar(archive,output,materialize_internal_links=True)
    assert (output/'bin/python3').read_bytes()==b'EXEC' and not (output/'bin/python3').is_symlink()
    with pytest.raises(common.ReleaseError):common.extract_runtime_tar(archive,tmp_path/'strict')


@pytest.mark.parametrize('link',['../../escape','/etc/passwd','python3'])
def test_runtime_link_escape_or_cycle(tmp_path,link):
    with pytest.raises(common.ReleaseError):common.extract_runtime_tar(tar(tmp_path,link),tmp_path/'runtime',materialize_internal_links=True)


def test_integrity_mutation_unknown_file_modes_and_userdata(tmp_path):
    root=payload(tmp_path);common.verify_payload(root)
    (root/'userdata').mkdir();(root/'userdata/account.private').write_bytes(b'private')
    common.verify_payload(root)
    with pytest.raises(common.ReleaseError):common.verify_payload(root,allow_user_data=False)
    (root/'userdata/account.private').unlink();(root/'userdata').rmdir()
    (root/'owned').write_bytes(b'changed')
    with pytest.raises(common.ReleaseError):common.verify_payload(root)


def test_windows_manifest_case_collision(tmp_path):
    root=payload(tmp_path,selected='windows-x64-py310');value=common.read_json(root/common.MANIFEST)
    (root/'OWNED').write_bytes(b'original source');value['files']=common.inventory(root,exclusions=(common.MANIFEST,))
    dump(root/common.MANIFEST,value)
    with pytest.raises(common.ReleaseError):common.verify_payload(root)


def test_symlink_parent_input_rejected(tmp_path):
    real=tmp_path/'real';real.mkdir();(real/'file').write_bytes(b'x');(tmp_path/'alias').symlink_to(real,target_is_directory=True)
    with pytest.raises(common.ReleaseError):common.checked_file(tmp_path/'alias/file',common.sha(b'x'))


def test_atomic_archive_output_outside_payload(tmp_path):
    root=payload(tmp_path)
    with pytest.raises(common.ReleaseError):builder.archive_release(root,root/'release.zip')
    result=builder.archive_release(root,tmp_path/'release.zip')
    assert result['sha256']==common.file_sha(tmp_path/'release.zip')
    extracted=tmp_path/'extracted';common.extract_zip(tmp_path/'release.zip',extracted,windows=False)
    common.verify_payload(extracted,allow_user_data=False)
    with pytest.raises(common.ReleaseError):builder.archive_release(root,tmp_path/'release.zip')


def test_clean_copy_rejects_prior_accounts_and_existing_destination(tmp_path):
    root=payload(tmp_path);copy=portable.copied_payload(root,tmp_path/'fresh')
    assert common.file_sha(copy/common.MANIFEST)==common.file_sha(root/common.MANIFEST)
    with pytest.raises(common.ReleaseError):portable.copied_payload(root,copy)
    (root/'userdata').mkdir();(root/'userdata/accounts').write_text('private')
    with pytest.raises(common.ReleaseError):portable.copied_payload(root,tmp_path/'blocked')


def test_target_markers_and_abi_real_tags(tmp_path):
    assert deps.environment('windows-x64-py310')['os_name']=='nt'
    assert deps.environment('linux-x64-py310')['python_version']=='3.10'
    w=wheel(tmp_path,tag='cp39-abi3-win_amd64')
    assert deps.wheel_record(w,common.file_sha(w),'windows-x64-py310')
    assert deps.wheel_record(w,common.file_sha(w),'linux-x64-py310') is None


def test_wheel_record_corruption_and_supplyhash_failclosed(tmp_path):
    w=wheel(tmp_path,bad_record=True)
    with pytest.raises(common.ReleaseError):deps.wheel_record(w,common.file_sha(w),'linux-x64-py310')
    with pytest.raises(common.ReleaseError):deps.wheel_record(w,'0'*64,'linux-x64-py310')


def test_closure_conflict_missing_dep_or_pin(tmp_path):
    first=wheel(tmp_path,requires=('other>=1',));r=deps.wheel_record(first,common.file_sha(first),'linux-x64-py310')
    with pytest.raises(common.ReleaseError):deps.merge_closure([r],'linux-x64-py310',required={})
    other=wheel(tmp_path,name='other');o=deps.wheel_record(other,common.file_sha(other),'linux-x64-py310')
    result=deps.merge_closure([r,o],'linux-x64-py310',required={'sample':'1.0'})
    assert result['dependency_edges'][0]['resolved']=='other==1.0'
    with pytest.raises(common.ReleaseError):deps.merge_closure([r,o,{**o,'sha256':'0'*64}],'linux-x64-py310',required={})
    with pytest.raises(common.ReleaseError):deps.merge_closure([r,o],'linux-x64-py310',required={'sample':'9.0'})


def test_manifest_external_wheel_needs_explicit_root(tmp_path):
    supply=tmp_path/'supply';supply.mkdir();external=tmp_path/'external';external.mkdir();w=wheel(external)
    manifest=dump(supply/'SOURCE.json',{'wheels':[{'path':str(w),'sha256':common.file_sha(w)}]})
    spec={'root':str(supply),'manifest':'SOURCE.json','sha256':common.file_sha(manifest)}
    with pytest.raises(common.ReleaseError):deps.manifest_wheels(spec,'linux-x64-py310',[])
    result,_=deps.manifest_wheels(spec,'linux-x64-py310',[external]);assert len(result)==1


def test_target_lock_explicit_legacy_omission_only(tmp_path):
    lock=tmp_path/'lock';lock.write_text('sample==1.0\npywin32==312\nclaude-agent-sdk==0.2.165\n')
    pins,omitted=deps.root_pins(lock,'linux-x64-py310',excluded={'claude-agent-sdk'})
    assert pins=={'sample':'1.0'} and {i['reason'] for i in omitted}=={'WINDOWS_ONLY_PACKAGE','EXPLICIT_FOCUSED_EXCLUSION'}
    assert 'pywin32' in deps.root_pins(lock,'windows-x64-py310',excluded={'claude-agent-sdk'})[0]
    lock.write_text('sample>=1.0')
    with pytest.raises(common.ReleaseError):deps.root_pins(lock,'linux-x64-py310')


def dist(tmp_path,src='./app.js'):
    directory=tmp_path/'apps/agent-shell/dist';directory.mkdir(parents=True)
    (directory/'index.html').write_text('<html><script src="'+src+'"></script></html>');(directory/'app.js').write_text('export {};')
    return directory


@pytest.mark.parametrize('uri',['https://cdn.example/app.js','//cdn.example/app.js','../secret.js','/outside/app.js','missing.js'])
def test_frontend_external_escape_missing_failclosed(tmp_path,uri):
    dist(tmp_path,uri)
    with pytest.raises(common.ReleaseError):builder.check_frontend(tmp_path)


def test_frontend_real_localclosure_and_symlink(tmp_path):
    directory=dist(tmp_path);(directory/'main.css').write_text('div{background:url(./local.woff2)}')
    (directory/'local.woff2').write_bytes(b'local');builder.check_frontend(tmp_path)
    (directory/'app.js').unlink();(tmp_path/'remote').write_bytes(b'outside');(directory/'app.js').symlink_to(tmp_path/'remote')
    with pytest.raises(common.ReleaseError):builder.check_frontend(tmp_path)


def test_source_policy_excludes_runtime_private_history():
    for path in ['packages/.git/config','apps/api/.venv/a','packages/reports/a','apps/api/auth.json','packages/var/data.db',
            'apps/agent-shell/node_modules/a','apps/api/credentials.json','apps/api/account.pyc']:
        assert not builder.admitted_source(path)
    assert builder.admitted_source('packages/domain/discovery.py')


def test_environment_isolated_install_scope_no_credentials_or_proxy(tmp_path):
    inherited={'PATH':'/usr/bin','EEAS_NATIVE_RUNTIME':'claude-agent-sdk','EEAS_DATA_DIR':'/private',
        'OPENAI_API_KEY':'private','OPENCODE_CONFIG_CONTENT':'private','PYTHONPATH':'/private','HTTPS_PROXY':'private'}
    env=launcher.environment(tmp_path,inherited)
    assert env['EEAS_NATIVE_RUNTIME']=='opencode-http' and env['EEAS_DATA_DIR']==str(tmp_path/'userdata/state')
    assert not any(key in env for key in ['OPENAI_API_KEY','OPENCODE_CONFIG_CONTENT','HTTPS_PROXY'])
    assert env['TEMP']==str(tmp_path/'userdata/tmp') and env['PATH']=='/usr/bin'
    assert not (tmp_path/'var').exists()


def test_no_git_npm_or_downloader_invoked_in_publish_code():
    for name in ['build.py','start.py','focused_common.py','focused_dependencies.py']:
        tree=__import__('ast').parse((HERE/name).read_text())
        strings=[node.value for node in __import__('ast').walk(tree) if isinstance(node,__import__('ast').Constant) and isinstance(node.value,str)]
        assert not any(value in {'git','npm','npx','curl','wget','git init','git checkout'} for value in strings)


def test_kernel_denies_real_descendant_outbound_and_localhost():
    if sys.platform!='linux':pytest.skip('Native Linux seccomp only; Windows NOT_RUN')
    result=subprocess.run([sys.executable,str(HERE/'network_guard.py')],capture_output=True,timeout=10)
    if result.returncode:pytest.fail(result.stderr.decode())
    proof=json.loads(result.stdout)
    assert proof['guard']['kind']=='LINUX_KERNEL_DENY_OUTBOUND_NETWORK'
    assert len(proof['negative_counters'])==3 and all(i['errno']==1 for i in proof['negative_counters'])


def test_verify_cli_needs_only_stdlib_under_isolation(tmp_path):
    root=payload(tmp_path)
    # -S suppresses globally installed PyPA packaging; verify must not import it.
    result=subprocess.run([sys.executable,'-I','-S',str(HERE/'build.py'),'verify',str(root)],capture_output=True,timeout=5)
    if result.returncode:pytest.fail(result.stderr.decode())
    assert json.loads(result.stdout)['status']=='PAYLOAD_HASHES_VERIFIED'


def test_source_freeze_wrapper_tamper(tmp_path):
    path=dump(tmp_path/'freeze',{'payload':{'schema_version':'eeas.focused-source-freeze.v1','target':'linux-x64-py310'},'sha256':'0'*64})
    with pytest.raises(common.ReleaseError):builder._read_freeze(path)


def test_extra_dependency_reaches_its_conditional_native_closure(tmp_path):
    w=wheel(tmp_path);base=deps.wheel_record(w,common.file_sha(w),'linux-x64-py310')
    parent={**base,'name':'parent','requires_dist':['sample[crypto]>=1.0']}
    sample={**base,'provides_extra':['crypto'],'requires_dist':['backend>=1.0; extra == "crypto"']}
    with pytest.raises(common.ReleaseError):deps.merge_closure([parent,sample],'linux-x64-py310',required={})
    backend={**base,'name':'backend','requires_dist':[]}
    result=deps.merge_closure([parent,sample,backend],'linux-x64-py310',required={})
    assert result['enabled_extras']=={'sample':['crypto']} and len(result['dependency_edges'])==2


def test_wheel_source_locator_rejects_ambiguous_filename(tmp_path):
    w=wheel(tmp_path);(tmp_path/'wheels').mkdir();(tmp_path/'wheels'/w.name).write_bytes(w.read_bytes())
    m=dump(tmp_path/'SOURCE.json',{'wheels':[{'filename':w.name,'sha256':common.file_sha(w)}]})
    with pytest.raises(common.ReleaseError):deps.manifest_wheels({'root':str(tmp_path),'manifest':m.name,'sha256':common.file_sha(m)},'linux-x64-py310',[])


def test_css_absolute_mount_uses_dist_root_not_css_parent(tmp_path):
    directory=dist(tmp_path);assets=directory/'assets';assets.mkdir();(assets/'font.woff').write_bytes(b'local')
    (assets/'style.css').write_text('div{font: url(/agent-shell/assets/font.woff)}')
    builder.check_frontend(tmp_path)


def test_publisher_code_is_bound_into_frozen_inputs(tmp_path):
    value={'schema_version':'eeas.focused-source-freeze.v1','target':'linux-x64-py310','publisher_files':[
        {'path':'packaging/focused/start.py','sha256':'0'*64,'bytes':1}]}
    path=dump(tmp_path/'freeze',{'payload':value,'sha256':common.sha(common.canonical(value))})
    with pytest.raises(common.ReleaseError):builder._read_freeze(path)


def test_kernel_allows_real_accepted_localhost_reply(tmp_path):
    if sys.platform!='linux':pytest.skip('Native Linux seccomp only; Windows NOT_RUN')
    import socket
    server=socket.socket();server.bind(('127.0.0.1',0));server.listen();port=server.getsockname()[1]
    code='import sys,socket;sys.path.insert(0,sys.argv[1]);from network_guard import deny_outbound_network;deny_outbound_network();s=socket.socket(fileno=int(sys.argv[2]));c,a=s.accept();c.sendall(b"actual-owned-reply");c.close()'
    child=subprocess.Popen([sys.executable,'-c',code,str(HERE),str(server.fileno())],pass_fds=(server.fileno(),),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
    try:
        client=socket.create_connection(('127.0.0.1',port),timeout=3)
        with client:received=client.recv(64)
        stdout,stderr=child.communicate(timeout=5)
        if child.returncode:pytest.fail(stderr.decode())
        assert received==b'actual-owned-reply'
    finally:
        server.close()
        if child.poll() is None:child.kill();child.wait(timeout=5)
