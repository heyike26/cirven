"""One durable software resource allowance across an admitted original work.

Independent native/POSIX file locking prevents nested Store writer deadlocks and
keeps reservations after a caller's SQL rollback. Counters are payload/accounting
units, not physical filesystem I/O, commercial usage, or an OS/origin quota.
Already-arriving output is retained and may exceed the soft retention threshold;
that overage blocks subsequent model admission, never erases original evidence.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import os,re,threading,uuid
from weakref import WeakValueDictionary
from packages.domain.local_materials import LocalMaterials
from packages.domain.paths import resolve_data_path
from packages.domain.store import Conflict,NotFound
from packages.domain.tasks import canonical,digest,now

SCHEMA='cirven.task-resource-budget.v1'
LIMITS={'source_bytes':536870912,'source_reads':8192,'model_attempts':128,'retained_output_bytes':134217728}
MAX_PENDING=32
MAX_ADJUSTMENTS=32
MAX_OUTPUT_GAPS=16384
MAX_RETENTION_RECEIPTS=512
MAX_STATE_BYTES=196608
_CURRENT=ContextVar('cirven_original_task_resource_budget',default=None)
_LOCAL_WRITERS=WeakValueDictionary()
_LOCAL_WRITERS_GUARD=threading.Lock()


class TaskResourceLimit(Conflict):pass


class TaskResourceBudget:
    def __init__(self,workspaces,authority,*,limits=None):
        self.workspaces=workspaces;self.materials=LocalMaterials(workspaces)
        if (not isinstance(authority,dict) or not authority or len(canonical(authority).encode())>8192
                or not isinstance(authority.get('project_id'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',authority['project_id'])):
            raise Conflict('Exact server-owned work authority is required for resource accounting')
        self.authority=deepcopy(authority);self.id=digest({'schema':SCHEMA,'authority':authority})
        self.limits=deepcopy(LIMITS if limits is None else limits)
        if set(self.limits)!=set(LIMITS) or any(type(v)is not int or not 1<=v<2**48 for v in self.limits.values()):raise Conflict('Invalid task resource limits')
        self.path=resolve_data_path('task-resource-budgets/'+self.id+'/state.json',workspaces.data_dir)
        self.lock_path=self.path.with_name('writer.lock');self.marker=self.path.with_name('allocation.json');self.gap=self.path.with_name('accounting-unconfirmed.json');self.accounting_unconfirmed=False

    @contextmanager
    def _lock(self,*,create):
        # Different callbacks/instances in this process share one short ledger
        # critical section. It contains file accounting and a WAL format read,
        # never a model/tool call, await, or SQL writer acquisition. Ordinary
        # local contention must not become a sticky unknown-output marker.
        key=os.path.normcase(str(self.lock_path.absolute()))
        with _LOCAL_WRITERS_GUARD:
            local=_LOCAL_WRITERS.get(key)
            if local is None:
                local=threading.RLock();_LOCAL_WRITERS[key]=local
        with local:
            # Cross-process ownership remains nonblocking, including when a
            # caller already holds its independent Store writer transaction.
            with self.materials._lock(self.lock_path,blocking=False,create=create):yield

    def _read(self):
        from packages.domain.native_capabilities import _json
        with self.workspaces.store.connect() as db:
            format_row=db.execute('SELECT format_tag FROM task_resource_artifact_format WHERE singleton=1').fetchone()
        if format_row is None or format_row[0]!='cirven.task-resource-retentions.v1-512-192k':
            raise Conflict('Task artifact accounting format is incompatible; preserve the stopped original snapshot')
        from packages.adapters.opencode_native_review import regular
        if not self.path.exists():raise NotFound('No task resource allowance is allocated')
        allocation=_json(regular(self.marker,16384,private=True))
        if (not isinstance(allocation,dict) or set(allocation)!={'value','sha256'} or not isinstance(allocation['value'],dict) or digest(allocation['value'])!=allocation['sha256']
                or allocation['value'].get('authority')!=self.authority or allocation['value'].get('limits')!=self.limits
                or allocation['value'].get('schema')!=SCHEMA):raise Conflict('Task resource allocation marker changed')
        outer=_json(regular(self.path,MAX_STATE_BYTES+4096,private=True))
        if not isinstance(outer,dict) or set(outer)!={'value','sha256'} or digest(outer['value'])!=outer['sha256']:raise Conflict('Task resource record integrity changed')
        v=outer['value']
        if (not isinstance(v,dict) or set(v)-{'adjustments','retentions'}!={'schema','authority','limits','revision','used','pending_reads','updated_at'}
                or v['schema']!=SCHEMA or v['authority']!=self.authority or v['limits']!=self.limits
                or type(v['revision'])is not int or not 0<=v['revision']<2**48
                or not isinstance(v['used'],dict) or set(v['used'])!=set(LIMITS)
                or any(type(n)is not int or not 0<=n<2**53 for n in v['used'].values())
                or not isinstance(v['pending_reads'],dict) or len(v['pending_reads'])>MAX_PENDING
                or not isinstance(v['updated_at'],str)):
            raise Conflict('Task resource authority/limits/counters changed')
        receipts=v.get('retentions',[]);seen=set();revision=-1;total=0
        if not isinstance(receipts,list) or len(receipts)>MAX_RETENTION_RECEIPTS:raise Conflict('Retained artifact receipt capacity changed')
        for receipt in receipts:
            if (not isinstance(receipt,dict) or set(receipt)!={'id','content_sha256','bytes','revision'}
                    or not isinstance(receipt['id'],str) or not re.fullmatch('[a-f0-9]{64}',receipt['id']) or receipt['id'] in seen
                    or not isinstance(receipt['content_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',receipt['content_sha256'])
                    or type(receipt['bytes'])is not int or not 0<=receipt['bytes']<2**53
                    or type(receipt['revision'])is not int or not revision<receipt['revision']<=v['revision']):
                raise Conflict('Original retained artifact receipt changed')
            seen.add(receipt['id']);revision=receipt['revision'];total+=receipt['bytes']
        if total>v['used']['retained_output_bytes']:raise Conflict('Retained artifact receipts exceed original consumption')
        limits=self._validate_adjustments(v)
        for key,row in v['pending_reads'].items():
            if (not re.fullmatch('[a-f0-9]{32}',key) or not isinstance(row,dict) or set(row)!={'bytes','created_at'}
                    or type(row['bytes'])is not int or not 0<=row['bytes']<=limits['source_bytes'] or not isinstance(row['created_at'],str)):
                raise Conflict('Task resource pending read changed')
        if v['used']['source_bytes']+sum(r['bytes'] for r in v['pending_reads'].values())>limits['source_bytes']:
            raise Conflict('Task source allowance exceeds its original limit')
        if v['used']['source_reads']>limits['source_reads'] or v['used']['model_attempts']>limits['model_attempts']:
            raise Conflict('Task resource consumed counters exceed the effective allowance')
        return v

    def _save(self,v,*,replace=True):
        from packages.domain.native_capabilities import NativeCapabilities
        if len(canonical(v).encode())>MAX_STATE_BYTES:raise TaskResourceLimit('Task resource metadata capacity reached; original counters retained')
        NativeCapabilities._write(self.path,v,replace=replace)

    def allocate(self):
        from packages.domain.native_capabilities import NativeCapabilities
        with self._lock(create=True):
            if self.path.exists():return self._read()
            if self.marker.exists():raise Conflict('Original task resource state is missing; no allowance reset')
            NativeCapabilities._write(self.marker,{'schema':SCHEMA,'authority':self.authority,'limits':self.limits,'created_at':now()})
            value={'schema':SCHEMA,'authority':self.authority,'limits':self.limits,'revision':0,'used':{k:0 for k in LIMITS},'pending_reads':{},'updated_at':now()}
            self._save(value,replace=False);return value

    def _change(self,update):
        with self._lock(create=False):
            v=self._read()
            if v['revision']>=2**48-1:raise TaskResourceLimit('Task resource revision capacity reached; original counters retained')
            result=update(v);v['revision']+=1;v['updated_at']=now();self._save(v);return result

    @staticmethod
    def effective_limits(v):
        return v['adjustments'][-1]['limits'] if v.get('adjustments') else v['limits']

    @staticmethod
    def _available(v):return TaskResourceBudget.effective_limits(v)['source_bytes']-v['used']['source_bytes']-sum(r['bytes'] for r in v['pending_reads'].values())

    def remaining_source_bytes(self):
        with self._lock(create=False):return self._available(self._read())

    def _output_gap_present(self):
        with self.workspaces.store.connect() as db:
            return bool(db.execute('SELECT count(*) FROM agent_execution_resource_gaps').fetchone()[0]>=MAX_OUTPUT_GAPS
                or db.execute('SELECT 1 FROM agent_execution_resource_gaps WHERE project_id=? AND resource_id=? LIMIT 1',
                    (self.authority['project_id'],self.id)).fetchone())

    def _require_output_confirmed(self):
        if self._output_gap_present():
            raise TaskResourceLimit('Original execution output accounting is unconfirmed; preserve received evidence')

    def consume(self,*,source_bytes=0,source_reads=0,model_attempts=0,retained_output_bytes=0):
        units=locals().copy();units.pop('self')
        if any(type(n)is not int or n<0 for n in units.values()):raise TaskResourceLimit('Invalid task resource charge')
        if model_attempts:self._require_output_confirmed()
        def update(v):
            used=v['used'];limits=self.effective_limits(v)
            if model_attempts and (self.accounting_unconfirmed or self.gap.exists()):raise TaskResourceLimit('Task output accounting is unconfirmed; preserve originals and stop new generation')
            if source_bytes>self._available(v) or used['source_reads']+source_reads>limits['source_reads'] or used['model_attempts']+model_attempts>limits['model_attempts']:
                raise TaskResourceLimit('Shared original-work source/model allowance exhausted')
            if model_attempts and used['retained_output_bytes']>=limits['retained_output_bytes']:
                raise TaskResourceLimit('Shared retained-output allowance reached; original material retained')
            if any(used[k]+n>=2**53 for k,n in units.items()):raise TaskResourceLimit('Task resource counter capacity reached')
            for k,n in units.items():used[k]+=n
            return deepcopy(used)
        return self._change(update)

    def reserve_read(self,maximum):
        if type(maximum)is not int or maximum<0:raise TaskResourceLimit('Invalid bounded task read')
        ticket=uuid.uuid4().hex
        def update(v):
            if len(v['pending_reads'])>=MAX_PENDING or maximum>self._available(v) or v['used']['source_reads']>=self.effective_limits(v)['source_reads']:
                raise TaskResourceLimit('Shared task read reservation exhausted')
            v['used']['source_reads']+=1;v['pending_reads'][ticket]={'bytes':maximum,'created_at':now()};return ticket
        return self._change(update)

    def finish_read(self,ticket,actual=None):
        def update(v):
            row=v['pending_reads'].get(ticket)
            if row is None:raise Conflict('Original task read reservation is no longer pending')
            size=row['bytes'] if actual is None else actual
            if type(size)is not int or not 0<=size<=row['bytes']:raise Conflict('Successful source read exceeds its original reservation')
            v['used']['source_bytes']+=size;del v['pending_reads'][ticket]
        return self._change(update)

    def _validate_adjustments(self,v):
        rows=v.get('adjustments',[]);limits=deepcopy(self.limits);seen=set();previous_revision=-1
        if not isinstance(rows,list) or len(rows)>MAX_ADJUSTMENTS:raise Conflict('Task allowance adjustment history changed')
        for row in rows:
            if (not isinstance(row,dict) or set(row)!={'operation_id','request_sha256','expected_resource_ref','requested_limits','previous_limits','limits','revision','status','reason','note','created_at'}
                    or not isinstance(row['operation_id'],str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',row['operation_id']) or row['operation_id'] in seen
                    or not isinstance(row['request_sha256'],str) or not re.fullmatch('[a-f0-9]{64}',row['request_sha256'])
                    or row['previous_limits']!=limits or not self._valid_limits(row['requested_limits']) or not self._valid_limits(row['limits'])
                    or type(row['revision'])is not int or not 1<=row['revision']<=v['revision'] or row['revision']<=previous_revision
                    or row['status'] not in {'APPLIED','REJECTED'} or not isinstance(row['note'],str) or len(row['note'])>160
                    or not isinstance(row['created_at'],str)):
                raise Conflict('Task allowance adjustment record differs')
            reference=row['expected_resource_ref']
            if (not isinstance(reference,dict) or set(reference)!={'id','revision','sha256'} or reference['id']!=self.id
                    or type(reference['revision'])is not int or not 0<=reference['revision']<2**48
                    or not isinstance(reference['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',reference['sha256'])):
                raise Conflict('Task allowance adjustment original resource reference differs')
            if row['status']=='APPLIED':
                if row['reason'] is not None or row['limits']!=row['requested_limits'] or reference['revision']!=row['revision']-1:raise Conflict('Applied task allowance history differs')
            elif row['reason'] not in {'RESOURCE_SNAPSHOT_CHANGED','BELOW_CONSUMED_OR_RESERVED'} or row['limits']!=limits:
                raise Conflict('Rejected task allowance changed the effective limits')
            limits=row['limits'];seen.add(row['operation_id']);previous_revision=row['revision']
        return limits

    @staticmethod
    def _valid_limits(value):return isinstance(value,dict) and set(value)==set(LIMITS) and all(type(n)is int and 1<=n<2**48 for n in value.values())

    def _adjustment_result(self,row,v):
        return {'kind':'ORIGINAL_WORK_RESOURCE_ALLOWANCE_OPERATION','resource_id':self.id,'operation_id':row['operation_id'],
            'request_sha256':row['request_sha256'],'status':row['status'],'reason':row['reason'],
            'adjustment_ref':{'id':digest([self.id,row['operation_id']]),'revision':1,'sha256':digest(row)},
            'recorded_revision':row['revision'],'applied_revision':row['revision'] if row['status']=='APPLIED' else None,
            'previous_limits':deepcopy(row['previous_limits']),'limits':deepcopy(row['limits']),
            'requested_limits':deepcopy(row['requested_limits']),
            'current_resource_ref':{'id':self.id,'revision':v['revision'],'sha256':digest(v)},
            'usage_refunded':False,'unknown_reservations_released':False,'model_or_tool_started':False}

    def adjustment(self,operation_id):
        with self._lock(create=False):
            v=self._read()
            for row in v.get('adjustments',[]):
                if row['operation_id']==operation_id:return self._adjustment_result(row,v)
        return {'kind':'ORIGINAL_WORK_RESOURCE_ALLOWANCE_OPERATION','resource_id':self.id,'operation_id':operation_id,
            'status':'NOT_OBSERVED','reason':'ABSENCE_IS_NOT_PROOF_OF_NOT_APPLIED','automatic_replay_allowed':False,'read_only':True,'metadata_writes':0}

    def adjust(self,operation_id,request_sha256,expected_resource_ref,limits,*,note=''):
        if (not isinstance(operation_id,str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',operation_id)
                or not isinstance(request_sha256,str) or not re.fullmatch('[a-f0-9]{64}',request_sha256)
                or not isinstance(note,str) or len(note)>160 or not self._valid_limits(limits)
                or not isinstance(expected_resource_ref,dict) or set(expected_resource_ref)!={'id','revision','sha256'}
                or expected_resource_ref['id']!=self.id or type(expected_resource_ref['revision'])is not int or not 0<=expected_resource_ref['revision']<2**48
                or not isinstance(expected_resource_ref['sha256'],str) or not re.fullmatch('[a-f0-9]{64}',expected_resource_ref['sha256'])):
            raise Conflict('Exact original task allowance adjustment is required')
        note.encode('utf-8')
        with self._lock(create=False):
            v=self._read();rows=v.get('adjustments',[])
            for old in rows:
                if old['operation_id']==operation_id:
                    if old['request_sha256']!=request_sha256 or old['expected_resource_ref']!=expected_resource_ref or old['requested_limits']!=limits or old['note']!=note:raise Conflict('Allowance operation ID already binds another exact request')
                    return self._adjustment_result(old,v)
            if len(rows)>=MAX_ADJUSTMENTS or v['revision']>=2**48-1:raise TaskResourceLimit('Task allowance history capacity reached; original records retained')
            current={'id':self.id,'revision':v['revision'],'sha256':digest(v)};previous=self.effective_limits(v);reason=None
            if expected_resource_ref!=current:reason='RESOURCE_SNAPSHOT_CHANGED'
            elif (limits['source_bytes']<v['used']['source_bytes']+sum(r['bytes'] for r in v['pending_reads'].values())
                    or limits['source_reads']<v['used']['source_reads'] or limits['model_attempts']<v['used']['model_attempts']):reason='BELOW_CONSUMED_OR_RESERVED'
            # The rejection itself is durable and queryable. No unknown usage,
            # held read or accounting-gap marker is refunded or cleared.
            revision=v['revision']+1
            row={'operation_id':operation_id,'request_sha256':request_sha256,'expected_resource_ref':deepcopy(expected_resource_ref),
                'requested_limits':deepcopy(limits),'previous_limits':deepcopy(previous),'limits':deepcopy(previous if reason else limits),
                'revision':revision,'status':'REJECTED' if reason else 'APPLIED','reason':reason,'note':note,'created_at':now()}
            v.update(adjustments=[*rows,row],revision=revision,updated_at=now());self._save(v)
            return self._adjustment_result(row,v)

    @staticmethod
    def _retention_ref(row):
        return {'id':row['id'],'revision':row['revision'],'sha256':digest(row),'bytes':row['bytes'],
            'content_sha256':row['content_sha256'],'read_only':True}

    def retention_receipt(self,operation_id):
        if not isinstance(operation_id,str) or not re.fullmatch('[a-f0-9]{64}',operation_id):raise Conflict('Exact artifact receipt ID required')
        with self._lock(create=False):
            value=self._read();row=next((r for r in value.get('retentions',[]) if r['id']==operation_id),None)
            return None if row is None else self._retention_ref(row)

    def retain_once(self,operation_id,content_sha256,size):
        """One exact received-artifact debit; readback repairs only its own ACK.

        Unknown/missing receipts never authorize a model replay or consumption
        reset. Already arriving bytes remain stored even if this bookkeeping
        refuses capacity or cannot be confirmed.
        """
        try:
            if (not isinstance(operation_id,str) or not re.fullmatch('[a-f0-9]{64}',operation_id)
                    or not isinstance(content_sha256,str) or not re.fullmatch('[a-f0-9]{64}',content_sha256)
                    or type(size)is not int or not 0<=size<2**53):raise Conflict('Invalid original retained artifact identity')
            with self._lock(create=False):
                value=self._read();rows=value.get('retentions',[]);old=next((r for r in rows if r['id']==operation_id),None)
                if old is not None:
                    if old['content_sha256']!=content_sha256 or old['bytes']!=size:raise Conflict('Original artifact receipt binds another payload')
                    return self._retention_ref(old)
                if (len(rows)>=MAX_RETENTION_RECEIPTS or value['revision']>=2**48-1
                        or value['used']['retained_output_bytes']+size>=2**53):raise TaskResourceLimit('Original artifact receipt capacity reached')
                row={'id':operation_id,'content_sha256':content_sha256,'bytes':size,'revision':value['revision']+1}
                value['used']['retained_output_bytes']+=size
                value.update(retentions=[*rows,row],revision=row['revision'],updated_at=now())
                self._save(value)
                return self._retention_ref(row)
        except Exception as error:
            try:
                observed=self.retention_receipt(operation_id)
                if observed and observed['content_sha256']==content_sha256 and observed['bytes']==size:return observed
            except Exception:pass
            self.mark_unconfirmed(type(error).__name__)
            return None

    def mark_unconfirmed(self,error_type,*,original_evidence_retained=True):
        self.accounting_unconfirmed=True
        try:
            from packages.domain.native_capabilities import NativeCapabilities
            if not self.gap.exists():NativeCapabilities._write(self.gap,{'resource_id':self.id,'created_at':now(),
                'error_type':str(error_type)[:80],'original_evidence_retained':bool(original_evidence_retained)})
        except Exception:pass

    def retain_received(self,size):
        try:return self.consume(retained_output_bytes=size)
        except Exception as error:
            # Saving already-arriving original evidence must survive quota
            # bookkeeping failure. Seal future generation with a sticky marker.
            self.mark_unconfirmed(type(error).__name__)
            return None

    def inspect(self):
        output_gap=self._output_gap_present()
        with self._lock(create=False):v=self._read()
        return {'kind':'ORIGINAL_WORK_RESOURCE_USAGE','resource_ref':{'id':self.id,'revision':v['revision'],'sha256':digest(v)},
            'authority':deepcopy(self.authority),'limits':deepcopy(self.effective_limits(v)),'allocation_limits':deepcopy(self.limits),'used':deepcopy(v['used']),
            'allowance_adjustments':len(v.get('adjustments',[])),'adjustment_capacity':MAX_ADJUSTMENTS,
            'pending_read_bytes':sum(r['bytes'] for r in v['pending_reads'].values()),'pending_reads':len(v['pending_reads']),
            'accounting_unconfirmed':self.accounting_unconfirmed or self.gap.exists() or output_gap,
            'retention_threshold_exceeded':v['used']['retained_output_bytes']>=self.effective_limits(v)['retained_output_bytes'],
            'usage_coverage':'SINCE_ALLOCATION_NOT_RETROACTIVE','counters_include_unconfirmed_reservations':True,
            'model_reservations_are_provider_usage':False,'physical_disk_io':'NOT_MEASURED','read_only':True,'metadata_writes':0}


def current_task_resource_budget():return _CURRENT.get()


@contextmanager
def task_resource_scope(budget):
    current=_CURRENT.get()
    if current is not None:
        if current.id!=budget.id or current.path!=budget.path:raise Conflict('Nested task resource scope cannot replace the original work')
        yield current;return
    token=_CURRENT.set(budget)
    try:yield budget
    finally:_CURRENT.reset(token)
