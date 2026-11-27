"""Node-scoped engineering files and host-observed check receipts."""
from __future__ import annotations

import ast
import hashlib
import json
from pathlib import Path
import re
import sqlite3
from contextlib import closing, contextmanager
from datetime import datetime, timezone

from packages.dynamic_workflow.kernel_settings import home
from packages.extensions.schema import safe_member

MAX_FILE = 2 * 1024**2
MAX_BINARY = 32 * 1024**2
MAX_STAGE = 256 * 1024**2


def _hash(data):
    return hashlib.sha256(data).hexdigest()


@contextmanager
def ledger(data_dir):
    root=home(data_dir)
    root.mkdir(parents=True,exist_ok=True,mode=0o700)
    db=sqlite3.connect(root/"artifacts.sqlite3",timeout=12)
    db.row_factory=sqlite3.Row
    try:
        db.executescript("""
          CREATE TABLE IF NOT EXISTS blobs(sha TEXT PRIMARY KEY, content BLOB NOT NULL);
          CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, scope TEXT NOT NULL, op TEXT NOT NULL, record TEXT NOT NULL);
          CREATE INDEX IF NOT EXISTS receipt_operation ON receipts(scope,op);
          CREATE TABLE IF NOT EXISTS stage_files(scope TEXT NOT NULL, path TEXT NOT NULL COLLATE NOCASE, sha TEXT NOT NULL,PRIMARY KEY(scope,path));
          CREATE TABLE IF NOT EXISTS delegates(scope TEXT PRIMARY KEY, count INTEGER NOT NULL);
        """)
        with db:
            yield db
    finally:
        db.close()


def stage_scope(project_id, run_id, stage_session_id):
    return _hash(json.dumps([project_id, run_id, stage_session_id]).encode())


def _scope(ctx):
    return stage_scope(ctx["project_id"], ctx["run_id"], ctx["stage_session_id"])


def _record(db,ctx,kind,**fields):
    scope=_scope(ctx)
    op=ctx["operation_id"]
    # An observed event is distinct even when content repeats.
    from uuid import uuid4
    key=uuid4().hex
    record=dict(id=key,kind=kind,recorded_at=datetime.now(timezone.utc).isoformat(),**fields)
    db.execute("INSERT INTO receipts VALUES(?,?,?,?)",(key,scope,op,json.dumps(record,ensure_ascii=False)))
    return record


def _path(name):
    if not isinstance(name,str) or not safe_member(name) or any(part.startswith(".") for part in name.split("/")):
        raise ValueError("文件路径必须是本阶段内的普通相对路径")
    return name


def _verified(db,e):
    if not isinstance(e,dict) or not isinstance(e.get("id"),str):return None
    row=db.execute("SELECT record FROM receipts WHERE id=?",(e["id"],)).fetchone()
    if row:
        actual=json.loads(row[0])
        if actual==e:return actual
    return None


def _visible(db,ctx):
    files={}
    for e in ctx.get("approved_evidence",[]):
        if e.get("kind")=="file" and _verified(db,e):
            key=e["path"].casefold()
            if key in files and files[key][1]!=e["sha256"]:
                raise ValueError("同一路径有多个获批版本，请选择唯一的上游版本")
            files[key]=(e["path"],e["sha256"])
    for row in db.execute("SELECT path,sha FROM stage_files WHERE scope=?",(_scope(ctx),)):
        files[row[0].casefold()]=(row[0],row[1])
    return files


def file_list(ctx):
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN")
        return [{"path":p,"sha256":s} for p,s in sorted(_visible(db,ctx).values())]


def _file(db,ctx,path,*,visible=None):
    path=_path(path)
    pair=(_visible(db,ctx) if visible is None else visible).get(path.casefold())
    if not pair:raise ValueError("当前节点没有此文件")
    data=db.execute("SELECT content FROM blobs WHERE sha=?",(pair[1],)).fetchone()
    if not data or _hash(data[0])!=pair[1]:raise ValueError("文件内容校验失败")
    return pair[0],pair[1],data[0]


def file_read(ctx,path):
    with ledger(ctx["_data_dir"]) as db:
        name,sha,data=_file(db,ctx,path)
        try:
            text=data.decode("utf-8") if len(data)<=MAX_FILE else None
        except UnicodeError:
            text=None
        return {"path":name,"sha256":sha,"size":len(data),"content":text,"encoding":"utf-8" if text is not None else "binary"}


def file_write(ctx,path,content,expected_sha256):
    data=content.encode("utf-8")
    if len(data)>MAX_FILE:raise ValueError("文件超过文本单文件上限")
    return file_write_bytes(ctx,path,data,expected_sha256)


def file_write_bytes(ctx,path,raw,expected_sha):
    return file_write_batch(ctx,[(path,raw,expected_sha)])[0]


def file_write_batch(ctx,entries):
    items=list(entries)
    if not 1<=len(items)<=256:raise ValueError("输出文件数量无效")
    scope=_scope(ctx)
    written=[]
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN IMMEDIATE")
        files=_visible(db,ctx)
        retained={r[0]:r[1] for r in db.execute(
            "SELECT sha,length(content) FROM blobs WHERE sha IN (SELECT DISTINCT json_extract(record,'$.sha256') "
            "FROM receipts WHERE scope=? AND json_extract(record,'$.kind')='file')",(scope,))}
        seen=set()
        for path,data,expected in items:
            key=_path(path).casefold()
            if key in seen or not isinstance(data,bytes) or len(data)>MAX_BINARY:
                raise ValueError("输出路径重复或文件大小超限")
            seen.add(key)
            prior=files.get(key)
            if prior and prior[0]!=path:raise ValueError("文件路径与已有路径仅大小写不同")
            if expected!=(prior[1] if prior else ""):raise ValueError("文件已改变，请先读取当前版本")
            sha=_hash(data)
            retained[sha]=len(data)
            files[key]=(path,sha)
            if len(files)>1024 or sum(retained.values())>MAX_STAGE:raise ValueError("本阶段文件数量或容量已达上限")
            db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?)",(sha,data))
            db.execute("INSERT OR REPLACE INTO stage_files VALUES(?,?,?)",(scope,path,sha))
            written.append(_record(db,ctx,"file",path=path,sha256=sha,size=len(data),
                                   previous_sha256=prior[1] if prior else None))
    return written


def package_files(ctx,path,members,expected_sha256=""):
    from io import BytesIO
    from zipfile import ZipFile,ZipInfo,ZIP_DEFLATED
    path=_path(path)
    if (not path.casefold().endswith(".zip") or not 1<=len(members)<=256 or
            len(set(members))!=len(members) or path.casefold() in {m.casefold() for m in members}):
        raise ValueError("请指定 ZIP 名称及唯一的交付文件清单")
    buffer=BytesIO()
    total=0
    with ledger(ctx["_data_dir"]) as db,ZipFile(buffer,"w") as archive:
        db.execute("BEGIN")
        visible=_visible(db,ctx)
        inserted=set()
        for member in sorted(members):
            name,_,data=_file(db,ctx,member,visible=visible)
            total+=len(data)
            if name.casefold() in inserted or total>MAX_BINARY:raise ValueError("交付内容过大或成员重复")
            inserted.add(name.casefold())
            info=ZipInfo(name,date_time=(1980,1,1,0,0,0))
            info.compress_type=ZIP_DEFLATED
            info.create_system=3
            info.external_attr=0o100644 << 16
            archive.writestr(info,data)
    return file_write_bytes(ctx,path,buffer.getvalue(),expected_sha256)


def check(ctx,case_id,path,mode,expected):
    from packages.dynamic_workflow.stage_assertions import bind_case_action
    binding=bind_case_action(ctx,case_id,method=mode,path=path,expected=expected)
    with ledger(ctx["_data_dir"]) as db:
        name,sha,data=_file(db,ctx,path)

        message=""
        try:
            if mode=="python_syntax":ast.parse(data.decode("utf-8"),filename=name)
            elif mode=="json_valid": json.loads(data)
            elif mode=="sha256_equals":
                if not re.fullmatch(r"[0-9a-f]{64}",expected):raise ValueError("需要完整 SHA-256")
                if sha!=expected:message="摘要不匹配"
            elif mode=="text_equals":
                if data.decode("utf-8")!=expected:message="内容与预期不符"
            else:raise ValueError("未知的检查方式")
        except (SyntaxError,UnicodeError,json.JSONDecodeError) as error:message=type(error).__name__
        return _record(db,ctx,"check",path=name,sha256=sha,case_id=case_id,method=mode,**binding,
                       exit_code=int(bool(message)),status="failed" if message else "passed",detail=message)


def _operation_records(db, ctx):
    records = [json.loads(row[0]) for row in db.execute(
        "SELECT record FROM receipts WHERE scope=? AND op=? ORDER BY rowid", (_scope(ctx), ctx["operation_id"]))]
    # Keep the original dispatch order, even when concurrent replies finish out of order.
    replies = {r["connector_dispatch_id"]: r for r in records if "connector_dispatch_id" in r}
    return [replies.get(r["id"], r) for r in records if "connector_dispatch_id" not in r]


def operation_receipts(ctx, *, read_only=False):
    if read_only:
        path=home(ctx["_data_dir"])/"artifacts.sqlite3"
        if not path.is_file():
            return []
        with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro",uri=True,timeout=12)) as db:
            if not db.execute("SELECT 1 FROM sqlite_master WHERE name='receipts'").fetchone():
                return []
            return _operation_records(db,ctx)
    with ledger(ctx["_data_dir"]) as db:
        return _operation_records(db, ctx)


def file_receipt_origin(data_dir, evidence_id):
    """Resolve a file's original scope and operation without creating a ledger."""
    path = home(data_dir) / "artifacts.sqlite3"
    if not path.is_file():
        return None
    with closing(sqlite3.connect(path.resolve().as_uri()+"?mode=ro", uri=True, timeout=12)) as db:
        row = db.execute("SELECT scope,op,record FROM receipts WHERE id=?", (evidence_id,)).fetchone()
    if row is None:
        return None
    source_scope, operation, body = row
    record = json.loads(body)
    if isinstance(record, dict) and record.get("id") == evidence_id and record.get("kind") == "file":
        return record, source_scope, operation
    return None


def read_evidence_file(data_dir,record):
    with ledger(data_dir) as db:
        if not _verified(db,record) or record.get("kind")!="file":raise ValueError("没有可下载的已验证文件")
        row=db.execute("SELECT content FROM blobs WHERE sha=?",(record["sha256"],)).fetchone()
        if not row or _hash(row[0])!=record["sha256"]:raise ValueError("交付文件校验失败")
        return Path(record["path"]).name,row[0]


def suite_input_files(data_dir, records):
    """Read retained input manifests from verified suite receipts."""
    if not records:
        return
    with ledger(data_dir) as db:
        db.execute("BEGIN")
        for record in records:
            sha=record.get("source_manifest_sha256")
            if not sha or not _verified(db,record):
                raise ValueError("容器测试的原输入回执无法核对")
            row=db.execute("SELECT content FROM blobs WHERE sha=?",(sha,)).fetchone()
            if row is None:
                if record.get("source_manifest_retained"):
                    raise ValueError("容器测试的原输入清单无法读取")
                continue
            if _hash(row[0])!=sha:
                raise ValueError("容器测试的原输入清单校验失败")
            manifest=json.loads(row[0])
            if (not isinstance(manifest,list) or len(manifest)!=record.get("input_file_count")
                    or any(not isinstance(pair,list) or len(pair)!=2 or not isinstance(pair[0],str)
                           or not isinstance(pair[1],str) for pair in manifest)):
                raise ValueError("容器测试的原输入清单格式无效")
            yield from manifest


def claim_delegate(ctx):
    limit=ctx["max_subagents"]
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN IMMEDIATE")
        scope=_scope(ctx)
        row=db.execute("SELECT count FROM delegates WHERE scope=?",(scope,)).fetchone()
        count=row[0] if row else 0
        if not 0<=count<limit:raise ValueError("本节点的子 Agent 名额已用完")
        db.execute("INSERT OR REPLACE INTO delegates VALUES(?,?)",(scope,count+1))


def complete_design_item(ctx,design_id,file_receipts):
    from packages.dynamic_workflow.stage_assertions import design_output_issue
    from packages.dynamic_workflow.stage_contracts import DESIGN_FILES_CONTRACT_VERSIONS, TRACE_CONTRACT_VERSIONS
    design=next((row for row in ctx.get("approved_by_kind",{}).get("design",{}).get("items",[])
                 if row["id"]==design_id),None)
    if design is None or not file_receipts or len(file_receipts)>256 or len(set(file_receipts))!=len(file_receipts):
        raise ValueError("请引用已审设计编号及对应的文件回执")
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN IMMEDIATE")
        visible=_visible(db,ctx)
        paths=[]
        version=ctx.get("contract_version")
        # A rework session can reuse the exact previously approved files it inherited.
        inherited=set(ctx.get("prior_file_ids", []))
        inherited.update(r["id"] for r in ctx.get("prior_evidence", []) if r.get("kind")=="file")
        for ident in file_receipts:
            row=db.execute("SELECT record FROM receipts WHERE id=? AND scope=?", (ident,_scope(ctx))).fetchone()
            if row is None and version in DESIGN_FILES_CONTRACT_VERSIONS and ident not in inherited:
                raise ValueError("开发项须使用本阶段或本次返工承接的已审文件回执")
            r=json.loads(row[0]) if row else next((record for e in ctx.get("approved_evidence",[])
                        if e.get("id")==ident and (record:=_verified(db,e))),{})
            if r.get("kind")!="file" or visible.get(r["path"].casefold())!=(r["path"],r["sha256"]):
                raise ValueError("开发项引用的文件回执已过期或不属于此节点")
            paths.append(r["path"])
        if version in TRACE_CONTRACT_VERSIONS and (issue:=design_output_issue(design,paths)):
            raise ValueError(issue)
        return _record(db,ctx,"design_item",design_id=design_id,file_refs=file_receipts)
