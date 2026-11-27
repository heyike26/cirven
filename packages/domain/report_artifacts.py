"""Source-bound immutable report artifacts; the existing exporter owns OOXML."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator

from packages.domain.paths import resolve_data_path
from packages.domain.pptx_export import PptxExportError, _ref, export_report_deck
from packages.domain.store import Conflict, InvalidInput, NotFound

PPTX_MIME = "application/vnd.openxmlformats-officedocument.presentationml.presentation"
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical(value).encode("utf-8"))


def filename(value):
    if (not value or len(value) > 200 or value != value.rstrip(" .") or
            any(ord(c) < 32 or ord(c) == 127 or c in '/\\:*?"<>|' for c in value) or
            not value.lower().endswith(".pptx") or
            value.split(".")[0].upper() in {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)],
                                          *[f"LPT{i}" for i in range(1, 10)]}):
        raise ValueError("Report filename must be a plain Windows-safe .pptx filename")
    return value


class TemplateDocumentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=200)
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class ReportArtifactInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    report: dict
    template: TemplateDocumentRef | None = None
    filename: str = Field(default="report.pptx", min_length=1, max_length=200)

    @field_validator("filename")
    @classmethod
    def safe_filename(cls, value):
        return filename(value)


class ReportArtifacts:
    def __init__(self, store, data_dir):
        self.store, self.data_dir = store, Path(data_dir)
        if self.data_dir.is_symlink():
            raise InvalidInput("Artifact data root must not be a symlink")

    def _path(self, project_id, *parts):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
            raise InvalidInput("Unsafe artifact project identity")
        return resolve_data_path("/".join(("report-artifacts", project_id, *parts)), self.data_dir)

    @staticmethod
    def _write(path, value):
        with path.open("x", encoding="utf-8") as stream:
            stream.write(canonical(value) + "\n")
            stream.flush()
            import os
            os.fsync(stream.fileno())

    @staticmethod
    def _read(path):
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise Conflict("Artifact manifest is missing or outside its size bound")
        try:
            value = json.loads(path.read_bytes())
            if not isinstance(value, dict):
                raise ValueError
            return value
        except (OSError, ValueError):
            raise Conflict("Artifact manifest cannot be read") from None

    def _document(self, connection, project_id, ref):
        row = connection.execute("""SELECT d.filename,d.current_revision,v.* FROM documents d
            JOIN document_versions v ON v.document_id=d.id
            WHERE d.id=? AND d.project_id=? AND d.deleted_at IS NULL AND v.revision=?""",
            (ref["document_id"], project_id, ref["revision"])).fetchone()
        if row is None:
            raise NotFound("Report source revision does not exist in this project")
        if row["sha256"] != ref["sha256"] or sha(row["raw_bytes"]) != ref["sha256"]:
            raise Conflict("Report source raw bytes drifted from their exact SHA256")
        parsed = self.store._parse_metadata(connection, ref["document_id"], ref["revision"])
        if parsed.get("content_sha256", sha(row["content"].encode("utf-8"))) != sha(row["content"].encode("utf-8")):
            raise Conflict("Report source parsed text integrity check failed")
        if "line_end" in ref and ref["line_end"] > len(row["content"].splitlines()):
            raise InvalidInput("Report source line range exceeds this exact revision")
        return {**dict(row), "format": parsed.get("format")}

    def _sources(self, connection, project_id, report):
        values = report.get("source_refs", [])
        sections = report.get("sections", [])
        if not isinstance(values, list) or not isinstance(sections, list):
            raise InvalidInput("Report source refs and sections must be lists")
        values = list(values)
        for section in sections:
            if not isinstance(section, dict) or not isinstance(section.get("source_refs", []), list):
                raise InvalidInput("Report sections require explicit source ref lists")
            values.extend(section.get("source_refs", []))
        if len(values) > 64 * 31:
            raise InvalidInput("Report source refs exceed their bounded section scope")
        refs = {canonical(_ref(value, project_id)): _ref(value, project_id) for value in values}
        if not 1 <= len(refs) <= 64:
            raise InvalidInput("Report requires 1..64 exact registered source refs")
        for ref in refs.values():
            if ref["kind"] == "document":
                self._document(connection, project_id, ref)
            else:
                artifact = self.get(project_id, ref["artifact_id"])
                if ref["revision"] != artifact["revision"] or ref["sha256"] != artifact["sha256"]:
                    raise Conflict("Report artifact source revision or SHA256 does not match")
        return [refs[key] for key in sorted(refs)]

    def create(self, project_id, body: ReportArtifactInput):
        self.store.project(project_id)
        try:
            request = body.model_dump(exclude={"idempotency_key"})
            request_sha = digest({"project_id": project_id, **request})
        except (ValueError, TypeError):
            raise InvalidInput("Report request must contain finite JSON values") from None
        artifact_id = digest({"project_id": project_id, "idempotency_key": body.idempotency_key})
        with self.store.connect() as connection:
            # This existing database lock also serializes independent API processes
            # and keeps document/template versions fixed until publication.
            connection.execute("BEGIN IMMEDIATE")
            root = self._path(project_id)
            root.mkdir(parents=True, exist_ok=True)
            operations = self._path(project_id, "operations")
            operations.mkdir(exist_ok=True)
            claim = self._path(project_id, "operations", artifact_id + ".json")
            if claim.exists():
                previous = self._read(claim)
                if previous.get("request_sha256") != request_sha:
                    raise Conflict("IDEMPOTENCY_CONFLICT: report key already binds different sources or input")
            else:
                self._write(claim, {"project_id": project_id, "artifact_id": artifact_id,
                                    "request_sha256": request_sha, "request": request})
            target = self._path(project_id, artifact_id)
            attempt_id = uuid.uuid4().hex
            attempt = self._path(project_id, "attempts", attempt_id)
            attempt.mkdir(parents=True, exist_ok=False)
            self._write(attempt / "started.json", {"attempt_id": attempt_id, "artifact_id": artifact_id,
                "request_sha256": request_sha, "started_at": datetime.now(timezone.utc).isoformat()})
            try:
                refs = self._sources(connection, project_id, body.report)
                template_bytes = None
                if body.template:
                    template = self._document(connection, project_id, body.template.model_dump())
                    if template["format"] != "pptx":
                        raise InvalidInput("Report template must be an original registered .pptx document")
                    template_bytes = template["raw_bytes"]
                if target.exists():
                    manifest = self.get(project_id, artifact_id)
                    if manifest["request_sha256"] != request_sha:
                        raise Conflict("Stored artifact does not match this report operation")
                    self._write(attempt / "result.json", {"status": "REPLAYED", "artifact_id": artifact_id,
                                                        "sha256": manifest["sha256"]})
                    return {"artifact_manifest": manifest, "replayed": True}
                result = export_report_deck(body.report, project_id=project_id,
                    validated_source_refs=refs, template_bytes=template_bytes)
                if sha(result.raw_bytes) != result.sha256 or not 0 < len(result.raw_bytes) <= MAX_ARTIFACT_BYTES:
                    raise Conflict("Exporter output failed the artifact byte/hash bound")
                manifest = {"schema_version": "eeas.artifact-manifest.v1", "id": artifact_id,
                    "project_id": project_id, "revision": 1, "kind": "REPORT_PPTX",
                    "filename": body.filename, "media_type": PPTX_MIME,
                    "sha256": result.sha256, "byte_length": len(result.raw_bytes),
                    "request_sha256": request_sha, "source_refs": refs,
                    "template_ref": body.template.model_dump() if body.template else None,
                    "traceability": result.source_manifest, "audit": result.audit,
                    "producer_sha256": {name: sha((Path(__file__).parent / name).read_bytes())
                                        for name in ("report_artifacts.py", "pptx_export.py")},
                    "runtime_kind": "none", "source_kind": None,
                    "source_kind_reason": "Source registry has no authenticated classification",
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "download_url": f"/api/projects/{project_id}/report-artifacts/{artifact_id}/download"}
                staging = self._path(project_id, ".pending-" + attempt_id)
                staging.mkdir(exist_ok=False)
                with (staging / "report.pptx").open("xb") as stream:
                    stream.write(result.raw_bytes)
                    stream.flush()
                    import os
                    os.fsync(stream.fileno())
                self._write(staging / "manifest.json", {"manifest": manifest, "manifest_sha256": digest(manifest)})
                staging.rename(target)
                self._write(attempt / "result.json", {"status": "SAVED", "artifact_id": artifact_id,
                                                    "sha256": manifest["sha256"]})
                return {"artifact_manifest": manifest, "replayed": False}
            except Exception as error:
                self._write(attempt / "result.json", {"status": "FAIL", "artifact_id": artifact_id,
                    "error_type": type(error).__name__, "message": str(error)[:2000]})
                if isinstance(error, PptxExportError):
                    raise InvalidInput(str(error)) from error
                raise

    def get(self, project_id, artifact_id):
        self.store.project(project_id)
        if not re.fullmatch(r"[0-9a-f]{64}", artifact_id):
            raise InvalidInput("Artifact id must be an exact lowercase SHA256 identity")
        directory = self._path(project_id, artifact_id)
        if not directory.exists():
            raise NotFound("Report artifact does not exist in this project")
        wrapper = self._read(self._path(project_id, artifact_id, "manifest.json"))
        manifest = wrapper.get("manifest")
        if (not isinstance(manifest, dict) or digest(manifest) != wrapper.get("manifest_sha256") or
                manifest.get("project_id") != project_id or manifest.get("id") != artifact_id or
                manifest.get("revision") != 1 or manifest.get("media_type") != PPTX_MIME):
            raise Conflict("Report artifact manifest integrity or project binding failed")
        try:
            filename(manifest["filename"])
        except (KeyError, TypeError, ValueError):
            raise Conflict("Report artifact filename integrity failed") from None
        self._bytes(project_id, artifact_id, manifest)
        return manifest

    def _bytes(self, project_id, artifact_id, manifest):
        path = self._path(project_id, artifact_id, "report.pptx")
        if not path.is_file() or not 0 < path.stat().st_size <= MAX_ARTIFACT_BYTES:
            raise Conflict("Report artifact bytes are missing or outside their size bound")
        raw = path.read_bytes()
        if len(raw) != manifest["byte_length"] or sha(raw) != manifest["sha256"]:
            raise Conflict("Report artifact content integrity check failed")
        return raw

    def download(self, project_id, artifact_id):
        manifest = self.get(project_id, artifact_id)
        return manifest, self._bytes(project_id, artifact_id, manifest)

    def list(self, project_id):
        self.store.project(project_id)
        root = self._path(project_id)
        return [self.get(project_id, path.name) for path in sorted(root.iterdir())
                if re.fullmatch(r"[0-9a-f]{64}", path.name)] if root.exists() else []

    def attempts(self, project_id):
        self.store.project(project_id)
        root = self._path(project_id, "attempts")
        rows = []
        for path in sorted(root.iterdir()) if root.exists() else []:
            start = self._read(self._path(project_id, "attempts", path.name, "started.json"))
            result = self._path(project_id, "attempts", path.name, "result.json")
            rows.append({**start, **(self._read(result) if result.exists() else {"status": "UNKNOWN_OUTCOME"})})
        return rows
