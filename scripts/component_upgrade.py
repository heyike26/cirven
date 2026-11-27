"""Validate and prepare offline component candidates; never install or apply them."""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import tempfile
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener
import xml.etree.ElementTree as ET
import zipfile

MANIFEST_SCHEMA = "eeas.component-manifest.v1"
CANDIDATE_SCHEMA = "eeas.upgrade-candidate.v1"
PRIMARY_HOSTS = {"api.github.com", "www.python.org"}
HEX = re.compile(r"^[0-9a-f]{64}$")
ID = re.compile(r"^[a-z][a-z0-9_.-]{0,79}$")
MAX_FILE = 256 * 1024 * 1024
MAX_TOTAL = 512 * 1024 * 1024


class Rejected(ValueError):
    pass


def require(condition, reason):
    if not condition:
        raise Rejected(reason)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                       allow_nan=False) + "\n").encode("utf-8")


def json_bytes(data):
    def pairs(items):
        result = {}
        for key, value in items:
            require(key not in result, "DUPLICATE_JSON_KEY")
            result[key] = value
        return result
    def invalid(value):
        raise Rejected("NONFINITE_JSON_NUMBER")
    require(len(data) <= 1024 * 1024, "JSON_BOUND_EXCEEDED")
    try:
        result = json.loads(data.decode("utf-8"), object_pairs_hook=pairs, parse_constant=invalid)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise Rejected("INVALID_UTF8_JSON") from error
    require(isinstance(result, dict), "JSON_OBJECT_REQUIRED")
    return result


def path_name(value):
    require(isinstance(value, str) and value and "\\" not in value and ":" not in value
            and "\0" not in value, "INVALID_RELATIVE_PATH")
    require(not any(ord(character) < 32 or character in '<>"|?*' for character in value),
            "WINDOWS_FORBIDDEN_CHARACTER")
    path = PurePosixPath(value)
    require(not path.is_absolute() and str(path) == value and all(part not in (".", "..")
            and part == part.strip().rstrip(".") for part in path.parts), "PATH_ESCAPE_OR_ALIAS")
    require(all(not re.fullmatch(r"(?i)(con|prn|aux|nul|com[1-9¹²³]|lpt[1-9¹²³])(?:\..*)?", part)
                for part in path.parts), "WINDOWS_DEVICE_PATH")
    return value


def read_file(root, relative, limit=MAX_FILE):
    path_name(relative)
    root = Path(root).resolve()
    path = root
    for part in PurePosixPath(relative).parts:
        path /= part
        require(not path.is_symlink(), "SYMLINK_NOT_ALLOWED")
    require(path.is_file() and path.resolve().is_relative_to(root), "REGULAR_PACKAGE_FILE_REQUIRED")
    require(path.stat().st_size <= limit, "FILE_BOUND_EXCEEDED")
    data = path.read_bytes()
    require(len(data) <= limit, "FILE_BOUND_EXCEEDED")
    return data


def save_new(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(canonical(value))


@dataclass(frozen=True)
class ComponentManifest:
    _bytes: bytes

    @classmethod
    def parse(cls, value):
        require(isinstance(value, dict) and set(value) == {"schema_version", "component_id", "version",
                "source", "interfaces", "data_schema", "files", "platforms", "capabilities"}, "MANIFEST_FIELDS")
        require(value["schema_version"] == MANIFEST_SCHEMA, "MANIFEST_SCHEMA_UNSUPPORTED")
        require(isinstance(value["component_id"], str) and ID.fullmatch(value["component_id"]), "COMPONENT_ID")
        require(isinstance(value["version"], str) and re.fullmatch(r"v?[0-9]+\.[0-9]+\.[0-9]+(?:-[A-Za-z0-9.-]+)?", value["version"]), "FIXED_VERSION_REQUIRED")
        source = value["source"]
        require(isinstance(source, dict) and set(source) == {"kind", "repository", "commit", "archive_path", "archive_sha256"}, "SOURCE_FIELDS")
        require(isinstance(source["kind"], str) and source["kind"] in {"synthetic", "public", "internal"} and isinstance(source["repository"], str)
                and source["repository"] and isinstance(source["commit"], str)
                and re.fullmatch(r"[0-9a-f]{40}", source["commit"]), "FIXED_SOURCE_REQUIRED")
        path_name(source["archive_path"])
        require(isinstance(source["archive_sha256"], str) and HEX.fullmatch(source["archive_sha256"]), "SOURCE_SHA256")
        files = value["files"]
        require(isinstance(files, dict) and 1 <= len(files) <= 256, "FILE_COUNT")
        for name, sha in files.items():
            path_name(name)
            require(isinstance(sha, str) and HEX.fullmatch(sha), "FILE_SHA256")
        require(len({name.casefold() for name in files}) == len(files), "WINDOWS_CASE_COLLISION")
        require(files.get(source["archive_path"]) == source["archive_sha256"], "SOURCE_ARCHIVE_NOT_BOUND")
        interfaces = value["interfaces"]
        require(isinstance(interfaces, dict) and interfaces and all(ID.fullmatch(name) and isinstance(sha, str)
                and HEX.fullmatch(sha) for name, sha in interfaces.items()), "INTERFACE_CONTRACT_SHA_REQUIRED")
        require(isinstance(value["data_schema"], str) and ID.fullmatch(value["data_schema"]), "DATA_SCHEMA_REQUIRED")
        caps = value["capabilities"]
        require(isinstance(caps, dict) and 1 <= len(caps) <= 64, "CAPABILITY_COUNT")
        for name, cap in caps.items():
            require(ID.fullmatch(name) and isinstance(cap, dict) and set(cap) == {"parameters_sha256", "permissions", "required_cases", "qualification_scope"}, "CAPABILITY_FIELDS")
            require(isinstance(cap["parameters_sha256"], str) and HEX.fullmatch(cap["parameters_sha256"]), "PARAMETERS_SHA_REQUIRED")
            require(isinstance(cap["permissions"], list) and len(cap["permissions"]) <= 32
                    and all(isinstance(item, str) and 0 < len(item) <= 100 for item in cap["permissions"]), "PERMISSIONS_REQUIRED")
            cases = cap["required_cases"]
            require(isinstance(cases, list) and 1 <= len(cases) <= 128
                    and all(isinstance(item, str) and 0 < len(item) <= 250 for item in cases)
                    and len(set(cases)) == len(cases), "EXACT_CASES_REQUIRED")
            require(isinstance(cap["qualification_scope"], str)
                    and cap["qualification_scope"] in {"fixture_contract", "local_runtime", "company_live"}, "QUALIFICATION_SCOPE")
        platforms = value["platforms"]
        require(isinstance(platforms, dict) and platforms and set(platforms) <= {"linux-x64", "windows-x64"}, "PLATFORMS_REQUIRED")
        for platform, closure in platforms.items():
            require(isinstance(closure, dict) and set(closure) == {"required_files", "required_capabilities"}, "CLOSURE_FIELDS")
            require(isinstance(closure["required_files"], list) and closure["required_files"]
                    and all(isinstance(name, str) and name in files for name in closure["required_files"]), "PLATFORM_FILE_CLOSURE_MISSING")
            require(isinstance(closure["required_capabilities"], list) and closure["required_capabilities"]
                    and all(isinstance(name, str) and name in caps for name in closure["required_capabilities"]), "PLATFORM_CAPABILITY_CLOSURE_MISSING")
        return cls(canonical(value))

    @property
    def value(self):
        return json_bytes(self._bytes)

    @classmethod
    def load(cls, path):
        path = Path(path)
        return cls.parse(json_bytes(read_file(path.parent, path.name, 1024 * 1024)))

    @property
    def sha256(self):
        return digest(self._bytes)

    def verify_files(self, root):
        total = 0
        for name, sha in self.value["files"].items():
            data = read_file(root, name)
            total += len(data)
            require(total <= MAX_TOTAL and digest(data) == sha, "PACKAGE_FILE_INTEGRITY")


def import_candidate(active_path, archive, archive_sha256, registry):
    """Offline-only ZIP import. No metadata client, installer or active write."""
    active = ComponentManifest.load(active_path)
    archive = Path(archive)
    require(not archive.is_symlink() and archive.is_file() and archive.stat().st_size <= MAX_TOTAL, "LOCAL_ARCHIVE_REQUIRED")
    require(isinstance(archive_sha256, str) and HEX.fullmatch(archive_sha256), "ARCHIVE_SHA_REQUIRED")
    archive_bytes = archive.read_bytes()
    require(len(archive_bytes) <= MAX_TOTAL and digest(archive_bytes) == archive_sha256, "ARCHIVE_INTEGRITY")
    registry = Path(registry)
    registry.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(archive_bytes)) as package:
        members = package.infolist()
        require(1 <= len(members) <= 257, "ZIP_MEMBER_COUNT")
        names = [path_name(item.filename) for item in members]
        require(len(set(names)) == len(names) == len({name.casefold() for name in names}), "ZIP_DUPLICATE_OR_CASE_ALIAS")
        require(all(not item.is_dir() and stat.S_IFMT(item.external_attr >> 16) in (0, stat.S_IFREG)
                    and not item.flag_bits & 1 and item.file_size <= MAX_FILE for item in members), "ZIP_REGULAR_FILES_ONLY")
        require(sum(item.file_size for item in members) <= MAX_TOTAL, "ZIP_TOTAL_BOUND")
        require("manifest.json" in names and package.getinfo("manifest.json").file_size <= 1024 * 1024, "ZIP_MANIFEST_REQUIRED")
        target = ComponentManifest.parse(json_bytes(package.read("manifest.json")))
        require(target.value["component_id"] == active.value["component_id"], "COMPONENT_MISMATCH")
        require(set(names) == {"manifest.json", *target.value["files"]}, "ZIP_UNDECLARED_FILES")
        candidate = {"schema_version": CANDIDATE_SCHEMA, "baseline_sha256": active.sha256,
                     "manifest_sha256": target.sha256, "archive_sha256": archive_sha256,
                     "component_id": target.value["component_id"], "version": target.value["version"],
                     "status": "CANDIDATE", "active_changed": False}
        candidate_id = digest(canonical(candidate))
        destination = registry / "candidates" / candidate_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        require(not destination.exists(), "CANDIDATE_ALREADY_EXISTS")
        with tempfile.TemporaryDirectory(dir=destination.parent, prefix=".candidate-") as stage:
            stage = Path(stage)
            save_new(stage / "manifest.json", target.value)
            save_new(stage / "candidate.json", candidate)
            for name in target.value["files"]:
                output = stage / name
                output.parent.mkdir(parents=True, exist_ok=True)
                with package.open(name) as source, output.open("xb") as stream:
                    shutil.copyfileobj(source, stream, length=65536)
            target.verify_files(stage)
            os.rename(stage, destination)
    return destination


def load_candidate(path, active):
    path = Path(path)
    candidate = json_bytes(read_file(path, "candidate.json", 1024 * 1024))
    require(candidate.get("schema_version") == CANDIDATE_SCHEMA and candidate.get("baseline_sha256") == active.sha256, "STALE_BASELINE")
    require(set(candidate) == {"schema_version", "baseline_sha256", "manifest_sha256", "archive_sha256",
                "component_id", "version", "status", "active_changed"}
            and candidate["status"] == "CANDIDATE" and candidate["active_changed"] is False
            and digest(canonical(candidate)) == path.name, "CANDIDATE_DESCRIPTOR_INTEGRITY")
    manifest = ComponentManifest.load(path / "manifest.json")
    require(candidate.get("manifest_sha256") == manifest.sha256, "CANDIDATE_MANIFEST_INTEGRITY")
    manifest.verify_files(path)
    return manifest


def receipt_facts(directory, manifest, capability_id, platform):
    """Verify a bound record_slice receipt. Hashes do not authenticate its author."""
    root = Path(directory)
    binding = json_bytes(read_file(root, "component-observation.json", 1024 * 1024))
    require(binding.get("schema_version") == "eeas.component-observation.v1"
            and binding.get("manifest_sha256") == manifest.sha256
            and binding.get("capability_id") == capability_id and binding.get("platform") == platform,
            "RECEIPT_SCOPE_MISMATCH")
    capability = manifest.value["capabilities"][capability_id]
    require(capability["qualification_scope"] != "company_live", "EXTERNAL_QUALIFICATION_REQUIRED")
    require(binding.get("qualification_scope") == capability["qualification_scope"]
            and binding.get("loaded") is True and binding.get("invoked") is True, "ACTUAL_LOAD_AND_CALL_REQUIRED")
    freeze = json_bytes(read_file(root, "freeze.json", 1024 * 1024))
    result = json_bytes(read_file(root, "result.json", 1024 * 1024))
    require(freeze.get("schema_version") == "eeas.slice-run.v1" and isinstance(freeze.get("command"), list)
            and freeze["command"] and type(result.get("exit_code")) is int and result["exit_code"] == 0
            and result.get("status") == "PASS", "EXECUTED_RECEIPT_REQUIRED")
    require(binding.get("command") == freeze["command"], "RECEIPT_COMMAND_MISMATCH")
    for name in ("freeze.json", "result.json", "stdout.txt", "stderr.txt", "junit.xml", "invocation.json"):
        data = read_file(root, name, 1024 * 1024)
        require(binding.get("artifact_sha256", {}).get(name) == digest(data), "RECEIPT_ARTIFACT_INTEGRITY")
    invocation = json_bytes(read_file(root, "invocation.json", 1024 * 1024))
    require(invocation.get("runtime_os") == binding.get("runtime_os")
            and invocation.get("runtime_arch") in ("x86_64", "AMD64", "amd64")
            and capability_id in invocation.get("capability_calls", []), "ACTUAL_INVOCATION_SCOPE_REQUIRED")
    loaded_files = invocation.get("loaded_files")
    require(isinstance(loaded_files, dict) and loaded_files and all(manifest.value["files"].get(name) == sha
            for name, sha in loaded_files.items()), "ACTUAL_LOADED_SOURCE_REQUIRED")
    code = freeze.get("code_sha256", {})
    refs = binding.get("source_bindings")
    require(isinstance(refs, dict) and set(refs) == set(manifest.value["files"]), "RECEIPT_SOURCE_COVERAGE")
    for source_name, frozen_name in refs.items():
        path_name(frozen_name)
        data = read_file(root / "snapshot", frozen_name)
        require(code.get(frozen_name) == digest(data) == manifest.value["files"][source_name], "RECEIPT_SOURCE_INTEGRITY")
    manifest_ref = binding.get("manifest_reference")
    path_name(manifest_ref)
    require(code.get(manifest_ref) == digest(read_file(root / "snapshot", manifest_ref)) == manifest.sha256,
            "RECEIPT_MANIFEST_INTEGRITY")
    xml = read_file(root, "junit.xml", 1024 * 1024)
    require(b"<!DOCTYPE" not in xml.upper() and b"<!ENTITY" not in xml.upper(), "JUNIT_ENTITY_FORBIDDEN")
    try:
        tree = ET.fromstring(xml)
    except ET.ParseError as error:
        raise Rejected("JUNIT_INVALID") from error
    nodes = list(tree.iter("testcase"))
    ids = [node.get("classname", "") + "." + node.get("name", "") for node in nodes]
    require(nodes and len(set(ids)) == len(ids) and set(ids) == set(capability["required_cases"])
            and all(not list(node) for node in nodes), "EXACT_NONSKIPPED_CASES_REQUIRED")
    require(binding.get("runtime_os") == ("windows" if platform == "windows-x64" else "linux"), "RECEIPT_OS_MISMATCH")
    return {"declared": True, "loaded": True, "qualified": True,
            "qualification_scope": capability["qualification_scope"], "source_kind": manifest.value["source"]["kind"],
            "receipt_sha256": digest(canonical(binding)), "case_ids": ids}


def qualify_candidate(active_path, candidate_path, capability_id, platform, receipt):
    active = ComponentManifest.load(active_path)
    manifest = load_candidate(candidate_path, active)
    require(platform in manifest.value["platforms"] and capability_id in manifest.value["capabilities"], "DECLARATION_REQUIRED")
    facts = receipt_facts(receipt, manifest, capability_id, platform)
    output = Path(candidate_path) / "qualifications" / platform / capability_id
    require(not output.exists(), "QUALIFICATION_ALREADY_EXISTS")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=output.parent, prefix=".qualification-") as stage:
        stage = Path(stage)
        binding = json_bytes(read_file(receipt, "component-observation.json", 1024 * 1024))
        freeze = json_bytes(read_file(receipt, "freeze.json", 1024 * 1024))
        names = ["component-observation.json", "freeze.json", "result.json", "stdout.txt", "stderr.txt", "junit.xml", "invocation.json"]
        names += ["snapshot/" + path_name(name) for name in freeze["code_sha256"]]
        require(len(names) <= 4096, "RECEIPT_FILE_COUNT_BOUND")
        total = 0
        for name in names:
            data = read_file(receipt, name)
            total += len(data)
            require(total <= MAX_TOTAL, "RECEIPT_TOTAL_BOUND")
            destination = stage / "receipt" / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("xb") as stream:
                stream.write(data)
        copied = receipt_facts(stage / "receipt", manifest, capability_id, platform)
        require(copied == facts, "RECEIPT_CHANGED_DURING_COPY")
        save_new(stage / "qualification.json", facts)
        os.rename(stage, output)
    return facts


def plan_upgrade(active_path, candidate_path, required_platforms, rollback=False):
    active = ComponentManifest.load(active_path)
    target = load_candidate(candidate_path, active)
    old, new = active.value, target.value
    require(old["component_id"] == new["component_id"], "COMPONENT_MISMATCH")
    blockers = []
    if any(new["interfaces"].get(name) != sha for name, sha in old["interfaces"].items()):
        blockers.append("INTERFACE_REVIEW_REQUIRED")
    if old["data_schema"] != new["data_schema"]:
        blockers.append("DATA_SCHEMA_MIGRATION_OR_RESTORE_REQUIRED")
    added = sorted(set(new["capabilities"]) - set(old["capabilities"]))
    removed = sorted(set(old["capabilities"]) - set(new["capabilities"]))
    if removed and not rollback:
        blockers.append("CAPABILITY_REMOVAL_REVIEW_REQUIRED")
    if old["version"].lstrip("v").split(".")[0] != new["version"].lstrip("v").split(".")[0] and not rollback:
        blockers.append("MAJOR_VERSION_REVIEW_REQUIRED")
    for name in set(old["capabilities"]) & set(new["capabilities"]):
        if not rollback and not set(old["capabilities"][name]["required_cases"]) <= set(new["capabilities"][name]["required_cases"]):
            blockers.append("REGRESSION_CASE_COVERAGE_REMOVED:" + name)
        if old["capabilities"][name]["parameters_sha256"] != new["capabilities"][name]["parameters_sha256"]:
            blockers.append("PARAMETERS_CONTRACT_REVIEW_REQUIRED:" + name)
    if any(set(cap["permissions"]) - set(old["capabilities"].get(name, {}).get("permissions", []))
           for name, cap in new["capabilities"].items()):
        blockers.append("NEW_PERMISSION_APPROVAL_REQUIRED")
    require(required_platforms and set(required_platforms) <= {"linux-x64", "windows-x64"}, "PLATFORM_POLICY_REQUIRED")
    facts = {}
    for platform in required_platforms:
        if platform not in new["platforms"]:
            blockers.append("PLATFORM_CLOSURE_MISSING:" + platform)
            continue
        for name in new["capabilities"]:
            location = Path(candidate_path) / "qualifications" / platform / name
            if not location.exists():
                facts[platform + "/" + name] = {"declared": True, "loaded": False, "qualified": False}
                if name in new["platforms"][platform]["required_capabilities"]:
                    blockers.append("CAPABILITY_NOT_QUALIFIED:" + platform + "/" + name)
            else:
                actual = receipt_facts(location / "receipt", target, name, platform)
                require(json_bytes(read_file(location, "qualification.json")) == actual, "QUALIFICATION_LEDGER_INTEGRITY")
                facts[platform + "/" + name] = actual
    return {"schema_version": "eeas.upgrade-plan.v1", "baseline_sha256": active.sha256,
            "target_sha256": target.sha256, "component_id": new["component_id"],
            "old_version": old["version"], "new_version": new["version"], "direction": "rollback" if rollback else "upgrade",
            "added_capabilities": added, "removed_capabilities": removed, "capabilities": facts,
            "blockers": sorted(set(blockers)), "status": "BLOCKED" if blockers else "PREPARABLE",
            "active_changed": False, "runtime_installed": False}


def prepare_transaction(active_path, candidate_path, registry, required_platforms, rollback=False):
    plan = plan_upgrade(active_path, candidate_path, required_platforms, rollback)
    require(not plan["blockers"], "PLAN_BLOCKED:" + ",".join(plan["blockers"]))
    active_path, registry = Path(active_path), Path(registry)
    active = ComponentManifest.load(active_path)
    active.verify_files(active_path.parent)
    snapshot = registry / "snapshots" / active.sha256
    if snapshot.exists():
        require(ComponentManifest.load(snapshot / "manifest.json").sha256 == active.sha256, "SNAPSHOT_INTEGRITY")
        active.verify_files(snapshot)
    else:
        snapshot.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=snapshot.parent, prefix=".snapshot-") as stage:
            stage = Path(stage)
            save_new(stage / "manifest.json", active.value)
            for name in active.value["files"]:
                output = stage / name
                output.parent.mkdir(parents=True, exist_ok=True)
                with output.open("xb") as stream:
                    stream.write(read_file(active_path.parent, name))
            active.verify_files(stage)
            os.rename(stage, snapshot)
    require(ComponentManifest.load(active_path).sha256 == plan["baseline_sha256"], "STALE_BASELINE")
    require(plan_upgrade(active_path, candidate_path, required_platforms, rollback) == plan, "CANDIDATE_CHANGED_DURING_PREPARATION")
    transaction = {"schema_version": "eeas.component-transition.v1", "status": "PREPARED",
                   "expected_active_sha256": plan["baseline_sha256"], "target_sha256": plan["target_sha256"],
                   "candidate_path": str(Path(candidate_path).resolve()), "retained_snapshot": str(snapshot.resolve()),
                   "plan": plan, "apply_allowed": False, "active_changed": False}
    output = registry / "prepared" / (digest(canonical(transaction)) + ".json")
    save_new(output, transaction)
    return output


def fetch_release(policy, provider, repository=None, version=None, transport=None):
    """Explicit external metadata discovery; offline commands never call this."""
    require(policy.get("schema_version") == "eeas.component-source-policy.v1", "SOURCE_POLICY_SCHEMA")
    require(isinstance(policy.get("allowed_hosts"), list)
            and all(isinstance(host, str) for host in policy["allowed_hosts"]), "PRIMARY_HOST_POLICY_REQUIRED")
    hosts = set(policy.get("allowed_hosts", []))
    require(hosts and hosts <= PRIMARY_HOSTS, "PRIMARY_HOST_POLICY_REQUIRED")
    def allowed(url):
        parsed = urlsplit(url)
        require(parsed.scheme == "https" and parsed.hostname in hosts and parsed.port in (None, 443)
                and not parsed.username and not parsed.password and not parsed.fragment, "SOURCE_URL_FORBIDDEN")
    class Redirect(HTTPRedirectHandler):
        def redirect_request(self, request, response, code, message, headers, newurl):
            allowed(newurl)
            return super().redirect_request(request, response, code, message, headers, newurl)
    opener = build_opener(ProxyHandler({}), Redirect())
    def get(url):
        allowed(url)
        if transport:
            data, final_url = transport(url)
        else:
            with opener.open(Request(url, headers={"User-Agent": "EEAS-component-metadata/1"}), timeout=20) as response:
                data, final_url = response.read(1024 * 1024 + 1), response.url
        allowed(final_url)
        require(isinstance(data, bytes) and len(data) <= 1024 * 1024, "METADATA_BOUND")
        return data
    if provider == "github":
        require(isinstance(repository, str) and re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository)
                and repository in policy.get("github_repositories", []), "REPOSITORY_NOT_CONFIGURED")
        url = "https://api.github.com/repos/" + repository + "/releases/latest"
        release = json_bytes(get(url))
        tag = release.get("tag_name")
        require(isinstance(tag, str) and 0 < len(tag) <= 100 and release.get("draft") is False
                and release.get("prerelease") is False, "STABLE_RELEASE_REQUIRED")
        commit_url = "https://api.github.com/repos/" + repository + "/commits/" + quote(tag, safe="")
        commit = json_bytes(get(commit_url)).get("sha")
        require(isinstance(commit, str) and re.fullmatch(r"[0-9a-f]{40}", commit), "FIXED_RELEASE_COMMIT_REQUIRED")
        return {"schema_version": "eeas.component-release-observation.v1", "provider": provider,
                "repository": repository, "version": tag, "commit": commit, "release_url": url,
                "release_metadata_sha256": digest(canonical(release)), "declared_capabilities": [],
                "qualified_capabilities": [], "status": "DISCOVERED_ONLY"}
    require(provider == "python" and isinstance(version, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", version)
            and version in policy.get("python_versions", []), "PYTHON_VERSION_NOT_CONFIGURED")
    url = "https://www.python.org/ftp/python/" + version + "/"
    data = get(url)
    return {"schema_version": "eeas.component-release-observation.v1", "provider": provider,
            "version": version, "release_url": url, "release_metadata_sha256": digest(data),
            "commit": None, "declared_capabilities": [], "qualified_capabilities": [], "status": "DISCOVERED_ONLY"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    validate = commands.add_parser("validate")
    validate.add_argument("manifest", type=Path)
    discover = commands.add_parser("discover")
    discover.add_argument("--mode", choices=["external"], required=True)
    discover.add_argument("--policy", type=Path, required=True)
    discover.add_argument("--provider", choices=["github", "python"], required=True)
    discover.add_argument("--repository")
    discover.add_argument("--version")
    for name in ("import", "plan", "qualify", "prepare"):
        command = commands.add_parser(name)
        command.add_argument("--active", type=Path, required=True)
        if name == "import":
            command.add_argument("--archive", type=Path, required=True)
            command.add_argument("--archive-sha256", required=True)
        else:
            command.add_argument("--candidate", type=Path, required=True)
        if name in ("import", "prepare"):
            command.add_argument("--registry", type=Path, required=True)
        if name in ("plan", "prepare"):
            command.add_argument("--platform", action="append", required=True)
            command.add_argument("--rollback", action="store_true")
        if name == "qualify":
            command.add_argument("--capability", required=True)
            command.add_argument("--platform", required=True)
            command.add_argument("--receipt", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            manifest = ComponentManifest.load(args.manifest)
            manifest.verify_files(args.manifest.parent)
            result = {"status": "VALID_DECLARATION", "manifest_sha256": manifest.sha256, "qualified": False}
        elif args.command == "discover":
            result = fetch_release(json_bytes(args.policy.read_bytes()), args.provider, args.repository, args.version)
        elif args.command == "import":
            result = {"candidate": str(import_candidate(args.active, args.archive, args.archive_sha256, args.registry)), "active_changed": False}
        elif args.command == "qualify":
            result = qualify_candidate(args.active, args.candidate, args.capability, args.platform, args.receipt)
        elif args.command == "plan":
            result = plan_upgrade(args.active, args.candidate, args.platform, args.rollback)
        else:
            result = {"prepared_transaction": str(prepare_transaction(args.active, args.candidate, args.registry, args.platform, args.rollback)), "active_changed": False}
        print(canonical(result).decode("utf-8"), end="")
        return 0
    except (Rejected, OSError, zipfile.BadZipFile) as error:
        print(canonical({"status": "REJECTED", "reason": str(error)}).decode("utf-8"), end="")
        return 2


if __name__ == "__main__":
    import sys
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    raise SystemExit(main())
