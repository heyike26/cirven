"""Original transformed-source contracts; no public authentication or model claim."""
import base64
import json
import shutil

import pytest

from scripts import public_campaign as campaign
from test_campaign_probes import bound_source
from test_campaign_workflow_probes import execute_workflow
from test_public_campaign_provenance import inputs, ORIGIN, COMMIT


def mapped(bound_source, inputs):
    material, _, sidecar, definition = inputs
    bound_source["prepare"]("requirements", material.read_bytes())
    target = bound_source["project"] / "provenance"
    shutil.copytree(sidecar.parent, target)
    copied = target / sidecar.name
    assert copied.read_bytes() == sidecar.read_bytes()
    record = bound_source["source"]
    record.update(origin=ORIGIN, revision=COMMIT,
                  provenance={"schema": definition["schema"], "path": "provenance/sidecar.json",
                              "sha256": campaign.digest(sidecar.read_bytes())})
    metadata = bound_source["project"] / "source.json"
    metadata.write_bytes(campaign.encode(record))
    binding = bound_source["binding"]
    binding.update(project=record, source_json_sha256=campaign.digest(metadata.read_bytes()),
                   provenance_path=copied.relative_to(campaign.ROOT).as_posix())
    bound_source["binding_path"].write_bytes(campaign.encode(binding))
    return definition, copied


def checked(source, report, command, result, observation):
    value = campaign.checked_evidence(campaign.ROOT, report, source["binding_path"], source["source"],
                                      command, result.returncode, observation_required=observation)
    assert value["test_counts"] == {"executed": 1, "failed": 0 if observation else 1, "skipped": 0}
    rows = json.loads((report / "actual-sqlite-rows.json").read_bytes())
    assert rows["agent_executions"] == []
    return rows


def test_mapped_requirements_use_whole_original_unicode_crlf_and_eof_line(bound_source, inputs):
    definition, sidecar = mapped(bound_source, inputs)
    result, report, command = execute_workflow(bound_source, "test_requirements")
    assert result.returncode == 0, result.stdout.decode()
    rows = checked(bound_source, report, command, result, True)
    source = json.loads((report / "actual-source-consumption.json").read_bytes())
    lines = source["source_lines"]
    assert lines[0]["physical_line"] == "真实正文 😀\r\n"
    assert lines[-1]["physical_line"] == "真实末尾"
    assert lines[-1]["original_source"]["generated_terminal_separator_excluded"] is True
    assert "Generated audit metadata" not in source["original_mock_goal"]
    assert "GENERATED WRAPPER" not in source["original_mock_goal"]
    raw = (bound_source["project"] / "source.raw").read_bytes()
    for line in lines:
        original = line["original_source"]
        item = next(x for x in definition["selected"] if x["source_path"] == original["path"])
        actual = (sidecar.parent / item["path"]).read_bytes()
        assert actual[original["byte_start"]:original["byte_end"]] == line["physical_line"].encode()
        assert actual.decode()[original["char_start"]:original["char_end"]] == line["physical_line"]
        assert actual.decode().splitlines(keepends=True)[original["line"] - 1] == line["physical_line"]
        assert raw[line["byte_start"]:line["byte_end"]] == line["physical_line"].encode()
        assert line["line"] == item["body"]["line_start"] + original["line"] - 1
    initial = json.loads(rows["requirement_versions"][0]["payload"])
    for item, line in zip(initial["requirements"], lines):
        ref = item["source_refs"][0]
        assert ref["line_start"] == ref["line_end"] == line["line"]
        assert ref["sha256"] == source["parentRawSHA"] and ref["revision"] == 1
    assert base64.b64decode(rows["document_versions"][0]["raw_bytes"]["raw_base64"]) == raw


@pytest.mark.parametrize("fault", ["original_changed", "original_not_frozen", "sidecar_not_bound"])
def test_provenance_fault_has_no_requirement_task_or_native_admission(bound_source, inputs, fault):
    definition, sidecar = mapped(bound_source, inputs)
    if fault == "original_changed":
        original = sidecar.parent / definition["selected"][0]["path"]
        original.write_bytes(original.read_bytes() + b"Original test-only tamper")
    elif fault == "original_not_frozen":
        bound_source["omit_original_references"] = True
    else:
        binding = bound_source["binding"]
        binding.pop("provenance_path")
        bound_source["binding_path"].write_bytes(campaign.encode(binding))
    result, report, command = execute_workflow(bound_source, "test_requirements")
    assert result.returncode == 1
    assert not (report / "campaign-observation.json").exists()
    rows = checked(bound_source, report, command, result, False)
    assert rows["requirement_sets"] == rows["tasks"] == rows["task_runs"] == rows["runner_jobs"] == []


def test_unmapped_collector_wrapper_is_not_a_business_source(bound_source):
    raw = b"EEAS PUBLIC AUDIT MATERIAL\nGenerated audit labels only\n"
    bound_source["prepare"]("requirements", raw)
    result, report, command = execute_workflow(bound_source, "test_requirements")
    assert result.returncode == 1 and "generated wrapper is not original content" in result.stdout.decode()
    assert not (report / "campaign-observation.json").exists()
    rows = checked(bound_source, report, command, result, False)
    assert rows["requirement_sets"] == rows["tasks"] == rows["task_runs"] == rows["runner_jobs"] == []
