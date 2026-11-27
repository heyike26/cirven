"""AUTHORED_NOT_RUN: saved observer/real Store and HTTP; no native runner proof."""
import pytest
from packages.domain.store import InvalidInput
from packages.domain.tasks import digest
from test_command_observations_routes import routed,persist
from test_opencode_command_observation import command_scene,exercise,observation


def test_selected_call_http_does_not_read_unrelated_command_records(routed,monkeypatch):
    from fastapi.testclient import TestClient
    from test_session_diffs import app_for,query
    import packages.adapters.opencode_command_observation as adapter
    s=routed;one=exercise(s,callID='selected',exit=0);other=exercise(s,callID='unrelated',exit=7)
    state,op,expected=persist(s,one);state['messages']+=observation(s,other,message='msg_other')['messages']
    s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    ignored=digest({'native_session_id':'ses_command','call_id':'unrelated'});regular=adapter._regular
    def read_selected(path,*args):
        assert ignored not in str(path)
        return regular(path,*args)
    monkeypatch.setattr(adapter,'_regular',read_selected)
    before=op.read_bytes()
    with TestClient(app_for(s['service'])) as client:
        route='/api/projects/'+s['pid']+'/agent-sessions/'+s['ident']+'/command-observations'
        result=client.get(route,params={**query(expected),'call_id':'selected'})
        assert result.status_code==200,result.text
        assert [c['call_id'] for c in result.json()['commands']]==['selected']
        assert client.get(route,params={**query(expected),'call_id':'../unrelated'}).status_code==422
    assert op.read_bytes()==before


def test_selected_historical_call_keeps_original_generation_and_current_observation(routed):
    s=routed;packet=exercise(s,callID='old_call',exit=0);state,_,expected=persist(s,packet)
    state['generation']=2;s['service']._write(s['service']._path(s['pid'],s['ident'],'state.json'),state,replace=True)
    result=s['service'].command_observations(s['pid'],s['ident'],**{**expected,'generation':2},call_id='old_call')
    assert result['scope']['generation']==2 and len(result['commands'])==1
    assert result['commands'][0]['source_generation']==1 and result['commands'][0]['source_scope']['generation']==1


def test_selected_call_validation_and_absent_call_never_broaden_selection(routed):
    s=routed;packet=exercise(s,callID='only',exit=0);_,_,expected=persist(s,packet)
    for invalid in ['', '../only',42,'x'*193]:
        with pytest.raises(InvalidInput):s['service'].command_observations(s['pid'],s['ident'],**expected,call_id=invalid)
    result=s['service'].command_observations(s['pid'],s['ident'],**expected,call_id='missing')
    assert result['commands']==[] and result['read_only'] is True
