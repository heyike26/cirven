"""Source-first search/approval faults; the inner fixtures never qualify company systems."""
import pytest

import campaign_sdk_fault_probes as probes
from test_campaign_probes import bound_source
from test_campaign_sdk_fault_probes import test_actual_source_first_then_complete_original_sdk_suite as execute_original


def test_search_approval_fixed_whole_files_are_disjoint_from_prior_pairs():
    previous = set().union(*(set(probes.APPROVED_FILES[name]) for name in (
        "fault-sdk-route", "fault-sdk-budget", "fault-sdk-session", "fault-sdk-images")))
    search = probes.fixed_sdk_suite("fault-search")
    runner = probes.fixed_sdk_suite("fault-approval-runner")
    search_files = {item["path"] for item in search["files"]}
    runner_files = {item["path"] for item in runner["files"]}
    assert search["expected_tests"] == 55 and runner["expected_tests"] == 24
    assert not search_files & runner_files
    assert not previous & (search_files | runner_files)
    assert "loopback" in search["limits"]
    assert "Windows" in runner["limits"]


@pytest.mark.parametrize("case_id,node,expected", [
    ("fault-search", "test_fault_search", 55),
    ("fault-approval-runner", "test_fault_approval_runner", 24),
])
def test_actual_source_first_then_original_search_or_approval_suite(bound_source, case_id, node, expected):
    execute_original(bound_source, case_id, node, expected)
