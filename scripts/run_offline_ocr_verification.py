"""Run the OCR acceptance cases and actual fresh no-index dependency probe."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def main():
    evidence = Path(os.environ["EEAS_EVIDENCE_DIR"]).resolve()
    with tempfile.TemporaryDirectory(prefix="eeas-ocr-normal-tests-") as tmp:
        value = subprocess.run([sys.executable, "-m", "pytest", "-q", "tests/test_offline_ocr.py", "--basetemp", tmp], cwd=ROOT, check=False)
        if value.returncode:
            return value.returncode
    value = subprocess.run([sys.executable, "scripts/verify_offline_ocr_closure.py", "--core-wheels",
                            "vendor/linux-py310/20261012T152738898709Z/wheels", "--evidence", str(evidence / "fresh-offline-ocr")],
                           cwd=ROOT, check=False)
    return value.returncode


if __name__ == "__main__":
    raise SystemExit(main())
