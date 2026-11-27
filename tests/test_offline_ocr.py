"""Real offline CPU OCR and local-file authority; native Windows capture is separate."""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from io import BytesIO
import base64
import json
import os
from pathlib import Path
import shutil
import socket
from types import SimpleNamespace

from PIL import Image, ImageDraw, ImageFont
import pytest

from packages.adapters.offline_ocr import OfflineOCR, OCRUnavailable
from packages.domain.document_import import image_metadata
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput, canonical, digest, sha
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import TaskService

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "toolchain/ocr/MODELS.json"
TEXTS = ["需求确认 REQ-001", "离线识别 Offline OCR", "报告版本 Version 3.10"]


def bitmap(mode="RGB", blank=False):
    image = Image.new(mode, (1500, 500), (255, 255, 255, 255) if mode == "RGBA" else "white")
    draw = ImageDraw.Draw(image)
    font = ImageFont.truetype(str(ROOT / "toolchain/fonts/NotoSansCJK-Regular.ttc"), 42, index=2)
    if not blank:
        for index, text in enumerate(TEXTS):
            draw.text((45, 45 + index * 130), text, font=font, fill="#16253b")
    out = BytesIO()
    image.save(out, format="PNG")
    return out.getvalue()


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    # These guards reject attempted networking/downloads, rather than returning
    # fake OCR. Every positive recognition below executes the real ONNX models.
    from rapidocr.utils.download_file import DownloadFile
    def forbidden(*args, **kwargs):
        raise AssertionError("OFFLINE_OCR_NETWORK_OR_DOWNLOAD_ATTEMPT")
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(DownloadFile, "run", forbidden)
    store = TaskService(tmp_path / "controller/eeas.sqlite3", ROOT)
    pid = store.create_project("真实离线文字识别", "")['id']
    actual = tmp_path / "用户选择的 原创屏幕样图"
    actual.mkdir()
    workspace = LocalWorkspaces(store, tmp_path / "controller")
    binding = workspace.bind(pid, WorkspaceInput(idempotency_key="select", root=str(actual)))
    raw = bitmap()
    name = "原创建图_中文 English.png"
    (actual / name).write_bytes(raw)
    ref = workspace.read(pid, binding["id"], name)["source_ref"]
    calls = []
    def reader(project, value):
        calls.append((project, deepcopy(value)))
        source = value["file_source_ref"]
        observed = workspace.read(project, source["workspace_id"], source["path"], source["revision"])
        if not observed["current"] or observed["source_ref"] != source:
            raise Conflict("LOCAL_BITMAP_SOURCE_DRIFT")
        content = base64.b64decode(observed["content"], validate=True) if observed["encoding"] == "base64" else observed["content"].encode()
        return {"bytes": content, **image_metadata(content), "source_ref": observed["source_ref"],
                "observation_authority": "LOCAL_FILE_BYTES_NOT_VERIFIED_SEMANTIC_FACTS"}
    value = {"store": store, "pid": pid, "workspace": workspace, "binding": binding, "actual": actual,
             "raw": raw, "reader": reader, "calls": calls,
             "ref": {"kind": "LOCAL_FILE", "file_source_ref": ref, "sha256": ref["sha256"]}, "results": []}
    value["ocr"] = OfflineOCR(MODEL, reader)
    yield value
    target = os.environ.get("EEAS_EVIDENCE_DIR")
    if target:
        evidence = Path(target) / "offline-ocr" / request.node.name
        evidence.mkdir(parents=True)
        (evidence / "original-screen.png").write_bytes(raw)
        inputs = [{"path": "original-screen.png", "sha256": sha(raw), "ground_truth": TEXTS}]
        for result in value["results"]:
            name = result["bitmap"]["source_ref"]["path"]
            content = (actual / name).read_bytes()
            if sha(content) != result["bitmap"]["sha256"]:
                raise AssertionError("Retained OCR evidence must match its exact recognized bitmap")
            destination = evidence / "recognized-inputs" / name
            destination.parent.mkdir(exist_ok=True)
            destination.write_bytes(content)
            inputs.append({"path": str(destination.relative_to(evidence)), "sha256": sha(content),
                           "ground_truth": [] if result["status"] == "NO_TEXT" else TEXTS})
        (evidence / "receipt.json").write_text(json.dumps({"fixture": "ORIGINAL_GENERATED_SCREEN_NOT_NATIVE_WINDOWS_CAPTURE",
            "image_sha256": sha(raw), "inputs": inputs, "ground_truth": TEXTS, "input_ref": value["ref"], "results": value["results"],
            "network_or_download_calls": 0, "native_windows_capture": "NOT_RUN"}, ensure_ascii=False, indent=2) + "\n")


def component(tmp_path, *, mutable_cls=False):
    """Hard-link immutable models; only the small cls file may need a test copy."""
    root = tmp_path / "component"
    (root / "models").mkdir(parents=True)
    wrapper = json.loads(MODEL.read_bytes())
    for role, value in wrapper["payload"]["models"].items():
        source = MODEL.parent / value["path"]
        target = root / value["path"]
        if mutable_cls and role == "cls":
            shutil.copyfile(source, target)
        else:
            os.link(source, target)
    path = root / "MODELS.json"
    path.write_text(canonical(wrapper))
    return path, wrapper["payload"]


def save_config(path, payload):
    path.write_text(canonical({"payload": payload, "sha256": digest(payload)}))


def test_real_selected_unicode_folder_png_character_boxes_and_restart(env):
    e = env
    result = e["ocr"].recognize(e["pid"], e["ref"])
    assert result["text"].splitlines() == TEXTS
    assert result["status"] == "RECOGNIZED" and result["qualification"] == "OBSERVATION_ONLY"
    assert result["bitmap"]["source_ref"] == e["ref"]["file_source_ref"] and result["network_access"] == "NEVER"
    assert digest({k: v for k, v in result.items() if k != "sha256"}) == result["sha256"]
    for line in result["lines"]:
        assert line["confidence"] > .9 and line["characters"]
        assert all(0 <= x <= 1500 and 0 <= y <= 500 for x, y in line["polygon"])
    restarted = OfflineOCR(MODEL, e["reader"]).recognize(e["pid"], e["ref"])
    assert restarted == result
    e["results"].extend([result, restarted])


def test_real_concurrent_requests_same_exact_image_have_same_observation(env):
    e = env
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: e["ocr"].recognize(e["pid"], e["ref"]), range(2)))
    assert results[0] == results[1] and results[0]["text"].splitlines() == TEXTS
    e["results"].extend(results)


@pytest.mark.parametrize("kind", ["blank", "rgba", "jpeg"])
def test_real_other_registered_bitmaps_and_empty_result(env, kind):
    e = env
    raw = bitmap("RGBA" if kind == "rgba" else "RGB", blank=kind == "blank")
    name = "其他.png"
    if kind == "jpeg":
        with Image.open(BytesIO(raw)) as image:
            out = BytesIO()
            image.save(out, format="JPEG", quality=95)
            raw, name = out.getvalue(), "其他.jpg"
    (e["actual"] / name).write_bytes(raw)
    source = e["workspace"].read(e["pid"], e["binding"]["id"], name)["source_ref"]
    ref = {"kind": "LOCAL_FILE", "file_source_ref": source, "sha256": source["sha256"]}
    result = e["ocr"].recognize(e["pid"], ref)
    assert result["status"] == ("NO_TEXT" if kind == "blank" else "RECOGNIZED")
    assert result["lines"] == [] if kind == "blank" else result["text"].splitlines() == TEXTS
    e["results"].append(result)


@pytest.mark.parametrize("fault", ["url", "path", "bytes", "coerced-revision", "extra-hwnd", "wrong-hash"])
def test_arbitrary_input_and_coerced_native_refs_rejected_without_read_or_inference(env, fault):
    e = env
    ref = {"kind": "NATIVE_CAPTURE", "view_ref": "a" * 32, "frame_revision": 1,
           "sha256": sha(e["raw"]), "binding_sha256": "b" * 64}
    if fault in {"url", "path", "bytes"}:
        ref[fault] = "https://example.com/image.png" if fault == "url" else "/outside.png"
    if fault == "coerced-revision": ref["frame_revision"] = "1"
    if fault == "extra-hwnd": ref["hwnd"] = 123
    if fault == "wrong-hash": ref["sha256"] = "not-a-hash"
    with pytest.raises(InvalidInput): e["ocr"].recognize(e["pid"], ref)
    assert e["calls"] == []


def test_cross_project_exact_file_ref_and_escape_rejected_by_real_authority(env):
    e = env
    pid = e["store"].create_project("其他项目", "")["id"]
    with pytest.raises((Conflict, NotFound)): e["ocr"].recognize(pid, e["ref"])
    ref = deepcopy(e["ref"])
    ref["file_source_ref"]["path"] = "../outside.png"
    with pytest.raises(InvalidInput): e["ocr"].recognize(e["pid"], ref)


def test_actual_local_source_drift_before_and_during_inference_has_no_success(env, monkeypatch):
    e = env
    name = e["ref"]["file_source_ref"]["path"]
    (e["actual"] / name).write_bytes(bitmap(blank=True))
    with pytest.raises(Conflict, match="SOURCE_DRIFT"): e["ocr"].recognize(e["pid"], e["ref"])
    (e["actual"] / name).write_bytes(e["raw"])
    def change(*args):
        (e["actual"] / name).write_bytes(bitmap(blank=True))
        return SimpleNamespace(txts=None, boxes=None, scores=None)
    monkeypatch.setattr(e["ocr"], "_inference", change)  # external inference failure boundary only
    with pytest.raises(Conflict, match="SOURCE_DRIFT"): e["ocr"].recognize(e["pid"], e["ref"])


@pytest.mark.parametrize("fault", ["sha", "dimensions", "media-type", "corrupt-image", "source-sha"])
def test_reader_receipt_cannot_substitute_image_bytes_or_metadata(env, fault):
    e = env
    value = e["reader"](e["pid"], e["ref"])
    if fault == "sha": value["sha256"] = "0" * 64
    if fault == "dimensions": value["width"] = 100
    if fault == "media-type": value["media_type"] = "image/jpeg"
    if fault == "corrupt-image": value["bytes"] = b"PNG not an actual image"
    if fault == "source-sha": value["source_ref"]["sha256"] = "0" * 64
    reader = lambda *args: value  # hostile authority-response fixture, not a live scope qualification
    with pytest.raises((InvalidInput, Conflict)): OfflineOCR(MODEL, reader).recognize(e["pid"], e["ref"])


@pytest.mark.parametrize("fault", ["unconfigured", "missing-model", "model-drift", "engine-drift", "manifest-digest"])
def test_missing_or_drifted_model_and_engine_never_download_or_fake_success(env, tmp_path, fault):
    e = env
    path, payload = component(tmp_path)
    if fault == "missing-model": payload["models"]["cls"]["path"] = "models/missing.onnx"
    if fault == "model-drift": payload["models"]["cls"]["sha256"] = "0" * 64
    if fault == "engine-drift": payload["engine_files"]["linux"]["rapidocr"]["rapidocr/main.py"] = "0" * 64
    save_config(path, payload)
    if fault == "manifest-digest": path.write_text(path.read_text().replace('"sha256":', '"wrong_digest":', 1))
    ocr = OfflineOCR(None if fault == "unconfigured" else path, e["reader"])
    assert not ocr.capabilities()["available"]
    with pytest.raises((OCRUnavailable, Conflict)): ocr.recognize(e["pid"], e["ref"])


def test_actual_invalid_onnx_original_error_retained_then_valid_retry(env, tmp_path):
    e = env
    path, payload = component(tmp_path, mutable_cls=True)
    original = (path.parent / payload["models"]["cls"]["path"]).read_bytes()
    target = path.parent / payload["models"]["cls"]["path"]
    target.write_bytes(b"invalid ONNX bytes")
    payload["models"]["cls"].update(sha256=sha(target.read_bytes()), byte_length=target.stat().st_size)
    save_config(path, payload)
    ocr = OfflineOCR(path, e["reader"])
    with pytest.raises(Exception, match="Protobuf|protobuf|ONNX|Invalid"):
        ocr.recognize(e["pid"], e["ref"])
    assert ocr._engine is None
    target.write_bytes(original)
    payload["models"]["cls"].update(sha256=sha(original), byte_length=len(original))
    save_config(path, payload)
    result = ocr.recognize(e["pid"], e["ref"])
    assert result["text"].splitlines() == TEXTS
    e["results"].append(result)


@pytest.mark.parametrize("fault", ["missing", "version"])
def test_actual_dependency_presence_gate_fails_closed(env, monkeypatch, fault):
    import importlib.metadata
    actual = importlib.metadata.distribution
    def lookup(name):
        if name == "rapidocr":
            if fault == "missing": raise importlib.metadata.PackageNotFoundError(name)
            return SimpleNamespace(version="0.0.0")
        return actual(name)
    monkeypatch.setattr(importlib.metadata, "distribution", lookup)
    assert not env["ocr"].capabilities()["available"]
    with pytest.raises(OCRUnavailable, match="DEPENDENCY"):
        env["ocr"].recognize(env["pid"], env["ref"])


@pytest.mark.parametrize("fault", ["escape", "drive", "symlink"])
def test_models_cannot_escape_the_component_or_follow_a_link(env, tmp_path, fault):
    path, payload = component(tmp_path)
    if fault == "escape": payload["models"]["cls"]["path"] = "../outside.onnx"
    if fault == "drive": payload["models"]["cls"]["path"] = "C:\\outside.onnx"
    if fault == "symlink":
        link = path.parent / "models/link.onnx"
        link.symlink_to(MODEL.parent / payload["models"]["cls"]["path"])
        payload["models"]["cls"]["path"] = "models/link.onnx"
    save_config(path, payload)
    with pytest.raises(Conflict): OfflineOCR(path, env["reader"]).recognize(env["pid"], env["ref"])


def test_actual_model_without_character_map_rejected_before_upstream_dictionary_download(env, tmp_path):
    path, payload = component(tmp_path)
    payload["models"]["rec"] = deepcopy(payload["models"]["cls"])
    save_config(path, payload)
    with pytest.raises(Conflict, match="CHARACTER_MAP_MISSING"):
        OfflineOCR(path, env["reader"]).recognize(env["pid"], env["ref"])


def test_actual_config_drift_during_real_inference_is_not_returned_success(env, tmp_path, monkeypatch):
    e = env
    path, payload = component(tmp_path)
    ocr = OfflineOCR(path, e["reader"])
    original = ocr._inference
    def changed(*args):
        value = original(*args)
        payload["models"]["cls"]["sha256"] = "0" * 64
        save_config(path, payload)
        return value
    monkeypatch.setattr(ocr, "_inference", changed)
    with pytest.raises(Conflict, match="MODEL_DRIFT"): ocr.recognize(e["pid"], e["ref"])


@pytest.mark.parametrize("fault", ["nan-confidence", "box-outside", "count", "empty-with-boxes"])
def test_hostile_inference_output_cannot_be_reported_success(env, monkeypatch, fault):
    e = env
    result = SimpleNamespace(txts=("text",), boxes=[[[1, 1], [100, 1], [100, 80], [1, 80]]], scores=(.9,), word_results=[[]])
    if fault == "nan-confidence": result.scores = (float("nan"),)
    if fault == "box-outside": result.boxes[0][1][0] = 2000
    if fault == "count": result.scores = ()
    if fault == "empty-with-boxes": result.txts = None
    monkeypatch.setattr(e["ocr"], "_inference", lambda *args: result)  # hostile external-model output only
    with pytest.raises(Conflict): e["ocr"].recognize(e["pid"], e["ref"])
