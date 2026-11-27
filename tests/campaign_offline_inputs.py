"""Frozen offline material inputs; integrity is distinct from native/company qualification."""
import hashlib
import json
import os
from pathlib import Path
import sys

from campaign_probes import ROOT, ingest, load_binding
from campaign_fault_probes import FaultEvidence

sys.path.insert(0, str(ROOT / "scripts"))
from bundle_support import assert_no_links, digest_file, safe_name
from install_offline import verify
from offline_test_support import verify_test_kit

DECLARATION = ROOT / "tests/campaign_offline_inputs.json"


def declaration():
    assert_no_links(DECLARATION)
    if DECLARATION.stat().st_size > 512 * 1024:
        raise ValueError("offline declaration exceeds its limit")
    value = json.loads(DECLARATION.read_bytes())
    if (set(value) != {"schema", "target", "runtime", "optional_test", "fonts"}
            or value["schema"] != "eeas.offline-fault-inputs.v1"
            or value["target"] != ("windows-py310" if os.name == "nt" else "linux-py310")):
        raise ValueError("offline declaration has the wrong schema or actual host target")
    for key, profile in (("runtime", "offline-dependencies.json"), ("optional_test", "offline-test-dependencies.json")):
        selection = json.loads((ROOT / "profiles" / profile).read_bytes())
        if (set(value[key]) != {"relative_path", "manifest_sha256", "files_sha256"}
                or value[key]["relative_path"] != safe_name(selection["targets"][value["target"]])):
            raise ValueError("offline declaration differs from the frozen profile")
    return value


def dependency_references():
    value = declaration()
    references = [DECLARATION, ROOT / "profiles/offline-dependencies.json", ROOT / "profiles/offline-test-dependencies.json"]
    runtime = ROOT / value["runtime"]["relative_path"]
    references += [runtime / "manifest.json", runtime / "requirements-core.lock", runtime / "requirements-runtime.lock"]
    test = ROOT / value["optional_test"]["relative_path"]
    references += [test / "manifest.json", test / "requirements-test.lock", test / "playwright-registry.json"]
    references += [test / safe_name(name) for name in value["optional_test"]["files_sha256"] if name.startswith("licenses/")]
    references += [ROOT / safe_name(item["path"]) for item in value["fonts"]]
    for path in references:
        assert_no_links(path)
        if not path.is_file() or not path.resolve().is_relative_to(ROOT):
            raise ValueError("offline reference must be a regular contained file")
    return references


def observe_inputs():
    value = declaration()
    wheel_root = Path(os.environ.get("EEAS_TEST_WHEEL_ROOT", str(ROOT))).absolute()
    runtime = wheel_root / value["runtime"]["relative_path"]
    assert_no_links(runtime)
    if digest_file(runtime / "manifest.json") != value["runtime"]["manifest_sha256"]:
        raise ValueError("runtime manifest differs from the frozen declaration")
    observed_runtime = verify(runtime, value["target"])
    if observed_runtime["files_sha256"] != value["runtime"]["files_sha256"]:
        raise ValueError("runtime files differ from the frozen declaration")
    test = ROOT / value["optional_test"]["relative_path"]
    assert_no_links(test)
    if digest_file(test / "manifest.json") != value["optional_test"]["manifest_sha256"]:
        raise ValueError("optional test manifest differs from the frozen declaration")
    observed_test = verify_test_kit(test, value["target"])
    if observed_test["files_sha256"] != value["optional_test"]["files_sha256"]:
        raise ValueError("optional test files differ from the frozen declaration")
    for item in value["fonts"]:
        path = ROOT / safe_name(item["path"])
        assert_no_links(path)
        if digest_file(path) != item["sha256"] or path.stat().st_size != item["bytes"]:
            raise ValueError("original font material differs from the frozen declaration")
    return {"schema": "eeas.actual-offline-material-inputs.v1", "target": value["target"],
            "runtime_root": str(wheel_root), "test_root": str(ROOT),
            "runtime": value["runtime"], "optional_test": value["optional_test"], "fonts": value["fonts"],
            "environment_variables": {name: os.environ.get(name) for name in (
                "HOME", "USERPROFILE", "CLAUDE_CONFIG_DIR", "PIP_NO_INDEX", "PIP_CONFIG_FILE", "EEAS_TEST_WHEEL_ROOT")},
            "browser_revision": observed_test["browser"]["revision"], "video": observed_test["video"],
            "runtime_versions": observed_runtime["runtime"], "test_versions": observed_test["versions"],
            "native_windows": "NOT_QUALIFIED_BY_HASH" if os.name == "nt" else "BLOCKED",
            "actual_model_calls": 0, "os_isolation": "NOT_QUALIFIED"}


def run_offline_restore(client):
    import campaign_sdk_fault_probes as probes

    probes.fixed_sdk_suite("fault-offline-restore")
    captured = load_binding("fault-offline-restore")
    before = observe_inputs()  # A damaged dependency must block before any import/child side effect.
    probes.source_references(captured)
    evidence = FaultEvidence(captured)
    evidence.save("dependency-inputs-before.json", before)
    ingest(client, captured, evidence)
    probes.execute_sdk_suite("fault-offline-restore", captured, evidence)
    after = observe_inputs()
    if before != after:
        evidence.save("dependency-inputs-after.json", after)
        raise ValueError("offline dependency inputs changed during the original whole suite")
    evidence.save("dependency-inputs-after.json", after)
    with client.app.state.store.connect() as connection:
        executions = connection.execute("SELECT count(*) FROM agent_executions").fetchone()[0]
    assert executions == 0
    evidence.save("actual-outer-runtime.json", {"agent_executions": executions, "real_model_calls": 0,
                                               "inner_scope": "exited original whole suite, actual fresh install/CLI/restore and synthetic SDK wire"})
    evidence.observe()
