"""In-process real FastAPI fixture over JSON lines; no listener, GUI or model.

Only temporary capability records and an original local MCP fixture are created.
The frontend test's bridge replaces Electron IPC, not the API or MCP protocol.
"""
import hashlib
import json
from pathlib import Path
import site
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from apps.api.native_capabilities import register_native_capability_routes
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict, InvalidInput, NotFound, Store

TOKEN = "a" * 64
temporary = tempfile.TemporaryDirectory(prefix="eeas-frontend-capability-")
directory = Path(temporary.name)
store = Store(directory / "records.sqlite3")


def create_client():
    app = FastAPI()
    for exception, status in ((Conflict, 409), (InvalidInput, 422), (NotFound, 404)):
        async def error_handler(_request, error, code=status):
            return JSONResponse(status_code=code, content={"error": str(error)})
        app.add_exception_handler(exception, error_handler)
    authority = NativeCollaborationAuthority(directory / "host", token=TOKEN, owner_pid=123, platform="posix")
    register_native_capability_routes(app, store, directory, authority=authority,
        service=NativeCapabilities(store, directory))
    return TestClient(app)


client = create_client()
try:
    for raw in sys.stdin:
        try:
            command = json.loads(raw)
            if command.get("fixture") == "restart":
                client.close()
                client = create_client()
                answer = {"status": 200, "body": {"reopened": True}}
            elif command.get("fixture") == "local-mcp":
                ident = "broken" if command.get("broken") else "original"
                source = directory / (ident + "_server.py")
                source.write_text("import sys\nsys.path.insert(0, " + repr(site.getsitepackages()[0]) + ")\n" +
                    ("raise RuntimeError('actual fixture startup failed')\n" if command.get("broken") else
                     "from mcp.server import MCPServer\nserver=MCPServer('frontend-contract-fixture')\n"
                     "@server.tool()\ndef exact_read(text:str)->str:\n    return text\n"
                     "server.run(transport='stdio')\n"))
                files = [str(Path(sys.executable).resolve()), str(source)]
                answer = {"status": 200, "body": {"type": "local", "command": files,
                    "files_sha256": {name: hashlib.sha256(Path(name).read_bytes()).hexdigest() for name in files},
                    "enabled": True, "timeout": 5000}}
            else:
                headers = {"X-EEAS-Desktop-Host": TOKEN} if command.get("host", True) else {}
                response = client.request(command["method"], command["path"], json=command.get("body"), headers=headers)
                answer = {"status": response.status_code, "body": response.json()}
        except Exception as error:
            answer = {"status": 500, "body": {"fixture_error": type(error).__name__, "message": str(error)}}
        print(json.dumps(answer, ensure_ascii=False), flush=True)
finally:
    client.close()
    temporary.cleanup()
