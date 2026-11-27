"""Opt-in, bounded startup measurements; never a source of application failure."""
from contextlib import contextmanager
import json
import os
import sys
import time


_PHASES = {
    "API_ENTRY": frozenset({"CLIENT_SERVER_IMPORTS", "APPLICATION_IMPORT", "SETTINGS",
                            "CREATE_APP", "SERVER_CONFIGURATION", "SERVER_RUN"}),
    "API_APP": frozenset({"STORE_INIT", "BUNDLED_SKILL_VERIFY", "LIFESPAN_START"}),
}
_STARTS = {}


def _now():
    try:
        return time.perf_counter_ns()
    except Exception:
        return None


def _emit(component, phase, status, started, finished=None):
    # No paths, exception messages, arguments, configuration or caller metadata.
    if os.environ.get("EEAS_STARTUP_TIMING") != "1":
        return
    try:
        if phase not in _PHASES.get(component, ()) or started is None:
            return
        instant = started if finished is None else finished
        origin = _STARTS.setdefault(component, started)
        row = {"schema": "eeas.startup-timing.v1", "event": "STARTUP_PHASE",
               "component": component, "pid": os.getpid(), "phase": phase,
               "status": status, "started_monotonic_ns": started,
               "elapsed_ms": round(max(0, instant - origin) / 1_000_000, 3)}
        if finished is not None:
            row.update(finished_monotonic_ns=finished,
                       duration_ms=round(max(0, finished - started) / 1_000_000, 3))
        sys.stderr.write(json.dumps(row, separators=(",", ":")) + "\n")
        sys.stderr.flush()
    except Exception:
        # An unavailable diagnostic sink must not abort startup or hide its error.
        pass


def startup_entered(component, phase):
    """Mark a boundary whose lifetime is not a startup-duration measurement."""
    if os.environ.get("EEAS_STARTUP_TIMING") == "1":
        _emit(component, phase, "ENTERED", _now())


@contextmanager
def startup_phase(component, phase):
    if os.environ.get("EEAS_STARTUP_TIMING") != "1":
        yield
        return
    started = _now()
    _emit(component, phase, "ENTERED", started)
    try:
        yield
    except BaseException as failure:
        cancelled = isinstance(failure, KeyboardInterrupt) or type(failure).__name__ == "CancelledError"
        _emit(component, phase, "CANCELLED" if cancelled else "FAILED", started, _now())
        raise
    else:
        _emit(component, phase, "COMPLETED", started, _now())
