"""Install only frozen wheels in a fresh Linux venv and run the actual OCR adapter."""
from __future__ import annotations

import argparse
from email.parser import BytesParser
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import venv
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CORE_NAMES = {"pydantic", "pydantic-core", "typing-extensions", "annotated-types", "typing-inspection"}


def sha(raw): return hashlib.sha256(raw).hexdigest()


def execute(argv, evidence, name, env):
    value = subprocess.run(argv, env=env, cwd=ROOT, capture_output=True, text=True, check=False, timeout=180)
    (evidence / (name + "-stdout.txt")).write_text(value.stdout)
    (evidence / (name + "-stderr.txt")).write_text(value.stderr)
    receipt = {"argv": argv, "exit_code": value.returncode}
    (evidence / (name + "-command.json")).write_text(json.dumps(receipt, indent=2) + "\n")
    if value.returncode:
        raise RuntimeError(name + " original exit " + str(value.returncode) + "; evidence " + str(evidence))
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--core-wheels", type=Path, required=True)
    parser.add_argument("--evidence", type=Path)
    args = parser.parse_args()
    evidence = args.evidence or Path(os.environ["EEAS_EVIDENCE_DIR"]) / "fresh-offline-ocr"
    evidence = evidence.resolve()
    evidence.mkdir(parents=True, exist_ok=False)
    core = args.core_wheels.resolve()
    manifest_path = core.parent / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    pins, source = [], []
    for path in sorted(core.glob("*.whl")):
        with zipfile.ZipFile(path) as archive:
            metadata = BytesParser().parsebytes(archive.read(next(n for n in archive.namelist() if n.endswith(".dist-info/METADATA"))))
        name = metadata["Name"].lower().replace("_", "-")
        if name not in CORE_NAMES: continue
        hashed = sha(path.read_bytes())
        expected = manifest["files_sha256"].get("wheels/" + path.name, manifest["files_sha256"].get(path.name))
        if expected != hashed: raise ValueError("Original core wheel SHA changed: " + path.name)
        pins.append(name + "==" + metadata["Version"] + " --hash=sha256:" + hashed)
        source.append({"path": str(path), "name": name, "version": metadata["Version"], "sha256": hashed})
    if len(source) != len(CORE_NAMES): raise ValueError("The exact five product core wheels are required")
    lock = evidence / "requirements-adapter.lock"
    lock.write_text((ROOT / "toolchain/ocr/requirements-linux310.lock").read_text() + "\n".join(pins) + "\n")
    worker = evidence / "probe.py"
    worker.write_text('''import sys,json,socket,base64
from pathlib import Path
from io import BytesIO
from PIL import Image,ImageDraw,ImageFont
root=Path(sys.argv[1]);sys.path.insert(0,str(root))
from packages.adapters.offline_ocr import OfflineOCR
from packages.domain.local_workspaces import WorkspaceInput,LocalWorkspaces,digest
from packages.domain.tasks import TaskService
from packages.domain.document_import import image_metadata
from rapidocr.utils.download_file import DownloadFile
def forbidden(*args,**kwargs):raise RuntimeError("OFFLINE_NETWORK_OR_DOWNLOAD_ATTEMPT")
socket.create_connection=forbidden;socket.getaddrinfo=forbidden;socket.socket.connect=forbidden;socket.socket.connect_ex=forbidden;DownloadFile.run=forbidden
data=Path(sys.argv[2]);actual=data/"selected-folder";actual.mkdir(parents=True)
image=Image.new("RGB",(1500,500),"white");draw=ImageDraw.Draw(image)
font=ImageFont.truetype(str(root/"toolchain/fonts/NotoSansCJK-Regular.ttc"),42,index=2)
texts=["需求确认 REQ-001","离线识别 Offline OCR","报告版本 Version 3.10"]
for n,t in enumerate(texts):draw.text((45,45+n*130),t,font=font,fill="#16253b")
image.save(actual/"original.png")
store=TaskService(data/"controller/eeas.sqlite3",root);pid=store.create_project("fresh offline OCR","")["id"]
ws=LocalWorkspaces(store,data/"controller");binding=ws.bind(pid,WorkspaceInput(idempotency_key="selected",root=str(actual)))
ref=ws.read(pid,binding["id"],"original.png")["source_ref"];bitmap_ref={"kind":"LOCAL_FILE","file_source_ref":ref,"sha256":ref["sha256"]}
def reader(pid,ref):
 source=ref["file_source_ref"];v=ws.read(pid,source["workspace_id"],source["path"],source["revision"])
 if not v["current"] or v["source_ref"]!=source:raise RuntimeError("SOURCE_DRIFT")
 raw=base64.b64decode(v["content"],validate=True)
 return {"bytes":raw,**image_metadata(raw),"source_ref":source,"observation_authority":"LOCAL_FILE_BYTES_NOT_VERIFIED_SEMANTIC_FACTS"}
result=OfflineOCR(root/"toolchain/ocr/MODELS.json",reader).recognize(pid,bitmap_ref)
if result["text"].splitlines()!=texts or result["qualification"]!="OBSERVATION_ONLY":raise RuntimeError("Actual OCR result mismatch")
import importlib.metadata as m
print(json.dumps({"status":"PASS","python":sys.version,"packages":{d.metadata["Name"]:d.version for d in m.distributions()},"observation":result,"network_or_download_calls":0,"qualification":"FRESH_LINUX_VENV_ACTUAL_ADAPTER_AND_ROOT_LOCAL_FILE_AUTHORITY_NOT_NATIVE_WINDOWS_OR_SELF_CONTAINED_DESKTOP"},ensure_ascii=False))
''', encoding="utf-8")
    clean = {k: v for k, v in os.environ.items() if k not in {"PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"}}
    clean.update(PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1", PYTHONNOUSERSITE="1")
    try:
        with tempfile.TemporaryDirectory(prefix="eeas-fresh-ocr-") as tmp:
            target = Path(tmp)
            # The bundled Linux interpreter locates libpython relative to its
            # executable; a normal isolated-site venv links that base interpreter.
            # This probe qualifies dependency isolation, not a portable runtime.
            venv.create(target / "venv", with_pip=True, symlinks=True)
            python = target / "venv/bin/python"
            commands = [execute([str(python), "-m", "pip", "install", "--no-index", "--require-hashes", "--find-links", str(core),
                "--find-links", str(ROOT / "toolchain/ocr/wheels/linux310"), "--find-links", str(ROOT / "toolchain/ocr/wheels/common"), "-r", str(lock)], evidence, "install", clean)]
            commands.append(execute([str(python), "-m", "pip", "check"], evidence, "pip-check", clean))
            commands.append(execute([str(python), str(worker), str(ROOT), str(target / "data")], evidence, "actual-ocr", clean))
            result = json.loads((evidence / "actual-ocr-stdout.txt").read_text().splitlines()[-1])
            shutil_source = target / "data/selected-folder/original.png"
            (evidence / "original-screen.png").write_bytes(shutil_source.read_bytes())
        receipt = {"status": "PASS", "core_manifest_sha256": sha(manifest_path.read_bytes()), "core_wheels": source,
                   "ocr_source_manifest_sha256": sha((ROOT / "toolchain/ocr/SOURCE.json").read_bytes()),
                   "model_manifest_sha256": sha((ROOT / "toolchain/ocr/MODELS.json").read_bytes()), "commands": commands,
                   "result": result, "native_windows": "NOT_RUN", "self_contained_desktop": "NOT_QUALIFIED"}
        (evidence / "receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        print(evidence / "receipt.json")
        return 0
    except Exception as error:
        output = getattr(error, "output", None)
        if output:
            (evidence / "bootstrap-original-output.txt").write_bytes(output if isinstance(output, bytes) else output.encode())
        (evidence / "failure.json").write_text(json.dumps({"status": "FAIL", "type": type(error).__name__, "error": str(error)}, indent=2) + "\n")
        raise


if __name__ == "__main__": raise SystemExit(main())
