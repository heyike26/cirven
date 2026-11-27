"""Actual SQLite/PPTX/MCP/CLI. Only the remote model replies are fixtures."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import importlib.util
from io import BytesIO
import json
import os
from pathlib import Path
import site
import sys
import threading
import zipfile

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from pptx import Presentation

from packages.domain.store import Store, Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest
from test_agent_sessions import ModelWire, control, owner, retain, scene, tool, until
from test_pptx_export import template as original_pptx_template

ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "plugins/deliverable-authoring"
SCRIPT = PLUGIN / "office_mcp.py"
SKILL = PLUGIN / "skills/eeas-deliverable-authoring/SKILL.md"
SPEC = importlib.util.spec_from_file_location("actual_deliverable_authoring", SCRIPT)
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def source(store, pid):
    raw = "# 原创读书与工作记录\n已观察的结果和待确认项必须分开。\n".encode()
    value = store.save_document(pid, "原创资料.md", raw, "synthetic:deliverable-authoring-original")
    return {"kind": "document", "project_id": pid, "document_id": value["id"],
        "revision": value["revision"], "sha256": value["sha256"]}, raw


def request(ref, key="original_report_001"):
    return {"idempotency_key": key, "filename": "原创汇报.pptx",
        "report": {"schema_version": "eeas.report-deck.v1", "title": "原创可编辑汇报", "subtitle": "只验证实际文件",
            "source_refs": [ref], "sections": [{"title": "结果与限制", "paragraphs": ["保留来源修订与人工修改。"],
                "bullets": ["尚未验证 Windows/Office 视觉"]}]}}


@pytest.fixture
def project_scene(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    store = Store(data / "eeas.sqlite3")
    pid = store.create_project("原创可选插件测试", "not a company run")["id"]
    ref, raw = source(store, pid)
    return {"data": data, "store": store, "pid": pid, "ref": ref, "raw": raw,
        "adapter": MODULE.DeliverableAuthoring(store, data, pid)}


def actual_attempts(value):
    directory = value["data"] / "report-artifacts" / value["pid"] / "attempts"
    return list(directory.iterdir()) if directory.exists() else []


def retain_plugin(name, value, **extra):
    if not os.environ.get("EEAS_DELIVERABLE_EVIDENCE"):
        return
    out = Path(os.environ["EEAS_DELIVERABLE_EVIDENCE"])
    out.mkdir(parents=True, exist_ok=True)
    (out / (name + ".json")).write_text(canonical({"qualification": "ACTUAL_HOST_STORE_PPTX_MCP_NOT_WINDOWS",
        "project_id": value["pid"], "source_ref": value["ref"], "source_sha256": hashlib.sha256(value["raw"]).hexdigest(),
        "windows": "NOT_RUN", "paid_model_calls": 0, **extra}))


def test_real_editable_deck_actual_source_and_readonly_inspection(project_scene):
    value = project_scene
    result = value["adapter"].make_report_deck(request(value["ref"]))
    assert result["status"] == "SAVED_NOT_VISUALLY_VERIFIED"
    assert result["actual_source_refs"] == [value["ref"]]
    assert result["visual_validation"] == "NOT_RUN" and result["semantic_acceptance"] == "NOT_PERFORMED"
    manifest, raw = value["adapter"].artifacts.download(value["pid"], result["artifact_ref"]["id"])
    assert hashlib.sha256(raw).hexdigest() == result["artifact_ref"]["sha256"]
    deck = Presentation(BytesIO(raw))
    assert len(deck.slides) == 2 and deck.slides[0].shapes.title.text == "原创可编辑汇报"
    deck.slides[1].shapes.title.text = "原创人工改稿"
    edited = BytesIO()
    deck.save(edited)
    assert Presentation(BytesIO(edited.getvalue())).slides[1].shapes.title.text == "原创人工改稿"
    checked = value["adapter"].get_artifact(result["artifact_ref"])
    assert checked["inspection"]["slide_count"] == 2
    assert "保留来源修订" in canonical(checked["inspection"])
    assert value["store"].document(value["pid"], value["ref"]["document_id"], 1)["raw_bytes"] == value["raw"]
    assert value["adapter"].artifacts.download(value["pid"], result["artifact_ref"]["id"])[1] == raw
    assert len(actual_attempts(value)) == 1
    if os.environ.get("EEAS_DELIVERABLE_EVIDENCE"):
        out = Path(os.environ["EEAS_DELIVERABLE_EVIDENCE"])
        out.mkdir(parents=True, exist_ok=True)
        (out / "actual-original.pptx").write_bytes(raw)
        (out / "actual-separate-user-edit.pptx").write_bytes(edited.getvalue())
    retain_plugin("editable-pptx", value, result=result, inspection=checked["inspection"], manifest=manifest)


@pytest.mark.parametrize("bad", ["cross_project", "missing_source", "changed_hash", "extra_path", "bad_deck", "nonfinite"])
def test_invalid_request_or_source_never_admitted(project_scene, bad):
    value = project_scene
    body = request(value["ref"], "invalid_" + bad)
    if bad == "cross_project":
        other = value["store"].create_project("另工程", "independent")["id"]
        body["report"]["source_refs"][0] = {**value["ref"], "project_id": other}
    elif bad == "missing_source":
        body["report"]["source_refs"][0] = {**value["ref"], "document_id": "does-not-exist"}
    elif bad == "changed_hash":
        body["report"]["source_refs"][0] = {**value["ref"], "sha256": "0" * 64}
    elif bad == "extra_path":
        body["output_path"] = "/should-never-be-written.pptx"
    elif bad == "bad_deck":
        body["report"]["sections"] = []
    else:
        body["report"]["title"] = float("nan")
    with pytest.raises((MODULE.PluginError, InvalidInput, Conflict, NotFound, ValueError)):
        value["adapter"].make_report_deck(body)
    assert value["adapter"].get_operation(body["idempotency_key"])["status"] == "NOT_ADMITTED"
    assert not actual_attempts(value)


def test_repeated_known_operation_only_reads_and_different_input_rejects(project_scene):
    value = project_scene
    body = request(value["ref"])
    first = value["adapter"].make_report_deck(body)
    second = value["adapter"].make_report_deck(body)
    assert first["artifact_ref"] == second["artifact_ref"]
    assert second["operation_observation"] == "READ_EXISTING_ONLY"
    assert len(actual_attempts(value)) == 1
    body["report"]["title"] = "不同意图"
    with pytest.raises(MODULE.PluginError, match="IDEMPOTENCY_CONFLICT"):
        value["adapter"].make_report_deck(body)
    assert len(actual_attempts(value)) == 1


def test_real_registered_template_preserves_original_and_unmodified_parts(project_scene):
    value = project_scene
    raw = original_pptx_template()
    saved = value["store"].save_document(value["pid"], "原创汇报模板.pptx", raw, "synthetic:original-template")
    body = request(value["ref"], "actual_registered_template")
    body["template"] = {"document_id": saved["id"], "revision": saved["revision"], "sha256": saved["sha256"]}
    result = value["adapter"].make_report_deck(body)
    assert result["status"] == "SAVED_NOT_VISUALLY_VERIFIED"
    manifest, output = value["adapter"].artifacts.download(value["pid"], result["artifact_ref"]["id"])
    assert result["artifact_manifest"]["template_ref"] == body["template"]
    deck = Presentation(BytesIO(output))
    assert len(deck.slides) == 1 and "原创可编辑汇报" in deck.slides[0].shapes[0].text
    with zipfile.ZipFile(BytesIO(raw)) as before, zipfile.ZipFile(BytesIO(output)) as after:
        assert before.read("custom/original.xml") == after.read("custom/original.xml")
    assert value["store"].document(value["pid"], saved["id"], saved["revision"])["raw_bytes"] == raw
    assert manifest["audit"]["text_fit_and_visual_rendering"] == "NOT_VALIDATED"


def test_two_real_instances_concurrent_same_key_publish_once(project_scene):
    value = project_scene
    barrier = threading.Barrier(2)
    def run():
        adapter = MODULE.DeliverableAuthoring(Store(value["data"] / "eeas.sqlite3"), value["data"], value["pid"])
        barrier.wait(timeout=5)
        return adapter.make_report_deck(request(value["ref"]))
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(run) for _ in range(2)]
        results = [future.result(timeout=20) for future in futures]
    assert {row["status"] for row in results} <= {"SAVED_NOT_VISUALLY_VERIFIED", "UNKNOWN_NOT_REPLAYED"}
    assert any(row["status"] == "SAVED_NOT_VISUALLY_VERIFIED" for row in results)
    assert len(actual_attempts(value)) == 1


def test_unknown_incomplete_and_oversized_admission_survive_restart_without_replay(project_scene):
    value = project_scene
    for key, raw in (("interrupted", b'{"incomplete"'), ("oversized", b"x" * 9000)):
        aid, path, _ = value["adapter"]._paths(key)
        path.parent.mkdir(parents=True)
        path.write_bytes(raw)
        restarted = MODULE.DeliverableAuthoring(Store(value["data"] / "eeas.sqlite3"), value["data"], value["pid"])
        assert restarted.get_operation(key)["status"] == "UNKNOWN_NOT_REPLAYED"
        assert restarted.make_report_deck(request(value["ref"], key))["status"] == "UNKNOWN_NOT_REPLAYED"
        assert path.read_bytes() == raw
    body = request(value["ref"], "valid_admitted_before_dispatch")
    aid, path, _ = value["adapter"]._paths(body["idempotency_key"])
    path.parent.mkdir(parents=True)
    checksum = digest({"project_id": value["pid"], "filename": body["filename"], "report": body["report"], "template": None})
    MODULE.write_once(path, {"schema": "eeas.deliverable-admission.v1", "project_id": value["pid"],
        "artifact_id": aid, "idempotency_key": body["idempotency_key"], "request_sha256": checksum})
    assert value["adapter"].make_report_deck(body)["status"] == "UNKNOWN_NOT_REPLAYED"
    assert not actual_attempts(value)


def test_orphan_directory_and_oversized_request_are_not_new_execution(project_scene):
    value = project_scene
    key = "orphaned_empty_directory"
    _, path, _ = value["adapter"]._paths(key)
    path.parent.mkdir(parents=True)
    result = value["adapter"].make_report_deck(request(value["ref"], key))
    assert result["status"] == "UNKNOWN_NOT_REPLAYED" and not path.exists()
    body = request(value["ref"], "oversized_request")
    body["report"]["subtitle"] = "x" * (MODULE.MAX_INPUT_BYTES + 1)
    with pytest.raises(MODULE.PluginError, match="JSON_BUDGET_EXCEEDED"):
        value["adapter"].make_report_deck(body)
    assert value["adapter"].get_operation("oversized_request")["status"] == "NOT_ADMITTED"
    assert not actual_attempts(value)


def test_exact_ref_scope_hash_and_corrupt_artifact_are_rejected(project_scene):
    value = project_scene
    result = value["adapter"].make_report_deck(request(value["ref"]))
    ref = result["artifact_ref"]
    for changes in ({"project_id": "another"}, {"revision": 2}, {"sha256": "0" * 64}, {"path": "../outside"}):
        with pytest.raises(MODULE.PluginError):
            value["adapter"].get_artifact({**ref, **changes})
    path = value["adapter"].artifacts._path(value["pid"], ref["id"], "report.pptx")
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(Conflict):
        value["adapter"].get_artifact(ref)
    with pytest.raises(Conflict):
        value["adapter"].get_operation("original_report_001")


def test_capacity_and_blocked_storage_preserve_old_artifact(project_scene):
    value = project_scene
    adapter = MODULE.DeliverableAuthoring(value["store"], value["data"], value["pid"], max_admissions=1)
    first = adapter.make_report_deck(request(value["ref"]))
    assert adapter.make_report_deck(request(value["ref"], "second"))["status"] == "NOT_ADMITTED_CAPACITY"
    assert adapter.get_artifact(first["artifact_ref"])["artifact_ref"] == first["artifact_ref"]
    other = value["store"].create_project("storage blocked", "original")["id"]
    ref, _ = source(value["store"], other)
    broken = MODULE.DeliverableAuthoring(value["store"], value["data"], other)
    root = value["data"] / "deliverable-authoring" / other
    root.mkdir(parents=True)
    (root / "operations").write_bytes(b"original non-directory must survive")
    with pytest.raises((OSError, MODULE.PluginError)):
        broken.make_report_deck(request(ref, "blocked"))
    assert (root / "operations").read_bytes() == b"original non-directory must survive"


async def mcp_call(parameters, action):
    async with stdio_client(parameters, errlog=sys.__stderr__) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return await action(session)


def decoded(result):
    assert not result.is_error, result
    if result.structured_content is not None:
        return result.structured_content
    return json.loads(next(part.text for part in result.content if part.type == "text"))


def parameters(value):
    return StdioServerParameters(command=sys.executable, args=["-I", str(SCRIPT), "--data-dir", str(value["data"]),
        "--project-id", value["pid"], "--source-root", str(ROOT)],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1", "TEMP": str(value["data"]), "TMP": str(value["data"])})


def test_official_sdk_actual_stdio_create_inspect_and_restart_readonly(project_scene):
    value = project_scene
    async def first(session):
        listed = await session.list_tools()
        assert {entry.name for entry in listed.tools} == {"make_report_deck", "get_report_artifact", "get_report_operation"}
        created = decoded(await session.call_tool("make_report_deck", {"request": request(value["ref"])}))
        inspected = decoded(await session.call_tool("get_report_artifact", {"artifact_ref": created["artifact_ref"]}))
        assert inspected["inspection"]["slide_count"] == 2
        return created
    created = asyncio.run(mcp_call(parameters(value), first))
    async def after_restart(session):
        observed = decoded(await session.call_tool("get_report_operation", {"idempotency_key": "original_report_001"}))
        assert observed["artifact_ref"] == created["artifact_ref"]
        rejected = await session.call_tool("get_report_artifact", {"artifact_ref": {**created["artifact_ref"], "project_id": "other"}})
        assert rejected.is_error
        assert "EXACT_PROJECT_ARTIFACT_REF_REQUIRED" in canonical(rejected.model_dump(mode="json"))
        return observed
    observed = asyncio.run(mcp_call(parameters(value), after_restart))
    assert len(actual_attempts(value)) == 1
    retain_plugin("official-stdio-restart", value, created=created, observed=observed)


def test_actual_missing_optional_component_without_faking_store_or_sdk(project_scene, tmp_path):
    value = project_scene
    isolated = tmp_path / "actual-package-links-without-pptx"
    isolated.mkdir()
    # Small symlinks only; no package install/copy. -S excludes ambient site.
    original = Path(site.getsitepackages()[0])
    for path in original.iterdir():
        if path.name == "pptx" or path.name.lower().startswith("python_pptx-"):
            continue
        if path.name == "__pycache__":
            continue
        (isolated / path.name).symlink_to(path, target_is_directory=path.is_dir())
    code = ("import sys,importlib.util;sys.path[:0]=" + repr([str(isolated), str(ROOT)]) + ";"
        "s=importlib.util.spec_from_file_location('external_plugin'," + repr(str(SCRIPT)) + ");"
        "m=importlib.util.module_from_spec(s);s.loader.exec_module(m);"
        "from packages.domain.store import Store;from pathlib import Path;"
        "a=m.DeliverableAuthoring(Store(Path(" + repr(str(value["data"] / "eeas.sqlite3")) + ")),"
        + repr(str(value["data"])) + "," + repr(value["pid"]) + ");m.create_server(a).run(transport='stdio')")
    params = StdioServerParameters(command=sys.executable, args=["-S", "-c", code],
        env={"PATH": os.environ.get("PATH", ""), "PYTHONDONTWRITEBYTECODE": "1"})
    async def run(session):
        result = decoded(await session.call_tool("make_report_deck", {"request": request(value["ref"], "missing_component")}))
        assert result["status"] == "NOT_ADMITTED_COMPONENT_UNAVAILABLE"
        assert result["component"]["code"] == "OPTIONAL_PYTHON_PPTX_MISSING"
        assert decoded(await session.call_tool("get_report_operation", {"idempotency_key": "missing_component"}))["status"] == "NOT_ADMITTED"
        return result
    result = asyncio.run(mcp_call(params, run))
    assert not actual_attempts(value)
    retain_plugin("missing-real-component-runtime", value, result=result, boundary="PYTHON_S_WITH_REAL_DEPENDENCY_LINKS_EXCEPT_PPTX")


def test_real_skill_import_and_actual_original_cli_calls_selected_mcp(scene):
    skill = scene["manager"].import_skill({"operation_id": "install-authoring-md", "id": "eeas-deliverable-authoring",
        "expected_revision": 0, "content": SKILL.read_text(), "enabled": True})
    ref, raw = source(scene["store"], scene["pid"])
    scene.update(ref=ref, raw=raw)
    executable = Path(sys.executable).resolve()
    files = {str(path): hashlib.sha256(path.read_bytes()).hexdigest() for path in (executable, SCRIPT, PLUGIN / "manifest.json", SKILL)}
    saved = scene["manager"].configure_mcp({"operation_id": "configure-optional-authoring", "id": "deliverable-authoring",
        "expected_revision": 0, "configuration": {"type": "local", "command": [str(executable), "-I", str(SCRIPT),
            "--data-dir", str(scene["data"]), "--project-id", scene["pid"], "--source-root", str(ROOT)],
            "files_sha256": files, "enabled": True, "timeout": 15000}})
    name = "deliverable-authoring_make_report_deck"
    wire = ModelWire([tool("skill", {"name": "eeas-deliverable-authoring"}),
        tool(name, {"request": request(ref, "actual_original_cli")}), "ACTUAL_OPTIONAL_AUTHORING_RETURNED"]).start()
    async def run():
        value, body = owner(scene, wire)
        body.update(allowed_effects=["PROCESS_START"], mcp_tool_allowlist=[name])
        try:
            opened = await value.open(scene["pid"], body)
            ident = opened["session_ref"]["id"]
            assert opened["capability_load"]["mcp"]["deliverable-authoring"]["loaded_in_original_session"]
            await value.send(scene["pid"], ident, {**control(opened, "make-actual-deliverable"),
                "raw_text": "Use the selected optional authoring skill and admitted tool to make the original local report."})
            pending = await until(value, scene, ident, lambda current: bool(current["pending_permissions"]))
            permission = next(item for item in pending["pending_permissions"] if item["native_request"]["permission"] == name)
            await value.reply(scene["pid"], ident, permission["request_ref"]["id"],
                {**control(opened, "allow-one-original-create"), "request_ref": permission["request_ref"], "reply": "once"}, kind="PERMISSION")
            final = await until(value, scene, ident, lambda current: current["active_operation_id"] is None)
            loaded_skill = next(item for item in final["capability_load"]["skills"] if item["id"] == "eeas-deliverable-authoring")
            assert loaded_skill["invoked"] and loaded_skill["config_ref"] == skill["config_ref"]
            assert final["capability_load"]["mcp"]["deliverable-authoring"]["invoked"]
            adapter = MODULE.DeliverableAuthoring(scene["store"], scene["data"], scene["pid"])
            result = adapter.get_operation("actual_original_cli")
            assert result["status"] == "SAVED_NOT_VISUALLY_VERIFIED" and len(actual_attempts(scene)) == 1
            assert len(wire.requests) == 3 and scene["actual"].read_bytes() == scene["original"]
            retain("optional-authoring-original-cli", scene, value, wire, final)
            retain_plugin("actual-original-cli-tool", scene, result=result, skill_ref=skill["config_ref"], mcp_ref=saved["config_ref"], rpc_count=len(wire.requests))
        finally:
            await value.close()
    try:
        asyncio.run(run())
    finally:
        wire.close()
