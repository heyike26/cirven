"""Original regression for explicit probes, never a public-project success count."""
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

import pytest

import campaign_native_probes as probes
from scripts import public_campaign as campaign
from test_campaign_probes import bound_source

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ("Original engineering source row with Unicode Ω and CRLF\r\n" * 190 +
            "REQ: Preserve the original final source constraint\r\n"
            "ACCEPT: Compare the original bytes and revision\r\n").encode()
ORDINARY = ("Original engineering source row with Unicode Ω and CRLF\r\n" * 1300 +
            "Retain the original ordinary-source tail constraint\r\n").encode()


def execute(bound, node):
    command = [sys.executable, "-m", "pytest", "tests/campaign_native_probes.py::" + node,
               "-q", "-ra", "-p", "scripts.public_campaign"]
    launcher = [sys.executable, "scripts/record_slice.py", "--acceptance",
                "Frozen original synthetic source drives actual API/SQLite/installed SDK readonly model-wire transport; selected original citation is exact, suggestion is not auto-confirmed, no company/model/GUI/network call or public success claim"]
    for path in [bound["binding_path"], *[bound["project"] / name for name in ("source.raw", "source.json", "license.raw")]]:
        launcher += ["--reference", path.relative_to(ROOT).as_posix()]
    launcher += ["E11-native-probe-" + uuid.uuid4().hex, "--", *command]
    result = subprocess.run(launcher, cwd=ROOT, capture_output=True, check=False,
                            env={**os.environ, "EEAS_CAMPAIGN_BINDING": str(bound["binding_path"])})
    targets = [line.removeprefix("Evidence: ") for line in result.stdout.decode().splitlines() if line.startswith("Evidence: ")]
    assert len(targets) == 1, result.stdout.decode() + result.stderr.decode()
    return result, ROOT / targets[0], command


def test_binding_required_before_any_api_or_sdk_submission(client, monkeypatch):
    monkeypatch.delenv("EEAS_CAMPAIGN_BINDING", raising=False)
    for case in ("extraction", "native-review"):
        with pytest.raises(ValueError, match="required"):
            probes.prepared(client, case)
    with client.app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM projects").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0


@pytest.mark.parametrize("case,node,raw", [
    ("extraction", "test_extraction", ORIGINAL),
    ("extraction", "test_extraction", ORDINARY),
    ("native-review", "test_native_review", ORIGINAL),
], ids=["extraction-markers", "extraction-ordinary-70k", "native-review-defaults"])
def test_actual_bound_source_drives_native_api_without_live_claim(bound_source, case, node, raw):
    bound_source["prepare"](case, raw)
    result, report, command = execute(bound_source, node)
    assert result.returncode == 0, result.stdout.decode() + result.stderr.decode()
    checked = campaign.checked_evidence(ROOT, report, bound_source["binding_path"], bound_source["source"],
                                        command, result.returncode, observation_required=True)
    assert checked["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    observation = json.loads((report / "campaign-observation.json").read_bytes())
    assert observation["source_kind"] == "synthetic" and observation["runtime_kind"] == "mock"
    assert observation["model_calls"] == 0 and observation["model_transport_source_kind"] == "synthetic"
    assert (report / "actual-original.bin").read_bytes() == raw
    quote = json.loads((report / "actual-selected-source-line.json").read_bytes())
    assert quote["quote"] == raw.decode().splitlines()[-1]
    if case == "extraction":
        actual = json.loads((report / "actual-native-extraction.json").read_bytes())
        assert actual["first"]["proposal"]["confirmed"] is False
        assert actual["omitted_citation"]["status"] == 422
        mock = json.loads((report / "actual-mock-extraction.json").read_bytes())
        assert mock["status"] == (422 if raw == ORDINARY else 200)
        if raw == ORDINARY:
            assert len(raw.decode()) > 70000
    else:
        actual = json.loads((report / "actual-native-review.json").read_bytes())
        assert actual["formal_run_after"]["execution_status"] == "AWAITING_APPROVAL"
        assert actual["formal_run_after"]["verification_status"] == "NOT_RUN"
        assert actual["completed"]["native_execution"]["execution_status"] == "RETURNED"


def test_selected_body_quote_never_silently_truncates_large_line(bound_source, monkeypatch, tmp_path):
    bound_source["prepare"]("extraction", ("# Original heading\n" + "x" * 1001 + "\n").encode())
    monkeypatch.setenv("EEAS_EVIDENCE_DIR", str(tmp_path))
    captured = probes.load_binding("extraction")
    with pytest.raises(ValueError, match="No bounded complete original body line"):
        probes.source_quote(captured, probes.NativeEvidence(captured))
    assert not (tmp_path / "actual-selected-source-line.json").exists()
