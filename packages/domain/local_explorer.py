"""Lazy, immutable selected-directory observations without source copies or VCS.

The first successful observation of each directory is its comparison baseline.
Directory entry presence is not a recursive content comparison. Native listing
and exact file reads reuse the existing Windows/POSIX workspace handle authority.
"""
import os
import re
import uuid

from packages.domain.local_materials import LocalMaterials
from packages.domain.local_workspaces import MAX_FILE_BYTES, canonical, digest, now, relative, sha
from packages.domain.store import Conflict, InvalidInput, NotFound


MAX_HASH_BYTES = 32 * 1024 * 1024
MAX_RECORD_BYTES = 4 * 1024 * 1024 - 256


class LocalExplorer:
    def __init__(self, workspaces, *, max_hash_bytes=MAX_HASH_BYTES):
        if type(max_hash_bytes) is not int or not 0 <= max_hash_bytes <= MAX_HASH_BYTES:
            raise InvalidInput("Explorer hash budget is invalid")
        self.ws = workspaces
        self.max_hash_bytes = max_hash_bytes

    def _path(self, binding, directory, *parts):
        return self.ws._data(binding["project_id"], binding["id"], "explorer", digest(directory), *parts)

    @staticmethod
    def _ref(payload):
        hashed = digest(payload)
        return {"id": hashed, "revision": payload["revision"], "sha256": hashed}

    def _put(self, path, payload):
        if len(canonical(payload).encode()) > MAX_RECORD_BYTES:
            raise InvalidInput("Explorer directory observation exceeds the 4 MiB record bound; no entries were silently omitted")
        self.ws._write(path, {"payload": payload, "sha256": digest(payload)})

    def _load(self, path, binding, directory, *, ident=None):
        wrapper = self.ws._json(path)
        value = wrapper.get("payload")
        try:
            if not isinstance(value, dict) or digest(value) != wrapper.get("sha256"):
                raise ValueError
            if ident is not None and digest(value) != ident:
                raise ValueError
            if type(value["revision"]) is not int or value["revision"] < 1:
                raise ValueError
            if path.name == "head.json":
                ref = value["observation_ref"]
                if (set(ref) != {"id", "revision", "sha256"} or ref["id"] != ref["sha256"] or
                        not re.fullmatch(r"[0-9a-f]{64}", ref["id"]) or ref["revision"] != value["revision"]):
                    raise ValueError
            else:
                entries = value["entries"]
                if not isinstance(entries, list) or len(entries) > 10000:
                    raise ValueError
                names = set()
                for row in entries:
                    relative(row["path"])
                    if (row["path"] != "/".join(filter(None, (directory, row["name"]))) or row["path"] in names or
                            row["type"] not in {"directory", "file", "symlink", "unsupported"} or
                            type(row["readable"]) is not bool or type(row["byte_length"]) is not int or row["byte_length"] < 0):
                        raise ValueError
                    names.add(row["path"])
                    if row.get("sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", row["sha256"]):
                        raise ValueError
                expected_schema = "eeas.local-explorer-baseline.v1" if path.name == "baseline.json" else "eeas.local-explorer-observation.v1"
                if value["schema_version"] != expected_schema or value["scope"] != "LAZY_DIRECTORY_ONLY":
                    raise ValueError
        except (ValueError, TypeError, KeyError, InvalidInput):
            raise Conflict("Explorer observation integrity or structure failed") from None
        if (value.get("project_id"), value.get("workspace_id"), value.get("binding_sha256"), value.get("path")) != (
                binding["project_id"], binding["id"], binding["binding_sha256"], directory):
            raise Conflict("Explorer observation belongs to another directory/workspace/project scope")
        return value

    def _head(self, binding, directory):
        path = self._path(binding, directory, "head.json")
        if not path.exists():
            return None
        head = self._load(path, binding, directory)
        ref = head["observation_ref"]
        value = self._load(self._path(binding, directory, "observations", ref["id"] + ".json"),
                           binding, directory, ident=ref["id"])
        if self._ref(value) != ref or head["revision"] != value["revision"]:
            raise Conflict("Explorer head reference integrity failed")
        return value

    def _replace_head(self, binding, directory, value):
        head = {"project_id": binding["project_id"], "workspace_id": binding["id"],
                "binding_sha256": binding["binding_sha256"], "path": directory,
                "observation_ref": self._ref(value), "revision": value["revision"]}
        temporary = self._path(binding, directory, ".pending-head-" + uuid.uuid4().hex)
        self._put(temporary, head)
        os.replace(temporary, self._path(binding, directory, "head.json"))

    def _scan(self, binding, directory, previous):
        pid, wid = binding["project_id"], binding["id"]
        try:
            tree = self.ws.tree(pid, wid, directory, 0, 10000)
        except NotFound:
            if previous is None:
                raise
            # Only a genuine missing path produces tombstones. Denied, replaced,
            # symlinked and otherwise unsafe directories propagate their errors.
            return [], True, 0
        consumed = 0
        rows = []
        for entry in tree["entries"]:
            row = {**entry, "sha256": None, "verification": "UNVERIFIED"}
            if entry["type"] == "directory" and entry["readable"]:
                row["verification"] = "DIRECTORY_ENTRY_ONLY"
                row["reason"] = "Children are compared only when this directory is observed"
            elif entry["type"] != "file" or not entry["readable"]:
                row["reason"] = "Links, special files and unreadable entries are not followed"
            elif entry["byte_length"] > MAX_FILE_BYTES:
                row["reason"] = "FILE_READ_LIMIT_EXCEEDED"
            elif consumed + entry["byte_length"] > self.max_hash_bytes:
                row["reason"] = "DIRECTORY_HASH_BUDGET_EXHAUSTED"
            else:
                try:
                    raw = self.ws._raw(binding, entry["path"])
                    if consumed + len(raw) > self.max_hash_bytes:
                        row["reason"] = "DIRECTORY_HASH_BUDGET_EXHAUSTED"
                    else:
                        consumed += len(raw)
                        row.update(sha256=sha(raw), byte_length=len(raw), verification="CONTENT_SHA256")
                except (Conflict, InvalidInput, NotFound, OSError) as error:
                    row["reason"] = type(error).__name__ + ": " + str(error)
            rows.append(row)
        # A changed namespace cannot quietly create a mixed pagination snapshot.
        after = self.ws.tree(pid, wid, directory, 0, 10000)
        signature = lambda values: [(r["path"], r["type"], r["readable"], r["byte_length"]) for r in values]
        if signature(tree["entries"]) != signature(after["entries"]):
            raise Conflict("EXPLORER_DIRECTORY_DRIFT: directory changed while being observed; refresh again")
        return rows, False, consumed

    def observe(self, pid, wid, directory="", offset=0, limit=100, *, observation_id=None, expected_observation_id=None):
        relative(directory, empty=True)
        if type(offset) is not int or offset < 0 or type(limit) is not int or not 1 <= limit <= 200:
            raise InvalidInput("Explorer pagination requires bounded integer offset/limit")
        for ident in (observation_id, expected_observation_id):
            if ident is not None and (not isinstance(ident, str) or not re.fullmatch(r"[0-9a-f]{64}", ident)):
                raise InvalidInput("Explorer observation identity must be an exact SHA256")
        if observation_id and expected_observation_id:
            raise InvalidInput("Use snapshot pagination or a compare-and-refresh expectation")
        if offset and not observation_id:
            raise Conflict("EXPLORER_SNAPSHOT_REQUIRED: paginate with the first page's observation_id")
        binding = self.ws.get(pid, wid)
        if observation_id:
            path = self._path(binding, directory, "observations", observation_id + ".json")
            if not path.exists():
                raise NotFound("Explorer observation is outside this directory/project/workspace")
            value = self._load(path, binding, directory, ident=observation_id)
            baseline = self._load(self._path(binding, directory, "baseline.json"), binding, directory)
            if value["baseline_ref"] != self._ref(baseline):
                raise Conflict("Explorer snapshot baseline reference changed")
        else:
            with LocalMaterials(self.ws)._lock(self._path(binding, directory, "lock")):
                previous = self._head(binding, directory)
                if expected_observation_id is not None and (previous is None or self._ref(previous)["id"] != expected_observation_id):
                    raise Conflict("EXPLORER_CAS_CONFLICT: another refresh changed the observation")
                rows, missing, consumed = self._scan(binding, directory, previous)
                current_binding = self.ws.get(pid, wid)
                if current_binding["binding_sha256"] != binding["binding_sha256"]:
                    raise Conflict("Explorer workspace binding changed during observation")
                base_path = self._path(binding, directory, "baseline.json")
                if base_path.exists():
                    baseline = self._load(base_path, binding, directory)
                    if previous is not None and previous["baseline_ref"] != self._ref(baseline):
                        raise Conflict("Explorer immutable baseline changed after prior observation")
                else:
                    baseline = {"schema_version": "eeas.local-explorer-baseline.v1", "project_id": pid,
                        "workspace_id": wid, "binding_sha256": binding["binding_sha256"], "path": directory,
                        "revision": 1, "observed_at": now(), "scope": "LAZY_DIRECTORY_ONLY", "entries": rows}
                    self._put(base_path, baseline)
                bases = {entry["path"]: entry for entry in baseline["entries"]}
                seen = {entry["path"]: entry for entry in previous["entries"]} if previous else {}
                entries = []
                for row in rows:
                    base = bases.get(row["path"])
                    if row["verification"] == "UNVERIFIED":
                        change = "UNVERIFIED"
                    elif base is None:
                        change = "ADDED"
                    elif base["type"] != row["type"]:
                        change = "MODIFIED"
                    elif row["type"] == "directory":
                        change = "UNCHANGED"
                    elif base["verification"] != "CONTENT_SHA256":
                        change = "UNVERIFIED"
                        row["reason"] = "BASELINE_CONTENT_NOT_VERIFIED"
                    else:
                        change = "UNCHANGED" if row["sha256"] == base["sha256"] else "MODIFIED"
                    entries.append({**row, "change": change, "deleted": False,
                        "restored": bool(seen.get(row["path"], {}).get("deleted")),
                        "comparison_basis": "FIRST_DIRECTORY_OBSERVATION_ENTRY_TYPE" if row["type"] == "directory" else "FIRST_DIRECTORY_OBSERVATION_BYTES"})
                present = {row["path"] for row in rows}
                for name, row in {**bases, **seen}.items():
                    if name not in present:
                        entries.append({**row, "change": "DELETED", "deleted": True, "restored": False,
                            "readable": False, "verification": "CONFIRMED_MISSING_DIRECTORY_ENTRY",
                            "comparison_basis": "PREVIOUSLY_OBSERVED_DIRECTORY_ENTRY"})
                entries.sort(key=lambda row: (row["type"] != "directory", row["name"]))
                value = {"schema_version": "eeas.local-explorer-observation.v1", "project_id": pid,
                    "workspace_id": wid, "binding_sha256": binding["binding_sha256"], "path": directory,
                    "revision": previous["revision"] + 1 if previous else 1, "observed_at": now(),
                    "scope": "LAZY_DIRECTORY_ONLY", "baseline_ref": self._ref(baseline), "entries": entries,
                    "directory_missing": missing, "hash_bytes": consumed, "hash_budget_bytes": self.max_hash_bytes,
                    "atomic_filesystem_snapshot": False}
                self._put(self._path(binding, directory, "observations", self._ref(value)["id"] + ".json"), value)
                self._replace_head(binding, directory, value)
        page = value["entries"][offset:offset + limit]
        return {key: value[key] for key in ("path", "observed_at", "scope", "baseline_ref", "directory_missing",
            "hash_bytes", "hash_budget_bytes", "atomic_filesystem_snapshot")} | {"entries": page,
            "total": len(value["entries"]), "next_offset": offset + limit if offset + limit < len(value["entries"]) else None,
            "observation_ref": self._ref(value)}
