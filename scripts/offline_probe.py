"""Fresh-venv installation and real API/SQLite smoke test with public DNS denied."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
selection = json.loads((ROOT / "profiles/offline-dependencies.json").read_text())
stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
destination = ROOT / ".offline-probe" / stamp
target = "windows-py310" if os.name == "nt" else "linux-py310"
env = {**os.environ, "PYTHONPATH": os.pathsep.join([str(ROOT / "tests/fixtures/offline_guard"), str(ROOT)]),
       "PIP_NO_INDEX": "1", "PIP_INDEX_URL": "http://127.0.0.1:1/unavailable",
       "PIP_EXTRA_INDEX_URL": "", "PIP_DISABLE_PIP_VERSION_CHECK": "1"}
subprocess.run([sys.executable, str(ROOT / "scripts/install_offline.py"), "--destination", str(destination),
                "--wheelhouse", str(ROOT / selection["targets"][target])], env=env, check=True)
executable = destination / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
subprocess.run([str(executable), str(ROOT / "tests/fixtures/offline_smoke.py")], cwd=ROOT, env=env, check=True)
