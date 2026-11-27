"""Original regression for source-first session/image faults; no model/Windows qualification."""
import pytest

import campaign_sdk_fault_probes as probes
from test_campaign_probes import bound_source
from test_campaign_sdk_fault_probes import test_actual_source_first_then_complete_original_sdk_suite as execute_original


def test_session_image_fixed_files_are_disjoint_from_previous_pair():
    previous = set(probes.APPROVED_FILES["fault-sdk-route"]) | set(probes.APPROVED_FILES["fault-sdk-budget"])
    session = probes.fixed_sdk_suite("fault-sdk-session")
    images = probes.fixed_sdk_suite("fault-sdk-images")
    session_files = {item["path"] for item in session["files"]}
    image_files = {item["path"] for item in images["files"]}
    assert session["expected_tests"] == 62 and images["expected_tests"] == 33
    assert not session_files & image_files
    assert not previous & (session_files | image_files)
    assert "no archive" in session["limits"]
    assert "company vision" in images["limits"]


@pytest.mark.parametrize("case_id,node,expected", [
    ("fault-sdk-session", "test_fault_sdk_session", 62),
    ("fault-sdk-images", "test_fault_sdk_images", 33),
])
def test_actual_source_first_then_original_session_or_image_suite(bound_source, case_id, node, expected):
    execute_original(bound_source, case_id, node, expected)
