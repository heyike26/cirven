"""Pure local collector regression. No API, SDK, model, GUI or Windows."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from packages.agent_sessions.service import AgentSessions
from packages.domain.store import Store
from packages.domain.tasks import digest, now
from apps.api.native_messages import NativeQuestionInput

from apps.api.supervisor import ApplicationTaskCollector as Collector

class ReadOnlySources:
    def __init__(self, root):self.root=root;self.reads=[]
    def _path(self, *parts):return self.root.joinpath(*parts)
    def _read(self, path, *, authority=False):
        assert authority is False
        self.reads.append(Path(path))
        wrapper=json.loads(Path(path).read_bytes())
        assert digest(wrapper["payload"]) == wrapper["sha256"]
        return wrapper["payload"]
    def write(self, path, value):
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(json.dumps({"payload":value,"sha256":digest(value)}))


def setup_case(tmp_path, case="exact"):
    store=Store(tmp_path/"state.sqlite");store.migrate()
    command_pid=store.create_project("Source38 原命令原生验收", "")['id']
    pid=store.create_project("EEAS conversation", "eeas.original-agent-session.internal-project.v1")['id']
    sessions=AgentSessions(store,tmp_path,provider_registry=None,workspaces=None,capabilities=None)
    sessions.inspect=lambda *_:pytest.fail("Collector must never inspect/start an owner")
    sid="a"*32;command_sid="b"*32;parent="supervisor-op";inner_id="ordinary-send-"+digest(parent)
    profile={"id":"fixture-profile","revision":1,"sha256":"c"*64}
    body=NativeQuestionInput(operation_id=parent,raw_text="Same user question. Not a title/prefix filter.",
        conversation_id="supervisor_conversation",provider_profile_ref=profile).model_dump()
    frozen_request={"project_id":None,"provider_profile_ref":profile,"prompt":"PRIVATE_FROZEN_PROMPT"}
    admission={"schema":"eeas.native-message-admission.v1","parent_operation_id":parent,
        "body":deepcopy(body),"body_sha256":digest(body),"request":frozen_request,
        "request_sha256":digest(frozen_request),"project_id":None,
        "conversation_id":body["conversation_id"],"provider_profile_ref":profile,
        "source_host_generation":"f"*32}
    if case=="admission_conversation":admission["conversation_id"]="another_conversation"
    if case=="admission_project":admission["project_id"]=command_pid
    admission_ref={"id":parent,"revision":1,"sha256":digest(admission)}
    manifest={"id":sid,"project_id":pid,"request":{"provider_profile_ref":profile},
        "native_message_admission":admission_ref}
    if case=="no_admission":manifest.pop("native_message_admission")
    if case=="wrong_admission_ref":manifest["native_message_admission"]={**admission_ref,"sha256":"0"*64}
    sessions._write(sessions._path(pid,sid,"manifest.json"),manifest)
    reference=sessions._manifest(pid,sid)[1]
    request={"operation_id":inner_id,"generation":1,"session_ref":reference,"raw_text":body["raw_text"]}
    if case=="different_input":request["raw_text"]="An actually different original request"
    op={"id":inner_id,"kind":"MESSAGE","status":"RETURNED","generation":1,"session_ref":reference,
        "request":request,"request_sha256":digest(request),
        "operation_ref":{"id":inner_id,"request_sha256":digest(request)},
        "native_message_id":"native_user_one","original_session_id":"original_session_one"}
    if case=="wrong_operation_hash":op["request_sha256"]="1"*64
    if case=="bool_generation":op["generation"]=True;request["generation"]=True
    def save_session(project,ident,operation):
        sessions._write(sessions._path(project,ident,"state.json"),{"generation":1,"active_operation_id":None,
            "pending_questions":[],"pending_permissions":[],"observation_at":now()})
        sessions._write(sessions._path(project,ident,"operations",digest(operation["id"])+".json"),operation)
    save_session(pid,sid,op)
    sessions._write(sessions._path(command_pid,command_sid,"manifest.json"),
        {"id":command_sid,"project_id":command_pid,"request":{},"title":"EEAS conversation"})
    save_session(command_pid,command_sid,{"id":"real-command","kind":"MESSAGE","status":"RETURNED",
        "operation_ref":{"id":"real-command","request_sha256":"2"*64},"request":{"generation":1}})
    cohort={"native_message_ref":admission_ref,"parent_operation_id":parent,"owner_ref":reference,
        "generation":1,"native_operation_id":inner_id,"native_user_message_id":"native_user_one",
        "native_session_id":"original_session_one","sequence":1}
    cohort["cohort_sha256"]=digest(cohort)
    record={"request":body,"request_sha256":digest(body),"generation":"f"*32,"status":"RETURNED",
        "native_message_ref":admission_ref,"native_operation_id":inner_id,
        "owner":{"project_id":pid,"session_ref":reference,"generation":1},"ordinary_cohort":cohort}
    if case=="wrong_owner_generation":record["owner"]["generation"]=2
    if case=="wrong_owner_reference":record["owner"]["session_ref"]={**reference,"sha256":"3"*64}
    if case=="wrong_cohort":record["ordinary_cohort"]["native_user_message_id"]="someone_else"
    if case=="wrong_operation_id":record["native_operation_id"]="unrelated-operation"
    if case=="outer_request_hash":record["request_sha256"]="4"*64
    sources=ReadOnlySources(tmp_path/"sources")
    sources.write(sources._path("native-message-sessions","admissions",parent+".json"),admission)
    if case!="outer_missing":sources.write(sources._path("native-messages",parent+".json"),record)
    if case=="admission_missing":sources._path("native-message-sessions","admissions",parent+".json").unlink()
    ordinary=SimpleNamespace(read=lambda kind,ident,authority=False:sources._read(
        sources._path("native-message-sessions",kind,ident+".json"),authority=authority))
    if case=="adapter_missing":ordinary=None
    messages=SimpleNamespace(ws=sources,generation="f"*32,ordinary=ordinary)
    collector=Collector(SimpleNamespace(state=SimpleNamespace(store=store,agent_sessions=sessions,native_messages=messages)))
    return collector,inner_id,sources


def test_exact_outer_and_inner_are_one_user_task_beside_independent_command(tmp_path):
    collector,inner,sources=setup_case(tmp_path)
    result=collector.collect_sync();rows=result["observations"]
    assert len(rows)==3, "Collector preserves all original facts"
    rows=collector.project_snapshot({"tasks":rows})["tasks"]
    assert len(rows)==2
    assert {r["key"]["kind"] for r in rows}=={"native_message","original_session"}
    assert {r["key"]["operation_id"] for r in rows}=={"supervisor-op","real-command"}
    assert result["unavailable_codes"]==[]
    assert collector.coverage["original_native_task_deduplication"]["verified_inner_outer_relations"]==1
    assert collector.coverage["model_calls"]==0 and collector.coverage["automatic_retry"] is False
    assert "PRIVATE_FROZEN_PROMPT" not in json.dumps(result)
    assert len(sources.reads)==2


@pytest.mark.parametrize("case",["outer_missing","wrong_admission_ref","no_admission","different_input",
    "wrong_operation_hash","bool_generation","wrong_owner_generation","wrong_owner_reference",
    "wrong_cohort","wrong_operation_id","outer_request_hash","admission_conversation",
    "admission_project","admission_missing","adapter_missing"])
def test_missing_or_mismatched_proof_retains_the_original_and_real_ordinary_work(tmp_path,case):
    collector,inner,_=setup_case(tmp_path,case)
    result=collector.collect_sync();rows=result["observations"]
    assert inner in {r["key"]["operation_id"] for r in rows}
    assert "real-command" in {r["key"]["operation_id"] for r in rows}
    assert collector.coverage["original_native_task_deduplication"]["verified_inner_outer_relations"]==0
    assert collector.coverage["model_calls"]==0


def test_previous_duplicate_survives_in_sqlite_but_default_projection_recovers_without_deletion(tmp_path):
    import asyncio
    from packages.agent_sessions.supervisor import GlobalSupervisor
    collector,inner,sources=setup_case(tmp_path)
    admission=sources._path("native-message-sessions","admissions","supervisor-op.json")
    saved=admission.read_bytes();admission.unlink()
    async def collect():return collector.collect_sync()
    supervisor=GlobalSupervisor(tmp_path/"supervisor-state",collect)
    async def scenario():
        before=await supervisor.poll_once()
        assert len(before["tasks"])==3 and len(collector.project_snapshot(before)["tasks"])==3
        admission.write_bytes(saved)
        after=await supervisor.poll_once()
        assert len(after["tasks"])==3
        projected=collector.project_snapshot(after)
        assert len(projected["tasks"])==2
        assert {row["key"]["operation_id"] for row in projected["tasks"]}=={"supervisor-op","real-command"}
        assert projected["alerts"]==after["alerts"] and projected["alerts_total"]==after["alerts_total"]
        assert any(row["key"]["operation_id"]==inner for row in supervisor.snapshot()["tasks"])
        # Existing exact links retain their original stored key/source_ref.
        original=next(row for row in after["tasks"] if row["key"]["operation_id"]==inner)
        assert any(row["key"]==original["key"] and row["source_ref"]==original["source_ref"] for row in supervisor.snapshot()["tasks"])
        admission.unlink()
        unavailable=await supervisor.poll_once()
        assert len(collector.project_snapshot(unavailable)["tasks"])==3
        await supervisor.close()
    asyncio.run(scenario())


def test_cached_projection_preserves_independent_attention_unknown_changed_or_missing_proof_without_reads(tmp_path):
    collector,inner,sources=setup_case(tmp_path)
    observations=collector.collect_sync()["observations"]
    snapshot={"tasks":deepcopy(observations),"alerts":[{"keep":"exact-original-history"}],"alerts_total":1}
    assert len(collector.project_snapshot(snapshot)["tasks"])==2
    before=deepcopy(snapshot)
    def reject_read(*args,**kwargs):pytest.fail("Default projection cannot read files/API/SDK/model")
    sources._read=reject_read;collector.collect_sync=reject_read
    for mutate in [
        lambda row:row.update(wait_reason="PERMISSION"),
        lambda row:row.update(wait_reason="USER_INPUT"),
        lambda row:row.update(actual_error_code="INDEPENDENT_ERROR"),
        lambda row:row.update(source_status="UNKNOWN_OUTCOME",execution_returned=None),
        lambda row:row.update(source_status="FAILED",execution_returned=None),
        lambda row:row.update(source_status="RUNNING",execution_returned=None),
        lambda row:row.update(observation_status="STALE"),
        lambda row:row.update(classification="NEEDS_CHECK"),
        lambda row:row.update(source_ref={**row["source_ref"],"sha256":"0"*64}),
    ]:
        changed=deepcopy(snapshot);row=next(row for row in changed["tasks"] if row["key"]["operation_id"]==inner);mutate(row)
        assert len(collector.project_snapshot(changed)["tasks"])==3
    missing=deepcopy(snapshot);missing["tasks"]=[r for r in missing["tasks"] if r["key"]["kind"]!="native_message"]
    assert len(collector.project_snapshot(missing)["tasks"])==2
    duplicate=deepcopy(snapshot);duplicate["tasks"].append(deepcopy(next(r for r in duplicate["tasks"] if r["key"]["kind"]=="native_message")))
    assert len(collector.project_snapshot(duplicate)["tasks"])==4
    assert snapshot==before


def test_unchanged_pending_collection_does_not_temporarily_reintroduce_a_duplicate(tmp_path):
    collector,inner,sources=setup_case(tmp_path)
    old={"tasks":collector.collect_sync()["observations"],"observer":{"status":"READY"}}
    assert len(collector.project_snapshot(old)["tasks"])==2
    original=collector._recent;probes=[]
    def during_same_original_collection(paths,errors,error_code):
        probes.append(len(collector.project_snapshot(old)["tasks"]))
        return original(paths,errors,error_code)
    collector._recent=during_same_original_collection
    collector.collect_sync()
    assert probes and set(probes)=={2}
    # If a whole collection cannot commit, the GlobalSupervisor marks its
    # observer unavailable; old proof cannot suppress the fallback in that view.
    for status in ["OBSERVATION_UNAVAILABLE","INITIAL","CLOSED","DISABLED"]:
        failed={**old,"observer":{"status":status}}
        assert len(collector.project_snapshot(failed)["tasks"])==3
