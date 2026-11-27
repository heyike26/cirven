"""Actual selected files, original OpenCode 1.19.0, authoring and offline OCR.

Only the external model endpoint is a protocol fixture. No planner, transport,
source authority, OCR engine, artifact renderer or exporter is substituted.
This is Linux/original-protocol evidence, not company AI or native Windows proof.
"""
from copy import deepcopy
from io import BytesIO
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
from zipfile import ZipFile

from PIL import Image, ImageDraw, ImageFont
from docx import Document
import pytest

from conftest import ROOT
from discovery_native_wire import DiscoveryNativeWire
from packages.adapters.offline_ocr import OfflineOCR
from packages.adapters.opencode_planning import OpenCodePlanningTransport
from packages.adapters.opencode_profile import CLI_SHA, COMMIT, VERSION
from packages.domain.discovery import DiscoveryWorkbench
from packages.domain.local_workspaces import canonical, digest
from test_opencode_planning import CLI, KEY, MODEL
from test_project_intake import project
from test_workbench_feedback import body as feedback_body, route as feedback_route


BUDGET = {"max_calls": 1, "timeout_seconds": 60, "max_output_tokens": 8192}
SOURCE_FILES = {
    "需求 原件.md": "# 原创项目需求\r\nREQ: 每个需求保留原文件修订与可核对来源。\r\nREQ: 缺失范围必须由用户回答并明确接受。\r\n".encode(),
    "限制与验收.txt": "来源清单覆盖本次选中的两份材料。\n模型建议须人工审阅；保存计划不等于授权执行。\n".encode(),
}


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _ref(value):
    return {key: value[key] for key in ("id", "revision", "sha256")}


class _Evidence:
    """Keep actual responses and bytes, including a failed development attempt."""
    def __init__(self, client, directory):
        self.client, self.directory = client, directory
        directory.mkdir(parents=True, exist_ok=True)
        self.calls, self.details = [], {}

    def json(self, method, url, *, body=None, status=200, params=None):
        response = self.client.request(method, url, json=body, params=params)
        self.calls.append({"method": method, "url": url, "params": params,
            "input": body, "status_code": response.status_code,
            "response": response.json()})
        self.flush()
        assert response.status_code == status, response.text
        return response.json()

    def download(self, descriptor, name):
        response = self.client.get(descriptor["download_url"])
        (self.directory / name).write_bytes(response.content)
        self.calls.append({"method": "GET", "url": descriptor["download_url"],
            "status_code": response.status_code, "stored_name": name,
            "sha256": _sha(response.content), "byte_length": len(response.content),
            "content_type": response.headers.get("content-type")})
        self.flush()
        assert response.status_code == 200, response.text
        assert _sha(response.content) == descriptor["sha256"]
        assert len(response.content) == descriptor["byte_length"]
        assert response.headers["content-type"].startswith(descriptor["media_type"])
        return response.content

    def flush(self):
        # The external fixture credential must not appear in retained evidence.
        raw = canonical({"qualification": "ACTUAL_ORIGINAL_CLI_EXTERNAL_MODEL_PROTOCOL_FIXTURE_ONLY",
            "native_windows": "NOT_RUN", "company_model": "NOT_RUN",
            "paid_model_calls": 0, "calls": self.calls, "details": self.details})
        (self.directory / "receipt.json").write_text(raw.replace(KEY, "<fixture-credential-redacted>"))


@pytest.fixture
def original_chain(client, tmp_path, request):
    assert VERSION == "1.19.0"
    assert CLI.is_file(), "Prepare the pinned actual original CLI; a fake transport is not permitted"
    assert _sha(CLI.read_bytes()) == CLI_SHA[os.name]
    wire = DiscoveryNativeWire().start()
    parent = os.environ.get("EEAS_ORIGINAL_E2E_EVIDENCE")
    directory = (Path(parent) if parent else tmp_path / "original-chain-evidence") / request.node.name
    evidence = _Evidence(client, directory)
    actual = tmp_path.parent / (tmp_path.name + " 实际所选工程目录")
    actual.mkdir()
    originals = directory / "selected-originals"
    originals.mkdir(exist_ok=True)
    for name, raw in SOURCE_FILES.items():
        (actual / name).write_bytes(raw)
        (originals / name).write_bytes(raw)
    pid = project(client)
    scene = {"pid": pid, "wire": wire, "actual": actual, "evidence": evidence,
             "store": client.app.state.store, "client": client}
    try:
        binding = evidence.json("POST", f"/api/projects/{pid}/local-workspaces", body={
            "idempotency_key": "actual-selected-folder", "root": str(actual),
            "allowed_write_paths": ["harness-output/"]}, status=201)
        workspace = {key: binding[key] for key in ("id", "revision", "binding_sha256")}
        materials = evidence.json("POST", f'/api/projects/{pid}/local-workspaces/{binding["id"]}/materials',
            body={"operation_id": "read-real-selected-files", "paths": list(SOURCE_FILES)}, status=201)
        # Reuse the actual pinned original binary without another 130 MiB copy.
        # The normal product provider path still hashes and runs these bytes;
        # neither its resolver nor the native planning transport is replaced.
        executable = client.app.state.provider_settings.path("runtime", VERSION, "opencode.exe" if os.name == "nt" else "opencode")
        executable.parent.mkdir(parents=True, exist_ok=True)
        os.link(CLI, executable)
        profile = evidence.json("POST", "/api/planning/providers", body={"operation_id": "real-wire-profile",
            "id": "original-discovery-e2e", "provider_url": wire.base_url,
            "model_id": MODEL, "api_key": KEY, "supports_images": False}, status=201)
        config = client.app.state.provider_settings.configuration(profile["profile_ref"]["id"], 1)
        assert _sha(Path(config["executable"]).read_bytes()) == CLI_SHA[os.name]
        assert config["version"] == VERSION and config["source_commit"] == COMMIT
        assert isinstance(client.app.state.discovery.transport, OpenCodePlanningTransport)
        assert client.app.state.discovery.transport is client.app.state.planning_proposals.transport
        view = evidence.json("POST", f"/api/projects/{pid}/discovery/sessions", body={
            "operation_id": "start-from-real-requirements", "title": "原创需求完整组合验收",
            "entry_mode": "REQUIREMENTS", "statement": "减少资料不清导致的返工；按真实材料发现并确认要求。",
            "materials": [item["document_ref"] for item in materials["materials"]],
            "workspace_ref": workspace}, status=201)
        selected = [doc for doc in view["payload"]["source_receipt"]["documents"] if doc.get("local_file_source_ref")]
        assert len(selected) == len(SOURCE_FILES)
        for doc in selected:
            source = doc["local_file_source_ref"]
            assert source["workspace_id"] == binding["id"] and source["project_id"] == pid
            assert source["sha256"] == doc["raw_sha256"] == _sha(SOURCE_FILES[source["path"]])
            assert doc["document_id"] in [item["document_ref"]["document_id"] for item in materials["materials"]]
        scene.update(binding=binding, workspace=workspace, materials=materials,
                     profile=profile["profile_ref"], view=view,
                     base=f'/api/projects/{pid}/discovery/sessions/{view["id"]}')
        evidence.details.update(original_cli={"version": VERSION, "source_commit": COMMIT,
            "sha256": CLI_SHA[os.name]}, selected_workspace=workspace,
            selected_source_sha256={name: _sha(raw) for name, raw in SOURCE_FILES.items()})
        yield scene
    finally:
        # Preserve protocol, exact admitted execution requests/scopes and original
        # native logs without copying dependencies or a full runtime/data tree.
        evidence.details["wire_requests"] = wire.requests
        with scene["store"].connect() as db:
            rows = db.execute("SELECT * FROM agent_executions WHERE project_id=? ORDER BY rowid", (pid,)).fetchall()
            evidence.details["executions"] = [dict(row) for row in rows]
            evidence.details["scopes"] = [dict(row) for row in db.execute(
                "SELECT s.* FROM agent_scopes s JOIN agent_executions e ON e.id=s.execution_id WHERE e.project_id=?", (pid,))]
            evidence.details["task_runs"] = db.execute("SELECT count(*) FROM task_runs").fetchone()[0]
        for index, row in enumerate(rows):
            logs = directory / f"original-native-{index + 1}"
            logs.mkdir(exist_ok=True)
            for name in ("http-starts.jsonl", "http-responses.jsonl", "native-events.jsonl",
                         "server-stdout.bin", "server-stderr.bin", "planning-native-result.json"):
                path = Path(row["workspace"]) / name
                if path.is_file():
                    (logs / name).write_bytes(path.read_bytes().replace(KEY.encode(), b"<fixture-credential-redacted>"))
        if actual.exists():
            shutil.copytree(actual, directory / "selected-folder-final", dirs_exist_ok=True)
        evidence.flush()
        wire.close()


def _no_execution(scene, native_count):
    with scene["store"].connect() as db:
        assert db.execute("SELECT count(*) FROM task_runs").fetchone()[0] == 0
        assert db.execute("SELECT count(*) FROM agent_executions WHERE project_id=?", (scene["pid"],)).fetchone()[0] == native_count


def _native_scope(scene, execution):
    assert execution["status"] == "RETURNED", canonical(execution)
    assert execution["session_id"].startswith("ses_")
    assert execution["usage"]["rpc_attempts"] == 1
    with scene["store"].connect() as db:
        record = db.execute("SELECT e.request,s.scope FROM agent_executions e JOIN agent_scopes s ON e.id=s.execution_id WHERE e.id=?",
                            (execution["execution_id"],)).fetchone()
    admitted, scope = json.loads(record["request"]), json.loads(record["scope"])
    assert scope["cli_version"] == VERSION and scope["upstream_commit"] == COMMIT
    assert scope["cli_sha256"] == CLI_SHA[os.name]
    assert scope["workspace_ref"] == scene["workspace"]
    assert scope["source_receipt_sha256"] == execution["source_receipt_sha256"] == admitted["source_receipt"]["sha256"]
    assert scope["permission_policy"] == "DENY_ALL_NATIVE_TOOLS_EXCEPT_STRUCTURED_OUTPUT"
    assert scope["budget"] == BUDGET
    return admitted, scope


def _diagram(scene, view, suffix):
    evidence, base = scene["evidence"], scene["base"]
    descriptor = evidence.json("GET", base + "/diagrams/architecture/artifact", params={"revision": view["revision"]})
    assert descriptor["session_ref"] == _ref(view)
    graph = view["payload"]["candidate"]["graphs"][0]
    assert descriptor["model_sha256"] == digest(graph)
    raw = {kind: evidence.download(item, f"{suffix}-architecture.{kind}") for kind, item in descriptor["files"].items()}
    model = json.loads(raw["json"])
    assert model["session_ref"] == _ref(view) and model["graph"] == graph
    assert b"<svg" in raw["svg"]
    with Image.open(BytesIO(raw["png"])) as image:
        assert image.format == "PNG" and min(image.size) > 100
        assert image.convert("RGB").getextrema() != ((255, 255), (255, 255), (255, 255))
    return descriptor, raw


def _report(scene, base, revision, diagram, suffix):
    evidence = scene["evidence"]
    body = {"operation_id": suffix, "expected_revision": revision, "format": "docx",
            "diagram_refs": [diagram["artifact_ref"]]}
    value = evidence.json("POST", base + "/reports", body=body, status=201)
    blobs = {key: evidence.download(value[key], suffix + {"artifact": ".docx", "pending": "-pending.md",
        "pending_json": "-pending.json", "source_snapshot": "-source.json"}[key])
        for key in ("artifact", "pending", "pending_json", "source_snapshot")}
    snapshot = json.loads(blobs["source_snapshot"])
    assert digest(snapshot) == value["artifact"]["id"]
    assert snapshot["diagrams"][0]["artifact_ref"] == diagram["artifact_ref"]
    frozen, = snapshot["diagrams"]
    authority = value.get("session_ref") or value["proposal_ref"]
    manifest = scene["client"].app.state.planning_reports._artifact(
        scene["pid"], authority["id"], value["artifact"]["id"])
    reading, = manifest["render_receipt"]["diagram_readability"]
    assert _sha(base64.b64decode(frozen["png_base64"], validate=True)) == frozen["png_sha256"] == diagram["files"]["png"]["sha256"] == reading["source_png_sha256"]
    assert digest(frozen["graph"]) == diagram["model_sha256"] == reading["graph_sha256"]
    assert reading["projection"] == "CIRVEN_WORD_READING_PROJECTION_V1"
    document = Document(BytesIO(blobs["artifact"]))
    shape, = document.inline_shapes
    image_ref = shape._inline.graphic.graphicData.pic.blipFill.blip.embed
    picture = document.part.related_parts[image_ref]
    assert picture.content_type == "image/png" and _sha(picture.blob) == reading["png_sha256"]
    return body, value, blobs, snapshot


def test_actual_original_cli_selected_files_to_confirmed_harness_and_drift(original_chain):
    scene = original_chain
    evidence, client, pid, base = scene["evidence"], scene["client"], scene["pid"], scene["base"]
    view = scene["view"]
    evidence.json("POST", base + "/plan", body={"operation_id": "too-early", "expected_revision": 1}, status=409)
    assert not scene["wire"].requests
    request = {"operation_id": "discover-real-materials", "expected_revision": 1,
        "message": "按选中两份原件提出要求、范围问题和可核对架构，未确认前不拆解或执行。",
        "workspace_ref": scene["workspace"], "provider_profile_ref": scene["profile"], "budget": BUDGET}
    view = evidence.json("POST", base + "/advance", body=request)
    _native_scope(scene, view["payload"]["execution"])
    assert view["revision"] == 2 and not view["readiness"]["can_plan"]
    assert len(scene["wire"].requests) == 1
    assert evidence.json("POST", base + "/advance", body=request) == view
    assert len(scene["wire"].requests) == 1
    draft_diagram, _ = _diagram(scene, view, "draft")
    draft_body, draft_report, draft_blobs, draft_snapshot = _report(scene, base, view["revision"], draft_diagram, "draft-discovery-report")
    assert draft_report["status"] == "DRAFT" and draft_snapshot["source_kind"] == "DISCOVERY"
    assert json.loads(draft_blobs["pending_json"])["pending_count"] == 2
    assert all(q["status"] == "AWAITING_ANSWER" for q in json.loads(draft_blobs["pending_json"])["questions"])
    assert not view["readiness"]["execution_authorized"]
    scope_answer = evidence.json("POST", base + "/answers", body={"operation_id": "answer-scope",
        "expected_revision": view["revision"], "question_id": "scope", "text": "首批只核对当前所选两份原件。"})
    optional_answer = evidence.json("POST", base + "/answers", body={"operation_id": "answer-language",
        "expected_revision": view["revision"], "question_id": "optional", "text": "本阶段只输出中文。"})
    pending = evidence.json("GET", base + "/pending")
    assert all(q["status"] == "ANSWERED_PENDING_ACCEPTANCE" for q in pending["questions"])
    assert not scope_answer["accepted"] and not optional_answer["accepted"]
    # A fresh service sees persisted answers, independently of the HTTP view.
    restored = DiscoveryWorkbench(scene["store"], client.app.state.planning_proposals)
    assert {a["id"] for a in restored.get(pid, view["id"])["answers"]} == {scope_answer["id"], optional_answer["id"]}
    confirmation = {"operation_id": "accept-requirements", "expected_revision": view["revision"],
        "session_sha256": view["sha256"], "user_confirmed": True,
        "accepted_claim_ids": ["req"], "accepted_graph_ids": ["architecture"]}
    evidence.json("POST", base + "/confirm", body=confirmation, status=409)
    confirmation["resolutions"] = [{"question_id": "scope", "mode": "ANSWER_ACCEPTED",
        "answer_id": scope_answer["id"], "reason": "用户明确接受仅核对当前原件的范围。"}]
    confirmed = evidence.json("POST", base + "/confirm", body=confirmation)
    assert confirmed["readiness"]["can_plan"] and not confirmed["readiness"]["execution_authorized"]
    questions = {q["id"]: q for q in view["payload"]["candidate"]["questions"]}
    frozen_answers = [
        {**scope_answer, "answer_sha256": digest(scope_answer), "question": questions["scope"],
         "applies_to_confirmed_question": True, "accepted": True},
        {**optional_answer, "answer_sha256": digest(optional_answer), "question": questions["optional"],
         "applies_to_confirmed_question": True, "accepted": False},
    ]
    assert confirmed["payload"]["answers_snapshot"] == frozen_answers
    pending = evidence.json("GET", base + "/pending")
    assert pending["pending_count"] == 1 and pending["questions"][0]["id"] == "optional"
    assert pending["questions"][0]["status"] == "ANSWERED_PENDING_ACCEPTANCE"
    assert json.loads(evidence.download(draft_report["pending_json"], "draft-pending-after-answer.json"))["pending_count"] == 2
    confirmed_diagram, _ = _diagram(scene, confirmed, "confirmed")
    plan_body = {"operation_id": "decompose-confirmed-requirements", "expected_revision": confirmed["revision"],
        "provider_profile_ref": scene["profile"], "budget": BUDGET}
    planned = evidence.json("POST", base + "/plan", body=plan_body)
    proposal = planned["proposal"]
    admitted, _ = _native_scope(scene, proposal["payload"]["execution"])
    assert proposal["payload"]["discovery_ref"] == _ref(confirmed)
    assert not proposal["payload"]["implementation_started"] and len(scene["wire"].requests) == 2
    baseline = next(doc for doc in admitted["source_receipt"]["documents"] if doc["source"].startswith("discovery-confirmed-baseline:"))
    assert confirmed["sha256"] in baseline["text_or_selected_chunks"]
    assert "首批只核对当前所选两份原件。" in baseline["text_or_selected_chunks"]
    baseline_model = json.loads(baseline["text_or_selected_chunks"].split("```json\n", 1)[1].rsplit("```", 1)[0])
    assert baseline_model["questions_and_answers"] == frozen_answers
    assert baseline_model["questions"] == view["payload"]["candidate"]["questions"]
    assert baseline_model["unaccepted_answers_are_not_requirements"] is True
    assert baseline_model["accepted_requirements"] == [confirmed["payload"]["candidate"]["claims"][0]]
    assert all(name in [doc["filename"] for doc in admitted["source_receipt"]["documents"]] for name in SOURCE_FILES)
    assert evidence.json("POST", base + "/plan", body=plan_body) == planned
    assert len(scene["wire"].requests) == 2
    proposal_base = f'/api/projects/{pid}/planning/proposals/{proposal["id"]}'
    report_body, report, blobs, snapshot = _report(scene, proposal_base, proposal["revision"], confirmed_diagram, "draft-task-report")
    assert report["status"] == "DRAFT" and snapshot["source_kind"] == "PROPOSAL"
    assert _ref(snapshot["discovery"]) == _ref(confirmed)
    assert snapshot["discovery"]["payload"]["status"] == "CONFIRMED"
    with ZipFile(BytesIO(blobs["artifact"])) as package:
        xml = package.read("word/document.xml").decode()
        assert all(text in xml for text in ["核对资料", "编写任务报告", "输出可核对原始来源的需求清单。"])
    approved = evidence.json("POST", proposal_base + "/confirm", body={"operation_id": "accept-task-plan",
        "expected_revision": proposal["revision"], "proposal_sha256": proposal["sha256"], "user_confirmed": True})
    assert approved["payload"]["status"] == "CONFIRMED" and approved["review_status"] == "CURRENT"
    assert not approved["payload"]["confirmation"]["execution_authorized"]
    harness = evidence.json("GET", proposal_base + "/harness")
    context = {item["filename"]: item for item in harness["context_files"]}
    background = json.loads(context["discovery-context.json"]["content"])
    assert background["discovery_ref"] == _ref(confirmed)
    assert background["payload"] == confirmed["payload"]
    assert background["answers"] == frozen_answers
    assert background["payload"]["candidate"]["competitors"][0]["observations"][0]["status"] == "UNKNOWN"
    assert background["payload"]["candidate"]["graphs"] == confirmed["payload"]["candidate"]["graphs"]
    # Live clarification can continue after confirmation, but cannot silently
    # rewrite the frozen answer context of the same accepted task plan.
    late_answer = evidence.json("POST", base + "/answers", body={"operation_id": "late-language-answer",
        "expected_revision": planned["session"]["revision"], "question_id": "optional",
        "text": "确认之后补充：将来可能需要英文，现在尚未接受。"})
    live = evidence.json("GET", base)
    assert late_answer["id"] in {a["id"] for a in live["answers"]}
    assert not next(a for a in live["answers"] if a["id"] == late_answer["id"])["accepted"]
    assert live["payload"]["answers_snapshot"] == frozen_answers
    assert evidence.json("GET", proposal_base + "/harness") == harness
    assert evidence.json("GET", proposal_base)["review_status"] == "CURRENT"
    export_body = {"operation_id": "write-actual-harness", "expected_revision": approved["revision"],
        "proposal_sha256": approved["sha256"], "workspace_ref": scene["workspace"], "directory": "harness-output"}
    exported = evidence.json("POST", proposal_base + "/harness/export", body=export_body)
    assert exported["status"] == "EXPORTED_CONFIRMED_PLAN" and not exported["execution_started"]
    version_folder = scene["actual"] / exported["version_directory"]
    for file in exported["files"]:
        raw = (scene["actual"] / file["path"]).read_bytes()
        assert _sha(raw) == file["sha256"] and len(raw) == file["byte_length"]
    for item in [*harness["prompts"], *harness["context_files"]]:
        assert (version_folder / item["filename"]).read_bytes() == item["content"].encode()
    assert json.loads((version_folder / "manifest.json").read_bytes())["proposal_ref"] == _ref(approved)
    assert late_answer["text"] not in (version_folder / "discovery-context.json").read_text()
    assert all("discovery-context.json" in p["content"] for p in harness["prompts"])
    assert len(list(version_folder.glob("task-*.md"))) == len(proposal["payload"]["candidate"]["nodes"])
    replay = evidence.json("POST", proposal_base + "/harness/export", body=export_body)
    assert replay["replayed"] and all(item["current"] for item in replay["file_checks"])
    assert all((scene["actual"] / name).read_bytes() == raw for name, raw in SOURCE_FILES.items())
    _no_execution(scene, 2)
    # An actual user requirement edit invalidates the dependent plan immediately,
    # while old reports/harness bytes and the accepted history remain immutable.
    latest = planned["session"]
    candidate = deepcopy(latest["payload"]["candidate"])
    candidate["claims"][0]["text"] = "用户改为逐条输出差异，并附人工复核结论。"
    changed = evidence.json("PUT", base, body={"operation_id": "user-changed-requirements",
        "expected_revision": latest["revision"], "candidate": candidate, "change_reason": "用户改变已确认要求。"})
    assert not changed["readiness"]["can_plan"]
    old = evidence.json("GET", proposal_base)
    assert old["review_status"] == "NEEDS_REVIEW" and old["payload"] == approved["payload"]
    evidence.json("POST", proposal_base + "/harness/export", body={**export_body, "operation_id": "reject-old-plan-export"}, status=409)
    evidence.json("POST", base + "/plan", body={**plan_body, "operation_id": "reject-unaccepted-new-requirements",
        "expected_revision": changed["revision"]}, status=409)
    assert evidence.json("POST", proposal_base + "/reports", body=report_body)["replayed"]
    assert evidence.download(report["artifact"], "word-after-requirements-change.docx") == blobs["artifact"]
    assert evidence.download(draft_report["artifact"], "discovery-word-after-requirements-change.docx") == draft_blobs["artifact"]
    (scene["actual"] / "需求 原件.md").write_text("用户从外部编辑了实际需求文件。", encoding="utf-8")
    assert not evidence.json("GET", base)["source_current"]
    assert evidence.json("GET", proposal_base)["review_status"] == "NEEDS_REVIEW"
    assert json.loads((version_folder / "discovery-context.json").read_bytes()) == background
    assert len(scene["wire"].requests) == 2
    _no_execution(scene, 2)


def test_actual_ocr_text_only_annotations_enter_one_original_cli_prompt(original_chain):
    scene = original_chain
    evidence, client, pid, base, view = scene["evidence"], scene["client"], scene["pid"], scene["base"], scene["view"]
    assert isinstance(client.app.state.offline_ocr, OfflineOCR)
    assert evidence.json("GET", "/api/workbench/ocr/capabilities")["available"] is True
    image = Image.new("RGB", (1500, 500), "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(ROOT / "toolchain/fonts/NotoSansCJK-Regular.ttc"), 42, index=2)
    lines = ["需求确认 REQ-001", "离线识别 Offline OCR", "报告版本 Version 3.10"]
    for index, line in enumerate(lines):
        draw.text((60, 45 + index * 140), line, font=font, fill="black")
    filename = "原创屏幕文字样图.png"
    image.save(scene["actual"] / filename)
    (evidence.directory / "selected-originals" / filename).write_bytes((scene["actual"] / filename).read_bytes())
    source = evidence.json("GET", f'/api/projects/{pid}/local-workspaces/{scene["binding"]["id"]}/file',
        params={"path": filename})["source_ref"]
    bitmap = {"kind": "LOCAL_FILE", "file_source_ref": source, "sha256": source["sha256"]}
    observation = evidence.json("POST", f"/api/projects/{pid}/workbench/ocr", body={"bitmap_ref": bitmap})
    recognition = observation["payload"]["recognition"]
    assert recognition["text"].splitlines() == lines
    assert recognition["network_access"] == "NEVER" and recognition["qualification"] == "OBSERVATION_ONLY"
    assert recognition["bitmap"]["sha256"] == _sha((scene["actual"] / filename).read_bytes())
    first = recognition["lines"][0]
    request = feedback_body(view, op="ocr-quotation-attachment", tool="TEXT_SELECTION")
    request["comment"] = "请保留这段观察文字，并说明它尚不是已保存文档或工程事实。"
    quote = first["text"][:4]
    request["selection"] = {"bitmap_ref": bitmap, "ocr_ref": observation["observation_ref"],
        "text_selection": {"text": quote, "quote_sha256": _sha(quote.encode()), "locator": {
            "kind": "OCR_LINE", "observation_ref": observation["observation_ref"],
            "line_id": first["id"], "char_start": 0, "char_end": 4}}}
    feedback = evidence.json("POST", feedback_route(pid), body=request)
    other_request = feedback_body(view, op="second-draft-attachment")
    other_request["comment"] = "同时说明两份需求原件的覆盖边界。"
    other_feedback = evidence.json("POST", feedback_route(pid), body=other_request)
    assert not feedback["native_requested"] and not other_feedback["native_requested"]
    assert evidence.json("GET", f'/api/projects/{pid}/workbench/ocr/{observation["observation_ref"]["id"]}') == observation
    assert not scene["wire"].requests
    _no_execution(scene, 0)
    advance_body = {"operation_id": "send-two-annotations-once", "expected_revision": view["revision"],
        "message": "把这两处批注与这条文字要求一同提交，保留来源和不确定性。",
        "provider_profile_ref": scene["profile"], "workspace_ref": scene["workspace"], "budget": BUDGET,
        "feedback_refs": [feedback["feedback_ref"], other_feedback["feedback_ref"]]}
    updated = evidence.json("POST", base + "/advance", body=advance_body)
    admitted, scope = _native_scope(scene, updated["payload"]["execution"])
    assert "artifact_images" not in admitted and scope["image_bindings"] == []
    assert len(scene["wire"].requests) == 1 and scene["wire"].requests[0]["auth_matches"]
    external_body = scene["wire"].requests[0]["body"]
    assert [tool["function"]["name"] for tool in external_body["tools"]] == ["StructuredOutput"]
    user_parts = [message["content"] for message in external_body["messages"] if message["role"] == "user"]
    for content in user_parts:
        assert isinstance(content, str) or all(part["type"] == "text" for part in content)
    encoded = canonical(user_parts)
    assert "image_url" not in encoded and "data:image" not in encoded
    for text in [*lines, quote, request["comment"], other_request["comment"], advance_body["message"],
                 feedback["feedback_ref"]["id"], other_feedback["feedback_ref"]["id"], observation["observation_ref"]["sha256"]]:
        assert text in encoded
    observations = admitted["source_receipt"]["documents"][0]["local_ocr_observations"]
    assert observations == [observation]
    assert "OCR_OBSERVATION_NOT_VERIFIED_DOCUMENT_OR_PROJECT_FACT" in encoded
    assert evidence.json("POST", base + "/advance", body=advance_body) == updated
    assert len(scene["wire"].requests) == 1
    operation = evidence.json("GET", f'/api/projects/{pid}/discovery/operations/{advance_body["operation_id"]}')
    assert operation["native_execution"]["id"] == updated["payload"]["execution"]["execution_id"]
    assert operation["native_execution"]["session_id"] == updated["payload"]["execution"]["session_id"]
    assert operation["events"]
    # Existing observations are readable historical receipts; changed bitmap
    # bytes cannot be re-admitted to another model request as that old source.
    Image.new("RGB", (1500, 500), "black").save(scene["actual"] / filename)
    assert evidence.json("GET", f'/api/projects/{pid}/workbench/ocr/{observation["observation_ref"]["id"]}') == observation
    evidence.json("POST", f"/api/projects/{pid}/workbench/ocr", body={"bitmap_ref": bitmap}, status=409)
    assert not evidence.json("GET", base)["source_current"]
    evidence.json("POST", base + "/advance", body={**advance_body, "operation_id": "reject-drifted-ocr-source",
        "expected_revision": updated["revision"]}, status=409)
    assert len(scene["wire"].requests) == 1
    _no_execution(scene, 1)
