"""Original long Chinese task books; no corporate samples or external model."""
import hashlib

from packages.domain.store import Store
from test_documents import project, upload


def test_long_taskbook_roundtrip_tail_citation_and_lossless_pages(client, tmp_path):
    pid = project(client)
    content = "# 原创工程任务书\r\n" + "状态采集必须保存时间戳并在断网后恢复。\r\n" * 4000
    content += "REQ: 末尾要求必须可检索与引用\r\nACCEPT: 末尾验收保存原始哈希\r\n"
    assert len(content) > 70000
    raw = content.encode("utf-8")
    response = upload(client, pid, raw, "七万字任务书.md")
    assert response.status_code == 201
    doc = response.json()
    assert doc["character_length"] == len(content)
    assert doc["sha256"] == hashlib.sha256(raw).hexdigest()
    store = Store(tmp_path / "eeas.sqlite3")
    assert store.document(pid, doc["id"], 1)["raw_bytes"] == raw
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    parts, offset = [], 0
    while offset is not None:
        page = client.get(path + "/chunks", params={"revision": 1, "offset": offset, "limit": 3}).json()
        assert page["sha256"] == doc["sha256"]
        assert page["total_characters"] == len(content)
        parts.extend(page["chunks"])
        offset = page["next_offset"]
    assert len(parts) == doc["chunk_count"]
    assert "".join(p["content"] for p in parts) == content
    assert parts[0]["char_start"] == 0 and parts[-1]["char_end"] == len(content)
    assert all(a["char_end"] == b["char_start"] for a, b in zip(parts, parts[1:]))
    for part in parts:
        assert len(part["content"]) <= 4000
        assert content[part["char_start"]:part["char_end"]] == part["content"]
        assert part["sha256"] == hashlib.sha256(part["content"].encode()).hexdigest()
    hits = client.get(f"/api/projects/{pid}/search", params={"keyword": "末尾要求"}).json()["hits"]
    assert hits[0]["line_start"] == 4002
    assert "末尾要求" in hits[0]["excerpt"]
    proposal = client.post(f"/api/projects/{pid}/planning/extract", json={"document_id": doc["id"], "revision": 1}).json()
    assert proposal["mode"] == "mock" and proposal["model_verified"] is False
    assert proposal["proposal"]["requirements"][0]["source_refs"][0]["line_start"] == 4002
    assert client.post(f"/api/projects/{pid}/requirements", json=proposal["proposal"]).status_code == 201


def test_single_long_line_unicode_windows_and_search_excerpt_near_match(client):
    pid = project(client)
    content = "甲😀" * 36000 + "末端ß检索"
    doc = upload(client, pid, content.encode(), "单行.txt").json()
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    parts = client.get(path + "/chunks", params={"limit": 50}).json()["chunks"]
    assert "".join(p["content"] for p in parts) == content
    assert all(p["line_start"] == p["line_end"] == 1 for p in parts)
    window = client.get(path + "/content", params={"char_start": 71998, "char_limit": 20}).json()
    assert window["content"] == content[71998:] and window["next_char_start"] is None
    hit = client.get(f"/api/projects/{pid}/search", params={"keyword": "SS检索"}).json()["hits"][0]
    assert "ß检索" in hit["excerpt"]
    assert content[hit["match_char_start"]:hit["match_char_end"]] == "ß检索"
    assert len(hit["excerpt"]) <= 512


def test_search_pagination_revision_pinning_cross_project_and_invalid_windows(client):
    pid, other = project(client), project(client, "另一项目")
    original = "\n".join(f"第{i}项可分页匹配" for i in range(120))
    doc = upload(client, pid, original.encode()).json()
    path = f"/api/projects/{pid}/documents/{doc['id']}"
    result = client.get(f"/api/projects/{pid}/search", params={"keyword": "分页匹配", "offset": 50, "limit": 50}).json()
    assert len(result["hits"]) == 50 and result["hits"][0]["line_start"] == 51
    assert result["next_offset"] == 100
    tail = client.get(f"/api/projects/{pid}/search", params={"keyword": "分页匹配", "offset": 100}).json()
    assert len(tail["hits"]) == 20 and tail["next_offset"] is None
    assert client.put(path, files={"file": ("original.md", b"new")}, data={"expected_revision": 1}).status_code == 200
    assert client.get(path + "/chunks", params={"revision": 1}).json()["chunks"][0]["content"] == original
    assert client.get(path + "/content").json()["revision"] == 2
    for route in ("chunks", "content"):
        assert client.get(path.replace(pid, other) + "/" + route).status_code == 404
        assert client.get(path + "/" + route, params={"revision": 999}).status_code == 404
    for params in ({"char_start": 4}, {"char_limit": 32001}, {"char_start": -1}):
        assert client.get(path + "/content", params=params).status_code == 422
    assert client.get(path + "/chunks", params={"limit": 51}).status_code == 422
    assert client.get(path + "/chunks", params={"offset": 100}).json()["chunks"] == []
    assert client.delete(path).status_code == 200
    assert client.get(path + "/chunks", params={"revision": 1}).status_code == 404


def test_empty_and_unusual_line_boundaries_are_lossless(client):
    pid = project(client)
    for content in ("", "甲\r乙\r\n丙\u2028丁\n", "x" * 3999 + "\r\n" + "😀" * 4001):
        doc = upload(client, pid, content.encode()).json()
        path = f"/api/projects/{pid}/documents/{doc['id']}"
        parts = client.get(path + "/chunks").json()["chunks"]
        assert "".join(p["content"] for p in parts) == content
        assert all(1 <= p["line_start"] <= p["line_end"] <= len(content.splitlines()) for p in parts)
        assert client.get(path + "/content", params={"char_start": len(content)}).json()["content"] == ""
