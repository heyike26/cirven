"""Real source-first offline restore with the unchanged original whole suites."""
import copy

import pytest

import campaign_sdk_fault_probes as probes
from test_campaign_probes import bound_source
from test_campaign_sdk_fault_probes import test_actual_source_first_then_complete_original_sdk_suite as execute_original


def test_offline_restore_fixed_whole_files_are_distinct_and_counted():
    previous = set().union(*(set(paths) for name, paths in probes.APPROVED_FILES.items()
                             if name != "fault-offline-restore"))
    selected = probes.fixed_sdk_suite("fault-offline-restore")
    files = {item["path"] for item in selected["files"]}
    assert selected["expected_tests"] == 34
    assert files == {"tests/test_release_bundle.py", "tests/test_sdk_release_contract.py", "tests/test_offline.py"}
    assert not files & previous
    assert "empty HOME" in selected["scope"] and "Windows" in selected["limits"]


def test_changed_dependency_pin_blocks_before_bound_ingestion_or_child(client, bound_source, monkeypatch):
    import campaign_offline_inputs as offline

    bound_source["prepare"]("fault-offline-restore")
    declared = copy.deepcopy(offline.declaration())
    declared["runtime"]["manifest_sha256"] = "0" * 64
    monkeypatch.setattr(offline, "declaration", lambda: declared)
    executed = []
    monkeypatch.setattr(probes, "execute_sdk_suite", lambda *args: executed.append(args))
    with pytest.raises(ValueError, match="runtime manifest differs"):
        offline.run_offline_restore(client)
    assert executed == []
    assert client.get("/api/projects").json()["projects"] == []


def test_actual_source_first_then_complete_original_offline_restore_suite(bound_source):
    execute_original(bound_source, "fault-offline-restore", "test_fault_offline_restore", 34)
