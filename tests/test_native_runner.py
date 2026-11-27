import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ROOT
from packages.domain.tasks import TaskService
from packages.runner.worker import Worker
from test_documents import project
from test_tasks import inputs
from test_worker import submitted, until


def test_native_qualification_rejects_actual_wrong_output_even_under_optimized_python(tmp_path):
    script = '''import pathlib,sys
sys.path.insert(0, str(pathlib.Path(sys.argv[1]) / 'scripts'))
import probe_native_runner as probe
original_writer = probe.atomic_json
def malformed_original_fixture(path, payload):
    if path.name == 'request.json' and payload.get('job_id') == 'normal':
        payload = {**payload, 'argv': [sys.executable, '-I', '-c', "import pathlib; pathlib.Path('中文 输出.txt').write_text('wrong original fixture',encoding='utf-8'); print('中文 参数 with spaces')"]}
    original_writer(path, payload)
probe.atomic_json = malformed_original_fixture
probe.probe(pathlib.Path(sys.argv[2]))
'''
    output = tmp_path / 'optimized rejected original fixture'
    result = subprocess.run([sys.executable, '-O', '-c', script, str(ROOT), str(output)],
                            capture_output=True, timeout=30)
    assert result.returncode != 0, 'Wrong actual bytes were qualified under optimized Python'
    assert not (output / 'result.json').exists()
    assert (output / '中文 空格 normal/工作 目录/中文 输出.txt').read_text(encoding='utf-8') == 'wrong original fixture'


def test_original_native_probe_unicode_exit_timeout_and_child_cancel(tmp_path):
    output = Path(os.environ.get("EEAS_EVIDENCE_DIR", tmp_path)) / "中文 验收"
    process = subprocess.run([sys.executable, str(ROOT / "scripts/probe_native_runner.py"),
                              "--output", str(output), "--allow-non-windows"],
                             capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr.decode("utf-8", errors="replace")
    result = json.loads((output / "result.json").read_bytes())
    assert len(result["cases"]) == 4
    assert result["windows_native"] == ("PASS" if os.name == "nt" else "BLOCKED")
    assert all(case["result"] == "PASS" for case in result["cases"])
    if os.name != "nt":
        native = subprocess.run([sys.executable, str(ROOT / "scripts/probe_native_runner.py")], capture_output=True, timeout=5)
        assert native.returncode == 3
        assert json.loads(native.stdout)["windows_native"] == "BLOCKED"


def test_runner_profile_and_real_logs_are_scoped_and_bounded(client, tmp_path):
    profile = client.get("/api/runner").json()
    assert profile["encoding"] == "utf-8" and profile["scope"] == "trusted_original_fixtures"
    assert profile["device_write"] == "DISABLED"
    pid = project(client)
    doc, req, body = inputs(client, pid)
    task, run = submitted(client, pid, body, "logs")
    service = TaskService(tmp_path / "eeas.sqlite3", ROOT)
    worker = Worker(service, tmp_path)
    assert worker.start()
    try:
        result = until(service, pid, run["id"], {"RETURNED"})
        route = f"/api/projects/{pid}/runs/{run['id']}/logs/"
        response = client.get(route + "stdout").json()
        assert response["snapshot"]
        assert "original-candidate-written" in response["text"]
        assert response["bytes"] == len(response["text"].encode("utf-8"))
        assert client.get(route + "invalid").status_code == 404
        other = project(client, "another-project")
        assert client.get(f"/api/projects/{other}/runs/{run['id']}/logs/stdout").status_code == 404
        (tmp_path / result["workspace"] / "stdout.txt").write_bytes(b"x" * 65537)
        assert client.get(route + "stdout").status_code == 422
    finally:
        worker.stop()
