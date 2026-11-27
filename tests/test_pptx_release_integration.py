"""Small default-builder control fixture and actual office artifact selection; no large build."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from unittest.mock import patch
import zipfile

import pytest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
from build_release_bundle import build, office_materials
from bundle_support import validate_bundle
from install_pptx_dependencies import selected_bundle,verify_bundle
from test_release_bundle import minimal_repository
from test_pptx_dependencies import tiny_bundle


def add_office(root):
    file=root/'packages/domain/pptx_import.py';file.parent.mkdir(parents=True,exist_ok=True);file.write_text('# synthetic office source fixture\n')
    source=root/'packages/adapters/office_vendor/markitdown_v018/SOURCE.json';source.parent.mkdir(parents=True);source.write_text('{"source_kind":"synthetic supply fixture, not upstream converter"}\n')
    selection={'schema':'eeas.office-pptx-selection.v1','component_source':source.relative_to(root).as_posix(),
        'component_source_sha256':hashlib.sha256(source.read_bytes()).hexdigest(),'targets':{}}
    for target in ('windows-py310','linux-py310'):
        relative='vendor/office-pptx/'+target+'/fixture';folder=root/relative;folder.mkdir(parents=True)
        manifest=tiny_bundle(folder,target=target)
        selection['targets'][target]={'root':relative,'manifest_sha256':hashlib.sha256((folder/'manifest.json').read_bytes()).hexdigest(),
            'requirements_sha256':manifest['requirements_sha256'],'wheels':1,'wheel_bytes':manifest['packages'][0]['wheel']['byte_length']}
    (root/'vendor/office-pptx/selection.json').write_text(json.dumps(selection))
    return selection


def test_default_builder_carries_office_whitelist_licenses_and_combination_without_sdk_pin_change(tmp_path):
    root,wheels,evidence=minimal_repository(tmp_path);selection=add_office(root)
    subprocess.run(['git','add','packages/domain/pptx_import.py','packages/adapters/office_vendor/markitdown_v018/SOURCE.json'],cwd=root,check=True)
    result=build(root,'windows-py310',wheels,[evidence],tmp_path/'small-office.zip')
    manifest=validate_bundle(Path(result['path']),result['sha256'])
    assert result['office_wheels']==2 and result['wheel_count']==1
    assert 'packages/domain/pptx_import.py' in manifest['files']
    assert 'packages/adapters/office_vendor/markitdown_v018/SOURCE.json' in manifest['files']
    with zipfile.ZipFile(result['path']) as archive:
        lock=json.loads(archive.read('release/combination-lock.json'))
        assert lock['runtime_versions']=={'original':'1.0'}
        assert lock['office_component']['versions']['windows-py310']=={'fixture':'1.0'}
        assert len(lock['office_component']['manifests'])==2
        assert 'vendor/office-pptx/selection.json' in lock['dependency_locks_sha256']
        for target,choice in selection['targets'].items():
            rootpath=choice['root'];assert rootpath+'/manifest.json' in manifest['files']
            assert rootpath+'/wheels/fixture-1.0-py3-none-any.whl' in manifest['files']
            assert 'release/licenses/office/'+target+'/packages.json' in manifest['files']
        assert json.loads(archive.read('profiles/offline-dependencies.json'))['targets']=={'windows-py310':'vendor/windows-py310/original'}


def test_actual_office_selection_whitelists_all_18_wheels_and_rejects_version_drift():
    paths,meta,_=office_materials(ROOT,'windows-py310',{'pillow':'12.3.0','typing-extensions':'4.16.0'})
    assert len([p for p in paths if p.endswith('.whl')])==18
    assert set(meta['versions'])=={'linux-py310','windows-py310'}
    assert all(p.exists() and not p.is_symlink() for p in paths.values())
    with pytest.raises(ValueError,match='version conflict'):
        office_materials(ROOT,'windows-py310',{'pillow':'0.0'})


def test_selection_manifest_tamper_and_unlisted_wheel_fail_before_install(tmp_path):
    selection=add_office(tmp_path);choice=selection['targets']['windows-py310'];folder=tmp_path/choice['root']
    (folder/'wheels/unlisted.whl').write_bytes(b'never installed')
    with pytest.raises(ValueError,match='Unlisted'):
        selected_bundle(tmp_path,'windows-py310')
    (folder/'wheels/unlisted.whl').unlink()
    value=json.loads((folder/'manifest.json').read_text());value['scope']='tampered after selection'
    (folder/'manifest.json').write_text(json.dumps(value))
    with pytest.raises(ValueError,match='selected manifest integrity'):
        selected_bundle(tmp_path,'windows-py310')


def test_runtime_office_conflict_fails_before_creating_any_environment(tmp_path):
    import install_offline
    office=ROOT/'vendor/office-pptx/linux-py310/v018';destination=tmp_path/'must-remain-absent'
    with patch.object(install_offline,'verify',return_value={'runtime':{'pillow':'0.0'}}), \
         patch.object(install_offline.subprocess,'run',side_effect=AssertionError('no pip subprocess before compatibility guard')):
        with pytest.raises(ValueError,match='conflict before environment creation'):
            install_offline.install(tmp_path,destination,office_directory=office)
    assert not destination.exists()
