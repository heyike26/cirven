"""Portable archive entry guards; lexical Windows contract, not native Windows."""
from pathlib import Path
import tempfile
import unittest

from scripts import component_upgrade as upgrade
from test_component_upgrade import archive, materials, write_package


class PortableCandidatePaths(unittest.TestCase):
    def test_windows_forbidden_names_cannot_enter_an_offline_candidate(self):
        # Microsoft File Naming Conventions prohibit these punctuation/control
        # characters in ordinary filenames. Test the actual manifest/ZIP entry.
        for character in ('<', '>', '"', '|', '?', '*', '\x01', '\x1f'):
            with self.subTest(character=repr(character)), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                old, old_files = materials()
                active = write_package(root / 'active', old, old_files)
                before = active.read_bytes()
                manifest, files = materials('1.1.0')
                name = 'packages/foreign' + character + 'dependency.dll'
                files[name] = b'Original lexical path guard fixture; no real DLL claim.\n'
                manifest['files'][name] = upgrade.digest(files[name])
                manifest['platforms']['linux-x64']['required_files'].append(name)
                zip_path = root / 'bad-candidate.zip'
                sha = archive(zip_path, manifest, files)
                with self.assertRaisesRegex(upgrade.Rejected, 'WINDOWS_FORBIDDEN_CHARACTER'):
                    upgrade.import_candidate(active, zip_path, sha, root / 'registry')
                self.assertEqual(active.read_bytes(), before)
                self.assertFalse(any((root / 'registry').glob('candidates/*')))

    def test_unicode_and_internal_spaces_remain_valid_portable_entries(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            old, old_files = materials()
            active = write_package(root / 'active', old, old_files)
            manifest, files = materials('1.1.0')
            name = 'packages/原件 依赖说明.txt'
            files[name] = '原创 Unicode 原件，不需外网。\n'.encode('utf-8')
            manifest['files'][name] = upgrade.digest(files[name])
            manifest['platforms']['linux-x64']['required_files'].append(name)
            zip_path = root / 'good-candidate.zip'
            sha = archive(zip_path, manifest, files)
            candidate = upgrade.import_candidate(active, zip_path, sha, root / 'registry')
            self.assertEqual((candidate / name).read_bytes(), files[name])
            self.assertFalse(upgrade.plan_upgrade(active, candidate, ['linux-x64'])['active_changed'])


if __name__ == '__main__':
    unittest.main()
