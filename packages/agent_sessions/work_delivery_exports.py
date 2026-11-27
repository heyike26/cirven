"""Original native Save As journal for an already completed delivery.

Only the trusted desktop host supplies the native-dialog path. No new package,
build, model, execution grant, overwrite, deletion or automatic file replay.
"""
import json
from typing import Literal
from pydantic import Field

from packages.adapters.delivery_export_files import DeliveryExportFiles, ExportWriteFailure, MAX_EXPORT_BYTES
from packages.domain.context_read_budget import context_read_scope, current_context_read_budget
from packages.domain.local_workspaces import canonical, digest
from packages.domain.questions import Id, Hash, stamp
from packages.domain.store import Conflict, InvalidInput, NotFound
from . import work_requirements as requirements
from .work_deliveries import WorkDeliveries, WorkDeliveryLookup

MAX_EXPORTS = 512
MAX_PER_DELIVERY = 16
MAX_RESPONSE = 65536


class WorkDeliveryExportRequest(requirements.WorkRequirementScope):
    operation_id: Id
    root_sha256: Hash
    delivery_operation_id: Id
    delivery_request_sha256: Hash
    manifest_sha256: Hash
    artifact_sha256: Hash
    artifact_byte_length: int = Field(ge=1, le=MAX_EXPORT_BYTES, strict=True)


class WorkDeliveryExportLookup(requirements.WorkRequirementOperation): pass


class WorkDeliveryExportSave(WorkDeliveryExportLookup):
    destination: str = Field(min_length=1, max_length=4096)


class WorkDeliveryExportCancel(WorkDeliveryExportLookup):
    reason: Literal['USER_CANCELLED', 'REQUEST_ABORTED']


class WorkDeliveryExportInspect(WorkDeliveryExportLookup):
    observe_output: bool = Field(default=False, strict=True)


class WorkDeliveryExportList(requirements.WorkRequirementScope):
    offset: int = Field(default=0, ge=0, le=MAX_EXPORTS, strict=True)


def _bounded(value, limit):
    if len(canonical(value).encode()) > limit: raise Conflict('Original export metadata exceeds its bound')
    return value


def _checked(raw, checksum, limit):
    if not isinstance(raw, str) or len(raw.encode()) > limit: raise Conflict('Original export record exceeds its bound')
    budget=current_context_read_budget()
    if budget is not None:budget.charge(len(raw.encode()))
    value = json.loads(raw)
    if digest(value) != checksum: raise Conflict('Original export record integrity differs')
    return value


def _event_limit(kind):
    if kind=='DESTINATION':return 65536
    if kind=='CHOICE':return 32768
    if kind in {'CREATED','RESULT','RECOVERY'}:return 16384
    raise Conflict('Original export event kind differs')


def _flags():
    return {'model_calls':0, 'commands_started':0, 'source_files_written':0,
        'new_delivery_created':False, 'automatic_file_replay':False, 'overwrite_performed':False,
        'permission_granted':False, 'deployment_performed':False, 'restore_performed':False,
        'whole_task_acceptance':'NOT_ASSESSED', 'target_environment_verified':False,
        'contents_writer_attribution':'NOT_PROVEN'}


class WorkDeliveryExports:
    def __init__(self, sessions, *, files=None):
        self.sessions, self.store = sessions, sessions.store
        self.deliveries = WorkDeliveries(sessions)
        self.files = files if files is not None else DeliveryExportFiles()

    def _lock(self, pid, sid, op):
        return self.deliveries.materials._lock(self.sessions._path(pid,sid,'work-export-locks',digest(op)), blocking=False)

    def _intent(self, db, pid, sid, op):
        row = db.execute('''SELECT * FROM original_delivery_export_intents
            WHERE project_id=? AND session_id=? AND operation_id=?''', (pid,sid,op)).fetchone()
        if row is None: raise NotFound('Original delivery export does not exist')
        request = _checked(row['request_json'],row['request_sha256'],8192)
        body = WorkDeliveryExportRequest.model_validate(request)
        descriptor = _checked(row['descriptor_json'],row['descriptor_sha256'],16384)
        if (body.model_dump()!=request or body.operation_id!=op or body.session_ref.id!=sid
                or row['root_operation_id']!=body.root_operation_id or row['delivery_operation_id']!=body.delivery_operation_id
                or descriptor.get('project_id')!=pid or descriptor.get('root_sha256')!=body.root_sha256
                or descriptor.get('operation_id')!=body.delivery_operation_id
                or descriptor.get('request_sha256')!=body.delivery_request_sha256
                or descriptor.get('manifest_sha256')!=body.manifest_sha256
                or descriptor.get('artifact',{}).get('sha256')!=body.artifact_sha256
                or descriptor.get('artifact',{}).get('byte_length')!=body.artifact_byte_length):
            raise Conflict('Original export request and immutable delivery descriptor differ')
        return {'body':body,'request_sha256':row['request_sha256'],'descriptor':descriptor,
            'descriptor_sha256':row['descriptor_sha256'],'origin_client_id':row['origin_client_id'],'created_at':row['created_at']}

    def _events(self, db, pid, sid, op):
        rows = db.execute('''SELECT kind,event_json,event_sha256 FROM original_delivery_export_events
            WHERE project_id=? AND session_id=? AND operation_id=?''', (pid,sid,op)).fetchall()
        events = {r['kind']:_checked(r['event_json'],r['event_sha256'],_event_limit(r['kind'])) for r in rows}
        if (set(events)-{'CHOICE','DESTINATION','CREATED','RESULT','RECOVERY'}
                or 'DESTINATION' in events and 'CHOICE' not in events
                or 'CREATED' in events and 'DESTINATION' not in events
                or 'RECOVERY' in events and ('CREATED' not in events or 'RESULT' not in events)):
            raise Conflict('Original export event lineage differs')
        if 'CHOICE' in events:
            chosen=events['CHOICE'].get('destination')
            if not isinstance(chosen,str) or not 1<=len(chosen)<=4096:raise Conflict('Original native save choice differs')
            if 'DESTINATION' in events and not self.files.same_destination(events['DESTINATION'],chosen):
                raise Conflict('Original native save choice and captured destination differ')
        if 'CREATED' in events and events['CREATED'].get('destination_sha256')!=digest(events['DESTINATION']):
            raise Conflict('Original created file proof belongs to another destination')
        for key in ['RESULT','RECOVERY']:
            if key in events:
                e=events[key]
                if e.get('status') not in {'SAVED','CANCELLED','NOT_WRITTEN','UNKNOWN'}:
                    raise Conflict('Original export result kind differs')
                if e['status']=='SAVED' and ('CREATED' not in events or e.get('destination_sha256')!=digest(events['DESTINATION'])
                        or e.get('file_identity')!=events['CREATED'].get('file_identity')):
                    raise Conflict('Saved export receipt lacks its original creation proof')
        return events

    def _append(self, pid, sid, op, kind, value):
        _bounded(value,_event_limit(kind))
        with self.store.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            current=self._events(db,pid,sid,op)
            if kind in current:
                if current[kind]!=value: raise Conflict('Original export event is already bound to different content')
                return
            if kind=='CREATED' and ('DESTINATION' not in current or 'RESULT' in current):
                raise Conflict('Original export creation phase differs')
            if (kind=='CHOICE' and current or kind=='DESTINATION' and set(current)!={'CHOICE'}
                    or kind=='RECOVERY' and ('RESULT' not in current or 'CREATED' not in current)):
                raise Conflict('Original export event order differs')
            db.execute('''INSERT INTO original_delivery_export_events VALUES (?,?,?,?,?,?)''',
                (pid,sid,op,kind,canonical(value),digest(value)))

    def _scope(self, pid, sid, body, intent=None, *, positive=False):
        root = requirements._root(self.sessions,pid,sid,body)
        if intent:
            r=intent['body']
            if (body.root_operation_id!=r.root_operation_id or body.session_ref!=r.session_ref or body.workspace_ref!=r.workspace_ref
                    or body.generation<r.generation or positive and body.generation!=r.generation
                    or root['sha256']!=r.root_sha256 or body.request_sha256!=intent['request_sha256']):
                raise Conflict('Original export scope or request checksum changed')
        return root

    @staticmethod
    def _lookup(body):
        return WorkDeliveryExportLookup.model_validate(body.model_dump(exclude={'destination','reason','observe_output'}))

    def _response(self, pid, sid, body, intent, events, *, claimed=False, observation=None):
        for key in ['RESULT','RECOVERY']:
            proof=events.get(key,{})
            if proof.get('status')=='SAVED' and (proof.get('sha256')!=intent['body'].artifact_sha256
                    or proof.get('byte_length')!=intent['body'].artifact_byte_length):
                raise Conflict('Saved export proof differs from the original delivery archive')
        current=events.get('RECOVERY',events.get('RESULT'))
        if current is not None: status=current['status']
        elif 'CREATED' in events: status='UNKNOWN'
        elif 'DESTINATION' in events: status='DESTINATION_RECORDED'
        elif 'CHOICE' in events: status='DESTINATION_CHOICE_RECORDED'
        else: status='WAITING_FOR_DESTINATION'
        result={'kind':'ORIGINAL_DELIVERY_EXPORT','version':1,'project_id':pid,
            'scope':body.model_dump(), 'query_sha256':digest(body.model_dump()),
            'operation_id':intent['body'].operation_id,'request_sha256':intent['request_sha256'],
            'original_request':intent['body'].model_dump(), 'delivery':intent['descriptor'],
            'delivery_descriptor_sha256':intent['descriptor_sha256'],'created_at':intent['created_at'],
            'status':status,'events':events,'events_sha256':digest(events),'current_observation':observation,
            'current_target_state':observation['status'] if observation is not None else 'NOT_CHECKED',
            'new_intent_claimed':claimed,'native_dialog_allowed':claimed,
            'read_only_target':True,'current_call_target_writes':0,
            'original_exported_files_created':1 if 'CREATED' in events else events.get('RESULT',{}).get(
                'exported_files_created',None if 'DESTINATION' in events else 0),
            **_flags()}
        return _bounded(result,MAX_RESPONSE)

    def begin(self, pid, sid, body, *, client_id):
        body=WorkDeliveryExportRequest.model_validate(body);request=body.model_dump();_bounded(request,8192)
        if not isinstance(client_id,str) or not 1<=len(client_id)<=100 or any(c not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for c in client_id):
            raise InvalidInput('An exact originating paired client is required')
        root=self._scope(pid,sid,body)
        if root['sha256']!=body.root_sha256: raise Conflict('Original export root changed')
        query=WorkDeliveryExportLookup.model_validate({**{k:request[k] for k in ('session_ref','generation','workspace_ref','root_operation_id')},
            'operation_id':body.operation_id,'request_sha256':digest(request)})
        with self._lock(pid,sid,body.operation_id),context_read_scope():
            with self.store.connect() as db:
                try: old=self._intent(db,pid,sid,body.operation_id)
                except NotFound: old=None
                if old:
                    self._scope(pid,sid,query,old)
                    return self._response(pid,sid,query,old,self._events(db,pid,sid,body.operation_id))
            receipt=self.deliveries.operation(pid,sid,WorkDeliveryLookup.model_validate({
                **{k:request[k] for k in ('session_ref','generation','workspace_ref','root_operation_id')},
                'operation_id':body.delivery_operation_id,'request_sha256':body.delivery_request_sha256}))
            if (receipt.get('kind')!='ORIGINAL_WORK_DELIVERY' or receipt['root_sha256']!=body.root_sha256
                    or receipt['manifest_sha256']!=body.manifest_sha256 or receipt['artifact']['sha256']!=body.artifact_sha256
                    or receipt['artifact']['byte_length']!=body.artifact_byte_length):
                raise Conflict('Native export requires the exact completed original delivery')
            descriptor={k:receipt[k] for k in ['project_id','operation_id','request_sha256','root_sha256','manifest_sha256','revision','version_label','artifact']}
            with self.store.connect() as db:
                db.execute('BEGIN IMMEDIATE')
                total=db.execute('SELECT COUNT(*) FROM original_delivery_export_intents').fetchone()[0]
                count=db.execute('''SELECT COUNT(*) FROM original_delivery_export_intents
                    WHERE project_id=? AND session_id=? AND delivery_operation_id=?''',(pid,sid,body.delivery_operation_id)).fetchone()[0]
                if total>=MAX_EXPORTS or count>=MAX_PER_DELIVERY: raise Conflict('Native export journal capacity reached; original records are retained')
                requirements._unchanged(self.sessions,pid,sid,body,root)
                db.execute('''INSERT INTO original_delivery_export_intents VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (pid,sid,body.operation_id,body.root_operation_id,body.delivery_operation_id,canonical(request),digest(request),
                        canonical(descriptor),digest(descriptor),client_id,stamp()))
                intent=self._intent(db,pid,sid,body.operation_id)
            return self._response(pid,sid,query,intent,{},claimed=True)

    def operation(self, pid, sid, body):
        body=WorkDeliveryExportInspect.model_validate(body);query=self._lookup(body)
        with context_read_scope(),self.store.connect() as db:
            intent=self._intent(db,pid,sid,body.operation_id);root=self._scope(pid,sid,query,intent)
            events=self._events(db,pid,sid,body.operation_id)
            observation=None
            if body.observe_output:
                if 'CREATED' not in events: observation={'status':'NO_ORIGINAL_CREATION_PROOF','files_read':0}
                else:
                    try: observation=self.files.observe(events['DESTINATION'],events['CREATED'],
                        intent['body'].artifact_sha256,intent['body'].artifact_byte_length)
                    except (OSError,Conflict,InvalidInput,NotFound): observation={'status':'UNAVAILABLE','files_read':None,'read_attempted':True}
            requirements._unchanged(self.sessions,pid,sid,query,root)
            return self._response(pid,sid,query,intent,events,observation=observation)

    def cancel(self, pid, sid, body):
        body=WorkDeliveryExportCancel.model_validate(body);query=self._lookup(body)
        with self._lock(pid,sid,body.operation_id):
            with self.store.connect() as db:
                intent=self._intent(db,pid,sid,body.operation_id);self._scope(pid,sid,query,intent)
                events=self._events(db,pid,sid,body.operation_id)
            if set(events)-{'CHOICE'}:return self._response(pid,sid,query,intent,events)
            self._append(pid,sid,body.operation_id,'RESULT',{'status':'CANCELLED','reason':body.reason,
                'recorded_at':stamp(),'exported_files_created':0,'file_write_invoked':False})
        return self.operation(pid,sid,{**query.model_dump(),'observe_output':False})

    def save(self, pid, sid, body):
        body=WorkDeliveryExportSave.model_validate(body);query=self._lookup(body)
        with self._lock(pid,sid,body.operation_id),context_read_scope():
            with self.store.connect() as db:
                intent=self._intent(db,pid,sid,body.operation_id);self._scope(pid,sid,query,intent)
                events=self._events(db,pid,sid,body.operation_id)
            # Even a lost pre-write reply never causes an automatic second create.
            if events:
                if 'DESTINATION' in events and not self.files.same_destination(events['DESTINATION'],body.destination):
                    raise Conflict('IDEMPOTENCY_CONFLICT: original export destination is already fixed')
                if 'CHOICE' in events and events['CHOICE']['destination']!=body.destination:
                    raise Conflict('IDEMPOTENCY_CONFLICT: original native save choice is already fixed')
                return self._response(pid,sid,query,intent,events)
            self._scope(pid,sid,query,intent,positive=True)
            invoked=False
            try:
                self._append(pid,sid,body.operation_id,'CHOICE',{'destination':body.destination,'chosen_at':stamp()})
                destination=self.files.capture(body.destination)
                self._append(pid,sid,body.operation_id,'DESTINATION',destination)
                raw,receipt=self.deliveries.download(pid,sid,intent['body'].root_operation_id,
                    intent['body'].delivery_operation_id,intent['body'].delivery_request_sha256)
                if (receipt['artifact']['sha256']!=intent['body'].artifact_sha256
                        or len(raw)!=intent['body'].artifact_byte_length): raise Conflict('Original archive bytes differ')
                self._scope(pid,sid,query,intent,positive=True)
                def created(proof): self._append(pid,sid,body.operation_id,'CREATED',proof)
                def verified(proof): self._append(pid,sid,body.operation_id,'RESULT',{'status':'SAVED',**proof,
                    'recorded_at':stamp(),'exported_files_created':1,'file_write_invoked':True})
                invoked=True
                self.files.create(destination,raw,record_created=created,record_verified=verified)
            except Exception as error:
                with self.store.connect() as db: events=self._events(db,pid,sid,body.operation_id)
                if 'RESULT' not in events:
                    effect=error.effect if isinstance(error,ExportWriteFailure) else 'UNKNOWN' if invoked else 'NOT_INVOKED'
                    self._append(pid,sid,body.operation_id,'RESULT',{'status':'NOT_WRITTEN' if effect in {'NOT_INVOKED','EXISTING_FILE_UNTOUCHED'} else 'UNKNOWN',
                        'effect':effect,'error_type':type(error).__name__,'recorded_at':stamp(),
                        'exported_files_created':0 if effect in {'NOT_INVOKED','EXISTING_FILE_UNTOUCHED'} else None,
                        'file_write_invoked':invoked,'partial_files_preserved':True})
        result=self.operation(pid,sid,{**query.model_dump(),'observe_output':False})
        if invoked:
            result['current_call_target_writes']=1 if result['status']=='SAVED' else 0 if result['status']=='NOT_WRITTEN' else None
            result['read_only_target']=result['current_call_target_writes']==0
            result['target_create_attempted']=True
        return result

    def reconcile(self, pid, sid, body):
        query=WorkDeliveryExportLookup.model_validate(body)
        with self._lock(pid,sid,query.operation_id),context_read_scope():
            with self.store.connect() as db:
                intent=self._intent(db,pid,sid,query.operation_id);root=self._scope(pid,sid,query,intent)
                events=self._events(db,pid,sid,query.operation_id)
            if events.get('RESULT',{}).get('status')=='SAVED' or 'RECOVERY' in events:
                return self._response(pid,sid,query,intent,events)
            if 'CREATED' not in events: raise Conflict('No original file creation proof exists; preserve the unresolved export')
            proof=self.files.observe(events['DESTINATION'],events['CREATED'],intent['body'].artifact_sha256,intent['body'].artifact_byte_length)
            if proof.get('status')!='MATCHING_CAPTURED_OUTPUT' or not proof.get('birth_identity_available'):
                raise Conflict('Original output cannot be attributed to its captured identity for metadata recovery')
            requirements._unchanged(self.sessions,pid,sid,query,root)
            if 'RESULT' not in events:self._append(pid,sid,query.operation_id,'RESULT',{'status':'UNKNOWN','recorded_at':stamp(),
                'effect':'INTERRUPTED_WITH_CREATION_PROOF','exported_files_created':None,'file_write_invoked':True,'partial_files_preserved':True})
            self._append(pid,sid,query.operation_id,'RECOVERY',{'status':'SAVED','destination_sha256':digest(events['DESTINATION']),
                'file_identity':proof['file_identity'],'sha256':proof['sha256'],'byte_length':proof['byte_length'],
                'verified_at':proof['observed_at'],'recorded_at':stamp(),'metadata_recovery_only':True,
                'new_file_writes':0,'contents_writer_attribution':'NOT_PROVEN'})
        return self.operation(pid,sid,{**query.model_dump(),'observe_output':False})

    def listing(self,pid,sid,body):
        with context_read_scope():return self._listing(pid,sid,body)

    def _listing(self,pid,sid,body):
        body=WorkDeliveryExportList.model_validate(body);root=self._scope(pid,sid,body)
        with self.store.connect() as db:
            rows=db.execute('''SELECT operation_id FROM original_delivery_export_intents
                WHERE project_id=? AND session_id=? AND root_operation_id=? ORDER BY created_at,operation_id LIMIT ? OFFSET ?''',
                (pid,sid,body.root_operation_id,11,body.offset)).fetchall()
            entries=[]
            for row in rows[:10]:
                intent=self._intent(db,pid,sid,row['operation_id']);events=self._events(db,pid,sid,row['operation_id'])
                if intent['body'].root_sha256!=root['sha256']:raise Conflict('Original export history root differs')
                entries.append({'operation_id':row['operation_id'],'request_sha256':intent['request_sha256'],
                    'delivery_operation_id':intent['body'].delivery_operation_id,'delivery_request_sha256':intent['body'].delivery_request_sha256,
                    'version_label':intent['descriptor']['version_label'],'created_at':intent['created_at'],
                    'status':events.get('RECOVERY',events.get('RESULT',{})).get('status','UNKNOWN'),
                    'destination_name':events.get('DESTINATION',{}).get('name')})
        requirements._unchanged(self.sessions,pid,sid,body,root)
        return _bounded({'kind':'ORIGINAL_DELIVERY_EXPORT_HISTORY','version':1,'project_id':pid,
            'scope':body.model_dump(exclude={'offset'}),'query_sha256':digest(body.model_dump()),'root_sha256':root['sha256'],
            'entries':entries,'next_offset':body.offset+10 if len(rows)>10 else None,'read_only':True,**_flags()},MAX_RESPONSE)
