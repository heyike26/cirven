import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

from apps.api.boot_timing import startup_entered, startup_phase

with startup_phase("API_ENTRY", "CLIENT_SERVER_IMPORTS"):
    import httpx
    import uvicorn

with startup_phase("API_ENTRY", "APPLICATION_IMPORT"):
    from apps.api.config import Settings
    from apps.api.main import create_app


def port_available(settings):
    family = socket.AF_INET6 if ":" in settings.host else socket.AF_INET
    with socket.socket(family) as probe:
        if os.name == "nt":
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            # Match the server's reuse behavior: TIME_WAIT is not a live listener.
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind((settings.host, settings.port))
        except OSError:
            return False
    return True


def main():
    parser = argparse.ArgumentParser(description="EEAS local service management")
    parser.add_argument("action", choices=["serve", "start", "stop", "diagnose"])
    args = parser.parse_args()
    try:
        with startup_phase("API_ENTRY", "SETTINGS"):
            settings = Settings.from_env()
    except ValueError as exception:
        print(f"Configuration error: {exception}", file=sys.stderr)
        return 2
    if args.action == "diagnose":
        with httpx.Client(trust_env=False) as client:
            try:
                response = client.get(settings.origin + "/health", timeout=2)
                response.raise_for_status()
                print(json.dumps(response.json()))
                return 0
            except httpx.HTTPError:
                print("EEAS is not reachable", file=sys.stderr)
                return 1
    if args.action == "stop":
        token_path = settings.data_dir / "local-access.token"
        if not token_path.is_file() or token_path.is_symlink():
            print("Local instance token is unavailable", file=sys.stderr)
            return 1
        with httpx.Client(trust_env=False) as client:
            import hmac
            token = token_path.read_text().strip()
            client.cookies.set("eeas_session", token)
            try:
                response = client.post(settings.origin + "/api/shutdown", headers={
                    "Origin": settings.origin,
                    "X-EEAS-CSRF": hmac.digest(token.encode(), b"eeas-csrf-v1", "sha256").hex()}, timeout=3)
                response.raise_for_status()
                for _ in range(100):
                    if port_available(settings):
                        print("EEAS stopped; port released")
                        return 0
                    time.sleep(0.05)
                print("EEAS stop timed out", file=sys.stderr)
            except httpx.HTTPError:
                print("EEAS stop failed", file=sys.stderr)
        return 1
    if not port_available(settings):
        print(f"Port {settings.port} is occupied; choose EEAS_PORT", file=sys.stderr)
        return 2
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    if args.action == "start":
        root = Path(__file__).resolve().parents[2]
        child_env = {**os.environ, "EEAS_DATA_DIR": str(settings.data_dir),
                     "EEAS_HOST": settings.host, "EEAS_PORT": str(settings.port)}
        with (settings.data_dir / "server.stdout.txt").open("a") as output:
            child = subprocess.Popen([sys.executable, "-m", "apps.api", "serve"], cwd=root,
                                     env=child_env, stdout=output, stderr=output,
                                     start_new_session=os.name != "nt")
        with httpx.Client(trust_env=False) as client:
            # Cold starts perform the same migrations and skill verification as the desktop host.
            deadline = time.monotonic() + 180
            while time.monotonic() < deadline:
                if child.poll() is not None:
                    print("EEAS startup failed; see local server output", file=sys.stderr)
                    return 1
                try:
                    response = client.get(settings.origin + "/health", timeout=0.2)
                    if response.status_code == 200:
                        print(f"EEAS running at {settings.origin}")
                        return 0
                except httpx.HTTPError:
                    pass
                time.sleep(0.05)
        child.terminate()
        child.wait(timeout=5)
        print("EEAS startup timed out", file=sys.stderr)
        return 1
    with startup_phase("API_ENTRY", "CREATE_APP"):
        app = create_app(settings, shutdown=lambda: setattr(server, "should_exit", True))
    with startup_phase("API_ENTRY", "SERVER_CONFIGURATION"):
        server = uvicorn.Server(uvicorn.Config(app, host=settings.host, port=settings.port,
                                              log_level="warning", access_log=False))
    startup_entered("API_ENTRY", "SERVER_RUN")
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
