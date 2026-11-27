"""Read-only collector executed inside the administrator-selected Linux build image.

Intentionally uses only Python's standard library. Its stdout is a private
length-delimited binary channel; the build command gets its own captured pipe.
"""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time

MAGIC = b"CIRVEN-OUTPUT-1\n"
MAX_FILE = 32 * 1024**2
MAX_TOTAL = 64 * 1024**2
MAX_LOG = 65536
MAX_LOG_WIRE = 2 * 1024**2


def regular_bytes(root_fd, name, remaining):
    parts = name.split("/")
    if any(not part or part in (".", "..") for part in parts) or "\\" in name or "\x00" in name:
        raise ValueError("invalid build output path")
    folder = os.dup(root_fd)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_NOFOLLOW | os.O_DIRECTORY, dir_fd=folder)
            os.close(folder)
            folder = child
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=folder)
    finally:
        os.close(folder)
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > min(MAX_FILE, remaining):
            raise ValueError("output is not a bounded, singly linked regular file")
        raw = stream.read(before.st_size + 1)
        after = os.fstat(fd)
        if (len(raw) != before.st_size or before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns
                or before.st_ctime_ns != after.st_ctime_ns or after.st_nlink != 1):
            raise ValueError("output changed during collection")
    return raw


def _stop(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def execute(argv, timeout, project):
    child = subprocess.Popen(argv, cwd=project, start_new_session=True, stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE, stderr=subprocess.STDOUT, shell=False)
    log, over, seen = bytearray(), threading.Event(), 0
    def drain():
        nonlocal seen
        with child.stdout as source:
            while chunk := source.read(16384):
                seen += len(chunk)
                log.extend(chunk[:max(0, MAX_LOG-len(log))])
                if seen > MAX_LOG_WIRE:
                    over.set()
    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    until = time.monotonic() + timeout
    stop = None
    try:
        while child.poll() is None:
            if over.wait(0.04):
                stop = "output_limit"
                break
            if time.monotonic() >= until:
                stop = "timeout"
                break
    finally:
        _stop(child)
        child.wait(timeout=4)
        reader.join(timeout=1)
    return child.returncode, bytes(log), stop or ("output_limit" if seen > MAX_LOG_WIRE else None), seen > len(log)


def publish(header, blobs):
    raw = json.dumps(header, ensure_ascii=False, separators=(",", ":")).encode()
    wire = sys.stdout.buffer
    wire.write(MAGIC + len(raw).to_bytes(4, "big") + raw)
    for blob in blobs:
        wire.write(blob)
    wire.flush()


def collect_build(cfg, inputs, project):
    # copyfile (without source permissions) gives the disposable build tree
    # ordinary writable files while its original host input mount stays read-only.
    shutil.copytree(inputs, project, copy_function=shutil.copyfile)
    base = os.open(project, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        code, log, limit, truncated = execute(cfg["argv"], cfg["timeout_seconds"], project)
        header = {"exit_code": code, "limit": limit, "detail": log.decode("utf-8", "replace"),
                  "output_truncated": truncated, "files": []}
        blobs = []
        if code == 0 and limit is None:
            remaining = MAX_TOTAL
            for path in cfg["outputs"]:
                try:
                    raw = regular_bytes(base, path, remaining)
                except (OSError, ValueError) as error:
                    blobs.clear()
                    header.update(files=[], limit="collection_error",
                                  detail=(header["detail"]+"\n"+path+": "+type(error).__name__)[-MAX_LOG:])
                    break
                remaining -= len(raw)
                blobs.append(raw)
                header["files"].append({"path": path, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()})
        publish(header, blobs)
    finally:
        os.close(base)


def main():
    # Do not allow a build child to access the collector's stdout via procfs.
    if ctypes.CDLL(None).prctl(4, 0, 0, 0, 0) != 0:
        raise RuntimeError("could not isolate collector descriptors")
    cfg = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    collect_build(cfg, Path("/input"), Path("/work/project"))


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        # The record is still machine-readable if a declared file is missing,
        # changed, or points outside the build tree.
        publish({"exit_code": None, "limit": "collection_error", "detail": type(error).__name__,
                 "output_truncated": False, "files":[]}, [])
        sys.exit(1)
