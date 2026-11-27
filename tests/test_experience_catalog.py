"""Original stdlib counterexamples; no browser, model, installer or upstream execution."""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import experience_catalog as catalog


class ExperienceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        (self.root / "reports").mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return path

    def draft(self, revision=1, previous=None):
        path = "reports/original/result.json"
        if not (self.root / path).exists():
            self.put(path, {"status": "FAIL", "exit_code": 1})
        record = {"schema_version": catalog.LESSON_SCHEMA, "lesson_key": "original-fixture-boundary",
                  "revision": revision, "recorded_at": "2026-10-13T00:00:00+00:00", "area": "evidence",
                  "trigger": "An original fixture changed", "observation": "A failure is retained",
                  "decision": "Bind raw bytes and do not infer live scope", "scope": "Original local parser fixture only",
                  "qualification": {"assessment": "HASH_BINDING_ONLY", "company_model": "NOT_RUN"},
                  "unvalidated": ["Native Windows and company model"],
                  "evidence": [{"path": path, "sha256": hashlib.sha256((self.root / path).read_bytes()).hexdigest(), "role": "before_fail"}],
                  "previous_sha256": previous, "record_sha256": ""}
        record["record_sha256"] = catalog.record_digest(record)
        return record

    def test_successful_metadata_does_not_grant_native_or_live_scope(self):
        self.put("reports/pass/result.json", {"status": "PASS", "exit_code": 0})
        self.put("reports/pass/freeze.json", {"git_base": "a" * 40, "slice": "original-local",
                 "acceptance_before_run": "No company call", "code_sha256": {"source.py": "b" * 64}})
        self.put("reports/missing/README.md", b"# No result was recorded\n")
        self.put("reports/conflict/result.json", {"status": "PASS", "exit_code": 1})
        self.put("reports/bool/result.json", {"status": "PASS", "exit_code": False})
        found = {entry["path"]: entry for entry in catalog.scan_reports(self.root / "reports")["entries"]}
        self.assertEqual(found["pass"]["status"], "PASS")
        self.assertEqual(found["pass"]["git_base"], "a" * 40)
        self.assertEqual(found["pass"]["qualification"]["company_model"], "UNKNOWN")
        self.assertEqual(found["pass"]["qualification"]["native_windows"], "UNKNOWN")
        for name in ("missing", "conflict", "bool"):
            self.assertEqual(found[name]["status"], "UNKNOWN")

    def test_duplicate_keys_nan_and_malformed_results_remain_unknown(self):
        bad = [b'{"status":"FAIL","status":"PASS","exit_code":0}',
               b'{"status":"PASS","exit_code":NaN}', b'{"status":"PASS",', b'[]',
               b'{"nested":' + b'[' * 2000 + b'0' + b']' * 2000 + b'}']
        for index, raw in enumerate(bad):
            self.put(f"reports/bad-{index}/result.json", raw)
        result = catalog.scan_reports(self.root / "reports")
        self.assertEqual(len(result["entries"]), len(bad))
        self.assertTrue(all(entry["status"] == "UNKNOWN" for entry in result["entries"]))
        for entry in result["entries"]:
            path = self.root / "reports" / entry["sources"][0]["path"]
            self.assertEqual(entry["sources"][0]["sha256"], hashlib.sha256(path.read_bytes()).hexdigest())
            self.assertTrue(entry["parse_notes"])

    def test_index_redacts_credential_values_but_keeps_original_raw_digest(self):
        self.put("reports/private-shape/freeze.json", {"command": ["original-runner", "--token", "never-display-this-value"],
                 "acceptance_before_run": '{"api_key":"never-display-this-value"}'})
        self.put("reports/private-shape/result.json", {"status": {"token": "never-display-this-value"}, "exit_code": 0})
        raw = b'ERROR tests/original.py::test_rpc - Authorization=Bearer never-display-this-value\n'
        self.put("reports/private-shape/stdout.txt", raw)
        entry = catalog.scan_reports(self.root / "reports")["entries"][0]
        self.assertNotIn("never-display-this-value", json.dumps(entry))
        source = next(value for value in entry["sources"] if value["kind"] == "log")
        self.assertEqual(source["sha256"], hashlib.sha256(raw).hexdigest())

    def test_large_failure_log_keeps_complete_hash_and_explicit_partial_analysis(self):
        prefix = b"ERROR tests/original.py::test_visible_failure - original\n"
        raw = prefix + b"original log fixture\n" * 60000 + b"FAILED tests/original.py::test_late_failure\n"
        path = self.put("reports/failure/stdout.txt", raw)
        result = catalog.scan_reports(self.root / "reports")
        entry = result["entries"][0]
        self.assertTrue(entry["sources"][0]["metadata_truncated"])
        self.assertTrue(entry["failure_cases_truncated"])
        self.assertEqual(entry["sources"][0]["sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotIn("test_late_failure", json.dumps(entry["failure_cases"]))
        path.write_bytes(raw + b"different trailing raw bytes")
        changed = catalog.scan_reports(self.root / "reports")["entries"][0]
        self.assertNotEqual(entry["sources"][0]["sha256"], changed["sources"][0]["sha256"])
        self.assertEqual(entry["failure_cases"], changed["failure_cases"])

    def test_source_snapshots_are_refs_not_duplicate_reports(self):
        self.put("reports/real/result.json", {"status": "FAIL", "exit_code": 1})
        self.put("reports/real/snapshot/reports/fake/result.json", {"status": "PASS", "exit_code": 0})
        self.put("reports/real/startup-snapshot/README.md", b"# Nested copy should not count\n")
        result = catalog.scan_reports(self.root / "reports")
        self.assertEqual(result["coverage"]["metadata_files_hashed"], 1)
        self.assertEqual(result["coverage"]["snapshot_refs_not_traversed"], 2)
        self.assertEqual([entry["path"] for entry in result["entries"]], ["real"])

    def test_symlink_external_evidence_and_windows_aliases_are_not_read_as_sources(self):
        outside = self.put("outside/result.json", {"status": "PASS", "exit_code": 0})
        try:
            (self.root / "reports" / "linked").symlink_to(outside.parent, target_is_directory=True)
        except OSError:
            self.skipTest("Native symlink creation not available; no Windows qualification")
        self.put("reports/A/result.json", {"status": "PASS", "exit_code": 0})
        self.put("reports/a/result.json", {"status": "FAIL", "exit_code": 1})
        if os.path.samefile(self.root / "reports/A", self.root / "reports/a"):
            self.skipTest("Distinct case-alias fixture needs a case-sensitive filesystem; no native Windows claim")
        result = catalog.scan_reports(self.root / "reports")
        self.assertEqual(len(result["rejected_entries"]), 2)
        self.assertEqual(result["coverage"]["metadata_files_hashed"], 1)
        for relative in (".", "../outside/result.json", "/outside/result.json", "C:/outside/result.json", "reports/A./result.json"):
            with self.assertRaises(catalog.CatalogError):
                catalog.safe_source(self.root, relative)

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO fixture is POSIX only")
    def test_non_regular_metadata_cannot_block_the_reader(self):
        os.mkfifo(self.root / "reports" / "stdout.txt")
        result = catalog.scan_reports(self.root / "reports")
        self.assertEqual(result["coverage"]["metadata_files_hashed"] if "metadata_files_hashed" in result["coverage"] else 0, 0)
        self.assertEqual(result["rejected_entries"][0]["reason"], "Evidence must be a regular file")

    def test_append_preserves_original_bytes_and_rejects_mutation_and_wrong_parent(self):
        first = self.draft()
        catalog.append_lesson(self.root, "experience/lessons.jsonl", first)
        ledger = self.root / "experience/lessons.jsonl"
        original = ledger.read_bytes()
        with self.assertRaises(catalog.CatalogError):
            catalog.append_lesson(self.root, "experience/lessons.jsonl", self.draft(2, "f" * 64))
        self.assertEqual(ledger.read_bytes(), original)
        second = self.draft(2, first["record_sha256"])
        catalog.append_lesson(self.root, "experience/lessons.jsonl", second)
        self.assertTrue(ledger.read_bytes().startswith(original))
        changed = first.copy(); changed["observation"] = "Rewritten old fact"
        ledger.write_bytes(catalog.canonical(changed) + b"\n" + catalog.canonical(second) + b"\n")
        with self.assertRaises(catalog.CatalogError):
            catalog.load_lessons(self.root, "experience/lessons.jsonl")

    def test_bad_evidence_creates_no_ledger_and_no_leftover_append_lock(self):
        record = self.draft()
        self.put("reports/original/result.json", {"status": "PASS", "exit_code": 0})
        with self.assertRaises(catalog.CatalogError):
            catalog.append_lesson(self.root, "experience/lessons.jsonl", record)
        self.assertFalse((self.root / "experience/lessons.jsonl").exists())
        self.assertFalse((self.root / "experience/lessons.jsonl.append-lock").exists())

    def test_small_bundle_binds_selected_raw_sources_without_product_or_source_snapshot(self):
        record = self.draft(); catalog.append_lesson(self.root, "experience/lessons.jsonl", record)
        for path in ("experience/README.md", "scripts/experience_catalog.py", ".claude/skills/eeas-development-lessons/SKILL.md",
                     ".claude/skills/eeas-development-lessons/references/ledger-schema.md"):
            self.put(path, b"original test fixture, not product code\n")
        catalog_path = self.put("catalog.json", catalog.scan_reports(self.root / "reports"))
        output = self.root / "experience.zip"
        manifest = catalog.bundle_experience(self.root, catalog_path, output)
        with zipfile.ZipFile(output) as archive:
            for member in manifest["members"]:
                self.assertEqual(hashlib.sha256(archive.read(member["path"])).hexdigest(), member["sha256"])
            self.assertNotIn("apps/web/index.html", archive.namelist())
        self.assertTrue(manifest["external_raw_reports_required"])
        self.assertFalse(manifest["source_snapshots_included"])
        original = output.read_bytes()
        with self.assertRaises(FileExistsError):
            catalog.bundle_experience(self.root, catalog_path, output)
        self.assertEqual(output.read_bytes(), original)

    def test_bundle_rejects_selected_private_config_before_creating_archive(self):
        record = self.draft()
        private = self.put(".claude/settings.json", b"original private-shape test fixture")
        record["evidence"] = [{"path": ".claude/settings.json", "sha256": hashlib.sha256(private.read_bytes()).hexdigest(), "role": "forbidden_raw_config"}]
        record["record_sha256"] = catalog.record_digest(record)
        catalog.append_lesson(self.root, "experience/lessons.jsonl", record)
        catalog_path = self.put("catalog.json", catalog.scan_reports(self.root / "reports"))
        output = self.root / "forbidden.zip"
        with self.assertRaises(catalog.CatalogError):
            catalog.bundle_experience(self.root, catalog_path, output)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
