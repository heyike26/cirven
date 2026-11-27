from __future__ import annotations
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import uuid
import zipfile

from pydantic import ValidationError
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.tasks import canonical, digest, now
from packages.extensions.schema import ExtensionManifest, fixture_manifest, safe_member
from packages.runner.supervisor import atomic_json
from packages.domain.paths import resolve_data_path

ROOT = Path(__file__).resolve().parents[2]


class ExtensionRegistry:
    """Local immutable candidates; only exact original fixture bytes may execute.

    Other native namespaces/options are retained as unqualified declarations.
    A claimed permission, README or existing path never grants execution rights.
    """
    def __init__(self, service, data_dir):
        self.service = service
        self.data_dir = data_dir.resolve()

    def import_snapshot(self, raw):
        if len(raw) > 1024 * 1024:
            raise InvalidInput("Extension archive exceeds 1MiB")
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                infos = archive.infolist()
                if not 1 <= len(infos) <= 20 or sum(i.file_size for i in infos) > 131072:
                    raise ValueError("Extension archive size or count exceeded")
                names = [i.filename for i in infos]
                if len({n.casefold() for n in names}) != len(names):
                    raise ValueError("Duplicate or Windows-aliased archive members")
                files = {}
                for item in infos:
                    file_type = stat.S_IFMT(item.external_attr >> 16)
                    if not safe_member(item.filename) or item.is_dir() or file_type not in {0, stat.S_IFREG}:
                        raise ValueError("Unsafe extension archive path or symlink")
                    if item.file_size > max(item.compress_size, 1) * 100:
                        raise ValueError("Extension archive compression ratio exceeded")
                    data = archive.read(item)
                    if len(data) != item.file_size:
                        raise ValueError("Extension archive member length changed")
                    files[item.filename] = data
                manifest = ExtensionManifest.model_validate_json(files["manifest.json"]).model_dump()
                if not safe_member(manifest["entrypoint"]) or manifest["entrypoint"] not in files:
                    raise ValueError("Extension entrypoint is missing")
                if len(set(manifest["permissions"])) != len(manifest["permissions"]):
                    raise ValueError("Duplicate extension permissions")
        except (ValueError, KeyError, zipfile.BadZipFile, RuntimeError, ValidationError) as error:
            raise InvalidInput("Invalid local extension archive: " + str(error).splitlines()[0]) from error
        hashes = {name: hashlib.sha256(data).hexdigest() for name, data in files.items()}
        snapshot_id = digest({"manifest": manifest, "files": hashes})
        directory = self.data_dir / "extensions" / snapshot_id
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT id FROM extension_snapshots WHERE provider=? AND version=?",
                                          (manifest["provider"], manifest["version"])).fetchone()
            if previous:
                if previous["id"] != snapshot_id:
                    raise Conflict("A published extension version cannot change bytes")
                return self.describe(snapshot_id)
            staging = self.data_dir / "extensions" / ("pending-" + str(uuid.uuid4()))
            staging.mkdir(parents=True, exist_ok=False)
            try:
                for name, data in files.items():
                    path = staging / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with path.open("xb") as stream:
                        stream.write(data)
                        stream.flush()
                        os.fsync(stream.fileno())
                if directory.exists():
                    raise Conflict("Unregistered extension snapshot already exists; inspect before recovery")
                staging.rename(directory)
                connection.execute("INSERT INTO extension_snapshots VALUES (?,?,?,?,?,?,?)",
                                   (snapshot_id, manifest["provider"], manifest["version"], canonical(manifest),
                                    canonical(hashes), str(directory), now()))
                connection.execute("INSERT INTO extension_actions(snapshot_id,action,created_at) VALUES (?,?,?)",
                                   (snapshot_id, canonical({"type": "import", "source_kind": manifest["source_kind"]}), now()))
            finally:
                if staging.exists():
                    shutil.rmtree(staging)
        return self.describe(snapshot_id)

    def _row(self, snapshot_id):
        with self.service.connect() as connection:
            row = connection.execute("SELECT * FROM extension_snapshots WHERE id=?", (snapshot_id,)).fetchone()
        if not row:
            raise NotFound("Extension snapshot does not exist")
        return dict(row)

    def _intact(self, row):
        try:
            directory = resolve_data_path(row["directory"], self.data_dir)
            if directory.is_symlink() or not directory.resolve().is_relative_to(self.data_dir / "extensions"):
                return False
            expected = json.loads(row["files_hash"])
            paths = list(directory.rglob("*"))
            if any(p.is_symlink() for p in paths):
                return False
            actual = {str(p.relative_to(directory)).replace("\\", "/"): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in paths if p.is_file()}
            return actual == expected
        except (OSError, InvalidInput):
            return False

    def _supported(self, row):
        manifest = json.loads(row["manifest"])
        version = manifest["version"]
        if version not in {"1", "2"} or manifest != fixture_manifest(version):
            return False
        approved = ROOT / "packages/extensions/fixtures" / ("v" + version) / "plugin.py"
        try:
            return json.loads(row["files_hash"]).get("plugin.py") == hashlib.sha256(approved.read_bytes()).hexdigest()
        except OSError:
            return False

    def describe(self, snapshot_id):
        row = self._row(snapshot_id)
        intact, supported = self._intact(row), self._supported(row)
        with self.service.connect() as connection:
            proof = connection.execute("SELECT id,payload FROM extension_evidence WHERE snapshot_id=? ORDER BY created_at DESC LIMIT 1", (snapshot_id,)).fetchone()
            stable = connection.execute("SELECT snapshot_id FROM extension_stable WHERE provider=?", (row["provider"],)).fetchone()
        qualified = intact and supported and bool(proof)
        if qualified:
            try:
                payload = json.loads(proof["payload"])
                qualified = payload["result"] == "PASS" and all(
                    resolve_data_path(path, self.data_dir).is_file() and hashlib.sha256(resolve_data_path(path, self.data_dir).read_bytes()).hexdigest() == sha
                    for path, sha in payload["receipts_sha256"].items())
            except (KeyError, OSError, ValueError, InvalidInput):
                qualified = False
        return {"id": snapshot_id, "provider": row["provider"], "version": row["version"],
                "manifest": json.loads(row["manifest"]), "files_sha256": json.loads(row["files_hash"]),
                "supported": supported, "loaded": qualified, "qualified": qualified,
                "integrity": "CURRENT" if intact else "INTEGRITY_ERROR",
                "channel": "STABLE" if stable and stable["snapshot_id"] == snapshot_id else "CANDIDATE",
                "evidence_id": proof["id"] if proof else None, "runtime_kind": "none",
                "qualification_scope": "original fixture upgrade control only; SDK plugin live NOT_VALIDATED"}

    def list(self):
        with self.service.connect() as connection:
            ids = connection.execute("SELECT id FROM extension_snapshots ORDER BY created_at,id").fetchall()
        return [self.describe(row["id"]) for row in ids]

    def _execute(self, snapshot_id, mode, text):
        row = self._row(snapshot_id)
        if not self._intact(row) or not self._supported(row):
            raise InvalidInput("Unqualified or changed code cannot execute without an OS-qualified provider")
        manifest = json.loads(row["manifest"])
        directory = self.data_dir / "extension-journal" / str(uuid.uuid4())
        workspace, journal = directory / "workspace", directory / "journal"
        workspace.mkdir(parents=True, exist_ok=False)
        journal.mkdir()
        request = {"job_id": directory.name, "run_id": "extension:" + snapshot_id,
                   "argv": [sys.executable, "-I", str(ROOT / "packages/extensions/loader.py"),
                            str(resolve_data_path(row["directory"], self.data_dir) / manifest["entrypoint"]), mode, text],
                   "workspace": str(workspace), "stdout_path": str(workspace / "stdout.txt"),
                   "stderr_path": str(workspace / "stderr.txt"), "timeout_seconds": 5, "output_limit_bytes": 16384}
        atomic_json(journal / "request.json", request)
        env = {k: os.environ[k] for k in ("SystemRoot", "WINDIR", "TEMP", "TMP") if k in os.environ}
        process = subprocess.run([sys.executable, "-I", str(ROOT / "packages/runner/supervisor.py"), str(journal / "request.json")],
                                 env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10, check=False)
        receipt = json.loads((journal / "receipt.json").read_bytes())
        if process.returncode != 0 or receipt["phase"] != "FINISHED" or receipt["exit_code"] != 0 or receipt["error_type"]:
            raise InvalidInput("Original plugin execution failed; inspect preserved journal")
        result = json.loads((workspace / "stdout.txt").read_bytes())
        if result["file_hash"] != json.loads(row["files_hash"])[manifest["entrypoint"]] or not self._intact(row):
            raise InvalidInput("Plugin bytes changed during execution")
        hashes = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in directory.rglob("*") if p.is_file()}
        return result, {"request": request, "receipt": receipt, "receipts_sha256": hashes}

    def qualify(self, snapshot_id):
        row = self._row(snapshot_id)
        initialized, first = self._execute(snapshot_id, "inspect", "")
        manifest = json.loads(row["manifest"])
        expected = {"provider": manifest["provider"], "version": manifest["version"], "capabilities": manifest["capabilities"]}
        if initialized["result"] != expected:
            raise InvalidInput("Actual initialization differs from declared capabilities")
        called, second = self._execute(snapshot_id, "call", "original fixture α")
        expected_output = "v1|original fixture α" if row["version"] == "1" else {"format": "v2", "text": "original fixture α", "length": 18}
        if called["result"] != expected_output:
            raise InvalidInput("New capability or original text regression failed")
        evidence_id = str(uuid.uuid4())
        payload = {"evidence_id": evidence_id, "snapshot_id": snapshot_id, "result": "PASS",
                   "source_kind": "synthetic", "runtime_kind": "none", "environment": "Windows" if os.name == "nt" else "WSL2/Linux",
                   "test_ids": ["actual-module-initialization", "observable-format-and-original-text"],
                   "actual_initialization": initialized, "actual_call": called, "runs": [first, second],
                   "receipts_sha256": {**first["receipts_sha256"], **second["receipts_sha256"]},
                   "sdk_plugin_live": "NOT_VALIDATED", "sandbox": "NOT_VALIDATED"}
        with self.service.connect() as connection:
            connection.execute("INSERT INTO extension_evidence VALUES (?,?,?,?)", (evidence_id, snapshot_id, canonical(payload), now()))
        return self.describe(snapshot_id)

    def review(self, snapshot_id):
        snapshot = self.describe(snapshot_id)
        if not snapshot["qualified"]:
            raise Conflict("Only an intact, actually loaded and tested candidate can be reviewed")
        with self.service.connect() as connection:
            stable = connection.execute("SELECT snapshot_id FROM extension_stable WHERE provider=?", (snapshot["provider"],)).fetchone()
        old = self.describe(stable["snapshot_id"]) if stable else None
        permissions = snapshot["manifest"]["permissions"]
        action = {"provider": snapshot["provider"], "snapshot_id": snapshot_id,
                  "expected_stable": old["id"] if old else None, "permissions": permissions,
                  "new_permissions": sorted(set(permissions) - set(old["manifest"]["permissions"] if old else [])),
                  "files_sha256": snapshot["files_sha256"], "data_policy": "configuration pointer only; no DB migration or overwrite"}
        review_id = str(uuid.uuid4())
        expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()
        with self.service.connect() as connection:
            connection.execute("INSERT INTO extension_reviews VALUES (?,?,?,?,?,'PENDING',?,NULL)",
                               (review_id, snapshot_id, canonical(action), digest(action), expires, now()))
        return {"id": review_id, "action": action, "action_hash": digest(action), "expires_at": expires}

    def approve(self, review_id, action_hash, permissions):
        with self.service.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM extension_reviews WHERE id=?", (review_id,)).fetchone()
            if not row:
                raise NotFound("Extension review does not exist")
            action = json.loads(row["action"])
            if action_hash != row["action_hash"] or sorted(permissions) != sorted(action["permissions"]):
                raise Conflict("Approval must bind the exact snapshot and complete permission list")
            if row["status"] == "CONSUMED":
                return {"applied": True, "replayed": False, "snapshot_id": row["snapshot_id"]}
            if row["expires_at"] <= now() or row["status"] != "PENDING":
                raise Conflict("Extension review is expired or no longer pending")
            candidate = self.describe(row["snapshot_id"])
            current = connection.execute("SELECT snapshot_id FROM extension_stable WHERE provider=?", (action["provider"],)).fetchone()
            if not candidate["qualified"] or (current[0] if current else None) != action["expected_stable"]:
                raise Conflict("Candidate integrity, qualification or stable pointer changed")
            connection.execute("INSERT INTO extension_stable VALUES (?,?) ON CONFLICT(provider) DO UPDATE SET snapshot_id=excluded.snapshot_id",
                               (action["provider"], row["snapshot_id"]))
            connection.execute("UPDATE extension_reviews SET status='CONSUMED',decided_at=? WHERE id=?", (now(), review_id))
            connection.execute("INSERT INTO extension_actions(snapshot_id,action,created_at) VALUES (?,?,?)",
                               (row["snapshot_id"], canonical({"type": "activate_or_rollback", "review_id": review_id, **action}), now()))
        return {"applied": True, "replayed": False, "snapshot_id": row["snapshot_id"]}

    def pin(self, project_id, snapshot_id):
        self.service.project(project_id)
        snapshot = self.describe(snapshot_id)
        if not snapshot["qualified"] or snapshot["channel"] != "STABLE":
            raise Conflict("Project binding requires an approved stable snapshot")
        payload = {"schema_version": "eeas.extension-lock.v1", "provider": snapshot["provider"],
                   "snapshot_id": snapshot_id, "version": snapshot["version"], "manifest": snapshot["manifest"],
                   "files_sha256": snapshot["files_sha256"], "sdk_plugin_live": "NOT_VALIDATED"}
        binding_id = str(uuid.uuid4())
        with self.service.connect() as connection:
            connection.execute("INSERT INTO extension_bindings VALUES (?,?,?,?,?,?)",
                               (binding_id, project_id, snapshot_id, canonical(payload), digest(payload), now()))
            connection.execute("INSERT INTO project_extension_binding VALUES (?,?) ON CONFLICT(project_id) DO UPDATE SET binding_id=excluded.binding_id", (project_id, binding_id))
        return {"id": binding_id, "lock": payload, "lock_hash": digest(payload)}

    def binding(self, project_id):
        self.service.project(project_id)
        with self.service.connect() as connection:
            row = connection.execute("SELECT b.* FROM extension_bindings b JOIN project_extension_binding p ON p.binding_id=b.id WHERE p.project_id=?", (project_id,)).fetchone()
        return {"id": row["id"], "lock": json.loads(row["lock_payload"]), "lock_hash": row["lock_hash"]} if row else None

    def invoke(self, snapshot_id, text):
        if not isinstance(text, str) or len(text) > 4000:
            raise InvalidInput("Formatter input must be a string of at most 4000 characters")
        if not self.describe(snapshot_id)["qualified"]:
            raise Conflict("Plugin is not currently qualified")
        with self.service.connect() as connection:
            approved = connection.execute("SELECT 1 FROM extension_reviews WHERE snapshot_id=? AND status='CONSUMED'", (snapshot_id,)).fetchone()
        if not approved:
            raise Conflict("Plugin permissions have not been explicitly approved")
        called, receipt = self._execute(snapshot_id, "call", text)
        return {"snapshot_id": snapshot_id, "result": called["result"], "receipt": receipt,
                "runtime_kind": "none", "sdk_plugin_live": "NOT_VALIDATED"}
