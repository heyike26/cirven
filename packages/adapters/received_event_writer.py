"""Keep received native events through failed or cancelled local commits.

The normal SQL event path is unchanged. An unknown commit is never retried;
its exact received payload is retained in a private recovery packet instead.
"""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
import hashlib
import json
import os
from pathlib import Path
import re
import threading
import time
import uuid

from packages.domain.paths import resolve_data_path
from packages.domain.tasks import canonical,digest
from packages.domain.store import Conflict

MAX_INLINE_RECOVERY_BYTES=128*1024*1024


def export_received_recovery(owner,row):
    """Existing explicit result export; status polling never reads packet bodies."""
    from packages.adapters.claude_transcripts import private_bytes
    result=json.loads(row['result']) if row.get('result') else {}
    recovery=row.get('received_event_recovery') or result.get('received_event_recovery') or {}
    packets=[];unavailable=[]
    for ref in recovery.get('archives',[])[:3]:
        try:
            if (ref.get('execution_id')!=row['id'] or not re.fullmatch('[a-f0-9]{32}',ref.get('id',''))
                    or type(ref.get('bytes')) is not int or not 0<ref['bytes']<=MAX_INLINE_RECOVERY_BYTES):
                raise Conflict('Recovery export reference exceeds its original scope or bound')
            path=resolve_data_path(ref['path'],owner.data_dir)
            workspace=resolve_data_path(row['workspace'],owner.data_dir)
            if path.parent!=workspace or not path.name.startswith('received-event-recovery-'+ref['id']+'-'):
                raise Conflict('Recovery export does not belong to the original execution folder')
            raw=private_bytes(path,ref['bytes'])
            if len(raw)!=ref['bytes'] or hashlib.sha256(raw).hexdigest()!=ref['sha256']:
                raise Conflict('Recovery export bytes differ')
            outer=json.loads(raw);packet=outer['value']
            if (digest(packet)!=outer['sha256'] or packet.get('execution_id')!=row['id']
                    or packet.get('id')!=ref['id'] or packet.get('schema')!='cirven.received-event-recovery.v1'):
                raise Conflict('Recovery export packet identity differs')
            exported={**packet}
            private=packet.get('private_native_frame')
            if isinstance(private,dict):
                # The existing result export exposes normalized/redacted event
                # bodies. Do not turn a private raw diagnostic into a token
                # disclosure; its exact original remains in the run folder.
                exported['private_native_frame']={k:v for k,v in private.items() if k!='base64'}
                exported['private_native_frame']['export_scope']='METADATA_ONLY_PRIVATE_ORIGINAL_RETAINED'
            packets.append(exported)
        except Exception as error:unavailable.append({'id':ref.get('id'),'error_type':type(error).__name__})
    return {'packets':packets,'unavailable':unavailable,'automatic_replay':False,'task_acceptance':'NOT_ASSESSED'}


class ReceivedEventWriter:
    def __init__(self,owner,context,ident):
        self.owner,self.context,self.ident=owner,context,ident
        self.lock=threading.RLock();self.threads=set();self.futures=set()
        self.pool=ThreadPoolExecutor(max_workers=1,thread_name_prefix='received-event-writer',initializer=self._register)
        self.current=None;self.pending=[];self.archives=[];self.failed=False
        self.close_task=None;self.join_task=None;self.close_confirmed=False
        self.packet_id=uuid.uuid4().hex;self.packet=None;self.packet_raw=None

    def _register(self):
        with self.lock:self.threads.add(threading.current_thread())

    @property
    def owned_threads(self):
        with self.lock:return tuple(self.threads)

    @property
    def owned_tasks(self):
        with self.lock:
            return tuple({*self.futures,*[t for t in (self.close_task,self.join_task) if t is not None]})

    @property
    def has_pending_data(self):
        return bool(self.pending or self.packet_raw is not None or self.current is not None and not self.current['committed']
            or not self.context['event_queue'].empty() or self.context.get('pending_received_event') is not None
            or self.context.get('pending_native_frame') is not None)

    def recovery(self):
        return {'schema':'cirven.received-event-recovery.v1','execution_id':self.ident,
            'packet_id':self.packet_id,
            'archives':list(self.archives),'pending_in_memory':self.has_pending_data,
            'writer_close_confirmed':self.close_confirmed,'automatic_replay':False,
            'scope':'RECEIVED_NATIVE_EVENT_PAYLOADS_NOT_TASK_ACCEPTANCE'}

    async def _work(self,callback,*args):
        scope=copy_context();future=self.pool.submit(scope.run,callback,*args)
        with self.lock:self.futures.add(future)
        wrapped=asyncio.wrap_future(future)
        wrapped.add_done_callback(self._consume)
        return await asyncio.shield(wrapped)

    @staticmethod
    def _consume(future):
        if not future.cancelled():future.exception()

    def _commit(self,job):
        started=time.monotonic()
        self.owner._event_batch(self.ident,job['payloads'])
        job['committed']=True
        # A diagnostic log failure cannot invalidate an already committed body.
        try:
            with (Path(self.context['workspace'])/'writer-transactions.jsonl').open('a',encoding='utf-8') as out:
                out.write(canonical({'count':len(job['payloads']),'elapsed_seconds':time.monotonic()-started})+'\n')
        except Exception as error:job['diagnostic_error_type']=type(error).__name__

    async def run(self):
        queue=self.context['event_queue']
        try:
            while True:
                batch=[await queue.get()]
                while len(batch)<32 and not queue.empty():batch.append(queue.get_nowait())
                # Snapshot before the worker starts. The original queue objects
                # cannot mutate an in-flight/unknown payload's identity.
                self.current={'id':uuid.uuid4().hex,'payloads':batch,'committed':False}
                try:
                    self.current['payloads']=json.loads(canonical(batch))
                    await self._work(self._commit,self.current)
                finally:
                    for _payload in batch:queue.task_done()
                self.current=None
                with self.lock:self.futures={f for f in self.futures if not f.done()}
        except asyncio.CancelledError:
            raise  # Shielded actual worker remains owned by this object.
        except Exception as error:
            self.failed=True
            self.context['protocol_error']='EVENT_WRITER_'+type(error).__name__
            self._mark(type(error).__name__)
            await self._stop_listener()
            self._collect()
            self.context['done'].set();self.context['connected'].set()

    async def _stop_listener(self):
        listener=self.context.get('listener')
        if listener is not None and listener is not asyncio.current_task():
            listener.cancel();await asyncio.gather(listener,return_exceptions=True)

    def _collect(self):
        if self.current is not None:
            if not self.current['committed']:self.pending.append(self.current)
            self.current=None
        queue=self.context['event_queue'];batch=[]
        while not queue.empty():
            batch.append(queue.get_nowait());queue.task_done()
        received=self.context.pop('pending_received_event',None)
        if received is not None:batch.append(received)
        if batch:self.pending.append({'id':uuid.uuid4().hex,'payloads':batch,
            'committed':False,'commit_not_attempted':True})

    def _mark(self,reason):
        budget=self.context.get('task_resource_budget')
        if budget is not None:budget.mark_unconfirmed('EVENT_WRITER_'+reason,original_evidence_retained=False)
        self.context['received_events_unconfirmed']=True

    def _packet(self):
        if self.packet_raw is not None:return self.packet_raw
        raw_frame=self.context.get('pending_native_frame')
        if not self.pending and raw_frame is None:return None
        packet={'schema':'cirven.received-event-recovery.v1','id':self.packet_id,'execution_id':self.ident,
            'batches':[{'id':j['id'],'payloads':j['payloads'],'commit_status':
                'NOT_ATTEMPTED' if j.get('commit_not_attempted') else 'UNCONFIRMED_NOT_REPLAYABLE'} for j in self.pending],
            'automatic_replay':False,'task_acceptance':'NOT_ASSESSED'}
        if raw_frame is not None:
            import base64
            packet['private_native_frame']={'representation':'ORIGINAL_NATIVE_SSE_FRAME_PRIVATE',
                'sha256':hashlib.sha256(raw_frame).hexdigest(),'base64':base64.b64encode(raw_frame).decode('ascii')}
        self.packet=packet;self.packet_raw=canonical({'value':packet,'sha256':digest(packet)}).encode()
        return self.packet_raw

    def _archive(self):
        raw=self._packet()
        if raw is None:return
        from packages.adapters.claude_transcripts import ExecutionArtifacts,private_bytes
        io=None
        try:io=ExecutionArtifacts(self.owner,self.ident)
        except Exception:pass  # Database failure must not forbid preserving a received body.
        root=resolve_data_path(str(self.context['workspace']),self.owner.data_dir)
        root.mkdir(parents=True,exist_ok=True,mode=0o700)
        saved=None
        for index in range(3):
            path=root/('received-event-recovery-'+self.packet_id+'-'+str(index)+'.json')
            try:
                if io is not None:io.write(path,raw,purpose='received-event-recovery',part=self.packet_id)
                else:
                    if not path.exists():
                        with path.open('xb') as out:out.write(raw);out.flush();os.fsync(out.fileno())
                        path.chmod(0o600)
                    if private_bytes(path,len(raw))!=raw:raise Conflict('Received event recovery candidate differs')
                if private_bytes(path,len(raw))!=raw:raise Conflict('Received event recovery readback differs')
                saved=path;break
            except Exception:continue  # Preserve partial/unknown candidates, never overwrite them.
        if saved is None:raise Conflict('Received event recovery could not be saved; original memory retained')
        self.archives=[{'id':self.packet_id,'execution_id':self.ident,
            'path':str(saved.relative_to(Path(self.owner.data_dir).absolute())),
            'sha256':hashlib.sha256(raw).hexdigest(),'bytes':len(raw),'representation':'PRIVATE_RECEIVED_EVENT_RECOVERY'}]
        self.pending=[];self.packet_raw=None;self.packet=None
        self.context.pop('pending_native_frame',None)

    async def flush(self):
        if self.close_confirmed:return True
        if self.close_task is None or self.close_task.done() and self.close_task.result() is False:
            self.close_task=asyncio.create_task(self._flush())
        return await asyncio.shield(self.close_task)

    async def _flush(self):
        self.context['closing']=True
        seal=getattr(self.context.get('relay'),'seal',None)
        if callable(seal):seal()
        await self._stop_listener()
        writer=self.context.get('writer')
        if writer is not None and writer is not asyncio.current_task() and not writer.done():
            writer.cancel();await asyncio.gather(writer,return_exceptions=True)
        # Wait the original actual commit. Cancellation/observation expiry is
        # never permission to rerun it or to report the worker stopped.
        with self.lock:futures=tuple(self.futures)
        for future in futures:
            try:await asyncio.shield(asyncio.wrap_future(future))
            except Exception as error:
                self.failed=True;self._mark(type(error).__name__)
        self._collect()
        if self.pending or self.context.get('pending_native_frame') is not None or self.packet_raw is not None:
            try:await self._work(self._archive)
            except Exception as error:
                self._mark(type(error).__name__)
                self.context['received_event_recovery']=self.recovery()
                return False
        self.join_task=asyncio.create_task(asyncio.to_thread(self.pool.shutdown,wait=True))
        await asyncio.shield(self.join_task)
        self.close_confirmed=all(not thread.is_alive() for thread in self.owned_threads)
        self.context['received_event_recovery']=self.recovery()
        return self.close_confirmed
