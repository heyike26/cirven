"""stdio MCP bridge launched by the selected kernel in its private working directory."""
from __future__ import annotations

import hashlib
import json
import inspect
import sqlite3
from functools import wraps
from pathlib import Path
import sys
from threading import Lock

if __package__ in (None, ""):
    sys.path.insert(0,str(Path(__file__).resolve().parents[2]))

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import CallToolResult
from packages.dynamic_workflow import stage_workspace as workspace

TOOL_CALL_LIMIT = 256
REPEATED_ERROR_LIMIT = 3


class _ToolBudget:
    def __init__(self, marker):
        self.marker = marker
        self.lock = Lock()
        self.calls = 0
        self.failure = None
        self.repeated = 0
        self.stopped = False

    def _stop(self, reason):
        self.stopped = True
        if self.marker is not None:
            temporary = self.marker.with_suffix(".tmp")
            temporary.write_text(reason, encoding="ascii")
            temporary.replace(self.marker)

    def begin(self):
        with self.lock:
            if self.stopped:
                raise ToolError("本次执行已达到工具停止条件")
            if self.calls >= TOOL_CALL_LIMIT:
                self._stop("tool-count")
                raise ToolError("本次执行的工具调用已达上限")
            self.calls += 1

    def failed(self, signature):
        with self.lock:
            if self.stopped:
                return
            self.repeated = self.repeated + 1 if self.failure == signature else 1
            self.failure = signature
            if self.repeated >= REPEATED_ERROR_LIMIT:
                self._stop("repeated-error")

    def reset_failure(self):
        with self.lock:
            self.failure = None
            self.repeated = 0


def check_owner(ctx):
    owner=ctx.get("_owner")
    if owner is None:return
    from packages.dynamic_workflow.stage_runs import _alive, _checked
    if not _alive(owner):
        raise ValueError("原执行进程已结束，工具授权已撤回")
    url=Path(ctx["_database"]).resolve().as_uri()+"?mode=ro"
    db=sqlite3.connect(url,uri=True,timeout=5)
    db.row_factory=sqlite3.Row
    try:
        run=_checked(db.execute("SELECT body,sha256 FROM stage_workflow_runs WHERE id=? AND project_id=?",
                                (ctx["run_id"],ctx["project_id"])).fetchone())
        current=next((s for s in run["stages"] if s["node_id"]==run["active_node_id"]),{})
        if (current.get("stage_session_id")!=ctx["stage_session_id"] or current.get("worker")!=owner or
            current.get("operation_id")!=ctx["operation_id"] or current.get("status")!="running" or
            current.get("stop_request")):
            raise ValueError("本次执行已结束或当前节点已更换")
    finally:
        db.close()


def _guard(ctx,handler,budget):
    if inspect.iscoroutinefunction(handler):
        @wraps(handler)
        async def asynchronous(*args,**kwargs):
            check_owner(ctx)
            budget.begin()
            # An external result may reflect a real device changing state.
            budget.reset_failure()
            return await handler(*args,**kwargs)
        return asynchronous
    signature = inspect.signature(handler)
    @wraps(handler)
    def synchronous(*args,**kwargs):
        check_owner(ctx)
        budget.begin()
        try:
            result = handler(*args,**kwargs)
        except ValueError as exc:
            arguments = signature.bind(*args,**kwargs)
            arguments.apply_defaults()
            message = str(exc)[:600]
            raw = json.dumps([handler.__name__,arguments.arguments,message],sort_keys=True,ensure_ascii=True)
            budget.failed(hashlib.sha256(raw.encode()).hexdigest())
            raise ToolError(message) from exc
        budget.reset_failure()
        return result
    return synchronous


def create_server(ctx, marker=None):
    server=MCPServer("cirven")
    allowed=set(ctx["tool_names"])
    budget=_ToolBudget(marker)

    def workspace_list() -> list[dict]:
        """List this stage's working file versions and explicitly approved upstream files."""
        return workspace.file_list(ctx)

    def workspace_read(path:str) -> dict:
        """Read an allowed relative path. The digest identifies the exact content."""
        return workspace.file_read(ctx,path)

    def workspace_write(path:str,content:str,expected_sha256:str="") -> dict:
        """Write UTF-8 content. Pass the previous digest when updating. Use returned id as file evidence."""
        return workspace.file_write(ctx,path,content,expected_sha256)

    def complete_design_item(design_id:str,file_receipts:list[str]) -> dict:
        """Close one approved design item against the verified current file receipt IDs; cite this design_item id in evidence_refs."""
        return workspace.complete_design_item(ctx,design_id,file_receipts)

    def run_check(case_id:str,path:str,mode:str,expected:str="") -> dict:
        """Check actual file bytes. Modes: python_syntax, json_valid, sha256_equals, text_equals."""
        return workspace.check(ctx,case_id,path,mode,expected)

    def run_suite(case_id:str,argv:list[str],timeout_seconds:int=120) -> dict:
        """Run the approved container command. The host applies approved standard input, expectations and any contrast input from the case."""
        from packages.dynamic_workflow.stage_suite import run_suite as run
        return run(ctx,case_id,argv,timeout_seconds)

    def package_files(path:str,members:list[str],expected_sha256:str="") -> dict:
        """Package exactly the listed visible files into a deterministic ZIP; returns the actual file receipt and digest."""
        return workspace.package_files(ctx,path,members,expected_sha256)

    def build_artifacts(design_id:str) -> dict:
        """Execute the approved design item's build recipe in the configured container and collect its named output files."""
        from packages.dynamic_workflow.stage_suite import build_artifacts as build
        return build(ctx,design_id)

    def delegate(question:str) -> str:
        """Request one independent analysis using the node's kernel. This consumes one child slot."""
        if not 0<len(question)<=16000:raise ValueError("子任务问题过长或为空")
        workspace.claim_delegate(ctx)
        from packages.dynamic_workflow.stage_kernel import _launch
        return _launch(ctx,question,delegate=True)

    async def list_connector_tools(server:str) -> dict:
        """List tools from one approved external MCP, including registered computer-use and laboratory adapters."""
        from packages.dynamic_workflow.stage_connectors import list_connector_tools as listing
        return await listing(ctx,server)

    async def call_connector(server:str,tool:str,arguments:dict,case_id:str="",check_pointer:str="",expected_json:str="") -> CallToolResult:
        """Call one selected MCP tool. To check a test case supply an approved case id, JSON pointer and expected JSON."""
        from packages.dynamic_workflow.stage_connectors import as_tool_result, call_connector as calling
        return as_tool_result(await calling(ctx,server,tool,arguments,case_id,check_pointer,expected_json))

    for handler in (workspace_list,workspace_read,workspace_write,complete_design_item,run_check,run_suite,package_files,build_artifacts,delegate,list_connector_tools,call_connector):
        if handler.__name__ in allowed:server.tool()(_guard(ctx,handler,budget))
    return server


if __name__=="__main__":
    policy = Path(sys.argv[1])
    create_server(json.loads(policy.read_text(encoding="utf-8")),policy.with_suffix(".limit")).run()
