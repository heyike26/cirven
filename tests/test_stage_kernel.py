"""The installed original CLIs talk to a controlled loopback model; no company credentials."""
import asyncio
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
from queue import Queue
import sys
import threading
import time

import psutil
import pytest

from packages.domain.store import Conflict, InvalidInput
from packages.dynamic_workflow import kernel_settings as settings
from packages.dynamic_workflow import stage_kernel as kernel
from packages.dynamic_workflow import stage_workspace as files
from packages.dynamic_workflow.stage_assertions import action_digest, count_passing_cases


def stage(tmp_path,engine,tools=()):
    return dict(_data_dir=tmp_path,project_id="project",run_id="run",stage_session_id="node-1",
                operation_id="operation",kind="init",kernel=engine,max_subagents=0,
                capabilities={"tools":list(tools),"mcp":[],"skills":[]},approved_evidence=[],
                approved_by_kind={},user_input={"background":"test"},annotations=[],prior_artifact=None)


def _response():
    return {"summary":"需要补充执行环境","items":[],"questions":["目标执行环境是什么？"]}


def test_testing_progress_needs_all_latest_bound_actions():
    first = {"id":"json","method":"json_valid","path":"result.json"}
    manual = {"id":"device","method":"manual_observation","instruction":"observe"}
    case = {"id":"TC-1","actions":[first, manual]}
    head = {"path":first["path"],"sha256":"a"*64}
    automated = {"kind":"check","case_id":case["id"],"action_id":first["id"],
                 "action_sha256":action_digest(first),"status":"passed","exit_code":0,**head}
    observation = {"kind":"manual_check","case_id":case["id"],"action_id":manual["id"],
                   "action_sha256":action_digest(manual),"case_sha256":action_digest(case),
                   "status":"passed","source":"local_user","environment":"lab","observation":"observed"}
    def count(cases, records):
        return count_passing_cases(cases, records, (), lambda: [head])
    assert count([case], [automated]) == 0
    assert count([case], [automated, {**observation, "case_sha256":"stale"}]) == 0
    assert count([case], [automated, observation]) == 1
    assert count([case], [automated, observation, {**automated, "status":"failed","exit_code":1}]) == 0
    assert count([case, case], [automated, observation]) == 0


def test_test_progress_matches_exact_target_or_container_snapshot(tmp_path):
    initial = stage(tmp_path, 'opencode')
    source = files.file_write(initial,'src/result.json','{}','')
    ctx = {**initial,'kind':'testing','stage_session_id':'testing','approved_evidence':[source]}
    direct = {'id':'read','method':'json_valid','path':source['path'],'expected':''}
    suite = {'id':'suite','method':'container_suite','argv':['true'],'image_id':'sha256:'+'1'*64,'timeout_seconds':20}
    cases = [{'id':'TC-1','actions':[direct]}, {'id':'TC-2','actions':[suite]}]
    ctx['approved_by_kind'] = {'cases':{'items':cases}}
    checked = files.check(ctx,'TC-1',source['path'],'json_valid','')
    receipt = {'kind':'check','case_id':'TC-2','action_id':suite['id'],
               'action_sha256':action_digest(suite),'exit_code':0,'status':'passed',
               'input_file_count':1,'source_manifest_sha256':action_digest([[source['path'],source['sha256']]])}
    def count(*records):
        return count_passing_cases(cases,records,[source],lambda: files.file_list(ctx))
    assert count(checked,receipt)==2
    extra = files.file_write(ctx,'fixtures/expected.json','{}','')
    assert count(checked,receipt)==1
    shadow = files.file_write(ctx,source['path'],'[]',source['sha256'])
    shadow_check = files.check(ctx,'TC-1',source['path'],'json_valid','')
    assert count(checked,receipt,shadow_check)==0
    files.file_write(ctx,source['path'],'{}',shadow['sha256'])
    final = files.check(ctx,'TC-1',source['path'],'json_valid','')
    expected_inputs = [[extra['path'],extra['sha256']],[source['path'],source['sha256']]]
    renewed = {**receipt,'input_file_count':2,'source_manifest_sha256':action_digest(expected_inputs)}
    assert count(checked,receipt,final,renewed)==2


def test_delivery_reviews_nested_bytes_and_preserves_new_material(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    from packages.dynamic_workflow.stage_delivery import review_delivery_members

    original = files.file_write(stage(tmp_path,'opencode'),'src/status.txt','tested','')
    ctx = {**stage(tmp_path,'opencode'),'stage_session_id':'deliver'}
    test = {'kind':'check','method':'text_equals','status':'passed','exit_code':0,
            'path':original['path'],'sha256':original['sha256']}
    def archive(entries):
        buffer = BytesIO()
        with ZipFile(buffer,'w') as out:
            for name,data in entries:
                out.writestr(name,data)
        return buffer.getvalue()
    def review(name,data):
        file = files.file_write_bytes(ctx,name,data,'')
        output = {'items':[{'id':'PKG','path':file['path'],'sha256':file['sha256']}]}
        return review_delivery_members(tmp_path,output,[file],[test])
    assert review('release.zip',archive([('bundle.zip',archive([
        ('src/status.txt',b'tested'),('notes.txt',b'new instructions')]))]))['PKG'][0]['members'][0]['sha256']==original['sha256']
    with pytest.raises(ValueError,match='changed.zip!/bundle.zip!/SRC/status.txt'):
        review('changed.zip',archive([('bundle.zip',archive([('SRC/status.txt',b'changed')]))]))
    with pytest.raises(ValueError,match='已审或已测'):
        review('src/status.txt',b'replaced directly')
    with pytest.raises(ValueError,match='重复'):
        review('collision.zip',archive([('note',b'a'),('NOTE',b'b')]))
    with pytest.raises(ValueError,match='ZIP 内容无法核对'):
        review('malformed.zip',b'invalid ZIP')


def test_development_progress_tracks_current_workspace_versions(tmp_path,monkeypatch):
    done = tmp_path/'model-done'
    result = json.dumps({'type':'text','part':{'text':'finished'}})
    script = ('from pathlib import Path; import sys,time\n'
              'while not Path(sys.argv[1]).exists(): time.sleep(.05)\n'
              'print(sys.argv[2])')
    monkeypatch.setattr(settings,'runtime',lambda *args:(sys.executable,{'credential_env':''},0))
    monkeypatch.setattr(kernel,'_command',lambda *args:[sys.executable,'-c',script,str(done),result])
    ctx = stage(tmp_path,'opencode',['workspace_write'])
    ctx.update(kind='development',approved_by_kind={'design':{'items':[{'id':'DS-1'},{'id':'DS-2'}]}})
    observations = Queue()
    ctx['_progress'] = lambda completed,total,message: observations.put((completed,total))
    with ThreadPoolExecutor(max_workers=1) as pool:
        active = pool.submit(kernel._launch,ctx,'controlled stage')
        try:
            assert observations.get(timeout=6)==(0,2)
            original = files.file_write(ctx,'src/first.txt','original','')
            assert observations.get(timeout=6)==(0,2)
            files.complete_design_item(ctx,'DS-1',[original['id']])
            assert observations.get(timeout=6)==(1,2)
            other = files.file_write(ctx,'src/second.txt','independent','')
            assert observations.get(timeout=6)==(1,2)
            files.complete_design_item(ctx,'DS-2',[other['id']])
            assert observations.get(timeout=6)==(2,2)
            replacement = files.file_write(ctx,'src/first.txt','changed',original['sha256'])
            assert observations.get(timeout=6)==(1,2)
            files.complete_design_item(ctx,'DS-1',[replacement['id']])
            assert observations.get(timeout=6)==(2,2)
        finally:
            done.touch()
        assert active.result(timeout=8)=='finished'


def test_kernel_stops_owned_process_when_requested(tmp_path,monkeypatch):
    marker=tmp_path/'owned-pid'
    command=[sys.executable,'-c','from pathlib import Path; import os,sys,time; '
             'Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(20)',str(marker)]
    monkeypatch.setattr(settings,'runtime',lambda *args:(sys.executable,{'credential_env':''},0))
    monkeypatch.setattr(kernel,'_command',lambda *args:command)
    context=stage(tmp_path,'opencode')
    def request_stop():
        return marker.is_file() and bool(marker.read_text())
    context['_stop_requested']=request_stop
    started=time.monotonic()
    with pytest.raises(ValueError,match='已停止本次执行'):
        kernel._launch(context,'test')
    assert marker.exists() and not psutil.pid_exists(int(marker.read_text()))
    assert time.monotonic()-started<10


@contextmanager
def local_gateway(respond=None):
    seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            raw=self.rfile.read(int(self.headers.get("Content-Length","0")))
            if self.headers.get("Content-Encoding")=="gzip":raw=gzip.decompress(raw)
            data=json.loads(raw)
            seen.append({"path":self.path,"model":data.get("model"),"stream":data.get("stream"),
                         "tools":[t.get("name") or t.get("function",{}).get("name") for t in data.get("tools",[])],
                         "authorized":self.headers.get("x-api-key")=="test-local" or
                                      self.headers.get("Authorization")=="Bearer test-local"})
            if self.path.endswith("count_tokens"):
                self.send_response(200);self.end_headers();self.wfile.write(b'{"input_tokens":200}');return
            value=respond(data) if respond else _response()
            tool=value.get("_call") if isinstance(value,dict) else None
            answer=value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
            usage={"input_tokens":100,"output_tokens":20}
            if "/v1/messages" in self.path:
                msg={"id":"msg_cirven","type":"message","role":"assistant","model":data["model"],
                     "content":[],"stop_reason":None,"stop_sequence":None,"usage":usage}
                if not data.get("stream"):
                    msg.update(content=[dict(type="text",text=answer)],stop_reason="end_turn")
                    self.send_response(200);self.send_header("Content-Type","application/json");self.end_headers()
                    self.wfile.write(json.dumps(msg).encode());return
                events=[{"type":"message_start","message":msg},
                        {"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}},
                        {"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":answer}},
                        {"type":"content_block_stop","index":0},
                        {"type":"message_delta","delta":{"stop_reason":"end_turn","stop_sequence":None},"usage":usage},
                        {"type":"message_stop"}]
                if tool:
                    events[1]["content_block"]={"type":"tool_use","id":"call_"+str(len(seen)),"name":tool[0],"input":{}}
                    events[2]["delta"]={"type":"input_json_delta","partial_json":json.dumps(tool[1])}
                    events[4]["delta"]["stop_reason"]="tool_use"
                stream="".join("event: "+e["type"]+"\ndata: "+json.dumps(e)+"\n\n" for e in events)
            elif "/chat/completions" in self.path:
                def chunk(delta,finish):
                    return dict(id="chatcmpl-cirven",object="chat.completion.chunk",created=100,model=data["model"],
                                choices=[dict(index=0,delta=delta,finish_reason=finish)])
                delta={"role":"assistant","content":answer}
                finish="stop"
                if tool:
                    delta={"role":"assistant","tool_calls":[{"index":0,"id":"call_"+str(len(seen)),
                           "type":"function","function":{"name":tool[0],"arguments":json.dumps(tool[1])}}]}
                    finish="tool_calls"
                stream="".join("data: "+json.dumps(v)+"\n\n" for v in
                    [chunk(delta,None),chunk({},finish)])+"data: [DONE]\n\n"
            else:
                self.send_error(404);return
            self.send_response(200);self.send_header("Content-Type","text/event-stream");self.end_headers()
            self.wfile.write(stream.encode())
    server=ThreadingHTTPServer(("127.0.0.1",0),Handler)
    thread=threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:yield "http://127.0.0.1:"+str(server.server_port),seen
    finally:server.shutdown();server.server_close();thread.join(2)


@pytest.mark.parametrize("engine",["opencode","claude-code"])
@pytest.mark.parametrize("variant",["omitted","duplicate-observation"])
def test_original_kernels_cannot_approve_an_omitted_acceptance_criterion(client,monkeypatch,engine,variant):
    from copy import deepcopy
    from urllib.parse import urlsplit
    from test_engineering_stage_contracts import APPROVED, INPUT, add_specialty_actions
    from test_stage_workflow_runs import action, define, node, ok, scope, start

    if not settings.bundled(engine):
        pytest.skip("No native workflow kernel installed for this platform")
    service=client.app.state.stage_workflows
    monkeypatch.setattr(kernel,"TIME_LIMIT",45)
    owner=scope(client,'Native kernel acceptance audit')
    define(client,owner,[{**node('R'),'kind':'requirements'},{**node('C',['R']),'kind':'cases'}])
    run=start(client,owner)
    requirements=deepcopy(APPROVED["requirements"])
    requirements["items"][0]["acceptance"].append("首次失败时显示可观察的错误提示")
    service.executor=lambda _: {"artifact":requirements}
    run=ok(action(client,owner,run,'requirements-execute','execute',{'user_input':INPUT}))
    run=ok(action(client,owner,run,'requirements-approve','approve'))
    complete=False
    received_feedback=[]
    def reply(body):
        artifact=deepcopy(APPROVED["cases"])
        case=artifact["items"][0]
        if variant=="duplicate-observation":
            original = {"id":"observe","method":"container_suite","argv":["./controller"],
                        "image_id":"sha256:"+"1"*64,"timeout_seconds":10,"expected_output":"ready"}
            case["actions"] = [original]
            copied = deepcopy(case)
            copied.update(id="TC-2",requirement_ids=["OR-1"],coverage=[])
            copied["actions"] = [{**original,"id":"observe-error","timeout_seconds":30}]
            copied["acceptance_refs"] = [{"requirement_id":"OR-1","criterion":2,"action_id":"observe-error"}]
            artifact["items"].append(copied)
        case["acceptance_refs"]=[{"requirement_id":"OR-1","criterion":1,"action_id":"observe"}]
        if complete:
            prompt = json.dumps(body,ensure_ascii=False)
            received_feedback.append("OR-1 第 2 条通过标准尚缺验收动作" in prompt and
                                     (variant!="duplicate-observation" or "TC-2 / observe-error 与 TC-1 / observe" in prompt))
            if variant=="duplicate-observation":
                copied["actions"][0].update(argv=["./controller","--simulate-start-failure"],expected_output="startup error")
            else:
                case["actions"].append({"id":"observe-error","method":"manual_observation",
                                        "instruction":"引发启动失败","expected":"看到可观察的错误提示"})
                case["acceptance_refs"].append({"requirement_id":"OR-1","criterion":2,"action_id":"observe-error"})
        add_specialty_actions(case)
        return artifact
    service.executor=kernel.execute_stage
    with local_gateway(reply) as (url,seen):
        settings.save_route(service.data_dir,engine,0,dict(endpoint=url+("/v1" if engine=="opencode" else ""),
            model="cirven-coverage-fixture",credential_env="",allow_http=False))
        run=ok(action(client,owner,run,'pin-native','configure',{'kernel':engine}))
        skipped=ok(action(client,owner,run,'native-omit','execute'))
        issues = skipped['stages'][-1]['approval_issues']
        assert "OR-1 第 2 条通过标准尚缺验收动作" in issues
        if variant=="duplicate-observation":
            assert any("TC-2 / observe-error" in issue and "TC-1 / observe" in issue for issue in issues)
        assert action(client,owner,skipped,'native-false-pass','approve').status_code==409
        complete=True
        corrected=ok(action(client,owner,skipped,'native-correct','execute'))
        assert not corrected['stages'][-1]['approval_issues']
        assert corrected['stages'][-1]['stage_session_id']==skipped['stages'][-1]['stage_session_id']
        assert ok(action(client,owner,corrected,'native-approved','approve'))['status']=='completed'
    assert received_feedback and all(received_feedback)
    messages=[row for row in seen if urlsplit(row["path"]).path.endswith(("messages","chat/completions"))]
    assert len(messages)>=2 and all(row["model"]=="cirven-coverage-fixture" for row in messages)


@pytest.mark.parametrize("engine", ["opencode", "claude-code"])
def test_specialty_labels_and_incomplete_receipts_cannot_pass_with_native_kernels(client, monkeypatch, engine):
    from copy import deepcopy
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED, INPUT, add_specialty_actions
    from test_stage_workflow_runs import action, define, node, ok, scope, start

    if not settings.bundled(engine):
        pytest.skip("No native workflow kernel installed for this platform")
    service = client.app.state.stage_workflows
    monkeypatch.setattr(kernel, "TIME_LIMIT", 45)
    owner = scope(client, "Specialty coverage and observations")
    define(client, owner, [{**node('R'),'kind':'requirements'}, {**node('C',['R']),'kind':'cases'},
                           {**node('T',['C']),'kind':'testing'}])
    run = start(client, owner)
    assert run['contract_version'] == CONTRACT_VERSION
    requirements = deepcopy(APPROVED['requirements'])
    requirements['items'][0]['surfaces'].append('hardware')
    service.executor = lambda _: {'artifact':requirements}
    run = ok(action(client,owner,run,'execute-req','execute',{'user_input':INPUT}))
    run = ok(action(client,owner,run,'approve-req','approve'))
    corrected = False
    retry_prompts = []

    def reply(body):
        artifact = deepcopy(APPROVED['cases'])
        case = artifact['items'][0]
        case['coverage'].append('hardware')
        if corrected:
            retry_prompts.append(json.dumps(body,ensure_ascii=False))
            case['actions'] = []
            add_specialty_actions(case)
        else:
            case['actions'] = [{'id':'file','method':'text_equals','path':'status.txt','expected':'ready'}]
            case['coverage_refs'] = [{'coverage':kind,'action_id':'file'} for kind in case['coverage']]
        return artifact

    service.executor = kernel.execute_stage
    with local_gateway(reply) as (url, _):
        settings.save_route(service.data_dir,engine,0,dict(endpoint=url+("/v1" if engine=="opencode" else ""),
            model="cirven-specialty-fixture",credential_env="",allow_http=False))
        run = ok(action(client,owner,run,'use-native','configure',{'kernel':engine}))
        run = ok(action(client,owner,run,'fabricated-coverage','execute'))
        blocked = "TC-1 / file 的方法无法核验硬件联动专项"
        assert any(blocked in detail for detail in run['stages'][1]['approval_issues'])
        assert action(client,owner,run,'reject-specialty-labels','approve').status_code==409
        corrected = True
        run = ok(action(client,owner,run,'concrete-coverage','execute'))
        assert not run['stages'][1]['approval_issues']
        assert any(blocked in prompt for prompt in retry_prompts)
        run = ok(action(client,owner,run,'approve-specialty-plan','approve'))

    case = run['stages'][1]['artifact']['items'][0]
    child = {**owner,'conversation_id':run['stages'][2]['stage_session_id']}

    def observe(current, item):
        return ok(action(client,child,current,'record-'+item['id'],'record_manual_check',{
            'case_id':case['id'],'action_id':item['id'],'status':'passed',
            'environment':'受控测试夹具','observation':item['expected']}))

    for check in case['actions'][:-1]:
        run = observe(run,check)
    assert run['stages'][2]['artifact']['items'][0]['status']=='not_run'
    assert action(client,owner,run,'reject-unobserved-specialty','approve').status_code==409
    run = observe(run,case['actions'][-1])
    assert run['stages'][2]['artifact']['items'][0]['status']=='passed'
    assert not run['stages'][2]['approval_issues']
    assert ok(action(client,owner,run,'approve-all-specialties','approve'))['status']=='completed'


@pytest.mark.parametrize("engine", ["opencode", "claude-code"])
def test_native_kernels_get_readback_but_cannot_override_sample_time(tmp_path, monkeypatch, engine):
    from copy import deepcopy
    from packages.domain.native_capabilities import NativeCapabilities
    from packages.domain.store import Store
    from packages.dynamic_workflow.stage_assertions import case_receipt_issues
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED
    from test_stage_connector_calls import install_readback_meter, readback_action

    if not settings.bundled(engine):
        pytest.skip("No native workflow kernel installed for this platform")
    monkeypatch.setattr(kernel, "TIME_LIMIT", 45)
    mode, _ = install_readback_meter(tmp_path, NativeCapabilities(Store(tmp_path/"register.sqlite3"), tmp_path))
    action = readback_action()
    base = {**stage(tmp_path,engine), "kind":"testing", "contract_version":CONTRACT_VERSION,
            "approved_by_kind":{"cases":{"items":[{"id":"TC-1","actions":[action]}]}}}
    base["capabilities"]["mcp"] = ["meter"]
    tool = ("cirven_" if engine=="opencode" else "mcp__cirven__")+"call_connector"
    payload = {"case_id":"TC-1", "server":"meter", "tool":"measure", "arguments":action["arguments"],
               "check_pointer":action["pointer"],"expected_json":action["expected_json"]}
    returned = []

    def reply(body):
        names = {item.get("name") or item.get("function",{}).get("name") for item in body.get("tools",[])}
        if tool not in names:
            return "Cirven test"
        records = files.operation_receipts(ctx)
        if not records:
            return {"_call":(tool,payload)}
        record, = records
        messages = json.dumps(body.get("messages",[]),ensure_ascii=False)
        returned.append(record["id"] in messages and "hardware_readback" in messages and "DUT-07" in messages)
        artifact = deepcopy(APPROVED["testing"])
        artifact["items"][0].update(status="passed",evidence_refs=[record["id"]],rework=[])
        return artifact

    with local_gateway(reply) as (url, _):
        settings.save_route(tmp_path,engine,0,dict(endpoint=url+("/v1" if engine=="opencode" else ""),
            model="cirven-hardware-fixture",credential_env="",allow_http=False))
        for variant in ("cached","fresh"):
            ctx = {**base,"operation_id":"read-"+variant}
            mode.write_text(variant)
            result = kernel.execute_stage(ctx)
            record, = result["verified_evidence"]
            assert (record["status"]=="passed") == (variant=="fresh")
            errors = case_receipt_issues(result["artifact"],base["approved_by_kind"],[record])
            assert bool(errors) == (variant=="cached")
    assert returned and all(returned)


@pytest.mark.parametrize("engine",["opencode","claude-code"])
@pytest.mark.parametrize("variant",["exact-output","contrast-input","direct-program"])
def test_native_kernels_receive_and_cannot_override_container_observation(tmp_path,monkeypatch,engine,variant):
    from copy import deepcopy
    from packages.dynamic_workflow.stage_assertions import case_receipt_issues
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED

    if os.name!="posix" or not settings.bundled(engine):
        pytest.skip("Requires a native kernel and the test-owned POSIX OCI driver")
    paired = variant!="exact-output"
    direct = variant=="direct-program"
    image = "sha256:"+"1"*64
    driver = tmp_path/"docker"
    driver.write_text(f"#!{sys.executable}\n"
        "import csv, json, pathlib, subprocess, sys, tempfile\n"
        "if sys.argv[1]=='image':\n"
        "    print(json.dumps([{'Id':sys.argv[-1],'Os':'linux','Config':{}}]))\n"
        "elif sys.argv[1]=='run':\n"
        "    mode=pathlib.Path(__file__).with_name('observed.txt').read_bytes()\n"
        "    boot=sys.argv[sys.argv.index('-c')+1]\n"
        "    if 'Approved program digest mismatch' in boot and mode!=b'constant':\n"
        "        descriptor=next(csv.reader([sys.argv[sys.argv.index('--mount')+1]]))\n"
        "        source=next(item.removeprefix('source=') for item in descriptor if item.startswith('source='))\n"
        "        boot,label,delay,_,_,path,sha,marker,*args=sys.argv[sys.argv.index('-c')+1:]\n"
        "        with tempfile.TemporaryDirectory() as work:\n"
        "            code=subprocess.run(['/bin/sh','-c',boot,label,delay,source,str(pathlib.Path(work)/'run'),path,sha,marker,*args],input=sys.stdin.buffer.read()).returncode\n"
        "        sys.exit(code)\n"
        "    elif '--interactive' not in sys.argv:\n"
        "        sys.stdout.buffer.write(mode)\n"
        "    elif sys.stdin.buffer.read()==b'BAD\\n' and mode!=b'constant':\n"
        "        print('status=retry'); sys.exit(2)\n"
        "    else:\n"
        "        print('status=ready')\n",
        encoding="utf-8")
    driver.chmod(0o700)
    observed = tmp_path/"observed.txt"
    monkeypatch.setenv("CIRVEN_WORKFLOW_CONTAINER_EXE",str(driver))
    monkeypatch.setenv("CIRVEN_WORKFLOW_CONTAINER_IMAGE",image)
    monkeypatch.setattr(kernel,"TIME_LIMIT",45)
    case = {"id":"TC-1","actions":[{"id":"status","method":"container_suite","argv":["./approved-status"],
             "timeout_seconds":15,"image_id":image,"expected_output":"status=ready\n"}]}
    if paired:
        case["actions"][0]["input_pair"] = {"normal_stdin":"READY\n","control_stdin":"BAD\n",
            "control_expected_output":"status=retry\n","control_exit_code":2}
    base = {**stage(tmp_path,engine,["run_check"]),"kind":"testing","contract_version":CONTRACT_VERSION,
            "approved_by_kind":{"cases":{"items":[case]}}}
    command = ["./approved-status"]
    if direct:
        subject = files.file_write({**base,"stage_session_id":"development"},"dist/status",
            '#!/bin/sh\nread -r input\nif [ "$input" = READY ]; then echo status=ready; else echo status=retry; exit 2; fi\n',"")
        identity = {"path":subject["path"],"sha256":subject["sha256"],"file_receipt_id":subject["id"],
                    "node_id":"dev","stage_session_id":"development","approval_sha256":"a"*64}
        base.update(approved_evidence=[subject],approved_subjects={subject["path"]:identity},
                    require_runtime_target=True)
        case["coverage_refs"] = [{"coverage":"runtime","action_id":"status"}]
        command = ["./dist/status"]
        case["actions"][0].update(argv=command,subject_path=subject["path"])
    tool=("cirven_" if engine=="opencode" else "mcp__cirven__")+"run_suite"
    received = []
    def reply(body):
        names = {item.get("name") or item.get("function",{}).get("name") for item in body.get("tools",[])}
        if tool not in names:
            return "Cirven test"
        records = files.operation_receipts(ctx)
        if not records:
            return {"_call":(tool,{"case_id":"TC-1","argv":command,"timeout_seconds":15})}
        record, = records
        messages = json.dumps(body.get("messages",[]),ensure_ascii=False)
        received.append(record["id"] in messages and "output_matches" in messages
                        and (not paired or "expected_exit_code" in messages))
        artifact = deepcopy(APPROVED["testing"])
        artifact["items"][0].update(status="passed",evidence_refs=[record["id"]],rework=[])
        return artifact
    with local_gateway(reply) as (gateway,seen):
        settings.save_route(tmp_path,engine,0,dict(endpoint=gateway+("/v1" if engine=="opencode" else ""),
            model="cirven-observation-fixture",credential_env="",allow_http=False))
        for index,actual in enumerate([b"",b"status=ready\n"]):
            ctx = {**base,"operation_id":f"tested-{index}"}
            observed.write_bytes(b"constant" if paired and index==0 else actual)
            result = kernel.execute_stage(ctx)
            record, = result["verified_evidence"]
            assert record["exit_code"]==0
            if paired:
                assert record["output_matches"] and record["control"]["output_matches"]==bool(actual)
                assert record["control"]["exit_code"]==(2 if actual else 0)
            else:
                assert record["output_matches"]==bool(actual)
            if direct:
                assert record.get("subject")== (identity if actual else None)
            assert bool(case_receipt_issues(result["artifact"],base["approved_by_kind"],[record]))!=bool(actual)
    assert len(received)>=2 and all(received)
    assert any(tool in row["tools"] for row in seen)


@pytest.mark.parametrize("engine",["opencode","claude-code"])
def test_original_kernel_registers_only_complete_design_files(tmp_path,monkeypatch,engine):
    from copy import deepcopy
    from packages.dynamic_workflow.stage_runs import StageRuns
    from packages.dynamic_workflow.stage_assertions import count_complete_design_items
    from packages.dynamic_workflow.stage_contracts import CONTRACT_VERSION
    from test_engineering_stage_contracts import APPROVED

    if not settings.bundled(engine):
        pytest.skip("No native workflow kernel installed for this platform")
    monkeypatch.setattr(kernel,"TIME_LIMIT",45)
    ctx=stage(tmp_path,engine,["workspace_write"])
    first=files.file_write({**ctx,"stage_session_id":"last-approved"},"src/start.py","previous implementation","")
    second=files.file_write(ctx,"src/recover.py","fixed implementation","")
    design={"id":"DS-1","directory":"src","implementation_files":[first["path"],second["path"]]}
    ctx.update(kind="development",contract_version=CONTRACT_VERSION,approved_evidence=[first],
               prior_evidence=[first,second],approved_by_kind={"design":{"items":[design]}})
    tool=("cirven_" if engine=="opencode" else "mcp__cirven__")+"complete_design_item"
    feedback=[]
    for corrected in (False,True):
        submitted=False
        refs=[first["id"],second["id"]] if corrected else [first["id"]]
        def reply(body):
            nonlocal submitted
            names = {item.get("name") or item.get("function",{}).get("name") for item in body.get("tools",[])}
            if tool not in names:
                return "Cirven design review"
            if not submitted:
                submitted=True
                return {"_call":(tool,{"design_id":"DS-1","file_receipts":refs})}
            artifact = deepcopy(APPROVED["development"])
            if not corrected:
                feedback.append("缺少必交文件的当前回执：src/recover.py" in json.dumps(body,ensure_ascii=False))
            elif records := files.operation_receipts(attempt):
                artifact["items"][0]["evidence_refs"] = [records[0]["id"]]
            return artifact
        attempt={**ctx,"operation_id":"complete" if corrected else "skip-file"}
        with local_gateway(reply) as (url,seen):
            settings.save_route(tmp_path,engine,int(corrected),dict(endpoint=url+("/v1" if engine=="opencode" else ""),
                model="cirven-design-files-fixture",credential_env="",allow_http=False))
            output=kernel.execute_stage(attempt)
        assert any(tool in row["tools"] for row in seen)
        assert output["artifact"]["items"][0]["status"]=="done"
        current=output["verified_evidence"]
        assert len(current)==int(corrected)
        evidence=[*ctx["prior_evidence"],*current]
        assert count_complete_design_items([design],evidence,require_design_output=True)==int(corrected)
        assert bool(StageRuns._evidence_issues("development",output["artifact"],evidence,
                                              designs={design["id"]:design}))!=corrected
        if corrected:
            assert current[0]["file_refs"]==refs
    assert feedback and all(feedback)


@pytest.mark.parametrize("engine",["opencode","claude-code"])
def test_original_kernel_stops_repeated_rejected_writes_and_preserves_receipt(tmp_path,monkeypatch,engine):
    if not settings.bundled(engine):
        pytest.skip("No native workflow kernel installed for this platform")
    monkeypatch.setattr(kernel,"TIME_LIMIT",35)
    ctx=stage(tmp_path,engine,["workspace_write"])
    tool=("cirven_" if engine=="opencode" else "mcp__cirven__")+"workspace_write"
    attempts=[]
    def reply(_):
        attempts.append(tool)
        if len(attempts)>12:
            return _response()
        return {"_call":(tool,{"path":"src/status.json","content":"{}","expected_sha256":""})}
    with local_gateway(reply) as (url,seen):
        settings.save_route(tmp_path,engine,0,dict(endpoint=url+("/v1" if engine=="opencode" else ""),
            model="cirven-loop-fixture",credential_env="",allow_http=False))
        with pytest.raises(ValueError,match="同一工具操作连续失败"):
            kernel.execute_stage(ctx)
    assert 4<=len(attempts)<=12,len(attempts)
    assert any(tool in row["tools"] for row in seen)
    file,=files.operation_receipts(ctx)
    assert (file["kind"],file["path"])==("file","src/status.json")
    assert files.read_evidence_file(tmp_path,file)==("status.json",b"{}")


def test_tool_failure_streak_changes_with_arguments_or_a_success(tmp_path):
    from packages.dynamic_workflow.stage_tool_server import ToolError, _ToolBudget, _guard

    marker=tmp_path/"node.limit"
    budget=_ToolBudget(marker)
    observed=[]
    def change(path,expected=""):
        observed.append((path,expected))
        if expected!="current":
            raise ValueError("File already exists")
        return path
    write=_guard({},change,budget)
    for path in ("first","first","second","second"):
        with pytest.raises(ToolError,match="File already exists"):
            write(path)
    assert write("second",expected="current")=="second"
    for _ in range(2):
        with pytest.raises(ToolError,match="File already exists"):
            write("second")
    assert not marker.exists()
    with pytest.raises(ToolError,match="File already exists"):
        write(path="second",expected="")
    assert marker.read_text()=="repeated-error"
    with pytest.raises(ToolError,match="停止条件"):
        write("different",expected="current")
    assert len(observed)==8


def test_tool_dispatch_budget_covers_external_calls_and_keeps_its_first_stop_reason(tmp_path,monkeypatch):
    from packages.dynamic_workflow import stage_tool_server as broker

    monkeypatch.setattr(broker,"TOOL_CALL_LIMIT",3)
    marker=tmp_path/"node.limit"
    budget=broker._ToolBudget(marker)
    seen=[]
    async def device_status():
        seen.append("poll")
        return "ready" if len(seen)==3 else "pending"
    poll=broker._guard({},device_status,budget)
    async def exercise():
        assert [await poll() for _ in range(3)]==["pending","pending","ready"]
        with pytest.raises(broker.ToolError,match="调用已达上限"):
            await poll()
        with pytest.raises(broker.ToolError,match="停止条件"):
            await poll()
    asyncio.run(exercise())
    assert len(seen)==3 and marker.read_text()=="tool-count"


@pytest.mark.parametrize("engine",["opencode","claude-code"])
def test_real_original_cli_with_only_selected_mcp_tools(tmp_path,monkeypatch,engine):
    if not settings.bundled(engine):pytest.skip("No native workflow kernel installed for this platform")
    monkeypatch.setenv("CIRVEN_STAGE_TEST_KEY","test-local")
    monkeypatch.setattr(kernel,"TIME_LIMIT",55)
    ctx=stage(tmp_path,engine,["workspace_read"])
    with local_gateway() as (base,seen):
        settings.save_route(tmp_path,engine,0,dict(endpoint=base+("/v1" if engine=="opencode" else ""),
            model="cirven-internal-qa",credential_env="CIRVEN_STAGE_TEST_KEY",allow_http=False))
        ctx["policy_pin"]=settings.freeze_policy(tmp_path,ctx)
        try:
            result=kernel.execute_stage(ctx)
        finally:
            print("WORKFLOW_ENGINE_WIRE",engine,json.dumps(seen,ensure_ascii=False))
        assert result["questions"]==_response()["questions"] and result["verified_evidence"]==[]
        generations=[s for s in seen if s["authorized"] and s["model"]=="cirven-internal-qa"]
        assert generations
        offered={t for g in generations for t in g["tools"]}
        expected=("cirven_workspace_read" if engine=="opencode" else "mcp__cirven__workspace_read")
        assert expected in offered
        assert not offered-{expected},offered
        assert not list((settings.home(tmp_path)/"sessions").iterdir())


def test_scoped_file_and_case_receipts(tmp_path):
    ctx=stage(tmp_path,"opencode")
    ctx["approved_by_kind"]={"design":{"items":[{"id":"DS-1"}]}}
    receipt=files.file_write(ctx,"src/status.json",'{"ok":true}',"")
    closed=files.complete_design_item(ctx,"DS-1",[receipt["id"]])
    assert closed["file_refs"]==[receipt["id"]]
    isolated={**ctx,"stage_session_id":"node-2","approved_by_kind":{"cases":{"items":[{"id":"TC-1","actions":[
        {"id":"contents","method":"text_equals","path":"src/status.json","expected":"no"},
        {"id":"parse","method":"json_valid","path":"src/status.json","expected":""}]}]}}}
    assert files.file_list(isolated)==[]
    isolated["approved_evidence"]=[receipt]
    bad=files.check(isolated,"TC-1","src/status.json","text_equals","no")
    good=files.check(isolated,"TC-1","src/status.json","json_valid","")
    assert bad["exit_code"]==1 and good["exit_code"]==0
    with pytest.raises(ValueError):files.check(isolated,"TC-2","src/status.json","json_valid","")
    with pytest.raises(ValueError):files.file_write(ctx,"../escape","oops","")
    with pytest.raises(ValueError):files.file_write(ctx,"src/status.json","v2","")
    assert files.read_evidence_file(tmp_path,receipt)==("status.json",b'{"ok":true}')
    with pytest.raises(ValueError):files.read_evidence_file(tmp_path,{**receipt,"path":"x"})


def test_managed_update_rollback_and_budget(tmp_path):
    ctx=stage(tmp_path,"opencode")
    with pytest.raises(ValueError):files.claim_delegate(ctx)
    ctx["max_subagents"]=1
    files.claim_delegate(ctx)
    with pytest.raises(ValueError):files.claim_delegate(ctx)
    source=tmp_path/"source.bin"
    digests=[]
    import hashlib
    for i in range(2):
        source.write_bytes(bytes([i+1]));h=hashlib.sha256(source.read_bytes()).hexdigest();digests.append(h)
        settings.install(tmp_path,"opencode",i,str(source),h,f"v{i}")
    settings.activate(tmp_path,"opencode",2,digests[0])
    assert settings.catalog(tmp_path)["kernels"][0]["active"]==digests[0]
    with pytest.raises(Conflict):settings.activate(tmp_path,"opencode",2,digests[1])
    with pytest.raises(InvalidInput):settings.install(tmp_path,"opencode",3,str(source),"f"*64,"v3")


def test_owned_native_delegate_has_no_tools_and_exhausts_node_limit(tmp_path,monkeypatch):
    engine="claude-code"
    if not settings.bundled(engine):pytest.skip("Claude Code unavailable")
    calls=[]
    def answer(data):
        text=json.dumps(data["messages"],ensure_ascii=False)
        if not data.get("tools"):
            calls.append("child")
            return {"summary":"independent answer","items":[]}
        results=[part for msg in data["messages"] if isinstance(msg.get("content"),list)
                 for part in msg["content"] if part.get("type")=="tool_result"]
        calls.append("parent")
        if len(results)<2:return {"_call":("mcp__cirven__delegate",{"question":"Read only this isolated child question."})}
        print("DELEGATE_RESULTS",json.dumps(results,ensure_ascii=False)[:1800],flush=True)
        assert "independent answer" in str(results[0]) and results[1].get("is_error") is True
        return _response()
    monkeypatch.setenv("CIRVEN_WORKFLOW_TEST_AUTH","test-local")
    ctx=stage(tmp_path,engine,["delegate"])
    ctx["max_subagents"]=1
    with local_gateway(answer) as (url,_seen):
        settings.save_route(tmp_path,engine,0,dict(endpoint=url,model="cirven-qa",credential_env="CIRVEN_WORKFLOW_TEST_AUTH",allow_http=False))
        assert kernel.execute_stage(ctx)["questions"]==_response()["questions"]
        assert calls.count("child")==1 and calls.count("parent")==3


def test_binary_batch_and_repeatable_zip(tmp_path):
    from io import BytesIO
    from zipfile import ZipFile
    ctx=stage(tmp_path,"opencode")
    one=files.file_write_bytes(ctx,"boot.bin",b"\xff\x00","")
    with pytest.raises(ValueError):
        files.file_write_batch(ctx,[("new.bin",b"new",""),("boot.bin",b"bad","")])
    assert [f["path"] for f in files.file_list(ctx)]==["boot.bin"]
    foreign={**ctx,"stage_session_id":"delivery","approved_evidence":[one]}
    archive=files.package_files(foreign,"delivery.zip",["boot.bin"])
    name,raw=files.read_evidence_file(tmp_path,archive)
    assert name=="delivery.zip" and ZipFile(BytesIO(raw)).read("boot.bin")==b"\xff\x00"
    assert files.package_files(foreign,"delivery.zip",["boot.bin"],archive["sha256"])["sha256"]==archive["sha256"]


@pytest.mark.parametrize("engine",["opencode","claude-code"])
def test_stage_question_uses_real_kernel_without_granting_stage_tools(tmp_path,monkeypatch,engine):
    if not settings.bundled(engine):
        pytest.skip("No native workflow kernel installed for this platform")
    monkeypatch.setenv("CIRVEN_STAGE_TEST_KEY","test-local")
    monkeypatch.setattr(kernel,"TIME_LIMIT",55)
    ctx=stage(tmp_path,engine,["workspace_read","workspace_write","delegate"])
    ctx.update(stage_title="需求梳理",artifact={"summary":"待审核"},approved_inputs={},history=[],
               question="查核这条的通过标准",annotation={"target":"OR-001","comment":"留存原文"})
    authored="OR-001 的通过标准需要验证导出文件中的条目编号。"
    def reply(body):
        sent=json.dumps(body["messages"],ensure_ascii=False)
        assert "只读咨询" in sent
        if "UR-A001" in sent:
            assert "保留审核时间" in sent and "已生成带审核时间的清单" in sent
        else:
            assert "OR-001" in sent and "留存原文" in sent
        return authored
    with local_gateway(reply) as (base,seen):
        settings.save_route(tmp_path,engine,0,dict(endpoint=base+("/v1" if engine=="opencode" else ""),
            model="cirven-internal-qa",credential_env="CIRVEN_STAGE_TEST_KEY",allow_http=False))
        ctx["policy_pin"]=settings.freeze_policy(tmp_path,ctx)
        assert kernel.answer_stage_question(ctx)==authored
        ctx.update(annotation=None,follow_up={'source_id':'UR-A001','text':'保留审核时间','status':'processed',
                                              'execution_summary':'已生成带审核时间的清单'})
        assert kernel.answer_stage_question(ctx)==authored
        generations=[v for v in seen if v['authorized']]
        assert generations and all(not v["tools"] for v in generations)
        assert files.operation_receipts(ctx)==[]
