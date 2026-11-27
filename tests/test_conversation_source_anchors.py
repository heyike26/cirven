"""Real ASGI/Store/original files, authored NOT_RUN until concentrated debug.

Synthetic saved SDK events only; no live model, SDK owner or native application.
"""
from copy import deepcopy

from packages.domain.tasks import digest
from test_conversation_directory import api, native_record, project, unit_session, forbid
from test_conversation_threads import organization, item


def enroll(client, kind):
    row = next(row for row in client.get('/api/conversations').json()['items'] if row['kind'] == kind)
    response = client.post('/api/conversations/'+row['id']+'/selection', json={'source': row['source']})
    assert response.status_code == 200, response.text
    return row, response.json()


def prepare(client, cid, row):
    response = client.post('/api/context/conversations/'+cid+'/message-source', json={
        'message_id': row['id'], 'expected_sha256': row['text_sha256'], 'offset': 0, 'length': 4000})
    assert response.status_code == 200, response.text
    return response.json()['source_ref']


def emit(owner, pid, sid, native):
    return owner._event(pid, sid, 'ORIGINAL_EVENT', {'native_payload': native,
        'native_payload_sha256': digest(native), 'generation': 1})


def indexed_session(client):
    owner, pid, sid, state = unit_session(client, guided=False)
    message = deepcopy(state['messages'][0])
    message['parts'][0]['id'] = 'prt_original'
    state['messages'] = [message]
    owner._write(owner._path(pid, sid, 'state.json'), state, replace=True)
    emit(owner, pid, sid, {'type':'message.updated', 'properties':{'info':message['info']}})
    emit(owner, pid, sid, {'type':'message.part.updated', 'properties':{'part':message['parts'][0]}})
    row, scope = enroll(client, 'original')
    return owner, pid, sid, message, row, scope


def test_exact_native_user_and_answer_remain_original_data_after_navigation_move_and_delete(api, monkeypatch):
    pid = project(api)
    native_record(api, 'quoted_turn', pid=pid, cid='quoted_discussion')
    row, scope = enroll(api, 'native')
    history = api.get('/api/conversations/'+row['id']+'/history').json()
    refs = [(message['text'], prepare(api, row['id'], message)) for message in history['messages']]
    assert len(refs) == 2
    native = api.app.state.native_messages
    before = native.path('quoted_turn').read_bytes()
    monkeypatch.setattr(native, 'get', forbid)
    monkeypatch.setattr(native.transport, 'inspect', forbid)
    assert organization(api, item(api,row['id']), 'move', 'move_quoted', target_project_id=project(api)).status_code == 200
    assert organization(api, item(api,row['id']), 'delete', 'delete_navigation',
                        delete_scope='CONVERSATION_ONLY_KEEP_EXECUTION_AND_FILES').status_code == 200
    for text, source in refs:
        response = api.post('/api/context/history/preview-source', json=source)
        assert response.status_code == 200, response.text
        assert response.json()['text'] == text and source['project_id'] == pid
    assert native.path('quoted_turn').read_bytes() == before


def test_event_quote_survives_saved_window_eviction_but_rejects_later_text_change(api, monkeypatch):
    owner,pid,sid,message,row,scope = indexed_session(api)
    history = api.get('/api/conversations/'+row['id']+'/history').json()
    answer = next(message for message in history['messages'] if message['role']=='assistant')
    reference = prepare(api,row['id'],answer)
    assert reference['source_anchor']['kind'] == 'ORIGINAL_MESSAGE'
    frozen = {path:path.read_bytes() for path in owner._path(pid,sid,'events').glob('*.json')}
    state = owner._state(pid,sid); state['messages'] = []
    owner._write(owner._path(pid,sid,'state.json'),state,replace=True)
    monkeypatch.setattr(owner,'get',forbid); monkeypatch.setattr(owner,'inspect',forbid)
    assert api.post('/api/context/history/preview-source',json=reference).json()['text']=='OLD_FIRST_RESPONSE'
    changed = {**message['parts'][0], 'text':'UPDATED_ORIGINAL'}
    emit(owner,pid,sid,{'type':'message.part.updated','properties':{'part':changed}})
    assert api.post('/api/context/history/preview-source',json=reference).status_code == 409
    assert all(path.read_bytes()==raw for path,raw in frozen.items())


def test_original_user_envelope_is_not_exported_and_gap_allows_only_verified_assistant_parts(api):
    from packages.agent_sessions.message_locations import record_message_gap
    owner,pid,sid,message,row,scope = indexed_session(api)
    user = {'id':'msg_private','role':'user','sessionID':message['info']['sessionID']}
    emit(owner,pid,sid,{'type':'message.updated','properties':{'info':user}})
    emit(owner,pid,sid,{'type':'message.part.updated','properties':{'part':{
        'id':'prt_private','messageID':user['id'],'sessionID':user['sessionID'],
        'type':'text','text':'PRIVATE_HOST_DECORATED_CONTEXT'}}})
    with api.app.state.store.connect() as db:
        record_message_gap(db,pid,sid,1,'MISSING_OR_INVALID_ORIGINAL_EVENT')
    response = api.get('/api/context/conversations/'+row['id']+'/event-excerpts',params={
        'source_alias_id':next(alias['id'] for alias in scope['sources'] if alias['original_source']==row['source'])})
    assert response.status_code==200, response.text
    body=response.json()
    assert body['partial'] and 'PRIVATE_HOST_DECORATED_CONTEXT' not in response.text
    assert len(body['items'])==1
    source=body['items'][0]['source_ref']
    assert source['source_anchor']['kind']=='ORIGINAL_PART'
    assert api.post('/api/context/history/preview-source',json=source).json()['text']=='OLD_FIRST_RESPONSE'
    history=api.get('/api/conversations/'+row['id']+'/history').json()
    answer=next(message for message in history['messages'] if message['role']=='assistant')
    rejected=api.post('/api/context/conversations/'+row['id']+'/message-source',json={
        'message_id':answer['id'],'expected_sha256':answer['text_sha256']})
    assert rejected.status_code==409


def test_explicit_backfill_keeps_original_files_and_idempotent_receipt(api):
    owner,pid,sid,message,row,scope=indexed_session(api)
    frozen={path:path.read_bytes() for path in owner._path(pid,sid,'events').glob('*.json')}
    with api.app.state.store.connect() as db:
        db.execute('DELETE FROM original_message_locations WHERE project_id=? AND session_id=?',(pid,sid))
    request={'operation_id':'backfill_old_journal','conversation_id':row['id'],
        'source_alias_id':next(alias['id'] for alias in scope['sources'] if alias['original_source']==row['source']),'cursor':None}
    response=api.post('/api/context/history/scan-events',json=request)
    assert response.status_code==200,response.text
    body=response.json()
    assert body['events_scanned']==2 and body['next_cursor'] is None and body['model_calls']==0
    assert body['raw_events_changed'] is False and body['transcript_copied'] is False
    assert api.post('/api/context/history/scan-events',json=request).json()==body
    assert all(path.read_bytes()==raw for path,raw in frozen.items())
