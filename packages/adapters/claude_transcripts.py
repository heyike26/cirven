"""Bounded original SDK transcript mirrors with received-batch preservation.

Received decoded entries are archived before a mutable working mirror changes.
A bad/full mirror cannot become a fresh resume checkpoint. This is payload and
retained-artifact bookkeeping, not measurement of CLI/OS physical disk traffic.
"""
from collections import OrderedDict
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import tempfile
import uuid

from packages.adapters.task_resources import _resolved
from packages.domain.task_resource_budget import MAX_OUTPUT_GAPS
from packages.domain.context_read_budget import context_read_scope
from packages.domain.paths import resolve_data_path
from packages.domain.tasks import canonical,digest,now
from packages.domain.store import Conflict

MAX_TRANSCRIPT_BYTES=8*1024*1024
MAX_BATCH_BYTES=16*1024*1024
MAX_PENDING_BATCHES=8
MAX_SNAPSHOT_FILES=4096
MAX_SNAPSHOT_ENTRIES=8192


def private_bytes(path,maximum):
    path=Path(path)
    for part in (path,*path.parents):
        info=part.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info,'st_file_attributes',0)&0x400:
            raise Conflict('Original transcript source cannot be a link or reparse point')
    before=path.lstat()
    if not stat.S_ISREG(before.st_mode) or before.st_nlink!=1 or before.st_size>maximum:
        raise Conflict('Original transcript is not an ordinary bounded private file')
    flags=os.O_RDONLY|getattr(os,'O_BINARY',0)|getattr(os,'O_NOFOLLOW',0)
    with os.fdopen(os.open(path,flags),'rb') as stream:
        opened=os.fstat(stream.fileno())
        if (opened.st_dev,opened.st_ino)!=(before.st_dev,before.st_ino):raise Conflict('Original transcript identity changed')
        raw=stream.read(before.st_size);after=os.fstat(stream.fileno())
    live=path.lstat()
    stamp=lambda v:(v.st_dev,v.st_ino,v.st_size,v.st_mtime_ns,v.st_ctime_ns,v.st_nlink)
    if len(raw)!=before.st_size or stamp(before)!=stamp(after) or stamp(before)!=stamp(live):
        raise Conflict('Original transcript changed while reading')
    return raw


def source_private_bytes(path,maximum):
    # Outside an admitted execution this is a bounded observation; it does not
    # allocate a historical task allowance or backfill its old consumption.
    from packages.domain.context_read_budget import current_context_read_budget
    with context_read_scope():
        allowance=current_context_read_budget()
        return allowance.guarded(lambda bound:private_bytes(path,bound),maximum)


class ExecutionArtifacts:
    def __init__(self,owner,ident):
        self.owner,self.ident=owner,ident
        with owner.service.connect() as db:
            db.execute('BEGIN')
            row=db.execute('SELECT * FROM agent_executions WHERE id=?',(ident,)).fetchone()
            if row is None:raise Conflict('Original artifact execution is missing')
            self.row=dict(row);self.pid=row['project_id']
            self.budget,self.scope=_resolved(owner,self.pid,db,self.row)

    def path(self,path):return resolve_data_path(str(path),self.owner.data_dir)

    def read(self,path,maximum):
        path=self.path(path);before=path.lstat()
        if before.st_size>maximum:raise Conflict('Original artifact exceeds its read bound')
        ticket=self.budget.reserve_read(before.st_size)
        try:raw=private_bytes(path,before.st_size)
        except BaseException:
            try:self.budget.finish_read(ticket,None)
            except Exception:pass
            raise
        self.budget.finish_read(ticket,len(raw));return raw

    def charge(self,path,content_sha,size,*,purpose,part='whole'):
        path=self.path(path);relative=path.relative_to(Path(self.owner.data_dir).absolute()).as_posix()
        key=digest({'execution_id':self.ident,'artifact':relative,'purpose':purpose,'part':part})
        return self.budget.retain_once(key,content_sha,size)

    def write(self,path,raw,*,purpose,part='whole'):
        path=self.path(path);path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        try:self.budget.allocate()
        except Exception:self.budget.mark_unconfirmed('ARTIFACT_ALLOCATION_UNCONFIRMED')
        try:
            if not path.exists():
                with path.open('xb') as stream:stream.write(raw);stream.flush();os.fsync(stream.fileno())
                path.chmod(0o600)
        except Exception:
            # A lost local write acknowledgement is checked, never overwritten.
            if not path.exists() or private_bytes(path,len(raw))!=raw:raise
        if private_bytes(path,len(raw))!=raw:raise Conflict('Original artifact target contains another payload')
        return self.charge(path,hashlib.sha256(raw).hexdigest(),len(raw),purpose=purpose,part=part)

    def mark(self,reason):
        self.budget.mark_unconfirmed(reason)
        try:
            with self.owner.service.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                if db.execute('SELECT count(*) FROM agent_execution_resource_gaps').fetchone()[0]<MAX_OUTPUT_GAPS:
                    db.execute('INSERT INTO agent_execution_resource_gaps VALUES (?,?,?,?,?) ON CONFLICT(execution_id) DO NOTHING',
                        (self.ident,self.pid,self.budget.id,reason,now()))
        except Exception:pass  # In-memory and private marker still seal this owner.
        record=getattr(self.owner,'http_relays',{}).get(self.scope['native_call_execution_id'])
        if record:record['relay'].seal()
        context=getattr(self.owner,'active',{}).get(self.scope['native_call_execution_id'])
        if context and context.get('task_resource_budget'):context['task_resource_budget'].accounting_unconfirmed=True


class OwnedTranscriptStore:
    def __init__(self,root,workspace,*,owner,execution_id):
        from claude_agent_sdk import project_key_for_directory
        self.io=ExecutionArtifacts(owner,execution_id);self.root=self.io.path(root)
        expected=Path(owner.data_dir).absolute()/'agent-transcripts'/execution_id
        if self.root!=expected:raise Conflict('Original transcript storage owner differs')
        self.project_key=project_key_for_directory(workspace);self.unconfirmed=False
        self.pending=OrderedDict();self.sequence=0
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)

    @property
    def marker(self):return self.root/'mirror-unconfirmed.json'

    def healthy(self):return not self.unconfirmed and not self.marker.exists()

    def path(self,key):
        if key.get('project_key')!=self.project_key or key.get('subpath') is not None:
            raise Conflict('Transcript key does not belong to its original execution workspace')
        session=str(uuid.UUID(key['session_id']))
        path=self.root/(session+'.jsonl')
        if path.is_symlink() or self.root.is_symlink():raise Conflict('Original transcript cannot be a symlink')
        return path

    def _head(self,path):return path.with_suffix('.mirror-head.json')

    def _read_head(self,path):
        from packages.domain.native_capabilities import _json
        head_path=self._head(path)
        if not head_path.exists():raise Conflict('Original transcript mirror reference is missing')
        outer=_json(self.io.read(head_path,16384))
        if (set(outer)!={'value','sha256'} or digest(outer['value'])!=outer['sha256']
                or outer['value'].get('execution_id')!=self.io.ident or outer['value'].get('session_id')!=path.stem):
            raise Conflict('Original transcript mirror reference changed')
        return outer['value']

    def _save_head(self,path,raw):
        from packages.domain.native_capabilities import NativeCapabilities,_json
        value={'execution_id':self.io.ident,'session_id':path.stem,'bytes':len(raw),'content_sha256':hashlib.sha256(raw).hexdigest()}
        target=self._head(path)
        if target.exists() and _json(private_bytes(target,16384))=={'value':value,'sha256':digest(value)}:return
        try:NativeCapabilities._write(target,value,replace=target.exists())
        except Exception:
            if not target.exists() or _json(private_bytes(target,16384))!={'value':value,'sha256':digest(value)}:raise
        if _json(private_bytes(target,16384))!={'value':value,'sha256':digest(value)}:raise Conflict('Transcript reference write unconfirmed')

    def _raw(self,path):
        if not path.exists():
            if self._head(path).exists():raise Conflict('Original transcript body is missing; no reset')
            return b''
        head=self._read_head(path);raw=self.io.read(path,MAX_TRANSCRIPT_BYTES)
        if len(raw)!=head['bytes'] or hashlib.sha256(raw).hexdigest()!=head['content_sha256']:
            raise Conflict('Original transcript differs from its saved reference')
        return raw

    def _failed(self,reason,batch_id=None):
        from packages.domain.native_capabilities import NativeCapabilities
        self.unconfirmed=True;self.io.mark(reason)
        value={'schema':'eeas.native-transcript-unconfirmed.v1','execution_id':self.io.ident,
            'reason':reason,'batch_id':batch_id,'created_at':now(),'automatic_replay_allowed':False}
        try:
            if not self.marker.exists():NativeCapabilities._write(self.marker,value)
        except Exception:pass

    async def load(self,key):
        if not self.healthy():raise Conflict('Original transcript mirror is unconfirmed; received batches retained')
        path=self.path(key);raw=self._raw(path)
        return None if not path.exists() else [json.loads(line) for line in raw.decode().splitlines()]

    def seed(self,key,raw,*,purpose):
        if len(raw)>MAX_TRANSCRIPT_BYTES:raise Conflict('Native resume transcript exceeds its original bound')
        path=self.path(key)
        receipt=self.io.write(path,raw,purpose=purpose)
        self._save_head(path,raw)
        if receipt is None:self._failed('TRANSCRIPT_SEED_ACCOUNTING_UNCONFIRMED')
        return receipt

    def _archive(self,pending):
        record=pending['record'];raw=(canonical({'value':record,'sha256':digest(record)})+'\n').encode()
        if len(raw)>MAX_BATCH_BYTES:raise Conflict('Decoded transcript batch exceeds its bounded preservation capacity')
        for attempt in range(3):
            path=self.root/'received-batches'/(pending['id']+'-'+str(attempt)+'.json')
            if path.exists():
                try:
                    if private_bytes(path,MAX_BATCH_BYTES)!=raw:continue
                except Exception:continue
            receipt=self.io.write(path,raw,purpose='RECEIVED_TRANSCRIPT_BATCH')
            return path,receipt
        raise Conflict('Original received batch candidates are unreadable; original files retained')

    def _replace_mirror(self,path,before,after):
        temporary=None
        try:
            fd,name=tempfile.mkstemp(prefix='mirror-pending-',dir=self.root);temporary=Path(name)
            with os.fdopen(fd,'wb') as stream:stream.write(after);stream.flush();os.fsync(stream.fileno())
            current=private_bytes(path,MAX_TRANSCRIPT_BYTES) if path.exists() else b''
            if current!=before:raise Conflict('Original mirror changed before replacement')
            os.replace(temporary,path);temporary=None;path.chmod(0o600)
        except Exception:
            if not path.exists() or private_bytes(path,MAX_TRANSCRIPT_BYTES)!=after:raise
        finally:
            # The complete incoming batch was archived before this private,
            # reconstructable working copy was attempted. Preserve unknown bytes.
            if temporary is not None and temporary.exists():
                partial=private_bytes(temporary,MAX_TRANSCRIPT_BYTES)
                current=private_bytes(path,MAX_TRANSCRIPT_BYTES) if path.exists() else b''
                if current in (before,after) and after.startswith(partial):temporary.unlink()
        if private_bytes(path,MAX_TRANSCRIPT_BYTES)!=after:raise Conflict('Original mirror replacement is unconfirmed')

    async def append(self,key,entries):
        path=self.path(key)
        if not entries:return
        # Pinned SDK retries failed append with the same entries object. Once
        # success is returned the handle is removed: a later identical no-UUID
        # delivery remains a new delivery instead of being silently deduplicated.
        payload=canonical(entries);key_sha=digest(key)
        pending=next((v for v in self.pending.values() if v['object'] is entries and v['payload']==payload and v['key_sha']==key_sha),None)
        if pending is None:
            if len(self.pending)>=MAX_PENDING_BATCHES:
                self._failed('UNSAVED_TRANSCRIPT_BATCH_CAPACITY');raise Conflict('Unsaved native batches remain in memory; preserve native originals')
            ident=uuid.uuid4().hex;self.sequence+=1
            pending={'id':ident,'object':entries,'payload':payload,'key_sha':key_sha,'record':{'schema':'eeas.received-native-transcript-batch.v1',
                'execution_id':self.io.ident,'key':deepcopy(key),'batch_id':ident,'delivery':self.sequence,'received_at':now(),
                'entries':deepcopy(entries),'representation':'SDK_DECODED_ENTRIES_NOT_RAW_STDOUT'}}
            self.pending[ident]=pending
        try:archive,archive_receipt=self._archive(pending)
        except BaseException:
            self._failed('RECEIVED_TRANSCRIPT_BATCH_UNSAVED',pending['id']);raise
        if archive_receipt is None or not self.healthy():
            self._failed('RECEIVED_TRANSCRIPT_BATCH_ACCOUNTING_UNCONFIRMED',pending['id']);self.pending.pop(pending['id'],None);return
        try:
            with self.io.budget.materials._lock(self.root/'mirror-writer.lock',blocking=False,create=True):
                old=self._raw(path);existing=[json.loads(line) for line in old.decode().splitlines()]
                by_id={e['uuid']:e for e in existing if isinstance(e,dict) and e.get('uuid')};additions=[]
                for entry in entries:
                    if not isinstance(entry,dict) or not isinstance(entry.get('type'),str):raise Conflict('Invalid decoded native transcript entry')
                    ident=entry.get('uuid')
                    if ident in by_id:
                        if entry!=by_id[ident]:raise Conflict('Original transcript UUID is bound to different data')
                        continue
                    additions.append(entry)
                    if ident:by_id[ident]=entry
                extra=''.join(canonical(e)+'\n' for e in additions).encode();after=old+extra
                if len(after)>MAX_TRANSCRIPT_BYTES:raise Conflict('Native working mirror capacity reached; received batches retained')
                if extra:self._replace_mirror(path,old,after)
                self._save_head(path,after)
                if extra and self.io.charge(path,hashlib.sha256(extra).hexdigest(),len(extra),purpose='NATIVE_MIRROR_ADDITION',part=pending['id']) is None:
                    raise Conflict('Native mirror addition accounting is unconfirmed')
        except BaseException:
            # Already received entries are durably archived. The SDK may yield
            # its original result, but this mirror cannot authorize continuation.
            self._failed('NATIVE_TRANSCRIPT_MIRROR_UNCONFIRMED',pending['id'])
        self.pending.pop(pending['id'],None)
