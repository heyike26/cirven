"""Developer entry only; this never supplies EEAS runtime credentials."""
import os
from pathlib import Path
import subprocess
import sys

root = Path(__file__).resolve().parents[1]
env = os.environ.copy()
env["PATH"] = os.pathsep.join([str(root / ".node-tools/node_modules/.bin"),
    str(root / ".tools/node_modules/.bin"), env.get("PATH", "")])
binary = root / ".tools/node_modules/.bin/claude"
if not binary.is_file():
    raise SystemExit("Install the pinned developer toolchain from toolchain/ first")
raise SystemExit(subprocess.call([str(binary), *sys.argv[1:]], env=env))
