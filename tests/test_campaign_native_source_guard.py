"""Original fail-closed microcase for the native probe's source-selection helper."""
import json
import os
from pathlib import Path

import campaign_native_probes as probes
from test_campaign_probes import bound_source


def test_magic_composite_without_provenance_never_becomes_native_business_quote(client, bound_source, tmp_path, monkeypatch):
    raw = (b"EEAS PUBLIC AUDIT MATERIAL\n# GENERATED WRAPPER\n"
           b"Generated audit labels are not original business requirements\n")
    bound_source["prepare"]("native-review", raw)
    selected = os.environ.get("EEAS_EVIDENCE_DIR")
    if not selected:
        monkeypatch.setenv("EEAS_EVIDENCE_DIR", str(tmp_path))
    error, unexpected_quote = None, None
    try:
        prepared = probes.prepared(client, "native-review")
        unexpected_quote = prepared[5]
    except ValueError as failure:
        error = str(failure)
    with client.app.state.store.connect() as db:
        counts = {name: db.execute("SELECT count(*) FROM " + name).fetchone()[0] for name in
                  ("requirement_sets", "tasks", "task_runs", "runner_jobs", "task_native_reviews", "agent_executions")}
    report = Path(os.environ["EEAS_EVIDENCE_DIR"]) / "actual-native-unmapped-wrapper.json"
    with report.open("x", encoding="utf-8") as stream:
        json.dump({"actual_error": error, "unexpected_quote": unexpected_quote, "actual_db_counts": counts,
                   "source_kind": "synthetic", "real_model_calls": 0}, stream, ensure_ascii=False, indent=2)
    assert all(count == 0 for count in counts.values())
    assert error and "Composite source requires frozen original-body provenance" in error, (
        "Native source helper accepted generated wrapper metadata as an original business quote", unexpected_quote)
    assert not (report.parent / "actual-selected-source-line.json").exists()
