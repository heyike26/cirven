"""Selected local-file sources into the existing real document/intake pipeline."""
from contextlib import contextmanager
import os
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from packages.domain.local_workspaces import FileSource, digest, relative, sha
from packages.domain.store import Conflict, InvalidInput, NotFound


class LocalMaterialsInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    paths: list[str] = Field(min_length=1, max_length=20)
    expected_sources: list[FileSource] | None = Field(default=None, max_length=20)

    @model_validator(mode="after")
    def exact_selection(self):
        if len(set(self.paths)) != len(self.paths): raise ValueError("Selected local paths must be unique")
        for path in self.paths: relative(path)
        if self.expected_sources is not None and (len(self.expected_sources) != len(self.paths) or
                {ref.path for ref in self.expected_sources} != set(self.paths)):
            raise ValueError("Expected FileSources must exactly cover the selected paths")
        return self


class LocalMaterialBusy(Conflict):
    code = 'BUSY'


class LocalMaterials:
    def __init__(self, workspaces):
        self.workspaces, self.store = workspaces, workspaces.store

    @contextmanager
    def _lock(self, path, *, blocking=True, create=True):
        if type(blocking) is not bool or type(create) is not bool:
            raise InvalidInput('Lock mode must be explicit booleans')
        native = self.workspaces._native_handles()
        if native is not None:
            options={} if blocking and create else {'blocking':blocking,'create':create}
            with native.lock(path,**options): yield
            return
        if os.name != "posix": raise InvalidInput("Local material process locks are not yet qualified on this platform")
        import fcntl
        if create:path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(path, os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0), 0o600)
        locked=False
        try:
            try:fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            except BlockingIOError:raise LocalMaterialBusy('BUSY: the original delivery is already active')from None
            locked=True;yield
        finally:
            if locked:fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def _lookup(self, project_id, marker, prefix):
        with self.store.connect() as connection:
            exact = connection.execute("""SELECT d.id,v.revision,v.sha256 FROM documents d
                JOIN document_versions v ON v.document_id=d.id
                WHERE d.project_id=? AND d.deleted_at IS NULL AND v.source=?""", (project_id, marker)).fetchall()
            parents = connection.execute("""SELECT DISTINCT d.id FROM documents d
                JOIN document_versions v ON v.document_id=d.id
                WHERE d.project_id=? AND d.deleted_at IS NULL AND v.source LIKE ?""", (project_id, prefix + "%")).fetchall()
        if len(exact) > 1 or len(parents) > 1:
            raise Conflict("UNKNOWN_OUTCOME: multiple document identities claim the same local source")
        return dict(exact[0]) if exact else None, parents[0]["id"] if parents else None

    def _validate_registered(self, project_id, material):
        ref = material["document_ref"]
        try: actual = self.store.document(project_id, ref["document_id"], ref["revision"])
        except NotFound as error: raise Conflict("Existing local material document is no longer registered") from error
        if actual["sha256"] != ref["sha256"] or sha(actual["raw_bytes"]) != ref["sha256"]:
            raise Conflict("Existing local material document bytes drifted")

    def materialize(self, project_id, wid, body: LocalMaterialsInput):
        workspace = self.workspaces.get(project_id, wid)
        operation = self.workspaces._data(project_id, wid, "material-operations", digest(body.operation_id))
        request_hash = digest(body.model_dump(exclude={"operation_id"}))
        with self._lock(operation / "lock"):
            claim = operation / "request.json"
            if claim.exists():
                if self.workspaces._json(claim)["request_sha256"] != request_hash:
                    raise Conflict("IDEMPOTENCY_CONFLICT: material operation already binds another selection")
            else:
                self.workspaces._write(claim, {"request_sha256": request_hash,
                                              "request": body.model_dump(exclude={"operation_id"})})
            snapshot = operation / "sources.json"
            if not snapshot.exists():
                sources = []
                for path in body.paths:
                    value = self.workspaces.read(project_id, wid, path)
                    sources.append(value["source_ref"])
                if body.expected_sources is not None:
                    expected = {ref.path: ref.model_dump() for ref in body.expected_sources}
                    if any(source != expected[source["path"]] for source in sources):
                        raise Conflict("SOURCE_DRIFT: selected local bytes differ from expected FileSources")
                self.workspaces._write(snapshot, {"binding_sha256": workspace["binding_sha256"], "sources": sources})
            frozen = self.workspaces._json(snapshot)
            if frozen["binding_sha256"] != workspace["binding_sha256"]:
                raise Conflict("Material workspace grant changed")
            refs = [FileSource.model_validate(ref) for ref in frozen["sources"]]
            checks = self.workspaces.check_sources(project_id, refs)
            completed = operation / "complete.json"
            if completed.exists():
                saved = self.workspaces._json(completed)
                for material in saved["materials"]: self._validate_registered(project_id, material)
                return {**saved, "replayed": True, "source_checks": checks}
            if any(check["status"] != "CURRENT" for check in checks):
                raise Conflict("SOURCE_DRIFT: local material changed during this operation; start a new source-bound operation")
            materials = []
            for ref in refs:
                slot = operation / (digest(ref.path) + ".saved.json")
                if slot.exists():
                    saved = self.workspaces._json(slot); self._validate_registered(project_id, saved)
                    materials.append(saved); continue
                # A per-file process lock also deduplicates different operation
                # keys that select the same actual FileSource concurrently.
                file_lock = self.workspaces._data(project_id, wid, "material-path-locks", digest(ref.path))
                with self._lock(file_lock):
                    version = self.workspaces._version(workspace, ref.path, ref.revision)
                    blob = self.workspaces._versions(workspace, ref.path) / f"{ref.revision:010d}" / "bytes"
                    raw = blob.read_bytes()
                    if version["sha256"] != ref.sha256 or sha(raw) != ref.sha256:
                        raise Conflict("Frozen local material byte integrity failed")
                    prefix = f"local-workspace:{wid}:{digest(ref.path)}:"
                    marker = prefix + f"r{ref.revision}:{ref.sha256}"
                    try:
                        exact, parent = self._lookup(project_id, marker, prefix)
                        if exact:
                            document = self.store.document(project_id, exact["id"], exact["revision"])
                            if document["sha256"] != ref.sha256 or sha(document["raw_bytes"]) != ref.sha256:
                                raise Conflict("Existing local document registration drifted")
                            row = {"id": exact["id"], "revision": exact["revision"], "sha256": exact["sha256"]}
                        else:
                            head = self.store.document(project_id, parent) if parent else None
                            row = self.store.save_document(project_id, Path(ref.path).name, raw, marker,
                                parent, head["current_revision"] if head else 0)
                        parsed = self.store.document_receipt(project_id, row["id"], row["revision"])
                        result = {"path": ref.path, "status": "SAVED", "file_source_ref": ref.model_dump(),
                            "document_ref": {"document_id": row["id"], "revision": row["revision"], "sha256": row["sha256"]},
                            "parse_receipt": parsed}
                        self.workspaces._write(slot, result); materials.append(result)
                    except (InvalidInput, Conflict, NotFound) as error:
                        result = {"path": ref.path, "status": "FAILED", "file_source_ref": ref.model_dump(),
                                  "error_type": type(error).__name__, "error": str(error)}
                        self.workspaces._write(operation / (digest(ref.path) + ".failure-" + __import__("uuid").uuid4().hex + ".json"), result)
                        materials.append(result)
            response = {"workspace_id": wid, "binding_sha256": workspace["binding_sha256"],
                        "status": "SAVED" if all(value["status"] == "SAVED" for value in materials) else "PARTIAL",
                        "materials": materials}
            if response["status"] == "SAVED": self.workspaces._write(completed, response)
            return {**response, "replayed": False, "source_checks": checks}
