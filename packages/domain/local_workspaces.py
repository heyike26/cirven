"""Selected real folders, exact FileSources and durable compare-and-edit.

No Agent loop, full-folder copy, VCS command or background watcher is created.
Windows dispatch uses the native handle adapter; real Windows qualification is
separate from the portable ABI/domain tests.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
import base64
import difflib
import hashlib
import json
from functools import lru_cache
import ntpath
import os
from pathlib import Path, PurePosixPath
import re
import stat
import uuid

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.context_read_budget import source_bytes, current_context_read_budget

MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_EDIT_BYTES = 1024 * 1024
PRIVATE = {".git", ".eeas-sdk-state", ".eeas-runtime"}


@lru_cache(maxsize=1)
def _windows_handles():
    from packages.adapters.windows_workspace_handles import WindowsWorkspaceHandles
    return WindowsWorkspaceHandles()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(canonical(value).encode("utf-8"))


def now():
    return datetime.now(timezone.utc).isoformat()


def relative(value, *, empty=False):
    if value == "" and empty:
        return value
    if (not isinstance(value, str) or not value or len(value) > 1024 or
            any(ord(c) < 32 or ord(c) == 127 or c in "\\:" for c in value) or
            value.startswith("/") or str(PurePosixPath(value)) != value or
            any(part in {".", ".."} or part.casefold() in PRIVATE or part.startswith(".eeas-write-")
                for part in value.split("/"))):
        raise InvalidInput("File path must be a normalized relative path inside the selected folder")
    return value


class WorkspaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    idempotency_key: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    root: str = Field(min_length=1, max_length=4096)
    allowed_write_paths: list[str] = Field(default_factory=list, max_length=128)

    @field_validator("allowed_write_paths")
    @classmethod
    def scope(cls, values):
        if len(set(values)) != len(values):
            raise ValueError("Write scopes must be unique")
        for value in values:
            if value != "*":
                relative(value[:-1] if value.endswith("/") else value)
        return sorted(values)


class FileEditInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    path: str = Field(min_length=1, max_length=1024)
    expected_revision: int = Field(ge=0, strict=True)
    expected_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    content: str = Field(max_length=MAX_EDIT_BYTES)

    @model_validator(mode="after")
    def matching_expectation(self):
        relative(self.path)
        if (self.expected_revision == 0) != (self.expected_sha256 is None):
            raise ValueError("Existing-file edits need revision/SHA; new files need revision zero and no SHA")
        if "\x00" in self.content or len(self.content.encode("utf-8")) > MAX_EDIT_BYTES:
            raise ValueError("Edits support UTF-8 text without NUL within 1 MiB")
        return self


class FileSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: str = Field(pattern=r"^local_file$")
    project_id: str
    workspace_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    binding_revision: int = Field(ge=1, strict=True)
    binding_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    path: str
    revision: int = Field(ge=1, strict=True)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class LocalWorkspaces:
    def __init__(self, store, data_dir):
        self.store, self.data_dir = store, Path(data_dir).absolute()
        if self.data_dir.is_symlink():
            raise InvalidInput("Workspace registry root must not be a symlink")

    @staticmethod
    def _native_handles():
        return _windows_handles() if os.name == "nt" else None

    def _data(self, project_id, workspace_id=None, *parts):
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", project_id):
            raise InvalidInput("Unsafe project identity")
        if workspace_id is not None and not re.fullmatch(r"[0-9a-f]{64}", workspace_id):
            raise InvalidInput("Workspace identity must be an exact lowercase SHA256")
        return resolve_data_path("/".join(("local-workspaces", project_id,
            *((workspace_id,) if workspace_id is not None else ()), *parts)), self.data_dir)

    @staticmethod
    def _write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("x", encoding="utf-8") as stream:
            stream.write(canonical(value) + "\n"); stream.flush(); os.fsync(stream.fileno())

    @staticmethod
    def _json(path):
        if not path.is_file() or path.stat().st_size > 4 * 1024 * 1024:
            raise Conflict("Workspace record is missing or outside its size bound")
        try:
            value = json.loads(source_bytes(path, 4 * 1024 * 1024))
            if not isinstance(value, dict): raise ValueError
            return value
        except (OSError, ValueError):
            raise Conflict("Workspace record integrity cannot be established") from None

    @staticmethod
    def _read_limit(stream,max_bytes):
        """Fill short raw reads without receiving more than cap+one sentinel."""
        if type(max_bytes)is not int or not 0<=max_bytes<=MAX_FILE_BYTES:
            raise InvalidInput('Private read limit cannot exceed the existing authority cap')
        chunks=[];received=0
        while received<=max_bytes:
            part=stream.read(min(1024*1024,max_bytes+1-received))
            if not part:break
            chunks.append(part);received+=len(part)
        return b''.join(chunks)

    @staticmethod
    def _bounded_json(path, *, max_bytes=4*1024*1024, _read_receipt=None):
        """Explicit small-cap/private proof path; legacy _json stays unchanged."""
        if type(max_bytes)is not int or not 0<=max_bytes<=4*1024*1024:
            raise InvalidInput('Metadata limit must remain within the existing 4 MiB cap')
        try:
            if not path.is_file() or path.stat().st_size>max_bytes:raise ValueError
            with path.open('rb',buffering=0)as stream:
                before=os.fstat(stream.fileno());raw=LocalWorkspaces._read_limit(stream,max_bytes);after=os.fstat(stream.fileno())
            if _read_receipt is not None:_read_receipt['metadata_bytes_read']+=len(raw)
            if len(raw)>max_bytes or (before.st_size,before.st_mtime_ns,before.st_ctime_ns)!=(after.st_size,after.st_mtime_ns,after.st_ctime_ns):raise ValueError
            value=json.loads(raw)
            if not isinstance(value,dict):raise ValueError
            return value
        except (OSError,ValueError):raise Conflict('Bounded workspace metadata integrity cannot be established')from None

    @staticmethod
    def _root(value):
        if os.name == "nt":
            root, identity = _windows_handles().root_identity(value)
            return Path(root), identity
        root = Path(value)
        if not root.is_absolute() or ".." in root.parts or any(ord(c) < 32 for c in value):
            raise InvalidInput("Choose an existing absolute local folder")
        if any(path.is_symlink() for path in (root, *root.parents)):
            raise InvalidInput("Selected folder path must not contain symlinks")
        if not root.is_dir(): raise NotFound("Selected local folder does not exist")
        root = root.resolve(); info = root.stat()
        return root, {"device": info.st_dev, "inode": info.st_ino}

    def bind(self, project_id, body: WorkspaceInput):
        self.store.project(project_id)
        root, identity = self._root(body.root)
        if root == self.data_dir.resolve() or root.is_relative_to(self.data_dir.resolve()):
            raise InvalidInput("Controller-owned data cannot be selected as a workspace")
        wid = digest({"project_id": project_id, "key": body.idempotency_key})
        request = {"root": str(root), "allowed_write_paths": body.allowed_write_paths}
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            path = self._data(project_id, wid, "binding.json")
            if path.exists():
                saved = self.get(project_id, wid)
                if saved["request_sha256"] != digest(request):
                    raise Conflict("IDEMPOTENCY_CONFLICT: workspace key already binds another folder or scope")
                return saved
            binding = {"schema_version": "eeas.local-workspace-binding.v1", "id": wid,
                "project_id": project_id, "revision": 1, **request, "root_identity": identity,
                "request_sha256": digest(request), "created_at": now(),
                "native_location": {"directory": str(root)}, "os_sandbox": "NOT_QUALIFIED"}
            self._write(path, {"binding": binding, "binding_sha256": digest(binding)})
            return {**binding, "binding_sha256": digest(binding)}

    def binding_record(self, project_id, workspace_id, revision=None):
        """Verified saved metadata only; conveys no current filesystem authority."""
        self.store.project(project_id)
        path = self._data(project_id, workspace_id, "binding.json")
        if not path.exists(): raise NotFound("Workspace does not exist in this project")
        record = self._json(path); binding = record.get("binding")
        if (not isinstance(binding, dict) or digest(binding) != record.get("binding_sha256") or
                binding.get("project_id") != project_id or binding.get("id") != workspace_id or
                binding.get("revision") != 1 or (revision is not None and revision != 1)):
            raise Conflict("Workspace binding revision or integrity does not match")
        return {**binding, "binding_sha256": record["binding_sha256"]}

    def get(self, project_id, workspace_id, revision=None):
        binding = self.binding_record(project_id, workspace_id, revision)
        root, identity = self._root(binding["root"])
        if identity != binding["root_identity"] or str(root) != binding["root"]:
            raise Conflict("WORKSPACE_ROOT_DRIFT: selected folder was replaced; bind the new folder explicitly")
        return binding

    def list(self, project_id):
        self.store.project(project_id); root = self._data(project_id)
        return [self.get(project_id, path.name) for path in sorted(root.iterdir())
                if re.fullmatch(r"[0-9a-f]{64}", path.name)] if root.exists() else []

    def _private(self, binding, path):
        if os.name == "nt":
            if any(part.casefold().startswith(".eeas-write-") for part in path.split("/")):
                raise InvalidInput("Private workspace write staging paths are outside the grant")
            candidate = ntpath.normcase(ntpath.join(binding["root"], *path.split("/")))
            data = ntpath.normcase(str(self.data_dir))
            try: inside = ntpath.commonpath((candidate, data)) == data
            except ValueError: inside = False  # Different selected local volume.
            if inside: raise InvalidInput("Controller-owned state is outside the workspace grant")
            return
        candidate = Path(binding["root"]) / path
        if candidate == self.data_dir or candidate.is_relative_to(self.data_dir):
            raise InvalidInput("Controller-owned state is outside the workspace grant")

    @contextmanager
    def _parent(self, binding, path, *, directory=False):
        relative(path, empty=directory); self._private(binding, path)
        native = self._native_handles()
        if native is not None:
            with native.open_parent(binding, path, directory=directory, private_check=self._private) as value:
                yield value
            return
        if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
            raise InvalidInput("Secure folder handles are not yet qualified on this platform")
        root = Path(binding["root"])
        descriptors = []
        try:
            fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW); descriptors.append(fd)
            info = os.fstat(fd)
            if {"device": info.st_dev, "inode": info.st_ino} != binding["root_identity"]:
                raise Conflict("WORKSPACE_ROOT_DRIFT: selected folder identity changed")
            parts = path.split("/") if path else []
            parents = parts if directory else parts[:-1]
            for part in parents:
                fd = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                descriptors.append(fd)
            yield fd, None if directory else parts[-1]
        except FileNotFoundError:
            raise NotFound("Local file or directory does not exist") from None
        except OSError:
            raise InvalidInput("Local path is not a permitted regular path; links and escapes are rejected") from None
        finally:
            for fd in reversed(descriptors): os.close(fd)

    def _raw(self, binding, path, *, missing=False, max_bytes=MAX_FILE_BYTES):
        if type(max_bytes) is not int or not 0 <= max_bytes <= MAX_FILE_BYTES:
            raise InvalidInput('Read byte limit must be an integer within the existing 16 MiB authority cap')
        native = self._native_handles()
        if native is not None:
            relative(path); self._private(binding, path)
            options={'max_bytes':max_bytes} if max_bytes!=MAX_FILE_BYTES else {}
            return native.read_regular(binding, path, missing=missing, private_check=self._private,**options)
        with self._parent(binding, path) as (parent, name):
            try: fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            except FileNotFoundError:
                if missing: return None
                raise NotFound("Local file does not exist") from None
            try:
                before = os.fstat(fd)
                if not stat.S_ISREG(before.st_mode): raise InvalidInput("Read requires a regular file")
                if before.st_size > max_bytes:
                    raise InvalidInput("Local read exceeds 16 MiB; no bytes were truncated" if max_bytes==MAX_FILE_BYTES else 'Local read exceeds the selected smaller byte limit')
                chunks = []; count = 0
                while True:
                    raw = os.read(fd, min(1024 * 1024, max_bytes + 1 - count))
                    if not raw: break
                    chunks.append(raw); count += len(raw)
                    if count > max_bytes: raise InvalidInput("Local file grew beyond the read limit")
                after = os.fstat(fd)
                if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                    raise Conflict("SOURCE_DRIFT: file changed while its bytes were being read")
                return b"".join(chunks)
            finally: os.close(fd)

    def _saved_raw(self, binding, path, *, missing=False):
        """Explicit nonwriting saved-byte observation, not a write preimage.

        The native adapter retains ordinary pinned parents, checks the live
        leaf after reading and rejects drift. Sharing never grants write access
        or observes application memory. Strict _raw remains the mutation path.
        """
        relative(path); self._private(binding, path)
        native = self._native_handles()
        allowance = current_context_read_budget()
        def read_saved(maximum):
            if native is not None:
                return native.read_regular(binding, path, missing=missing, max_bytes=maximum,
                    private_check=self._private, shared_saved_document=True)
            return self._raw(binding, path, missing=missing, max_bytes=maximum)
        return allowance.guarded(read_saved, MAX_FILE_BYTES) if allowance is not None else read_saved(MAX_FILE_BYTES)

    def _folder_payload(self, binding, parent):
        native = self._native_handles()
        if native is not None:
            info = native.api.info(parent.native_handle)
            actual = native.api.final_path(parent.native_handle)
            local = ntpath.relpath(actual, binding['root']).replace('\\', '/')
            parts = [] if local == '.' else local.split('/')
            identity = {'device': info['device'], 'inode': info['inode']}
        else:
            info = os.fstat(parent)
            # POSIX callers already traversed these exact, non-link components.
            parts = None
            identity = {'device': info.st_dev, 'inode': info.st_ino}
        return parts, identity

    def folder_reference(self, project_id, wid, path="", *, binding_revision, binding_sha256):
        """Pin an existing directory; no enumeration or new source registration."""
        relative(path, empty=True)
        binding = self.get(project_id, wid, binding_revision)
        if binding['binding_sha256'] != binding_sha256:
            raise Conflict('Folder workspace binding changed')
        with self._parent(binding, path, directory=True) as (parent, _):
            parts, identity = self._folder_payload(binding, parent)
            return {'project_id': project_id, 'workspace_id': wid,
                'binding_revision': binding_revision, 'binding_sha256': binding_sha256,
                'segments': parts if parts is not None else path.split('/') if path else [], **identity}

    def file_slot_location(self, project_id, wid, path, *, binding_revision, binding_sha256):
        """Pin a filename in its existing parent even when the leaf is absent.

        This conveys no file content, write grant or permission to create a
        missing parent. Original workspace/root and private-path rules apply.
        """
        relative(path)
        binding = self.get(project_id, wid, binding_revision)
        if binding['binding_sha256'] != binding_sha256:
            raise Conflict('File slot workspace binding changed')
        with self._parent(binding, path) as (parent, leaf):
            parts, identity = self._folder_payload(binding, parent)
            if parts is None: parts = path.split('/')[:-1]
            folder = {'project_id': project_id, 'workspace_id': wid,
                'binding_revision': binding_revision, 'binding_sha256': binding_sha256,
                'segments': parts, **identity}
            return {'kind': 'WORKSPACE_FILE_SLOT', 'project_id': project_id,
                'parent_ref': folder, 'name': leaf, 'path': '/'.join([*parts, leaf])}

    def file_location(self, project_id, wid, path, *, binding_revision, binding_sha256):
        """Logical filename in a pinned parent, stable across ordinary atomic
        saves. This is separate from byte revisions and native window identity.
        """
        relative(path)
        binding = self.get(project_id, wid, binding_revision)
        if binding['binding_sha256'] != binding_sha256:
            raise Conflict('File location workspace binding changed')
        with self._parent(binding, path) as (parent, leaf):
            parts, identity = self._folder_payload(binding, parent)
            native = self._native_handles()
            if native is not None:
                # Attribute-only, shared handle preserves Word/editor ownership
                # and resolves the actual stored spelling on NTFS directories.
                handle = native.api.open_relative(parent.native_handle, leaf, 0x80, 7, directory=False)
                try:
                    native._check(handle, ntpath.join(parent.path, leaf), directory=False)
                    name = ntpath.basename(native.api.final_path(handle))
                finally:
                    native.api.close(handle)
            else:
                info = os.stat(leaf, dir_fd=parent, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise InvalidInput('File location is not an ordinary file')
                name = leaf
            if parts is None:
                parts = path.split('/')[:-1]
            folder = {'project_id': project_id, 'workspace_id': wid,
                'binding_revision': binding_revision, 'binding_sha256': binding_sha256,
                'segments': parts, **identity}
            key = digest({'project_id': project_id, 'workspace_id': wid,
                'binding_revision': binding_revision, 'binding_sha256': binding_sha256,
                'parent_identity': identity, 'name': name})
            return {'kind': 'WORKSPACE_FILE', 'file_key': key, 'project_id': project_id,
                'parent_ref': folder, 'name': name, 'path': '/'.join([*parts, name])}

    def tree(self, project_id, wid, path="", offset=0, limit=100):
        binding = self.get(project_id, wid); rows = []
        native = self._native_handles()
        if native is not None:
            relative(path, empty=True); self._private(binding, path)
            rows = native.list_directory(binding, path, private_check=self._private)
        else:
            with self._parent(binding, path, directory=True) as (fd, _):
                with os.scandir(fd) as entries:
                    for entry in entries:
                        child = "/".join(filter(None, (path, entry.name)))
                        try: relative(child); self._private(binding, child)
                        except InvalidInput: continue
                        info = entry.stat(follow_symlinks=False)
                        kind = "directory" if stat.S_ISDIR(info.st_mode) else "file" if stat.S_ISREG(info.st_mode) else "symlink" if stat.S_ISLNK(info.st_mode) else "unsupported"
                        rows.append({"name": entry.name, "path": child, "type": kind,
                                     "readable": kind in {"directory", "file"}, "byte_length": info.st_size})
                        if len(rows) > 10000: raise InvalidInput("Directory exceeds the bounded 10000-entry listing")
        rows.sort(key=lambda value: (value["type"] != "directory", value["name"]))
        return {"entries": rows[offset:offset + limit], "total": len(rows),
                "next_offset": offset + limit if offset + limit < len(rows) else None}

    def _versions(self, binding, path):
        return self._data(binding["project_id"], binding["id"], "files", digest(path), "versions")

    def _version(self, binding, path, revision=None,*,max_bytes=MAX_FILE_BYTES,_read_receipt=None,_metadata_max_bytes=4*1024*1024,
            _exact_revision=False,_with_bytes=False):
        if type(max_bytes)is not int or not 0<=max_bytes<=MAX_FILE_BYTES:
            raise InvalidInput('Read byte limit must remain within the existing authority cap')
        directory = self._versions(binding, path)
        if _exact_revision:
            if type(revision)is not int or revision<1:raise InvalidInput('Exact saved lookup requires an explicit revision')
            names=[f'{revision:010d}']
        elif (max_bytes!=MAX_FILE_BYTES or _read_receipt is not None) and directory.exists():
            names=[]
            for count,entry in enumerate(directory.iterdir(),1):
                if count>200:raise InvalidInput('Bounded source-head lookup exceeds 200 metadata entries')
                if re.fullmatch(r'[0-9]{10}',entry.name):names.append(entry.name)
            names.sort()
        else:names = sorted(entry.name for entry in directory.iterdir() if re.fullmatch(r"[0-9]{10}", entry.name)) if directory.exists() else []
        if not names: return None
        name = names[-1] if revision is None else f"{revision:010d}"
        target = resolve_data_path(directory / name, self.data_dir)
        if not target.exists(): raise NotFound("Local file source revision does not exist")
        metadata=resolve_data_path(target / "metadata.json", self.data_dir)
        wrapper = self._bounded_json(metadata,max_bytes=_metadata_max_bytes,_read_receipt=_read_receipt)if max_bytes!=MAX_FILE_BYTES or _read_receipt is not None else self._json(metadata)
        value = wrapper.get("version")
        if (not isinstance(value, dict) or digest(value) != wrapper.get("sha256") or
                value.get("path") != path or value.get("workspace_id") != binding["id"] or value.get("revision") != int(name)):
            raise Conflict("Local source version integrity failed")
        blob = resolve_data_path(target / "bytes", self.data_dir)
        if not blob.is_file() or blob.stat().st_size > max_bytes:
            raise Conflict("Frozen local source bytes failed integrity checking")
        allowance = current_context_read_budget()
        if allowance is not None:
            raw = allowance.file(blob, max_bytes)
            stable = True  # The shared reader already checked identity and post-read metadata.
        else:
            with blob.open('rb',buffering=0)as stream:
                before=os.fstat(stream.fileno());raw=self._read_limit(stream,max_bytes);after=os.fstat(stream.fileno())
            stable = (before.st_size,before.st_mtime_ns,before.st_ctime_ns)==(after.st_size,after.st_mtime_ns,after.st_ctime_ns)
        if _read_receipt is not None:_read_receipt['head_bytes_read']+=len(raw)
        if (len(raw)>max_bytes or sha(raw)!=value['sha256'] or
                not stable):
            raise Conflict("Frozen local source bytes failed integrity checking")
        return (value,raw) if _with_bytes else value

    def _observe(self, binding, path, raw, *, origin="OBSERVED",max_bytes=MAX_FILE_BYTES,_read_receipt=None,_metadata_max_bytes=4*1024*1024):
        options={'max_bytes':max_bytes,'_read_receipt':_read_receipt,'_metadata_max_bytes':_metadata_max_bytes}if max_bytes!=MAX_FILE_BYTES or _read_receipt is not None else {}
        head = self._version(binding, path,**options); hashed = sha(raw)
        if head and head["sha256"] == hashed: return head
        revision = head["revision"] + 1 if head else 1
        value = {"schema_version": "eeas.local-file-source-version.v1", "project_id": binding["project_id"],
            "workspace_id": binding["id"], "binding_revision": binding["revision"],
            "binding_sha256": binding["binding_sha256"], "path": path, "revision": revision,
            "sha256": hashed, "byte_length": len(raw), "origin": origin, "observed_at": now()}
        directory = self._versions(binding, path); directory.mkdir(parents=True, exist_ok=True)
        staging = resolve_data_path(directory / (".pending-" + uuid.uuid4().hex), self.data_dir)
        staging.mkdir()
        with (staging / "bytes").open("xb") as stream:
            stream.write(raw); stream.flush(); os.fsync(stream.fileno())
        self._write(staging / "metadata.json", {"version": value, "sha256": digest(value)})
        staging.rename(directory / f"{revision:010d}")
        return value

    @staticmethod
    def _ref(value):
        return {"kind": "local_file", **{key: value[key] for key in
            ("project_id", "workspace_id", "binding_revision", "binding_sha256", "path", "revision", "sha256")}}

    @staticmethod
    def _content(raw):
        try:
            text = raw.decode("utf-8")
            if "\x00" in text: raise UnicodeError
            return {"encoding": "utf-8", "content": text}
        except UnicodeError:
            return {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii")}

    def read(self, project_id, wid, path, revision=None,*,max_bytes=MAX_FILE_BYTES,_metadata_max_bytes=4*1024*1024):
        relative(path)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = self.get(project_id, wid)
            live = self._raw(binding, path, missing=revision is not None,max_bytes=max_bytes)
            receipt={'live_bytes_read':len(live)if live is not None else 0,'head_bytes_read':0,'metadata_bytes_read':0}
            if revision is None:
                options={'max_bytes':max_bytes,'_read_receipt':receipt,'_metadata_max_bytes':_metadata_max_bytes}if max_bytes!=MAX_FILE_BYTES else {}
                version = self._observe(binding, path, live,**options)
                raw = live
            else:
                version = self._version(binding, path, revision)
                if version is None: raise NotFound("Local file has no saved source revision")
                raw = (self._versions(binding, path) / f"{revision:010d}" / "bytes").read_bytes()
            return {"source_ref": self._ref(version), "byte_length": len(raw), **self._content(raw),
                    "current": live is not None and sha(live) == version["sha256"],
                    "live_sha256": sha(live) if live is not None else None,
                    **({'read_receipt':receipt}if max_bytes!=MAX_FILE_BYTES and revision is None else {})}

    def read_saved_snapshot(self, project_id, wid, path, revision=None):
        """Read an explicitly selected saved file while its application is open.

        Two independently guarded reads must agree before source registration.
        This is a bounded saved-byte snapshot, not an atomic application/CAS
        guarantee; callers performing writes must revalidate through strict read.
        """
        relative(path)
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = self.get(project_id, wid)
            live = self._saved_raw(binding, path, missing=revision is not None)
            checked = self._saved_raw(binding, path, missing=revision is not None)
            if live != checked:
                raise Conflict("SOURCE_DRIFT: saved file changed between snapshot reads")
            if revision is None:
                version = self._observe(binding, path, live)
                raw = live
            else:
                version = self._version(binding, path, revision)
                if version is None: raise NotFound("Local file has no saved source revision")
                raw = (self._versions(binding, path) / f"{revision:010d}" / "bytes").read_bytes()
            return {"source_ref": self._ref(version), "byte_length": len(raw), **self._content(raw),
                    "current": live is not None and sha(live) == version["sha256"],
                    "live_sha256": sha(live) if live is not None else None}

    def history(self, project_id, wid, path):
        relative(path); binding = self.get(project_id, wid); directory = self._versions(binding, path)
        return [self._version(binding, path, int(entry.name)) for entry in sorted(directory.iterdir())
                if re.fullmatch(r"[0-9]{10}", entry.name)] if directory.exists() else []

    def search(self, project_id, wid, query, *, path="", limit=50):
        if not query or len(query) > 1000: raise InvalidInput("Search needs a literal query within 1000 characters")
        binding = self.get(project_id, wid); relative(path, empty=True); self._private(binding, path)
        with self._parent(binding, path, directory=True): pass
        hits, skipped = [], []; scanned = 0
        root = Path(binding["root"])
        # Traversal is only discovery; every content read independently uses
        # root-anchored no-follow handles, including discovered descendants.
        def discovered():
            native = self._native_handles()
            if native is not None:
                yield from native.walk_files(binding, path, private_check=self._private, max_files=2001, max_directories=2000)
                return
            for folder, dirs, files in os.walk(root / path, followlinks=False):
                dirs[:] = sorted(name for name in dirs if name.casefold() not in PRIVATE and
                    not (Path(folder) / name).is_symlink() and not (Path(folder) / name).is_relative_to(self.data_dir))
                for name in sorted(files): yield (Path(folder) / name).relative_to(root).as_posix()
        for selected in discovered():
            scanned += 1
            if scanned > 2000: return {"hits": hits, "skipped": skipped, "truncated": True, "files_scanned": scanned - 1}
            try:
                relative(selected); raw = self._raw(binding, selected); text = raw.decode("utf-8")
                if "\x00" in text: raise UnicodeError
            except (InvalidInput, NotFound, UnicodeError) as error:
                skipped.append({"path": selected, "reason": type(error).__name__}); continue
            matches = [(number, line) for number, line in enumerate(text.splitlines(), 1) if query in line]
            if matches:
                with self.store.connect() as connection:
                    connection.execute("BEGIN IMMEDIATE")
                    version = self._observe(binding, selected, raw)
                for number, line in matches:
                    if len(hits) >= limit: return {"hits": hits, "skipped": skipped, "truncated": True, "files_scanned": scanned}
                    hits.append({"path": selected, "line_number": number, "text": line,
                                 "source_ref": self._ref(version)})
        return {"hits": hits, "skipped": skipped, "truncated": False, "files_scanned": scanned}

    def check_sources(self, project_id, refs):
        rows = []
        for ref in refs:
            if ref.project_id != project_id: raise InvalidInput("FileSource belongs to another project")
            binding = self.get(project_id, ref.workspace_id, ref.binding_revision)
            if binding["binding_sha256"] != ref.binding_sha256: raise Conflict("FileSource workspace binding SHA changed")
            version = self._version(binding, relative(ref.path), ref.revision)
            if not version or version["sha256"] != ref.sha256: raise Conflict("FileSource revision SHA does not match")
            raw = self._raw(binding, ref.path, missing=True)
            rows.append({"source_ref": ref.model_dump(), "status": "CURRENT" if raw is not None and sha(raw) == ref.sha256 else "MISSING" if raw is None else "STALE",
                         "live_sha256": sha(raw) if raw is not None else None})
        return rows

    def check_saved_sources(self, project_id, refs,*,_exact_revisions=False):
        """Current saved-byte checks for explicit readonly delivery observations."""
        rows = []
        for ref in refs:
            if ref.project_id != project_id: raise InvalidInput("FileSource belongs to another project")
            binding = self.get(project_id, ref.workspace_id, ref.binding_revision)
            if binding["binding_sha256"] != ref.binding_sha256: raise Conflict("FileSource workspace binding SHA changed")
            version = self._version(binding, relative(ref.path), ref.revision,**({'_exact_revision':True}if _exact_revisions else {}))
            if not version or version["sha256"] != ref.sha256: raise Conflict("FileSource revision SHA does not match")
            raw = self._saved_raw(binding, ref.path, missing=True)
            checked = self._saved_raw(binding, ref.path, missing=True)
            if raw != checked:
                raise Conflict("SOURCE_DRIFT: saved file changed between snapshot checks")
            rows.append({"source_ref": ref.model_dump(),
                         "status": "CURRENT" if raw is not None and sha(raw) == ref.sha256 else "MISSING" if raw is None else "STALE",
                         "live_sha256": sha(raw) if raw is not None else None})
        return rows

    def _replace(self, binding, path, raw):
        native = self._native_handles()
        if native is not None:
            relative(path); self._private(binding, path)
            return native.replace_atomic(binding, path, raw, private_check=self._private)
        with self._parent(binding, path) as (parent, name):
            temporary = ".eeas-write-" + uuid.uuid4().hex
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                view = memoryview(raw)
                while view: view = view[os.write(fd, view):]
                os.fsync(fd)
            finally: os.close(fd)
            # Preserve the old permissions for existing source files.
            try: mode = stat.S_IMODE(os.stat(name, dir_fd=parent, follow_symlinks=False).st_mode)
            except FileNotFoundError: mode = 0o600
            os.chmod(temporary, mode, dir_fd=parent, follow_symlinks=False)
            os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)

    def operation(self, project_id, wid, operation_id):
        binding = self.get(project_id, wid)
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,100}", operation_id): raise InvalidInput("Invalid local edit operation id")
        directory = self._data(project_id, wid, "operations", digest(operation_id))
        if not directory.exists(): raise NotFound("Local edit operation does not exist")
        claim = self._json(directory / "request.json")
        failures = [self._json(path) for path in sorted(directory.glob("failure-*.json"))]
        result = directory / "result.json"
        if result.exists():
            saved = self._json(result)
            version = self._version(binding, saved["source_ref"]["path"], saved["source_ref"]["revision"])
            if not version or self._ref(version) != saved["source_ref"]: raise Conflict("Edit result source integrity failed")
            return {"operation_id": operation_id, "status": "APPLIED", "result": saved, "failures": failures}
        return {"operation_id": operation_id, "status": "UNKNOWN_OUTCOME" if (directory / "write-begun.json").exists() else "FAILED" if failures else "NOT_APPLIED",
                "path": claim["request"]["path"], "request_sha256": claim["request_sha256"], "failures": failures}

    def edit_binding(self,project_id,wid,path):
        """Current binding and path grant only; not a write or editor lease."""
        binding=self.get(project_id,wid);relative(path)
        scopes=binding['allowed_write_paths']
        if not any(scope=='*' or scope==path or scope.endswith('/') and path.startswith(scope) for scope in scopes):
            raise InvalidInput('Write path is outside the selected workspace grant')
        return binding

    def edit(self, project_id, wid, body: FileEditInput):
        with self.store.connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            binding = self.edit_binding(project_id,wid,body.path)
            request = body.model_dump(exclude={"operation_id"}); request_hash = digest(request)
            operation = self._data(project_id, wid, "operations", digest(body.operation_id)); operation.mkdir(parents=True, exist_ok=True)
            claim = operation / "request.json"
            if claim.exists():
                if self._json(claim).get("request_sha256") != request_hash:
                    raise Conflict("IDEMPOTENCY_CONFLICT: edit operation already binds different input")
            else: self._write(claim, {"request_sha256": request_hash, "request": request, "created_at": now()})
            result_path = operation / "result.json"
            live = self._raw(binding, body.path, missing=True)
            if result_path.exists():
                result = self._json(result_path)
                version = self._version(binding, body.path, result["source_ref"]["revision"])
                if not version or self._ref(version) != result["source_ref"]: raise Conflict("Stored edit result source integrity failed")
                return {**result, "replayed": True, "current": live is not None and sha(live) == version["sha256"]}
            raw = body.content.encode("utf-8"); proposed_sha = sha(raw)
            begun = operation / "write-begun.json"
            if begun.exists():
                if live is not None and sha(live) == proposed_sha:
                    version = self._observe(binding, body.path, live, origin="RECOVERED_API_EDIT")
                    result = {"outcome": "APPLIED", "source_ref": self._ref(version), "recovered": True}
                    self._write(result_path, result)
                    return {**result, "replayed": True, "current": True}
                raise Conflict("UNKNOWN_OUTCOME: interrupted edit cannot be safely repeated; inspect live bytes/history")
            from packages.domain.editor_handoff_scope import assert_handoff_free
            assert_handoff_free(connection,binding['root'],identity=binding['root_identity'])
            head = self._observe(binding, body.path, live) if live is not None else self._version(binding, body.path)
            if (body.expected_revision == 0 and (live is not None or head is not None)) or (body.expected_revision > 0 and
                    (live is None or head is None or head["revision"] != body.expected_revision or sha(live) != body.expected_sha256 or head["sha256"] != body.expected_sha256)):
                self._write(operation / ("failure-" + uuid.uuid4().hex + ".json"), {"status": "FAIL", "reason": "SOURCE_DRIFT", "created_at": now()})
                raise Conflict("SOURCE_DRIFT: expected revision/SHA differs from current file; reread before editing")
            before = live or b""
            try:
                old_text = before.decode("utf-8")
                if "\x00" in old_text: raise UnicodeError
            except UnicodeError:
                raise InvalidInput("This edit interface supports existing UTF-8 text files only") from None
            diff = "".join(difflib.unified_diff(old_text.splitlines(keepends=True), body.content.splitlines(keepends=True), fromfile=body.path, tofile=body.path))
            self._write(begun, {"before_sha256": sha(live) if live is not None else None,
                               "after_sha256": proposed_sha, "created_at": now()})
            try:
                native = self._native_handles()
                commit = None
                if native is not None:
                    commit = native.compare_write(binding, body.path, raw,
                        expected_raw=live, private_check=self._private)
                else:
                    self._replace(binding, body.path, raw)
                actual = self._raw(binding, body.path)
                if actual != raw: raise Conflict("UNKNOWN_OUTCOME: actual edited bytes do not match the proposed result")
                version = self._observe(binding, body.path, actual, origin="API_EDIT")
                result = {"outcome": "APPLIED", "source_ref": self._ref(version), "diff": diff, "recovered": False}
                if commit is not None: result['commit_qualification'] = commit
                self._write(result_path, result)
                return {**result, "replayed": False, "current": True}
            except Exception as error:
                self._write(operation / ("failure-" + uuid.uuid4().hex + ".json"), {"status": "FAIL", "reason": type(error).__name__, "message": str(error)[:1000], "created_at": now()})
                raise
