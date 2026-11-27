"""Conversation isolation through the real native-question HTTP/store boundary."""
from fastapi.testclient import TestClient
from test_native_messages import application, TOKEN


def test_quick_and_main_share_only_the_same_bounded_conversation(tmp_path):
    app, service, _ = application(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        for op, conversation, text in [
            ('legacy', None, '旧无项目上下文'),
            ('project-a-first', 'project-a-conversation', '项目A的接口约束'),
            ('project-b-secret', 'project-b-conversation', '项目B的内容不该混入A'),
            ('project-a-followup', 'project-a-conversation', '接着解释刚才的接口'),
        ]:
            result = client.post('/api/desktop-host/native-messages', json={
                'operation_id': op, 'raw_text': text, 'conversation_id': conversation})
            assert result.status_code in (200, 201), result.text
            assert result.json()['status'] == 'NOT_CONFIGURED'
        history = service.read_history({}, {'request': {'operation_id': 'project-a-followup'}})
        assert history['conversation_id'] == 'project-a-conversation'
        assert [row['operation_id'] for row in history['messages']] == ['project-a-first']
        assert '项目B' not in str(history) and '旧无项目' not in str(history)
        assert history['automatic_input_authorized'] is False
        assert service.transport.active == {}
        # The same operation cannot be silently reassigned to another topic.
        conflict = client.post('/api/desktop-host/native-messages', json={
            'operation_id': 'project-a-followup', 'raw_text': '接着解释刚才的接口',
            'conversation_id': 'project-b-conversation'})
        assert conflict.status_code == 409


def test_topic_limits_and_history_budget_do_not_change_execution_facts(tmp_path):
    app, service, _ = application(tmp_path)
    with TestClient(app) as client:
        client.headers['X-EEAS-Desktop-Host'] = TOKEN
        for index in range(9):
            response = client.post('/api/desktop-host/native-messages', json={
                'operation_id': f'topic-message-{index}', 'raw_text': f'问题{index}',
                'conversation_id': 'one-topic'})
            assert response.status_code in (200, 201)
        history = service.read_history({}, {'request': {'operation_id': 'topic-message-8'}})
        assert len(history['messages']) == 6
        assert {row['operation_id'] for row in history['messages']} == {
            f'topic-message-{index}' for index in range(2, 8)}
        assert all(row['status'] == 'NOT_CONFIGURED' for row in history['messages'])
        invalid = client.post('/api/desktop-host/native-messages', json={
            'operation_id': 'bad-topic', 'raw_text': '问题', 'conversation_id': '../another-topic'})
        assert invalid.status_code == 422
