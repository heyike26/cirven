"""Original fixtures test audit bookkeeping; they are not ten public projects."""
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from scripts import public_campaign as campaign

ROOT = Path(__file__).resolve().parents[1]

# This probe handles the captured bytes using the actual EEAS API/SQLite/importer.
# No model transport, internet request, hand-written passing receipt or marker is used.
PROBE = '''
import hashlib, json, os
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from apps.api.config import Settings
from apps.api.main import create_app

def process(write_observation=True):
    binding_path = Path(os.environ['EEAS_CAMPAIGN_BINDING'])
    binding = json.loads(binding_path.read_bytes())
    root = Path(__file__).resolve().parents[1]
    raw = (root / binding['raw_path']).read_bytes()
    assert hashlib.sha256(raw).hexdigest() == binding['project']['raw_sha256']
    evidence = Path(os.environ['EEAS_EVIDENCE_DIR'])
    settings = Settings(evidence / 'actual-db', worker_enabled=False)
    with TestClient(create_app(settings), base_url=settings.origin) as client:
        token = client.get('/api/bootstrap', headers={'X-EEAS-Bootstrap':'1'}).json()['csrf_token']
        client.headers.update({'Origin':settings.origin, 'X-EEAS-CSRF':token})
        project = client.post('/api/projects', json={'name':'Original campaign fixture'})
        assert project.status_code == 201
        pid = project.json()['id']
        uploaded = client.post(f'/api/projects/{pid}/documents',
                               files={'file':('fixture.md',raw)}, data={'source':'original:campaign-boundary'})
        assert uploaded.status_code == 201
        doc = uploaded.json()
        assert doc['sha256'] == binding['project']['raw_sha256']
        original = client.get(f'/api/projects/{pid}/documents/{doc["id"]}/download', params={'revision':doc['revision']})
        assert original.status_code == 200 and original.content == raw
        (evidence/'actual-document.json').write_text(json.dumps(doc),encoding='utf-8')
        (evidence/'actual-download.md').write_bytes(original.content)
    if write_observation:
        receipts = [{'path':n,'sha256':hashlib.sha256((evidence/n).read_bytes()).hexdigest()}
                    for n in ['actual-document.json','actual-download.md']]
        (evidence/'campaign-observation.json').write_text(json.dumps({
            'schema':'eeas.campaign-observation.v1',
            'binding_sha256':hashlib.sha256(binding_path.read_bytes()).hexdigest(),
            'source_sha256':doc['sha256'],'case_id':binding['case_id'],'actual_receipts':receipts
        }),encoding='utf-8')

def test_ingest(): process()
def test_failure():
    process()
    assert False, 'Original intentional failure: retain the failed attempt'
def test_no_observation(): process(False)
def test_skipped(): pytest.skip('Original skip must never become campaign PASS')
'''


@pytest.fixture
def harness(tmp_path, monkeypatch):
    root = tmp_path / "standalone-harness"
    # Keep this isolated tree small; copy actual API/domain/SDK files, not a fake API.
    shutil.copytree(ROOT / "packages", root / "packages", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(ROOT / "apps", root / "apps", ignore=shutil.ignore_patterns("__pycache__", "*.png"))
    shutil.copytree(ROOT / "profiles", root / "profiles")
    (root / "scripts").mkdir()
    for name in ("record_slice.py", "public_campaign.py"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "tests").mkdir()
    (root / "tests/test_campaign_probe.py").write_text(PROBE, encoding="utf-8")
    cases = [
        {"id": "material", "evidence_class": "PUBLIC_MATERIAL", "acceptance": "Public bytes handled by actual importer"},
        {"id": "workflow", "evidence_class": "PROJECT_WORKFLOW", "acceptance": "Actual source driven work"},
        {"id": "fault", "evidence_class": "FAULT_BOUNDARY", "acceptance": "Original raw bytes processed through actual API and immutable DB, exact original download"},
        {"id": "qualification", "evidence_class": "UPSTREAM_QUALIFICATION", "acceptance": "Actual native qualification, never fixture inference", "external_qualification": True},
    ]
    actual = campaign.surfaces(root)
    files = actual["entry_files"] | {"apps/api/main.py", "apps/web/index.html", "apps/web/assets/app.js", "apps/web/assets/app.css"}
    files |= {p.relative_to(root).as_posix() for pattern in ("*.py", "*.sql") for p in (root / "packages").rglob(pattern)}
    files |= {p.relative_to(root).as_posix() for p in (root / "apps/api").glob("*.py")}
    files |= {p.relative_to(root).as_posix() for p in (root / "profiles").glob("*.json") if p.name != Path(campaign.CATALOG).name}
    catalog = {"schema": "eeas.public-campaign-catalog.v1", "cases": cases,
               "surfaces": {key: {name: ["fault"] for name in names} for key, names in actual.items()},
               "source_sha256": {name: campaign.digest((root / name).read_bytes()) for name in files}}
    (root / campaign.CATALOG).write_bytes(campaign.encode(catalog))
    directory = root / "var/campaign"
    campaign.init(root, directory)
    raw = tmp_path / "original.md"
    raw.write_bytes("# 原创小夹具\r\nREQ: original bytes\r\nACCEPT: exact hash\r\n尾部\n".encode())
    legal = tmp_path / "LICENSE"
    legal.write_text("Original test fixture, created for EEAS regression.\n")
    for key in tuple(__import__('os').environ):
        if key.startswith(("EEAS_MODEL", "EEAS_CLAUDE", "EEAS_SEARCH", "ANTHROPIC")):
            monkeypatch.delenv(key, raising=False)
    campaign.register(root, directory, "original", raw, legal, source_kind="synthetic", origin=None,
                      revision="original-fixture-v1", license_id="Original-test-fixture",
                      expected_sha256=campaign.digest(raw.read_bytes()), expected_license_sha256=campaign.digest(legal.read_bytes()))
    return root, directory, raw, legal


def run_probe(harness, node="test_ingest"):
    root, directory, *_ = harness
    return campaign.run_case(root, directory, "original", "fault", ["tests/test_campaign_probe.py::" + node])


def test_actual_catalog_inventories_every_current_surface_without_feature_pass():
    value = campaign.check_catalog(ROOT, campaign.load(ROOT / campaign.CATALOG))
    assert value == {"case_count": 41, "routes_count": 68, "controls_count": 118, "entry_files_count": 30,
                     "meaning": "inventory consistency only; no feature was executed"}
    assert {c["evidence_class"] for c in campaign.load(ROOT / campaign.CATALOG)["cases"]} == campaign.CLASSES
    source = (ROOT / "scripts/build_release_bundle.py").read_text()
    assert '"profiles"' in source  # Existing bundle includes the required catalog; no builder change.
    cli = subprocess.run([sys.executable, "-S", str(ROOT / "scripts/public_campaign.py"), "catalog"], capture_output=True)
    assert cli.returncode == 0 and json.loads(cli.stdout)["meaning"] == value["meaning"]


def test_all_project_cases_present_defaults_and_external_cannot_grant_pass(harness):
    root, directory, *_ = harness
    rows = campaign.summary(root, directory)
    assert rows["distinct_declared_public_sources"] == 0
    assert rows["verified_public_sources"] is None
    assert rows["minimum_distinct_public_projects"] == 10
    cases = rows["projects"][0]["cases"]
    assert set(cases) == {"material", "workflow", "fault", "qualification"}
    assert [cases[k]["state"] for k in cases] == ["NOT_RUN", "NOT_RUN", "NOT_RUN", "QUALIFICATION_REQUIRED"]
    with pytest.raises(ValueError, match="external qualification"):
        campaign.run_case(root, directory, "original", "qualification", ["tests/test_campaign_probe.py::test_ingest"])
    with pytest.raises(ValueError, match="fixtures cannot satisfy"):
        campaign.run_case(root, directory, "original", "material", ["tests/test_campaign_probe.py::test_ingest"])


def test_explicit_public_metadata_hash_license_and_duplicate_origin_are_required(harness):
    root, directory, raw, legal = harness
    raw.write_bytes(b"Declared public metadata fixture, not fetched source\n")
    kwargs = dict(source_kind="public", origin="https://github.com/Original/Repository.git", revision="tag:fixture-v1",
                  license_id="MIT", expected_sha256=campaign.digest(raw.read_bytes()), expected_license_sha256=campaign.digest(legal.read_bytes()))
    with pytest.raises(ValueError, match="checksum"):
        campaign.register(root, directory, "bad-hash", raw, legal, **{**kwargs, "expected_sha256": "0" * 64})
    with pytest.raises(ValueError, match="origin required"):
        campaign.register(root, directory, "bad-origin", raw, legal, **{**kwargs, "origin": None})
    with pytest.raises(ValueError, match="credential-free"):
        campaign.register(root, directory, "bad-secret", raw, legal, **{**kwargs, "origin": "https://dummy:dummy@github.com/Original/Repository"})
    with pytest.raises(ValueError, match="repository root"):
        campaign.register(root, directory, "bad-subpath", raw, legal,
                          **{**kwargs, "origin": "https://github.com/Original/Repository/blob/tag/README.md"})
    value = campaign.register(root, directory, "declared", raw, legal, **kwargs)
    assert value["provenance_status"] == "DECLARED_NOT_INDEPENDENTLY_VERIFIED"
    raw.write_bytes(b"Different version of same declared repository\n")
    with pytest.raises(ValueError, match="duplicate"):
        campaign.register(root, directory, "duplicate", raw, legal, **{**kwargs, "origin": "https://github.com/original/repository/",
                          "expected_sha256": campaign.digest(raw.read_bytes())})
    assert campaign.summary(root, directory)["distinct_declared_public_sources"] == 1  # Declaration, not real-origin verification.


@pytest.mark.parametrize("path", ["source.raw", "license.raw", "source.json"])
def test_source_and_license_records_are_write_once_and_checked(harness, path):
    root, directory, *_ = harness
    (directory / "projects/original" / path).write_bytes(b"changed")
    with pytest.raises((ValueError, json.JSONDecodeError)):
        campaign.summary(root, directory)


def test_new_route_control_entry_or_source_invalidates_frozen_catalog(harness):
    root, directory, *_ = harness
    app = root / "apps/api/main.py"
    old = app.read_bytes()
    app.write_bytes(old + b'\n@app.get("/unmapped")\ndef new_feature(): pass\n')
    with pytest.raises(ValueError, match="surface"):
        campaign.summary(root, directory)
    app.write_bytes(old)
    (root / "apps/web/index.html").write_text('<button id="unmapped-control">New</button>')
    with pytest.raises(ValueError, match="surface"):
        campaign.summary(root, directory)


def test_actual_run_requires_bound_original_input_artifacts_and_retains_failed_attempt(harness):
    first = run_probe(harness, "test_failure")
    assert first["state"] == "FAIL" and first["probe_exit_code"] == 1
    assert first["test_counts"] == {"executed": 1, "failed": 1, "skipped": 0}
    second = run_probe(harness)
    assert second["state"] == "PASS", second
    assert second["test_counts"] == {"executed": 1, "failed": 0, "skipped": 0}
    root, directory, *_ = harness
    row = campaign.summary(root, directory)["projects"][0]["cases"]["fault"]
    assert row["state"] == "PASS"
    assert [r["state"] for r in row["attempts"]] == ["FAIL", "PASS"]
    assert (root / first["evidence"] / "stdout.txt").is_file()
    assert second["source_kind"] == "synthetic" and second["review_required"]


@pytest.mark.parametrize("node,reason", [("test_no_observation", "campaign-observation"), ("test_skipped", "hidden skipped")])
def test_zero_exit_without_actual_bound_receipt_or_skips_is_never_pass(harness, node, reason):
    receipt = run_probe(harness, node)
    assert receipt["probe_exit_code"] == 0
    assert receipt["state"] == "FAIL" and reason in receipt["validation_error"]
    root, directory, *_ = harness
    (root / receipt["evidence"] / "stdout.txt").write_bytes(b"changed rejected-run output\n")
    with pytest.raises(ValueError, match="raw evidence"):
        campaign.summary(root, directory)


def test_changed_stdout_or_self_written_receipt_cannot_be_accepted(harness):
    root, directory, *_ = harness
    receipt = run_probe(harness)
    assert receipt["state"] == "PASS", receipt
    stdout = root / receipt["evidence"] / "stdout.txt"
    old = stdout.read_bytes()
    stdout.write_bytes(b"1 passed (self-written replacement)\n")
    with pytest.raises(ValueError, match="raw evidence"):
        campaign.summary(root, directory)
    stdout.write_bytes(old)
    fake = directory / "projects/original/attempts/self-written"
    fake.mkdir()
    campaign.put(fake / "receipt.json", {"state": "PASS", "probe_exit_code": 0})
    with pytest.raises((ValueError, OSError, KeyError)):
        campaign.summary(root, directory)
    result = subprocess.run([sys.executable, str(root / "scripts/public_campaign.py"), "mark-pass"], capture_output=True)
    assert result.returncode == 2  # No import/mark-PASS API accepts an Agent's self-written receipt.
