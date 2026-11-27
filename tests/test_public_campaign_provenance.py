"""Original regression fixtures for composite provenance, not public-source authentication."""
from copy import deepcopy
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace
import zipfile

import pytest

from scripts import public_campaign as campaign

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "eeas.public-material-provenance.v1"
COMMIT = "a" * 40
ORIGIN = "https://github.com/Original/Fixture"


def span(text, start, end):
    prefix = text[:start]
    line_start = len(prefix.splitlines()) + int(not prefix or prefix.endswith(("\n", "\r")))
    return {"char_start": start, "char_end_exclusive": end,
            "byte_start": len(prefix.encode()), "byte_end_exclusive": len(text[:end].encode()),
            "line_start": line_start, "line_end": line_start + len(text[start:end].splitlines()) - 1}


@pytest.fixture
def inputs(tmp_path):
    base = tmp_path / "inputs"
    base.mkdir()
    texts = [("docs/first.txt", "真实正文 😀\r\n第二行\r\n"), ("docs/末尾.md", "# Original heading\n真实末尾")]
    material = "Generated audit metadata\n\n"
    selected, synthetic = [], []
    for name, text in texts:
        header_start = len(material)
        material += "=== GENERATED WRAPPER: " + name + " ===\n"
        synthetic.append({"kind": "GENERATED_WRAPPER", **span(material, header_start, len(material))})
        body_start = len(material)
        material += text
        path = "original/" + name
        original = base / path
        original.parent.mkdir(parents=True, exist_ok=True)
        original.write_bytes(text.encode())
        selected.append({"path": path, "source_path": name, "archive_member": "Fixture-" + COMMIT + "/" + name,
                         "source_url": ORIGIN + "/blob/" + COMMIT + "/" + name,
                         "sha256": campaign.digest(text.encode()), "byte_length": len(text.encode()),
                         "characters": len(text), "body": span(material, body_start, len(material)),
                         "original": span(text, 0, len(text))})
        if not text.endswith("\n"):
            start = len(material)
            material += "\n"
            synthetic.append({"kind": "GENERATED_WRAPPER", **span(material, start, len(material))})
    synthetic.insert(0, {"kind": "GENERATED_WRAPPER", **span(material, 0, len("Generated audit metadata\n\n"))})
    # Canonical complement coalesces adjacent generated regions.
    bodies = [(s["body"]["char_start"], s["body"]["char_end_exclusive"]) for s in selected]
    synthetic = []
    cursor = 0
    for start, end in bodies + [(len(material), len(material))]:
        if cursor < start:
            synthetic.append({"kind": "GENERATED_WRAPPER", **span(material, cursor, start)})
        cursor = end
    raw = base / "material.txt"
    raw.write_bytes(material.encode())
    legal = base / "LICENSE.txt"
    legal.write_bytes(b"Original public-metadata regression fixture; no upstream licensing claim.\n")
    sidecar = {"schema": SCHEMA,
               "parent_archive": {"sha256": "b" * 64, "origin": ORIGIN, "commit_sha": COMMIT,
                                  "archive_url": "https://codeload.github.com/Original/Fixture/zip/" + COMMIT},
               "material": {"sha256": campaign.digest(raw.read_bytes()), "byte_length": raw.stat().st_size,
                            "characters": len(material)},
               "selected": selected, "synthetic_ranges": synthetic,
               "conversion": {"version": "original-fixture-v1", "input_manifest_sha256": "c" * 64},
               "source_authentication": "DECLARED_NOT_INDEPENDENTLY_VERIFIED"}
    sidecar_path = base / "sidecar.json"
    sidecar_path.write_bytes(campaign.encode(sidecar))
    return raw, legal, sidecar_path, sidecar


PROBE = '''
import hashlib,json,os
from pathlib import Path
from scripts.public_campaign import source_for
def test_actual_bound_original():
    root=Path(__file__).resolve().parents[1]
    binding_path=Path(os.environ['EEAS_CAMPAIGN_BINDING'])
    binding=json.loads(binding_path.read_bytes())
    project=(root/binding['raw_path']).parent
    source=source_for(project)
    sidecar_path=root/binding['provenance_path']
    sidecar=json.loads(sidecar_path.read_bytes())
    raw=(root/binding['raw_path']).read_bytes()
    selected=sidecar['selected'][-1]
    original=(sidecar_path.parent/selected['path']).read_bytes()
    body=selected['body']
    assert raw[body['byte_start']:body['byte_end_exclusive']]==original
    assert original.decode().endswith('真实末尾')
    evidence=Path(os.environ['EEAS_EVIDENCE_DIR'])
    artifact=evidence/'actual-selected-original.txt'
    artifact.write_bytes(original)
    observation={'schema':'eeas.campaign-observation.v1',
      'binding_sha256':hashlib.sha256(binding_path.read_bytes()).hexdigest(),
      'source_sha256':source['raw_sha256'],'case_id':binding['case_id'],
      'actual_receipts':[{'path':artifact.name,'sha256':hashlib.sha256(original).hexdigest()}]}
    (evidence/'campaign-observation.json').write_text(json.dumps(observation),encoding='utf-8')
def test_mutates_after_actual_read():
    test_actual_bound_original()
    root=Path(__file__).resolve().parents[1]
    binding=json.loads(Path(os.environ['EEAS_CAMPAIGN_BINDING']).read_bytes())
    sidecar_path=root/binding['provenance_path']
    sidecar=json.loads(sidecar_path.read_bytes())
    original=sidecar_path.parent/sidecar['selected'][0]['path']
    original.write_bytes(original.read_bytes()+b'changed-after-actual-read')
'''


@pytest.fixture
def harness(tmp_path, monkeypatch, request):
    root = tmp_path / "standalone-harness"
    for directory in ("apps/api", "apps/web/assets", "scripts", "tests", "profiles"):
        (root / directory).mkdir(parents=True, exist_ok=True)
    (root / "apps/api/main.py").write_text("app = None\n")
    (root / "apps/api/__main__.py").write_text("# Original inert CLI fixture\n")
    (root / "apps/web/index.html").write_text("<main id='reader'></main>")
    for name in ("app.js", "app.css"):
        (root / "apps/web/assets" / name).write_text("/* Original fixture */")
    for name in ("public_campaign.py", "record_slice.py"):
        shutil.copyfile(ROOT / "scripts" / name, root / "scripts" / name)
    (root / "tests/test_original_provenance.py").write_text(PROBE, encoding="utf-8")
    cases = [{"id": kind.lower(), "evidence_class": kind, "acceptance": "Original provenance boundary"}
             for kind in sorted(campaign.CLASSES)]
    cases[-1]["external_qualification"] = True
    surfaces = campaign.surfaces(root)
    files = surfaces["entry_files"] | {"apps/api/main.py", "apps/web/index.html", "apps/web/assets/app.js", "apps/web/assets/app.css"}
    catalog = {"schema": "eeas.public-campaign-catalog.v1", "cases": cases,
               "surfaces": {key: {name: ["fault_boundary"] for name in names} for key, names in surfaces.items()},
               "source_sha256": {name: campaign.digest((root / name).read_bytes()) for name in files}}
    (root / campaign.CATALOG).write_bytes(campaign.encode(catalog))
    directory = root / "var/campaign"
    campaign.init(root, directory)
    for name in tuple(os.environ):
        if name.startswith(("EEAS_MODEL", "EEAS_CLAUDE", "EEAS_SEARCH", "ANTHROPIC")):
            monkeypatch.delenv(name, raising=False)
    yield root, directory
    # Preserve actual nested launcher/pytest/record_slice evidence beyond pytest's temporary-directory retention.
    evidence = os.environ.get("EEAS_EVIDENCE_DIR")
    if evidence and any((directory / "projects").glob("*/attempts/*/receipt.json")):
        destination = Path(evidence) / "nested-actual" / request.node.name / "standalone-harness"
        shutil.copytree(root, destination, ignore=shutil.ignore_patterns("__pycache__"))


def register(harness, inputs, name="original", **extra):
    root, directory = harness
    raw, legal, path, _ = inputs
    return campaign.register(root, directory, name, raw, legal, source_kind="public", origin=ORIGIN,
                             revision=COMMIT, license_id="Original-test-fixture",
                             expected_sha256=campaign.digest(raw.read_bytes()),
                             expected_license_sha256=campaign.digest(legal.read_bytes()),
                             provenance_file=path, **extra)


def test_register_freezes_whole_originals_and_actual_run_references(harness, inputs):
    root, directory = harness
    record = register(harness, inputs)
    project = directory / "projects/original"
    sidecar = project / record["provenance"]["path"]
    assert sidecar.read_bytes() == inputs[2].read_bytes()
    for item in inputs[3]["selected"]:
        assert (sidecar.parent / item["path"]).read_bytes() == (inputs[2].parent / item["path"]).read_bytes()
    inputs[2].unlink()  # The campaign consumes the captured copy, never the collector's live path.
    receipt = campaign.run_case(root, directory, "original", "fault_boundary", ["tests/test_original_provenance.py::test_actual_bound_original"])
    assert receipt["state"] == "PASS", receipt
    frozen = receipt["frozen_input_sha256"]
    assert frozen[sidecar.relative_to(root).as_posix()] == record["provenance"]["sha256"]
    for item in inputs[3]["selected"]:
        assert frozen[(sidecar.parent / item["path"]).relative_to(root).as_posix()] == item["sha256"]
    binding = campaign.load(project / "attempts" / receipt["attempt_id"] / "binding.json")
    assert binding["provenance_path"] == sidecar.relative_to(root).as_posix()
    assert campaign.summary(root, directory)["verified_public_sources"] is None


@pytest.mark.parametrize("target", ["sidecar", "original", "source"], ids=["sidecar-map", "original-body", "source-binding"])
def test_frozen_mutation_is_rejected_before_execution(harness, inputs, target):
    root, directory = harness
    record = register(harness, inputs)
    project = directory / "projects/original"
    sidecar = project / record["provenance"]["path"]
    path = {"sidecar": sidecar, "original": sidecar.parent / inputs[3]["selected"][0]["path"],
            "source": project / "source.json"}[target]
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ValueError):
        campaign.run_case(root, directory, "original", "fault_boundary", ["tests/test_original_provenance.py::test_actual_bound_original"])
    assert not (project / "attempts").exists()


def test_actual_successful_subprocess_with_midrun_source_mutation_retains_failed_attempt(harness, inputs):
    root, directory = harness
    record = register(harness, inputs)
    receipt = campaign.run_case(root, directory, "original", "fault_boundary",
                                ["tests/test_original_provenance.py::test_mutates_after_actual_read"])
    assert receipt["probe_exit_code"] == 0
    assert receipt["state"] == "FAIL"
    assert "original checksum" in receipt["validation_error"]
    attempt = directory / "projects/original/attempts" / receipt["attempt_id"]
    assert campaign.load(attempt / "receipt.json") == receipt
    assert b"1 passed" in (root / receipt["evidence"] / "stdout.txt").read_bytes()
    item = inputs[3]["selected"][0]
    original_name = (directory / "projects/original/provenance" / item["path"]).relative_to(root).as_posix()
    assert receipt["frozen_input_sha256"][original_name] == item["sha256"]
    assert (root / receipt["evidence"] / "snapshot" / original_name).read_bytes() == (inputs[2].parent / item["path"]).read_bytes()


@pytest.mark.parametrize("attack", ["schema", "material_hash", "commit", "origin", "body_byte", "body_char", "body_line", "original_line", "wrapper", "overlap", "duplicate", "bool_size", "source_url", "authentication"], ids=str)
def test_schema_and_body_map_counterexamples_reject_registration(harness, inputs, attack):
    raw, legal, path, original = inputs
    changed = deepcopy(original)
    first = changed["selected"][0]
    if attack == "schema": changed["schema"] = "unsupported"
    elif attack == "material_hash": changed["material"]["sha256"] = "0" * 64
    elif attack == "commit": changed["parent_archive"]["commit_sha"] = "d" * 40
    elif attack == "origin": changed["parent_archive"]["origin"] = "https://github.com/Another/Fixture"
    elif attack == "body_byte": first["body"]["byte_start"] += 1
    elif attack == "body_char": first["body"]["char_end_exclusive"] -= 1
    elif attack == "body_line": first["body"]["line_start"] += 1
    elif attack == "original_line": first["original"]["line_start"] = 2
    elif attack == "wrapper": changed["synthetic_ranges"] = []
    elif attack == "overlap": changed["selected"][1]["body"] = first["body"]
    elif attack == "duplicate": changed["selected"].append(deepcopy(first))
    elif attack == "bool_size": first["byte_length"] = True
    elif attack == "source_url": first["source_url"] = "https://github.com/Original/Fixture/blob/main/docs/first.txt"
    elif attack == "authentication": changed["source_authentication"] = "VERIFIED"
    path.write_bytes(campaign.encode(changed))
    with pytest.raises(ValueError):
        register(harness, inputs)
    assert not (harness[1] / "projects/original").exists()


@pytest.mark.parametrize("name", ["../outside.txt", "/tmp/outside.txt", "original//file.txt", "original/./file.txt", "original\\file.txt", "original/C:secret.txt", "original/AUX.txt", "original/file. "])
def test_unsafe_reference_paths_are_rejected(harness, inputs, name):
    inputs[3]["selected"][0]["path"] = name
    inputs[2].write_bytes(campaign.encode(inputs[3]))
    with pytest.raises(ValueError): register(harness, inputs)


@pytest.mark.parametrize("attack", ["original_symlink", "parent_symlink", "sidecar_symlink", "case_alias", "count", "sidecar_size", "duplicate_json_key"])
def test_no_symlinks_aliases_or_unbounded_sidecars(harness, inputs, attack):
    raw, legal, path, sidecar = inputs
    if attack in {"original_symlink", "parent_symlink"}:
        original = path.parent / sidecar["selected"][0]["path"]
        if attack == "original_symlink":
            copy = path.parent / "copy.txt"
            copy.write_bytes(original.read_bytes())
            original.unlink()
            original.symlink_to(copy)
        else:
            parent = original.parent
            moved = parent.with_name("real-docs")
            parent.rename(moved)
            parent.symlink_to(moved, target_is_directory=True)
    elif attack == "sidecar_symlink":
        original = path.with_name("real-sidecar.json")
        path.rename(original)
        path.symlink_to(original)
    elif attack == "case_alias":
        duplicate = deepcopy(sidecar["selected"][0])
        duplicate["source_path"] = duplicate["source_path"].upper()
        duplicate["path"] = "original/" + duplicate["source_path"]
        duplicate["archive_member"] = "Fixture-" + COMMIT + "/" + duplicate["source_path"]
        duplicate["source_url"] = ORIGIN + "/blob/" + COMMIT + "/" + duplicate["source_path"]
        alias_path = path.parent / duplicate["path"]
        alias_path.parent.mkdir(parents=True, exist_ok=True)
        alias_path.write_bytes((path.parent / sidecar["selected"][0]["path"]).read_bytes())
        sidecar["selected"].append(duplicate)
        path.write_bytes(campaign.encode(sidecar))
    elif attack == "count":
        sidecar["selected"] = sidecar["selected"] * 129
        path.write_bytes(campaign.encode(sidecar))
    elif attack == "sidecar_size": path.write_bytes(b" " * (512 * 1024 + 1))
    elif attack == "duplicate_json_key": path.write_bytes(b'{"schema":"wrong",' + campaign.encode(sidecar).lstrip()[1:])
    with pytest.raises(ValueError, match="path alias" if attack == "case_alias" else None): register(harness, inputs)


def test_no_sidecar_retains_previous_source_shape_and_behavior(harness, inputs):
    root, directory = harness
    raw, legal, *_ = inputs
    record = campaign.register(root, directory, "legacy", raw, legal, source_kind="synthetic", origin=None,
                               revision="original-v1", license_id="Original-test-fixture",
                               expected_sha256=campaign.digest(raw.read_bytes()), expected_license_sha256=campaign.digest(legal.read_bytes()))
    assert "provenance" not in record
    assert campaign.source_for(directory / "projects/legacy") == record


def test_optional_cli_register_runs_with_stdlib_only(harness, inputs):
    root, directory = harness
    raw, legal, sidecar, _ = inputs
    command = [sys.executable, "-S", str(root / "scripts/public_campaign.py"), "register", str(directory), "cli-captured",
               str(raw), str(legal), "--source-kind", "public", "--origin", ORIGIN, "--revision", COMMIT,
               "--license-id", "Original-test-fixture", "--expected-sha256", campaign.digest(raw.read_bytes()),
               "--expected-license-sha256", campaign.digest(legal.read_bytes()), "--provenance-file", str(sidecar)]
    process = subprocess.run(command, capture_output=True, check=False)
    evidence = os.environ.get("EEAS_EVIDENCE_DIR")
    if evidence:
        target = Path(evidence) / "actual-stdlib-cli"
        target.mkdir()
        campaign.put(target / "command.json", {"command": command, "exit_code": process.returncode,
                                               "source_kind": "original fixture with public metadata; not authenticated source"})
        (target / "stdout.txt").write_bytes(process.stdout)
        (target / "stderr.txt").write_bytes(process.stderr)
        shutil.copytree(directory / "projects", target / "projects")
    assert process.returncode == 0, process.stderr.decode()
    record = json.loads(process.stdout)
    assert record["provenance"]["sha256"] == campaign.digest(sidecar.read_bytes())
    assert campaign.source_for(directory / "projects/cli-captured") == record


@pytest.mark.parametrize("attack", ["material_symlink", "license_symlink", "material_size", "archive_member"])
def test_initial_capture_applies_limits_before_unbounded_reads(harness, inputs, attack):
    raw, legal, sidecar_path, sidecar = inputs
    if attack.endswith("symlink"):
        target = raw if attack == "material_symlink" else legal
        moved = target.with_suffix(".actual")
        target.rename(moved)
        target.symlink_to(moved)
    elif attack == "material_size":
        raw.write_bytes(b"x" * (campaign.MATERIAL_MAX_BYTES + 1))
    else:
        sidecar["selected"][0]["archive_member"] = "another-root/docs/first.txt"
        sidecar_path.write_bytes(campaign.encode(sidecar))
    with pytest.raises(ValueError): register(harness, inputs)
    assert not (harness[1] / "projects/original").exists()


@pytest.mark.parametrize("target", ["file", "parent"], ids=["windows-reparse-file-not-native", "windows-junction-parent-not-native"])
def test_windows_reparse_metadata_is_rejected_before_open_not_native(inputs, monkeypatch, target):
    raw = inputs[0]
    marked = raw if target == "file" else raw.parent
    original_stat = Path.stat
    def injected_stat(path, *args, **kwargs):
        value = original_stat(path, *args, **kwargs)
        if path == marked:
            fields = {name: getattr(value, name) for name in
                      ("st_mode", "st_size", "st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns")}
            return SimpleNamespace(**fields, st_file_attributes=0x400)
        return value
    def forbidden_open(*args, **kwargs):
        raise AssertionError("reparse metadata reached os.open before rejection; this is a simulated Windows contract")
    monkeypatch.setattr(Path, "stat", injected_stat)
    monkeypatch.setattr(campaign.os, "open", forbidden_open)
    with pytest.raises(ValueError, match="symlink|reparse"):
        campaign.provenance_read(raw, campaign.MATERIAL_MAX_BYTES)


def converter_module():
    spec = importlib.util.spec_from_file_location("original_provenance_converter", ROOT / "reports/E11-public-provenance/convert.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def old_manifest(inputs):
    raw, legal, sidecar_path, sidecar = inputs
    archive_path = raw.parent / "original.zip"
    with zipfile.ZipFile(archive_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in sidecar["selected"]:
            archive.writestr(item["archive_member"], (raw.parent / item["path"]).read_bytes())
    selected = []
    for item in sidecar["selected"]:
        row = {key: item[key] for key in ("archive_member", "source_url", "sha256", "byte_length", "characters")}
        row["path"] = item["source_path"]
        row.update({"composite_" + key: value for key, value in item["body"].items()})
        row.update({"original_" + key: value for key, value in item["original"].items()
                    if key not in {"byte_start", "byte_end_exclusive"}})
        selected.append(row)
    manifest = {"schema_version": "eeas.public-corpus.v1", "repo": "Original/Fixture", "commit_sha": COMMIT,
                "archive": {"sha256": campaign.digest(archive_path.read_bytes()), "byte_length": archive_path.stat().st_size,
                            "requested_url": sidecar["parent_archive"]["archive_url"]},
                "material": sidecar["material"], "selected": selected}
    path = raw.parent / "old-manifest.json"
    path.write_bytes(campaign.encode(manifest))
    return path, archive_path, manifest


def test_converter_verifies_archive_and_preserves_old_inputs_then_registers(harness, inputs, old_manifest):
    raw, legal, sidecar_path, _ = inputs
    manifest_path, archive_path, manifest = old_manifest
    before = {p: p.read_bytes() for p in (raw, sidecar_path, manifest_path, archive_path)}
    output = raw.parent / "converted"
    converter = converter_module()
    result = converter.convert(manifest_path, raw, raw.parent / "original", archive_path, output)
    assert result["selected_file_count"] == 2
    assert result["upstream_code_execution"] == "NOT_RUN"
    assert {p: p.read_bytes() for p in before} == before
    with pytest.raises(FileExistsError):
        converter.convert(manifest_path, raw, raw.parent / "original", archive_path, output)
    sidecar = campaign.provenance_json((output / "sidecar.json").read_bytes())
    assert sidecar["parent_archive"]["sha256"] == manifest["archive"]["sha256"]
    assert sidecar["conversion"]["input_manifest_sha256"] == campaign.digest(before[manifest_path])
    record = register(harness, (raw, legal, output / "sidecar.json", sidecar))
    assert record["provenance"]["sha256"] == result["sidecar_sha256"]


@pytest.mark.parametrize("attack", ["archive_sha", "archive_body", "source_body", "old_line_map", "compression"])
def test_converter_rejects_inconsistent_archive_or_mapping_before_output(inputs, old_manifest, attack):
    raw, legal, _, sidecar = inputs
    manifest_path, archive_path, manifest = old_manifest
    first = sidecar["selected"][0]
    if attack == "archive_sha":
        archive_path.write_bytes(archive_path.read_bytes() + b"changed")
    elif attack in {"archive_body", "compression"}:
        compression = zipfile.ZIP_BZIP2 if attack == "compression" else zipfile.ZIP_DEFLATED
        with zipfile.ZipFile(archive_path, "w", compression=compression) as archive:
            for item in sidecar["selected"]:
                body = (raw.parent / item["path"]).read_bytes()
                archive.writestr(item["archive_member"], body + b"changed" if attack == "archive_body" else body)
        manifest["archive"]["sha256"] = campaign.digest(archive_path.read_bytes())
        manifest["archive"]["byte_length"] = archive_path.stat().st_size
        manifest_path.write_bytes(campaign.encode(manifest))
    elif attack == "source_body":
        path = raw.parent / first["path"]
        path.write_bytes(path.read_bytes() + b"changed")
    else:
        manifest["selected"][0]["composite_line_start"] += 1
        manifest_path.write_bytes(campaign.encode(manifest))
    output = raw.parent / "rejected-output"
    with pytest.raises(ValueError):
        converter_module().convert(manifest_path, raw, raw.parent / "original", archive_path, output)
    assert not output.exists()

