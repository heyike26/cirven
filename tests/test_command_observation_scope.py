"""AUTHORED_NOT_RUN: explicit observer scope, immutable configuration and legacy wire.

Real Store/observer files where stated; original SDK/process/model boundaries
remain UNIT. No native/whole-task qualification follows from these cases.
"""
import asyncio
from copy import deepcopy
from types import SimpleNamespace
import pytest
from pydantic import ValidationError

from packages.agent_sessions.models import OpenSession,OpenConversation
from packages.agent_sessions.command_observation_scope import observation_sources
from packages.agent_sessions.service import AgentSessions
from packages.adapters.opencode_command_observation import prepare,bind,read_history
from packages.domain.local_workspaces import LocalWorkspaces,WorkspaceInput
from packages.domain.store import Store
from packages.domain.store import Conflict,InvalidInput
from packages.domain.tasks import digest
from apps.api.conversation_configuration import ConfigureConversation,confirmation_value
from apps.api.canonical_conversations import CanonicalConversations
from test_conversation_configuration import configured,packet
from test_canonical_provider_generation import metadata_service
from test_opencode_command_observation import command_scene,exercise,observation


BASE={'operation_id':'observer_scope','provider_profile_ref':{'id':'provider','revision':1,'sha256':'a'*64},'capability_binding_sha256':'b'*64}


def test_absent_observer_scope_preserves_legacy_and_nested_request_hashes():
    old=OpenConversation.model_validate(BASE).model_dump()
    assert 'command_observation_paths' not in old
    assert OpenConversation.model_validate({**BASE,'command_observation_paths':None}).model_dump()==old
    body={'request':BASE,'preparation_sha256':'c'*64,'confirmation_sha256':'d'*64}
    assert ConfigureConversation.model_validate(body).model_dump()['request']==old
    assert OpenConversation.model_validate(BASE).model_dump(exclude={'project_id'})=={k:v for k,v in old.items() if k!='project_id'}


def test_explicit_empty_scope_is_distinct_and_normalized_paths_preserve_spaces_and_order():
    old=OpenSession.model_validate(BASE).model_dump()
    empty=OpenSession.model_validate({**BASE,'command_observation_paths':[]}).model_dump()
    assert empty['command_observation_paths']==[] and digest(empty)!=digest(old)
    selected=OpenSession.model_validate({**BASE,'command_observation_paths':['reports/测试.xml','space name.py','trailing ']}).model_dump()
    assert selected['command_observation_paths']==['reports/测试.xml','space name.py','trailing ']
    assert OpenSession.model_validate(selected).model_dump()==selected


def test_observation_declaration_rejects_globs_private_paths_duplicates_and_large_lists():
    for paths in [['../secret'],['/abs'],['C:/report.xml'],['back\\report'],['a//b'],['a/./b'],['a/'],['*.xml'],['a?'],['a[b]'],['.GIT/config'],['x/.eeas-runtime/file'],['.eeas-write-stage/file'],['a\0b'],['a\nb'],['x'*1025],['same','same'],['f'+str(i) for i in range(33)]]:
        with pytest.raises((ValidationError,InvalidInput)):
            OpenSession.model_validate({**BASE,'command_observation_paths':paths})


def test_explicit_observation_is_independent_of_write_grants_and_empty_never_falls_back():
    workspace={'allowed_write_paths':['*','src/only.py']};before=deepcopy(workspace)
    assert observation_sources({'command_observation_paths':['reports/tests.xml']},workspace,clean=lambda x:x)==['reports/tests.xml']
    assert observation_sources({'command_observation_paths':[]},workspace,clean=lambda x:x)==[]
    assert workspace==before


def test_legacy_fallback_remains_bounded_and_does_not_expand_globs_or_directories():
    assert observation_sources({}, {'allowed_write_paths':['*','src/','b.py','a.py']},clean=lambda x:x)==['a.py','b.py']
    assert observation_sources({}, {'allowed_write_paths':['f'+str(i) for i in range(33)]},clean=lambda x:x)==[]
    assert observation_sources({}, {'allowed_write_paths':['a.py','secret']},clean=lambda x:'REDACTED' if x=='secret' else x)==['a.py']


def test_private_runtime_redaction_rejects_explicit_scope_instead_of_silently_capturing_less():
    with pytest.raises(Conflict):observation_sources({'command_observation_paths':['secret']},{'allowed_write_paths':[]},clean=lambda x:'REDACTED')


def test_nonempty_observer_scope_requires_registered_exact_commands_before_capability_admission():
    for update in [{},{'workspace_ref':{'id':'c'*64,'revision':1,'binding_sha256':'d'*64}}, {'workspace_ref':{'id':'c'*64,'revision':1,'binding_sha256':'d'*64},'allowed_effects':['BACKGROUND_COMMAND']}]:
        body=OpenSession.model_validate({**BASE,**update,'command_observation_paths':['report.xml']})
        with pytest.raises(Conflict,match='Observed files require'):
            AgentSessions._admit_open(None,body,{})


def test_real_observer_saves_explicit_file_with_no_workspace_write_grant(command_scene):
    s=command_scene;ws=LocalWorkspaces(Store(s['data']/'eeas.sqlite3'),s['data']).bind(s['pid'],
        WorkspaceInput(idempotency_key='readonly-observer',root=str(s['folder']),allowed_write_paths=[]))
    s['workspace']=ws
    s['admission']=bind(prepare(s['runtime']/'explicit',project_id=s['pid'],session_ref=s['ref'],generation=1,
        workspace=ws,source_paths=observation_sources({'command_observation_paths':['a.py']},ws,clean=lambda x:x)),'ses_command')
    result=exercise(s,changeDuring='<testsuite><testcase name="one"/></testsuite>',exit=0)
    rows,_=read_history(s['admission'],observation(s,result),data_dir=s['data'])
    assert len(rows)==1 and rows[0]['sources'][0]['path']=='a.py'
    assert rows[0]['sources'][0]['after']['state']=='UTF8' and ws['allowed_write_paths']==[]


def test_configuration_confirmation_freezes_observation_and_preserves_empty_write_grants(configured,tmp_path_factory):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'initial_owner')))
    folder=tmp_path_factory.mktemp('readonly-workspace')
    w=h.service.sessions.workspaces.bind(h.pid,WorkspaceInput(idempotency_key='observer-only',root=str(folder),allowed_write_paths=[]))
    ref={k:w[k] for k in ('id','revision','binding_sha256')}
    p=packet(h,'observe_config',workspace=ref,commands=['python check.py'])
    p['request']['command_observation_paths']=['reports/tests.xml']
    p['confirmation_sha256']=digest(confirmation_value(p['request'],p['preparation_sha256'],p['execution_project_id']))
    result=asyncio.run(h.configuration.configure(p));calls=h.calls['open']
    repeated=asyncio.run(h.configuration.configure(p))
    assert repeated['session_ref']==result['session_ref'] and h.calls['open']==calls
    assert h.service.sessions.workspaces.get(h.pid,w['id'])['allowed_write_paths']==[]
    changed=deepcopy(p);changed['request']['command_observation_paths']=['another.xml']
    with pytest.raises(Conflict):asyncio.run(h.configuration.configure(changed))
    assert h.calls['open']==calls


def test_provider_only_switch_cannot_silently_replace_explicit_observer_scope():
    async def exercise_rejection():
        c=object.__new__(CanonicalConversations);c._lock=lambda _key:asyncio.Lock()
        old={'project_id':'project','session_ref':{'id':'old'},'provider_profile_ref':BASE['provider_profile_ref']}
        c._view=lambda _source:old
        declared={'allowed_effects':['BACKGROUND_COMMAND'],'command_allowlist':['python check.py'],'command_observation_paths':['report.xml']}
        c.sessions=SimpleNamespace(_manifest=lambda *_args:({'request':declared},None))
        for paths in [None,[],['another.xml']]:
            request=OpenConversation.model_validate({**BASE,'conversation_ref':{'id':'native:'+'e'*64,'revision':1},'provider_profile_ref':{**BASE['provider_profile_ref'],'revision':2},'allowed_effects':['BACKGROUND_COMMAND'],'command_allowlist':['python check.py'],'command_observation_paths':paths})
            with pytest.raises(Conflict,match='cannot expand'):
                await c._switch_provider(request,None,{}, {'conversation_ref':request.conversation_ref.model_dump()}, {})
    asyncio.run(exercise_rejection())


def test_controller_owned_paths_rejected_before_retiring_original_configuration(configured,tmp_path):
    h=configured;asyncio.run(h.configuration.configure(packet(h,'keep_original')))
    root=tmp_path.parent
    w=h.service.sessions.workspaces.bind(h.pid,WorkspaceInput(idempotency_key='ancestor-root',root=str(root),allowed_write_paths=[]))
    p=packet(h,'reject_private_observer',workspace={k:w[k] for k in ('id','revision','binding_sha256')},commands=['python check.py'])
    p['request']['command_observation_paths']=[tmp_path.name+'/controller-private.txt']
    p['confirmation_sha256']=digest(confirmation_value(p['request'],p['preparation_sha256'],p['execution_project_id']))
    with pytest.raises((Conflict,InvalidInput)):asyncio.run(h.configuration.configure(p))
    assert h.calls=={'open':1,'send':0,'stop':0}
