"""Explicit source probes; run only with a frozen EEAS_CAMPAIGN_BINDING.

This filename is deliberately outside pytest's default test_*.py discovery.
Source declarations and successful probes do not independently certify origin/license.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def load_binding(case_id):
    selected = os.environ.get("EEAS_CAMPAIGN_BINDING")
    if not selected:
        raise ValueError("EEAS_CAMPAIGN_BINDING is required; there is no fallback source")
    binding_path = Path(selected)
    binding_raw = binding_path.read_bytes()
    binding = json.loads(binding_raw)
    if binding.get("schema") != "eeas.campaign-binding.v1" or binding.get("case_id") != case_id:
        raise ValueError("frozen binding has the wrong schema/case")
    relative = Path(binding["raw_path"])
    if relative.is_absolute() or ".." in relative.parts or relative.name != "source.raw":
        raise ValueError("source must be the captured root-relative source.raw")
    path = ROOT / relative
    if not path.resolve().is_relative_to(ROOT) or any(part.is_symlink() for part in [path, *path.parents] if part.is_relative_to(ROOT)):
        raise ValueError("source path escapes its frozen owner or uses a symlink")
    if any((path.parent / name).is_symlink() for name in ("source.json", "license.raw")):
        raise ValueError("captured metadata/license must not be symlinks")
    source = binding["project"]
    source_raw = (path.parent / "source.json").read_bytes()
    if sha(source_raw) != binding["source_json_sha256"] or json.loads(source_raw) != source:
        raise ValueError("source metadata differs from the frozen binding")
    if source.get("source_kind") not in {"public", "synthetic"}:
        raise ValueError("this probe accepts only declared public or original synthetic source")
    raw, legal = path.read_bytes(), (path.parent / "license.raw").read_bytes()
    if sha(raw) != source["raw_sha256"] or not legal or sha(legal) != source["license_sha256"]:
        raise ValueError("captured source/license SHA mismatch")
    name = source["original_filename"]
    if Path(name).name != name or "\\" in name or Path(name).suffix.lower() not in {".txt", ".md"}:
        raise ValueError("this source probe requires an original Markdown/TXT artifact")
    if not 0 < len(raw) <= 1024 * 1024:
        raise ValueError("captured text must fit the actual 1MiB text-import limit")
    text = raw.decode("utf-8")
    if "\x00" in text or not text.strip():
        raise ValueError("captured source must contain actual UTF-8 text")
    return {"binding": binding, "binding_raw": binding_raw, "raw": raw, "text": text,
            "name": name, "source": source, "path": path}


class Evidence:
    def __init__(self, captured):
        selected = os.environ.get("EEAS_EVIDENCE_DIR")
        if not selected or not Path(selected).is_dir():
            raise ValueError("actual record_slice evidence directory is required")
        self.root = Path(selected)
        self.captured = captured
        self.receipts = []

    def save(self, name, value):
        raw = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False, sort_keys=True,
                                                                indent=2, allow_nan=False) + "\n").encode()
        path = self.root / name
        with path.open("xb") as stream:
            stream.write(raw)
        self.receipts.append({"path": name, "sha256": sha(raw)})

    def observe(self):
        captured = self.captured
        value = {"schema": "eeas.campaign-observation.v1", "case_id": captured["binding"]["case_id"],
                 "binding_sha256": sha(captured["binding_raw"]), "source_sha256": sha(captured["raw"]),
                 "source_kind": captured["source"]["source_kind"], "runtime_kind": "none",
                 "model_calls": 0, "origin_verification": "DECLARED_NOT_INDEPENDENTLY_VERIFIED",
                 "actual_receipts": self.receipts}
        with (self.root / "campaign-observation.json").open("xb") as stream:
            stream.write((json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode())


def ingest(client, captured, evidence):
    source = captured["source"]
    response = client.post("/api/projects", json={"name": "Campaign " + source["project_id"]})
    assert response.status_code == 201, response.text
    pid = response.json()["id"]
    declaration = f"campaign:{source['source_kind']}:{source['project_id']}:{source['raw_sha256']}"
    response = client.post(f"/api/projects/{pid}/documents", files={"file": (captured["name"], captured["raw"])},
                           data={"source": declaration})
    assert response.status_code == 201, response.text
    doc = response.json()
    assert doc["sha256"] == sha(captured["raw"]) and doc["revision"] == 1 and doc["source"] == declaration
    base = f"/api/projects/{pid}/documents/{doc['id']}"
    actual = client.get(base, params={"revision": 1})
    assert actual.status_code == 200
    assert actual.json()["content"] == captured["text"]
    assert actual.json()["content_sha256"] == sha(captured["raw"])
    downloaded = client.get(base + "/download", params={"revision": 1})
    assert downloaded.status_code == 200 and downloaded.content == captured["raw"]
    evidence.save("actual-document.json", actual.json())
    evidence.save("actual-original.bin", downloaded.content)
    evidence.save("actual-parse.json", client.get(base + "/structure", params={"revision": 1}).json())
    evidence.save("source-declaration.json", source)
    return pid, doc, base


def all_chunks(client, base, captured, evidence):
    parts, offset, pages = [], 0, []
    while True:
        response = client.get(base + "/chunks", params={"revision": 1, "offset": offset, "limit": 20})
        assert response.status_code == 200
        page = response.json()
        assert page["sha256"] == sha(captured["raw"]) and page["revision"] == 1
        pages.append(page)
        parts += page["chunks"]
        next_offset = page["next_offset"]
        if next_offset is None:
            break
        assert next_offset > offset and len(pages) <= 263  # 1MiB text's bounded pagination.
        offset = next_offset
    text = captured["text"]
    assert "".join(part["content"] for part in parts) == text
    starts, running = [], 0
    for line in text.splitlines(keepends=True):
        starts.append(running)
        running += len(line)
    cursor = 0
    for index, part in enumerate(parts):
        assert part["index"] == index and part["char_start"] == cursor
        assert part["char_end"] == cursor + len(part["content"])
        assert part["content"] == text[part["char_start"]:part["char_end"]]
        assert part["sha256"] == sha(part["content"].encode())
        # Independent physical-line positions, including CRLF and Unicode line breaks.
        assert part["line_start"] == sum(pos <= cursor for pos in starts)
        assert part["line_end"] == sum(pos <= part["char_end"] - 1 for pos in starts)
        cursor = part["char_end"]
    evidence.save("actual-chunk-pages.json", pages)
    return parts


def test_source_text(client):
    captured = load_binding("source-text")
    evidence = Evidence(captured)
    pid, doc, base = ingest(client, captured, evidence)
    all_chunks(client, base, captured, evidence)
    text = captured["text"]
    tail = next((line for line in reversed(text.splitlines()) if line.strip()), "")
    keyword = tail[-64:]
    hits = client.get(f"/api/projects/{pid}/search", params={"keyword": keyword, "document_id": doc["id"], "revision": 1}).json()["hits"]
    expected_line = max(n for n, line in enumerate(text.splitlines(), 1) if keyword.casefold() in line.casefold())
    # Pagination prevents a common term occurring >50 times from hiding the real tail.
    offset = 0
    while not any(hit["line_start"] == expected_line for hit in hits):
        offset += 50
        response = client.get(f"/api/projects/{pid}/search", params={"keyword": keyword, "document_id": doc["id"], "revision": 1, "offset": offset}).json()
        hits += response["hits"]
        assert response["hits"], "Actual source tail search never became reachable"
    assert all(hit["sha256"] == doc["sha256"] and hit["revision"] == 1 for hit in hits)
    window = client.get(base + "/content", params={"revision": 1, "char_start": max(0, len(text) - 8000)}).json()
    assert window["content"] == text[window["char_start"]:] and window["next_char_start"] is None
    evidence.save("actual-tail-search.json", hits)
    evidence.save("actual-tail-window.json", window)
    evidence.observe()


def test_source_history(client):
    captured = load_binding("source-history")
    evidence = Evidence(captured)
    pid, doc, base = ingest(client, captured, evidence)
    # This is a declared original fault/derived revision, never an upstream new tag.
    derived = ("EEAS original history mutation; parent rawSHA=" + sha(captured["raw"]) + "\n").encode()
    response = client.put(base, files={"file": (captured["name"], derived)},
                          data={"expected_revision": 1, "source": "original:campaign-history-mutation"})
    assert response.status_code == 200 and response.json()["revision"] == 2
    assert client.get(base).json()["sha256"] == sha(derived)
    assert client.get(base, params={"revision": 1}).json()["content"] == captured["text"]
    assert client.get(base + "/download", params={"revision": 1}).content == captured["raw"]
    assert client.get(base + "/download", params={"revision": 2}).content == derived
    stale = client.put(base, files={"file": (captured["name"], b"original stale write")}, data={"expected_revision": 1})
    assert stale.status_code == 409
    assert client.get(base).json()["current_revision"] == 2
    foreign = client.post("/api/projects", json={"name": "Original foreign project"}).json()["id"]
    assert client.get(base.replace(pid, foreign)).status_code == 404
    assert client.delete(base.replace(pid, foreign)).status_code == 404
    deleted = client.delete(base)
    assert deleted.status_code == 200 and client.get(base).status_code == 404
    assert client.get(base + "/download", params={"revision": 1}).status_code == 404
    with client.app.state.store.connect() as conn:
        rows = conn.execute("SELECT revision,sha256,raw_bytes FROM document_versions WHERE document_id=? ORDER BY revision", (doc["id"],)).fetchall()
    assert [(r["revision"], r["sha256"], r["raw_bytes"]) for r in rows] == [(1, sha(captured["raw"]), captured["raw"]), (2, sha(derived), derived)]
    evidence.save("actual-revision2.bin", derived)
    evidence.save("actual-history.json", {"created_revision2": response.json(), "stale_write": stale.json(),
                   "delete": deleted.json(), "retained_db_versions": [{"revision": r["revision"], "sha256": r["sha256"], "byte_length": len(r["raw_bytes"])} for r in rows],
                   "mutation_source_kind": "synthetic-derived-fault", "parent_raw_sha256": sha(captured["raw"])})
    evidence.observe()


def test_source_context(client):
    captured = load_binding("source-context")
    assert len(captured["text"]) >= 70000, "Source context case requires actual bound >=70k characters; no synthetic padding"
    evidence = Evidence(captured)
    _, doc, base = ingest(client, captured, evidence)
    parts = all_chunks(client, base, captured, evidence)
    full_response = client.post(base + "/context", json={"revision": 1})
    full_expected = len(captured["text"]) <= 120000 and len(captured["raw"]) <= 220000
    if full_expected:
        assert full_response.status_code == 200
        full = full_response.json()
        assert full["coverage"] == "COMPLETE" and full["truncated"] is False
        assert "".join(p["content"] for p in full["chunks"]) == captured["text"]
        assert full["omitted_chunk_count"] == 0
    else:
        assert full_response.status_code == 422 and "No text was truncated" in full_response.json()["error"]["message"]
    last = len(parts) - 1
    partial_response = client.post(base + "/context", json={"revision": 1, "chunk_indexes": [last]})
    assert partial_response.status_code == 200
    partial = partial_response.json()
    assert partial["coverage"] == "PARTIAL_EXPLICIT_SELECTION" and partial["truncated"] is False
    assert partial["included_chunks"] == [last] and partial["omitted_chunk_count"] == last
    assert partial["chunks"][0]["content"] == parts[last]["content"]
    assert partial["sha256"] == doc["sha256"]
    denied = []
    for body in ({"revision": 1, "byte_budget": 1}, {"revision": 1, "char_budget": 1},
                 {"revision": 1, "chunk_indexes": [last, last]}, {"revision": 1, "chunk_indexes": [len(parts)]}):
        response = client.post(base + "/context", json=body)
        assert response.status_code == 422
        denied.append({"requested": body, "status": response.status_code, "response": response.json()})
    evidence.save("actual-full-context.json", {"status": full_response.status_code, "response": full_response.json()})
    evidence.save("actual-selected-context.json", partial)
    evidence.save("actual-context-denials.json", denied)
    evidence.observe()
