import hashlib
import json
from urllib.parse import quote

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_documents import project
from test_tasks import inputs
from test_worker import submitted, until


def test_frozen_budget_unicode_artifact_evidence_download_and_integrity(client, tmp_path):
    pid = project(client)
    doc, req, body = inputs(client, pid)
    filename, goal = "中文 结果.txt", "原创 中文候选"
    invalid = {**body, "allowed_write_paths": ["Candidate.txt", "candidate.txt"]}
    assert client.post(f"/api/projects/{pid}/tasks", json=invalid).status_code == 422
    task, run = submitted(client, pid, {**body, "goal": goal, "allowed_write_paths": [filename], "max_steps": 4, "max_repairs": 1}, "result-api")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    worker.start()
    try:
        until(service, pid, run["id"], {"RETURNED"})
        route = f"/api/projects/{pid}/runs/{run['id']}"
        before = client.get(route + "/results").json()
        assert before["evidence"] is None
        assert before["usage"] == {"tokens": None, "cost": None}
        assert before["budget"]["max_steps"] == 4 and before["budget"]["max_repairs"] == 1
        expected = (goal + "\n").encode("utf-8")
        assert before["artifact_hash"][filename] == hashlib.sha256(expected).hexdigest()
        evidence = client.post(route + "/verify").json()
        assert evidence["payload"]["result"] == "PASSED"
        summary = client.get(route + "/results").json()
        assert summary["evidence"]["association_status"] == "CURRENT"
        artifact_route = route + "/artifacts/" + quote(filename)
        assert client.get(artifact_route).content == expected
        download = client.get(f"/api/projects/{pid}/evidence/{evidence['payload']['evidence_id']}/download")
        assert download.status_code == 200 and "attachment" in download.headers["content-disposition"]
        assert download.json()["payload"] == evidence["payload"]
        (tmp_path / run["workspace"] / filename).write_bytes(b"changed")
        assert client.get(route + "/results").json()["evidence"]["association_status"] == "INTEGRITY_ERROR"
        assert client.get(artifact_route).status_code == 422
    finally:
        worker.stop()
