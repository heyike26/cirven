"""Explicit original-byte reader and revision-search probes; no default discovery."""
from bisect import bisect_right

from campaign_probes import Evidence, all_chunks, ingest, load_binding, sha


def search_all(client, pid, doc_id, keyword, revision=None):
    pages, hits, offset = [], [], 0
    while True:
        params = {"document_id": doc_id, "keyword": keyword, "offset": offset, "limit": 17}
        if revision is not None:
            params["revision"] = revision
        response = client.get(f"/api/projects/{pid}/search", params=params)
        assert response.status_code == 200, response.text
        page = response.json()
        pages.append(page)
        hits.extend(page["hits"])
        following = page["next_offset"]
        if following is None:
            return pages, hits
        assert following > offset and len(pages) <= 65536
        offset = following


def test_source_reader(client):
    captured = load_binding("source-reader")
    evidence = Evidence(captured)
    pid, doc, base = ingest(client, captured, evidence)
    all_chunks(client, base, captured, evidence)
    text = captured["text"]
    starts, cursor = [], 0
    for line in text.splitlines(keepends=True):
        starts.append(cursor)
        cursor += len(line)
    windows, offset = [], 0
    while True:
        response = client.get(base + "/content", params={"revision": 1, "char_start": offset, "char_limit": 8191})
        assert response.status_code == 200, response.text
        page = response.json()
        assert page["sha256"] == doc["sha256"] and page["revision"] == 1
        assert page["char_start"] == offset and page["char_end"] == min(offset + 8191, len(text))
        assert page["content"] == text[page["char_start"]:page["char_end"]]
        assert page["total_characters"] == len(text)
        assert page["line_start"] == bisect_right(starts, offset)
        assert page["line_end"] == bisect_right(starts, page["char_end"] - 1)
        windows.append(page)
        following = page["next_char_start"]
        if following is None:
            break
        assert following == page["char_end"] > offset
        offset = following
    assert "".join(page["content"] for page in windows) == text
    assert windows[-1]["char_end"] == len(text)
    eof = client.get(base + "/content", params={"revision": 1, "char_start": len(text)}).json()
    assert eof["content"] == "" and eof["next_char_start"] is None
    assert eof["line_start"] is eof["line_end"] is None
    denials = []
    for params in ({"char_start": len(text) + 1}, {"char_start": -1}, {"char_limit": 32001}, {"char_limit": 0}, {"revision": 999}):
        response = client.get(base + "/content", params=params)
        assert response.status_code == (404 if params.get("revision") == 999 else 422)
        denials.append({"requested": params, "status": response.status_code, "response": response.json()})
    foreign = client.post("/api/projects", json={"name": "Original reader foreign boundary"}).json()["id"]
    assert client.get(base.replace(pid, foreign) + "/content").status_code == 404
    evidence.save("actual-reader-windows.json", windows)
    evidence.save("actual-reader-eof.json", eof)
    evidence.save("actual-reader-denials.json", denials)
    evidence.observe()


def test_source_history_search(client):
    captured = load_binding("source-history")
    evidence = Evidence(captured)
    pid, doc, base = ingest(client, captured, evidence)
    # Selection uses genuine original bodies for composites; wrappers are never
    # represented as upstream business text. This helper also checks freeze refs.
    from campaign_workflow_probes import source_lines
    lines = source_lines(captured, evidence)
    derived = b"EEAS original revision-search mutation; history body remains immutable.\n"
    keyword = next((line["content"][-150:] for line in reversed(lines)
                    if any(char.isalnum() for char in line["content"]) and line["content"][-150:].casefold() not in derived.decode().casefold()), None)
    assert keyword, "No whole original line provides an applicable distinct query; no padding"
    before_pages, before_hits = search_all(client, pid, doc["id"], keyword)
    assert before_hits and all(h["revision"] == 1 and h["sha256"] == doc["sha256"] for h in before_hits)
    response = client.put(base, files={"file": (captured["name"], derived)},
                          data={"expected_revision": 1, "source": "original:revision-search-mutation"})
    assert response.status_code == 200 and response.json()["revision"] == 2
    current_pages, current_hits = search_all(client, pid, doc["id"], keyword)
    assert current_hits == []
    historical_pages, historical_hits = search_all(client, pid, doc["id"], keyword, 1)
    assert historical_hits == before_hits
    _, new_hits = search_all(client, pid, doc["id"], "revision-search mutation")
    assert new_hits and all(h["revision"] == 2 and h["sha256"] == sha(derived) for h in new_hits)
    assert client.get(base + "/content").json()["content"] == derived.decode()
    assert client.get(base + "/content", params={"revision": 1}).json()["content"] == captured["text"][:8000]
    assert client.get(base + "/download", params={"revision": 1}).content == captured["raw"]
    assert client.get(base + "/download", params={"revision": 2}).content == derived
    stale = client.put(base, files={"file": (captured["name"], b"Original stale mutation")}, data={"expected_revision": 1})
    assert stale.status_code == 409 and client.get(base).json()["current_revision"] == 2
    deleted = client.delete(base)
    assert deleted.status_code == 200
    assert search_all(client, pid, doc["id"], keyword, 1)[1] == []
    assert client.get(base + "/download", params={"revision": 1}).status_code == 404
    with client.app.state.store.connect() as conn:
        rows = conn.execute("SELECT revision,sha256,raw_bytes FROM document_versions WHERE document_id=? ORDER BY revision", (doc["id"],)).fetchall()
    assert [(r["revision"], r["sha256"], r["raw_bytes"]) for r in rows] == [(1, sha(captured["raw"]), captured["raw"]), (2, sha(derived), derived)]
    evidence.save("actual-history-search.json", {"query": keyword, "selected_original_lines": lines,
                  "before_pages": before_pages, "current_pages": current_pages,
                  "historical_pages": historical_pages, "new_current_hits": new_hits,
                  "updated": response.json(), "stale": stale.json(), "deleted": deleted.json(),
                  "mutation_source_kind": "synthetic-derived-fault", "parent_raw_sha256": sha(captured["raw"])})
    evidence.save("actual-history-search-revision2.bin", derived)
    evidence.observe()


def test_source_text_original_tail(client):
    captured = load_binding("source-text")
    evidence = Evidence(captured)
    pid, doc, base = ingest(client, captured, evidence)
    all_chunks(client, base, captured, evidence)
    from campaign_workflow_probes import source_lines
    selected = source_lines(captured, evidence)
    original_tail = selected[-1]
    keyword = original_tail["content"][-150:]
    pages, hits = search_all(client, pid, doc["id"], keyword, 1)
    hit = next(h for h in hits if h["line_start"] == original_tail["line"])
    assert hit["line_end"] == original_tail["line"]
    assert hit["sha256"] == doc["sha256"] and hit["revision"] == 1
    text = captured["text"]
    assert keyword.casefold() in text[hit["match_char_start"]:hit["match_char_end"]].casefold()
    response = client.get(base + "/content", params={"revision": 1,
                          "char_start": original_tail["char_start"], "char_limit": 32000})
    assert response.status_code == 200
    window = response.json()
    assert window["content"] == text[window["char_start"]:window["char_end"]]
    assert window["content"].startswith(original_tail["physical_line"])
    assert captured["raw"][original_tail["byte_start"]:original_tail["byte_end"]] == original_tail["physical_line"].encode()
    evidence.save("actual-original-tail-source.json", original_tail)
    evidence.save("actual-original-tail-search-pages.json", pages)
    evidence.save("actual-original-tail-reader-window.json", window)
    evidence.observe()
