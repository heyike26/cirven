"""Golden content contracts and failure modes for the default review workflow."""
from copy import deepcopy
from hashlib import sha256
from io import BytesIO
import json
from zipfile import ZipFile

from docx import Document
import pytest
from jsonschema import Draft202012Validator

from packages.dynamic_workflow.stage_contracts import (
    DEFAULT_STAGES, READBACK_V6_CONTRACT_VERSION, SCENARIO_V4_CONTRACT_VERSION, build_stage_prompt, parse_stage_template, source_urs,
    stage_contract, stage_schema, validate_artifact,
)


def envelope(*rows):
    return {"summary": "阶段摘要", "items": list(rows)}


def row(code, **fields):
    return {"id": code, "title": f"条目 {code}", **fields}


def scenario(code, requirement, action, *, criterion=1, path="normal"):
    return {"id":code,"requirement_id":requirement,"criterion":criterion,"case_id":"TC-1",
            "action_id":action,"path_kind":path,"condition":"触发并观察已审场景"}


def decision(code, level, parent):
    return row(code, parent_id=parent, level=level, key_points="本地交互", priority="P1",
               design_requirement="显示运行状态", requirement_ids=["OR-1","OR-2"], dimensions=["恢复成本", "速度"],
               candidates=[dict(id=i, title=t, advantages="可部署", disadvantages="有维护成本",
                           evaluations={"恢复成本": "需要一次读取", "速度": "有待测量"}) for i, t in
                           [("a", "同步刷新"), ("b", "事件推送"), ("c", "定时读取")]],
               selected_id="b", rationale="可复用现有事件队列")


INPUT = {"ur": "界面可显示运行状态\n文件包含启动日志"}
REQ = envelope(*(row(f"OR-{i}", category="functional" if i == 1 else "constraint",
    source_ur=[f"UR-00{i}"], sub_description="", description="描述", design_spec="有出处的规格",
    constraints="", remarks="", priority="P1", acceptance=["界面与运行状态一致"], surfaces=["frontend"] if i == 1 else [])
    for i in (1, 2)))

APPROVED = {
    "init": envelope(*(row(t, topic=t, detail="已明确") for t in
                       ("background", "development_constraints", "build_environment", "runtime_environment"))),
    "requirements": REQ,
    "alternatives": envelope(decision("A-1", 1, None), decision("A-2", 2, "A-1"), decision("A-3", 3, "A-2")),
    "cases": envelope(row("TC-1", requirement_ids=["OR-1", "OR-2"], category="系统", subcategory="启动和界面",
                content="启动验收", priority="P0", preconditions="", method="桌面执行",
                steps=["启动应用", "读取界面与启动日志"], checks=["观察启动状态及日志"], expected="状态一致",
                coverage=["runtime", "layout", "motion", "interaction", "accessibility"],
                actions=[{"id":"observe","method":"manual_observation","instruction":"读取界面和日志","expected":"界面状态与启动日志相符"}])),
    "design": envelope(row("DS-1", directory="src/ui/status.py", requirement_ids=["OR-1", "OR-2"],
                decision_ids=["A-3"], description="职责说明", interfaces="队列接口", implementation="状态事件处理",
                self_check="退出时释放订阅")),
    "development": envelope(row("DEV-1", design_ids=["DS-1"], status="done",
                        evidence_refs=["run-1"], self_check="设计要求已逐项核对")),
    "testing": envelope(row("TEST-1", case_id="TC-1", status="passed", evidence_refs=["run-2"],
                         rework=[{"at": "2026-10-01T07:01:02Z", "detail": "修复后复测"}])),
    "delivery": envelope(row("PKG-1", path="dist/app.zip", sha256="2"*64, description="用户要求的应用包")),
}


def add_specialty_actions(case):
    procedures = {
        "runtime": ("启动应用并等待主窗口", "启动完成、主窗口可用"),
        "layout": ("在窄窗口打开状态页", "状态正文完整可读，操作按钮未相互遮挡"),
        "motion": ("打开状态浮层，观察过场与正文位置", "浮层进入平稳，正文位置保持"),
        "interaction": ("切换状态页的筛选后打开第一行", "打开筛选后的对应条目"),
        "accessibility": ("只用键盘进入状态页、打开并退出详情", "关闭后焦点回到原条目"),
        "hardware": ("读取设备端实测反馈", "待机状态对应绿色指示"),
    }
    if case["coverage"]:
        case["coverage_refs"] = [{"coverage":kind,"action_id":f"specialty-{kind}"} for kind in case["coverage"]]
        case["actions"].extend({"id":f"specialty-{kind}","method":"manual_observation",
                                "instruction":procedures[kind][0],"expected":procedures[kind][1]}
                               for kind in case["coverage"])


@pytest.mark.parametrize("kind", APPROVED)
def test_default_contract_round_trip(kind):
    contract = stage_contract(kind)
    Draft202012Validator.check_schema(stage_schema(kind))
    assert contract["kind"] == kind and contract["title"] and contract["instruction"] and contract["fields"]
    # No unrestricted whole-project bundle is necessary to validate a step.
    scoped = {key: APPROVED[key] for key in contract["reads"]}
    artifact = deepcopy(APPROVED[kind])
    if kind=="cases":
        add_specialty_actions(artifact["items"][0])
    if kind=="design":
        artifact["items"][0]["behavior_scenarios"] = [scenario("S-1","OR-1","observe"),scenario("S-2","OR-2","observe")]
        artifact["items"][0]["implementation_files"] = ["src/ui/status.py"]
    assert validate_artifact(kind, artifact, scoped, inputs=INPUT if kind == "requirements" else {}) == []


def test_new_container_case_needs_an_observation_and_keeps_legacy_contract():
    from packages.dynamic_workflow.stage_contracts import TRACE_V2_CONTRACT_VERSION

    cases = deepcopy(APPROVED["cases"])
    suite = {"id":"check","method":"container_suite","argv":["./device-report"],
             "image_id":"sha256:"+"1"*64,"timeout_seconds":10}
    cases["items"][0]["actions"] = [suite]
    upstream = {"requirements":deepcopy(REQ)}
    upstream["requirements"]["items"][0]["surfaces"] = []
    cases["items"][0]["coverage"] = ["runtime"]
    assert validate_artifact("cases",cases,upstream)
    assert not validate_artifact("cases",cases,upstream,version=TRACE_V2_CONTRACT_VERSION)
    cases["items"][0]["coverage_refs"] = [{"coverage":"runtime","action_id":"check"}]
    suite["expected_output"] = " \n"
    assert validate_artifact("cases",cases,upstream)
    suite["expected_output"] = "状态正常："*5000
    assert any("32 KiB" in issue for issue in validate_artifact("cases",cases,upstream))
    suite["expected_output"] = "设备已就绪\n"
    assert not validate_artifact("cases",cases,upstream)
    suite["input_pair"] = {"normal_stdin":"READY\n","control_stdin":"READY\n",
                         "control_expected_output":"硬件拒绝输入\n","control_exit_code":2}
    assert any("不同的两组标准输入" in issue for issue in validate_artifact("cases",cases,upstream))
    suite["input_pair"]["control_stdin"] = "BAD\n"
    assert not validate_artifact("cases",cases,upstream)
    suite["input_pair"]["control_exit_code"] = 200
    assert not validate_artifact("cases",cases,upstream)
    suite["input_pair"]["control_exit_code"] = 124
    assert validate_artifact("cases",cases,upstream)
    suite.pop("input_pair")
    suite.update(subject_path="dist/program",argv=["sh","./dist/program"])
    assert any("直接启动指定的项目产物" in item for item in validate_artifact("cases",cases,upstream))
    suite["argv"] = ["./dist/program","--status"]
    assert not validate_artifact("cases",cases,upstream)


def test_default_context_has_no_forward_edges_or_sibling_transcripts():
    visited = set()
    for node in DEFAULT_STAGES:
        kind = node["kind"]
        assert set(node["reads"]) <= visited
        visited.add(kind)
    assert "alternatives" not in stage_contract("cases")["reads"]


def test_prompt_determinism_and_ur_binding():
    left = build_stage_prompt("requirements", {"z": 1, "user_input": INPUT}, {"init": APPROVED["init"]})
    right = build_stage_prompt("requirements", {"user_input": INPUT, "z": 1}, {"init": APPROVED["init"]})
    assert left == right
    payload = json.loads(left.split("\n", 1)[1])
    assert payload["source_urs"] == {"UR-001": "界面可显示运行状态", "UR-002": "文件包含启动日志"}
    assert source_urs({"ur": "a\n  \n a"}) == {"UR-001": "a", "UR-002": "a"}
    assert set(payload["approved_artifacts"]) == {"init"} and payload["schema"] == stage_schema("requirements")


def test_clarification_and_custom():
    assert validate_artifact("init", {"summary": "请补充", "items": [], "questions": ["编译器版本？"]}, {})
    item = envelope(row("DOC", description="说明正文"))
    assert not validate_artifact("organization-docs", item, {})
    assert validate_artifact("organization-docs", item, {}, template="指定模板")
    assert not validate_artifact("organization-docs", {**item, "template_output": "按栏目填写的正文"}, {}, template="指定模板")


@pytest.mark.parametrize(("kind", "field", "value"), [
    ("requirements", "source_ur", ["UR-042"]),
    ("requirements", "acceptance", []),
    ("alternatives", "level", 2),
    ("alternatives", "selected_id", "unlisted"),
    ("alternatives", "dimensions", ["成本"]),
    ("alternatives", "requirement_ids", ["OR-2"]),
    ("cases", "requirement_ids", ["OR-1"]),
    ("cases", "coverage", ["runtime"]),
    ("cases", "steps", []),
    ("design", "decision_ids", ["A-1"]),
    ("design", "directory", "C:/external"),
    ("development", "status", "running"),
    ("development", "evidence_refs", []),
    ("testing", "status", "failed"),
    ("testing", "case_id", "case-in-another-project"),
    ("testing", "rework", [{"at": "2026-10-01T07:00", "detail": "缺少时区"}]),
    ("delivery", "path", "../outside.exe"),
    ("delivery", "sha256", "unknown"),
])
def test_invalid_approval(kind, field, value):
    artifact = deepcopy(APPROVED[kind])
    artifact["items"][0][field] = value
    assert validate_artifact(kind, artifact, APPROVED, inputs=INPUT if kind == "requirements" else None)


def test_no_delivery_without_checked_tests():
    assert validate_artifact("delivery", APPROVED["delivery"], {})
    failed = deepcopy(APPROVED["testing"])
    failed["items"][0]["status"] = "not_run"
    assert validate_artifact("delivery", APPROVED["delivery"], {"testing": failed})


def test_scope_and_shared_tree_at_parent():
    altered = deepcopy(APPROVED["alternatives"])
    altered["items"][0]["requirement_ids"].remove("OR-2")
    assert validate_artifact("alternatives", altered, APPROVED)
    assert validate_artifact("alternatives", APPROVED["alternatives"], {})


def test_missing_actual_hardware_and_software_runtime_coverage():
    reqs = deepcopy(REQ)
    reqs["items"][0]["surfaces"].append("hardware")
    assert validate_artifact("cases", APPROVED["cases"], {"requirements": reqs})
    case = deepcopy(APPROVED["cases"])
    case["items"][0]["coverage"].remove("runtime")
    assert validate_artifact("cases", case, APPROVED)


def test_specialties_need_distinct_observable_actions_within_the_case():
    from packages.dynamic_workflow.stage_contracts import SPECIALTY_V5_CONTRACT_VERSION
    requirements = deepcopy(REQ)
    requirements["items"][0]["surfaces"].append("hardware")
    upstream = {"requirements":requirements}
    artifact = deepcopy(APPROVED["cases"])
    case = artifact["items"][0]
    case["coverage"].append("hardware")
    file_check = {"id":"status","method":"text_equals","path":"status.txt","expected":"ready"}
    case["actions"] = [file_check]
    assert not validate_artifact("cases",artifact,upstream,version=SCENARIO_V4_CONTRACT_VERSION)
    assert any("专项的有效验收动作" in issue for issue in validate_artifact("cases",artifact,upstream))
    case["coverage_refs"] = [{"coverage":kind,"action_id":"status"} for kind in case["coverage"]]
    assert any("TC-1 / status 的方法无法核验硬件联动专项" in issue
               for issue in validate_artifact("cases",artifact,upstream))

    add_specialty_actions(case)
    assert not validate_artifact("cases",artifact,upstream)
    frozen = deepcopy(artifact)
    case["coverage_refs"][0]["action_id"] = "sibling-only"
    assert any("TC-1 / sibling-only 的启动与运行专项引用有误" in issue
               for issue in validate_artifact("cases",artifact,upstream))

    artifact = deepcopy(frozen)
    case = artifact["items"][0]
    case["coverage"].remove("motion")
    case["coverage_refs"] = [ref for ref in case["coverage_refs"] if ref["coverage"]!="motion"]
    copied = {**case,"id":"TC-2","coverage":["motion"],"actions":[
        {**case["actions"][1],"id":"new","expected":"另一段预期描述"}],
        "coverage_refs":[{"coverage":"motion","action_id":"new"}]}
    artifact["items"].append(copied)
    assert any("TC-2 / new 的动效专项与 TC-1 / specialty-runtime" in issue
               for issue in validate_artifact("cases",artifact,upstream))
    copied["actions"][0]["instruction"] = "在两个窗口尺寸验证打开动画"
    assert not validate_artifact("cases",artifact,upstream)

    case = frozen["items"][0]
    index = next(i for i, action in enumerate(case["actions"]) if action["id"]=="specialty-hardware")
    case["actions"][index] = {"id":"specialty-hardware","method":"container_suite","argv":["./status"],
                              "image_id":"sha256:"+"1"*64,"timeout_seconds":15,"expected_output":"ready"}
    assert any("方法无法核验硬件联动专项" in issue for issue in validate_artifact("cases",frozen,upstream))
    case["actions"][index] = {"id":"specialty-hardware","method":"mcp_response","server":"meter","tool":"measure",
                               "arguments":{},"pointer":"/readings/status","expected_json":"\"ready\""}
    assert not validate_artifact("cases",frozen,upstream,version=SPECIALTY_V5_CONTRACT_VERSION)
    assert any("硬件联动专项尚缺已审设备编号" in issue for issue in validate_artifact("cases",frozen,upstream))
    case["actions"][index]["hardware_readback"] = {
        "device_id":"DUT-01","device_id_pointer":"/device","sample_time_pointer":"/readings/status/time"}
    assert any("独立的回读字段" in issue for issue in validate_artifact("cases",frozen,upstream))
    case["actions"][index]["hardware_readback"]["sample_time_pointer"] = "/sampled_at"
    case["actions"][index]["expected_json"] = "null"
    assert any("非空预期读值" in issue for issue in validate_artifact("cases",frozen,upstream))
    case["actions"][index]["expected_json"] = '"ready"'
    assert not validate_artifact("cases",frozen,upstream)
    assert not validate_artifact("cases",frozen,upstream,version=READBACK_V6_CONTRACT_VERSION)


def test_each_requirement_reaches_its_own_design_decision_in_new_contract():
    from packages.dynamic_workflow.stage_contracts import LEGACY_CONTRACT_VERSION
    omitted = deepcopy(APPROVED["alternatives"])
    for item in omitted["items"]:
        item["requirement_ids"] = ["OR-1"]
    assert not validate_artifact("alternatives",omitted,APPROVED,version=LEGACY_CONTRACT_VERSION)
    assert "尚未落实到三级方案的需求：OR-2" in validate_artifact("alternatives",omitted,APPROVED)

    inputs = deepcopy(APPROVED)
    other = decision("B-3",3,"A-2")
    other["requirement_ids"] = ["OR-2"]
    inputs["alternatives"]["items"][-1]["requirement_ids"] = ["OR-1"]
    inputs["alternatives"]["items"].append(other)
    original = inputs["design"]["items"][0]
    swapped = envelope({**original,"requirement_ids":["OR-1"],"decision_ids":["B-3"]},
                       {**original,"id":"DS-2","requirement_ids":["OR-2"],"decision_ids":["A-3"]})
    assert not validate_artifact("design",swapped,inputs,version=LEGACY_CONTRACT_VERSION)
    assert any("DS-1 的具体方案未覆盖：OR-1" in issue for issue in validate_artifact("design",swapped,inputs))
    swapped["items"][0]["decision_ids"],swapped["items"][1]["decision_ids"] = ["A-3"],["B-3"]
    swapped["items"][0]["behavior_scenarios"] = [scenario("S-1","OR-1","observe")]
    swapped["items"][1]["behavior_scenarios"] = [scenario("S-2","OR-2","observe")]
    for item in swapped["items"]:
        item["implementation_files"] = [item["directory"]]
    assert not validate_artifact("design",swapped,inputs)


def test_design_scenarios_must_bind_the_actual_approved_criterion_and_check():
    from packages.dynamic_workflow.stage_contracts import OUTPUT_V3_CONTRACT_VERSION

    upstream = deepcopy(APPROVED)
    upstream["requirements"]["items"][0]["acceptance"].append("启动失败时保留上次状态")
    case = upstream["cases"]["items"][0]
    first = case["actions"][0]
    case["actions"].append({**first,"id":"observe-error","instruction":"断开测试设备，观察上次状态"})
    case["acceptance_refs"] = [
        {"requirement_id":"OR-1","criterion":1,"action_id":"observe"},
        {"requirement_id":"OR-1","criterion":2,"action_id":"observe-error"},
        {"requirement_id":"OR-2","criterion":1,"action_id":"observe"},
    ]
    design = deepcopy(APPROVED["design"])
    assert not validate_artifact("design",design,upstream,version=OUTPUT_V3_CONTRACT_VERSION)
    design["items"][0]["implementation_files"] = ["src/ui/status.py"]
    missing = validate_artifact("design",design,upstream)
    assert "OR-1 第 2 条通过标准尚未落到具体设计场景" in missing
    assert any("TC-1 / observe-error" in item for item in missing)
    paths = [scenario("S-1","OR-1","observe"), scenario("S-2","OR-2","observe"),
             scenario("S-3","OR-1","observe",criterion=2,path="error")]
    design["items"][0]["behavior_scenarios"] = paths
    assert any("DS-1 / S-3 未对应" in item for item in validate_artifact("design",design,upstream))
    paths[-1]["action_id"] = "observe-error"
    assert not validate_artifact("design",design,upstream)
    paths.append({**paths[0],"id":"copy","path_kind":"recovery"})
    assert any("重复引用" in item for item in validate_artifact("design",design,upstream))

    # Two identifiers and two case rows can still represent the same operation.
    paths.pop()
    case_two = deepcopy(case)
    case_two.update(id="TC-2",acceptance_refs=[{"requirement_id":"OR-1","criterion":1,"action_id":"observe"}])
    upstream["cases"]["items"].append(case_two)
    paths.append({**paths[0],"id":"fake-error","case_id":"TC-2","path_kind":"error"})
    assert any("复用了另一路径" in item for item in validate_artifact("design",design,upstream))


def test_multiple_acceptance_criteria_need_distinct_approved_checks():
    inputs = deepcopy(APPROVED)
    inputs["requirements"]["items"][0]["acceptance"].append("启动失败时显示可区分错误")
    artifact = deepcopy(APPROVED["cases"])
    case = artifact["items"][0]
    first = {"requirement_id":"OR-1","criterion":1,"action_id":"observe"}
    absent = {"requirement_id":"OR-1","criterion":2,"action_id":"missing"}
    case["acceptance_refs"] = [first,absent]
    issues = validate_artifact("cases",artifact,inputs,version=SCENARIO_V4_CONTRACT_VERSION)
    assert any("第 2 条通过标准尚缺验收动作" in issue for issue in issues)
    case["acceptance_refs"][1]["action_id"] = "observe"
    assert any("同一" in issue or "同时替" in issue
               for issue in validate_artifact("cases",artifact,inputs,version=SCENARIO_V4_CONTRACT_VERSION))
    case["acceptance_refs"] = [first]
    copied = {**case,"id":"TC-2","actions":[{**case["actions"][0],"id":"new-id"}],
              "acceptance_refs":[{**absent,"action_id":"new-id"}]}
    assert any("同时替" in issue for issue in
               validate_artifact("cases",envelope(case,copied),inputs,version=SCENARIO_V4_CONTRACT_VERSION))
    case["actions"].append({"id":"error","method":"manual_observation","instruction":"触发启动失败","expected":"显示可区分错误"})
    case["acceptance_refs"].append({**absent,"action_id":"error"})
    assert not validate_artifact("cases",artifact,inputs,version=SCENARIO_V4_CONTRACT_VERSION)


@pytest.mark.parametrize(("method", "original", "cosmetic", "independent"), [
    ("text_equals", {"path":"dist/status","expected":"ready"},
     {"expected":"retry"}, {"path":"dist/fallback"}),
    ("mcp_response", {"server":"bench","tool":"observe","arguments":{},
                      "pointer":"/state","expected_json":"1"},
     {"expected_json":"2"}, {"pointer":"/error"}),
    ("manual_observation", {"instruction":"查看指示灯","expected":"绿灯"},
     {"instruction":" 查看指示灯 ","expected":"黄灯"}, {"instruction":"拔掉设备并观察指示灯"}),
    ("container_suite", {"image_id":"sha256:"+"1"*64,"argv":["./controller"],
                         "timeout_seconds":10,"expected_output":"ready"},
     {"timeout_seconds":30}, {"argv":["./controller","--recover"]}),
    ("container_suite", {"image_id":"sha256:"+"1"*64,"argv":["./controller"],
                         "timeout_seconds":10,"expected_output":"ready","input_pair":{
                             "normal_stdin":"ready","control_stdin":"fail",
                             "control_expected_output":"retry","control_exit_code":0}},
     {"expected_output":"retry","input_pair":{"normal_stdin":"fail","control_stdin":"ready",
      "control_expected_output":"ready","control_exit_code":0}},
     {"input_pair":{"normal_stdin":"fail","control_stdin":"reset",
      "control_expected_output":"reset","control_exit_code":0}}),
], ids=["file-expected", "connector-expected", "manual-expected", "container-timeout", "container-swapped-inputs"])
def test_disguised_observation_cannot_satisfy_two_standards_or_design_paths(method, original, cosmetic, independent):
    inputs = deepcopy(APPROVED)
    inputs["requirements"]["items"][0]["acceptance"].append("异常后可恢复")
    cases = inputs["cases"]
    first = cases["items"][0]
    first["actions"] = [{"id":"normal","method":method,**original}]
    first["acceptance_refs"] = [{"requirement_id":"OR-1","criterion":1,"action_id":"normal"}]
    second = deepcopy(first)
    second.update(id="TC-2",requirement_ids=["OR-1"],coverage=[])
    second["actions"] = [{**first["actions"][0],**cosmetic,"id":"repair"}]
    second["acceptance_refs"] = [{"requirement_id":"OR-1","criterion":2,"action_id":"repair"}]
    cases["items"].append(second)
    issues = validate_artifact("cases",cases,inputs,version=SCENARIO_V4_CONTRACT_VERSION)
    assert any("TC-2 / repair" in issue and "TC-1 / normal" in issue for issue in issues)
    assert "OR-1 第 2 条通过标准尚缺验收动作" in issues

    design = deepcopy(APPROVED["design"])
    design["items"][0]["implementation_files"] = ["src/ui/status.py"]
    inputs["requirements"]["items"][0]["acceptance"].pop()
    second["acceptance_refs"][0]["criterion"] = 1
    design["items"][0]["behavior_scenarios"] = [
        scenario("S-1","OR-1","normal"),
        scenario("S-2","OR-2","normal"),
        {**scenario("S-3","OR-1","repair",path="recovery"),"case_id":"TC-2"},
    ]
    assert any("复用了另一路径" in issue for issue in validate_artifact("design",design,inputs))

    second["actions"][0].update(independent)
    assert not validate_artifact("design",design,inputs)
    inputs["requirements"]["items"][0]["acceptance"].append("异常后可恢复")
    second["acceptance_refs"][0]["criterion"] = 2
    assert not validate_artifact("cases",cases,inputs,version=SCENARIO_V4_CONTRACT_VERSION)


def test_design_files_are_explicit_and_portable_while_v6_stays_readable():
    plan = deepcopy(APPROVED["design"])
    item, = plan["items"]
    item.update(directory="src/controller",behavior_scenarios=[
        scenario("S-1","OR-1","observe"),scenario("S-2","OR-2","observe")])
    assert not validate_artifact("design",plan,APPROVED,version=READBACK_V6_CONTRACT_VERSION)
    assert any("必须列明" in issue for issue in validate_artifact("design",plan,APPROVED))
    for paths in ([],["src/elsewhere.py"],["src/controller/*.py"],["src/controller/./file"],
                  ["src/controller/.hidden"],["src/controller/Input","src/controller/input"],
                  ["src/controller/parser","src/controller/parser/rules.json"]):
        item["implementation_files"] = paths
        assert any("必交" in issue for issue in validate_artifact("design",plan,APPROVED)), paths
    item["implementation_files"] = ["src/controller/parse.py","src/controller/recover.py"]
    assert not validate_artifact("design",plan,APPROVED)
    item.update(implementation_files=[],build={"argv":["./build"],"image_id":"sha256:"+"1"*64,
        "timeout_seconds":15,"outputs":["dist/controller.bin"]})
    assert not validate_artifact("design",plan,APPROVED)


def test_templates_from_docx_utf8_utf16_and_binary_error():
    document = Document()
    document.add_heading("验证范围", level=1)
    document.add_paragraph("写成可检查的观察值")
    stream = BytesIO()
    document.save(stream)
    raw = stream.getvalue()
    parsed = parse_stage_template("写法.docx", raw)
    assert parsed["sha256"] == sha256(raw).hexdigest()
    assert "验证范围" in parsed["text"] and "可检查" in parsed["text"]
    for name, raw_text, wanted in [("custom.org", "项目要点".encode(), "项目要点"),
                                  ("columns.csv", "序号,结论".encode("utf-16"), "序号,结论")]:
        assert parse_stage_template(name, raw_text)["text"] == wanted
    for name, data in [("../file", b"a"), ("bad.docx", b"PK"), ("unknown", b"a\x00b"),
                       ("large.txt", b"a"*180_001), ("blank.md", b"  "), ("scan.png", b"example")]:
        with pytest.raises(ValueError):
            parse_stage_template(name, data)


def test_spreadsheet_template_preserves_sheet_cells_and_merge():
    ns = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
    rel = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    stream = BytesIO()
    with ZipFile(stream, "w") as z:
        z.writestr("xl/workbook.xml", f'<workbook xmlns="{ns}" xmlns:r="{rel}"><sheets><sheet name="检查" r:id="r1"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels", f'<Relationships><Relationship Id="r1" Type="{rel}/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        z.writestr("xl/sharedStrings.xml", f'<sst xmlns="{ns}"><si><r><t>执行</t></r><r><t>方式</t></r></si></sst>')
        z.writestr("xl/worksheets/sheet1.xml", f'<worksheet xmlns="{ns}"><sheetData><row><c r="B2" t="s"><v>0</v></c></row></sheetData><mergeCells><mergeCell ref="B2:D2"/></mergeCells></worksheet>')
    parsed = parse_stage_template("检查.xlsx", stream.getvalue())
    assert json.loads(parsed["text"]) == [{"name": "检查", "cells": [{"address": "B2", "value": "执行方式"}],
                                           "merged_cells": ["B2:D2"]}]


@pytest.mark.parametrize(("filename","template","good","bad"), [
    ("栏目.csv","编号,名称\nOR-1,样例","编号,名称\nOR-2,功能描述","名称,编号\n功能描述,OR-2"),
    ("规格.md","# {{项目}}\n## 背景\n### 成本\n\n```md\n# 忽略\n```",
     "# 实际项目\n## 背景\n项目背景\n### 成本\n预算", "# 实际项目\n### 背景\n#### 成本"),
])
def test_machine_readable_template_structure_gates_approval(filename,template,good,bad):
    profile = parse_stage_template(filename,template.encode())
    original = envelope(row("1",description="解释"))
    assert not validate_artifact("custom",{**original,"template_output":good},{},template=profile)
    assert validate_artifact("custom",{**original,"template_output":bad},{},template=profile)


def test_docx_heading_order_gates_approval():
    document = Document()
    for text in ("背景","验收"):
        document.add_heading(text,level=1)
    stream = BytesIO()
    document.save(stream)
    profile = parse_stage_template("格式.docx",stream.getvalue())
    assert profile["format_rules"]["labels"]==["背景","验收"]
    artifact = envelope(row("1",description="内容"))
    assert not validate_artifact("custom",{**artifact,"template_output":"背景：工程。\n验收：操作检查。"},{},template=profile)
    assert validate_artifact("custom",{**artifact,"template_output":"验收。\n背景。"},{},template=profile)


def test_approval_rejects_unrelated_actions_and_stale_passing_receipt():
    from packages.dynamic_workflow.stage_assertions import bind_case_action, case_receipt_issues
    action = {"id":"parse","method":"json_valid","path":"src/status.json","expected":""}
    second = {"id":"content","method":"text_equals","path":"src/status.json","expected":"{}"}
    cases = deepcopy(APPROVED["cases"])
    ctx = {"approved_by_kind":{"cases":cases}}
    result = {"items":[{"id":"RESULT","case_id":"TC-1","evidence_refs":["check-1","check-2"]}]}
    assert case_receipt_issues(result,ctx["approved_by_kind"],[])
    cases["items"][0]["actions"]=[action,second]
    assert not validate_artifact("cases",cases,{"requirements":REQ},version=SCENARIO_V4_CONTRACT_VERSION)
    with pytest.raises(ValueError):
        bind_case_action(ctx,"TC-1",method="text_equals",path="src/status.json",expected="different")
    receipts = [{"id":f"check-{i+1}","kind":"check","status":"passed","exit_code":0,"case_id":"TC-1",
         **bind_case_action(ctx,"TC-1",**{k:v for k,v in a.items() if k!="id"})} for i,a in enumerate((action,second))]
    assert not case_receipt_issues(result,ctx["approved_by_kind"],receipts)
    assert case_receipt_issues(result,ctx["approved_by_kind"],receipts[:1])
    assert case_receipt_issues(result,ctx["approved_by_kind"],[*receipts,{**receipts[0],"id":"check-3","status":"failed","exit_code":1}])
    cases["items"][0]["actions"]=[action,{**action,"id":"parse-again"}]
    assert validate_artifact("cases",cases,{"requirements":REQ})


def test_template_edits_preserve_authorized_targets_and_row_cardinality():
    template = {"name":"test.docx","edit_targets":[
        {"target":"paragraph:3","columns":["范围"],"repeatable":False,"required":True},
        {"target":"table:0:row:2","columns":["序号","名称"],"repeatable":True}]}
    base = {**envelope(row("R",description="正文")),"template_output":"范围\n1 初始\n2 验收"}
    summary = {"target":"paragraph:3","values":["范围"]}
    first = {"target":"table:0:row:2","values":["1","初始"]}
    second = {"target":"table:0:row:2","values":["2","验收"]}
    good = {**base, "template_edits":[summary,first,second]}
    assert not validate_artifact("custom",good,{},template=template)
    for edits in ([],[first],[summary,summary],[summary,{"target":"table:9:row:1","values":["a"]}],
                  [summary,{"target":"table:0:row:2","values":["missing name"]}]):
        assert validate_artifact("custom",{**base,"template_edits":edits},{},template=template)
    assert validate_artifact("custom",good,{})
