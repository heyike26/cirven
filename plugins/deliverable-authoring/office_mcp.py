"""Optional project-scoped Office MCP. Uses EEAS's existing artifact authority.

No model, download, arbitrary command/path, source-file mutation or native GUI.
Only an explicit host command can select the project, data and source roots.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import sys
from io import BytesIO

PLUGIN_ID = "eeas.deliverable-authoring"
VERSION = "0.1.0"
MAX_INPUT_BYTES = 1024 * 1024
MAX_OUTPUT_BYTES = 65536
MAX_ADMISSIONS = 1000


class PluginError(ValueError):
    pass


def bounded_json(value, maximum=MAX_INPUT_BYTES):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    except (ValueError, TypeError, RecursionError, UnicodeError) as error:
        raise PluginError("FINITE_BOUNDED_JSON_REQUIRED") from error
    if len(raw) > maximum:
        raise PluginError("JSON_BUDGET_EXCEEDED")
    return raw


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def ordinary(path, *, directory=False):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise PluginError("EXPLICIT_ORDINARY_ABSOLUTE_PATH_REQUIRED")
    if not (path.is_dir() if directory else path.is_file()):
        raise PluginError("EXPLICIT_HOST_PATH_UNAVAILABLE")
    return path.resolve(strict=True)


def read_json(path, maximum=MAX_INPUT_BYTES):
    path = ordinary(path)
    if path.stat().st_size > maximum:
        raise PluginError("RECORD_BUDGET_EXCEEDED")
    try:
        value = json.loads(path.read_bytes(), parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        bounded_json(value, maximum)
    except (ValueError, UnicodeError, RecursionError) as error:
        raise PluginError("RECORD_INTEGRITY_UNAVAILABLE") from error
    if not isinstance(value, dict):
        raise PluginError("RECORD_OBJECT_REQUIRED")
    return value


def write_once(path, value):
    raw = bounded_json(value)
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)
        stream.flush()
        os.fsync(stream.fileno())


def component_status():
    try:
        actual = importlib.metadata.version("python-pptx")
    except importlib.metadata.PackageNotFoundError:
        return {"available": False, "code": "OPTIONAL_PYTHON_PPTX_MISSING", "expected_version": "1.0.2"}
    return {"available": actual == "1.0.2", "code": "FIXED_COMPONENT_PRESENT" if actual == "1.0.2" else "OPTIONAL_PYTHON_PPTX_VERSION_MISMATCH",
        "expected_version": "1.0.2", "observed_version": actual}


class DeliverableAuthoring:
    def __init__(self, store, data_dir, project_id, *, max_admissions=MAX_ADMISSIONS):
        from packages.domain.report_artifacts import ReportArtifacts
        from packages.domain.paths import resolve_data_path
        self.store, self.data_dir, self.project_id = store, ordinary(data_dir, directory=True), project_id
        store.project(project_id)
        self.artifacts = ReportArtifacts(store, self.data_dir)
        self.resolve = resolve_data_path
        if type(max_admissions) is not int or not 1 <= max_admissions <= MAX_ADMISSIONS:
            raise PluginError("INVALID_ADMISSION_BUDGET")
        self.max_admissions = max_admissions

    def _id(self, key):
        if not isinstance(key, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", key):
            raise PluginError("EXACT_IDEMPOTENCY_KEY_REQUIRED")
        return sha(bounded_json({"project_id": self.project_id, "idempotency_key": key}))

    def _paths(self, key):
        aid = self._id(key)
        directory = self.resolve("/".join(("deliverable-authoring", self.project_id, "operations", aid)), self.data_dir)
        return aid, directory / "claim.json", directory / "outcome.json"

    def _core_claim(self, aid):
        return self.resolve("/".join(("report-artifacts", self.project_id, "operations", aid + ".json")), self.data_dir)

    def _claim(self, key):
        aid, path, _outcome = self._paths(key)
        if not path.exists():
            return {"incomplete": True} if path.parent.exists() else None
        try:
            value = read_json(path, 8192)
        except (PluginError, OSError):
            # A writer may still be fsyncing or may have crashed mid-write.
            # An unreadable record is never absence or authority to resend.
            return {"incomplete": True}
        if (value.get("schema") != "eeas.deliverable-admission.v1" or value.get("project_id") != self.project_id
            or value.get("artifact_id") != aid or value.get("idempotency_key") != key
            or not re.fullmatch(r"[0-9a-f]{64}", str(value.get("request_sha256")))):
            return {"incomplete": True}
        return value

    def _public(self, manifest):
        audit = manifest["audit"]
        selected = {key: manifest[key] for key in ("id", "project_id", "revision", "kind", "filename", "media_type",
            "sha256", "byte_length", "source_refs", "template_ref", "download_url", "created_at")}
        selected["audit"] = {key: audit[key] for key in ("supported_scope", "preservation_scope", "native_office", "native_windows", "text_fit_and_visual_rendering")}
        result = {"status": "SAVED_NOT_VISUALLY_VERIFIED", "artifact_manifest": selected,
            "manifest_projection": "BOUNDED_SUMMARY_NOT_FULL_MANIFEST", "manifest_sha256": sha(bounded_json(manifest, 4 * MAX_INPUT_BYTES)),
            "artifact_ref": {key: manifest[key] for key in ("project_id", "id", "revision", "sha256")},
            "actual_source_refs": manifest["source_refs"], "request_sha256": manifest["request_sha256"],
            "producer": {"id": PLUGIN_ID, "version": VERSION}, "visual_validation": "NOT_RUN",
            "semantic_acceptance": "NOT_PERFORMED", "native_open": "NOT_REQUESTED",
            "app_plugin_result_projection": "NOT_CONNECTED", "source_files_modified": False}
        bounded_json(result, MAX_OUTPUT_BYTES)
        return result

    def get_operation(self, idempotency_key):
        from packages.domain.store import NotFound
        aid, _claim_path, outcome_path = self._paths(idempotency_key)
        claim = self._claim(idempotency_key)
        if claim and claim.get("incomplete"):
            return {"status": "UNKNOWN_NOT_REPLAYED", "artifact_id": aid, "project_id": self.project_id,
                "reason": "ADMISSION_UNREADABLE_OR_INCOMPLETE", "automatic_replay": False}
        core_path = self._core_claim(aid)
        try:
            core = read_json(core_path) if core_path.exists() else None
        except (PluginError, OSError):
            return {"status": "UNKNOWN_NOT_REPLAYED", "artifact_id": aid, "project_id": self.project_id,
                "reason": "CORE_OPERATION_UNREADABLE", "automatic_replay": False}
        if core is not None and (core.get("project_id") != self.project_id or core.get("artifact_id") != aid
            or claim and core.get("request_sha256") != claim["request_sha256"]):
            raise PluginError("CORE_OPERATION_BINDING_CHANGED")
        try:
            manifest = self.artifacts.get(self.project_id, aid)
        except NotFound:
            manifest = None
        if manifest is not None:
            if core is None or manifest["request_sha256"] != core.get("request_sha256"):
                raise PluginError("ARTIFACT_OPERATION_BINDING_CHANGED")
            return {**self._public(manifest), "operation_observation": "READ_EXISTING_ONLY"}
        if outcome_path.exists():
            outcome = read_json(outcome_path, 8192)
            if (claim is None or outcome.get("schema") != "eeas.deliverable-outcome.v1"
                or outcome.get("project_id") != self.project_id or outcome.get("artifact_id") != aid
                or outcome.get("request_sha256") != claim["request_sha256"] or outcome.get("dispatch_status") != "CALL_RAISED"):
                raise PluginError("OUTCOME_INTEGRITY_FAILED")
            return {"status": "CREATE_RETURNED_ERROR_NOT_REPLAYED", "artifact_id": aid, "project_id": self.project_id,
                "actual_error_code": outcome["actual_error_code"], "automatic_replay": False}
        return {"status": "UNKNOWN_NOT_REPLAYED" if claim or core else "NOT_ADMITTED",
            "artifact_id": aid, "project_id": self.project_id, "automatic_replay": False}

    def make_report_deck(self, request):
        from packages.domain.report_artifacts import ReportArtifactInput, digest
        from pydantic import ValidationError
        from packages.domain.store import Conflict, InvalidInput, NotFound
        bounded_json(request)
        try:
            body = ReportArtifactInput.model_validate(request)
        except ValidationError as error:
            raise PluginError("INVALID_REPORT_REQUEST") from error
        checksum = digest({"project_id": self.project_id, **body.model_dump(exclude={"idempotency_key"})})
        aid, claim_path, outcome_path = self._paths(body.idempotency_key)
        previous = self._claim(body.idempotency_key)
        if previous and previous.get("incomplete"):
            return self.get_operation(body.idempotency_key)
        core_path = self._core_claim(aid)
        core = read_json(core_path) if core_path.exists() else None
        for value in (previous, core):
            if value is not None and value.get("request_sha256") != checksum:
                raise PluginError("IDEMPOTENCY_CONFLICT")
        if previous is not None or core is not None:
            return self.get_operation(body.idempotency_key)
        component = component_status()
        if not component["available"]:
            return {"status": "NOT_ADMITTED_COMPONENT_UNAVAILABLE", "component": component,
                "artifact_id": aid, "automatic_install": False, "model_calls": 0}
        from packages.domain.pptx_export import _report, _template
        # Deterministic source/schema preflight before admission; the core
        # repeats its own authoritative source checks while publishing.
        with self.store.connect() as connection:
            refs = self.artifacts._sources(connection, self.project_id, body.report)
            _report(body.report, self.project_id, refs)
            if body.template:
                template = self.artifacts._document(connection, self.project_id, body.template.model_dump())
                if template["format"] != "pptx":
                    raise PluginError("REGISTERED_PPTX_TEMPLATE_REQUIRED")
                _template(template["raw_bytes"])
        # Reuse the existing SQLite lock only for bounded admission bookkeeping;
        # release it before calling ReportArtifacts.create (which owns its lock).
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = self._claim(body.idempotency_key)
            if previous is not None:
                if not previous.get("incomplete") and previous["request_sha256"] != checksum:
                    raise PluginError("IDEMPOTENCY_CONFLICT")
                return self.get_operation(body.idempotency_key)
            parent = claim_path.parent.parent
            parent.mkdir(parents=True, exist_ok=True)
            self._paths(body.idempotency_key)
            with os.scandir(parent) as entries:
                count = 0
                for entry in entries:
                    count += 1
                    if count >= self.max_admissions:
                        return {"status": "NOT_ADMITTED_CAPACITY", "maximum_admissions": self.max_admissions,
                            "automatic_cleanup": False, "source_files_modified": False}
            claim_path.parent.mkdir(exist_ok=True)
            self._paths(body.idempotency_key)
            try:
                write_once(claim_path, {"schema": "eeas.deliverable-admission.v1", "project_id": self.project_id,
                    "artifact_id": aid, "idempotency_key": body.idempotency_key, "request_sha256": checksum,
                    "plugin_id": PLUGIN_ID, "plugin_version": VERSION})
            except FileExistsError:
                return self.get_operation(body.idempotency_key)
        try:
            saved = self.artifacts.create(self.project_id, body)
        except (Conflict, InvalidInput, NotFound, OSError) as error:
            code = {Conflict: "REPORT_CONFLICT", InvalidInput: "REPORT_INPUT_REJECTED", NotFound: "SOURCE_NOT_FOUND"}.get(type(error), "LOCAL_IO_FAILURE")
            try:
                write_once(outcome_path, {"schema": "eeas.deliverable-outcome.v1", "project_id": self.project_id,
                    "artifact_id": aid, "request_sha256": checksum, "dispatch_status": "CALL_RAISED", "actual_error_code": code})
            except OSError:
                pass  # The admission still blocks replay; UNKNOWN is honest.
            return self.get_operation(body.idempotency_key)
        return self._public(saved["artifact_manifest"])

    def get_artifact(self, artifact_ref):
        if (not isinstance(artifact_ref, dict) or set(artifact_ref) != {"project_id", "id", "revision", "sha256"}
            or artifact_ref["project_id"] != self.project_id or type(artifact_ref["revision"]) is not int
            or artifact_ref["revision"] != 1 or not re.fullmatch(r"[0-9a-f]{64}", str(artifact_ref["id"]))
            or not re.fullmatch(r"[0-9a-f]{64}", str(artifact_ref["sha256"]))):
            raise PluginError("EXACT_PROJECT_ARTIFACT_REF_REQUIRED")
        manifest, raw = self.artifacts.download(self.project_id, artifact_ref["id"])
        if manifest["sha256"] != artifact_ref["sha256"] or manifest["revision"] != artifact_ref["revision"]:
            raise PluginError("ARTIFACT_REF_MISMATCH")
        from packages.domain.pptx_export import _archive
        archive = _archive(raw)
        slide_parts = sorted(name for name in archive.xml if re.fullmatch(r"ppt/slides/slide[0-9]+\.xml", name))
        previews = []
        for name in slide_parts[:30]:
            texts = [element.text or "" for element in archive.xml[name].iter() if element.tag.endswith("}t")]
            previews.append({"part": name, "text_preview": "\n".join(texts)[:160]})
        result = {**self._public(manifest), "inspection": {"byte_sha256": sha(raw), "slide_count": len(slide_parts),
            "archive_member_count": len(archive.data), "slides": previews,
            "coverage": "VERIFIED_ARTIFACT_BYTES_AND_BOUNDED_OOXML_TEXT_NOT_RENDERING"}}
        bounded_json(result, MAX_OUTPUT_BYTES)
        return result


def create_server(adapter):
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from packages.domain.store import Conflict, InvalidInput, NotFound
    server = MCPServer("eeas-deliverable-authoring", version=VERSION, instructions=(
        "Create bounded editable-text PPTX from exact registered source revisions in the host-bound project. "
        "No model, native open, original-file overwrite or semantic/visual acceptance. "
        "After an unknown create outcome only get_report_operation; never resend create to retry."))

    def invoke(function, argument):
        try:
            return function(argument)
        except PluginError as error:
            raise ToolError(str(error)) from error
        except (Conflict, InvalidInput, NotFound, OSError, ValueError) as error:
            code = {Conflict: "REPORT_CONFLICT", NotFound: "SOURCE_OR_ARTIFACT_NOT_FOUND",
                InvalidInput: "REPORT_INPUT_REJECTED"}.get(type(error), "LOCAL_RECORD_OR_INPUT_UNAVAILABLE")
            raise ToolError(code) from error

    @server.tool(name="make_report_deck")
    def make_report_deck(request: dict) -> dict:
        """One explicit source-bound PPTX creation. request uses ReportArtifactInput; no paths/project override."""
        return invoke(adapter.make_report_deck, request)

    @server.tool(name="get_report_artifact")
    def get_report_artifact(artifact_ref: dict) -> dict:
        """Read exact project/id/revision/SHA artifact bytes and bounded OOXML inspection; no rendering."""
        return invoke(adapter.get_artifact, artifact_ref)

    @server.tool(name="get_report_operation")
    def get_report_operation(idempotency_key: str) -> dict:
        """Read only the original create operation after timeout/restart; never recreate an unknown result."""
        return invoke(adapter.get_operation, idempotency_key)
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-dir", required=True, type=Path)
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--source-root", required=True, type=Path)
    args = parser.parse_args()
    source = ordinary(args.source_root, directory=True)
    data = ordinary(args.data_dir, directory=True)
    ordinary(data / "eeas.sqlite3")  # Never create a project or a replacement DB.
    manifest = read_json(Path(__file__).resolve().parent / "manifest.json", 65536)
    if manifest.get("id") != PLUGIN_ID or manifest.get("version") != VERSION:
        raise PluginError("OPTIONAL_PLUGIN_MANIFEST_MISMATCH")
    for row in manifest["files"]:
        path = ordinary(Path(__file__).resolve().parent / row["path"])
        if not path.is_relative_to(Path(__file__).resolve().parent) or sha(path.read_bytes()) != row["sha256"]:
            raise PluginError("OPTIONAL_PLUGIN_BYTES_CHANGED")
    for name, expected in manifest["host_source_sha256"].items():
        path = ordinary(source / name)
        if not path.is_relative_to(source) or sha(path.read_bytes()) != expected:
            raise PluginError("HOST_REPORT_ADAPTER_VERSION_CHANGED")
    if sys.version_info[:2] != (3, 10):
        raise PluginError("FIXED_PYTHON_310_REQUIRED")
    # An exact executable path may be the real binary (native capability
    # admission rejects symlink executables). Use only declared host layouts,
    # not ambient PYTHONPATH or a downloaded fallback environment.
    library = next((source / value for value in manifest["host_dependency_layouts"] if (source / value).is_dir()), None)
    if library is None:
        raise PluginError("MATCHING_HOST_DEPENDENCY_LAYOUT_UNAVAILABLE")
    library = ordinary(library, directory=True)
    if not library.is_relative_to(source):
        raise PluginError("HOST_DEPENDENCY_LAYOUT_ESCAPED")
    sys.path.insert(0, str(library))
    sys.path.insert(0, str(source))
    from packages.domain.store import Store
    adapter = DeliverableAuthoring(Store(data / "eeas.sqlite3"), data, args.project_id)
    create_server(adapter).run(transport="stdio")


if __name__ == "__main__":
    main()
