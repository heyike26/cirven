"""Conservative, content-free projection of a saved original auth rejection."""
import hashlib
import math
from datetime import datetime

AUTH_REJECTION_TEXT = '模型连接已失效，本次请求未能完成。原问题已保留，请检查模型连接后继续。'


def original_auth_rejection(operation, messages):
    """Recognize only an exact terminal 401 before any observed answer/tool.

    An SDK failure, timeout, arbitrary error string, or partial tool run is not
    this evidence. This function neither reads credentials nor dispatches work.
    """
    if (operation.get('status') != 'FAILED' or operation.get('kind') not in {'MESSAGE', 'GUIDANCE'}
            or operation.get('native_post_attempted') is not True
            or operation.get('native_dispatch_acknowledged') is not True
            or not isinstance(messages, list) or len(messages) > 200):
        return None
    parent = operation.get('native_message_id')
    session = operation.get('original_session_id')
    selected = operation.get('native_assistant_ids')
    if (not parent or not session or not isinstance(selected, list) or not selected
            or any(not isinstance(x, str) for x in selected) or len(set(selected)) != len(selected)):
        return None
    by_id = {}
    for item in messages:
        if not isinstance(item, dict) or not isinstance(item.get('info'), dict):
            return None
        ident = item['info'].get('id')
        if not isinstance(ident, str) or ident in by_id:
            return None
        by_id[ident] = item
    user = by_id.get(parent, {})
    info = user.get('info', {})
    parts = user.get('parts', [])
    if (info.get('role') != 'user' or info.get('sessionID') != session
            or not isinstance(parts, list) or any(not isinstance(p, dict) for p in parts)):
        return None
    text = ''.join(p.get('text', '') for p in parts
                   if p.get('type') == 'text' and isinstance(p.get('text'), str))
    payload = operation.get('host_payload')
    if payload and (not isinstance(payload, dict) or not isinstance(payload.get('text_parts'), list)
                    or any(not isinstance(p, str) for p in payload['text_parts'])):
        return None
    expected = (''.join(payload['text_parts']) if payload else operation.get('request', {}).get('raw_text'))
    if not isinstance(expected, str):
        return None
    if text != expected:
        # ConversationRuntime projects only the authored input for display. Its
        # exact wire/operation/context link is persisted with that projection.
        context_sha = operation.get('conversation_context_sha256')
        origin = {'operation_id': operation.get('id'), 'request_sha256': operation.get('request_sha256'),
                  'context_sha256': context_sha}
        if (not payload or not isinstance(context_sha, str) or len(context_sha) != 64
                or payload.get('conversation_context_sha256') != context_sha
                or user.get('conversation_input_origin') != origin
                or user.get('conversation_input_unavailable')
                or text != operation.get('request', {}).get('raw_text')):
            return None
    try:
        deadline = datetime.fromisoformat(operation['deadline']).timestamp() * 1000
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    assistants = [item for item in messages if item['info'].get('role') == 'assistant'
                  and item['info'].get('parentID') == parent]
    if set(selected) != {item['info']['id'] for item in assistants}:
        return None
    completed = []
    provider_models = set()
    for item in assistants:
        info = item['info']; error = info.get('error')
        timing = info.get('time'); stamp = timing.get('completed') if isinstance(timing, dict) else None
        if (info.get('sessionID') != session or item.get('parts') != []
                or info.get('structured') is not None
                or not isinstance(error, dict) or error.get('name') != 'APIError'
                or not isinstance(error.get('data'), dict)
                or type(error['data'].get('statusCode')) is not int or error['data']['statusCode'] != 401
                or type(stamp) not in {int, float} or not math.isfinite(stamp)
                or not 0 < stamp <= deadline
                or not isinstance(info.get('providerID'), str) or not isinstance(info.get('modelID'), str)):
            return None
        completed.append(stamp)
        provider_models.add((info['providerID'], info['modelID']))
    if len(provider_models) != 1:
        return None
    provider, model = next(iter(provider_models))
    return {'code': 'MODEL_AUTH_REQUIRED', 'http_status': 401,
            'original_session_id': session, 'native_assistant_ids': list(selected),
            'native_user_messages': {parent: hashlib.sha256(expected.encode()).hexdigest()},
            'completed_ms': max(completed), 'provider_id': provider, 'model_id': model}
