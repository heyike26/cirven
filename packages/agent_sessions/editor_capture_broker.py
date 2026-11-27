"""One Main-only metadata feed for original desktop execution admission.

No second owner, filesystem actions, idle polling, model or permission grant.
Durable cohorts remain the authority; events only wake bounded fresh reads.
"""
import asyncio
import json
import threading
import time
import uuid

from packages.domain.editor_observer_scope import relevant_observers, assert_observers_clean
from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest


class CaptureFeedLease:
    def __init__(self, broker):
        self.broker=broker;self.loop=asyncio.get_running_loop();self.event=asyncio.Event();self.closed=False

    def wake(self):
        if not self.closed and not self.loop.is_closed():self.loop.call_soon_threadsafe(self.event.set)

    def close(self):
        with self.broker.lock:
            if self.closed:return
            self.closed=True
            if self.broker.feed is self:self.broker.feed=None
            self.broker.pending.clear()
        self.wake_waiters()
        if not self.loop.is_closed():self.loop.call_soon_threadsafe(self.event.set)

    def wake_waiters(self):
        self.broker.changed()

    async def lines(self):
        try:
            yield json.dumps({'type':'ready','runtime_id':self.broker.runtime_id},separators=(',',':'))+'\n'
            while not self.closed:
                with self.broker.lock:
                    self.event.clear()
                    current=int(time.time()*1000)
                    items=list(self.broker.pending.values());self.broker.pending.clear()
                for item in items:
                    rows=[r for r in item if r['expires_at_ms']>=current]
                    if rows:yield json.dumps({'type':'editor-capture-batch','runtime_id':self.broker.runtime_id,'rows':rows},separators=(',',':'))+'\n'
                if not self.closed:await self.event.wait()
        finally:self.close()


class DesktopCaptureBroker:
    def __init__(self, captures):
        self.captures=captures;self.owner=captures.owner
        self.runtime_id=self.owner.editor_observers.runtime_id
        self.actor='desktop_main_'+self.runtime_id
        self.lock=threading.RLock();self.feed=None;self.pending={};self.waiters={};self.closed=False

    def reserve(self):
        with self.lock:
            if self.closed or self.feed is not None:raise Conflict('EDITOR_CAPTURE_CHANNEL_BUSY: one original Main metadata feed is allowed')
            self.feed=CaptureFeedLease(self)
            return self.feed

    def changed(self):
        with self.lock:waiters=list(self.waiters.values())
        for loop,event in waiters:
            if not loop.is_closed():loop.call_soon_threadsafe(event.set)

    def _publish(self, reference, rows):
        with self.lock:
            if self.feed is None or self.feed.closed:
                raise Conflict('EDITOR_CAPTURE_CHANNEL_REQUIRED: reconnect the original desktop App; original content retained')
            if rows:
                if reference not in self.pending and len(self.pending)>=8:
                    raise Conflict('EDITOR_CAPTURE_CAPACITY: bounded Main metadata queue is full')
                self.pending[reference]=rows
                self.feed.wake()

    def _fresh(self,binding,*,permission_ref=None,target=None):
        with self.owner.store.connect() as db:
            members=relevant_observers(db,binding['root'],identity=binding['root_identity'])
            if not members:return True
            try:
                assert_observers_clean(db,binding['root'],identity=binding['root_identity'],permission_ref=permission_ref,target=target)
                return all(0<=time.time()-v['observed_at_epoch']<=3 for v in members)
            except Conflict:return False

    async def admit(self, manifest, *, generation, operation_id, kind, permission_ref=None,original_request_sha256=None):
        ref=manifest['request'].get('workspace_ref')
        if not ref:return None
        pid=manifest['project_id'];sid=manifest['id']
        binding=self.owner.workspaces.binding_record(pid,ref['id'],ref['revision'])
        if self._fresh(binding,permission_ref=permission_ref,target={'project_id':pid,'session_id':sid}):return None
        from .editor_captures import EditorCaptureRequest
        _,session_ref=self.owner._manifest(pid,sid)
        def create(key):
            request=EditorCaptureRequest(operation_id='desktop_capture_'+key,session_ref=session_ref,generation=generation,
                workspace_ref=ref,editor_client_id=self.actor,permission_ref=permission_ref)
            action={'kind':kind,'operation_id':operation_id}
            if original_request_sha256 is not None:action['request_sha256']=original_request_sha256
            return self.captures.request(pid,sid,request,original_action=action)
        return await self._collect(create,lambda operation,dispatch:self.captures.status(pid,sid,operation,client=self.actor,include_dispatch=dispatch))

    async def admit_workspace(self,pid,wid,body):
        service=self.owner.workspaces
        binding=service.edit_binding(pid,wid,body.path)
        # An old original write intent is inspected/recovered through its own
        # ledger. Never generate a new capture as an automatic write retry.
        try:service.operation(pid,wid,body.operation_id)
        except NotFound:pass
        else:return None
        if self._fresh(binding):return None
        from .workspace_editor_captures import WorkspaceCaptureRequest
        ref={k:binding[k] for k in ('id','revision','binding_sha256')}
        def create(key):
            request=WorkspaceCaptureRequest(operation_id='workspace_capture_'+key,workspace_ref=ref,
                editor_client_id=self.actor,edit_operation_id=body.operation_id,
                edit_request_sha256=digest(body.model_dump(exclude={'operation_id'})))
            return self.captures.workspace.request(pid,wid,request)
        return await self._collect(create,lambda operation,dispatch:self.captures.workspace.status(pid,wid,operation,client=self.actor,include_dispatch=dispatch))

    async def _collect(self,create,status):
        key=uuid.uuid4().hex;event=asyncio.Event();loop=asyncio.get_running_loop()
        with self.lock:
            if self.closed or self.feed is None or self.feed.closed:
                raise Conflict('EDITOR_CAPTURE_CHANNEL_REQUIRED: reconnect the original desktop App; original content retained')
            if len(self.waiters)>=8:raise Conflict('EDITOR_CAPTURE_CAPACITY: eight original desktop captures are already waiting')
            self.waiters[key]=(loop,event)
        reference=None
        try:
            receipt=create(key);reference=receipt['capture_ref'];deadline=loop.time()+8
            for _ in range(32):
                event.clear();state=status(receipt['id'],True)
                if state['state']=='READY':return {k:state[k] for k in ('capture_ref','request_sha256','state','required_windows','ready_windows')}
                if state['state']!='WAITING':
                    raise Conflict('EDITOR_CAPTURE_'+state['state']+': original editor metadata does not admit this action; input retained')
                self._publish(reference,state['dispatch_requests'])
                remaining=min(deadline-loop.time(),(state['expires_at_ms']/1000)-time.time())
                if remaining<=0:break
                try:await asyncio.wait_for(event.wait(),remaining)
                except asyncio.TimeoutError:
                    final=status(receipt['id'],False)
                    if final['state']=='READY':return {k:final[k] for k in ('capture_ref','request_sha256','state','required_windows','ready_windows')}
                    if final['state']!='WAITING':raise Conflict('EDITOR_CAPTURE_'+final['state']+': preserve original editor state')
                    break
            raise Conflict('EDITOR_CAPTURE_WAITING: original windows have not supplied current metadata; input retained')
        finally:
            with self.lock:
                self.waiters.pop(key,None)
                if reference:self.pending.pop(reference,None)

    def close(self):
        with self.lock:
            self.closed=True
            if self.feed:self.feed.close()
            self.pending.clear()
        self.changed()
