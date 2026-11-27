"""Recovery enumerates exact existing records; never recreates an operation."""
import json
import sqlite3
import pytest

from packages.domain.intake import digest
from test_discovery_workbench import create
from test_discovery_outcome import TerminalPlanner


class MetadataPlanner(TerminalPlanner):
    def stream_events(self, *_args):
        raise AssertionError('Recovery headers must not read full event payloads')


def add(client, pid, sid, op, native='UNKNOWN_OUTCOME', kind='ADVANCE'):
    result={'id':sid,'status':'FAILED','private':'internal raw receipt must stay private'}
    with client.app.state.store.connect() as c:
        c.execute('INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)',
                  (pid,op,sid,kind,'immutable-request-pin','FAILED',json.dumps(result),digest(result)))
        prefix='discplan-' if kind=='PLAN' else 'disc-'
        c.execute('INSERT INTO agent_executions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
                  ('native-'+op,pid,prefix+digest([pid,op])[:40],'pin','PROTOCOL_FIXTURE','fixture',native,
                   'NOT_RUN','original-'+op,'{}','fixture','{}',None,'2026-10-21','2026-10-21',0))
        return tuple(c.execute('SELECT * FROM discovery_operations WHERE operation_id=?',(op,)).fetchone())


def setup(client):
    pid,_,_,session=create(client)
    planner=MetadataPlanner(client.app.state.store,'UNKNOWN_OUTCOME')
    client.app.state.discovery.transport=planner
    return pid,session,planner,f'/api/projects/{pid}/discovery/sessions/{session["id"]}/recoverable-operations'


def test_lost_local_marker_lists_exact_legacy_unknown_without_rewriting(client):
    pid,session,planner,url=setup(client)
    old=add(client,pid,session['id'],'lost-marker')
    value=client.get(url).json()
    assert value['next_before'] is None and len(value['operations'])==1
    header=value['operations'][0]
    assert header['operation_id']=='lost-marker' and header['session_id']==session['id']
    assert header['status']=='UNKNOWN_OUTCOME'
    assert header['sequence']==1
    assert header['native_execution']['execution_status']=='UNKNOWN_OUTCOME'
    assert not {'events','result','request','private','request_sha256'} & header.keys()
    assert 'internal raw receipt' not in json.dumps(value)
    assert client.get(url).json()==value and planner.calls==[]
    assert client.app.state.discovery.get(pid,session['id'])==session
    with client.app.state.store.connect() as c:
        assert tuple(c.execute('SELECT * FROM discovery_operations WHERE operation_id=?',('lost-marker',)).fetchone())==old


def test_partial_page_never_claims_no_earlier_unknown(client):
    pid,session,planner,url=setup(client)
    add(client,pid,session['id'],'older-unknown')
    add(client,pid,session['id'],'later-failed',native='FAILED')
    add(client,pid,session['id'],'latest-failed',native='FAILED')
    first=client.get(url+'?limit=2').json()
    assert first['operations']==[] and first['next_before'] is not None
    second=client.get(url+'?limit=2&before='+str(first['next_before'])).json()
    assert second['next_before'] is None
    assert [v['operation_id'] for v in second['operations']]==['older-unknown']
    assert second['operations'][0]['sequence']==1
    assert planner.calls==[]


def test_multiple_unknown_are_distinct_and_cross_project_or_session_is_rejected(client):
    pid,session,planner,url=setup(client)
    add(client,pid,session['id'],'advance-unknown')
    add(client,pid,session['id'],'plan-unknown',kind='PLAN')
    headers=client.get(url).json()['operations']
    assert {h['operation_id'] for h in headers}=={'advance-unknown','plan-unknown'}
    assert [h['sequence'] for h in headers]==[2,1]
    other,_,_,other_session=create(client)
    assert client.get(url.replace(pid,other)).status_code==404
    assert client.get(url.replace(session['id'],other_session['id'])).status_code==404
    assert planner.calls==[]


def test_recovery_cursor_and_page_cap_are_validated(client):
    _,_,planner,url=setup(client)
    for query in ['?limit=21','?limit=0','?before=0','?before=-1','?before=oops']:
        assert client.get(url+query).status_code==422
    assert client.get(url).json()=={'operations':[],'next_before':None}
    assert planner.calls==[]


def test_recovery_page_uses_session_index_and_rowid_cursor(client):
    pid,session,_,_=setup(client)
    with client.app.state.store.connect() as c:
        plan=c.execute("EXPLAIN QUERY PLAN SELECT rowid,operation_id FROM discovery_operations "
            "WHERE project_id=? AND session_id=? AND kind IN ('ADVANCE','PLAN') "
            "AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?",
            (pid,session['id'],None,None,21)).fetchall()
        detail=' '.join(row['detail'] for row in plan)
        assert 'discovery_operation_recovery_page' in detail,detail
        assert 'TEMP B-TREE' not in detail,detail


def test_exact_operation_read_exposes_its_checked_original_basis_not_session_head(client):
    pid,session,planner,url=setup(client)
    client.app.state.discovery.transport=planner=TerminalPlanner(client.app.state.store,'UNKNOWN_OUTCOME')
    old=add(client,pid,session['id'],'basis-one')
    with client.app.state.store.connect() as c:
        client.app.state.discovery._event(c,pid,session['id'],'basis-one','ADVANCE_REQUESTED',revision=1)
    endpoint=f'/api/projects/{pid}/discovery/operations/basis-one'
    value=client.get(endpoint)
    assert value.status_code==200,value.text
    assert value.json()['basis_revision']==1
    assert client.get(url).json()['operations'][0]['basis_revision']==1
    assert client.app.state.discovery.get(pid,session['id'])==session
    assert planner.calls==[]
    with client.app.state.store.connect() as c:
        assert tuple(c.execute('SELECT * FROM discovery_operations WHERE operation_id=?',('basis-one',)).fetchone())==old
        with pytest.raises(sqlite3.IntegrityError,match='immutable'):
            c.execute("UPDATE discovery_events SET sha256=? WHERE operation_id=?",('0'*64,'basis-one'))
    add(client,pid,session['id'],'bad-event')
    with client.app.state.store.connect() as c:
        c.execute('INSERT INTO discovery_events(project_id,session_id,operation_id,payload,sha256) VALUES (?,?,?,?,?)',
                  (pid,session['id'],'bad-event',json.dumps({'revision':99}),'0'*64))
    endpoint=endpoint.replace('basis-one','bad-event')
    assert client.get(endpoint).status_code==409
