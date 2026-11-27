"""Original real-API / ZIP / recovery cases; no GUI, model or native Office."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import time
from threading import Barrier
from unittest.mock import patch

from pptx import Presentation
import pytest
import httpx

from apps.api.config import Settings
from apps.api.main import create_app
from fastapi.testclient import TestClient
from packages.domain import report_artifacts as reports
from packages.domain.pptx_export import PptxExportError
from test_pptx_export import members, template


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def project(client, name="原创报告项目"):
    response = client.post("/api/projects", json={"name": name})
    assert response.status_code == 201, response.text
    return response.json()["id"]


def document(client, pid, name="原创资料.md", raw="原创说明\n保留失败和验证边界。\n".encode()):
    response = client.post(f"/api/projects/{pid}/documents", files={"file": (name, raw)},
                           data={"source": "synthetic:original-report-fixture"})
    assert response.status_code == 201, response.text
    value = response.json()
    return {"kind": "document", "project_id": pid, "document_id": value["id"],
            "revision": value["revision"], "sha256": value["sha256"]}


def payload(ref, key="original_report"):
    return {"idempotency_key": key, "filename": "原创结果报告.pptx",
            "report": {"schema_version": "eeas.report-deck.v1", "title": "原创报告",
                       "subtitle": "只有实际证据", "source_refs": [ref],
                       "sections": [{"title": "接口与验证", "paragraphs": ["记录真实结果及失败。"],
                                     "bullets": ["Windows/Office 尚未实测"]}]}}


def endpoint(pid):
    return f"/api/projects/{pid}/report-artifacts"


def create(client, pid, body):
    return client.post(endpoint(pid), json=body)


@pytest.fixture(autouse=True)
def keep_original_inputs_and_actual_outputs(request, client):
    yield
    if not os.environ.get("EEAS_EVIDENCE_DIR"):
        return
    target = Path(os.environ["EEAS_EVIDENCE_DIR"]) / "fixtures" / re.sub(r"[^A-Za-z0-9_-]", "_", request.node.name)
    target.mkdir(parents=True, exist_ok=True)
    with client.app.state.store.connect() as connection:
        originals = list(connection.execute("SELECT document_id,revision,raw_bytes,sha256 FROM document_versions"))
    index = []
    for row in originals:
        name = f"{row['document_id']}-v{row['revision']}.original"
        (target / name).write_bytes(row["raw_bytes"])
        index.append({"path": name, "stored_sha256": row["sha256"], "actual_sha256": sha(row["raw_bytes"])})
    registry = client.app.state.settings.data_dir / "report-artifacts"
    if registry.exists() and not registry.is_symlink():
        def excluded(folder, names):
            return [name for name in names if (Path(folder) / name).is_symlink()]
        shutil.copytree(registry, target / "report-artifacts", ignore=excluded)
    (target / "original-input-index.json").write_text(json.dumps(index, indent=2) + "\n")


def test_real_api_generates_source_manifest_and_downloads_exact_editable_zip_bytes(client):
    pid = project(client)
    ref = document(client, pid)
    ref.update(line_start=1, line_end=2)
    response = create(client, pid, payload(ref))
    assert response.status_code == 201, response.text
    saved = response.json()
    assert saved["replayed"] is False
    manifest = saved["artifact_manifest"]
    assert manifest["schema_version"] == "eeas.artifact-manifest.v1"
    assert manifest["project_id"] == pid and manifest["revision"] == 1
    assert manifest["source_refs"] == [ref]
    assert manifest["template_ref"] is None
    assert manifest["runtime_kind"] == "none" and manifest["source_kind"] is None
    assert manifest["audit"]["native_office"] == "NOT_RUN"
    raw = client.get(manifest["download_url"])
    assert raw.status_code == 200
    assert raw.headers["content-type"] == reports.PPTX_MIME
    assert "filename*=UTF-8''" in raw.headers["content-disposition"]
    assert raw.headers["etag"] == '"' + manifest["sha256"] + '"'
    assert raw.headers["x-content-type-options"] == "nosniff"
    assert sha(raw.content) == manifest["sha256"] and len(raw.content) == manifest["byte_length"]
    deck = Presentation(BytesIO(raw.content))
    assert len(deck.slides) == 2 and deck.slides[0].shapes.title.text == "原创报告"
    assert ref["sha256"] in deck.slides[0].notes_slide.notes_text_frame.text
    deck.slides[1].shapes.title.text = "仍可编辑"
    revised = BytesIO(); deck.save(revised)
    assert Presentation(BytesIO(revised.getvalue())).slides[1].shapes.title.text == "仍可编辑"
    assert client.get(endpoint(pid)).json()["artifacts"] == [manifest]


def test_real_user_template_original_and_untouched_opc_payload_are_preserved(client):
    pid = project(client)
    body = payload(document(client, pid))
    original = template()
    ref = document(client, pid, "原创用户母版.pptx", original)
    body["template"] = {key: ref[key] for key in ("document_id", "revision", "sha256")}
    response = create(client, pid, body)
    assert response.status_code == 201, response.text
    manifest = response.json()["artifact_manifest"]
    raw = client.get(manifest["download_url"]).content
    assert manifest["template_ref"]["sha256"] == sha(original)
    assert manifest["traceability"]["template_sha256"] == sha(original)
    assert members(raw)["custom/original.xml"] == members(original)["custom/original.xml"]
    before, after = Presentation(BytesIO(original)), Presentation(BytesIO(raw))
    assert before.slides[0].slide_id == after.slides[0].slide_id
    assert [shape.shape_id for shape in before.slides[0].shapes] == [shape.shape_id for shape in after.slides[0].shapes]
    assert "用户原始备注必须保留。" in after.slides[0].notes_slide.notes_text_frame.text
    stored = client.app.state.store.document(pid, ref["document_id"], 1)
    assert stored["raw_bytes"] == original
    assert manifest["audit"]["native_office"] == "NOT_RUN"


def test_explicit_historical_pptx_template_survives_a_later_txt_revision(client):
    pid = project(client); body = payload(document(client, pid)); original = template()
    ref = document(client, pid, "original-template.pptx", original)
    updated = client.put(f"/api/projects/{pid}/documents/{ref['document_id']}",
                         files={"file": ("new-current-notes.txt", b"original later text revision")},
                         data={"expected_revision": "1"})
    assert updated.status_code == 200 and updated.json()["revision"] == 2, updated.text
    body["template"] = {key: ref[key] for key in ("document_id", "revision", "sha256")}
    response = create(client, pid, body)
    assert response.status_code == 201, response.text
    manifest = response.json()["artifact_manifest"]
    assert manifest["template_ref"]["revision"] == 1
    assert manifest["traceability"]["template_sha256"] == sha(original)
    assert members(client.get(manifest["download_url"]).content)["custom/original.xml"] == members(original)["custom/original.xml"]


def test_same_source_bound_retry_and_discarded_response_never_reexport_saved_artifact(client):
    pid = project(client); body = payload(document(client, pid))
    with patch.object(reports, "export_report_deck", wraps=reports.export_report_deck) as exporter:
        first = create(client, pid, body)
        # The caller can discard this response; durable registration is the
        # authority for the retry, rather than process-local response memory.
        assert first.status_code == 201
        second = create(client, pid, deepcopy(body))
        assert second.status_code == 200 and second.json()["replayed"] is True
        assert first.json()["artifact_manifest"] == second.json()["artifact_manifest"]
        assert exporter.call_count == 1
    assert len(client.get(endpoint(pid)).json()["artifacts"]) == 1


def test_restart_replays_registered_bytes_without_export_or_schema_hole(client):
    pid = project(client); body = payload(document(client, pid))
    original = create(client, pid, body).json()["artifact_manifest"]
    settings = Settings(client.app.state.settings.data_dir)
    with TestClient(create_app(settings), base_url=settings.origin) as restarted:
        csrf = restarted.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
        restarted.headers.update({"Origin": settings.origin, "X-EEAS-CSRF": csrf})
        with patch.object(reports, "export_report_deck", side_effect=AssertionError("must not regenerate")):
            replay = create(restarted, pid, body)
            assert replay.status_code == 200, replay.text
            assert replay.json()["artifact_manifest"] == original
            assert sha(restarted.get(original["download_url"]).content) == original["sha256"]
        with restarted.app.state.store.connect() as connection:
            migrations = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert [int(name.split("_")[0]) for name in migrations] == list(range(1, len(migrations) + 1))


def test_concurrent_api_requests_generate_only_one_registered_artifact(client):
    pid = project(client); body = payload(document(client, pid)); barrier = Barrier(2)
    def submit():
        barrier.wait(timeout=5)
        return create(client, pid, deepcopy(body))
    with patch.object(reports, "export_report_deck", wraps=reports.export_report_deck) as exporter:
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda _: submit(), range(2)))
        assert sorted(response.status_code for response in responses) == [200, 201], [r.text for r in responses]
        assert exporter.call_count == 1
    manifests = [response.json()["artifact_manifest"] for response in responses]
    assert manifests[0] == manifests[1]
    assert len(client.get(endpoint(pid)).json()["artifacts"]) == 1


def test_independent_processes_share_durable_idempotency_not_an_in_memory_lock(client):
    pid = project(client); body = payload(document(client, pid)); data = client.app.state.settings.data_dir
    script = """import json,sys,time
from pathlib import Path
from packages.domain.store import Store
from packages.domain import report_artifacts as module
directory=Path(sys.argv[1]); actual=module.export_report_deck
def observed(*args,**kwargs):
    with (directory/'actual-export-calls.txt').open('a') as stream: stream.write('actual export\\n')
    time.sleep(.15)
    return actual(*args,**kwargs)
module.export_report_deck=observed
service=module.ReportArtifacts(Store(directory/'eeas.sqlite3'),directory)
result=service.create(sys.argv[2],module.ReportArtifactInput.model_validate(json.loads(sys.argv[3])))
print(json.dumps({'id':result['artifact_manifest']['id'],'sha256':result['artifact_manifest']['sha256'],'replayed':result['replayed']}))
"""
    argv = [sys.executable, "-c", script, str(data), pid, json.dumps(body, ensure_ascii=False)]
    children = [subprocess.Popen(argv, cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                for _ in range(2)]
    results = []
    for child in children:
        stdout, stderr = child.communicate(timeout=20)
        assert child.returncode == 0, stderr
        results.append(json.loads(stdout))
    assert sorted(item["replayed"] for item in results) == [False, True]
    assert len({item["id"] for item in results}) == len({item["sha256"] for item in results}) == 1
    assert (data / "actual-export-calls.txt").read_text().splitlines() == ["actual export"]
    assert len(client.get(endpoint(pid)).json()["artifacts"]) == 1


def test_real_tcp_disconnect_after_export_starts_replays_saved_output_once(tmp_path):
    data = tmp_path / "tcp-data"
    data.mkdir()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0)); port = probe.getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    script = """import sys,time
from pathlib import Path
import uvicorn
from apps.api.main import create_app
from apps.api.config import Settings
from packages.domain import report_artifacts as module
directory=Path(sys.argv[1]); actual=module.export_report_deck
def observed(*args,**kwargs):
    (directory/'export-entered').write_text('actual export entered')
    deadline=time.monotonic()+20
    while not (directory/'release-export').is_file():
        if time.monotonic()>deadline: raise module.PptxExportError('original controlled wait timed out')
        time.sleep(.01)
    with (directory/'actual-export-calls.txt').open('a') as stream: stream.write('actual export\\n')
    return actual(*args,**kwargs)
module.export_report_deck=observed
uvicorn.run(create_app(Settings(directory,port=int(sys.argv[2]))),host='127.0.0.1',port=int(sys.argv[2]),log_level='warning')
"""
    stdout_path, stderr_path = data / "server-stdout.txt", data / "server-stderr.txt"
    with stdout_path.open("w") as stdout, stderr_path.open("w") as stderr:
        process = subprocess.Popen([sys.executable, "-c", script, str(data), str(port)],
            cwd=Path(__file__).parents[1], stdout=stdout, stderr=stderr)
        try:
            with httpx.Client(base_url=origin, trust_env=False, timeout=5) as network:
                deadline = time.monotonic() + 10
                while True:
                    try:
                        if network.get("/health").status_code == 200: break
                    except httpx.HTTPError:
                        pass
                    assert time.monotonic() < deadline, "controlled original API server did not start"
                    time.sleep(.02)
                token = network.get("/api/bootstrap", headers={"X-EEAS-Bootstrap": "1"}).json()["csrf_token"]
                session = network.cookies.get("eeas_session")
                network.headers.update({"Origin": origin, "X-EEAS-CSRF": token})
                pid = project(network, "原创TCP断连报告项目")
                ref = document(network, pid)
                body = payload(ref, "tcp_disconnect_original")
                raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
                wire_headers = (f"POST {endpoint(pid)} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\n"
                    f"Origin: {origin}\r\nX-EEAS-CSRF: {token}\r\nContent-Type: application/json\r\n"
                    f"Cookie: eeas_session={session}\r\n"
                    f"Content-Length: {len(raw_body)}\r\nConnection: close\r\n\r\n").encode("ascii")
                with socket.create_connection(("127.0.0.1", port), timeout=5) as connection:
                    connection.sendall(wire_headers + raw_body)
                    deadline = time.monotonic() + 10
                    while not (data / "export-entered").is_file():
                        assert time.monotonic() < deadline, "request did not reach the real export path"
                        time.sleep(.01)
                    # Close the real TCP response channel after the actual API
                    # entered generation, before it can publish/send success.
                    connection.shutdown(socket.SHUT_RDWR)
                (data / "release-export").write_text("caller disconnected; complete actual export")
                deadline = time.monotonic() + 10
                while True:
                    rows = network.get(endpoint(pid)).json()["artifacts"]
                    if rows: break
                    assert time.monotonic() < deadline, "disconnected export was not durably registered"
                    time.sleep(.02)
                retry = network.post(endpoint(pid), json=body)
                assert retry.status_code == 200 and retry.json()["replayed"] is True, retry.text
                manifest = retry.json()["artifact_manifest"]
                actual_output = network.get(manifest["download_url"])
                assert actual_output.status_code == 200 and sha(actual_output.content) == manifest["sha256"]
                assert (data / "actual-export-calls.txt").read_text().splitlines() == ["actual export"]
                if os.environ.get("EEAS_EVIDENCE_DIR"):
                    evidence = Path(os.environ["EEAS_EVIDENCE_DIR"]) / "fixtures" / "real-tcp-disconnect"
                    evidence.mkdir(parents=True)
                    (evidence / "original-report-input.json").write_text(json.dumps(body, ensure_ascii=False, indent=2))
                    (evidence / "actual-output.pptx").write_bytes(actual_output.content)
                    (evidence / "receipt.json").write_text(json.dumps({"response_channel_closed_before_publish": True,
                        "actual_export_calls": 1, "retry_status": retry.status_code,
                        "artifact_manifest": manifest}, ensure_ascii=False, indent=2))
        finally:
            process.terminate(); process.wait(timeout=5)
    if os.environ.get("EEAS_EVIDENCE_DIR"):
        for path in (stdout_path, stderr_path):
            shutil.copyfile(path, Path(os.environ["EEAS_EVIDENCE_DIR"]) / path.name)


@pytest.mark.parametrize("change", ["source_sha", "title", "filename", "template"])
def test_idempotency_key_rejects_changed_exact_sources_or_input(client, change):
    pid = project(client); body = payload(document(client, pid))
    assert create(client, pid, body).status_code == 201
    changed = deepcopy(body)
    if change == "source_sha": changed["report"]["source_refs"][0]["sha256"] = "a" * 64
    elif change == "title": changed["report"]["title"] = "修改后的报告"
    elif change == "filename": changed["filename"] = "another.pptx"
    else: changed["template"] = {"document_id": "missing", "revision": 1, "sha256": "b" * 64}
    response = create(client, pid, changed)
    assert response.status_code == 409 and "IDEMPOTENCY_CONFLICT" in response.text
    assert len(client.get(endpoint(pid)).json()["artifacts"]) == 1


@pytest.mark.parametrize("attack", ["source_project", "source_id", "template", "download", "manifest"])
def test_cross_project_refs_and_access_are_rejected(client, attack):
    first = project(client); other = project(client, "第二原创项目")
    ref = document(client, first); body = payload(ref)
    saved = create(client, first, body).json()["artifact_manifest"]
    if attack in {"download", "manifest"}:
        response = client.get(endpoint(other) + "/" + saved["id"] + ("/download" if attack == "download" else ""))
        assert response.status_code == 404
    else:
        changed = payload(document(client, other))
        if attack == "source_project": changed["report"]["source_refs"] = [ref]
        elif attack == "source_id": changed["report"]["source_refs"] = [{**ref, "project_id": other}]
        else: changed["template"] = {key: ref[key] for key in ("document_id", "revision", "sha256")}
        response = create(client, other, changed)
        assert response.status_code in {404, 422}, response.text
    assert client.get(endpoint(other)).json()["artifacts"] == []


@pytest.mark.parametrize("target", ["source", "template"])
def test_original_byte_drift_is_rejected_before_export_and_preserves_failure(client, target):
    pid = project(client); ref = document(client, pid); body = payload(ref)
    if target == "template":
        ref = document(client, pid, "template.pptx", template())
        body["template"] = {key: ref[key] for key in ("document_id", "revision", "sha256")}
    with client.app.state.store.connect() as connection:
        # Deliberate storage corruption: normal API updates cannot bypass this trigger.
        connection.execute("DROP TRIGGER document_versions_no_update")
        connection.execute("UPDATE document_versions SET raw_bytes=? WHERE document_id=? AND revision=1",
                           (b"original corruption counter", ref["document_id"]))
    with patch.object(reports, "export_report_deck", side_effect=AssertionError("must not export drifted source")):
        response = create(client, pid, body)
    assert response.status_code == 409 and "drifted" in response.text
    assert client.get(endpoint(pid)).json()["artifacts"] == []
    attempts = client.get(f"/api/projects/{pid}/report-artifact-attempts").json()["attempts"]
    assert len(attempts) == 1 and attempts[0]["status"] == "FAIL"


def test_export_failure_is_retained_and_same_input_can_retry_without_fake_success(client):
    pid = project(client); body = payload(document(client, pid)); actual = reports.export_report_deck
    with patch.object(reports, "export_report_deck", side_effect=[PptxExportError("original export failure"),
                lambda: None]) as exporter:
        failed = create(client, pid, body)
        assert failed.status_code == 422 and "original export failure" in failed.text
        assert client.get(endpoint(pid)).json()["artifacts"] == []
        exporter.side_effect = actual
        success = create(client, pid, body)
        assert success.status_code == 201, success.text
        assert create(client, pid, body).status_code == 200
        assert exporter.call_count == 2
    attempts = client.get(f"/api/projects/{pid}/report-artifact-attempts").json()["attempts"]
    assert sorted(item["status"] for item in attempts) == ["FAIL", "REPLAYED", "SAVED"]
    assert any(item.get("message") == "original export failure" for item in attempts)


@pytest.mark.parametrize("corruption", ["bytes", "manifest", "symlink"])
def test_corrupted_or_escaped_artifact_never_downloads_or_regenerates(client, tmp_path, corruption):
    pid = project(client); body = payload(document(client, pid))
    manifest = create(client, pid, body).json()["artifact_manifest"]
    root = client.app.state.settings.data_dir / "report-artifacts" / pid / manifest["id"]
    if corruption == "bytes": (root / "report.pptx").write_bytes(b"corrupted original counter")
    elif corruption == "manifest":
        value = json.loads((root / "manifest.json").read_text()); value["manifest"]["project_id"] = "wrong-project"
        (root / "manifest.json").write_text(json.dumps(value))
    else:
        outside = tmp_path / "outside-original.pptx"; outside.write_bytes(b"outside must not be disclosed")
        (root / "report.pptx").unlink(); (root / "report.pptx").symlink_to(outside)
    with patch.object(reports, "export_report_deck", side_effect=AssertionError("must not silently overwrite")):
        response = client.get(manifest["download_url"])
        assert response.status_code in {409, 422} and b"outside must not be disclosed" not in response.content
        assert create(client, pid, body).status_code in {409, 422}


def test_registered_artifact_can_be_an_exact_source_for_another_report(client):
    pid = project(client); body = payload(document(client, pid))
    first = create(client, pid, body).json()["artifact_manifest"]
    ref = {"kind": "artifact", "project_id": pid, "artifact_id": first["id"], "revision": 1, "sha256": first["sha256"]}
    second = create(client, pid, payload(ref, "second_original_report"))
    assert second.status_code == 201, second.text
    assert second.json()["artifact_manifest"]["source_refs"] == [ref]
    changed = payload({**ref, "sha256": "b" * 64}, "wrong_artifact_sha")
    assert create(client, pid, changed).status_code == 409


@pytest.mark.parametrize("name", ["../escape.pptx", "C:\\escape.pptx", "bad\r\nheader.pptx", "CON.pptx", "wrong.txt"])
def test_unsafe_filename_is_rejected_before_artifact_side_effects(client, name):
    pid = project(client); body = payload(document(client, pid)); body["filename"] = name
    assert create(client, pid, body).status_code == 422
    assert client.get(endpoint(pid)).json()["artifacts"] == []
    assert client.get(f"/api/projects/{pid}/report-artifact-attempts").json()["attempts"] == []


def test_invalid_range_template_and_missing_csrf_do_not_publish_success(client):
    pid = project(client); body = payload(document(client, pid))
    invalid = deepcopy(body); invalid["report"]["source_refs"][0].update(line_start=1, line_end=99)
    assert create(client, pid, invalid).status_code == 422
    raw = template(missing=True); ref = document(client, pid, "missing-symbol.pptx", raw)
    missing = deepcopy(body); missing["idempotency_key"] = "missing_template_symbol"
    missing["template"] = {key: ref[key] for key in ("document_id", "revision", "sha256")}
    assert create(client, pid, missing).status_code == 422
    headers = dict(client.headers); del client.headers["X-EEAS-CSRF"]
    try: assert create(client, pid, body).status_code == 403
    finally: client.headers.clear(); client.headers.update(headers)
    assert client.get(endpoint(pid)).json()["artifacts"] == []
