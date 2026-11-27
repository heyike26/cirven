"""Direct-file receipts in tiny roots with no Git executable or repository.

The historical twelve CLI contracts are inherited unchanged, with only their
Git-creating fixture replaced. Additional cases check byte identity, source
drift and the explicitly partial default dependency snapshot.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from unittest.mock import patch

import test_record_slice_cli as legacy


class RecordSliceWithoutGit(legacy.RecordSliceCLI):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="eeas-record-direct-")
        self.root = Path(self.temp.name)
        (self.root / "scripts").mkdir()
        shutil.copyfile(legacy.SCRIPT, self.root / "scripts/record_slice.py")
        (self.root / "apps").mkdir()
        (self.root / "apps/original.py").write_bytes(b"# original public source\n")
        (self.root / "notes").mkdir()
        self.reference = self.root / "notes/原始 空格.txt"
        self.reference.write_bytes("原始来源\r\n末尾保留。\r\n".encode())
        (self.root / "no-executables").mkdir()
        self.path_patch = patch.dict(os.environ, {"PATH": str(self.root / "no-executables")})
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)
        self.events = []
        self.assertIsNone(shutil.which("git"))
        self.assertFalse((self.root / ".git").exists())

    def tearDown(self):
        try:
            self.assertFalse((self.root / ".git").exists())
        finally:
            super().tearDown()

    def all_receipts(self):
        return [(path.parent, json.loads(path.read_text()),
                 json.loads((path.parent / "result.json").read_text()))
                for path in sorted((self.root / "reports").glob("*/*/freeze.json"))]

    def simple_run(self, *, body="print('original direct-file result')", extra=()):
        return self.invoke(["E00-direct", "--acceptance", legacy.CAP, *extra], body=body, payload=[])

    def assert_direct_identity(self, freeze, result):
        self.assertIsNone(freeze["git_base"])
        self.assertEqual(freeze["source_control"], "DIRECT_FILES")
        manifest = {"code_sha256": freeze["code_sha256"],
                    "dependency_sha256": freeze["dependency_sha256"]}
        expected = hashlib.sha256(json.dumps(manifest, sort_keys=True, ensure_ascii=False,
                                            separators=(",", ":")).encode("utf-8")).hexdigest()
        self.assertEqual(freeze["source_revision"], expected)
        self.assertTrue(freeze["source_stable_before_run"])
        self.assertEqual(freeze["source_revision_before_run"], expected)
        if result["source_stable_after_run"]:
            self.assertEqual(result["source_revision_after_run"], expected)

    def test_no_git_executable_records_direct_identity_and_never_creates_repository(self):
        run, command = self.simple_run()
        self.assertEqual(run.returncode, 0, run.stderr)
        path, freeze, result = self.receipt()
        self.assert_direct_identity(freeze, result)
        self.assertEqual(freeze["command"], command)
        self.assertEqual(result["status"], "PASS")
        self.assertTrue(result["source_stable_after_run"])
        self.assertEqual(freeze["snapshot_scope"], "SOURCE_ONLY")
        self.assertFalse(freeze["snapshot_self_contained"])
        self.assertEqual((path / "snapshot/apps/original.py").read_bytes(),
                         (self.root / "apps/original.py").read_bytes())

    def test_unchanged_bytes_keep_revision_across_runs_and_acceptance_changes(self):
        first, _ = self.simple_run()
        self.assertEqual(first.returncode, 0, first.stderr)
        second, _ = self.invoke(["E00-other", "--acceptance", "different public acceptance"],
                                body="print('different command metadata')", payload=[])
        self.assertEqual(second.returncode, 0, second.stderr)
        receipts = self.all_receipts()
        self.assertEqual(len(receipts), 2)
        for _, freeze, result in receipts:
            self.assert_direct_identity(freeze, result)
            self.assertTrue(result["source_stable_after_run"])
        self.assertEqual(receipts[0][1]["source_revision"], receipts[1][1]["source_revision"])
        self.assertNotEqual(receipts[0][1]["run_id"], receipts[1][1]["run_id"])

    def test_source_byte_change_creates_new_revision_and_preserves_previous_freeze(self):
        first, _ = self.simple_run()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_path, first_freeze, _ = self.receipt()
        frozen_receipt = (first_path / "freeze.json").read_bytes()
        original = (first_path / "snapshot/apps/original.py").read_bytes()
        (self.root / "apps/original.py").write_bytes(b"# second public source version\n")
        second, _ = self.simple_run()
        self.assertEqual(second.returncode, 0, second.stderr)
        latest = self.all_receipts()[-1]
        self.assertNotEqual(first_freeze["source_revision"], latest[1]["source_revision"])
        self.assertEqual((first_path / "freeze.json").read_bytes(), frozen_receipt)
        self.assertEqual((first_path / "snapshot/apps/original.py").read_bytes(), original)
        self.assertTrue(latest[2]["source_stable_after_run"])

    def test_intentional_source_mutation_is_recorded_without_rewriting_success(self):
        before = (self.root / "apps/original.py").read_bytes()
        run, command = self.simple_run(body="from pathlib import Path; "
                                       "Path('apps/original.py').write_text('intentional source change'); "
                                       "print('original success remains')")
        self.assertEqual(run.returncode, 0, run.stderr)
        path, freeze, result = self.receipt()
        self.assert_direct_identity(freeze, result)
        self.assertEqual(freeze["command"], command)
        self.assertEqual((result["status"], result["exit_code"]), ("PASS", 0))
        self.assertFalse(result["source_stable_after_run"])
        self.assertNotEqual(freeze["source_revision"], result["source_revision_after_run"])
        self.assertEqual((path / "snapshot/apps/original.py").read_bytes(), before)

    def test_intentional_source_mutation_keeps_original_failure_exit_and_logs(self):
        run, _ = self.simple_run(body="import sys; from pathlib import Path; "
                                "Path('apps/original.py').write_text('changed before original fail'); "
                                "print('original stdout'); print('original stderr',file=sys.stderr); sys.exit(7)")
        self.assertEqual(run.returncode, 7, run.stderr)
        path, freeze, result = self.receipt()
        self.assert_direct_identity(freeze, result)
        self.assertEqual((result["status"], result["exit_code"]), ("FAIL", 7))
        self.assertFalse(result["source_stable_after_run"])
        self.assertEqual((path / "stdout.txt").read_bytes(), b"original stdout\n")
        self.assertEqual((path / "stderr.txt").read_bytes(), b"original stderr\n")

    def test_new_source_file_during_command_is_recorded_as_unstable(self):
        run, _ = self.simple_run(body="from pathlib import Path; "
                                "Path('apps/new.py').write_text('# original new source'); print('added')")
        self.assertEqual(run.returncode, 0, run.stderr)
        _, freeze, result = self.receipt()
        self.assertNotIn("apps/new.py", freeze["code_sha256"])
        self.assertFalse(result["source_stable_after_run"])

    def test_deleted_source_file_during_command_is_recorded_as_unstable(self):
        run, _ = self.simple_run(body="from pathlib import Path; "
                                "Path('apps/original.py').unlink(); print('deleted')")
        self.assertEqual(run.returncode, 0, run.stderr)
        path, freeze, result = self.receipt()
        self.assertIn("apps/original.py", freeze["code_sha256"])
        self.assertTrue((path / "snapshot/apps/original.py").is_file())
        self.assertFalse(result["source_stable_after_run"])

    def test_default_hashes_dependency_without_copy_and_binds_it_to_revision(self):
        (self.root / "toolchain").mkdir()
        dependency = self.root / "toolchain/original-install.bin"
        dependency.write_bytes(b"original dependency bytes\x00" * 1024)
        first, _ = self.simple_run()
        self.assertEqual(first.returncode, 0, first.stderr)
        path, frozen, result = self.receipt()
        self.assert_direct_identity(frozen, result)
        self.assertTrue(result["source_stable_after_run"])
        self.assertEqual(frozen["dependency_sha256"], {
            "toolchain/original-install.bin": hashlib.sha256(dependency.read_bytes()).hexdigest()})
        self.assertFalse((path / "snapshot/toolchain").exists())
        dependency.write_bytes(b"second exact dependency bytes\x00")
        second, _ = self.simple_run()
        self.assertEqual(second.returncode, 0, second.stderr)
        latest = self.all_receipts()[-1]
        self.assertEqual(frozen["code_sha256"], latest[1]["code_sha256"])
        self.assertNotEqual(frozen["source_revision"], latest[1]["source_revision"])

    def test_explicit_dependency_snapshot_copies_exact_bytes_without_changing_revision(self):
        (self.root / "toolchain").mkdir()
        dependency = self.root / "toolchain/original-install.bin"
        dependency.write_bytes(b"original explicit dependency\x00")
        first, _ = self.simple_run()
        self.assertEqual(first.returncode, 0, first.stderr)
        first_path, first_freeze, _ = self.receipt()
        second, _ = self.simple_run(extra=["--snapshot-dependencies"])
        self.assertEqual(second.returncode, 0, second.stderr)
        path, freeze, result = self.all_receipts()[-1]
        self.assert_direct_identity(freeze, result)
        self.assertEqual(freeze["snapshot_scope"], "SOURCE_AND_DEPENDENCIES")
        self.assertFalse(freeze["snapshot_self_contained"])
        self.assertEqual((path / "snapshot/toolchain/original-install.bin").read_bytes(), dependency.read_bytes())
        self.assertEqual(first_freeze["source_revision"], freeze["source_revision"])
        self.assertFalse((first_path / "snapshot/toolchain").exists())

    def test_dependency_mutation_during_command_is_recorded_as_unstable(self):
        (self.root / "toolchain").mkdir()
        dependency = self.root / "toolchain/original-install.bin"
        dependency.write_bytes(b"original stable dependency")
        run, _ = self.simple_run(body="from pathlib import Path; "
                                "Path('toolchain/original-install.bin').write_bytes(b'changed dependency'); print('changed')")
        self.assertEqual(run.returncode, 0, run.stderr)
        _, freeze, result = self.receipt()
        self.assert_direct_identity(freeze, result)
        self.assertEqual(result["status"], "PASS")
        self.assertFalse(result["source_stable_after_run"])

    def test_explicit_dependency_reference_is_frozen_even_in_source_only_mode(self):
        (self.root / "toolchain").mkdir()
        dependency = self.root / "toolchain/original-manifest.json"
        dependency.write_bytes(b'{"fixture":"public original"}\n')
        run, _ = self.simple_run(extra=["--reference", "toolchain/original-manifest.json"])
        self.assertEqual(run.returncode, 0, run.stderr)
        path, freeze, result = self.receipt()
        self.assert_direct_identity(freeze, result)
        name = "toolchain/original-manifest.json"
        self.assertEqual(freeze["code_sha256"][name], freeze["dependency_sha256"][name])
        self.assertEqual((path / "snapshot" / name).read_bytes(), dependency.read_bytes())
        self.assertEqual(freeze["snapshot_scope"], "SOURCE_ONLY")

    def test_legacy_command_owned_dependency_flag_remains_opaque(self):
        payload = ["--snapshot-dependencies", "--", "original command value"]
        run, command = self.invoke(["--acceptance", legacy.CAP, "E00-direct"],
                                   separator=False, payload=payload)
        self.assertEqual(run.returncode, 0, run.stderr)
        path, freeze, result = self.receipt()
        self.assert_direct_identity(freeze, result)
        self.assertEqual(freeze["command"], command)
        self.assertEqual(json.loads((path / "stdout.txt").read_text())["argv"], payload)
        self.assertEqual(freeze["snapshot_scope"], "SOURCE_ONLY")
