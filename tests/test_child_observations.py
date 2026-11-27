"""Real original files/Store/ASGI; authored NOT_RUN for concentrated debug.

Synthetic saved child events and explicitly injected SDK GET/stream boundaries
do not qualify live child execution, Windows, VS Code or model behavior.
"""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace

from packages.agent_sessions.child_observations import child_reference
from packages.domain.tasks import digest
from test_conversation_directory import api, unit_session, forbid
from test_conversation_source_anchors import emit


def saved_child(client):
    owner,pid,sid,state=unit_session(client,guided=False)
    child={'id':'ses_saved_child','parentID':state['original_session_id'],'directory':'/original/workspace','title':'Read implementation'}
    state['children']=[child]
    state['original_session']={'id':state['original_session_id'],'directory':child['directory']}
    owner._write(owner._path(pid,sid,'state.json'),state,replace=True)
    info={'id':'msg_child_answer','sessionID':child['id'],'role':'assistant','parentID':'msg_child_input'}
    part={'id':'prt_child_text','type':'text','sessionID':child['id'],'messageID':info['id'],'text':'ORIGINAL_CHILD_TEXT'}
    emit(owner,pid,sid,{'type':'message.updated','properties':{'info':info}})
    emit(owner,pid,sid,{'type':'message.part.updated','properties':{'part':part}})
    ref=owner._manifest(pid,sid)[1];reference=child_reference(state,child)
    route=f'/api/projects/{pid}/agent-sessions/{sid}/children/{child["id"]}'
    query={'session_sha256':ref['sha256'],'generation':1,'child_sha256':reference['sha256'],
        'root_native_session_id':reference['root_native_session_id']}
    return owner,pid,sid,child,info,part,route,query


def test_disconnected_child_reads_only_verified_original_events_without_starting_owner(api,monkeypatch):
    owner,pid,sid,child,info,part,route,query=saved_child(api)
    frozen={path:(path.read_bytes(),path.stat().st_mtime_ns) for path in owner._path(pid,sid).rglob('*.json')}
    monkeypatch.setattr(owner,'get',forbid);monkeypatch.setattr(owner,'inspect',forbid)
    monkeypatch.setattr(owner,'_context',forbid);monkeypatch.setattr(owner,'_http',forbid)
    response=api.get(route,params=query)
    assert response.status_code==200,response.text
    value=response.json()
    assert value['messages'][0]['parts'][0]['text']=='ORIGINAL_CHILD_TEXT'
    assert value['read_only'] and value['new_owner_started'] is False and value['native_posts']==0
    assert value['observation_is_current'] is False and value['coverage']['history_complete'] is False
    assert all((path.read_bytes(),path.stat().st_mtime_ns)==before for path,before in frozen.items())


def test_changed_child_lineage_generation_or_event_never_substitutes_other_saved_text(api):
    owner,pid,sid,child,info,part,route,query=saved_child(api)
    assert api.get(route,params={**query,'generation':2}).status_code==409
    assert api.get(route,params={**query,'child_sha256':'f'*64}).status_code==409
    state=owner._state(pid,sid);state['children'][0]['directory']='/different/workspace'
    owner._write(owner._path(pid,sid,'state.json'),state,replace=True)
    assert api.get(route,params=query).status_code==409
    query['child_sha256']=child_reference(state,state['children'][0])['sha256']
    assert api.get(route,params=query).status_code==409


def test_live_read_uses_the_same_admitted_owner_and_get_only_with_bounded_timeouts(api,monkeypatch):
    owner,pid,sid,child,info,part,route,query=saved_child(api)
    context={'process':SimpleNamespace(returncode=None),'lock':asyncio.Lock(),'directory':child['directory']}
    owner.active[sid]=context;calls=[]
    monkeypatch.setattr(owner,'_context',lambda *args,**kw:(None,context))
    monkeypatch.setattr(owner,'_clean',lambda _,value:value)
    async def sdk(current,method,path,**kwargs):
        assert current is context and method=='GET' and kwargs['timeout']==3
        calls.append(path)
        return [{'info':info,'parts':[part]}] if path.endswith('/message') else child
    monkeypatch.setattr(owner,'_http',sdk)
    try:
        response=api.get(route,params=query)
        assert response.status_code==200,response.text
        assert response.json()['observation_is_current'] and response.json()['native_posts']==0
        assert calls==['/session/'+child['id'],'/session/'+child['id']+'/message']
    finally:
        owner.active.pop(sid,None)


def test_original_listener_commits_part_session_identity_without_outer_session_field(api,monkeypatch):
    owner,pid,sid,child,info,part,route,query=saved_child(api)
    before=owner._state(pid,sid)['last_event_seq']
    foreign={**part,'id':'prt_foreign','sessionID':'ses_foreign'}
    context={'known_sessions':{child['id']},'generation':1,'closing':False,'directory':child['directory']}
    class Stream:
        status_code=200
        async def __aenter__(self):return self
        async def __aexit__(self,*_):return False
        async def aiter_lines(self):
            for value in [part,foreign]:
                yield 'data: '+json.dumps({'type':'message.part.updated','properties':{'part':value}})
                yield ''
            context['closing']=True
    context['client']=SimpleNamespace(stream=lambda *args,**kwargs:Stream())
    monkeypatch.setattr(owner,'_clean',lambda _,value:value)
    asyncio.run(owner._listen(pid,sid,context))
    assert owner._state(pid,sid)['last_event_seq']==before+1
    event=owner._read(owner._path(pid,sid,'events',f'{before+1:010d}.json'))
    assert event['payload']['native_payload']['properties']['part']==part
    with owner.store.connect() as db:
        row=db.execute('SELECT event_seq FROM original_message_locations WHERE project_id=? AND session_id=? AND slot_id=?',
            (pid,sid,part['id'])).fetchone()
    assert row['event_seq']==before+1
