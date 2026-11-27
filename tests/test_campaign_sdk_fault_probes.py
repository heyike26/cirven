"""Original synthetic regression of explicit route/budget probes; not public-project certification."""
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import uuid

import pytest

import campaign_sdk_fault_probes as probes
from scripts import public_campaign as campaign
from test_campaign_probes import bound_source


def test_fixed_whole_files_are_disjoint_and_keep_original_assertions():
    route = probes.fixed_sdk_suite("fault-sdk-route")
    budget = probes.fixed_sdk_suite("fault-sdk-budget")
    assert route["expected_tests"] == 41 and budget["expected_tests"] == 33
    assert not {item["path"] for item in route["files"]} & {item["path"] for item in budget["files"]}
    assert "NOT_NATIVE" in route["limits"]
    with pytest.raises(ValueError, match="approved SDK"):
        probes.fixed_sdk_suite("fault-documents")


@pytest.mark.parametrize("attack", ["whole_file", "file_sha", "bool_count", "wrong_count", "schema"])
def test_changed_or_unreviewed_plan_is_rejected_without_modifying_original_tests(tmp_path, monkeypatch, attack):
    plan = json.loads(probes.PLAN.read_bytes())
    suite = plan["cases"]["fault-sdk-route"]
    if attack == "whole_file": suite["files"][0]["path"] = "tests/test_sdk_install.py"
    elif attack == "file_sha": suite["files"][0]["sha256"] = "0" * 64
    elif attack == "bool_count": suite["expected_tests"] = True
    elif attack == "wrong_count": suite["expected_tests"] += 1
    else: plan["schema"] = "unsupported"
    path = tmp_path / "original-mutated-plan.json"
    path.write_bytes(campaign.encode(plan))
    monkeypatch.setattr(probes, "PLAN", path)
    with pytest.raises(ValueError): probes.fixed_sdk_suite("fault-sdk-route")


def test_source_fault_blocks_suite_before_any_child_or_document(client, bound_source, monkeypatch):
    bound_source["prepare"]("fault-sdk-route")
    (bound_source["project"] / "source.raw").write_bytes(b"original mutation")
    called = []
    def forbidden(*args): called.append(True); raise AssertionError("not allowed after invalid source")
    monkeypatch.setattr(probes, "execute_sdk_suite", forbidden)
    with pytest.raises(ValueError, match="SHA"):
        probes.run_sdk_boundary(client, "fault-sdk-route")
    assert called == [] and client.get("/api/projects").json()["projects"] == []


def test_sidecar_and_selected_original_references_are_real_and_mutation_rejected(bound_source):
    bound_source["prepare"]("fault-sdk-budget")
    source, project, binding = bound_source["source"], bound_source["project"], bound_source["binding"]
    raw = (project / "source.raw").read_bytes()
    text, commit, origin = raw.decode(), "a" * 40, "https://github.com/Original/Fixture"
    sidecar = project / "provenance/sidecar.json"
    original = sidecar.parent / "original/docs/original.md"
    original.parent.mkdir(parents=True)
    original.write_bytes(raw)
    span = campaign.provenance_span(text, 0, len(text))
    mapping = {"schema": campaign.PROVENANCE_SCHEMA,
               "parent_archive": {"sha256": "b" * 64, "origin": origin, "commit_sha": commit,
                                  "archive_url": "https://codeload.github.com/Original/Fixture/zip/" + commit},
               "material": {"sha256": campaign.digest(raw), "byte_length": len(raw), "characters": len(text)},
               "selected": [{"path": "original/docs/original.md", "source_path": "docs/original.md",
                             "archive_member": "Fixture-" + commit + "/docs/original.md",
                             "source_url": origin + "/blob/" + commit + "/docs/original.md",
                             "sha256": campaign.digest(raw), "byte_length": len(raw), "characters": len(text),
                             "body": span, "original": span}], "synthetic_ranges": [],
               "conversion": {"version": "original-sdk-fault-fixture-v1", "input_manifest_sha256": "c" * 64},
               "source_authentication": "DECLARED_NOT_INDEPENDENTLY_VERIFIED"}
    sidecar.write_bytes(campaign.encode(mapping))
    source.update(origin=origin, revision=commit,
                  provenance={"schema": campaign.PROVENANCE_SCHEMA, "path": "provenance/sidecar.json",
                              "sha256": campaign.digest(sidecar.read_bytes())})
    (project / "source.json").write_bytes(campaign.encode(source))
    binding.update(source_json_sha256=campaign.digest((project / "source.json").read_bytes()),
                   provenance_path=sidecar.relative_to(probes.ROOT).as_posix())
    bound_source["binding_path"].write_bytes(campaign.encode(binding))
    captured = probes.load_binding("fault-sdk-budget")
    references = probes.source_references(captured)
    assert sidecar in references and original in references and len(references) == 7
    assert campaign.digest(original.read_bytes()) == mapping["selected"][0]["sha256"]
    original.write_bytes(raw + b"original tamper")
    with pytest.raises(ValueError, match="original checksum"):
        probes.source_references(captured)


@pytest.mark.parametrize("case_id,node,expected", [
    ("fault-sdk-route", "test_fault_sdk_route", 41),
    ("fault-sdk-budget", "test_fault_sdk_budget", 33),
])
def test_actual_source_first_then_complete_original_sdk_suite(bound_source, case_id, node, expected):
    bound_source["prepare"](case_id)
    root, binding = probes.ROOT, bound_source["binding_path"]
    references = [binding, *[bound_source["project"] / name for name in ("source.raw", "source.json", "license.raw")], probes.PLAN]
    command = [sys.executable, "-m", "pytest", "tests/campaign_sdk_fault_probes.py::" + node, "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Actual synthetic regression: import bound original text then execute one fixed SDK fault suite; "
                "original assertion SHA, nested trusted JUnit, raw/freeze retained, no model/company qualification"]
    for path in references:
        launcher += ["--reference", path.relative_to(root).as_posix()]
    launcher += ["E11-sdk-fault-probe-" + uuid.uuid4().hex, "--", *command]
    actual = subprocess.run(launcher, cwd=root, capture_output=True, check=False, env=dict(os.environ))
    matches = [line.removeprefix("Evidence: ") for line in actual.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(matches) == 1
    evidence = root / matches[0]
    outer = os.environ.get("EEAS_EVIDENCE_DIR")
    if outer:
        target = Path(outer) / "nested-sdk-fault" / case_id
        target.mkdir(parents=True)
        shutil.copytree(evidence, target / "actual-probe")
        (target / "launcher-stdout.txt").write_bytes(actual.stdout)
        (target / "launcher-stderr.txt").write_bytes(actual.stderr)
        campaign.put(target / "command.json", {"launcher": launcher, "command": command, "exit_code": actual.returncode})
    assert actual.returncode == 0, "See original outer/child evidence at " + str(evidence)
    checked = campaign.checked_evidence(root, evidence, binding, bound_source["source"], command,
                                        actual.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    assert (evidence / "actual-original.bin").read_bytes() == (bound_source["project"] / "source.raw").read_bytes()
    suite = json.loads((evidence / "actual-sdk-fault-suite.json").read_bytes())
    assert suite["actual_counts"] == {"executed": expected, "failed": 0, "skipped": 0}
    assert suite["project_material_source_kind"] == "synthetic" and suite["fault_input_source_kind"] == "synthetic"
    assert suite["runtime_kind"] == "mock" and suite["subprocess_returned"] is True
    observation = json.loads((evidence / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["model_calls"] == 0
    assert observation["runtime_kind"] == "mock" and observation["fault_input_source_kind"] == "synthetic"
    assert json.loads((evidence / "actual-outer-runtime.json").read_bytes())["agent_executions"] == 0
    assert all((evidence / item["path"]).read_bytes() == b"" for item in suite["preserved_empty_raw_files"])
    for path in references:
        assert checked["frozen_input_sha256"][path.relative_to(root).as_posix()] == campaign.digest(path.read_bytes())
