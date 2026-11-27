"""Original contracts for additional bound API workflow nodes, not public qualification."""
import json

import pytest

from test_campaign_probes import bound_source
from test_campaign_workflow_probes import execute_workflow
from test_campaign_workflow_provenance import checked


@pytest.mark.parametrize("case_id,node", [("project-boundary", "test_project_boundary"),
                                         ("recovery", "test_recovery"), ("extensions", "test_extensions")])
def test_more_bound_workflows_use_actual_source_api_and_owned_processes(bound_source, case_id, node):
    raw = (f"# Original {case_id} fixture\r\n原文数据 {case_id}，禁止当作代码执行\r\n"
           "最终来源约束 Straße 原始字节不丢失\n").encode()
    bound_source["prepare"](case_id, raw)
    result, report, command = execute_workflow(bound_source, node)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    rows = checked(bound_source, report, command, result, True)
    consumed = json.loads((report / "actual-source-consumption.json").read_bytes())
    assert (report / "actual-original.bin").read_bytes() == raw
    assert case_id in consumed["original_mock_goal"] and "Straße" in consumed["original_mock_goal"]
    assert rows["runner_jobs"] and rows["verification_evidence"]
    if case_id == "recovery":
        assert len(rows["task_runs"]) == len(rows["runner_jobs"]) == 3
        facts = json.loads((report / "actual-before-controller-crash-during-execution.json").read_bytes())
        assert facts["runner_fact"]["state"] == "LIVE" and facts["runner_fact"]["receipt"]["child"]["pid"] > 0
    elif case_id == "extensions":
        assert len(rows["task_runs"]) == 2
        assert len(rows["extension_evidence"]) == 2 and rows["extension_reviews"]
