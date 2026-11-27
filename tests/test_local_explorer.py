"""Actual selected-directory byte comparisons, tombstones and stable lazy pages."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import os
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest

from packages.domain.local_explorer import LocalExplorer
from packages.domain.local_workspaces import LocalWorkspaces, digest
from packages.domain.store import Conflict, InvalidInput
from test_project_intake import project


@pytest.fixture
def scene(client, tmp_path):
    folder = tmp_path.parent / (tmp_path.name + " 实际Explorer目录")
    folder.mkdir()
    (folder / "中文子目录").mkdir()
    (folder / "中文子目录/实际子文件.txt").write_bytes(b"child original")
    (folder / "a.txt").write_bytes(b"same-size-original")
    (folder / "z.txt").write_bytes(b"second-original")
    pid = project(client)
    result = client.post(f"/api/projects/{pid}/local-workspaces", json={
        "idempotency_key": "actual-explorer", "root": str(folder)})
    assert result.status_code == 201, result.text
    binding = result.json()
    value = {"client": client, "folder": folder, "pid": pid, "binding": binding,
             "service": client.app.state.local_explorer,
             "base": f'/api/projects/{pid}/local-workspaces/{binding["id"]}/explorer'}
    yield value
    target = os.environ.get("EEAS_EXPLORER_EVIDENCE")
    if target:
        out = Path(target) / tmp_path.name
        out.mkdir(parents=True, exist_ok=True)
        registry = client.app.state.local_workspaces._data(pid, binding["id"], "explorer")
        if registry.exists():
            shutil.copytree(registry, out / "immutable-explorer-records", dirs_exist_ok=True)
        if folder.exists():
            paths = [{"path": str(path.relative_to(folder)), "symlink": path.is_symlink(),
                      "directory": path.is_dir() if not path.is_symlink() else False}
                     for path in folder.rglob("*")]
            (out / "actual-source-entry-names.json").write_text(json.dumps(paths, ensure_ascii=False, indent=2))


def observe(scene, **params):
    result = scene["client"].get(scene["base"], params=params)
    assert result.status_code == 200, result.text
    return result.json()


def rows(value):
    return {entry["path"]: entry for entry in value["entries"]}


def test_real_unicode_directory_hashes_same_size_edit_added_deleted_and_restored(scene):
    first = observe(scene)
    assert first["total"] == 3 and first["entries"][0]["type"] == "directory"
    assert all(row["change"] == "UNCHANGED" for row in first["entries"])
    assert rows(first)["a.txt"]["verification"] == "CONTENT_SHA256"
    assert rows(first)["中文子目录"]["verification"] == "DIRECTORY_ENTRY_ONLY"
    assert first["scope"] == "LAZY_DIRECTORY_ONLY" and not first["atomic_filesystem_snapshot"]
    original = (scene["folder"] / "a.txt").read_bytes()
    (scene["folder"] / "a.txt").write_bytes(b"same-size-modified")
    assert len(original) == (scene["folder"] / "a.txt").stat().st_size
    (scene["folder"] / "新文件.md").write_text("真实新增", encoding="utf-8")
    (scene["folder"] / "z.txt").unlink()
    updated = observe(scene)
    changes = rows(updated)
    assert changes["a.txt"]["change"] == "MODIFIED"
    assert changes["新文件.md"]["change"] == "ADDED"
    assert changes["z.txt"]["change"] == "DELETED" and changes["z.txt"]["deleted"]
    assert updated["baseline_ref"] == first["baseline_ref"]
    assert rows(observe(scene))["z.txt"]["deleted"]
    (scene["folder"] / "z.txt").write_bytes(b"second-original")
    restored = rows(observe(scene))["z.txt"]
    assert restored["change"] == "UNCHANGED" and restored["restored"] and not restored["deleted"]
    (scene["folder"] / "新文件.md").unlink()
    assert rows(observe(scene))["新文件.md"]["change"] == "DELETED"
    (scene["folder"] / "新文件.md").write_text("真实新增", encoding="utf-8")
    restored_added = rows(observe(scene))["新文件.md"]
    assert restored_added["change"] == "ADDED" and restored_added["restored"]


def test_lazy_children_and_removed_directory_keep_observed_child_tombstones(scene):
    first = observe(scene)
    children = observe(scene, path="中文子目录")
    assert children["total"] == 1 and rows(children)["中文子目录/实际子文件.txt"]["change"] == "UNCHANGED"
    shutil.rmtree(scene["folder"] / "中文子目录")
    root = rows(observe(scene))
    assert root["中文子目录"]["deleted"] and root["中文子目录"]["type"] == "directory"
    missing = observe(scene, path="中文子目录")
    assert missing["directory_missing"] and missing["entries"][0]["deleted"]
    assert missing["baseline_ref"] == children["baseline_ref"]
    scene["folder"].joinpath("中文子目录").mkdir()
    (scene["folder"] / "中文子目录/实际子文件.txt").write_bytes(b"child original")
    assert rows(observe(scene, path="中文子目录"))["中文子目录/实际子文件.txt"]["restored"]
    assert first["baseline_ref"] == observe(scene)["baseline_ref"]
    result = scene["client"].get(scene["base"], params={"path": "未观察不存在目录"})
    assert result.status_code == 404


def test_snapshot_pages_never_mix_mutating_files_or_other_directory_scope(scene):
    first = observe(scene, limit=1)
    assert first["next_offset"] == 1
    (scene["folder"] / "new-after-first-page.txt").write_text("later")
    selected = first["observation_ref"]["id"]
    second = observe(scene, limit=1, offset=1, observation_id=selected)
    third = observe(scene, limit=1, offset=2, observation_id=selected)
    assert second["observation_ref"] == third["observation_ref"] == first["observation_ref"]
    assert third["next_offset"] is None and second["total"] == 3
    assert not any(row["path"] == "new-after-first-page.txt" for value in [first, second, third] for row in value["entries"])
    assert scene["client"].get(scene["base"], params={"offset": 1}).status_code == 409
    assert scene["client"].get(scene["base"], params={"path": "中文子目录", "observation_id": selected}).status_code == 404
    fresh = observe(scene)
    assert fresh["total"] == 4 and fresh["observation_ref"] != first["observation_ref"]
    assert observe(scene, observation_id=selected)["total"] == 3


def test_concurrent_cas_refresh_accepts_one_and_immutable_baseline_is_single(scene):
    first = observe(scene)
    service = scene["service"]
    def refresh(_):
        try:
            return service.observe(scene["pid"], scene["binding"]["id"], expected_observation_id=first["observation_ref"]["id"])
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(refresh, range(4)))
    assert sum(value is not None for value in values) == 1
    winner = next(value for value in values if value is not None)
    assert winner["observation_ref"]["revision"] == 2 and winner["baseline_ref"] == first["baseline_ref"]
    assert scene["client"].get(scene["base"], params={"expected_observation_id": first["observation_ref"]["id"]}).status_code == 409


def test_restart_keeps_added_then_deleted_tombstones_and_baseline(scene):
    original = observe(scene)
    (scene["folder"] / "added-then-removed.txt").write_text("added")
    assert rows(observe(scene))["added-then-removed.txt"]["change"] == "ADDED"
    (scene["folder"] / "added-then-removed.txt").unlink()
    removed = observe(scene)
    assert rows(removed)["added-then-removed.txt"]["deleted"]
    ws = LocalWorkspaces(scene["client"].app.state.store, scene["client"].app.state.settings.data_dir)
    restarted = LocalExplorer(ws)
    value = restarted.observe(scene["pid"], scene["binding"]["id"])
    assert value["baseline_ref"] == original["baseline_ref"]
    assert rows(value)["added-then-removed.txt"]["deleted"]
    assert restarted.observe(scene["pid"], scene["binding"]["id"], observation_id=removed["observation_ref"]["id"])["entries"] == removed["entries"]


def test_symlink_and_denied_read_are_unverified_never_deleted_or_outside_read(scene, tmp_path):
    observe(scene)
    outside = tmp_path.parent / (tmp_path.name + " outside-secret")
    outside.write_bytes(b"external bytes must not be observed")
    (scene["folder"] / "a.txt").unlink()
    (scene["folder"] / "a.txt").symlink_to(outside)
    current = rows(observe(scene))["a.txt"]
    assert current["change"] == "UNVERIFIED" and not current["deleted"] and current["sha256"] is None
    assert scene["client"].get(scene["base"], params={"path": "a.txt"}).status_code == 422
    ws = scene["service"].ws
    original = ws._raw
    def denied(binding, path):
        if path == "z.txt": raise InvalidInput("original handle read denied")
        return original(binding, path)
    with patch.object(ws, "_raw", side_effect=denied):
        denied_result = scene["service"].observe(scene["pid"], scene["binding"]["id"])
    row = rows(denied_result)["z.txt"]
    assert row["change"] == "UNVERIFIED" and not row["deleted"] and row["sha256"] is None
    assert b"external bytes" not in json.dumps(denied_result).encode()


def test_large_file_and_hash_budget_exhaustion_are_not_unchanged(scene):
    with (scene["folder"] / "large.bin").open("wb") as stream:
        stream.truncate(16 * 1024 * 1024 + 1)
    large = rows(observe(scene))["large.bin"]
    assert large["change"] == "UNVERIFIED" and large["reason"] == "FILE_READ_LIMIT_EXCEEDED"
    constrained = LocalExplorer(scene["service"].ws, max_hash_bytes=0)
    value = constrained.observe(scene["pid"], scene["binding"]["id"])
    assert value["hash_bytes"] == 0
    assert all(row["change"] == "UNVERIFIED" for row in value["entries"] if row["type"] == "file")


@pytest.mark.parametrize("part", ["baseline", "observation", "head"])
def test_tampered_records_are_rejected_and_not_rewritten(scene, part):
    first = observe(scene)
    service = scene["service"]
    directory = service._path(scene["binding"], "")
    path = directory / ("baseline.json" if part == "baseline" else "head.json" if part == "head" else
                        "observations/" + first["observation_ref"]["id"] + ".json")
    record = json.loads(path.read_bytes())
    record["payload"]["path"] = "different-scope"
    # Even a recomputed wrapper SHA cannot change the original immutable
    # observation identity or its admitted directory/project scope.
    record["sha256"] = digest(record["payload"])
    path.write_text(json.dumps(record))
    result = scene["client"].get(scene["base"])
    assert result.status_code == 409
    assert json.loads(path.read_bytes()) == record


def test_invalid_record_shape_and_namespace_drift_never_publish_a_fake_snapshot(scene):
    first = observe(scene)
    service = scene["service"]
    head = service._path(scene["binding"], "", "head.json")
    original_head = head.read_bytes()
    record = json.loads(original_head)
    del record["payload"]["observation_ref"]
    record["sha256"] = digest(record["payload"])
    head.write_text(json.dumps(record))
    assert scene["client"].get(scene["base"]).status_code == 409
    head.write_bytes(original_head)
    real = service.ws.tree
    count = 0
    def changed_namespace(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            (scene["folder"] / "actual-during-observation.txt").write_text("actual addition during listing")
        return real(*args, **kwargs)
    with patch.object(service.ws, "tree", side_effect=changed_namespace):
        result = scene["client"].get(scene["base"])
    assert result.status_code == 409 and "DIRECTORY_DRIFT" in result.text
    assert head.read_bytes() == original_head
    assert observe(scene, observation_id=first["observation_ref"]["id"])["total"] == 3


def test_project_isolation_root_identity_drift_and_escape_reject_without_fake_tombstones(scene, tmp_path):
    first = observe(scene)
    other = project(scene["client"])
    route = f'/api/projects/{other}/local-workspaces/{scene["binding"]["id"]}/explorer'
    assert scene["client"].get(route, params={"observation_id": first["observation_ref"]["id"]}).status_code == 404
    for path in ["../escape", "/etc", "C:\\outside", "中文子目录/../a.txt"]:
        assert scene["client"].get(scene["base"], params={"path": path}).status_code == 422
    original = tmp_path.parent / (tmp_path.name + " moved-root")
    scene["folder"].rename(original)
    scene["folder"].mkdir()
    result = scene["client"].get(scene["base"], params={"observation_id": first["observation_ref"]["id"]})
    assert result.status_code == 409 and "ROOT_DRIFT" in result.text


def test_observation_changes_no_source_bytes_creates_no_file_mirror_or_model_calls(scene):
    original = {path: path.read_bytes() for path in scene["folder"].rglob("*") if path.is_file()}
    observe(scene)
    observe(scene, path="中文子目录")
    assert all(path.read_bytes() == raw for path, raw in original.items())
    ws = scene["service"].ws
    assert not ws._data(scene["pid"], scene["binding"]["id"], "files").exists()
    with scene["client"].app.state.store.connect() as db:
        assert db.execute("SELECT count(*) FROM agent_executions").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM task_runs").fetchone()[0] == 0
