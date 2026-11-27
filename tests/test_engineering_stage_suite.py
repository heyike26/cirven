"""Container adapter boundary using an explicitly selected controlled CLI.

The subprocess fixture records exact host CLI arguments. It is not an OCI engine.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from packages.dynamic_workflow import stage_suite as suite
from packages.dynamic_workflow.stage_assertions import count_passing_cases
from packages.dynamic_workflow.stage_workspace import file_list, file_write, operation_receipts, suite_input_files


IMAGE = "sha256:"+"1"*64


@pytest.fixture
def configured(tmp_path, monkeypatch):
    # Use a Python-driven driver because several CI /dev/shm mounts forbid
    # executable scratch. The host Popen boundary still launches a real process.
    exe = tmp_path/"docker"
    exe.write_text("fixture")
    exe.chmod(0o700)
    driver = tmp_path/"driver.py"
    saved = tmp_path/"calls.jsonl"
    driver.write_text("""
from pathlib import Path
import csv,json,os,subprocess,sys,tempfile,time
saved=Path(sys.argv[1])
cmd=sys.argv[2:]
entry={'cmd':cmd,'parent_secret':'CIRVEN_SUITE_FAKE_TOKEN' in os.environ}
if cmd[0]=='image':
    print(json.dumps([{'Id':cmd[-1],'Os':'linux','Config':{}}]))
elif cmd[0]=='run':
    opt=next(i for i in csv.reader([cmd[cmd.index('--mount')+1]]))
    source=Path(next(x.removeprefix('source=') for x in opt if x.startswith('source=')))
    entry['inputs']={str(f.relative_to(source)):f.read_text(errors='replace') for f in source.rglob('*') if f.is_file()}
    entry['mode']=source.stat().st_mode&0o777
    if '-c' in cmd and 'Approved program digest mismatch' in cmd[cmd.index('-c')+1]:
        start=cmd.index('-c')+1
        boot,label,duration,root,work,path,sha,marker,*rest=cmd[start:]
        entry['subject']=[path,sha,*rest]
        with saved.open('a') as out:
            out.write(json.dumps(entry)+'\\n')
        with tempfile.TemporaryDirectory(dir=source.parent) as temp:
            data=sys.stdin.buffer.read() if '--interactive' in cmd else b''
            done=subprocess.run(['/bin/sh','-c',boot,label,duration,str(source),str(Path(temp)/'work'),path,sha,marker,*rest],input=data)
        sys.exit(done.returncode)
    elif '--interactive' in cmd:
        value=sys.stdin.buffer.read().decode()
        entry['stdin']=value
        constant=entry['inputs'].get('mode.txt')=='constant'
        expected_input=value=='READY\\n'
        print('status=ready' if constant or expected_input else 'status=retry')
    elif cmd[-1]=='long':
        print('x'*(2*1024*1024+1))
    else:
        print('observed command')
with saved.open('a') as out:
    out.write(json.dumps(entry)+'\\n')
if cmd[0]=='run' and cmd[-1]=='broken':sys.exit(7)
if cmd[0]=='run' and '--interactive' in cmd and not constant and not expected_input:sys.exit(2)
""")
    original = subprocess.Popen
    def driver_popen(argv, **kwargs):
        assert argv[0]==str(exe) and kwargs["shell"] is False
        return original([sys.executable,"-B",str(driver),str(saved),*argv[1:]], **kwargs)
    monkeypatch.setattr(suite.subprocess,"Popen",driver_popen)
    monkeypatch.setenv("CIRVEN_WORKFLOW_CONTAINER_EXE",str(exe))
    monkeypatch.setenv("CIRVEN_WORKFLOW_CONTAINER_IMAGE",IMAGE)
    monkeypatch.setenv("CIRVEN_SUITE_FAKE_TOKEN","private")
    ctx = {"_data_dir":tmp_path/"data", "capabilities":{"tools":["run_check"]},
           "project_id":"p","run_id":"flow","operation_id":"exec","stage_session_id":"s",
           "approved_by_kind":{"cases":{"items":[{"id":"TC-1"}]}}}
    commands = [["pytest","--","a; $USER"],["tests","broken"],["tests","long"],["tests"]]
    ctx["approved_by_kind"]["cases"]["items"][0]["actions"] = [
        {"id":f"check-{i}","method":"container_suite","argv":a,"image_id":IMAGE,"timeout_seconds":120}
        for i,a in enumerate(commands)]
    return ctx,lambda:[json.loads(line) for line in saved.read_text().splitlines()] if saved.is_file() else []


def test_isolated_manifest_receipt_and_actual_exit(configured):
    ctx, calls = configured
    source = file_write(ctx,"tests/input.txt","test input","")
    file_write({**ctx,"stage_session_id":"other"},"other.txt","not visible","")
    record = suite.run_suite(ctx,"TC-1",["pytest","--","a; $USER"])
    assert record["status"]=="passed" and record["exit_code"]==0 and record["input_file_count"]==1
    assert record["image_id"]==IMAGE and record["detail"].strip()=="observed command"
    cmd = calls()[1]["cmd"]
    assert cmd[-3:]==["pytest","--","a; $USER"]
    assert {"--network=none","--pull=never","--user=65534:65534","--read-only","--cap-drop=ALL",
            "--pids-limit=128","--memory=512m","--security-opt=no-new-privileges","--log-driver=none"} <= set(cmd)
    assert sum(x=="--mount" for x in cmd)==1 and "target=/input,readonly" in cmd[cmd.index("--mount")+1]
    assert calls()[1]["inputs"]=={"tests/input.txt":"test input"} and calls()[1]["mode"]==0o755
    assert all(not c["parent_secret"] for c in calls())
    assert calls()[-1]["cmd"][:2]==["container","ls"]
    accepted = [{"id":"TC-1","actions":[ctx["approved_by_kind"]["cases"]["items"][0]["actions"][0]]}]
    assert count_passing_cases(accepted,[record],[],lambda: file_list(ctx))==1
    assert list(suite_input_files(ctx["_data_dir"],[record]))==[[source["path"],source["sha256"]]]
    from packages.dynamic_workflow.stage_delivery import review_delivery_members
    changed = file_write({**ctx,"stage_session_id":"delivery"},source["path"],"replaced","")
    artifact = {"items":[{"id":"PKG-1","path":changed["path"],"sha256":changed["sha256"]}]}
    with pytest.raises(ValueError,match="已审或已测"):
        review_delivery_members(ctx["_data_dir"],artifact,[changed],[record])
    file_write(ctx,"tests/second.txt","new input","")
    assert count_passing_cases(accepted,[record],[],lambda: file_list(ctx))==0
    failed = suite.run_suite(ctx,"TC-1",["tests","broken"])
    assert failed["status"]=="failed" and failed["exit_code"]==7
    assert not list((ctx["_data_dir"]/"workflow-kernels").glob("suite-*"))
    assert [item["id"] for item in operation_receipts(ctx) if item["kind"]=="check"]==[record["id"],failed["id"]]


def test_rejects_unknown_without_host_command(configured,monkeypatch):
    ctx, calls = configured
    for new_ctx, cid, argv, timeout in [
        (ctx,"TC-other",["test"],1),(ctx,"TC-1",[],1),(ctx,"TC-1",["test"],True),
        ({**ctx,"capabilities":{"tools":[]}},"TC-1",["test"],3),(ctx,"TC-1",["-x"],1)]:
        with pytest.raises(ValueError):
            suite.run_suite(new_ctx,cid,argv,timeout)
    with pytest.raises(ValueError, match="不一致"):
        suite.run_suite(ctx,"TC-1",["true"])
    monkeypatch.setenv("CIRVEN_WORKFLOW_CONTAINER_IMAGE","example:latest")
    with pytest.raises(ValueError):
        suite.run_suite(ctx,"TC-1",["test"])
    assert calls()==[]


def test_observation_contract_rejects_empty_and_wrong_output_at_exit_zero(configured,monkeypatch):
    from packages.dynamic_workflow.stage_assertions import action_digest, case_receipt_issues
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION

    ctx, calls = configured
    ctx["contract_version"] = CONTRACT_VERSION
    case, = ctx["approved_by_kind"]["cases"]["items"]
    case["actions"] = case["actions"][:1]
    cmd = case["actions"][0]["argv"]
    with pytest.raises(ValueError,match="可观察输出"):
        suite.run_suite(ctx,"TC-1",cmd)
    assert not calls()
    action = case["actions"][0]
    action["expected_output"] = "verified device status\n"
    command_result = (0, b"", None, False)
    cli = suite._cli
    monkeypatch.setattr(suite,"_cli",lambda argv,*args,**kwargs:command_result
                        if argv[1]=="run" else cli(argv,*args,**kwargs))

    def check(probe):
        report = {"items":[{"case_id":"TC-1","evidence_refs":[probe["id"]]}]}
        return case_receipt_issues(report,ctx["approved_by_kind"],operation_receipts(ctx))

    none = suite.run_suite(ctx,"TC-1",cmd)
    assert none["exit_code"]==0 and none["status"]=="failed" and none["output_matches"] is False
    assert check(none)==["TC-1 / check-0 实际容器输出缺失、不同或被截断"]
    command_result = (0, b"verified device status", None, False)
    wrong = suite.run_suite(ctx,"TC-1",cmd)
    assert wrong["status"]=="failed" and wrong["observed_output_sha256"]!=wrong["expected_output_sha256"]
    command_result = (0, b"verified device status\n", None, True)
    truncated = suite.run_suite(ctx,"TC-1",cmd)
    assert truncated["status"]=="failed" and "observed_output_sha256" not in truncated
    command_result = (0, b"verified device status\n", None, False)
    correct = suite.run_suite(ctx,"TC-1",cmd)
    assert correct["output_matches"] and correct["status"]=="passed" and correct["source_manifest_retained"]
    assert correct["action_sha256"]==action_digest(action)
    assert correct["observed_output_sha256"]==correct["expected_output_sha256"]
    assert not check(correct)
    forged = {**correct,"observed_output_sha256":"x"*64}
    assert count_passing_cases([case],[forged],[],lambda: file_list(ctx))==0


def test_same_command_matches_both_pinned_inputs_and_rejects_constant_output(configured):
    from packages.dynamic_workflow.stage_assertions import case_receipt_issues
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION

    ctx, calls = configured
    ctx["contract_version"] = CONTRACT_VERSION
    case, = ctx["approved_by_kind"]["cases"]["items"]
    cmd = ["./status","pair"]
    pair = {"normal_stdin":"READY\n","control_stdin":"BAD\n",
            "control_expected_output":"status=retry\n","control_exit_code":2}
    action = {"id":"paired","method":"container_suite","argv":cmd,"image_id":IMAGE,
              "timeout_seconds":120,"expected_output":"status=ready\n","input_pair":pair}
    case["actions"] = [action]
    mode = file_write(ctx,"mode.txt","constant","")
    bogus = suite.run_suite(ctx,"TC-1",cmd)
    assert bogus["output_matches"] and not bogus["control"]["output_matches"]
    assert bogus["status"]=="failed" and bogus["control"]["exit_code"]==0
    report = {"items":[{"case_id":"TC-1","evidence_refs":[bogus["id"]]}]}
    assert case_receipt_issues(report,ctx["approved_by_kind"],operation_receipts(ctx))==[
        "TC-1 / paired 对照输入的实际输出或退出状态与预期不符"]
    file_write(ctx,"mode.txt","decode",mode["sha256"])
    real = suite.run_suite(ctx,"TC-1",cmd)
    assert real["status"]=="passed" and real["output_matches"]
    assert real["control"]["output_matches"] and real["control"]["exit_code"]==2
    assert real["container"]!=real["control"]["container"]
    assert real["input_sha256"]!=real["control"]["input_sha256"]
    invoked = [step for step in calls() if step["cmd"][0]=="run"]
    assert [step["stdin"] for step in invoked]==["READY\n","BAD\n"]*2
    assert all(step["cmd"][-2:]==cmd and "--network=none" in step["cmd"] for step in invoked)
    assert real["source_manifest_retained"] and list(suite_input_files(ctx["_data_dir"],[real]))
    assert count_passing_cases([case],[real],[],lambda:file_list(ctx))==1
    assert count_passing_cases([case],[{**real,"control":None}],[],lambda:file_list(ctx))==0
    forged = {**real,"control":{**real["control"],"exit_code":0}}
    assert count_passing_cases([case],[forged],[],lambda:file_list(ctx))==0
    with suite.ledger(ctx["_data_dir"]) as db:
        assert db.execute("SELECT count FROM suite_counts").fetchone()[0]==4
        db.execute("UPDATE suite_counts SET count=31")
    with pytest.raises(ValueError,match="次数上限"):
        suite.run_suite(ctx,"TC-1",cmd)
    assert len([step for step in calls() if step["cmd"][0]=="run"])==4


def test_launcher_executes_the_approved_bytes_and_rejects_a_shadow(configured):
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION

    ctx, calls = configured
    ctx["contract_version"] = CONTRACT_VERSION
    path = "dist/device status"
    case, = ctx["approved_by_kind"]["cases"]["items"]
    command = ["./"+path,"--status"]
    case["actions"] = [{"id":"direct","method":"container_suite","argv":command,
        "subject_path":path,"image_id":IMAGE,"timeout_seconds":120,"expected_output":"ready\n",
        "input_pair":{"normal_stdin":"OK\n","control_stdin":"BAD\n","control_expected_output":"retry\n",
                      "control_exit_code":2}}]
    program = '#!/bin/sh\ntest "$#" = 1 && test "$1" = --status || exit 6\nread -r input\n' \
              'if [ "$input" = OK ]; then echo ready; else echo retry; exit 2; fi\n'
    built = file_write({**ctx,"stage_session_id":"D"},path,program,"")
    ctx["approved_evidence"] = [built]
    with pytest.raises(ValueError,match="唯一已审核开发产物"):
        suite.run_suite(ctx,"TC-1",command)
    identity = {"path":path,"sha256":built["sha256"],"file_receipt_id":built["id"],
                "node_id":"dev","stage_session_id":"D","approval_sha256":"a"*64}
    subjects = ctx["approved_subjects"] = {path:identity}
    receipt = suite.run_suite(ctx,"TC-1",command)
    assert receipt["status"]=="passed" and receipt["subject"]==receipt["control"]["subject"]==identity
    assert receipt["detail"]=="ready\n" and receipt["control"]["detail"]=="retry\n"
    assert [item["subject"] for item in calls() if "subject" in item]==[[path,built["sha256"],"--status"]]*2
    def count(result, origins=subjects):
        return count_passing_cases([case],[result],[built],lambda:file_list(ctx),origins)
    assert count(receipt)==1
    assert count({**receipt,"subject":None})==0
    other_review = {path:{**identity,"approval_sha256":"b"*64}}
    assert count(receipt,other_review)==0
    file_write(ctx,path,"#!/bin/sh\necho ready\n",built["sha256"])
    assert count(receipt)==0
    with pytest.raises(ValueError,match="已审核开发产物版本不符"):
        suite.run_suite(ctx,"TC-1",command)
    assert len([item for item in calls() if "subject" in item])==2


def test_output_limit_and_cancel_cleanup(configured,monkeypatch):
    ctx, calls = configured
    record = suite.run_suite(ctx,"TC-1",["tests","long"])
    assert record["status"]=="failed" and record["limit"]=="output_limit" and record["output_truncated"]
    assert len(record["detail"])<=suite.MAX_CAPTURE
    orig = suite._cli
    def interrupted(argv,*args,**kwargs):
        if argv[1]=="run":
            raise KeyboardInterrupt
        return orig(argv,*args,**kwargs)
    monkeypatch.setattr(suite,"_cli",interrupted)
    with pytest.raises(KeyboardInterrupt):
        suite.run_suite(ctx,"TC-1",["tests"])
    assert calls()[-1]["cmd"][:2]==["container","ls"]
    assert len([r for r in operation_receipts(ctx) if r["kind"]=="check"])==1


def test_timeout_of_actual_pipe_command(tmp_path):
    # Test only the bounded process primitive with a harmless test-owned sleeper.
    code, data, limit, truncated=suite._cli([sys.executable,"-B","-c","import time;time.sleep(15)"],
                              {"TMPDIR":str(tmp_path)},time.monotonic()+0.1)
    assert code!=0 and limit=="timeout" and not truncated


def test_cleanup_removes_only_original_labelled_engine_id(monkeypatch):
    container_id = "a"*64
    seen = []
    def cli(argv, env, deadline):
        seen.append(argv)
        return (0, (container_id+"\n").encode() if argv[2]=="ls" else b"", None, False)
    monkeypatch.setattr(suite,"_cli",cli)
    assert suite._owned_cleanup("docker","cirven-suite-owned",{})
    assert seen[0][-3:] == ["label=io.cirven.workflow.suite=cirven-suite-owned","--filter","name=cirven-suite-owned"]
    assert seen[1]==["docker","container","rm","--force","--volumes",container_id]
    def ambiguous(argv, env, deadline):
        assert argv[2]=="ls"
        return 0,(container_id+"\n"+"b"*64+"\n").encode(),None,False
    monkeypatch.setattr(suite,"_cli",ambiguous)
    assert not suite._owned_cleanup("docker","cirven-suite-owned",{})
