"""Real Store/ASGI materials plus isolated public-transport admission counters."""
import copy
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import socket
import threading

import httpx
import pytest
from pydantic import ValidationError

from apps.api.competition_sources import register_competition_source_routes
from packages.domain import competition_sources as module
from packages.domain.competition_sources import CompetitionSources, CompetitionSourceInput, CaptureDenied
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import canonical, digest
from test_documents import project, upload


@pytest.fixture
def selected(client, tmp_path):
    pid = project(client, "原创竞品证据")
    raw = "# 实际用户测试记录\n设备断线后恢复用了五分钟。未提供设备型号和版本。\n".encode()
    document = upload(client, pid, raw, "actual-user-test.md").json()
    register_competition_source_routes(client.app, client.app.state.store, tmp_path)
    return {"client": client, "pid": pid, "store": client.app.state.store,
        "service": client.app.state.competition_sources, "raw": raw,
        "ref": {"document_id": document["id"], "revision": document["revision"], "sha256": document["sha256"]},
        "tmp_path": tmp_path}


def offline_body(selected, operation="selected-offline"):
    return {"operation_id": operation, "mode": "OFFLINE_PACKAGE", "document_ref": selected["ref"], "publisher_kind": "USER_TEST"}


def online_body(url="https://official.example.invalid/product", operation="selected-online"):
    return {"operation_id": operation, "mode": "ONLINE_URL", "url": url, "publisher_kind": "OFFICIAL"}


def controlled_http(monkeypatch, responses, addresses=("93.184.216.34",)):
    observed = {"requests": [], "clients": [], "dns": []}
    original_client = httpx.Client
    def resolve(host, port, **kwargs):
        observed["dns"].append((host, port))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, port)) for ip in addresses]
    def handle(request):
        observed["requests"].append(request)
        status, headers, raw = responses[len(observed["requests"])-1]
        return httpx.Response(status, headers=headers, stream=httpx.ByteStream(raw))
    def factory(**kwargs):
        observed["clients"].append(copy.deepcopy(kwargs))
        return original_client(transport=httpx.MockTransport(handle), **kwargs)
    monkeypatch.setattr(module.socket, "getaddrinfo", resolve)
    monkeypatch.setattr(module.httpx, "Client", factory)
    return observed


def test_real_offline_material_freezes_bytes_and_ref_and_replay_never_connects(selected, monkeypatch):
    def denied(*args, **kwargs): pytest.fail("Offline source capture attempted DNS/HTTP")
    monkeypatch.setattr(module.socket, "getaddrinfo", denied)
    monkeypatch.setattr(module.httpx, "Client", denied)
    body = offline_body(selected)
    response = selected["client"].post(f"/api/projects/{selected['pid']}/competition/sources", json=body)
    assert response.status_code == 201, response.text
    value = response.json()
    assert value["status"] == "SAVED" and value["document_ref"] == selected["ref"]
    assert value["plaintext"].encode() == selected["raw"] and value["network_requests"] == 0
    assert value["publisher_kind_authority"] == "USER_DECLARED_NOT_VERIFIED"
    assert value["raw_snapshot_ref"]["sha256"] == hashlib.sha256(selected["raw"]).hexdigest()
    replay = selected["client"].post(f"/api/projects/{selected['pid']}/competition/sources", json=body)
    assert replay.status_code == 200 and replay.json()["id"] == value["id"] and replay.json()["replayed"]
    snapshot = selected["client"].get(f"/api/projects/{selected['pid']}/competition/sources/{value['id']}/snapshot")
    assert snapshot.content == selected["raw"] and snapshot.headers["content-type"] == "application/octet-stream"
    assert "attachment" in snapshot.headers["content-disposition"] and snapshot.headers["x-content-type-options"] == "nosniff"
    with selected["store"].connect() as db:
        assert db.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
        assert db.execute("SELECT count(*) FROM tasks").fetchone()[0] == 0


def test_online_html_stores_actual_extracted_utf8_text_and_keeps_raw_snapshot(selected, monkeypatch):
    raw = ('<!doctype html><html><head><title>原创产品说明</title></head><body><h1>接口能力</h1>'
        '<p>产品提供接口文档。尚未证明目标设备能运行。</p><script>invented capability should not appear</script></body></html>').encode()
    seen = controlled_http(monkeypatch, [(200, {"content-type": "text/html; charset=utf-8", "etag": '"version-original"'}, raw)])
    body = CompetitionSourceInput(**online_body())
    value = selected["service"].capture(selected["pid"], body)
    assert value["status"] == "PARTIAL" and value["coverage"] == "PARTIAL_HTML_TEXT"
    assert value["title"] == "原创产品说明" and "尚未证明" in value["plaintext"]
    assert "invented capability" not in value["plaintext"] and "SCRIPT_CONTENT_NOT_EXECUTED" in value["warnings"]
    document = selected["store"].document(selected["pid"], value["document_ref"]["document_id"])
    assert document["raw_bytes"] == value["plaintext"].encode() and document["content"] == value["plaintext"]
    assert selected["service"].snapshot(selected["pid"], value["id"]) == raw
    assert value["etag"] == '"version-original"' and value["http_status"] == 200
    request = seen["requests"][0]
    assert request.url.host == "93.184.216.34" and request.headers["host"] == "official.example.invalid"
    assert request.extensions["sni_hostname"] == "official.example.invalid"
    assert request.headers["accept-encoding"] == "identity" and "authorization" not in request.headers and "cookie" not in request.headers
    assert seen["clients"][0]["trust_env"] is False and seen["clients"][0]["follow_redirects"] is False
    replay = selected["service"].capture(selected["pid"], body)
    assert replay["id"] == value["id"] and len(seen["requests"]) == 1


@pytest.mark.parametrize("url", ["http://127.0.0.1/", "http://169.254.169.254/latest/meta-data", "http://10.0.0.1/",
    "http://192.168.2.3/", "http://[::1]/", "http://[::ffff:127.0.0.1]/", "http://224.0.0.1/", "http://0.0.0.0/",
    "http://[fe80::1]/", "https://user:password@official.example.invalid/", "file:///etc/passwd", "http://example.invalid:22/",
    "https://example.invalid:0/", "https://example.invalid/#fragment", "https://example.invalid/\n"])
def test_nonpublic_and_ambiguous_urls_are_durable_failures_before_http(selected, monkeypatch, url):
    seen = controlled_http(monkeypatch, [])
    value = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body(url)))
    assert value["status"] == "FAILED" and value["document_ref"] is None and seen["requests"] == []
    again = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body(url)))
    assert again["id"] == value["id"] and again["replayed"] and seen["requests"] == []


@pytest.mark.parametrize("addresses", [("127.0.0.1",), ("93.184.216.34", "10.0.0.2")])
def test_dns_private_answer_or_mixed_public_private_answers_fail_closed(selected, monkeypatch, addresses):
    seen = controlled_http(monkeypatch, [], addresses)
    value = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body()))
    assert value["status"] == "FAILED" and value["reason"] == "PUBLIC_ADDRESS_REQUIRED" and seen["requests"] == []


def test_dns_rebinding_cannot_change_actual_connected_target_and_redirect_is_readmitted(selected, monkeypatch):
    seen = controlled_http(monkeypatch, [(302, {"location": "http://169.254.169.254/metadata"}, b"")])
    value = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body()))
    assert len(seen["requests"]) == 1 and seen["requests"][0].url.host == "93.184.216.34"
    assert value["status"] == "FAILED" and value["reason"] == "PUBLIC_ADDRESS_REQUIRED"
    assert value["http_status"] == 302 and len(value["redirects"]) == 1
    assert value["document_ref"] is None


def test_public_redirect_cannot_forward_cookies_or_hide_request_count(selected, monkeypatch):
    seen = controlled_http(monkeypatch, [(302, {"location": "/new-page", "set-cookie": "tracking=must-not-forward"}, b""),
        (200, {"content-type": "text/plain; charset=utf-8"}, b"Actual new-page evidence, without tests of any product.")])
    value = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body()))
    assert value["status"] == "SAVED" and value["network_requests"] == 2
    assert value["final_url"].endswith("/new-page") and all("cookie" not in r.headers for r in seen["requests"])


@pytest.mark.parametrize("case", ["length", "stream", "compressed", "unsupported", "http-error", "script-only"])
def test_oversize_and_incomplete_or_failed_pages_never_claim_complete_source(selected, monkeypatch, case):
    status, headers, raw = 200, {"content-type": "text/html"}, b"<script>renderActualData()</script>"
    if case == "length": headers["content-length"] = str(module.MAX_NETWORK_BYTES + 1)
    elif case == "stream": raw = b"X" * (module.MAX_NETWORK_BYTES + 1)
    elif case == "compressed": headers["content-encoding"] = "gzip"
    elif case == "unsupported": headers["content-type"] = "application/octet-stream"
    elif case == "http-error": status, raw = 403, b"<h1>Access denied</h1>"
    seen = controlled_http(monkeypatch, [(status, headers, raw)])
    value = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body()))
    assert len(seen["requests"]) == 1 and value["status"] in {"FAILED", "PARTIAL"}
    assert value["document_ref"] is None and value["coverage"] != "COMPLETE"
    if case == "script-only": assert value["coverage"] == "INSUFFICIENT_NO_TEXT"
    if case == "http-error": assert value["http_status"] == 403 and value["reason"] == "CAPTURE_HTTP_ERROR"


def test_capture_cross_project_and_request_changes_and_snapshot_tampering_are_rejected(selected):
    body = CompetitionSourceInput(**offline_body(selected))
    value = selected["service"].capture(selected["pid"], body)
    other = project(selected["client"], "其他真实项目")
    with pytest.raises(NotFound): selected["service"].get(other, value["id"])
    other_value = selected["service"].capture(other, body)
    assert other_value["status"] == "FAILED" and other_value["reason"] == "NotFound"
    changed = body.model_copy(update={"publisher_kind": "OFFICIAL"})
    with pytest.raises(Conflict): selected["service"].capture(selected["pid"], changed)
    with selected["service"]._connect() as db: db.execute("UPDATE captures SET raw=? WHERE id=?", (b"tampered", value["id"]))
    with pytest.raises(Conflict): selected["service"].get(selected["pid"], value["id"])


def test_unknown_capture_after_restart_never_refetches(selected, monkeypatch):
    body = CompetitionSourceInput(**online_body())
    request = body.model_dump()
    with selected["service"]._connect() as db:
        db.execute("INSERT INTO captures VALUES (?,?,?,?,?,'PROCESSING',NULL,NULL,NULL,NULL)",
            ("original-lost-owner", selected["pid"], body.operation_id, digest(request), canonical(request)))
    def denied(*args, **kwargs): pytest.fail("Restart automatically refetched a lost capture")
    monkeypatch.setattr(module.httpx, "Client", denied)
    restored = CompetitionSources(selected["store"], selected["tmp_path"])
    result = restored.capture(selected["pid"], body)
    assert result["id"] == "original-lost-owner"
    assert result["status"] == "UNKNOWN_OUTCOME" and result["replayed"]


def test_material_context_preserves_user_declared_provenance_without_changing_original_text(selected):
    original = selected["store"].document(selected["pid"], selected["ref"]["document_id"])
    first = selected["service"].capture(selected["pid"], CompetitionSourceInput(**offline_body(selected)))
    second_body = offline_body(selected, "second-publisher-label"); second_body["publisher_kind"] = "OFFICIAL"
    second = selected["service"].capture(selected["pid"], CompetitionSourceInput(**second_body))
    context = selected["service"].material_context(selected["pid"], selected["ref"])
    assert {c["id"] for c in context["captures"]} == {first["id"], second["id"]}
    assert {c["publisher_kind"] for c in context["captures"]} == {"OFFICIAL", "USER_TEST"}
    assert all(c["publisher_kind_authority"] == "USER_DECLARED_NOT_VERIFIED" for c in context["captures"])
    assert "plaintext" not in context["captures"][0]
    current = selected["store"].document(selected["pid"], selected["ref"]["document_id"])
    assert current["raw_bytes"] == original["raw_bytes"] and current["content"] == original["content"]


def test_unregistered_competition_source_marker_cannot_borrow_another_capture_provenance(selected):
    captured = selected["service"].capture(selected["pid"], CompetitionSourceInput(**offline_body(selected)))
    forged = selected["store"].save_document(selected["pid"], "forged-marker.txt", b"Unregistered statement",
        "competition-source:" + captured["id"])
    ref = {"document_id": forged["id"], "revision": forged["revision"], "sha256": forged["sha256"]}
    with pytest.raises(Conflict): selected["service"].material_context(selected["pid"], ref)


def test_existing_real_pdf_parser_is_reused_for_selected_offline_and_online_document(selected, monkeypatch):
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject
    writer = PdfWriter(); page = writer.add_blank_page(300, 300)
    font = DictionaryObject({NameObject("/Type"): NameObject("/Font"), NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): writer._add_object(font)})})
    stream = DecodedStreamObject(); stream.set_data(b"BT /F1 12 Tf 12 280 Td (Original selected PDF evidence; target-device tests missing.) Tj ET")
    page[NameObject("/Contents")] = writer._add_object(stream)
    buffer = BytesIO(); writer.write(buffer); raw = buffer.getvalue()
    document = upload(selected["client"], selected["pid"], raw, "original-evidence.pdf").json()
    ref = {"document_id": document["id"], "revision": document["revision"], "sha256": document["sha256"]}
    body = offline_body(selected, "actual-pdf-offline"); body["document_ref"] = ref
    offline = selected["service"].capture(selected["pid"], CompetitionSourceInput(**body))
    assert "Original selected PDF evidence" in offline["plaintext"]
    assert offline["document_ref"] == ref and selected["service"].snapshot(selected["pid"], offline["id"]) == raw
    seen = controlled_http(monkeypatch, [(200, {"content-type": "application/pdf"}, raw)])
    online = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body(operation="actual-pdf-wire")))
    assert online["plaintext"] == offline["plaintext"] and online["document_ref"] is not None and len(seen["requests"]) == 1


@pytest.mark.parametrize("invalid", [{"mode": "ONLINE_URL", "document_ref": {}}, {"mode": "OFFLINE_PACKAGE", "url": "https://example.invalid"},
    {"mode": "AUTO"}, {"headers": {"Authorization": "personal"}}, {"publisher_kind": "VERIFIED_CAPABILITY"}])
def test_explicit_mode_and_source_shape_cannot_expand_to_auto_network_or_auth(selected, invalid):
    body = offline_body(selected); body.update(invalid)
    with pytest.raises(ValidationError): CompetitionSourceInput(**body)


def test_existing_csrf_and_origin_boundary_protect_competition_capture(selected):
    route = f"/api/projects/{selected['pid']}/competition/sources"
    denied = selected["client"].post(route, json=offline_body(selected), headers={"X-EEAS-CSRF": "wrong"})
    assert denied.status_code == 403 and selected["service"].list(selected["pid"]) == []
    denied = selected["client"].post(route, json=offline_body(selected), headers={"Origin": "https://other.invalid"})
    assert denied.status_code == 403 and selected["service"].list(selected["pid"]) == []


def test_selected_actual_public_official_page_is_captured_only_when_explicitly_requested(selected):
    url = os.environ.get("EEAS_ACTUAL_PUBLIC_CAPTURE_URL")
    if not url: pytest.skip("Actual public retrieval not selected; policy counters use original isolated transport fixtures")
    value = selected["service"].capture(selected["pid"], CompetitionSourceInput(**online_body(url, "actual-public-official-page")))
    evidence = os.environ.get("EEAS_COMPETITION_EVIDENCE_DIR")
    if evidence:
        out = Path(evidence); out.mkdir(parents=True, exist_ok=True)
        (out / "actual-public-capture.json").write_text(json.dumps(value, ensure_ascii=False, indent=2))
        (out / "actual-public-snapshot.bin").write_bytes(selected["service"].snapshot(selected["pid"], value["id"]))
    assert value["http_status"] == 200 and value["document_ref"] is not None, canonical(value)
    assert value["plaintext"] and value["raw_snapshot_ref"]["bytes"] > 0
    assert value["publisher_kind_authority"] == "USER_DECLARED_NOT_VERIFIED"
