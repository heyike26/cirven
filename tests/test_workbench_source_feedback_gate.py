"""A new material selection cannot silently rebase an old annotation."""
from test_discovery_workbench import create,edited
from test_workbench_feedback import body,route


def test_changed_original_rejects_old_feedback_even_with_explicit_new_materials(client):
    pid,doc,_,view=create(client,files=True);view=edited(client,pid,view)
    recorded=client.post(route(pid),json=body(view,op='before-source-change'))
    assert recorded.status_code==200,recorded.text
    changed=client.put(f'/api/projects/{pid}/documents/{doc["id"]}',
        files={'file':('updated.md','用户在本机补充的新要求。'.encode())},data={'expected_revision':'1'})
    assert changed.status_code==200,changed.text
    latest=changed.json()
    response=client.post(f'/api/projects/{pid}/discovery/sessions/{view["id"]}/advance',json={
        'operation_id':'old-annotation-with-new-context','expected_revision':view['revision'],
        'message':'提交新增资料及之前的图批注。',
        'materials':[{'document_id':latest['id'],'revision':latest['revision'],'sha256':latest['sha256']}],
        'feedback_refs':[recorded.json()['feedback_ref']]})
    assert response.status_code==409,response.text
    assert 'Feedback source changed' in response.json()['error']['message']
    assert client.get(f'/api/projects/{pid}/discovery/sessions/{view["id"]}').json()['revision']==view['revision']
    assert client.get(route(pid)+'/'+recorded.json()['feedback_ref']['id']).status_code==200
    with client.app.state.store.connect() as c:
        assert c.execute('SELECT count(*) FROM agent_executions').fetchone()[0]==0
        assert c.execute('SELECT count(*) FROM discovery_operations WHERE operation_id=?',
            ('old-annotation-with-new-context',)).fetchone()[0]==0
