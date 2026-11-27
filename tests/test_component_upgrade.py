"""Original fixtures, actual record_slice subprocess and component calls; no GUI/model."""
import copy
import io
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from scripts import component_upgrade as upgrade

ROOT = Path(__file__).resolve().parents[1]
PROBE = '''import hashlib, importlib.util, json, os, pathlib, platform, sys, unittest
import xml.etree.ElementTree as ET
root=pathlib.Path.cwd(); evidence=pathlib.Path(os.environ["EEAS_EVIDENCE_DIR"])
path=root/"packages/component.py"
spec=importlib.util.spec_from_file_location("original_component",path)
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
calls=[]
class Probe(unittest.TestCase):
 def test_base(self):
  self.assertEqual(module.normalize("Bound Source"),"BOUND SOURCE");calls.append("normalize")
if hasattr(module,"render"):
 def test_new(self):
  self.assertEqual(module.render("首行\\n末行"),"1:首行\\n2:末行");calls.append("render")
 Probe.test_new=test_new
suite=unittest.defaultTestLoader.loadTestsFromTestCase(Probe)
result=unittest.TextTestRunner(verbosity=2).run(suite)
tree=ET.Element("testsuite",tests=str(result.testsRun))
for name in ["test_base"]+(["test_new"] if hasattr(module,"render") else []):
 node=ET.SubElement(tree,"testcase",classname="original.Probe",name=name)
 if name in {test.id().rsplit(".",1)[-1] for test,_ in result.failures+result.errors}:
  ET.SubElement(node,"failure").text="Actual original fixture assertion failed"
ET.ElementTree(tree).write(evidence/"junit.xml",encoding="utf-8",xml_declaration=True)
observation={"runtime_os":platform.system().lower(),"runtime_arch":platform.machine(),
 "capability_calls":calls,"loaded_files":{"packages/component.py":hashlib.sha256(path.read_bytes()).hexdigest()}}
(evidence/"invocation.json").write_text(json.dumps(observation),encoding="utf-8")
sys.exit(0 if result.wasSuccessful() else 1)
'''
V1 = b'def normalize(value):\n    return value.upper()\n'
V2 = V1 + 'def render(value):\n    return "\\n".join(f"{i}:{line}" for i,line in enumerate(value.splitlines(),1))\n'.encode()


def materials(version="1.0.0", code=V1):
    files = {"packages/component.py": code, "packages/upstream.raw": code,
             "packages/LICENSE.txt": b"Original regression fixture; no upstream product claim.\n",
             "tests/probe.py": PROBE.encode("utf-8")}
    cases = ["original.Probe.test_base"] + (["original.Probe.test_new"] if b"def render" in code else [])
    capabilities = {"normalize": {"parameters_sha256": upgrade.digest(b"string->string"), "permissions": [],
                                  "required_cases": cases, "qualification_scope": "fixture_contract"}}
    if b"def render" in code:
        capabilities["render"] = copy.deepcopy(capabilities["normalize"])
    manifest = {"schema_version": upgrade.MANIFEST_SCHEMA, "component_id": "original-formatter", "version": version,
      "source": {"kind": "synthetic", "repository": "original: component upgrade fixture", "commit": "a" * 40,
                 "archive_path": "packages/upstream.raw", "archive_sha256": upgrade.digest(code)},
      "interfaces": {"format.v1": upgrade.digest(b"original fixed JSON interface")}, "data_schema": "original.data.v1",
      "files": {name: upgrade.digest(raw) for name, raw in files.items()},
      "platforms": {"linux-x64": {"required_files": list(files), "required_capabilities": list(capabilities)}},
      "capabilities": capabilities}
    return manifest, files


def write_package(root, manifest, files):
    root.mkdir(parents=True)
    for name, raw in files.items():
        path = root / name; path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(raw)
    upgrade.save_new(root / "manifest.json", manifest)
    return root / "manifest.json"


def archive(path, manifest, files, extra=None):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as stream:
        stream.writestr("manifest.json", upgrade.canonical(manifest))
        for name, data in files.items(): stream.writestr(name, data)
        for name, data in (extra or {}).items(): stream.writestr(name, data)
    return upgrade.digest(path.read_bytes())


def actual_receipt(root, manifest, files, capability="normalize"):
    write_package(root, manifest, files)
    (root / "scripts").mkdir()
    shutil.copyfile(ROOT / "scripts/record_slice.py", root / "scripts/record_slice.py")
    command = [sys.executable, "tests/probe.py"]
    run = subprocess.run([sys.executable, "scripts/record_slice.py",
            "--acceptance", "Actual original loaded component behavior, source bytes and exact non-skipped cases",
            "--reference", "manifest.json", "E08-original-component", "--", *command], cwd=root, capture_output=True, text=True)
    receipt = next((root / "reports/E08-original-component").iterdir())
    binding = {"schema_version": "eeas.component-observation.v1",
        "manifest_sha256": upgrade.ComponentManifest.parse(manifest).sha256, "capability_id": capability,
        "platform": "linux-x64", "runtime_os": platform.system().lower(), "loaded": True, "invoked": True,
        "qualification_scope": "fixture_contract", "command": command,
        "source_bindings": {name: name for name in files}, "manifest_reference": "manifest.json",
        "artifact_sha256": {name: upgrade.digest((receipt / name).read_bytes()) for name in
                            ["freeze.json", "result.json", "stdout.txt", "stderr.txt", "junit.xml", "invocation.json"]}}
    upgrade.save_new(receipt / "component-observation.json", binding)
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        saved = Path(os.environ["EEAS_EVIDENCE_DIR"]) / ("original-component-" + root.parent.name + "-" + root.name)
        shutil.copytree(receipt, saved)
    return receipt, run


class ComponentUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.old, self.old_files = materials()
        self.new, self.new_files = materials("1.1.0", V2)
        self.active = write_package(self.root / "active", self.old, self.old_files)
        self.zip = self.root / "candidate.zip"
        self.sha = archive(self.zip, self.new, self.new_files)
        self.registry = self.root / "registry"

    def tearDown(self):
        self.temp.cleanup()

    def candidate(self, manifest=None, files=None):
        if manifest is not None:
            self.sha = archive(self.zip, manifest, files or self.new_files)
        return upgrade.import_candidate(self.active, self.zip, self.sha, self.registry)

    def test_offline_candidate_is_immutable_and_declarations_do_not_qualify(self):
        before = self.active.read_bytes()
        candidate = self.candidate()
        plan = upgrade.plan_upgrade(self.active, candidate, ["linux-x64", "windows-x64"])
        self.assertEqual(plan["added_capabilities"], ["render"])
        self.assertIn("PLATFORM_CLOSURE_MISSING:windows-x64", plan["blockers"])
        self.assertFalse(plan["capabilities"]["linux-x64/render"]["qualified"])
        self.assertEqual(self.active.read_bytes(), before)
        self.assertEqual((self.active.parent / "packages/component.py").read_bytes(), V1)
        with self.assertRaisesRegex(upgrade.Rejected, "CANDIDATE_ALREADY_EXISTS"): self.candidate()

    def test_bad_hash_and_windows_unsafe_zip_are_rejected(self):
        with self.assertRaisesRegex(upgrade.Rejected, "ARCHIVE_INTEGRITY"):
            upgrade.import_candidate(self.active, self.zip, "0" * 64, self.registry)
        for name in ["../outside.py", "C:/private.py", "packages/CON.txt", "Packages/component.py"]:
            with self.subTest(name=name):
                sha = archive(self.zip, self.new, self.new_files, {name: b"unsafe"})
                with self.assertRaises(upgrade.Rejected):
                    upgrade.import_candidate(self.active, self.zip, sha, self.registry)
        self.assertFalse((self.root / "outside.py").exists())

    def test_manifest_cannot_self_assert_qualification_or_moving_version(self):
        for change in [{"qualified": True}, {"version": "latest"}, {"schema_version": "eeas.component-manifest.v2"}]:
            value = {**self.new, **change}
            with self.subTest(change=change), self.assertRaises(upgrade.Rejected): upgrade.ComponentManifest.parse(value)
        with self.assertRaisesRegex(upgrade.Rejected, "DUPLICATE_JSON_KEY"):
            upgrade.json_bytes(b'{"version":"v1","version":"v2"}')
        manifest = upgrade.ComponentManifest.parse(self.new)
        viewed = manifest.value
        viewed["version"] = "9.9.9"
        self.assertEqual(manifest.value["version"], "1.1.0")

    def test_stale_baseline_and_broken_interface_or_data_schema_do_not_prepare(self):
        candidate = self.candidate()
        self.active.write_bytes(upgrade.canonical({**self.old, "version": "1.0.1"}))
        with self.assertRaisesRegex(upgrade.Rejected, "STALE_BASELINE"):
            upgrade.plan_upgrade(self.active, candidate, ["linux-x64"])
        self.active.write_bytes(upgrade.canonical(self.old))
        changed = copy.deepcopy(self.new)
        changed["interfaces"]["format.v1"] = "b" * 64
        changed["data_schema"] = "original.data.v2"
        candidate = self.candidate(changed)
        plan = upgrade.plan_upgrade(self.active, candidate, ["linux-x64"], rollback=True)
        self.assertIn("INTERFACE_REVIEW_REQUIRED", plan["blockers"])
        self.assertIn("DATA_SCHEMA_MIGRATION_OR_RESTORE_REQUIRED", plan["blockers"])
        with self.assertRaisesRegex(upgrade.Rejected, "PLAN_BLOCKED"):
            upgrade.prepare_transaction(self.active, candidate, self.registry, ["linux-x64"], rollback=True)

    def test_real_receipts_qualify_exact_source_and_prepare_retains_old_bytes(self):
        candidate = self.candidate()
        receipt, run = actual_receipt(self.root / "actual-run", self.new, self.new_files)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        facts = upgrade.qualify_candidate(self.active, candidate, "normalize", "linux-x64", receipt)
        self.assertEqual(facts["source_kind"], "synthetic")
        binding = upgrade.json_bytes((receipt / "component-observation.json").read_bytes())
        binding["capability_id"] = "render"
        (receipt / "component-observation.json").write_bytes(upgrade.canonical(binding))
        upgrade.qualify_candidate(self.active, candidate, "render", "linux-x64", receipt)
        before = self.active.read_bytes()
        prepared = upgrade.prepare_transaction(self.active, candidate, self.registry, ["linux-x64"])
        transaction = upgrade.json_bytes(prepared.read_bytes())
        self.assertEqual(transaction["status"], "PREPARED")
        self.assertFalse(transaction["apply_allowed"])
        self.assertEqual(self.active.read_bytes(), before)
        retained = Path(transaction["retained_snapshot"])
        self.assertEqual((retained / "packages/component.py").read_bytes(), V1)
        self.assertEqual(upgrade.ComponentManifest.load(retained / "manifest.json").sha256, transaction["expected_active_sha256"])
        with self.assertRaises(FileExistsError):
            upgrade.prepare_transaction(self.active, candidate, self.registry, ["linux-x64"])

    def test_raw_stdout_frozen_source_and_scope_tampering_are_rejected(self):
        receipt, run = actual_receipt(self.root / "actual-run", self.new, self.new_files)
        self.assertEqual(run.returncode, 0)
        for name in ["stdout.txt", "snapshot/packages/component.py", "snapshot/manifest.json"]:
            path = receipt / name; original = path.read_bytes(); path.write_bytes(original + b"changed")
            with self.subTest(name=name), self.assertRaises(upgrade.Rejected):
                upgrade.receipt_facts(receipt, upgrade.ComponentManifest.parse(self.new), "normalize", "linux-x64")
            path.write_bytes(original)
        with self.assertRaisesRegex(upgrade.Rejected, "RECEIPT_SCOPE_MISMATCH"):
            upgrade.receipt_facts(receipt, upgrade.ComponentManifest.parse(self.old), "normalize", "linux-x64")

    def test_failed_actual_behavior_receipt_cannot_qualify(self):
        bad_code = V2.replace(b"return value.upper()", b"return value.lower()")
        manifest, files = materials("1.1.0", bad_code)
        receipt, run = actual_receipt(self.root / "failed-actual-run", manifest, files)
        self.assertNotEqual(run.returncode, 0)
        with self.assertRaisesRegex(upgrade.Rejected, "EXECUTED_RECEIPT_REQUIRED"):
            upgrade.receipt_facts(receipt, upgrade.ComponentManifest.parse(manifest), "normalize", "linux-x64")
        self.assertEqual(upgrade.json_bytes((receipt / "result.json").read_bytes())["status"], "FAIL")

    def test_platform_claim_and_company_scope_do_not_inherit_fixture_success(self):
        manifest = copy.deepcopy(self.new)
        manifest["capabilities"]["normalize"]["qualification_scope"] = "company_live"
        receipt, run = actual_receipt(self.root / "actual-run", manifest, self.new_files)
        self.assertEqual(run.returncode, 0)
        with self.assertRaisesRegex(upgrade.Rejected, "EXTERNAL_QUALIFICATION_REQUIRED"):
            upgrade.receipt_facts(receipt, upgrade.ComponentManifest.parse(manifest), "normalize", "linux-x64")
        with self.assertRaisesRegex(upgrade.Rejected, "RECEIPT_SCOPE_MISMATCH"):
            upgrade.receipt_facts(receipt, upgrade.ComponentManifest.parse(manifest), "normalize", "windows-x64")

    def test_new_permission_and_removed_regression_coverage_are_review_items(self):
        manifest = copy.deepcopy(self.new)
        manifest["capabilities"]["normalize"]["permissions"] = ["file:write"]
        manifest["capabilities"]["normalize"]["required_cases"] = ["original.Probe.test_new"]
        candidate = self.candidate(manifest)
        plan = upgrade.plan_upgrade(self.active, candidate, ["linux-x64"])
        self.assertIn("NEW_PERMISSION_APPROVAL_REQUIRED", plan["blockers"])
        self.assertIn("REGRESSION_CASE_COVERAGE_REMOVED:normalize", plan["blockers"])

    def test_retained_old_version_can_prepare_rollback_without_rewriting_active(self):
        current = write_package(self.root / "current-new", self.new, self.new_files)
        old_zip = self.root / "old-version.zip"
        old_sha = archive(old_zip, self.old, self.old_files)
        old_candidate = upgrade.import_candidate(current, old_zip, old_sha, self.registry)
        receipt, run = actual_receipt(self.root / "actual-old-rollback", self.old, self.old_files)
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        upgrade.qualify_candidate(current, old_candidate, "normalize", "linux-x64", receipt)
        before = current.read_bytes()
        result = upgrade.prepare_transaction(current, old_candidate, self.registry, ["linux-x64"], rollback=True)
        transaction = upgrade.json_bytes(result.read_bytes())
        self.assertEqual(transaction["plan"]["direction"], "rollback")
        self.assertEqual(transaction["plan"]["removed_capabilities"], ["render"])
        self.assertEqual(current.read_bytes(), before)
        self.assertEqual((Path(transaction["retained_snapshot"]) / "packages/component.py").read_bytes(), V2)

    def test_external_primary_metadata_is_discovery_not_new_capability_success(self):
        policy = {"schema_version": "eeas.component-source-policy.v1", "allowed_hosts": ["api.github.com", "www.python.org"],
                  "github_repositories": ["github/spec-kit"], "python_versions": ["3.10.11"]}
        seen = []
        def transport(url):
            seen.append(url)
            data = {"tag_name": "v1.2.3", "draft": False, "prerelease": False} if url.endswith("latest") else {"sha": "b" * 40}
            return upgrade.canonical(data), url
        result = upgrade.fetch_release(policy, "github", "github/spec-kit", transport=transport)
        self.assertEqual(result["commit"], "b" * 40)
        self.assertEqual(result["qualified_capabilities"], [])
        self.assertEqual(len(seen), 2)
        with self.assertRaisesRegex(upgrade.Rejected, "SOURCE_URL_FORBIDDEN"):
            upgrade.fetch_release(policy, "github", "github/spec-kit", transport=lambda url: (b'{}', "https://evil.invalid/"))
        with self.assertRaisesRegex(upgrade.Rejected, "PRIMARY_HOST_POLICY_REQUIRED"):
            upgrade.fetch_release({**policy, "allowed_hosts": ["evil.invalid"]}, "github", "github/spec-kit", transport=transport)
        result = upgrade.fetch_release(policy, "python", version="3.10.11", transport=lambda url: (b"official directory fixture", url))
        self.assertIsNone(result["commit"])
        self.assertEqual(result["status"], "DISCOVERED_ONLY")

    def test_cli_validate_and_plan_are_real_json_offline_commands(self):
        candidate = self.candidate()
        for argv in [["validate", str(self.active)], ["plan", "--active", str(self.active), "--candidate", str(candidate), "--platform", "windows-x64"]]:
            process = subprocess.run([sys.executable, str(ROOT / "scripts/component_upgrade.py"), *argv], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stdout + process.stderr)
            value = json.loads(process.stdout)
            self.assertIn(value["status"], ["VALID_DECLARATION", "BLOCKED"])


if __name__ == "__main__":
    unittest.main()
