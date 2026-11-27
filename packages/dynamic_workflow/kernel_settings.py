"""Owned runtime selections and version changes for engineering workflow nodes."""
from __future__ import annotations

from functools import lru_cache
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import tempfile
from contextlib import contextmanager
from urllib.parse import urlsplit

from packages.adapters.opencode_profile import binary_hash
from packages.domain.native_capabilities import NativeCapabilities
from packages.domain.store import Conflict, InvalidInput

ROOT = Path(__file__).resolve().parents[2]
ENGINES = ("opencode", "claude-code")
TOOLS = {"workspace_list": "查看本步文件", "workspace_read": "读取文件",
         "workspace_write": "写入工程文件", "run_check": "执行已审测试",
         "package_files":"打包交付文件", "build_artifacts":"构建已审成果物",
         "delegate": "委派独立分析"}
DEFAULT_ROUTE = {"endpoint": "", "model": "", "credential_env": "", "allow_http": False}


def home(data_dir):
    return Path(data_dir) / "workflow-kernels"


@contextmanager
def database(data_dir):
    root = home(data_dir)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    connection = sqlite3.connect(root / "settings.sqlite3", timeout=15)
    try:
        connection.row_factory = sqlite3.Row
        connection.executescript("""
        CREATE TABLE IF NOT EXISTS settings(engine TEXT PRIMARY KEY, rev INTEGER NOT NULL, route TEXT NOT NULL,active TEXT);
        CREATE TABLE IF NOT EXISTS versions(engine TEXT, digest TEXT, version TEXT NOT NULL, PRIMARY KEY(engine,digest));
        """)
        with connection:
            yield connection
    finally:
        connection.close()


def engine_name(name):
    if name not in ENGINES:
        raise InvalidInput("请选择 OpenCode 或 Claude Code")
    return name


@lru_cache(maxsize=32)
def _cached_hash(path,device,inode,size,mtime_ns,ctime_ns):
    return binary_hash(path)


def executable_digest(path):
    if not path or not path.is_file():return None
    st=path.stat()
    return _cached_hash(path,st.st_dev,st.st_ino,st.st_size,st.st_mtime_ns,st.st_ctime_ns)


def bundled(engine):
    exe = "opencode" if engine == "opencode" else "claude"
    suffix = ".exe" if os.name == "nt" else ""
    choices = [ROOT / "runtime" / exe / (exe + suffix)]
    if engine=="opencode" and os.name=="posix":
        candidate=ROOT/"var"/"opencode-ui-offline-guard"/"opencode"
        if candidate.is_file():
            from packages.adapters.opencode_profile import CLI_SHA
            if executable_digest(candidate)==CLI_SHA["posix"]:choices.append(candidate)
    if engine == "claude-code":
        spec = importlib.util.find_spec("claude_agent_sdk")
        if spec and spec.origin:
            choices.append(Path(spec.origin).parent / "_bundled" / (exe + suffix))
    return next((p for p in choices if p.is_file()), None)


def _row(db, engine):
    row = db.execute("SELECT * FROM settings WHERE engine=?", (engine,)).fetchone()
    return (row["rev"], json.loads(row["route"]), row["active"]) if row else (0, DEFAULT_ROUTE.copy(), None)


def _set(db, engine, revision, route, active):
    if _row(db, engine)[0] != revision:
        raise Conflict("内核配置已更新，请刷新后再保存")
    db.execute("INSERT OR REPLACE INTO settings VALUES(?,?,?,?)",
               (engine, revision + 1, json.dumps(route), active))


def select_binary(data_dir, engine, active):
    if not active:
        return bundled(engine)
    return home(data_dir) / "versions" / engine / active / ("kernel.exe" if os.name == "nt" else "kernel")


def runtime(data_dir, engine):
    with database(data_dir) as db:
        rev, route, active = _row(db, engine_name(engine))
        _,company,_=_row(db,"company-policy")
        if company.get("required") and engine!="claude-code":raise ValueError("公司内网模式仅允许 Claude Code，请更改当前节点内核")
    executable = select_binary(data_dir, engine, active)
    if not executable or not executable.is_file() or (active and binary_hash(executable) != active):
        raise ValueError("所选内核文件缺失或已改变，请在运行内核中选择可用版本")
    if not route["endpoint"] or not route["model"]:
        raise ValueError("请在运行内核中设置本步使用的网关地址和模型")
    secret = os.environ.get(route["credential_env"], "") if route["credential_env"] else ""
    if route["credential_env"] and not secret:
        raise ValueError("服务进程未读取到指定的网关凭据环境变量")
    return executable, {**route, "secret": secret}, rev


def save_route(data_dir, engine, revision, route):
    engine_name(engine)
    address, model, variable = (route[k].strip() for k in ("endpoint","model","credential_env"))
    url = urlsplit(address)
    if (url.scheme not in ("http","https") or not url.hostname or url.username is not None or
            url.password is not None or url.query or url.fragment or any(ord(c) <= 32 for c in address) or
            (url.scheme == "http" and url.hostname not in ("localhost","127.0.0.1","::1") and not route["allow_http"])):
        raise InvalidInput("请输入 HTTPS 网关地址；内网 HTTP 需要显式启用")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,159}", model):
        raise InvalidInput("请输入有效的模型路由名称")
    if variable and not re.fullmatch(r"CIRVEN_[A-Z0-9_]{1,80}", variable):
        raise InvalidInput("凭据环境变量名称必须以 CIRVEN_ 开头")
    with database(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        _set(db, engine, revision, dict(endpoint=address.rstrip("/"), model=model,
             credential_env=variable, allow_http=route["allow_http"]), _row(db, engine)[2])


def install(data_dir, engine, revision, filename, expected, version):
    engine_name(engine)
    source = Path(filename)
    if (not source.is_absolute() or not source.is_file() or not 0 < source.stat().st_size <= 768 * 1024**2
            or not re.fullmatch(r"[0-9a-f]{64}",expected) or not re.fullmatch(r"[A-Za-z0-9_.+-]{1,80}",version)):
        raise InvalidInput("请选择已下载的原厂可执行文件、版本与 SHA-256")
    dest = select_binary(data_dir, engine, expected)
    dest.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.NamedTemporaryFile(dir=dest.parent,delete=False) as stream:
        tmp = Path(stream.name)
    try:
        with source.open("rb") as inp,tmp.open("wb") as out:
            shutil.copyfileobj(inp,out,1024**2)
        if binary_hash(tmp) != expected:
            raise InvalidInput("内核文件与提供的 SHA-256 不符")
        # Only publish verified immutable bytes; a failed CAS never switches the active version.
        with database(data_dir) as db:
            db.execute("BEGIN IMMEDIATE")
            _, route, _ = _row(db,engine)
            _set(db,engine,revision,route,expected)
            if not dest.exists() or binary_hash(dest) != expected:
                tmp.chmod(0o700)
                os.replace(tmp,dest)
            db.execute("INSERT OR IGNORE INTO versions VALUES(?,?,?)",(engine,expected,version))
    finally:
        tmp.unlink(missing_ok=True)


def activate(data_dir,engine,revision,selected):
    engine_name(engine)
    with database(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        if selected is not None and not db.execute("SELECT 1 FROM versions WHERE engine=? AND digest=?",
                                                   (engine,selected)).fetchone():
            raise InvalidInput("未找到此内核版本")
        binary=select_binary(data_dir,engine,selected)
        if not binary or not binary.is_file() or (selected and binary_hash(binary)!=selected):
            raise InvalidInput("内核文件未就绪")
        _set(db,engine,revision,_row(db,engine)[1],selected)


def catalog(data_dir):
    registry=NativeCapabilities(None,data_dir)
    index=registry._index()
    caps={"tools":[{"id":key,"name":name} for key,name in TOOLS.items()]}
    for kind in ("skills","mcp"):
        caps[kind]=[{"id":key,"name":key,"revision":ref["revision"],"sha256":ref["sha256"]}
                    for key,ref in sorted(index[kind].items())
                    if registry._configuration(kind,ref)["configuration"].get("enabled")]
    with database(data_dir) as db:
        kernels=[]
        for engine in ENGINES:
            rev,route,active=_row(db,engine)
            binary=select_binary(data_dir,engine,active)
            versions=[dict(row) for row in db.execute("SELECT digest,version FROM versions WHERE engine=? ORDER BY rowid DESC",(engine,))]
            kernels.append(dict(id=engine,revision=rev,route=route,active=active,versions=versions,
                                available=bool(binary and binary.is_file()),bundled=bool(bundled(engine)),
                                credential_ready=bool(route["credential_env"] and os.environ.get(route["credential_env"]))))
        policy_rev,policy,_=_row(db,"company-policy")
    return {"kernels":kernels,"capabilities":caps,"company":{"revision":policy_rev,"required":bool(policy.get("required"))}}


def selected_capabilities(data_dir, grants):
    registry=NativeCapabilities(None,data_dir)
    index=registry._index()
    skills,servers,refs={},{},{}
    tools=list(grants.get("tools",[]))
    if set(tools)-TOOLS.keys():raise ValueError("节点使用了未登记的工具")
    for kind in ("skills","mcp"):
        refs[kind]={}
        for key in grants.get(kind,[]):
            ref=index[kind].get(key)
            if not ref:raise ValueError("节点选择的插件已移除："+key)
            refs[kind][key]=ref
            if kind=="skills":
                skill=registry.skill_content(key)
                if skill["config_ref"]!=ref or not skill["enabled"]:raise ValueError("节点 Skill 已改变或停用："+key)
                skills[key]=skill["content"]
            else:
                item=registry.mcp_content(ref)
                if item["integrity"]!="CURRENT" or not item["configuration"]["enabled"]:
                    raise ValueError("节点 MCP 已改变或停用："+key)
                servers[key]=item["configuration"]
    return skills,servers,tools,refs


def freeze_policy(data_dir,node):
    with database(data_dir) as db:
        revision,_,active=_row(db,engine_name(node["kernel"]))
        company_rev,_,_=_row(db,"company-policy")
    _,_,_,refs=selected_capabilities(data_dir,node["capabilities"])
    return {"kernel_revision":revision,"capability_refs":refs,"company_revision":company_rev,
            "executable_sha256":executable_digest(select_binary(data_dir,node["kernel"],active))}


def set_company(data_dir,revision,required):
    with database(data_dir) as db:
        db.execute("BEGIN IMMEDIATE")
        _set(db,"company-policy",revision,{"required":required},None)


def company_required(data_dir):
    with database(data_dir) as db:
        return bool(_row(db,"company-policy")[1].get("required"))
