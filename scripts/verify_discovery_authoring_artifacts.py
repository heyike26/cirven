"""Qualify real frozen authoring artifacts; never overwrite failed evidence."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from xml.sax.saxutils import escape

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.domain.local_workspaces import digest, sha
from packages.domain.planning_docx import bounded_docx

CASES = {
    "dotx-structured": "test_dotx_normalization_preserves_original_all_parts_and_structured_report_tables",
    "reviewed-chapters": "test_reviewed_real_chapter_mapping_fills_existing_template_without_append_or_slots",
    "reviewed-experience-diagram": "test_report_freezes_reviewed_style_real_graph_pending_and_late_answer",
}


def write(path: Path, value: object) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def command(argv: list[str], out: Path, label: str, env: dict[str, str], timeout: int = 90) -> dict:
    result = subprocess.run(argv, env=env, capture_output=True, text=True, timeout=timeout, check=False)
    (out / (label + "-stdout.txt")).write_text(result.stdout, encoding="utf-8")
    (out / (label + "-stderr.txt")).write_text(result.stderr, encoding="utf-8")
    receipt = {"command": argv, "exit_code": result.returncode,
               "stdout": label + "-stdout.txt", "stderr": label + "-stderr.txt"}
    write(out / (label + "-command.json"), receipt)
    if result.returncode:
        raise RuntimeError(f"{label} failed with original exit {result.returncode}; see {out}")
    return receipt


def verify_case(label: str, case: str, evidence: Path, office: Path, validator: Path) -> dict:
    out = evidence / "rendered-authoring" / label
    out.mkdir(parents=True, exist_ok=False)
    try:
        candidates = []
        for path in (evidence / "authoring-artifacts" / case / "planning-reports").glob("*/*/artifacts/*/manifest.json"):
            wrapper = json.loads(path.read_bytes())
            manifest = wrapper["payload"]
            if digest(manifest) != wrapper["sha256"]:
                raise ValueError("Saved report manifest digest changed")
            if label != "reviewed-experience-diagram" or manifest.get("diagram_refs"):
                candidates.append((path, manifest))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one original {label} report, found {len(candidates)}")
        path, manifest = candidates[0]
        for item in manifest["files"].values():
            raw = (path.parent / item["stored_name"]).read_bytes()
            if len(raw) != item["byte_length"] or sha(raw) != item["sha256"]:
                raise ValueError("Frozen output failed its original byte receipt")
        source = json.loads((path.parent / manifest["files"]["source_snapshot"]["stored_name"]).read_bytes())
        if (digest(source) != manifest["id"] or source["source_kind"] != "DISCOVERY" or
                digest(source["discovery"]["payload"]) != manifest["session_ref"]["sha256"]):
            raise ValueError("Saved source/ref binding failed")
        raw = (path.parent / manifest["files"]["docx"]["stored_name"]).read_bytes()
        bounded_docx(raw)
        docx = out / (label + ".docx")
        docx.write_bytes(raw)
        wheel = ROOT / "toolchain/word/defusedxml-0.7.1-py2.py3-none-any.whl"
        env = {**os.environ, "PYTHONPATH": str(wheel) + os.pathsep + os.environ.get("PYTHONPATH", "")}
        validation = command([sys.executable, str(validator), str(docx)], out, "ooxml-xsd", env)
        if sha(docx.read_bytes()) != sha(raw):
            raise ValueError("Validator changed the original report")
        profile = out / "office-profile"
        profile.mkdir()
        cache = out / "font-cache"
        cache.mkdir()
        config = out / "fontconfig.xml"
        config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
            '<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>' +
            escape(str(ROOT / "toolchain/fonts")) + '</dir><cachedir>' + escape(str(cache)) + '</cachedir></fontconfig>',
            encoding="utf-8")
        program = office / "usr/lib/libreoffice/program"
        env.update(LD_LIBRARY_PATH=str(program) + ":" + str(office / "usr/lib/x86_64-linux-gnu"),
                   SAL_USE_VCLPLUGIN="svp", FONTCONFIG_FILE=str(config), XDG_CACHE_HOME=str(out / "cache"))
        render = command([str(program / "soffice"), "-env:UserInstallation=" + profile.as_uri(),
                          "--headless", "--convert-to", "pdf", "--outdir", str(out), str(docx)],
                         out, "libreoffice", env)
        pdf = out / (label + ".pdf")
        if not pdf.is_file() or not pdf.read_bytes().startswith(b"%PDF-"):
            raise ValueError("Office returned without actual PDF bytes")
        raster = command([str(office / "usr/bin/pdftoppm"), "-png", "-r", "100", str(pdf), str(out / "page")],
                         out, "pdf-raster", env)
        from pypdf import PdfReader
        pages = PdfReader(str(pdf)).pages
        pngs = sorted(out.glob("page-*.png"))
        if len(pngs) != len(pages) or not pages:
            raise ValueError("Each actual PDF page needs an image")
        texts = [page.extract_text() for page in pages]
        (out / "pdf-extracted-text.txt").write_text("\n\n".join(texts), encoding="utf-8")
        receipt = {"status": "PASS", "source_manifest": str(path.relative_to(evidence)),
                   "source_kind": manifest["source_kind"], "session_ref": manifest["session_ref"],
                   "template_mode": manifest["render_receipt"]["template_mode"],
                   "diagram_refs": manifest["diagram_refs"], "artifact_id": manifest["id"],
                   "docx_sha256": sha(raw), "pdf_sha256": sha(pdf.read_bytes()), "page_count": len(pages),
                   "pages": [{"path": str(p.relative_to(evidence)), "sha256": sha(p.read_bytes())} for p in pngs],
                   "commands": [validation, render, raster], "validator_sha256": sha(validator.read_bytes()),
                   "validator_dependency_sha256": sha(wheel.read_bytes()),
                   "render_executable_sha256": sha((program / "soffice.bin").read_bytes()),
                   "raster_executable_sha256": sha((office / "usr/bin/pdftoppm").read_bytes()),
                   "font_sha256": sha((ROOT / "toolchain/fonts/NotoSansCJK-Regular.ttc").read_bytes()),
                   "visual_inspection": "NOT_RUN_ROOT_ONLY", "native_word": "NOT_RUN", "native_windows": "NOT_RUN",
                   "qualification": "Actual immutable DOCX OOXML/XSD and isolated Linux LibreOffice PDF/PNG rendering"}
        write(out / "render-receipt.json", receipt)
        return receipt
    except Exception as error:
        write(out / "failure.json", {"status": "FAIL", "error_type": type(error).__name__, "error": str(error)})
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--office-root", type=Path, required=True)
    parser.add_argument("--validator", type=Path, required=True)
    parser.add_argument("--run-tests", action="store_true")
    args = parser.parse_args()
    evidence = Path(os.environ["EEAS_EVIDENCE_DIR"]).resolve()
    if args.run_tests:
        with tempfile.TemporaryDirectory(prefix="eeas-authoring-normal-") as tmp:
            result = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_discovery_authoring.py",
                                     "tests/test_planning_reports.py", "--basetemp", tmp], cwd=ROOT, check=False)
            if result.returncode:
                return result.returncode
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(verify_case, label, case, evidence, args.office_root.resolve(), args.validator.resolve())
                   for label, case in CASES.items()]
        results, failures = [], []
        for future in futures:
            try:
                results.append(future.result())
            except Exception as error:
                failures.append({"type": type(error).__name__, "error": str(error)})
    write(evidence / "authoring-render-summary.json", {"status": "FAIL" if failures else "PASS", "renders": results,
                                                     "failures": failures, "native_windows": "NOT_RUN"})
    print(json.dumps({"rendered": len(results), "failures": failures}, ensure_ascii=False), flush=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
