"""Execute original Python fixtures on the current OS; never label WSL as Windows."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from packages.runner.supervisor import atomic_json, matching_process


def require(condition, detail):
    if not condition:
        raise RuntimeError('Native qualification failed: ' + str(detail))


def probe(output):
    output.mkdir(parents=True, exist_ok=False)
    cases = []
    for mode in ("normal", "nonzero", "timeout", "cancel-tree"):
        directory = output / ("中文 空格 " + mode)
        workspace, journal = directory / "工作 目录", directory / "journal"
        workspace.mkdir(parents=True)
        journal.mkdir()
        if mode == "normal":
            script = "import pathlib,sys; pathlib.Path('中文 输出.txt').write_text(sys.argv[1],encoding='utf-8'); print(sys.argv[1])"
        elif mode == "nonzero":
            script = "import sys; print('原创 中文故障',file=sys.stderr); raise SystemExit(7)"
        elif mode == "timeout":
            script = "import time; time.sleep(60)"
        else:
            script = """import json,pathlib,subprocess,sys,time
import psutil
child=psutil.Popen([sys.executable,'-I','-c','import time; time.sleep(60)'])
pathlib.Path('child.json').write_text(json.dumps({'pid':child.pid,'create_time':child.create_time()}),encoding='utf-8')
print('原创 中文进程树',flush=True)
time.sleep(60)
"""
        request = {"job_id": mode, "run_id": "original-native-probe", "argv": [sys.executable, "-I", "-c", script, "中文 参数 with spaces"],
                   "workspace": str(workspace), "stdout_path": str(workspace / "stdout.txt"),
                   "stderr_path": str(workspace / "stderr.txt"), "timeout_seconds": 1 if mode == "timeout" else 10,
                   "output_limit_bytes": 65536}
        atomic_json(journal / "request.json", request)
        child_identity = None
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, "-I", str(ROOT / "packages/runner/supervisor.py"), str(journal / "request.json")])
        try:
            if mode == "cancel-tree":
                while not (workspace / "child.json").exists() and process.poll() is None and time.monotonic() - started < 6:
                    time.sleep(0.02)
                child_identity = json.loads((workspace / "child.json").read_text(encoding="utf-8"))
                require(matching_process(child_identity), 'Original child did not start')
                atomic_json(journal / "cancel.json", {"job_id": mode,
                    "request_hash": hashlib.sha256((journal / "request.json").read_bytes()).hexdigest(), "reason": "CANCELLED"})
            process.wait(timeout=14)
            receipt = json.loads((journal / "receipt.json").read_bytes())
            require(receipt['phase'] == 'FINISHED', receipt)
            require(process.returncode == 0, 'Supervisor did not return exit zero')
            if mode == "normal":
                require(receipt['exit_code'] == 0 and receipt['error_type'] is None, receipt)
                require((workspace / '中文 输出.txt').read_text(encoding='utf-8') == '中文 参数 with spaces', 'Original output bytes differ')
                require('中文 参数 with spaces' in (workspace / 'stdout.txt').read_text(encoding='utf-8'), 'Original UTF8 stdout differs')
            elif mode == "nonzero":
                require(receipt['exit_code'] == 7 and '中文故障' in (workspace / 'stderr.txt').read_text(encoding='utf-8'), 'Actual nonzero exit/stderr differs')
            else:
                require(receipt['error_type'] == ('TIME_LIMIT' if mode == 'timeout' else 'CANCELLED'), receipt)
                require(not matching_process(receipt['child']), 'Original process is still alive')
                require(not matching_process(child_identity), 'Original descendant is still alive')
            cases.append({"test_id": mode, "result": "PASS", "receipt": str((journal / "receipt.json").relative_to(output)),
                          "argv": request["argv"], "exit_code": receipt["exit_code"], "error_type": receipt["error_type"]})
        finally:
            if process.poll() is None:
                atomic_json(journal / "cancel.json", {"job_id": mode,
                    "request_hash": hashlib.sha256((journal / "request.json").read_bytes()).hexdigest(), "reason": "INTERRUPTED"})
                try:
                    process.wait(timeout=4)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=4)
            target = matching_process(child_identity)
            if target:
                target.kill()
    result = {"schema_version": "eeas.native-runner-probe.v1", "result": "PASS", "cases": cases,
              "environment": platform.system(), "wsl": "microsoft" in platform.release().lower(),
              "python": platform.python_version(), "encoding": "utf-8", "source_kind": "synthetic", "runtime_kind": "none",
              "windows_native": "PASS" if os.name == "nt" else "BLOCKED",
              "sandbox": "NOT_VALIDATED", "supervisor_hash": hashlib.sha256((ROOT / "packages/runner/supervisor.py").read_bytes()).hexdigest()}
    atomic_json(output / "result.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--allow-non-windows", action="store_true")
    args = parser.parse_args()
    if os.name != "nt" and not args.allow_non_windows:
        print(json.dumps({"result": "BLOCKED", "windows_native": "BLOCKED", "reason": "Native Windows host required"}))
        return 3
    output = args.output or ROOT / "reports/native-runner" / datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    result = probe(output.resolve())
    print(json.dumps({"result": result["result"], "windows_native": result["windows_native"], "evidence": str(output)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
