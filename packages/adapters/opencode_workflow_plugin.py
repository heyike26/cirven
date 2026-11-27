"""Exact bundled OpenCode plugin admission/configuration; no model/provider secrets.

This helper never edits OpenCode itself or arbitrary user configuration. A fresh
owner runtime directory receives a canonical private config and CAS policy file.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
from urllib.parse import urlsplit

PLUGIN_ID = "eeas.dynamic-workflow"
PLUGIN_VERSION = "0.2.0"
OPENCODE_VERSION = "1.19.0"
OPENCODE_COMMIT = "733b2c627e5aec39cb33cc22373bdce33053ef2c"
MANIFEST_SHA256 = "86f5301c99b6ecf10a6263c08dd6dc898bc6a238a61c60ac2e1a02be7ddb41b5"
_IDENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z")
_VALUE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,119}\Z")


class WorkflowPluginError(ValueError):
    pass


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(value):
    return hashlib.sha256((value if isinstance(value, bytes) else _canonical(value).encode("utf-8"))).hexdigest()


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise WorkflowPluginError("WORKFLOW_DUPLICATE_KEY")
        result[key] = value
    return result


def _json(raw):
    return json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(WorkflowPluginError("WORKFLOW_NON_FINITE")))


def _path(value):
    item = Path(value)
    if not item.is_absolute() or ".." in item.parts or any(part.is_symlink() for part in (item, *item.parents)):
        raise WorkflowPluginError("WORKFLOW_PATH_REJECTED")
    return item


def _read(path):
    path = _path(path)
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise WorkflowPluginError("WORKFLOW_FILE_REJECTED")
    return path.read_bytes()


def _ref(value):
    return (isinstance(value, dict) and set(value) == {"id", "revision", "sha256"}
            and isinstance(value["id"], str) and _IDENT.fullmatch(value["id"])
            and type(value["revision"]) is int and value["revision"] >= 1
            and isinstance(value["sha256"], str) and re.fullmatch(r"[a-f0-9]{64}", value["sha256"]))


def _owner(value):
    if (not isinstance(value, dict) or set(value) != {"project_id", "session_ref", "generation"}
            or not isinstance(value["project_id"], str) or not _IDENT.fullmatch(value["project_id"])
            or not _ref(value["session_ref"]) or type(value["generation"]) is not int or value["generation"] < 0):
        raise WorkflowPluginError("WORKFLOW_OWNER_INVALID")


def _nodes(value):
    if not isinstance(value, list) or len(value) > 64:
        raise WorkflowPluginError("WORKFLOW_NODES_INVALID")
    ids = set()
    for node in value:
        if (not isinstance(node, dict) or not {"id", "agent_policy"} <= set(node) <= {"id", "title", "agent_policy"}
                or not isinstance(node["id"], str) or not _IDENT.fullmatch(node["id"]) or node["id"] in ids
                or ("title" in node and (not isinstance(node["title"], str) or len(node["title"]) > 200
                                        or any(ord(c) < 32 or ord(c) == 127 for c in node["title"])))):
            raise WorkflowPluginError("WORKFLOW_NODE_INVALID")
        ids.add(node["id"])
        policy = node["agent_policy"]
        if not isinstance(policy, dict) or set(policy) != {"model", "depth"}:
            raise WorkflowPluginError("WORKFLOW_POLICY_INVALID")
        for pin in policy.values():
            if (not isinstance(pin, dict) or set(pin) != {"mode", "value"}
                    or not ((pin["mode"] == "AUTO" and pin["value"] is None)
                            or (pin["mode"] == "MANUAL" and isinstance(pin["value"], str) and _VALUE.fullmatch(pin["value"])))):
                raise WorkflowPluginError("WORKFLOW_POLICY_INVALID")


def _dependencies(value, nodes):
    ids = {node["id"] for node in nodes}
    if not isinstance(value, list) or len(value) > 256:
        raise WorkflowPluginError("WORKFLOW_DEPENDENCIES_INVALID")
    seen = set()
    for edge in value:
        if (not isinstance(edge, dict) or set(edge) != {"source", "target"}
                or edge["source"] not in ids or edge["target"] not in ids
                or edge["source"] == edge["target"] or (edge["source"], edge["target"]) in seen):
            raise WorkflowPluginError("WORKFLOW_DEPENDENCY_INVALID")
        seen.add((edge["source"], edge["target"]))
    done, visiting = set(), set()
    def visit(ident):
        if ident in visiting:
            raise WorkflowPluginError("WORKFLOW_DEPENDENCY_CYCLE")
        if ident in done:
            return
        visiting.add(ident)
        for source, target in seen:
            if source == ident:
                visit(target)
        visiting.remove(ident)
        done.add(ident)
    for ident in ids:
        visit(ident)


def _catalog(value):
    if not isinstance(value, list) or len(value) > 16:
        raise WorkflowPluginError("WORKFLOW_CATALOG_INVALID")
    ids = set()
    for route in value:
        if (not isinstance(route, dict) or set(route) != {"id", "provider_id", "model_id", "variants", "read_only"}
                or not all(isinstance(route.get(key), str) and _IDENT.fullmatch(route[key]) for key in ("id", "provider_id"))
                or route["id"] in ids or not isinstance(route["model_id"], str) or not _VALUE.fullmatch(route["model_id"])
                or route["read_only"] is not True or not isinstance(route["variants"], list) or not 1 <= len(route["variants"]) <= 8
                or any(item is not None and (not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", item)) for item in route["variants"])
                or len(set(route["variants"])) != len(route["variants"])):
            raise WorkflowPluginError("WORKFLOW_CATALOG_ROUTE_INVALID")
        ids.add(route["id"])


def verify_workflow_plugin(plugin_root=None):
    root = _path(plugin_root or Path(__file__).resolve().parents[2] / "toolchain/opencode-plugins/dynamic-workflow")
    raw = _read(root / "manifest.json")
    if hashlib.sha256(raw).hexdigest() != MANIFEST_SHA256:
        raise WorkflowPluginError("WORKFLOW_PLUGIN_MANIFEST_DRIFT")
    manifest = _json(raw)
    if manifest.get("id") != PLUGIN_ID or manifest.get("version") != PLUGIN_VERSION:
        raise WorkflowPluginError("WORKFLOW_PLUGIN_VERSION_DRIFT")
    observed, checked_directories = {}, {root}
    for item in manifest["files"]:
        name = item["path"]
        if (not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_./-]+", name)
                or name.startswith("/") or any(part in ("", ".", "..") for part in name.split("/"))):
            raise WorkflowPluginError("WORKFLOW_PLUGIN_MEMBER_REJECTED")
        try:
            member = root
            for part in name.split("/")[:-1]:
                member = member / part
                if member not in checked_directories:
                    metadata = member.lstat()
                    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISDIR(metadata.st_mode):
                        raise WorkflowPluginError("WORKFLOW_PLUGIN_PATH_REJECTED")
                    checked_directories.add(member)
            member = root / name
            metadata = member.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 1024 * 1024:
                raise WorkflowPluginError("WORKFLOW_PLUGIN_MEMBER_REJECTED")
            content = member.read_bytes()
        except OSError:
            raise WorkflowPluginError("WORKFLOW_PLUGIN_SOURCE_MISSING_OR_UNREADABLE") from None
        if len(content) != item["bytes"] or hashlib.sha256(content).hexdigest() != item["sha256"]:
            raise WorkflowPluginError("WORKFLOW_PLUGIN_SOURCE_DRIFT")
        observed[name] = item["sha256"]
    return {"plugin_id": PLUGIN_ID, "plugin_version": PLUGIN_VERSION, "plugin_root": str(root),
            "manifest_sha256": MANIFEST_SHA256, "source_hashes": observed,
            "entry": (root / "index.mjs").as_uri(), "capabilities": manifest["capabilities"]}


def _write_new(path, value):
    raw = (_canonical(value) + "\n").encode("utf-8")
    try:
        with path.open("xb") as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
    except FileExistsError:
        if _read(path) != raw:
            raise WorkflowPluginError("WORKFLOW_PRIVATE_SOURCE_CONFLICT") from None
    return hashlib.sha256(raw).hexdigest()


def _policy(owner_ref, nodes, plan_ref, dependencies, revision):
    _owner(owner_ref)
    _nodes(nodes)
    _dependencies(dependencies, nodes)
    if plan_ref is not None and not _ref(plan_ref):
        raise WorkflowPluginError("WORKFLOW_PLAN_REF_INVALID")
    value = {"schema": "eeas.dynamic-workflow.policy.v1", "owner_ref": owner_ref,
             "revision": revision, "plan_ref": plan_ref, "nodes": nodes, "dependencies": dependencies}
    return {**value, "sha256": _digest(value)}


def prepare_workflow_plugin(runtime_dir, *, owner_ref, catalog, nodes, plan_ref=None, dependencies=(), sink_url=None, plugin_root=None, model_replan=False):
    """Write only fresh/canonical own files and return exact V1 config admission.

    Catalog qualification and profile credentials are owned by the caller. This
    helper never declares an untested route effective or installs npm packages.
    """
    verified = verify_workflow_plugin(plugin_root)
    _owner(owner_ref)
    _catalog(catalog)
    nodes = json.loads(_canonical(nodes))
    dependencies = list(dependencies)
    policy = _policy(owner_ref, nodes, plan_ref, dependencies, 1)
    if type(model_replan) is not bool or (model_replan and (plan_ref is None or sink_url is None)):
        raise WorkflowPluginError("WORKFLOW_MODEL_REPLAN_SCOPE_REQUIRED")
    count = len(nodes) * sum(len(route["variants"]) for route in catalog)
    if count > 256:
        raise WorkflowPluginError("WORKFLOW_ALIAS_CAPACITY")
    sink = None
    if sink_url is not None:
        url = urlsplit(sink_url)
        if (url.scheme != "http" or url.hostname != "127.0.0.1" or not url.port or url.username or url.password
                or url.path != "/api/dynamic-workflow/plugin-events" or url.query or url.fragment):
            raise WorkflowPluginError("WORKFLOW_EVENT_SINK_REJECTED")
        sink = {"url": sink_url, "token_env": "EEAS_WORKFLOW_EVENT_TOKEN"}
    directory = _path(runtime_dir)
    directory.mkdir(parents=True, exist_ok=True)
    config_path, policy_path, state_path = (directory / name for name in ("plugin-config.json", "policy.json", "projection.json"))
    for target in (config_path, policy_path, state_path):
        _path(target)
    _write_new(policy_path, policy)
    config = {"schema": "eeas.dynamic-workflow.plugin-config.v1", "runtime": {"version": OPENCODE_VERSION, "source_commit": OPENCODE_COMMIT},
              "owner_ref": owner_ref, "catalog": catalog, "nodes": nodes, "plan_ref": plan_ref,
              "dependencies": dependencies, "policy_path": str(policy_path), "state_path": str(state_path), "sink": sink, "model_replan": model_replan}
    config_sha = _write_new(config_path, config)
    admission = {**verified, "config_path": str(config_path), "config_sha256": config_sha,
                 "policy_path": str(policy_path), "state_path": str(state_path), "owner_ref": owner_ref,
                 "policy_ref": {"revision": 1, "sha256": policy["sha256"]}}
    aliases = [{"alias": "eeas_wf_" + _digest([node["id"], route["id"], variant])[:24], "node_id": node["id"],
                "route_id": route["id"], "provider_id": route["provider_id"], "model_id": route["model_id"], "variant": variant}
               for node in nodes for route in catalog for variant in route["variants"]]
    return {"config_fragment": {"plugin": [[verified["entry"], {"configPath": str(config_path), "configSha256": config_sha}]]},
            "admission": admission, "aliases": aliases, "capabilities": verified["capabilities"]}


def write_workflow_policy(admission, *, nodes, plan_ref, dependencies=(), expected_ref):
    """Update only the admitted mutable policy via real lock/CAS; no model dispatch."""
    filename = _path(admission["policy_path"])
    lock = filename.with_name(filename.name + ".lock")
    try:
        descriptor = os.open(lock, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise WorkflowPluginError("WORKFLOW_POLICY_BUSY") from None
    temp = None
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(b"eeas-workflow-policy-cas\n")
            stream.flush()
            os.fsync(stream.fileno())
        old = _json(_read(filename))
        old_body = {key: value for key, value in old.items() if key != "sha256"}
        if (old.get("schema") != "eeas.dynamic-workflow.policy.v1" or old.get("owner_ref") != admission["owner_ref"]
                or _digest(old_body) != old.get("sha256") or expected_ref != {"revision": old.get("revision"), "sha256": old.get("sha256")}):
            raise WorkflowPluginError("WORKFLOW_POLICY_CAS_CONFLICT")
        if old.get("plan_ref") is not None and (plan_ref is None or old["plan_ref"]["id"] != plan_ref.get("id")):
            raise WorkflowPluginError("WORKFLOW_POLICY_PLAN_CONFLICT")
        new = _policy(admission["owner_ref"], nodes, plan_ref, list(dependencies), old["revision"] + 1)
        if all(new[key] == old[key] for key in ("owner_ref", "plan_ref", "nodes", "dependencies")):
            return {"revision": old["revision"], "sha256": old["sha256"]}
        with tempfile.NamedTemporaryFile(dir=filename.parent, prefix="policy-", suffix=".tmp", delete=False) as stream:
            temp = Path(stream.name)
            stream.write((_canonical(new) + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, filename)
        return {"revision": new["revision"], "sha256": new["sha256"]}
    finally:
        if temp is not None and temp.exists():
            temp.unlink()
        lock.unlink()
