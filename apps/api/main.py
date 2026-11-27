import hmac
import json
import os
from pathlib import Path
import secrets
import sqlite3
import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, UploadFile, File, Form, Query
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.background import BackgroundTask
from starlette.requests import ClientDisconnect
from apps.api.owner_shutdown import register_owner_shutdown
from apps.api.conversation_directory import register_conversation_directory
from apps.api.intake import register_intake_routes
from apps.api.questions import register_question_routes
from apps.api.decisions import register_decision_routes
from apps.api.taskbooks import register_taskbook_routes
from apps.api.harnesses import register_harness_routes
from apps.api.reports import register_report_routes
from apps.api.planning import register_planning_routes
from apps.api.local_workspaces import register_local_workspace_routes
from apps.api.direct_actions import register_direct_action_routes
from apps.api.native_feedback import register_native_feedback_routes
from apps.api.native_rework import register_native_rework_routes
from apps.api.providers import register_provider_routes
from apps.api.subscription import register_subscription_routes
from apps.api.planning_tools import register_planning_tool_routes
from apps.api.native_gateway import register_native_gateway_routes
from apps.api.planning_reports import register_planning_report_routes
from apps.api.discovery import register_discovery_routes
from apps.api.workbench_feedback import register_workbench_feedback_routes
from apps.api.competition_sources import register_competition_source_routes
from apps.api.template_profiles import register_template_profile_routes
from apps.api.discovery_diagrams import register_discovery_diagram_routes
from apps.api.workbench_texts import register_workbench_text_routes
from apps.api.desktop_host import register_desktop_host_routes
from apps.api.native_runtime import register_native_collaboration_routes
from apps.api.native_messages import register_native_message_routes
from apps.api.docx_revisions import register_docx_revision_routes
from apps.api.native_append_composition import register_bound_native_append
from apps.api.native_open import register_native_open_routes
from apps.api.native_delivery_exports import register_native_delivery_export_routes
from apps.api.native_capabilities import register_native_capability_routes
from apps.api.agent_sessions import register_agent_session_routes
from apps.api.feedback_rework import register_feedback_rework_routes
from apps.api.planning_delivery import register_planning_delivery_routes
from apps.api.desktop_control import register_desktop_control_routes
from apps.api.workbench_observations import register_workbench_observation_routes
from apps.api.workbench_state import register_workbench_state_routes
from apps.api.supervisor import register_supervisor_routes
from apps.api.dynamic_workflow import register_dynamic_workflow_routes
from apps.api.agent_collaboration import register_agent_collaboration_routes
from pydantic import BaseModel, ConfigDict, Field, field_validator
from typing import Literal, Annotated

from apps.api.config import Settings
from apps.api.body_limit import BodyLimitMiddleware
from apps.api.boot_timing import startup_phase
from packages.domain.store import NotFound, Store, Conflict, InvalidInput
from packages.domain.requirements import RequirementBundle
from packages.domain.document_import import MAX_DOCUMENT_BYTES
from packages.domain.bundled_capabilities import ensure_bundled_skills
from packages.adapters.planning import MockPlanner
from packages.domain.tasks import TaskService
from packages.domain.task_schema import TaskInput, StartRunInput
from packages.runner.worker import Worker
from packages.adapters.claude_sdk import ClaudeBridge, LiveBlocked
from packages.adapters.native_runtime import create_runtime_bridge
from packages.adapters.task_sdk import TaskNativeReviews
from packages.verifier.service import Verifier
from packages.verifier.repair import RepairCoordinator
from packages.domain.approvals import Approvals
from packages.extensions.service import ExtensionRegistry

ROOT = Path(__file__).resolve().parents[2]


class ProjectInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=8000)
    operation_id: str | None = Field(default=None, pattern=r'^[A-Za-z0-9_-]{1,100}$')

    @field_validator("name")
    @classmethod
    def name_not_blank(cls, value):
        if not value.strip():
            raise ValueError("Project name cannot be blank")
        return value.strip()


class ExtractInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str
    revision: int = Field(ge=1)
    mode: Literal["mock", "live"] = "mock"
    chunk_indexes: list[Annotated[int, Field(ge=0, strict=True)]] | None = Field(default=None, max_length=50)
    include_images: bool = Field(default=False, strict=True)
    image_asset_ids: list[str] | None = Field(default=None, max_length=16)
    search_enabled: bool = Field(default=False, strict=True)


class PlanInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)


class RepairInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_repairs: int = Field(default=3, ge=1, le=3)


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    approve: bool


class ExtensionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    permissions: list[str] = Field(max_length=20)


class ExtensionPin(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str = Field(pattern=r"^[0-9a-f]{64}$")


class NativeTaskBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")
    run_id: str = Field(min_length=1, max_length=100)
    task_id: str = Field(min_length=1, max_length=100)
    task_revision: int = Field(ge=1, strict=True)
    spec_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    stack_lock_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


class NativeTaskSubmit(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    task_binding: NativeTaskBinding
    input_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    source_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    review_prompt: str = Field(default="", max_length=8000)
    needs_tools: bool = Field(default=False, strict=True)
    max_turns: int | None = Field(default=None, ge=1, le=20, strict=True)
    timeout_seconds: int | None = Field(default=None, ge=1, le=120, strict=True)
    native: dict = Field(default_factory=dict)
    chunk_selections: list["DocumentChunkSelection"] = Field(default_factory=list, max_length=20)
    include_images: bool = Field(default=False, strict=True)
    image_selections: list["NativeImageSelection"] | None = Field(default=None, max_length=16)
    search_enabled: bool = Field(default=False, strict=True)


class NativeImageSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, strict=True)
    asset_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class DocumentChunkSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    document_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(ge=1, strict=True)
    chunk_indexes: list[Annotated[int, Field(ge=0, strict=True)]] = Field(min_length=1, max_length=50)


class DocumentContextInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1, strict=True)
    chunk_indexes: list[Annotated[int, Field(ge=0, strict=True)]] | None = Field(default=None, max_length=50)
    char_budget: int = Field(default=120000, ge=1, le=200000, strict=True)
    byte_budget: int = Field(default=220000, ge=1, le=240000, strict=True)


class NativeTaskControl(BaseModel):
    model_config = ConfigDict(extra="forbid")
    operation_id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,100}$")
    expected_session_id: str = Field(min_length=1, max_length=200)
    prompt: str = Field(min_length=1, max_length=32768)


def error(status: int, code: str, message: str):
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


class _DisconnectedResponse(Response):
    async def __call__(self, scope, receive, send):
        """Finish the middleware call without sending on a closed connection."""


def secure_equal(actual: str, expected: str) -> bool:
    return hmac.compare_digest(actual.encode("utf-8"), expected.encode("utf-8"))


def create_app(settings: Settings | None = None, shutdown=None) -> FastAPI:
    settings = settings or Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.data_dir.chmod(0o700)
    token_path = settings.data_dir / "local-access.token"
    if token_path.is_symlink():
        raise ValueError("Access token must not be a symlink")
    try:
        descriptor = os.open(token_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        token = token_path.read_text().strip()
        if len(token) < 40:
            raise ValueError("Invalid local access token")
    else:
        token = secrets.token_urlsafe(48)
        with os.fdopen(descriptor, "w") as stream:
            stream.write(token)
    token_path.chmod(0o600)
    csrf = hmac.digest(token.encode(), b"eeas-csrf-v1", "sha256").hex()
    with startup_phase("API_APP", "STORE_INIT"):
        store = TaskService(settings.data_dir / "eeas.sqlite3", ROOT)
    worker = Worker(store, settings.data_dir)
    bridge = create_runtime_bridge(store, settings.data_dir)
    native_reviews = TaskNativeReviews(store, bridge)
    verifier = Verifier(store, settings.data_dir)
    repair_coordinator = RepairCoordinator(store, verifier)
    approvals = Approvals(store)
    extensions = ExtensionRegistry(store, settings.data_dir)
    worker.repair_coordinator = repair_coordinator
    worker.verifier = verifier

    @asynccontextmanager
    async def lifespan(app):
        with startup_phase("API_APP", "LIFESPAN_START"):
            if settings.worker_enabled:
                worker.start()
            await app.state.global_supervisor.start()
        try:
            yield
        finally:
            await app.state.owner_shutdown.close()
            if settings.worker_enabled:
                await asyncio.to_thread(worker.stop)

    app = FastAPI(title="EEAS", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.store = store
    app.state.settings = settings
    app.state.worker = worker
    app.state.bridge = bridge
    app.state.native_reviews = native_reviews
    app.state.verifier = verifier
    app.state.repair_coordinator = repair_coordinator
    register_intake_routes(app, store, ROOT)
    register_question_routes(app, store, hmac.digest(token.encode(), b"eeas-local-caller-v1", "sha256").hex())
    register_decision_routes(app, store, hmac.digest(token.encode(), b"eeas-local-caller-v1", "sha256").hex())
    register_taskbook_routes(app, store, hmac.digest(token.encode(), b"eeas-local-caller-v1", "sha256").hex())
    register_harness_routes(app, store, verifier, hmac.digest(token.encode(), b"eeas-local-caller-v1", "sha256").hex())
    register_report_routes(app, store, settings.data_dir)
    register_local_workspace_routes(app, store, settings.data_dir)
    register_workbench_state_routes(app, store, settings.data_dir, workspaces=app.state.local_workspaces)
    register_provider_routes(app, store, settings.data_dir, ROOT)
    register_planning_routes(app, store, ROOT)
    register_direct_action_routes(app,store,settings.data_dir)
    register_native_feedback_routes(app,store)
    register_discovery_routes(app,store)
    register_workbench_feedback_routes(app,store)
    register_feedback_rework_routes(app,store)
    register_native_rework_routes(app,store)
    register_competition_source_routes(app,store,settings.data_dir)
    app.state.planning_proposals.competition_sources=app.state.competition_sources
    register_template_profile_routes(app,store,settings.data_dir)
    app.state.discovery.template_profiles=app.state.template_profiles
    register_discovery_diagram_routes(app,store,settings.data_dir,source_root=ROOT)
    register_planning_tool_routes(app)
    register_native_gateway_routes(app, store)
    register_planning_report_routes(app,store,settings.data_dir,
        experience_reader=app.state.template_profiles.instructions,
        discovery_reader=app.state.discovery.get,
        diagram_reader=app.state.planning_diagrams.read_artifact)
    register_planning_delivery_routes(app,store)
    from packages.domain.file_continuity import FileContinuity
    app.state.file_continuity = FileContinuity(store, settings.data_dir, app.state.local_workspaces,
        report_reader=app.state.planning_reports.download, artifact_reader=app.state.report_artifacts.download)
    register_workbench_text_routes(app)
    register_desktop_host_routes(app,store,settings.data_dir)
    companion=register_native_collaboration_routes(app,store,settings.data_dir)
    register_subscription_routes(app,store,settings.data_dir,ROOT,authority=companion.authority)
    revisions = register_docx_revision_routes(app, store, settings.data_dir,
        snapshot_reader=app.state.file_continuity.read_snapshot_bytes,
        owner_checker=lambda pid, owner: (getattr(app.state, 'native_messages', None) is not None
            and app.state.native_messages.docx_owner_current(pid, owner)))
    app.state.file_continuity.revision_reader = revisions.download
    register_native_message_routes(app,authority=companion.authority,context_rpc=companion.context_rpc)
    if companion.authority.enabled:
        register_bound_native_append(app,companion=companion,source_root=ROOT,
            session_cookie_checker=lambda actual: isinstance(actual,str) and secure_equal(actual,token))
    register_native_capability_routes(app,store,settings.data_dir,authority=companion.authority)
    with startup_phase("API_APP", "BUNDLED_SKILL_VERIFY"):
        app.state.bundled_skills = ensure_bundled_skills(app.state.native_capabilities,ROOT)
    register_agent_session_routes(app,store,settings.data_dir)
    register_agent_collaboration_routes(app)
    register_dynamic_workflow_routes(app, store)
    register_native_open_routes(app,authority=companion.authority)
    register_native_delivery_export_routes(app,authority=companion.authority)
    if companion.authority.enabled:
        register_desktop_control_routes(app,store,settings.data_dir,workspace=companion.authority,
            transport=app.state.desktop_transport,capability_reader=lambda:{'desktop_input_routes_qualified':False})
    app.state.workbench_feedback.bitmap_authority=app.state.desktop_host.bitmap
    app.state.planning_proposals.transport.bitmap_authority=app.state.desktop_host.bitmap
    register_workbench_observation_routes(app,store,settings.data_dir,ROOT)
    register_supervisor_routes(app, settings.data_dir)
    register_conversation_directory(app)
    from apps.api.stage_workflows import register_stage_workflows
    register_stage_workflows(app)
    from apps.api.context_graph import register_context_graph
    register_context_graph(app)
    from apps.api.workflow_kernels import register_workflow_kernels
    register_workflow_kernels(app, settings.data_dir)
    register_owner_shutdown(app, settings.data_dir)
    app.add_middleware(BodyLimitMiddleware)

    @app.middleware("http")
    async def local_boundary(request: Request, call_next):
        if request.headers.get("host") != settings.origin.removeprefix("http://"):
            return error(403, "HOST_DENIED", "Use the configured localhost address")
        origin = request.headers.get("origin")
        if origin and origin != settings.origin:
            return error(403, "ORIGIN_DENIED", "Cross-origin requests are denied")
        if request.headers.get("sec-fetch-site") == "cross-site":
            return error(403, "CROSS_SITE_DENIED", "Cross-site requests are denied")
        path = request.url.path
        plugin_hint = False
        if path == '/api/dynamic-workflow/plugin-events' and request.method == 'POST':
            from packages.agent_sessions.workflow import admitted_event_owner
            plugin_hint = admitted_event_owner(app.state.agent_sessions, request.headers.get('authorization')) is not None
        elif ((path == '/api/dynamic-workflow/plugin-plan' and request.method == 'POST') or
              (path.startswith('/api/dynamic-workflow/plugin-plan/operations/') and request.method == 'GET')):
            from packages.agent_sessions.workflow import admitted_workflow_owner
            plugin_hint = admitted_workflow_owner(app.state.agent_sessions, request.headers.get('authorization'),
                plan=True, read_only=request.method == 'GET') is not None
        if path.startswith("/api/") and path != "/api/bootstrap" and not plugin_hint:
            if not secure_equal(request.cookies.get("eeas_session", ""), token):
                return error(401, "AUTH_REQUIRED", "Open the local EEAS page to establish a session")
        if request.method not in {"GET", "HEAD"} and not plugin_hint:
            if origin != settings.origin or not secure_equal(
                    request.headers.get("x-eeas-csrf", ""), csrf):
                return error(403, "CSRF_DENIED", "A same-origin session and write token are required")
        closer = app.state.owner_shutdown
        try:
            admission = closer.enter(request.method, path)
        except Conflict:
            return error(409, "OWNER_SHUTDOWN_IN_PROGRESS", "Application owners are closing; inspect the recorded operation")
        try:
            response = await call_next(request)
        except ClientDisconnect:
            return _DisconnectedResponse()
        finally:
            closer.leave(admission)
        if (request.method not in {"GET", "HEAD", "OPTIONS"}
                and response.status_code < 400
                and not path.startswith(("/api/supervisor", "/api/owner-shutdown"))):
            # Wake a local record reader, never a model or task retry. Runtime
            # completions additionally signal after their own durable commit.
            try:
                app.state.global_supervisor.wake("OWNER_CHANGED")
            except Exception:
                # Observer storage failure cannot turn a committed mutation
                # into an unknown execution result. Its GET retains diagnostics.
                pass
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        agent_surface = path == "/" and (ROOT / "apps/agent-shell/dist/index.html").is_file() or path.startswith("/agent-shell/")
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; font-src 'self'; worker-src 'self' blob:; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            if agent_surface else
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_input(request, exception):
        # Do not echo raw user data, filesystem paths or secrets.
        details = "; ".join(".".join(str(p) for p in e["loc"]) + ": " + e["msg"]
                            for e in exception.errors())
        return error(422, "INVALID_INPUT", details)

    @app.exception_handler(NotFound)
    async def not_found(request, exception):
        return error(404, "NOT_FOUND", str(exception))

    @app.exception_handler(Conflict)
    async def conflict(request, exception):
        return error(409, "REVISION_CONFLICT", str(exception))

    @app.exception_handler(InvalidInput)
    async def domain_invalid(request, exception):
        return error(422, "INVALID_INPUT", str(exception))

    @app.exception_handler(sqlite3.Error)
    async def database_error(request, exception):
        return error(503, "STORAGE_UNAVAILABLE", "Local storage is unavailable")

    @app.exception_handler(LiveBlocked)
    async def live_blocked(request, exception):
        return JSONResponse({"error": {"code": "LIVE_BLOCKED", "message": "Native review preflight blocked", "reasons": exception.reasons}}, status_code=503)

    @app.get("/health")
    def health():
        with store.connect() as connection:
            migrations = [row[0] for row in connection.execute("SELECT version FROM schema_migrations ORDER BY version")]
        version = max((int(name.split("_", 1)[0]) for name in migrations), default=0)
        return {"status": "ok", "storage": "sqlite", "schema_version": version, "schema_migrations": migrations, "worker": worker.status,
                "execution_scope": "trusted_original_mock_fixtures", "sandbox": "NOT_VALIDATED"}

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        if request.headers.get("x-eeas-bootstrap") != "1":
            return error(403, "BOOTSTRAP_DENIED", "Use the local EEAS client")
        response = JSONResponse({"csrf_token": csrf})
        response.set_cookie("eeas_session", token, httponly=True, samesite="strict", path="/")
        return response

    @app.get("/api/projects")
    def list_projects():
        return {"projects": store.list_projects()}

    @app.post("/api/projects", status_code=201)
    def create_project(body: ProjectInput):
        return store.create_project(body.name, body.description, operation_id=body.operation_id)

    @app.get('/api/project-operations/{operation_id}')
    def project_operation(operation_id: str):
        return store.project_operation(operation_id)

    @app.get("/api/projects/{project_id}")
    def project(project_id: str):
        return store.project(project_id)

    async def import_file(project_id, file, source, document_id=None, expected_revision=0):
        try:
            content = await file.read(MAX_DOCUMENT_BYTES + 1)
            return store.save_document(project_id, file.filename or "", content, source,
                                       document_id, expected_revision)
        finally:
            await file.close()

    @app.post("/api/projects/{project_id}/documents", status_code=201)
    async def upload_document(project_id: str, file: UploadFile = File(),
                              source: str = Form(default="original:manual", max_length=1000)):
        return await import_file(project_id, file, source)

    @app.put("/api/projects/{project_id}/documents/{document_id}")
    async def revise_document(project_id: str, document_id: str, file: UploadFile = File(),
                              expected_revision: int = Form(ge=1),
                              source: str = Form(default="original:manual", max_length=1000)):
        return await import_file(project_id, file, source, document_id, expected_revision)

    @app.get("/api/projects/{project_id}/documents")
    def documents(project_id: str):
        return {"documents": store.documents(project_id)}

    @app.get("/api/projects/{project_id}/documents/{document_id}")
    def document(project_id: str, document_id: str, revision: int | None = Query(default=None, ge=1)):
        row = store.document(project_id, document_id, revision)
        del row["raw_bytes"]
        return row

    @app.get("/api/projects/{project_id}/documents/{document_id}/chunks")
    def document_chunks(project_id: str, document_id: str, revision: int | None = Query(default=None, ge=1),
                        offset: int = Query(default=0, ge=0), limit: int = Query(default=20, ge=1, le=50)):
        return store.document_chunks(project_id, document_id, revision, offset, limit)

    @app.get("/api/projects/{project_id}/documents/{document_id}/structure")
    def document_structure(project_id: str, document_id: str, revision: int | None = Query(default=None, ge=1)):
        doc = store.document(project_id, document_id, revision)
        return {"document_id": document_id, "revision": doc["revision"], "sha256": doc["sha256"],
                "filename": doc["filename"], "current_revision": doc["current_revision"],
                "format": doc.get("format", "legacy-text"), "outline": doc.get("outline", {}),
                "graph": doc.get("graph", {}), "warnings": doc.get("warnings", []),
                "assets": store.document_assets(project_id, document_id, doc["revision"])}

    @app.get("/api/projects/{project_id}/documents/{document_id}/content")
    def document_content(project_id: str, document_id: str, revision: int | None = Query(default=None, ge=1),
                         char_start: int = Query(default=0, ge=0), char_limit: int = Query(default=8000, ge=1, le=32000)):
        return store.document_content(project_id, document_id, revision, char_start, char_limit)

    @app.post("/api/projects/{project_id}/documents/{document_id}/context")
    def document_context(project_id: str, document_id: str, body: DocumentContextInput):
        return store.document_context(project_id, document_id, body.revision, body.chunk_indexes, body.char_budget, body.byte_budget)

    @app.get("/api/projects/{project_id}/documents/{document_id}/assets")
    def document_assets(project_id: str, document_id: str, revision: int | None = Query(default=None, ge=1)):
        doc = store.document(project_id, document_id, revision)
        return {"document_id": document_id, "revision": doc["revision"], "sha256": doc["sha256"],
                "assets": store.document_assets(project_id, document_id, doc["revision"])}

    @app.get("/api/projects/{project_id}/documents/{document_id}/assets/{asset_id}")
    def document_asset(project_id: str, document_id: str, asset_id: str, revision: int = Query(ge=1)):
        asset = store.document_asset(project_id, document_id, revision, asset_id)
        return Response(asset["raw_bytes"], media_type=asset["media_type"],
                        headers={"X-Content-Type-Options": "nosniff", "ETag": '"' + asset["sha256"] + '"',
                                 "Cache-Control": "private, no-store"})

    @app.get("/api/projects/{project_id}/documents/{document_id}/download")
    def download_document(project_id: str, document_id: str, revision: int = Query(ge=1)):
        from urllib.parse import quote
        doc = store.document(project_id, document_id, revision)
        return Response(doc["raw_bytes"], media_type="application/octet-stream", headers={
            "Content-Disposition": "attachment; filename*=UTF-8''" + quote(doc["filename"], safe=""),
            "X-Content-Type-Options": "nosniff", "Cache-Control": "private, no-store"})

    @app.delete("/api/projects/{project_id}/documents/{document_id}")
    def delete_document(project_id: str, document_id: str):
        store.delete_document(project_id, document_id)
        return {"status": "deleted"}

    @app.get("/api/projects/{project_id}/search")
    def search(project_id: str, keyword: str = Query(min_length=1, max_length=200),
               document_id: str | None = None, revision: int | None = Query(default=None, ge=1),
               offset: int = Query(default=0, ge=0), limit: int = Query(default=50, ge=1, le=50)):
        return store.search_page(project_id, keyword, document_id, revision, offset, limit)

    @app.post("/api/projects/{project_id}/requirements", status_code=201)
    def create_requirements(project_id: str, body: RequirementBundle):
        return store.save_requirements(project_id, body.model_dump())

    @app.put("/api/projects/{project_id}/requirements/{requirement_id}")
    def revise_requirements(project_id: str, requirement_id: str, body: RequirementBundle,
                            expected_revision: int = Query(ge=1)):
        return store.save_requirements(project_id, body.model_dump(), requirement_id, expected_revision)

    @app.get("/api/projects/{project_id}/requirements")
    def list_requirements(project_id: str):
        return {"requirements": store.list_requirements(project_id)}

    @app.get("/api/projects/{project_id}/requirements/{requirement_id}")
    def requirement_revision(project_id: str, requirement_id: str, revision: int | None = Query(default=None, ge=1)):
        return store.requirements(project_id, requirement_id, revision)

    @app.post("/api/projects/{project_id}/planning/extract")
    async def extract(project_id: str, body: ExtractInput):
        document = store.document(project_id, body.document_id, body.revision)
        if body.image_asset_ids is not None and not body.include_images:
            raise InvalidInput("Image asset selection requires explicit include_images")
        if body.mode == "live":
            context = store.document_context(project_id, body.document_id, body.revision, body.chunk_indexes)
            images = []
            if body.include_images:
                assets = store.document_assets(project_id, body.document_id, body.revision)
                ids = [asset["asset_id"] for asset in assets] if body.image_asset_ids is None else body.image_asset_ids
                if not ids or len(ids) != len(set(ids)) or any(i not in {asset["asset_id"] for asset in assets} for i in ids):
                    raise InvalidInput("Images must be unique actual assets from the selected document revision")
                images = [{"document_id": body.document_id, "revision": body.revision,
                           "asset_id": asset["asset_id"], "sha256": asset["sha256"]}
                          for asset in assets if asset["asset_id"] in ids]
            try:
                return await bridge.extract(project_id, document, context=context, image_refs=images,
                                            search_enabled=body.search_enabled)
            except LiveBlocked as exception:
                return error(503, "AGENT_NOT_CONNECTED", ", ".join(exception.reasons))
            except (ValueError, TypeError):
                return error(422, "MODEL_PROPOSAL_INVALID", "Model proposal failed schema or citation validation")
        if body.include_images:
            raise InvalidInput("Mock extraction cannot read image contents; use manual text or an explicitly enabled vision profile")
        if body.search_enabled:
            raise InvalidInput("Mock extraction cannot search the web; enable an explicitly configured native search-only MCP profile")
        context = store.document_context(project_id, body.document_id, body.revision, body.chunk_indexes) if body.chunk_indexes is not None else None
        try:
            proposal = await MockPlanner().extract(document, context=context)
        except ValueError as exception:
            return error(422, "EXTRACTION_INPUT_INVALID", "Mock extraction needs valid REQ:/ACCEPT: lines within supported limits")
        return {"mode": "mock", "model_verified": False, "proposal": proposal.model_dump(),
                "coverage": context["coverage"] if context else "COMPLETE",
                "context_sha256": context["context_sha256"] if context else None}

    @app.post("/api/projects/{project_id}/requirements/{requirement_id}/plan")
    def plan(project_id: str, requirement_id: str, body: PlanInput):
        return {"tasks": store.plan(project_id, requirement_id, body.revision)}

    @app.get("/api/projects/{project_id}/planning/tasks")
    def planning_tasks(project_id: str):
        return {"tasks": store.planning_tasks(project_id)}

    @app.post("/api/projects/{project_id}/tasks", status_code=201)
    def create_task(project_id: str, body: TaskInput):
        return store.save_task(project_id, body.model_dump())

    @app.put("/api/projects/{project_id}/tasks/{task_id}")
    def revise_task(project_id: str, task_id: str, body: TaskInput, expected_revision: int = Query(ge=1)):
        return store.save_task(project_id, body.model_dump(), task_id, expected_revision)

    @app.get("/api/projects/{project_id}/tasks")
    def tasks(project_id: str):
        return {"tasks": store.tasks(project_id)}

    @app.get("/api/projects/{project_id}/tasks/{task_id}")
    def task(project_id: str, task_id: str, revision: int | None = Query(default=None, ge=1)):
        return store.task(project_id, task_id, revision)

    @app.post("/api/projects/{project_id}/tasks/{task_id}/runs", status_code=202)
    def start_run(project_id: str, task_id: str, body: StartRunInput):
        return store.start_run(project_id, task_id, body.revision, body.operation_id)

    @app.get("/api/projects/{project_id}/tasks/{task_id}/runs")
    def runs(project_id: str, task_id: str):
        return {"runs": store.runs(project_id, task_id)}

    @app.get("/api/projects/{project_id}/runs/{run_id}")
    def run(project_id: str, run_id: str, include_inputs: bool = True):
        return store.run(project_id, run_id, include_inputs)

    @app.get("/api/projects/{project_id}/runs/{run_id}/events")
    def events(project_id: str, run_id: str, after: int = Query(default=0, ge=0)):
        rows = store.events(project_id, run_id, after)
        return {"events": rows, "next_seq": rows[-1]["event_seq"] if rows else after}

    @app.post("/api/projects/{project_id}/runs/{run_id}/cancel")
    def cancel(project_id: str, run_id: str):
        return store.cancel(project_id, run_id)

    @app.get("/api/projects/{project_id}/runs/{run_id}/runner")
    def inspect_runner(project_id: str, run_id: str):
        from packages.runner.fixture import TrustedFixtureRunner
        from packages.runner.supervisor import matching_process
        fact = TrustedFixtureRunner(store, settings.data_dir).inspect(store.run(project_id, run_id))
        job, receipt = fact.get("job") or {}, fact.get("receipt") or {}
        supervisor = receipt.get("supervisor") or {"pid": job.get("supervisor_pid"), "create_time": job.get("supervisor_created")}
        child = receipt.get("child") or {"pid": job.get("pid"), "create_time": job.get("child_created")}
        return {"state": fact["state"], "reason": fact.get("reason"), "phase": fact.get("phase"),
                "job_id": job.get("id"), "supervisor": supervisor, "supervisor_alive": bool(matching_process(supervisor)),
                "child": child, "child_alive": bool(matching_process(child)), "exit_code": receipt.get("exit_code"),
                "error_type": receipt.get("error_type"), "replay_allowed": False}

    @app.get("/api/runtime")
    def runtime():
        return bridge.describe_capabilities()

    @app.get("/api/runner")
    def runner_capabilities():
        from packages.runner.fixture import TrustedFixtureRunner
        return TrustedFixtureRunner.capabilities()

    @app.get("/api/extensions")
    def list_extensions():
        return {"extensions": extensions.list(), "sdk_plugin_live": "NOT_VALIDATED"}

    @app.post("/api/extensions", status_code=201)
    async def import_extension(file: UploadFile = File()):
        if not file.filename or not file.filename.lower().endswith(".zip"):
            raise InvalidInput("Import a local extension ZIP archive")
        return extensions.import_snapshot(await file.read(1024 * 1024 + 1))

    @app.post("/api/extensions/{snapshot_id}/qualify")
    def qualify_extension(snapshot_id: str):
        return extensions.qualify(snapshot_id)

    @app.post("/api/extensions/{snapshot_id}/review")
    def review_extension(snapshot_id: str):
        return extensions.review(snapshot_id)

    @app.post("/api/extension-reviews/{review_id}/approve")
    def approve_extension(review_id: str, body: ExtensionApproval):
        return extensions.approve(review_id, body.action_hash, body.permissions)

    @app.post("/api/projects/{project_id}/extension-binding")
    def pin_extension(project_id: str, body: ExtensionPin):
        return extensions.pin(project_id, body.snapshot_id)

    @app.get("/api/projects/{project_id}/extension-binding")
    def extension_binding(project_id: str):
        return {"binding": extensions.binding(project_id)}

    @app.get("/api/projects/{project_id}/runs/{run_id}/logs/{stream}")
    def runner_log(project_id: str, run_id: str, stream: str):
        from hashlib import sha256
        if stream not in {"stdout", "stderr"}:
            raise NotFound("Unknown log stream")
        run = store.run(project_id, run_id, include_inputs=False)
        workspace = (settings.data_dir / run["workspace"]).resolve()
        path = workspace / (stream + ".txt")
        if not workspace.is_relative_to(settings.data_dir.resolve()) or path.is_symlink() or not path.resolve().is_relative_to(workspace):
            raise InvalidInput("Log path escaped the workspace")
        if not path.is_file():
            raise NotFound("Runner has not produced this log")
        with path.open("rb") as source:
            raw = source.read(65537)
        if len(raw) > 65536:
            raise InvalidInput("Runner log exceeds the stored output limit")
        return {"stream": stream, "text": raw.decode("utf-8", errors="replace"),
                "sha256": sha256(raw).hexdigest(), "bytes": len(raw),
                "snapshot": run["execution_status"] in {"RETURNED", "FAILED", "CANCELLED", "INTERRUPTED"},
                "output_limited": run["error_type"] == "OUTPUT_LIMIT"}

    @app.get("/api/projects/{project_id}/runs/{run_id}/results")
    def run_results(project_id: str, run_id: str):
        run = store.run(project_id, run_id)
        spec = json.loads(run["frozen_inputs"])["spec"]
        with store.connect() as connection:
            proof = connection.execute("SELECT id FROM verification_evidence WHERE run_id=? ORDER BY created_at DESC LIMIT 1", (run_id,)).fetchone()
        return {"run_id": run_id, "task_revision": run["task_revision"], "budget": spec["budget"],
                "usage": {"tokens": None, "cost": None}, "source_kind": "synthetic", "runtime_kind": "mock",
                "artifact_hash": verifier.artifacts(run), "evidence": verifier.evidence(project_id, proof["id"]) if proof else None,
                "limitations": ["Original fixture validation only", "Windows native/OS sandbox/live model NOT_VALIDATED"]}

    @app.get("/api/projects/{project_id}/evidence/{evidence_id}/download")
    def export_evidence(project_id: str, evidence_id: str):
        response = JSONResponse(verifier.evidence(project_id, evidence_id))
        response.headers["Content-Disposition"] = 'attachment; filename="eeas-evidence.json"'
        return response

    @app.get("/api/projects/{project_id}/agent-executions/{execution_id}")
    def agent_execution(project_id: str, execution_id: str):
        return bridge.inspect(project_id, execution_id)

    @app.get("/api/projects/{project_id}/agent-executions/{execution_id}/result")
    def agent_result(project_id: str, execution_id: str):
        return bridge.export_result(project_id, execution_id)

    @app.get("/api/projects/{project_id}/tasks/{task_id}/runs/{run_id}/native-review-scope")
    def native_review_scope(project_id: str, task_id: str, run_id: str):
        return native_reviews.public_scope(project_id, task_id, run_id)

    @app.post("/api/projects/{project_id}/tasks/{task_id}/runs/{run_id}/native-reviews", status_code=202)
    async def native_review_submit(project_id: str, task_id: str, run_id: str, body: NativeTaskSubmit):
        return await native_reviews.submit(project_id, task_id, run_id, body.model_dump())

    @app.get("/api/projects/{project_id}/native-reviews/{review_id}")
    def native_review_inspect(project_id: str, review_id: str):
        return native_reviews.inspect(project_id, review_id)

    @app.get("/api/projects/{project_id}/native-reviews/{review_id}/events")
    def native_review_events(project_id: str, review_id: str, after: int = Query(default=0, ge=0)):
        return {"events": native_reviews.events(project_id, review_id, after)}

    @app.get("/api/projects/{project_id}/native-reviews/{review_id}/result")
    def native_review_result(project_id: str, review_id: str):
        response = JSONResponse(native_reviews.export(project_id, review_id))
        response.headers["Content-Disposition"] = 'attachment; filename="eeas-task-native-review.json"'
        return response

    @app.post("/api/projects/{project_id}/native-reviews/{review_id}/cancel")
    async def native_review_cancel(project_id: str, review_id: str):
        return await native_reviews.cancel(project_id, review_id)

    @app.post("/api/projects/{project_id}/native-reviews/{review_id}/{kind}", status_code=202)
    async def native_review_control(project_id: str, review_id: str, kind: Literal["resume", "steer", "fork"], body: NativeTaskControl):
        return await native_reviews.control(project_id, review_id, kind, body.model_dump())

    @app.get("/api/projects/{project_id}/agent-executions/{execution_id}/events")
    def agent_events(project_id: str, execution_id: str, after: int = Query(default=0, ge=0)):
        return {"events": bridge.stream_events(project_id, execution_id, after)}

    @app.post("/api/projects/{project_id}/agent-executions/{execution_id}/cancel")
    async def agent_cancel(project_id: str, execution_id: str):
        return await bridge.request_cancel(project_id, execution_id)

    @app.post("/api/projects/{project_id}/runs/{run_id}/verify")
    def verify(project_id: str, run_id: str):
        return verifier.verify(project_id, run_id)

    @app.get("/api/projects/{project_id}/evidence/{evidence_id}")
    def evidence(project_id: str, evidence_id: str):
        return verifier.evidence(project_id, evidence_id)

    @app.get("/api/projects/{project_id}/runs/{run_id}/artifacts/{filename:path}")
    def artifact(project_id: str, run_id: str, filename: str):
        return FileResponse(verifier.fetch_artifact(project_id, run_id, filename), filename=Path(filename).name)

    @app.post("/api/projects/{project_id}/runs/{run_id}/repair", status_code=202)
    def repair(project_id: str, run_id: str, body: RepairInput):
        return repair_coordinator.request(project_id, run_id, body.max_repairs)

    @app.get("/api/projects/{project_id}/repair-sessions/{session_id}")
    def repair_session(project_id: str, session_id: str):
        return repair_coordinator.inspect(project_id, session_id)

    @app.get("/api/projects/{project_id}/approvals")
    def list_approvals(project_id: str):
        return {"approvals": approvals.list(project_id)}

    @app.post("/api/projects/{project_id}/approvals/{approval_id}/decision")
    def decide_approval(project_id: str, approval_id: str, body: ApprovalDecision):
        return approvals.decide(project_id, approval_id, body.action_hash, body.approve)

    @app.post("/api/shutdown")
    async def stop():
        if shutdown is None:
            return error(409, "STOP_UNAVAILABLE", "Stop this embedded instance through its owner")
        receipt = await app.state.owner_shutdown.close()
        if receipt["status"] != "CLOSED":
            return {"status": "CLOSE_UNKNOWN", "owner_shutdown": receipt}
        # Starlette runs the callback only after sending the complete body.
        return JSONResponse({"status": "stopping", "owner_shutdown": receipt}, background=BackgroundTask(shutdown))

    web = ROOT / "apps/web"
    app.mount("/assets", StaticFiles(directory=web / "assets"), name="assets")
    agent = ROOT / "apps/agent-shell/dist"
    if agent.is_dir():
        app.mount("/agent-shell",StaticFiles(directory=agent,html=True),name="agent-shell")

    @app.get("/legacy")
    def legacy_index():
        return FileResponse(web / "index.html")

    @app.get("/")
    def index():
        return FileResponse(agent / "index.html" if (agent / "index.html").is_file() else web / "index.html")

    return app
