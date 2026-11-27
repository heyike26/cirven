"""Execute an approved test in a locally configured Linux OCI image.

The administrator chooses an installed Docker/Podman executable and immutable
image ID. Only the argv following our container entrypoint is model supplied.
No package installation, network, host shell, user project mount or daemon
socket is available to the command inside the container.
"""
from __future__ import annotations

import csv
import hashlib
from io import StringIO
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import threading
import time
import uuid

from packages.dynamic_workflow.kernel_settings import home
from packages.dynamic_workflow.stage_assertions import MAX_EXPECTED_SUITE_OUTPUT, action_digest, build_descriptor_ok, resolve_case_action, suite_input_pair_valid, suite_subject_path_valid
from packages.dynamic_workflow.stage_contracts import OUTPUT_CONTRACT_VERSIONS, container_target_issue
from packages.dynamic_workflow.stage_workspace import _path, _record, _scope, _visible, ledger, MAX_STAGE

MAX_CAPTURE = 64 * 1024
MAX_OUTPUT = 2 * 1024 * 1024
_OWNER_LABEL = "io.cirven.workflow.suite"
# Independent of the host client: if its broker exits abruptly the container
# still has a deadline. $@ is an argv vector, never interpolated shell source.
_BOOT = ('set -eu; mkdir /work/project; cp -R /input/. /work/project/; cd /work/project; chmod -R u+w .; '
         'duration="$1"; shift; exec timeout -k 3 "$duration" "$@"')


_SUBJECT_BOOT = (
    'set -eu; duration="$1"; root="$2"; work="$3"; subject="$root/$4"; expected="$5"; marker="$6"; shift 6; '
    'sum=$(sha256sum < "$subject"); '
    'test "${sum%% *}" = "$expected" || { printf "%s\\n" "Approved program digest mismatch" >&2; exit 125; }; '
    'mkdir "$work"; cp -R "$root"/. "$work"/; cd "$work"; chmod -R u+w .; '
    'printf "%s\\n" "$marker"; exec timeout -k 3 "$duration" "$subject" "$@"')


def _admin():
    setting = os.environ.get("CIRVEN_WORKFLOW_CONTAINER_EXE", "")
    image = os.environ.get("CIRVEN_WORKFLOW_CONTAINER_IMAGE", "")
    exe = Path(setting)
    if (not setting or not exe.is_absolute() or not exe.is_file() or not os.access(exe, os.X_OK)
            or exe.name.lower() not in {"docker", "docker.exe", "podman", "podman.exe"}
            or not re.fullmatch("sha256:[0-9a-f]{64}", image)):
        raise ValueError("管理员需指定已安装的 Docker/Podman 路径和本机镜像完整 SHA-256 ID")
    return str(exe), image


def _copy_inputs(ctx, folder, output_versions=None, *, subject=None):
    total, manifest = 0, []
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN")
        visible = _visible(db, ctx)
        if subject and visible.get(subject["path"].casefold())!=(subject["path"], subject["sha256"]):
            raise ValueError("被测程序与已审核开发产物版本不符，请恢复原版本")
        entries = sorted(visible.values())
        if len(entries) > 1024:
            raise ValueError("测试输入文件数量超过节点限制")
        omitted = {path.casefold():path for path in output_versions or {}}
        for name, sha in entries:
            if name.casefold() in omitted:
                if name != omitted[name.casefold()]:
                    raise ValueError("输出路径大小写与原文件不同")
                output_versions[name] = sha
                continue
            target = folder / _path(name)
            row = db.execute("SELECT content FROM blobs WHERE sha=?", (sha,)).fetchone()
            if row is None or hashlib.sha256(row[0]).hexdigest() != sha:
                raise ValueError("获准测试源的版本校验失败")
            total += len(row[0])
            if total > MAX_STAGE:
                raise ValueError("测试输入文件总量超过节点限制")
            target.parent.mkdir(parents=True, exist_ok=True)
            with target.open("xb") as stream:
                stream.write(row[0])
            target.chmod(0o555 if subject and name==subject["path"] else 0o444)
            manifest.append([name, sha])
    for directory, _, _ in os.walk(folder):
        os.chmod(directory, 0o755)
    return manifest


def _host_env(private, exe):
    # This env belongs to the trusted local CLI. The container receives only
    # the explicit --env flags, not the CLI environment or its socket.
    allowed = ("SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "PATH", "PATHEXT", "XDG_RUNTIME_DIR", "LANG")
    tmp = str(private)
    env = {**{k:os.environ[k] for k in allowed if k in os.environ}, "HOME":tmp, "USERPROFILE":tmp,
            "DOCKER_CONFIG":tmp, "REGISTRY_AUTH_FILE":str(private / "empty-auth.json"),
            "TMP":tmp, "TEMP":tmp, "TMPDIR":tmp}
    if os.name == "posix" and Path(exe).name == "podman" and os.getuid() != 0:
        # A fresh HOME would otherwise select an empty rootless image store.
        # These engine paths are never mounted into the test container.
        import pwd
        account = Path(pwd.getpwuid(os.getuid()).pw_dir)
        env["XDG_DATA_HOME"] = str(account / ".local/share")
        env.setdefault("XDG_RUNTIME_DIR", "/run/user/"+str(os.getuid()))
        storage_config = account / ".config/containers/storage.conf"
        if storage_config.is_file():
            env["CONTAINERS_STORAGE_CONF"] = str(storage_config)
    return env


def _cli(argv, env, deadline, *, capture=MAX_CAPTURE, wire_limit=MAX_OUTPUT, binary=False, stdin=subprocess.DEVNULL):
    process = subprocess.Popen(argv, cwd=env["TMPDIR"], env=env, stdin=stdin,
                               stdout=subprocess.PIPE, stderr=subprocess.DEVNULL if binary else subprocess.STDOUT, shell=False)
    kept, stop, seen = bytearray(), threading.Event(), 0

    def drain():
        nonlocal seen
        with process.stdout as stream:
            while chunk := stream.read(16384):
                seen += len(chunk)
                kept.extend(chunk[:max(0, capture - len(kept))])
                if seen > wire_limit:
                    stop.set()

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    reason = None
    try:
        while process.poll() is None:
            if stop.wait(0.05):
                reason = "output_limit"
                break
            if time.monotonic() > deadline:
                reason = "timeout"
                break
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=10)
        reader.join(timeout=2)
    return process.returncode, bytes(kept), reason or ("output_limit" if seen > wire_limit else None), seen > len(kept)


def _image(exe, image, env):
    status, data, limit, truncated = _cli([exe, "image", "inspect", image], env, time.monotonic()+15,
                                          capture=512*1024)
    if status or limit or truncated:
        raise ValueError("未找到可核查的本机固定镜像，请检查本地容器引擎和镜像 ID")
    try:
        info, = json.loads(data)
        identity = info.get("Id", info.get("ID", ""))
        if not identity.startswith("sha256:"):
            identity = "sha256:" + identity
        if (identity != image or (info.get("Os") or info.get("OS")) != "linux"
                or (info.get("Config") or {}).get("Volumes")):
            raise ValueError("镜像必须是所选固定版本的 Linux 镜像，且不能声明额外可写卷")
    except (TypeError, KeyError, json.JSONDecodeError, AttributeError) as exc:
        raise ValueError("无法核对容器镜像信息") from exc


def _owned_cleanup(exe, name, env):
    # Never delete a foreign container, including one that claimed the name.
    probe = [exe, "container", "ls", "--all", "--quiet", "--no-trunc", "--filter",
             "label="+_OWNER_LABEL+"="+name, "--filter", "name="+name]
    code, output, reason, truncated = _cli(probe, env, time.monotonic()+10)
    if code or reason or truncated:
        return False
    ids = output.decode("ascii", "replace").splitlines()
    if not ids:
        return True
    if len(ids) != 1 or not re.fullmatch("[0-9a-f]{64}", ids[0]):
        return False
    code, _, reason, _ = _cli([exe, "container", "rm", "--force", "--volumes", ids[0]], env,
                              time.monotonic()+12)
    if code or reason:
        # --rm may have finished between list and rm.
        code, output, reason, cut = _cli(probe, env, time.monotonic()+8)
        return not code and not output.strip() and not reason and not cut
    return True


def _mount(source, target="/input"):
    # Docker and Podman parse --mount as a CSV record; quote the whole option
    # when the path includes a comma.
    value = StringIO()
    csv.writer(value, lineterminator="\n").writerow(["type=bind", "source="+str(source), "target="+target, "readonly"])
    return value.getvalue().rstrip("\n")


def _reserve(ctx, count=1):
    with ledger(ctx["_data_dir"]) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("CREATE TABLE IF NOT EXISTS suite_counts(scope TEXT,op TEXT,count INTEGER NOT NULL,PRIMARY KEY(scope,op))")
        key = (_scope(ctx), ctx["operation_id"])
        row = db.execute("SELECT count FROM suite_counts WHERE scope=? AND op=?", key).fetchone()
        if (row[0] if row else 0) + count > 32:
            raise ValueError("本次执行的容器测试已达次数上限")
        db.execute("INSERT INTO suite_counts VALUES(?,?,?) ON CONFLICT(scope,op) DO UPDATE SET count=count+excluded.count",
                   (*key,count))


def _container(exe, name, inputs):
    command = [exe, "run", "--rm", "--name", name, "--label", _OWNER_LABEL+"="+name,
                   "--pull=never", "--network=none", "--read-only", "--user=65534:65534",
                   "--cap-drop=ALL", "--security-opt=no-new-privileges", "--pids-limit=128",
                   "--cpus=2", "--memory=512m", "--memory-swap=512m", "--ipc=private",
                   "--no-healthcheck", "--log-driver=none",
                   "--mount", _mount(inputs),
                   "--tmpfs", "/work:rw,nosuid,nodev,exec,size=192m,mode=1777",
                   "--tmpfs", "/tmp:rw,nosuid,nodev,noexec,size=32m,mode=1777",
                   "--env", "HOME=/tmp", "--workdir=/work", 
    ]
    if Path(exe).name.lower() in {"podman","podman.exe"}:
        command.insert(2,"--http-proxy=false")
    return command


def _suite_observation(exe, image, inputs, env, argv, timeout_seconds, expected, stdin=None, *, subject=None):
    name = "cirven-suite-"+uuid.uuid4().hex
    command = _container(exe,name,inputs)
    if stdin is not None:
        command.append("--interactive")
    marker = f"cirven-subject-{uuid.uuid4().hex}" if subject else None
    command += ["--entrypoint=/bin/sh",image,"-c",_SUBJECT_BOOT if subject else _BOOT,"cirven-suite",str(timeout_seconds)]
    if subject:
        command += ["/input","/work/project",subject["path"],subject["sha256"],marker,*argv[1:]]
    else:
        command += argv
    try:
        deadline = time.monotonic()+timeout_seconds+10
        if stdin is None:
            code, data, limit, truncated = _cli(command,env,deadline)
        else:
            with tempfile.TemporaryFile(dir=env["TMPDIR"]) as stream:
                stream.write(stdin)
                stream.seek(0)
                code, data, limit, truncated = _cli(command,env,deadline,stdin=stream)
    finally:
        cleaned = _owned_cleanup(exe,name,env)
    if not cleaned:
        raise RuntimeError("容器终态未确认，请核查该节点原操作："+name)
    if limit is None and code == 124:
        limit = "command_timeout_or_exit_124"
    verified = False
    if marker:
        prefix = marker.encode("ascii")+b"\n"
        verified = data.startswith(prefix)
        if verified:
            data = data[len(prefix):]
        else:
            limit = limit or "unconfirmed_subject"
    result = {"container":name,"exit_code":code,"limit":limit,"output_truncated":truncated,
              "detail":data.decode("utf-8","replace")}
    if verified:
        result["subject"] = subject
    if stdin is not None:
        result["input_sha256"] = hashlib.sha256(stdin).hexdigest()
    if expected:
        result.update(expected_output_sha256=hashlib.sha256(expected).hexdigest(),
                      output_matches=not truncated and data==expected)
        if not truncated:
            result["observed_output_sha256"] = hashlib.sha256(data).hexdigest()
    return result


def run_suite(ctx, case_id, argv: list[str], timeout_seconds=120) -> dict:
    if "run_check" not in ctx.get("capabilities", {}).get("tools", []):
        raise ValueError("本节点未启用测试工具")
    cases = ctx.get("approved_by_kind", {}).get("cases", {}).get("items", [])
    if not case_id or not any(row.get("id")==case_id for row in cases):
        raise ValueError("请选择当前节点获准的测试用例")
    if (not isinstance(argv, list) or not 1 <= len(argv) <= 128 or
            any(not isinstance(arg, str) or "\x00" in arg or len(arg)>4096 for arg in argv)
            or not argv[0].strip() or argv[0].startswith("-") or sum(map(len,argv)) > 16384):
        raise ValueError("容器测试需要命令及有界参数列表")
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300:
        raise ValueError("容器测试超时范围为 1 到 300 秒")
    exe, image = _admin()
    action = resolve_case_action(ctx,case_id,approved_fields=("expected_output","input_pair","subject_path"),method="container_suite",
                                 argv=argv,image_id=image,timeout_seconds=timeout_seconds)
    if ctx.get("require_runtime_target"):
        case = next(row for row in ctx["approved_by_kind"]["cases"]["items"] if row["id"]==case_id)
        if issue := container_target_issue(case,action):
            raise ValueError(issue)
    binding = {"action_id":action["id"],"action_sha256":action_digest(action)}
    expected = action.get("expected_output")
    expected_bytes = expected.encode("utf-8") if isinstance(expected,str) else b""
    if "expected_output" in action or ctx.get("contract_version") in OUTPUT_CONTRACT_VERSIONS:
        if not expected_bytes.strip() or not expected.strip() or len(expected_bytes)>MAX_EXPECTED_SUITE_OUTPUT:
            raise ValueError("已审容器验收动作缺少有界、非空的可观察输出")
    pair = action.get("input_pair")
    if "input_pair" in action and not suite_input_pair_valid(action):
        raise ValueError("已审对照动作需要不同的两组标准输入和不同的可观察输出")
    subject = None
    if "subject_path" in action:
        path = action["subject_path"]
        if not suite_subject_path_valid(path,argv):
            raise ValueError("容器验收命令须直接启动已审程序")
        subject = ctx.get("approved_subjects",{}).get(path)
        if not subject or subject.get("path")!=path:
            raise ValueError("未找到此程序对应的唯一已审核开发产物")
    _reserve(ctx,2 if pair else 1)
    root = home(ctx["_data_dir"])
    with tempfile.TemporaryDirectory(dir=root, prefix="suite-") as scratch:
        folder = Path(scratch)
        env = _host_env(folder, exe)
        (folder / "empty-auth.json").write_text('{"auths":{}}',encoding="utf-8")
        inputs = folder / "input"
        inputs.mkdir()
        manifest = _copy_inputs(ctx, inputs, subject=subject)
        _image(exe, image, env)
        stdin = pair["normal_stdin"].encode("utf-8") if pair else None
        result = _suite_observation(exe,image,inputs,env,argv,timeout_seconds,expected_bytes,stdin,subject=subject)
        passed = result["exit_code"]==0 and result["limit"] is None and result.get("output_matches",True)
        if pair:
            control = _suite_observation(exe,image,inputs,env,argv,timeout_seconds,
                         pair["control_expected_output"].encode("utf-8"),pair["control_stdin"].encode("utf-8"),subject=subject)
            control["expected_exit_code"] = pair["control_exit_code"]
            result["control"] = control
            passed = (passed and control["exit_code"]==control["expected_exit_code"]
                      and control["output_matches"] and control["limit"] is None)
        source_hash = action_digest(manifest)
        args_hash = action_digest(argv)
        with ledger(ctx["_data_dir"]) as db:
            if passed:
                source_bytes = json.dumps(manifest,ensure_ascii=False,separators=(",",":")).encode()
                db.execute("INSERT OR IGNORE INTO blobs VALUES(?,?)",(source_hash,source_bytes))
            return _record(db, ctx, "check", case_id=case_id, method="container_suite", **binding,
                argv=argv, argv_sha256=args_hash, image_id=image, source_manifest_sha256=source_hash,
                input_file_count=len(manifest), source_manifest_retained=passed, timeout_seconds=timeout_seconds,
                status="passed" if passed else "failed", **result)


def _unpack_build(wire, code, limit, outputs):
    from packages.dynamic_workflow.stage_build_output import MAGIC, MAX_FILE, MAX_TOTAL
    if limit or not wire.startswith(MAGIC) or len(wire) < len(MAGIC)+4:
        raise ValueError("构建未返回完整的二进制成果记录，请检查固定镜像的 Python 3 运行环境")
    pos = len(MAGIC)
    length = int.from_bytes(wire[pos:pos+4], "big")
    pos += 4
    if length > 512*1024 or len(wire)-pos < length:
        raise ValueError("构建结果头部长度无效")
    metadata = json.loads(wire[pos:pos+length])
    pos += length
    if (not isinstance(metadata,dict) or set(metadata)!={"exit_code","limit","detail","output_truncated","files"}
            or metadata["limit"] not in (None,"timeout","output_limit","collection_error")
            or (metadata["exit_code"] is not None and type(metadata["exit_code"]) is not int)
            or not isinstance(metadata.get("files"),list) or
            not isinstance(metadata.get("detail"),str) or len(metadata["detail"])>MAX_CAPTURE or
            type(metadata.get("output_truncated")) is not bool):
        raise ValueError("构建执行结果格式无效")
    if code or metadata.get("limit") or type(metadata.get("exit_code")) is not int or metadata["exit_code"]!=0:
        if metadata["files"] or len(wire)!=pos:
            raise ValueError("失败的构建不能提供成果文件")
        return metadata, {}
    if [meta.get("path") if isinstance(meta,dict) else None for meta in metadata["files"]]!=outputs:
        raise ValueError("构建返回的文件与审核清单不一致")
    blobs, total = {}, 0
    for meta in metadata["files"]:
        count = meta.get("size")
        if type(count) is not int or not 0<=count<=MAX_FILE or count>len(wire)-pos or total+count>MAX_TOTAL:
            raise ValueError("构建文件大小无效或超过限制")
        raw = wire[pos:pos+count]
        if hashlib.sha256(raw).hexdigest()!=meta.get("sha256"):
            raise ValueError("构建成果物摘要不匹配")
        blobs[meta["path"]] = raw
        pos += count
        total += count
    if pos != len(wire):
        raise ValueError("构建返回了额外的未声明内容")
    return metadata, blobs


def build_artifacts(ctx, design_id):
    from packages.dynamic_workflow.stage_build_output import MAX_TOTAL
    from packages.dynamic_workflow.stage_workspace import file_write_batch
    if "build_artifacts" not in ctx.get("capabilities",{}).get("tools",[]):
        raise ValueError("本节点未启用二进制构建工具")
    designs = ctx.get("approved_by_kind",{}).get("design",{}).get("items",[])
    matches = [row for row in designs if row.get("id")==design_id]
    if len(matches)!=1 or not build_descriptor_ok(matches[0].get("build")):
        raise ValueError("此设计项没有已审构建命令和输出清单")
    build = matches[0]["build"]
    exe, image = _admin()
    if image!=build["image_id"]:
        raise ValueError("管理员当前容器镜像与已审设计不符")
    _reserve(ctx)
    root = home(ctx["_data_dir"])
    with tempfile.TemporaryDirectory(dir=root,prefix="build-") as scratch:
        folder = Path(scratch)
        env = _host_env(folder,exe)
        (folder/"empty-auth.json").write_text('{"auths":{}}',encoding="utf-8")
        _image(exe,image,env)
        inputs = folder/"input"
        inputs.mkdir()
        old = dict.fromkeys(build["outputs"], "")
        manifest = _copy_inputs(ctx,inputs,old)
        request = folder/"request.json"
        request.write_text(json.dumps(build,ensure_ascii=False),encoding="utf-8")
        request.chmod(0o444)
        helper = Path(__file__).with_name("stage_build_output.py").resolve()
        name = "cirven-build-"+uuid.uuid4().hex
        command = _container(exe,name,inputs) + ["--mount",_mount(helper,"/cirven/collector.py"),
                   "--mount",_mount(request,"/cirven/request.json"),"--entrypoint=python3",image,
                   "-I","-B","/cirven/collector.py","/cirven/request.json"]
        budget = MAX_TOTAL+512*1024+64
        try:
            code, wire, limit, cut = _cli(command,env,time.monotonic()+build["timeout_seconds"]+15,
                                          capture=budget,wire_limit=budget,binary=True)
        finally:
            cleaned = _owned_cleanup(exe,name,env)
        if not cleaned:
            raise RuntimeError("构建容器终态未确认，请核查原操作："+name)
        try:
            meta, blobs = _unpack_build(wire,code,limit or ("output_limit" if cut else None),build["outputs"])
        except (ValueError,UnicodeError,TypeError,KeyError):
            meta, blobs = {"exit_code":code,"detail":"未得到可校验的完整成果物，请检查构建镜像、命令及输出。",
                           "limit":limit or "invalid_build_output","output_truncated":cut}, {}
        from packages.domain.store import Conflict
        try:
            files = file_write_batch(ctx,[(path,raw,old[path]) for path,raw in blobs.items()]) if blobs else []
        except (ValueError,Conflict):
            files = []
            meta.update(limit="output_conflict",detail="成果物的保存版本或容量已变化，未采用本次输出")

        with ledger(ctx["_data_dir"]) as db:
            receipt = _record(db,ctx,"build",design_id=design_id,descriptor_sha256=action_digest(build),
                      source_manifest_sha256=action_digest(manifest),source_files=manifest,image_id=image,argv_sha256=action_digest(build["argv"]),
                      status="passed" if files else "failed",exit_code=meta["exit_code"],limit=meta["limit"],
                      detail=meta["detail"],output_truncated=meta["output_truncated"],container=name,
                      file_refs=[r["id"] for r in files],outputs=[{k:r[k] for k in ("id","path","sha256")} for r in files])
        return {"build":receipt,"files":files}
