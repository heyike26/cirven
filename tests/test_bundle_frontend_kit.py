"""Original small public fixtures; never install wheels or execute packaged skills."""
import hashlib
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts import bundle_frontend_kit as kit
from scripts import experience_catalog as lessons


class FrontendKitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.base = Path(self.temporary.name)
        self.root = self.base / "原创 中文 fixture"
        self.root.mkdir()
        for relative in kit.FILES + kit.ESSENTIAL_ASSETS:
            self.put(relative, b"Original kit fixture only; not production/upstream code.\n")
        self.put("experience/lessons.jsonl", b"")
        profile = []
        for name in kit.SKILLS:
            base = ".claude/skills/" + name
            skill = self.put(base + "/SKILL.md", ("# Original guidance fixture: " + name).encode())
            if name in kit.NEW_SKILLS:
                license_path = self.put(base + "/UPSTREAM-LICENSE.txt", b"Original license-shape fixture; not an upstream license.\n")
                self.put(base + "/UPSTREAM_SOURCE.json", {"commit": "a" * 40, "repository": "original-fixture",
                         "license_spdx": "UNKNOWN_ORIGINAL_FIXTURE", "files": {
                             "SKILL.md": self.sha(skill), "UPSTREAM-LICENSE.txt": self.sha(license_path)}})
            elif name in kit.EXISTING_SKILLS:
                license_path = self.put(base + "/LICENSE.txt", b"Original license-shape fixture.\n")
                profile.append({"name": name, "commit": "b" * 40, "license": "UNKNOWN_ORIGINAL_FIXTURE",
                                "files_sha256": {"SKILL.md": self.sha(skill), "LICENSE.txt": self.sha(license_path)}})
        self.put("profiles/development-skills.json", profile)
        self.font_fixture()
        engine_base = ".claude/skills/impeccable/scripts"
        self.put(engine_base + "/VERSION", b"0.1.14\n")
        engine_rows = []
        for relative in (engine_base + "/bin/linux-x64/impeccable", engine_base + "/bin/windows-x64/impeccable.exe"):
            path = self.root / relative
            sidecar = self.put(relative + ".sha256", (self.sha(path) + "  " + path.name + "\n").encode())
            engine_rows.extend({"path": str(item.relative_to(self.root)).replace(os.sep, "/"), "sha256": self.sha(item),
                                "byte_length": item.stat().st_size, "kind": "original-fixture-never-executed",
                                "upstream_url": "https://example.invalid/original-fixture"} for item in (path, sidecar))
        license_path = self.put(engine_base + "/bin/LICENSE-engine-v0.1.14.txt", b"Original engine license-shaped fixture only.")
        engine_rows.append({"path": str(license_path.relative_to(self.root)).replace(os.sep, "/"),
                            "sha256": self.sha(license_path), "byte_length": license_path.stat().st_size,
                            "kind": "license-fixture", "upstream_url": "https://example.invalid/original-fixture"})
        self.put(engine_base + "/bin/OFFLINE_ENGINE_MANIFEST.json", {"schema": "eeas.skill-offline-engine.v1",
                 "commit": "d" * 40, "release_tag": "engine-v0.1.14", "files": engine_rows})
        self.git("init", "-q")
        self.git("add", ".")
        self.git("-c", "user.name=Original Fixture", "-c", "user.email=fixture@local.invalid", "commit", "-qm", "Original source fixture")

    def tearDown(self):
        self.temporary.cleanup()

    def put(self, relative, value):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(value if isinstance(value, bytes) else json.dumps(value).encode())
        return path

    @staticmethod
    def sha(path):
        return hashlib.sha256(path.read_bytes()).hexdigest()

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.root), *args], check=True, capture_output=True).stdout

    def font_fixture(self):
        fonts = []
        for name in ("notosanssc", "manrope"):
            original = self.put("apps/web/assets/fonts/upstream/" + name + "/original.ttf", ("Original font-shaped fixture: " + name).encode())
            license_path = self.put("apps/web/assets/fonts/upstream/" + name + "/OFL.txt", b"Original OFL-shaped fixture, not a real font license.")
            fonts.extend({"path": str(path.relative_to(self.root / "apps/web/assets/fonts")).replace(os.sep, "/"),
                          "sha256": self.sha(path), "byte_length": path.stat().st_size} for path in (original, license_path))
        self.put("apps/web/assets/fonts/sources.json", {"commit": "c" * 40, "files": fonts})
        runtime = self.put("apps/web/assets/fonts/sans-fixture.woff2", b"Original WOFF2-shaped fixture; not decoded/executed.")
        self.put("apps/web/assets/fonts/manifest.json", {"fonts": [{"original_path": fonts[0]["path"],
                 "original_sha256": fonts[0]["sha256"], "woff2_path": runtime.name,
                 "woff2_sha256": self.sha(runtime), "woff2_bytes": runtime.stat().st_size}]})
        serif_base = "toolchain/ui-fonts/noto-serif-sc"
        original = self.put(serif_base + "/upstream/original.ttf", b"Original serif-shaped fixture; not decoded.")
        ofl = self.root / (serif_base + "/upstream/OFL.txt")
        self.put(serif_base + "/sources.json", {"commit": "c" * 40, "files": [{"path": "upstream/" + path.name,
                 "sha256": self.sha(path), "byte_length": path.stat().st_size} for path in (original, ofl)]})
        self.put(serif_base + "/manifest.json", {"original_path": str(original.relative_to(self.root)).replace(os.sep, "/"),
                 "original_sha256": self.sha(original), "original_bytes": original.stat().st_size,
                 "runtime_path": str(runtime.relative_to(self.root)).replace(os.sep, "/"),
                 "woff2_sha256": self.sha(runtime), "woff2_bytes": runtime.stat().st_size})
        rows = []
        for name in ("fonttools-4.65.0-py3-none-any.whl", "brotli-1.2.0-cp310-cp310-win_amd64.whl",
                     "brotli-1.2.0-cp310-cp310-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"):
            wheel = self.put("apps/web/assets/fonts/generation/wheels/" + name, ("Original wheel-shaped bytes, never installed: " + name).encode())
            license_path = self.put("apps/web/assets/fonts/generation/licenses/" + name + "/LICENSE", b"Original wheel license-shaped fixture.")
            rows.append({"path": "wheels/" + name, "sha256": self.sha(wheel), "byte_length": wheel.stat().st_size,
                         "licenses": [{"path": "licenses/" + name + "/LICENSE", "sha256": self.sha(license_path)}]})
        self.put("apps/web/assets/fonts/generation/tools-manifest.json", {"schema": "eeas.offline-font-tools.v1", "files": rows})

    def build(self, name="kit.zip", **kwargs):
        output = self.base / name
        manifest = kit.build_frontend_kit(self.root, output, **kwargs)
        return output, manifest

    def add_lesson(self):
        relative = "reports/selected/result.json"
        evidence = self.put(relative, {"status": "FAIL", "exit_code": 1})
        record = {"schema_version": lessons.LESSON_SCHEMA, "lesson_key": "original-kit-test", "revision": 1,
                  "recorded_at": "2026-10-13T00:00:00+00:00", "area": "offline", "trigger": "Original fixture",
                  "observation": "Original failure retained", "decision": "Bind original raw", "scope": "Original small test only",
                  "qualification": {"assessment": "HASH_ONLY_NOT_NATIVE"}, "unvalidated": ["Windows/model/company"],
                  "evidence": [{"path": relative, "sha256": self.sha(evidence), "role": "before_fail"}],
                  "previous_sha256": None, "record_sha256": ""}
        record["record_sha256"] = lessons.record_digest(record)
        lessons.append_lesson(self.root, "experience/lessons.jsonl", record)
        return relative

    def test_actual_dirty_bytes_and_original_sources_are_bound_without_commercial_toolchain(self):
        self.put("toolchain/claude-commercial/forbidden.bin", b"Do not include this original exclusion sentinel")
        changed = self.put("apps/web/assets/app.js", b"Original changed working bytes differing from Git commit")
        output, manifest = self.build()
        self.assertTrue(manifest["source_before"]["repository_dirty"])
        self.assertEqual(manifest["source_before"]["git_head"], self.git("rev-parse", "HEAD").decode().strip())
        with zipfile.ZipFile(output) as archive:
            self.assertNotIn("toolchain/claude-commercial/forbidden.bin", archive.namelist())
            self.assertEqual(archive.read("apps/web/assets/app.js"), changed.read_bytes())
            self.assertIn("不是完整 EEAS", archive.read(kit.README).decode())
            for name in kit.NEW_SKILLS:
                self.assertEqual(archive.read(".claude/skills/" + name + "/UPSTREAM-LICENSE.txt"),
                                 (self.root / ".claude/skills" / name / "UPSTREAM-LICENSE.txt").read_bytes())
            self.assertEqual((archive.getinfo(".claude/skills/impeccable/scripts/bin/linux-x64/impeccable").external_attr >> 16) & 0xffff,
                             stat.S_IFREG | 0o755)
        checked = kit.validate_frontend_kit(output, self.sha(output))
        self.assertEqual(checked["qualification"], "NOT_INFERRED")
        self.assertEqual(manifest["qualification"]["public_ten_project_campaign"], "NOT_COMPLETE")

    def test_missing_pinned_wheel_and_modified_original_license_fail_before_output(self):
        wheel = next((self.root / "apps/web/assets/fonts/generation/wheels").iterdir())
        original = wheel.read_bytes(); wheel.unlink()
        with self.assertRaises((kit.CatalogError, OSError)):
            self.build("missing.zip")
        self.assertFalse((self.base / "missing.zip").exists())
        wheel.write_bytes(original)
        self.put(".claude/skills/break-ui/UPSTREAM-LICENSE.txt", b"Changed original license bytes")
        with self.assertRaisesRegex(kit.CatalogError, "Pinned original"):
            self.build("changed.zip")
        self.assertFalse((self.base / "changed.zip").exists())

    def test_output_is_exclusive_and_selected_private_cache_is_rejected(self):
        output, _ = self.build()
        before = output.read_bytes()
        with self.assertRaises(FileExistsError):
            kit.build_frontend_kit(self.root, output)
        self.assertEqual(output.read_bytes(), before)
        self.put("apps/web/.env.local", b"Original private-shape exclusion sentinel")
        with self.assertRaisesRegex(kit.CatalogError, "Private/cache"):
            self.build("private.zip")
        self.assertFalse((self.base / "private.zip").exists())

    def test_selected_symlink_and_case_alias_do_not_enter_archive(self):
        outside = self.base / "external-original.txt"; outside.write_bytes(b"Outside exclusion sentinel")
        link = self.root / "apps/web/link.txt"
        try:
            link.symlink_to(outside)
        except OSError:
            self.skipTest("Native symlink fixture unavailable; no native Windows qualification")
        with self.assertRaisesRegex(kit.CatalogError, "Symlink"):
            self.build("link.zip")
        link.unlink()
        a = self.put("apps/web/A.txt", b"Original A")
        b = self.put("apps/web/a.txt", b"Original a")
        if os.path.samefile(a, b):
            self.skipTest("Distinct alias fixture needs case-sensitive filesystem")
        with self.assertRaisesRegex(kit.CatalogError, "alias"):
            self.build("alias.zip")

    def test_lesson_raw_is_explicit_optional_and_snapshot_references_are_not_copied(self):
        relative = self.add_lesson()
        report = "reports/" + kit.REPORTS[0] + "/original-run"
        self.put(report + "/result.json", {"status": "FAIL", "exit_code": 1})
        self.put(report + "/snapshot/result.json", {"status": "PASS", "exit_code": 0})
        output, manifest = self.build(include_lesson_evidence=True)
        with zipfile.ZipFile(output) as archive:
            self.assertEqual(archive.read(relative), (self.root / relative).read_bytes())
            self.assertFalse(any("/snapshot/" in name for name in archive.namelist()))
        refs = manifest["report_references"][0]
        self.assertEqual([row["path"] for row in refs["metadata"]], [report + "/result.json"])
        self.assertFalse(refs["snapshots_traversed"])
        self.assertEqual(manifest["copied_lesson_evidence"], [relative])
        other, _ = self.build("refs-only.zip")
        with zipfile.ZipFile(other) as archive:
            self.assertNotIn(relative, archive.namelist())

    def test_actual_source_write_during_copy_rejects_instead_of_attesting_old_pin(self):
        original = kit.inspect_file
        changed = False
        def reader(path, limit=kit.MAX_FILE_BYTES):
            nonlocal changed
            if limit == kit.MAX_FILE_BYTES and Path(path) == self.root / "apps/web/assets/app.js" and not changed:
                changed = True
                Path(path).write_bytes(b"Original injected source race")
            return original(path, limit)
        with patch.object(kit, "inspect_file", reader):
            with self.assertRaisesRegex(kit.CatalogError, "Source changed during kit build"):
                self.build("race.zip")
        self.assertTrue(changed)
        with self.assertRaises((kit.CatalogError, zipfile.BadZipFile)):
            kit.validate_frontend_kit(self.base / "race.zip")

    def rewrite(self, source, destination, change=None, omit=None, extra=None):
        with zipfile.ZipFile(source) as original, zipfile.ZipFile(destination, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name in original.namelist():
                if name == omit:
                    continue
                raw = original.read(name)
                target.writestr(original.getinfo(name), change(raw) if name == "apps/web/assets/app.js" and change else raw)
            if extra:
                target.writestr(*extra)

    def test_member_tamper_missing_piece_and_unlisted_payload_rejected_independently(self):
        output, _ = self.build()
        for name, kwargs, pattern in (
            ("tamper.zip", {"change": lambda raw: bytes([raw[0] ^ 1]) + raw[1:]}, "SHA differs"),
            ("missing.zip", {"omit": "tests/conftest.py"}, "Missing or unlisted"),
            ("extra.zip", {"extra": ("packages/adapters/private.py", b"Original unlisted fixture")}, "Missing or unlisted"),
        ):
            destination = self.base / name; self.rewrite(output, destination, **kwargs)
            with self.assertRaisesRegex(kit.CatalogError, pattern):
                kit.validate_frontend_kit(destination)
        # A self-consistent attacker manifest still cannot omit a mandatory helper.
        destination = self.base / "self-consistent-missing.zip"
        with zipfile.ZipFile(output) as original, zipfile.ZipFile(destination, "w") as target:
            for name in original.namelist():
                if name == "tests/conftest.py":
                    continue
                raw = original.read(name)
                if name == kit.MANIFEST:
                    manifest = json.loads(raw)
                    manifest["members"] = [row for row in manifest["members"] if row["path"] != "tests/conftest.py"]
                    raw = json.dumps(manifest).encode()
                target.writestr(original.getinfo(name), raw)
        with self.assertRaisesRegex(kit.CatalogError, "Required frontend"):
            kit.validate_frontend_kit(destination)

    def test_archive_traversal_alias_special_compression_and_small_bomb_are_blocked_before_read(self):
        cases = [
            ("traversal.zip", "../outside.txt", zipfile.ZIP_STORED, b"x", "traversal"),
            ("bzip2.zip", "apps/web/test.txt", zipfile.ZIP_BZIP2, b"x", "compression"),
            ("lzma.zip", "apps/web/test.txt", zipfile.ZIP_LZMA, b"x", "compression"),
            ("ratio.zip", "apps/web/test.txt", zipfile.ZIP_DEFLATED, b"0" * 20000, "ratio"),
        ]
        for filename, name, compression, raw, pattern in cases:
            output = self.base / filename
            with zipfile.ZipFile(output, "w", compression=compression) as archive:
                archive.writestr(name, raw)
            with self.assertRaisesRegex(kit.CatalogError, pattern):
                kit.validate_frontend_kit(output)
        output = self.base / "unicode-alias.zip"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr("apps/web/Café.txt", b"Original composed")
            archive.writestr("apps/web/Cafe\u0301.txt", b"Original decomposed")
        with self.assertRaisesRegex(kit.CatalogError, "alias"):
            kit.validate_frontend_kit(output)
        info = zipfile.ZipInfo("apps/web/link.txt"); info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        output = self.base / "symlink.zip"
        with zipfile.ZipFile(output, "w") as archive:
            archive.writestr(info, "../outside.txt")
        with self.assertRaisesRegex(kit.CatalogError, "special"):
            kit.validate_frontend_kit(output)
        self.assertFalse((self.base / "outside.txt").exists())

    def test_external_archive_identity_is_not_replaced_by_valid_inner_hashes(self):
        output, _ = self.build()
        with self.assertRaisesRegex(kit.CatalogError, "External archive SHA"):
            kit.validate_frontend_kit(output, "0" * 64)
        self.assertEqual(kit.validate_frontend_kit(output, self.sha(output))["status"], "HASH_AND_STRUCTURE_VALID_ONLY")


if __name__ == "__main__":
    unittest.main()
