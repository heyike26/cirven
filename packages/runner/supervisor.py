"""Detached supervisor for trusted original fixtures; this is not an OS sandbox.

Invoked by absolute script path with python -I. It deliberately does not import
the application or access SQLite: the result survives controller termination.
"""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

import psutil


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def atomic_json(path, value):
    temporary = path.with_suffix(".pending")
    with temporary.open("wb") as stream:
        stream.write(encoded(value))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def identity(process):
    return {"pid": process.pid, "create_time": process.create_time()}


def matching_process(value):
    if not value or not isinstance(value.get("pid"), int) or value["pid"] <= 0 or not isinstance(value.get("create_time"), (int, float)):
        return None
    try:
        process = psutil.Process(value["pid"])
        if process.create_time() != value["create_time"]:
            return None
        if process.status() == psutil.STATUS_ZOMBIE:
            return None
        return process
    except (psutil.Error, KeyError, TypeError):
        return None


def run(request_path):
    raw = request_path.read_bytes()
    request = json.loads(raw)
    directory = request_path.parent
    state = {"schema_version": "eeas.runner-receipt.v1", "job_id": request["job_id"],
             "run_id": request["run_id"], "request_hash": hashlib.sha256(raw).hexdigest(),
             "supervisor": identity(psutil.Process()), "child": None,
             "phase": "STARTING", "exit_code": None, "error_type": None,
             "started_at": time.time(), "ended_at": None}
    sequence = 0

    def publish(phase):
        nonlocal sequence
        sequence += 1
        state.update(phase=phase, event_seq=sequence)
        with (directory / f"event-{sequence:04d}.json").open("xb") as stream:
            stream.write(encoded(state))
            stream.flush()
            os.fsync(stream.fileno())
        atomic_json(directory / "receipt.json", state)

    publish("STARTING")
    process = None
    descendants = {}
    output_limit = threading.Event()
    counter_lock = threading.Lock()
    total = 0
    threads = []
    paths = [Path(request["stdout_path"]), Path(request["stderr_path"])]

    def capture(pipe, path):
        nonlocal total
        with path.open("xb") as stream:
            while block := pipe.read(4096):
                with counter_lock:
                    remaining = max(0, request["output_limit_bytes"] - total)
                    total += len(block)
                    if total > request["output_limit_bytes"]:
                        output_limit.set()
                stream.write(block[:remaining])
            stream.flush()
            os.fsync(stream.fileno())
        pipe.close()

    def stop_tree():
        if process:
            try:
                for child in process.children(recursive=True):
                    descendants[child.pid] = identity(child)
            except psutil.Error:
                pass
        identities = list(descendants.values()) + [state["child"]]
        for value in identities:
            target = matching_process(value)
            if target:
                try:
                    target.kill()  # psutil also checks for PID reuse before signalling.
                except psutil.Error:
                    pass
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            if not any(matching_process(value) for value in identities):
                return True
            time.sleep(0.02)
        return False

    try:
        env = {key: os.environ[key] for key in ("SystemRoot", "WINDIR", "TEMP", "TMP") if key in os.environ}
        env["PYTHONIOENCODING"] = "utf-8"
        process = psutil.Popen(request["argv"], cwd=request["workspace"], env=env,
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, close_fds=True)
        try:
            state["child"] = identity(process)
        except psutil.NoSuchProcess:
            # A very short-lived process still has a real Popen exit receipt.
            state["child"] = {"pid": process.pid, "create_time": None}
        for pipe, path in zip((process.stdout, process.stderr), paths):
            thread = threading.Thread(target=capture, args=(pipe, path), daemon=True)
            thread.start()
            threads.append(thread)
        publish("RUNNING")
        deadline = time.monotonic() + request["timeout_seconds"]
        while process.poll() is None:
            try:
                for child in process.children(recursive=True):
                    descendants[child.pid] = identity(child)
            except psutil.Error:
                pass
            cancel_path = directory / "cancel.json"
            if cancel_path.exists():
                cancel = json.loads(cancel_path.read_bytes())
                if cancel.get("request_hash") == state["request_hash"] and cancel.get("job_id") == state["job_id"]:
                    if cancel.get("reason") in {"CANCELLED", "INTERRUPTED"}:
                        state["error_type"] = cancel["reason"]
            if time.monotonic() >= deadline:
                state["error_type"] = "TIME_LIMIT"
            if output_limit.is_set():
                state["error_type"] = "OUTPUT_LIMIT"
            if state["error_type"]:
                if not stop_tree():
                    state["error_type"] = "STOP_UNCONFIRMED"
                    state["ended_at"] = time.time()
                    publish("UNKNOWN_OUTCOME")
                    return
                break
            time.sleep(0.03)
        state["exit_code"] = process.wait(timeout=3)
        # Also finish tracked descendants before claiming the tool stopped.
        if any(matching_process(value) for value in descendants.values()) and not stop_tree():
            raise RuntimeError("Descendant stop was not confirmed")
        for thread in threads:
            thread.join(timeout=3)
        if any(thread.is_alive() for thread in threads):
            raise RuntimeError("Output pipes did not close")
        if output_limit.is_set():
            state["error_type"] = "OUTPUT_LIMIT"
        state["outputs"] = {path.name: {"bytes": path.stat().st_size,
                             "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in paths}
        state["ended_at"] = time.time()
        publish("FINISHED")
    except Exception as error:
        confirmed = stop_tree()
        if process:
            try:
                state["exit_code"] = process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                confirmed = False
        state["error_type"] = "SUPERVISOR_ERROR:" + type(error).__name__
        state["ended_at"] = time.time()
        # Even a stopped child may have acted before an incomplete result write.
        state["stop_confirmed"] = confirmed
        publish("UNKNOWN_OUTCOME")


if __name__ == "__main__":
    run(Path(sys.argv[1]).resolve(strict=True))
