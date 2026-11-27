"""Official Windows device-name contract, exercised on local candidate ZIPs."""
import os
from pathlib import Path
import tempfile
import unittest

from scripts import component_upgrade as upgrade
from test_component_upgrade import archive, materials, write_package


class SuperscriptDeviceCandidates(unittest.TestCase):
    def test_superscript_com_lpt_names_and_extensions_cannot_be_imported(self):
        # Microsoft File Naming Conventions explicitly reserve COM/LPT ¹²³,
        # including extensions. These are original bytes, not Windows DLLs.
        for prefix in ('COM', 'LPT'):
            for digit in ('¹', '²', '³'):
                for variant, basename in (
                    ('bare', prefix + digit),
                    ('extension', prefix + digit + '.txt'),
                    ('lower-multi-extension', prefix.lower() + digit + '.tar.gz'),
                ):
                    with self.subTest(name=basename), tempfile.TemporaryDirectory() as temp:
                        root = Path(temp)
                        old, old_files = materials()
                        active = write_package(root / 'active', old, old_files)
                        before = active.read_bytes()
                        manifest, files = materials('1.1.0')
                        name = 'packages/' + basename
                        files[name] = b'Original device-name import fixture; no native claim.\n'
                        manifest['files'][name] = upgrade.digest(files[name])
                        manifest['platforms']['linux-x64']['required_files'].append(name)
                        zip_path = root / 'candidate.zip'
                        sha = archive(zip_path, manifest, files)
                        if os.environ.get('EEAS_EVIDENCE_DIR'):
                            evidence = Path(os.environ['EEAS_EVIDENCE_DIR']) / 'device-inputs'
                            evidence.mkdir(exist_ok=True)
                            stem = prefix + str(('¹', '²', '³').index(digit) + 1) + '-' + variant
                            (evidence / (stem + '.zip')).write_bytes(zip_path.read_bytes())
                            upgrade.save_new(evidence / (stem + '.json'), {
                                'schema': 'eeas.original-device-path-input.v1',
                                'source_kind': 'synthetic', 'path': name,
                                'archive_sha256': sha,
                                'active_manifest_sha256': upgrade.digest(before),
                                'candidate_manifest': manifest,
                                'qualification': 'LEXICAL_IMPORT_CONTRACT_ONLY',
                                'windows_native': 'NOT_RUN',
                            })
                        with self.assertRaisesRegex(upgrade.Rejected, 'WINDOWS_DEVICE_PATH'):
                            upgrade.import_candidate(active, zip_path, sha, root / 'registry')
                        self.assertEqual(active.read_bytes(), before)
                        self.assertFalse(any((root / 'registry').glob('candidates/*')))


if __name__ == '__main__':
    unittest.main()
