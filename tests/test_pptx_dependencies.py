"""Offline closure and hostile install-kit fixtures; Windows checks are static only."""
from email.parser import BytesParser
import hashlib
import json
from pathlib import Path
import re
import tempfile
import unittest
import zipfile

from scripts.install_pptx_dependencies import verify_bundle

ROOT=Path(__file__).resolve().parents[1]


def tiny_bundle(root, *, target='linux-py310', extra=(), tags='py3-none-any'):
    folder=Path(root);(folder/'wheels').mkdir();(folder/'licenses').mkdir()
    name='fixture-1.0-py3-none-any.whl';wheel=folder/'wheels'/name
    source={'fixture-1.0.dist-info/WHEEL':f'Wheel-Version: 1.0\nTag: {tags}\n'.encode(),
            'fixture-1.0.dist-info/METADATA':b'Name: fixture\nVersion: 1.0\nRequires-Python: >=3.10\n',
            'fixture-1.0.dist-info/LICENSE':b'Original fixture license\n'}
    with zipfile.ZipFile(wheel,'w',compression=zipfile.ZIP_DEFLATED) as archive:
        for n,b in [*source.items(),*extra]:archive.writestr(n,b)
    records=[]
    for member in ['fixture-1.0.dist-info/LICENSE','fixture-1.0.dist-info/METADATA']:
        raw=source[member];path='licenses/'+Path(member).name;(folder/path).write_bytes(raw)
        records.append({'path':path,'wheel_member':member,'sha256':hashlib.sha256(raw).hexdigest(),'byte_length':len(raw)})
    raw=wheel.read_bytes();sha=hashlib.sha256(raw).hexdigest()
    lock=f'fixture==1.0 --hash=sha256:{sha}\n';(folder/'requirements.lock').write_text(lock)
    value={'schema':'eeas.office-pptx-dependencies.v1','python':'3.10','target':target,
           'requirements_sha256':hashlib.sha256(lock.encode()).hexdigest(),
           'packages':[{'name':'fixture','version':'1.0','wheel':{'filename':name,'sha256':sha,'byte_length':len(raw)},
                        'licenses':[records[0]],'wheel_metadata':records[1]}]}
    (folder/'manifest.json').write_text(json.dumps(value));return value


class OfflinePptxClosure(unittest.TestCase):
    def test_actual_linux_windows_artifacts_original_licenses_and_metadata_closure(self):
        from pip._vendor.packaging.requirements import Requirement
        from pip._vendor.packaging.specifiers import SpecifierSet
        from pip._vendor.packaging.markers import default_environment
        for target in ('linux-py310','windows-py310'):
            with self.subTest(target=target):
                folder=ROOT/'vendor/office-pptx'/target/'v018';value=verify_bundle(folder,target)
                self.assertEqual(len(value['packages']),9)
                versions={re.sub('[-_.]+','-',p['name']).lower():p['version'] for p in value['packages']}
                self.assertFalse({'magika','torch','numpy','onnxruntime','markitdown'} & set(versions))
                env=default_environment();env.update({'python_version':'3.10','python_full_version':'3.10.22',
                    'sys_platform':'win32' if target.startswith('windows') else 'linux',
                    'platform_system':'Windows' if target.startswith('windows') else 'Linux',
                    'os_name':'nt' if target.startswith('windows') else 'posix',
                    'platform_machine':'AMD64' if target.startswith('windows') else 'x86_64','extra':''})
                for package in value['packages']:
                    metadata=BytesParser().parsebytes((folder/package['wheel_metadata']['path']).read_bytes())
                    self.assertIn('3.10.22',SpecifierSet(metadata.get('Requires-Python','')))
                    self.assertTrue(package['licenses'])
                    for raw in metadata.get_all('Requires-Dist',[]):
                        requirement=Requirement(raw)
                        if requirement.marker and not requirement.marker.evaluate(env):continue
                        name=re.sub('[-_.]+','-',requirement.name).lower()
                        self.assertIn(name,versions,raw)
                        self.assertIn(versions[name],requirement.specifier,raw)

    def test_direct_url_and_index_lock_cannot_gain_install_qualification(self):
        for suffix in ('\n--index-url https://invalid.example\n','\nfixture @ https://invalid.example/a.whl\n'):
            with tempfile.TemporaryDirectory() as temp:
                value=tiny_bundle(temp);folder=Path(temp);lock=(folder/'requirements.lock').read_bytes()+suffix.encode()
                (folder/'requirements.lock').write_bytes(lock)
                value['requirements_sha256']=hashlib.sha256(lock).hexdigest();(folder/'manifest.json').write_text(json.dumps(value))
                with self.assertRaisesRegex(ValueError,'closed-pins'):verify_bundle(folder,'linux-py310')

    def test_hash_license_and_wrong_target_each_block_install(self):
        for attack in ('wheel','license','target'):
            with self.subTest(attack=attack),tempfile.TemporaryDirectory() as temp:
                value=tiny_bundle(temp);folder=Path(temp)
                if attack=='wheel':(folder/'wheels'/value['packages'][0]['wheel']['filename']).write_bytes(b'tamper')
                if attack=='license':(folder/'licenses/LICENSE').write_bytes(b'changed')
                with self.assertRaises(ValueError):verify_bundle(folder,'windows-py310' if attack=='target' else 'linux-py310')

    def test_windows_cannot_accept_actual_linux_native_wheel(self):
        with tempfile.TemporaryDirectory() as temp:
            tiny_bundle(temp,target='windows-py310',tags='cp310-cp310-manylinux_2_17_x86_64')
            with self.assertRaisesRegex(ValueError,'platform'):verify_bundle(Path(temp),'windows-py310')

    def test_py2_only_wheel_does_not_qualify_for_python310(self):
        with tempfile.TemporaryDirectory() as temp:
            tiny_bundle(temp,tags='py2-none-any')
            with self.assertRaisesRegex(ValueError,'platform'):verify_bundle(Path(temp),'linux-py310')

    def test_unsafe_archive_members_fail_before_pip(self):
        attacks=[('../outside.py',b'x'),('CON.py',b'x'),('fixture./bad.py',b'x'),
                 ('fixture/a.py',b'a'),('fixture/large',b'x'*(2*1024*1024))]
        for index,item in enumerate(attacks):
            extra=[item]
            if index==3:extra.append(('FIXTURE/b.py',b'b'))
            with self.subTest(attack=index),tempfile.TemporaryDirectory() as temp:
                tiny_bundle(temp,extra=extra)
                with self.assertRaises(ValueError):verify_bundle(Path(temp),'linux-py310')

    def test_symlink_wheel_and_license_parent_are_rejected(self):
        for attack in ('wheel','license'):
            with self.subTest(attack=attack),tempfile.TemporaryDirectory() as temp:
                folder=Path(temp);value=tiny_bundle(temp)
                path=folder/'wheels'/value['packages'][0]['wheel']['filename'] if attack=='wheel' else folder/'licenses/LICENSE'
                original=folder/'original';path.rename(original);path.symlink_to(original)
                with self.assertRaisesRegex(ValueError,'[Ss]ymlink|escapes'):verify_bundle(folder,'linux-py310')


if __name__ == '__main__':unittest.main(verbosity=2)
