"""Bind a host check and its receipt to an exact action in an approved test case."""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from datetime import datetime
from functools import cached_property
from itertools import combinations
from pathlib import PurePosixPath

from packages.extensions.schema import safe_member

MANUAL_METHOD = "manual_observation"
CHECK_KINDS = {"check", "manual_check"}
FILE_CHECK_METHODS = {"python_syntax", "json_valid", "sha256_equals", "text_equals"}
MAX_EXPECTED_SUITE_OUTPUT = 32 * 1024
READBACK_ISSUES = {
    "missing_readback": "MCP 未返回设备编号、采样时间或读值",
    "wrong_device": "MCP 回读设备与已审设备不一致",
    "invalid_sample_time": "MCP 采样时间缺少可核对的时区或格式",
    "outside_dispatch": "MCP 采样时间不在本次调用的请求与返回之间，请重新采样",
}
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)", re.ASCII)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def action_digest(action):
    return hashlib.sha256(_canonical(action).encode()).hexdigest()


def pointer_tokens(pointer):
    if not isinstance(pointer,str) or not pointer.startswith("/") or len(pointer)>1024 or re.search(r"~(?![01])", pointer):
        raise ValueError("无效的 JSON Pointer")
    return tuple(s.replace("~1", "/").replace("~0", "~") for s in pointer[1:].split("/"))


def readback_descriptor_ok(action):
    plan = action.get("hardware_readback")
    if not isinstance(plan,dict) or set(plan)!={"device_id","device_id_pointer","sample_time_pointer"}:
        return False
    device = plan["device_id"]
    if (not isinstance(device,str) or not device.strip() or len(device)>160
            or any(ord(c)<32 or ord(c)==127 for c in device)):
        return False
    try:
        paths = [pointer_tokens(pointer) for pointer in
                 (action["pointer"], plan["device_id_pointer"], plan["sample_time_pointer"])]
    except ValueError:
        return False
    return all(a[:len(b)]!=b and b[:len(a)]!=a for a,b in combinations(paths,2))


def sample_time(value):
    if isinstance(value,str) and len(value)<=32 and _TIMESTAMP.fullmatch(value):
        try:
            return datetime.fromisoformat(value.replace("Z","+00:00"))
        except ValueError:
            pass
    return None


def readback_in_window(receipt):
    try:
        start, sampled, end = (sample_time(receipt[key]) for key in ("dispatched_at","sampled_at","received_at"))
        return start is not None and sampled is not None and end is not None and start <= sampled <= end
    except (KeyError, TypeError, OverflowError):
        return False


def _json_value(text):
    def pairs(items):
        obj = dict(items)
        if len(obj) != len(items):
            raise ValueError("预期 JSON 包含重复字段")
        return obj
    return _canonical(json.loads(text, object_pairs_hook=pairs))


def action_meaning(action):
    value = {key:val for key,val in action.items() if key != "id"}
    if value["method"] == "mcp_response":
        value["expected_json"] = _json_value(value["expected_json"])
    return _canonical(value)


def action_observation(action):
    """Identify the subject and stimulus used for coverage, separately from receipt identity."""
    method = action["method"]
    if method == "container_suite":
        pair = action.get("input_pair")
        inputs = sorted((pair["normal_stdin"], pair["control_stdin"])) if pair else []
        observed = [action["image_id"], action["argv"], inputs]
    elif method == "mcp_response":
        observed = [action["server"], action["tool"], action["arguments"], action["pointer"]]
    elif method == MANUAL_METHOD:
        observed = [action["instruction"].strip()]
    else:
        observed = [action["path"].casefold()]
    return _canonical([method, observed])


def suite_input_pair_valid(action):
    pair = action.get("input_pair")
    if not isinstance(pair,dict) or set(pair)!={"normal_stdin","control_stdin","control_expected_output","control_exit_code"}:
        return False
    normal, control, expected = (pair[key] for key in ("normal_stdin","control_stdin","control_expected_output"))
    code = pair["control_exit_code"]
    return (type(code) is int and 0<=code<=255 and code not in (124,125,126,127) and
            all(isinstance(value,str) and len(value.encode("utf-8"))<=MAX_EXPECTED_SUITE_OUTPUT
                for value in (normal,control,expected)) and
            normal!=control and bool(expected.strip()) and expected!=action.get("expected_output"))


def suite_subject_path_valid(path, argv):
    return (isinstance(path,str) and safe_member(path) and
            not any(part.startswith(".") for part in path.split("/")) and
            bool(argv) and argv[0]=="./"+path)


def case_action_issues(case):
    actions = case.get("actions", [])
    if len({a["id"] for a in actions}) != len(actions):
        return [f"{case['id']} 的验收动作编号重复"]
    try:
        invocations = set()
        for action in actions:
            method = action["method"]
            if method == "mcp_response":
                pointer_tokens(action["pointer"])
                if "hardware_readback" in action and not readback_descriptor_ok(action):
                    return [f"{case['id']} / {action['id']} 的设备编号、采样时间和读值需要各自独立的回读字段"]
                expected = _json_value(action["expected_json"])
                if "hardware_readback" in action and expected=="null":
                    return [f"{case['id']} / {action['id']} 的设备回读需要明确的非空预期读值"]
                if len(expected.encode()) > 65536 or len(_canonical(action["arguments"]).encode()) > 65536:
                    raise ValueError
            elif method == "container_suite":
                if (not action["argv"][0].strip() or action["argv"][0].startswith("-")
                        or any("\x00" in arg for arg in action["argv"]) or sum(map(len,action["argv"])) > 16384):
                    raise ValueError
                if "expected_output" in action:
                    expected = action["expected_output"]
                    if not expected.strip() or len(expected.encode("utf-8")) > MAX_EXPECTED_SUITE_OUTPUT:
                        return [f"{case['id']} / {action['id']} 需要不超过 32 KiB 的非空可观察输出"]
                    invocation = action_meaning({k:v for k,v in action.items()
                                                 if k not in ("expected_output","input_pair","subject_path")})
                    if invocation in invocations:
                        return [f"{case['id']} / {action['id']} 与同用例的容器命令重复，请分配不同的可执行输入"]
                    invocations.add(invocation)
                if "input_pair" in action and not suite_input_pair_valid(action):
                    return [f"{case['id']} / {action['id']} 需要不同的两组标准输入和不同的可观察输出"]
                if "subject_path" in action and not suite_subject_path_valid(action["subject_path"],action["argv"]):
                    return [f"{case['id']} / {action['id']} 的命令必须直接启动指定的项目产物"]
            elif method != MANUAL_METHOD:
                if not safe_member(action["path"]) or any(seg.startswith(".") for seg in action["path"].split("/")):
                    raise ValueError
                if method in ("json_valid","python_syntax") and action["expected"] != "":
                    raise ValueError
                if method == "sha256_equals" and not re.fullmatch(r"[a-f0-9]{64}", action["expected"]):
                    raise ValueError
        if len({action_meaning({key:value for key,value in action.items() if key!="hardware_readback"})
                for action in actions}) != len(actions):
            return [f"{case['id']} 的验收动作内容重复"]
    except (TypeError, ValueError, RecursionError):
        return [f"{case['id']} 的验收动作需要有效的目标、参数和明确预期"]
    return []


def resolve_case_action(ctx, case_id, *, approved_fields=(), **observed):
    if observed.get("method") == MANUAL_METHOD:
        raise ValueError("人工实测只能由当前阶段的用户登记")
    cases = ctx.get("approved_by_kind", {}).get("cases", {}).get("items", [])
    matches = [case for case in cases if case.get("id") == case_id]
    if len(matches) != 1:
        raise ValueError("用例编号不在本节点已审测试集内或存在歧义")
    try:
        wanted = action_meaning(observed)
        matched = [action for action in matches[0].get("actions", [])
                   if action_meaning({key:value for key,value in action.items() if key not in approved_fields})==wanted]
    except (KeyError, ValueError, TypeError, RecursionError) as exc:
        raise ValueError("无法识别已审用例的验收动作") from exc
    if len(matched) != 1:
        raise ValueError("测试目标、命令或预期与本用例已审动作不一致")
    return matched[0]


def bind_case_action(ctx, case_id, **observed):
    action = resolve_case_action(ctx, case_id, **observed)
    return {"action_id":action["id"],"action_sha256":action_digest(action)}


def passing_check(record):
    if record.get("kind") == "manual_check":
        return (record.get("status") == "passed" and record.get("source") == "local_user"
                and bool(record.get("observation")) and bool(record.get("environment")))
    return record.get("status") == "passed" and type(record.get("exit_code")) is int and record["exit_code"] == 0


def _latest_action_records(records):
    return {(r.get("case_id"), r.get("action_id"), r.get("action_sha256")): r
            for r in records if r.get("kind") in CHECK_KINDS}


def _action_record(latest, case, action):
    record = latest.get((case["id"], action["id"], action_digest(action)))
    if record is None:
        return None
    if action["method"] == MANUAL_METHOD:
        return record if (record.get("kind") == "manual_check" and
                          record.get("case_sha256") == action_digest(case)) else None
    if record.get("kind") != "check":
        return None
    if "hardware_readback" in action and record.get("status")=="passed":
        receipt = record.get("hardware_readback")
        expected = hashlib.sha256(_json_value(action["expected_json"]).encode()).hexdigest()
        if (record.get("method")!="mcp_response" or not record.get("connector_dispatch_id")
                or record.get("readback_issue") or not isinstance(receipt,dict)
                or receipt.get("source")!="connector_reported"
                or receipt.get("device_id")!=action["hardware_readback"]["device_id"]
                or not readback_in_window(receipt) or record.get("observed_sha256")!=expected
                or record.get("expected_sha256")!=expected):
            return None
    if "expected_output" in action and record.get("status")=="passed":
        expected = hashlib.sha256(action["expected_output"].encode("utf-8")).hexdigest()
        if (record.get("method")!="container_suite" or record.get("output_matches") is not True
                or record.get("output_truncated") is not False
                or record.get("expected_output_sha256")!=expected or record.get("observed_output_sha256")!=expected):
            return None
        if subject_path := action.get("subject_path"):
            subject = record.get("subject")
            if not isinstance(subject,dict) or subject.get("path")!=subject_path:
                return None
            if action.get("input_pair") and (record.get("control") or {}).get("subject")!=subject:
                return None
        if pair := action.get("input_pair"):
            contrast = record.get("control") or {}
            control_expected = hashlib.sha256(pair["control_expected_output"].encode("utf-8")).hexdigest()
            if (record.get("input_sha256")!=hashlib.sha256(pair["normal_stdin"].encode("utf-8")).hexdigest()
                    or contrast.get("input_sha256")!=hashlib.sha256(pair["control_stdin"].encode("utf-8")).hexdigest()
                    or contrast.get("output_matches") is not True or contrast.get("output_truncated") is not False
                    or contrast.get("expected_output_sha256")!=control_expected
                    or contrast.get("observed_output_sha256")!=control_expected
                    or type(contrast.get("exit_code")) is not int or contrast["exit_code"]!=pair["control_exit_code"]
                    or contrast.get("expected_exit_code")!=pair["control_exit_code"]
                    or contrast.get("limit") is not None):
                return None
    return record


def action_receipts(case, records):
    """Select the same latest action receipts used by the approval gate."""
    latest = _latest_action_records(records)
    return {action["id"]: _action_record(latest, case, action) for action in case.get("actions", [])}


class TestEvidence:
    """Correlate passing actions with the current visible and approved inputs."""

    def __init__(self, records, upstream, visible, subjects=None):
        self.latest = _latest_action_records(records)
        self.approved = {row["path"].casefold():(row["path"], row["sha256"])
                         for row in upstream if row.get("kind") == "file"}
        self.read_visible = visible
        self.subjects = subjects or {}

    @cached_property
    def visible(self):
        try:
            return {row["path"].casefold():(row["path"], row["sha256"]) for row in self.read_visible()}
        except ValueError:
            return None

    @cached_property
    def visible_manifest(self):
        return (len(self.visible), action_digest(sorted(self.visible.values())))

    def action_issue(self, action, record):
        method = action["method"]
        if method not in FILE_CHECK_METHODS and method != "container_suite":
            return None
        if self.visible is None:
            return "当前测试输入无法核对，请检查后复测"
        if method == "container_suite":
            if path := action.get("subject_path"):
                expected = self.subjects.get(path)
                if not expected or expected!=record.get("subject"):
                    return "实际执行产物的来源与当前开发审批不一致，请按当前版本复测"
            if any(self.visible.get(key) != value for key, value in self.approved.items()):
                return "测试环境覆盖了已审文件，请恢复已审版本后复测"
            count, checksum = self.visible_manifest
            if (record.get("input_file_count"), record.get("source_manifest_sha256")) != (count, checksum):
                return "容器测试的输入清单已变化或缺少摘要，请复测"
            return None
        key = action["path"].casefold()
        current = self.visible.get(key)
        if key in self.approved and current != self.approved[key]:
            return "被测文件覆盖了已审版本，请恢复已审版本后复测"
        if current != (record.get("path"), record.get("sha256")):
            return "被测文件已变化或缺少摘要，请复测"
        return None

    def passing_record(self, case, action):
        record = _action_record(self.latest, case, action)
        return record if record and passing_check(record) else None

    def stale_cases(self, cases):
        issues = {}
        for case in cases:
            for action in case.get("actions", []):
                record = self.passing_record(case, action)
                if record and (issue := self.action_issue(action, record)):
                    issues.setdefault(case["id"], []).append(f"{case['id']} / {action['id']} {issue}")
        return issues


def count_passing_cases(cases, records, upstream, visible, subjects=None):
    """Use the same action and input binding as test approval."""
    evidence = TestEvidence(records, upstream, visible, subjects)
    case_ids = Counter(case["id"] for case in cases)
    completed = 0
    for case in cases:
        actions = case.get("actions", [])
        if case_ids[case["id"]] == 1 and actions and all(
            (record := evidence.passing_record(case, action)) and not evidence.action_issue(action, record)
            for action in actions
        ):
            completed += 1
    return completed


class DevelopmentEvidence:
    """Read the latest completion, source and build receipts for one stage."""

    def __init__(self, records, upstream=()):
        self.files = {row["id"]:row for row in records if row.get("kind")=="file"}
        self.file_heads = {row["path"].casefold():row["id"] for row in self.files.values()}
        self.closures = {row["design_id"]:row for row in records if row.get("kind")=="design_item"}
        self.builds = {row["design_id"]:row for row in records if row.get("kind")=="build"}
        self.sources = {row["path"].casefold():(row["path"],row["sha256"])
                        for row in [*upstream,*self.files.values()] if row.get("kind")=="file"}

    def completion_issue(self, ident, refs=None, *, design=None):
        close = self.closures.get(ident, {})
        if (not close.get("file_refs") or close.get("status")=="failed" or
                (refs is not None and close["id"] not in refs)):
            return f"{ident} 缺少设计项完成回执及文件版本"
        for ref in close["file_refs"]:
            record = self.files.get(ref)
            if not record or self.file_heads[record["path"].casefold()]!=ref:
                return f"{ident} 对应的文件已变化，请核对并重新完成此项"
        if design is not None:
            return design_output_issue(design, (self.files[ref]["path"] for ref in close["file_refs"]))
        return None

    def build_issue(self, ident, recipe):
        record = self.builds.get(ident, {})
        close = self.closures.get(ident, {})
        if (record.get("status")!="passed" or type(record.get("exit_code")) is not int or record["exit_code"]!=0
                or record.get("descriptor_sha256")!=action_digest(recipe) or record.get("image_id")!=recipe["image_id"]
                or record.get("argv_sha256")!=action_digest(recipe["argv"])):
            return f"{ident} 缺少与已审构建命令、镜像和输出合同一致的成功回执"
        manifest = record.get("source_files")
        if (not isinstance(manifest,list) or record.get("source_manifest_sha256")!=action_digest(manifest)
                or any(not isinstance(pair,(list,tuple)) or len(pair)!=2 or not isinstance(pair[0],str)
                       or self.sources.get(pair[0].casefold())!=tuple(pair) for pair in manifest)):
            return f"{ident} 构建时使用的输入文件版本已变化，请根据最终源码重新构建"
        outputs = record.get("outputs",[])
        ids = {output.get("id") for output in outputs}
        paths = {output.get("path") for output in outputs}
        if (len(outputs)!=len(recipe["outputs"]) or paths!=set(recipe["outputs"])
                or ids != set(record.get("file_refs",[])) or not ids <= set(close.get("file_refs",[]))):
            return f"{ident} 的已审构建输出尚未全部关联到开发完成回执"
        for output in outputs:
            row = self.files.get(output.get("id"),{})
            if row.get("path")!=output.get("path") or row.get("sha256")!=output.get("sha256"):
                return f"{ident} 的构建文件版本与回执不匹配"
        return None


def file_matches_design(path, design):
    root = PurePosixPath(design["directory"].replace("\\","/")).as_posix().casefold()
    key = path.casefold()
    return key == root or key.startswith(root+"/") or key in {
        output.casefold() for output in design.get("build",{}).get("outputs",[])
    }


def planned_design_files(design):
    return list(dict.fromkeys([*design.get("implementation_files", []), *design.get("build", {}).get("outputs", [])]))


def design_output_issue(design, paths):
    ident = design["id"]
    if "implementation_files" in design:
        evidence = set(paths)
        missing = [path for path in planned_design_files(design) if path not in evidence]
        if missing:
            suffix = f"（另有 {len(missing)-8} 个）" if len(missing) > 8 else ""
            return f"{ident} 缺少必交文件的当前回执：" + "、".join(missing[:8]) + suffix
    elif not any(file_matches_design(path, design) for path in paths):
        return f"{ident} 缺少设计落点 {design['directory']} 的实际输出文件"
    return None


def count_complete_design_items(designs, records, upstream=(), *, require_design_output=False):
    """Count current development evidence before the model returns its report."""
    evidence = DevelopmentEvidence(records, upstream)
    design_ids = Counter(row["id"] for row in designs)
    return sum(design_ids[row["id"]]==1 and not evidence.completion_issue(
                   row["id"],design=row if require_design_output else None)
               and (not row.get("build") or not evidence.build_issue(row["id"], row["build"]))
               for row in designs)


def apply_manual_checks(artifact, approved_by_kind, records):
    """Project the same latest human observations into each affected result."""
    cases = {c["id"]: c for c in approved_by_kind.get("cases", {}).get("items", [])}
    latest = _latest_action_records(records)
    manual_refs = {r["id"] for r in records if r.get("kind") == "manual_check"}
    for row in artifact["items"]:
        case = cases.get(row["case_id"], {})
        actions = case.get("actions", [])
        if not any(action["method"] == MANUAL_METHOD for action in actions):
            continue
        refs = [ref for ref in row["evidence_refs"] if ref not in manual_refs]
        checks = [_action_record(latest, case, action) for action in actions]
        refs.extend(r["id"] for r in checks if r and r.get("kind") == "manual_check")
        row["evidence_refs"] = refs
        statuses = {r["status"] for r in checks if r}
        if "failed" in statuses:
            row["status"] = "failed"
        elif "blocked" in statuses:
            row["status"] = "blocked"
        elif all(r and r["id"] in refs and passing_check(r) for r in checks):
            row["status"] = "passed"
        else:
            row["status"] = "not_run"


def case_receipt_issues(artifact, approved_by_kind, records):
    """Each approved action needs the latest passing evidence of its own kind."""
    cases = {c["id"]: c for c in approved_by_kind.get("cases", {}).get("items", [])}
    latest = _latest_action_records(records)
    problems = []
    for result in artifact["items"]:
        case = cases.get(result["case_id"], {})
        actions = case.get("actions", [])
        refs = set(result.get("evidence_refs", []))
        if not actions:
            problems.append(f"{result['case_id']} 尚无已审验收动作，不能签为测试通过")
        for action in actions:
            record = _action_record(latest, case, action)
            if not record or record["id"] not in refs or not passing_check(record):
                detail = "尚缺最新的对应通过回执"
                if "hardware_readback" in action:
                    detail = READBACK_ISSUES.get((record or {}).get("readback_issue"),
                                                "尚缺与已审设备及本次调用匹配的回读通过回执")
                elif record and record.get("limit")=="unconfirmed_subject":
                    detail = "启动器未确认实际执行的已审程序"
                elif record and record.get("output_matches") is False:
                    detail = "实际容器输出缺失、不同或被截断"
                elif record and (control := record.get("control")) and (
                        not control.get("output_matches") or control.get("exit_code")!=control.get("expected_exit_code")
                        or control.get("limit") is not None):
                    detail = "对照输入的实际输出或退出状态与预期不符"
                problems.append(f"{result['case_id']} / {action['id']} {detail}")
    return problems


def build_descriptor_ok(build):
    if not isinstance(build,dict) or set(build) != {"argv","image_id","timeout_seconds","outputs"}:
        return False
    args, outputs = build["argv"],build["outputs"]
    if (not isinstance(build["image_id"], str) or not re.fullmatch(r"sha256:[a-f0-9]{64}",build["image_id"])
            or type(build["timeout_seconds"]) is not int or not 1<=build["timeout_seconds"]<=300
            or not isinstance(args,list) or not 1<=len(args)<=128
            or any(not isinstance(a,str) or len(a)>4096 or "\x00" in a for a in args)
            or not args[0].strip() or args[0].startswith("-") or sum(map(len,args))>16384
            or not isinstance(outputs,list) or not 1<=len(outputs)<=32
            or any(not isinstance(s,str) or not safe_member(s) or any(part.startswith(".") for part in s.split("/")) for s in outputs)):
        return False
    folded = set(s.casefold() for s in outputs)
    return len(folded)==len(outputs) and not any(any(p.startswith(s+"/") for p in folded) for s in folded)
