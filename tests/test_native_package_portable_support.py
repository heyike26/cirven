"""Real tiny portable folders and exact manifest selection; no Windows/runtime claim."""
import hashlib,json
import pytest
from scripts import package_native_companion as builder
from scripts.native_package_bootstrap import PackageError,verify_package
from test_native_package_ide_capability import scene,write
NAME='ripgrep-15.1.0-x86_64-pc-windows-msvc.zip'
CANONICAL='toolchain/opencode-support/'+NAME
LEGACY='runtime/opencode-support/'+NAME

def check(value):
    if not value:raise AssertionError('Portable support archive contract failed')

def portable(scene,tmp_path):
    source,runtime,structural=scene
    plan=builder.make_refresh_plan(source,runtime)
    output=tmp_path/'real-portable'
    manifest=builder.copy_plan({**structural,'jobs':{**structural['jobs'],**plan['jobs']}},output)
    check(verify_package(output)==manifest and (output/CANONICAL).is_file()and not(output/LEGACY).exists())
    return source,runtime,output,manifest

def register(root,name):
    p=root/name;mp=root/'PACKAGE_MANIFEST.json';value=json.loads(mp.read_bytes())
    value['files'].append({'path':name,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest(),'startup_critical':False})
    mp.write_text(json.dumps(value),encoding='utf-8')

def test_portable_canonical_only_plans_and_refreshes_with_runtime_history_private_bytes_preserved(scene,tmp_path):
    source,_,root,old=portable(scene,tmp_path)
    private=write(root/'user-data/private-draft.txt',b'own handwritten draft')
    original_runtime={r['path']:dict(r)for r in old['files']if r['path'].startswith('runtime/')}
    original_zip=(root/CANONICAL).read_bytes();prior=(root/'PACKAGE_MANIFEST.json').read_bytes()
    plan=builder.make_refresh_plan(source,root)
    check(plan['jobs'][CANONICAL]==root/CANONICAL and LEGACY not in plan['jobs'])
    write(source/'apps/api/original.txt',b'new approved source')
    refreshed=builder.refresh_source(builder.make_refresh_plan(source,root),root)
    check(verify_package(root)==refreshed)
    check({r['path']:r for r in refreshed['files']if r['path'].startswith('runtime/')}==original_runtime)
    check((root/CANONICAL).read_bytes()==original_zip and not(root/LEGACY).exists())
    check(private.read_bytes()==b'own handwritten draft')
    check((root/refreshed['source_refresh']['history_relative_path']/'PACKAGE_MANIFEST.before.json').read_bytes()==prior)

@pytest.mark.parametrize('fault',['missing','bytes','manifest_bytes','manifest_hash','manifest_alias','file_alias','file_link','ancestor_link','hardlink','legacy_only'])
def test_portable_missing_tampered_aliased_or_linked_archive_fails_without_mutation(scene,tmp_path,fault):
    source,_,root,_=portable(scene,tmp_path);path=root/CANONICAL;mp=root/'PACKAGE_MANIFEST.json'
    if fault=='missing':path.unlink()
    elif fault=='bytes':path.write_bytes(b'X'*path.stat().st_size)
    elif fault in{'manifest_bytes','manifest_hash','manifest_alias'}:
        value=json.loads(mp.read_bytes());row=next(r for r in value['files']if r['path']==CANONICAL)
        if fault=='manifest_bytes':row['bytes']+=1
        elif fault=='manifest_hash':row['sha256']='0'*64
        else:value['files'].append({**row,'path':CANONICAL.upper()})
        mp.write_text(json.dumps(value),encoding='utf-8')
    elif fault=='file_alias':
        # D may be case insensitive: writing the uppercase spelling would
        # merely reopen the same file. Force a real stored-name case alias.
        temporary=path.with_name('case-transition.tmp');path.rename(temporary)
        temporary.rename(path.with_name(NAME.upper()))
    elif fault=='file_link':
        other=write(tmp_path/'outside.zip',path.read_bytes());path.unlink();path.symlink_to(other)
    elif fault=='ancestor_link':
        moved=tmp_path/'outside-support';path.parent.rename(moved);path.parent.symlink_to(moved,target_is_directory=True)
    elif fault=='hardlink':(tmp_path/'archive-alias.zip').hardlink_to(path)
    elif fault=='legacy_only':
        write(root/LEGACY,path.read_bytes());path.unlink()
    before=mp.read_bytes()
    with pytest.raises(PackageError):builder.make_refresh_plan(source,root)
    check(mp.read_bytes()==before and not(root/'package-history').exists())

@pytest.mark.parametrize('registered,same',[(False,True),(False,False),(True,False)])
def test_portable_legacy_copy_cannot_bypass_manifest_or_conflict_with_original(scene,tmp_path,registered,same):
    source,_,root,_=portable(scene,tmp_path)
    write(root/LEGACY,(root/CANONICAL).read_bytes()if same else b'conflicting second original')
    if registered:register(root,LEGACY)
    with pytest.raises(PackageError):builder.make_refresh_plan(source,root)
    check(not(root/'package-history').exists())

def test_identical_manifest_bound_legacy_duplicate_is_preserved_not_removed_from_runtime(scene,tmp_path):
    source,_,root,_=portable(scene,tmp_path);write(root/LEGACY,(root/CANONICAL).read_bytes());register(root,LEGACY)
    old=verify_package(root);original={r['path']:r for r in old['files']if r['path'].startswith('runtime/')}
    plan=builder.make_refresh_plan(source,root)
    check(plan['jobs'][CANONICAL]==root/CANONICAL and plan['jobs'][LEGACY]==root/LEGACY)
    refreshed=builder.refresh_source(plan,root);check(verify_package(root)==refreshed)
    check({r['path']:r for r in refreshed['files']if r['path'].startswith('runtime/')}==original)

def test_raw_legacy_layout_remains_explicit_and_conflicting_second_archive_refuses(scene):
    source,runtime,_=scene
    check(not(runtime/'PACKAGE_MANIFEST.json').exists())
    check(builder.make_refresh_plan(source,runtime)['jobs'][CANONICAL]==runtime/LEGACY)
    write(runtime/CANONICAL,(runtime/LEGACY).read_bytes())
    check(builder.make_refresh_plan(source,runtime)['jobs'][CANONICAL]==runtime/LEGACY)
    (runtime/CANONICAL).write_bytes(b'changed duplicate')
    with pytest.raises(PackageError):builder.make_refresh_plan(source,runtime)
