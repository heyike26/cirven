"""Bridge one stale metadata preflight from the existing relay worker.

The worker never holds a Store transaction while the original owner event loop
collects IDE metadata. It never starts a loop, owner, model request or retry.
"""
import asyncio
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime, timezone

from packages.domain.store import Conflict


def refresh_relay_capture(owner,pid,sid,manifest,request_sha,expected_context=None):
    ref=manifest['request'].get('workspace_ref')
    if not ref:return None
    context=owner.active.get(sid);state=owner._state(pid,sid)
    if (context is None or expected_context is not None and context is not expected_context
            or context.get('closing') or state.get('generation')!=context.get('generation')
            or state.get('owner_status')!='READY' or not state.get('active_operation_id')):
        raise Conflict('Original relay owner or active operation changed before metadata capture')
    broker=owner.editor_captures.broker
    binding=owner.workspaces.binding_record(pid,ref['id'],ref['revision'])
    if broker._fresh(binding):return None
    loop=context.get('loop')
    if loop is None or loop.is_closed() or not loop.is_running():
        raise Conflict('EDITOR_CAPTURE_CHANNEL_REQUIRED: original owner loop cannot collect editor metadata')
    try:running=asyncio.get_running_loop()
    except RuntimeError:running=None
    if running is loop:
        raise Conflict('EDITOR_CAPTURE_CHANNEL_REQUIRED: never block the original owner loop on its own metadata')
    remaining=min(9,(datetime.fromisoformat(context['expires_at'])-datetime.now(timezone.utc)).total_seconds())
    if remaining<=0:raise Conflict('Original relay authority expired before metadata capture')
    basis={'generation':state['generation'],'operation_id':state['active_operation_id'],'context':context}
    future=asyncio.run_coroutine_threadsafe(broker.admit(manifest,generation=basis['generation'],
        operation_id=basis['operation_id'],kind='MODEL_RELAY',original_request_sha256=request_sha),loop)
    try:future.result(timeout=remaining)
    except FutureTimeoutError as error:
        # Cancel only this metadata waiter. Original task/model POSTs are never
        # cancelled, recreated or inferred successful by this transport.
        future.cancel()
        raise Conflict('EDITOR_CAPTURE_WAITING: metadata deadline reached before model forwarding') from error
    latest=owner._state(pid,sid)
    if owner.active.get(sid) is not context or context.get('closing') or latest['generation']!=basis['generation'] or latest.get('active_operation_id')!=basis['operation_id']:
        raise Conflict('Original relay operation changed while collecting editor metadata')
    return basis
