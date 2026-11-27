"""Execute the original source-bound session probe, without public/live claims."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

from scripts import public_campaign as campaign
from test_campaign_probes import bound_source

ROOT = Path(__file__).resolve().parents[1]


def test_actual_source_bound_native_session_preserves_scope_budget_and_formal_runs(bound_source):
    raw = ("Original source with Unicode Ω and exact CRLF\r\n" * 110 +
           "Original physical source tail for readonly session controls\r\n").encode()
    bound_source["prepare"]("native-session", raw)
    command = [sys.executable, "-m", "pytest", "tests/campaign_native_session_probes.py::test_native_session",
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
        "Original synthetic bound source drives actual installed SDK JSON-wire session controls through real API/SQLite. Current head/scope/cost required; immutable parents/formal runs and true shared budget; explicit resume/fork/queued steer/cancel confirmation; unknown/expired/cancelled/source/profile changes refuse new admission. All costs/transcripts are original fixtures, zero public/company/model/GUI/network qualification."]
    for path in [bound_source["binding_path"], *[bound_source["project"] / name for name in ("source.raw", "source.json", "license.raw")]]:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-native-session-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(bound_source["binding_path"])},
                            capture_output=True, check=False)
    targets = [line.removeprefix("Evidence: ") for line in result.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(targets) == 1, result.stdout.decode() + result.stderr.decode()
    report = ROOT / targets[0]
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"],
                                        command, result.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    assert (report / "actual-original.bin").read_bytes() == raw
    observation = json.loads((report / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["runtime_kind"] == "mock"
    assert observation["model_calls"] == 0 and observation["model_transport_source_kind"] == "synthetic"
    budget = json.loads((report / "actual-known-budget-before-final-call.json").read_bytes())
    assert budget["spent_usd"] == "0.007" and budget["remaining_usd"] == "0.003"
    unknown = json.loads((report / "actual-unknown-cost-denials.json").read_bytes())
    assert json.loads(unknown["parent"]["native_result"]["execution"]["result"])["total_cost_usd"] is None
    queued = json.loads((report / "actual-queued-steer.json").read_bytes())
    assert queued["semantics"] == "NATIVE_QUERY_QUEUED_FOLLOWUP_NOT_INSTANT_MANIPULATION"
    assert len([message for message in queued["writes"] if message["type"] == "user"]) == 2
    records = json.loads((report / "actual-session-durable-records.json").read_bytes())
    assert len(records["task_native_reviews"]) == 8  # Four known + unknown + two shared steer + cancel.
    assert len(records["agent_scopes"]) == 9  # Plus the explicitly separate readonly SDK expiry root.
    expiry = json.loads((report / "actual-real-clock-expiry.json").read_bytes())
    assert expiry["request"]["session_ttl_seconds"] == 1
    assert expiry["resume_and_fork_denied"] == "AUTHORIZATION_EXPIRED"
    assert 0 < expiry["actual_sleep_seconds"] <= 1.10
