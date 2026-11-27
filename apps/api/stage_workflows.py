"""Same-origin engineering workflow API using canonical project/conversation scope."""
import hashlib
from tempfile import SpooledTemporaryFile
from typing import Literal
from urllib.parse import quote
from uuid import uuid4
from zipfile import ZipFile

from fastapi import File, Form, Query, Response, UploadFile
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import Field, StrictInt

from apps.api.conversation_creation import CreateConversation
from packages.domain.document_import import MAX_DOCUMENT_BYTES
from packages.domain.questions import RequestModel
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.dynamic_workflow.stage_delivery import receipt_bytes
from packages.dynamic_workflow.stage_runs import StageRuns


DELIVERY_CHUNK_BYTES = 1024**2


def _delivery_archive(owner, project_id, conversation_id, ident, node_id, approval_sha256):
    stream = SpooledTemporaryFile(max_size=4*1024**2, dir=owner.data_dir)
    try:
        with ZipFile(stream,'w') as archive:
            owner.delivery_receipt(project_id,conversation_id,ident,node_id,approval_sha256,archive=archive)
        return stream
    except OSError as exc:
        stream.close()
        raise Conflict('交付包暂时无法保存到本机，请检查剩余空间后重试。') from exc
    except BaseException:
        stream.close()
        raise


class DeliveryArchiveResponse(StreamingResponse):
    def __init__(self, stream, approval):
        self.stream = stream
        length = stream.tell()
        stream.seek(0)
        super().__init__(iter(lambda: stream.read(DELIVERY_CHUNK_BYTES), b''), media_type='application/zip', headers={
            'Content-Disposition':f'attachment; filename="cirven-delivery-{approval[:12]}.zip"',
            'Content-Length':str(length), 'Cache-Control':'no-store',
            'X-Cirven-Delivery-Approval':approval,
        })

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            self.stream.close()


class Operation(RequestModel):
    project_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    conversation_id: str = Field(pattern=r'^(native|original|discovery):[0-9a-f]{64}$')
    operation_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')


class Define(Operation):
    definition: dict
    expected_revision: StrictInt = Field(ge=0)


class Start(Operation):
    definition_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    expected_definition_revision: StrictInt | None = Field(default=None, ge=1)

class Improvement(Operation):
    approval_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')
    source_field: Literal['lessons','plugin_suggestions']
    source_index: StrictInt = Field(ge=0)
    target_node_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    category: Literal['skills','tools','mcp']
    change: Literal['add','remove']
    capability_id: str = Field(min_length=1,max_length=300)
    capability_sha256: str | None = Field(default=None,pattern=r'^[a-f0-9]{64}$')
    mode: Literal['copy','update']
    copy_name: str | None = Field(default=None,max_length=100)


class Action(Operation):
    node_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    expected_revision: StrictInt = Field(ge=1)
    action: Literal['configure','note','annotate','approve','execute','stop','recover','rework','supplement','withdraw_supplement','revise','record_manual_check']
    payload: dict = Field(default_factory=dict)

class Dialogue(Operation):
    node_id: str = Field(pattern=r'^[a-zA-Z0-9_-]{1,100}$')
    question: str = Field(min_length=1,max_length=4000)
    annotation_id: str | None = Field(default=None,pattern=r'^ann_[a-f0-9]{32}$')
    follow_up_id: str | None = Field(default=None,pattern=r'^[a-zA-Z0-9_-]{1,100}$')


def register_stage_workflows(app):
    def create_child(db, pid, title):
        return app.state.conversation_creation.create_in(db,CreateConversation(project_id=pid,
            operation_id='stage_child_'+uuid4().hex,title=title))

    def execute(context):
        from packages.dynamic_workflow.stage_kernel import execute_stage
        return execute_stage(context)

    owner = app.state.stage_workflows = StageRuns(app.state.store,app.state.settings.data_dir,create_child,execute)
    def answer(context):
        from packages.dynamic_workflow.stage_kernel import answer_stage_question
        return answer_stage_question(context)
    owner.dialogue_executor = answer
    root = '/api/stage-workflows'

    @app.get(root)
    def catalog(project_id: str, conversation_id: str):
        return owner.list(project_id,conversation_id)

    @app.post(root+'/definitions')
    def define(body: Define):
        return owner.define(body.project_id,body.conversation_id,body.operation_id,
                              body.expected_revision,body.definition)

    @app.get(root+'/definitions/{ident}/improvements')
    def definition_improvements(ident: str, project_id: str, conversation_id: str,
                                revision: int = Query(ge=1,le=2**53-1),
                                before: int | None = Query(default=None,ge=1,le=2**53-1)):
        return owner.definition_improvements(project_id,conversation_id,ident,revision,before=before)

    @app.get(root+'/definitions/{ident}/improvements/{operation}')
    def definition_improvement_source(ident: str, operation: str, project_id: str, conversation_id: str,
                                      revision: int = Query(ge=1,le=2**53-1)):
        return owner.definition_improvements(project_id,conversation_id,ident,revision,operation=operation)

    @app.post(root+'/runs')
    def start(body: Start):
        return owner.start(body.project_id,body.conversation_id,body.operation_id,
                             body.definition_id,body.expected_definition_revision)

    @app.get(root+'/runs/{ident}')
    def get(ident: str, project_id: str, conversation_id: str):
        return owner.get(project_id,conversation_id,ident)

    @app.get(root+'/transition-receipts/{operation_id}')
    def transition_receipt(operation_id: str, project_id: str, conversation_id: str):
        return owner.transition_receipt(project_id,conversation_id,operation_id)

    @app.get(root+'/runs/{ident}/stages/{node_id}/planning-source')
    def planning_source(ident: str, node_id: str, project_id: str, conversation_id: str,
                        operation_id: str = Query(pattern=r'^[a-zA-Z0-9_-]{1,100}$')):
        return owner.planning_source(project_id,conversation_id,ident,node_id,operation_id)

    @app.get(root+'/runs/{ident}/stages/{node_id}/rework-preview')
    def rework_preview(ident: str, node_id: str, project_id: str, conversation_id: str):
        return owner.rework_preview(project_id,conversation_id,ident,node_id)

    @app.get(root+'/runs/{ident}/stages/{node_id}/bench-firmware')
    def bench_firmware(ident: str, node_id: str, project_id: str, conversation_id: str,
                       case_id: str | None = Query(default=None,min_length=1,max_length=100),
                       action_id: str | None = Query(default=None,min_length=1,max_length=100)):
        if (case_id is None) != (action_id is None):
            raise InvalidInput('请同时指定用例和实测动作')
        return owner.bench_firmware(project_id,conversation_id,ident,node_id,
                                    (case_id,action_id) if case_id is not None else None)

    @app.get(root+'/runs/{ident}/stages/{node_id}/improvements')
    def improvements(ident: str,node_id: str,project_id: str,conversation_id: str,
                     approval_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return owner.improvements(project_id,conversation_id,ident,node_id,approval_sha256)

    @app.post(root+'/runs/{ident}/stages/{node_id}/improvements')
    def improve(ident: str,node_id: str,body: Improvement):
        request=body.model_dump(exclude={'operation_id','project_id','conversation_id'})
        return owner.apply_improvement(body.project_id,body.conversation_id,ident,node_id,body.operation_id,request)

    @app.get(root+'/runs/{ident}/stages/{node_id}/revision-difference')
    def revision_difference(ident: str, node_id: str, project_id: str, conversation_id: str,
                            revision: int = Query(ge=1,le=2**53-1),
                            group: Literal['artifact','row'] | None = None,
                            row_id: str | None = Query(default=None,min_length=1,max_length=300),
                            offset: int = Query(default=0,ge=0,le=2**53-1)):
        if (group=='row')!=(row_id is not None) or (group is not None and offset):
            raise InvalidInput('对比请求的条目或分页条件无效')
        return owner.revision_difference(project_id,conversation_id,ident,node_id,revision,
                                         group=group,row_id=row_id,offset=offset)

    @app.get(root+'/runs/{ident}/coverage-overview')
    def coverage_overview(ident: str, project_id: str, conversation_id: str):
        return owner.coverage_overview(project_id,conversation_id,ident)

    @app.get(root+'/runs/{ident}/stages/{node_id}/coverage')
    def coverage(ident: str, node_id: str, project_id: str, conversation_id: str,
                 selector: Literal['ur','or'], row_id: str = Query(min_length=1,max_length=300)):
        return owner.coverage(project_id,conversation_id,ident,node_id,selector,row_id)

    @app.get(root+'/runs/{ident}/stages/{node_id}/rework-evidence')
    def rework_evidence(ident: str,node_id: str,project_id: str,conversation_id: str,
                        revision: int | None = Query(default=None,ge=1,le=2**53-1),
                        offset: int = Query(default=0,ge=0,le=2**53-1)):
        return owner.rework_evidence(project_id,conversation_id,ident,node_id,revision,offset)

    @app.get(root+'/runs/{ident}/stages/{node_id}/coverage-record')
    def coverage_record(ident: str, node_id: str, project_id: str, conversation_id: str,
                        row_id: str = Query(min_length=1,max_length=300), operation_id: str | None = None):
        return owner.coverage_record(project_id,conversation_id,ident,node_id,operation_id,row_id)

    @app.get(root+'/runs/{ident}/stages/{node_id}/attempts')
    def attempts(ident: str,node_id: str,project_id: str,conversation_id: str,operation_id: str | None=None):
        return owner.attempts(project_id,conversation_id,ident,node_id,operation_id)

    @app.get(root+'/runs/{ident}/stages/{node_id}/execution-record')
    def execution_record(ident: str,node_id: str,project_id: str,conversation_id: str,
                         operation_id: str = Query(pattern=r'^[a-zA-Z0-9_-]{1,100}$')):
        return owner.execution_record(project_id,conversation_id,ident,node_id,operation_id)

    @app.post(root+'/runs/{ident}/actions')
    def action(ident: str, body: Action):
        return owner.action(body.project_id,body.conversation_id,ident,body.operation_id,
                              body.expected_revision,body.node_id,body.action,body.payload)

    @app.post(root+'/runs/{ident}/dialogue')
    def dialogue(ident: str, body: Dialogue):
        return owner.ask(body.project_id,body.conversation_id,ident,body.node_id,
                         body.operation_id,body.question,body.annotation_id,body.follow_up_id)

    def file_attachment(name, data):
        return Response(data,media_type='application/octet-stream',
            headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(name,safe=''),
                     'Cache-Control':'no-store','X-Content-Type-Options':'nosniff'})

    def download_record(record):
        from packages.dynamic_workflow.stage_workspace import read_evidence_file
        try:
            return file_attachment(*read_evidence_file(app.state.settings.data_dir,record))
        except (OSError,ValueError,RuntimeError):
            raise NotFound('找不到回执所对应的文件版本') from None

    @app.get(root+'/runs/{ident}/files/{evidence_id}')
    def download(ident: str,evidence_id: str,project_id: str,conversation_id: str):
        return download_record(owner.file_record(project_id,conversation_id,ident,evidence_id))

    @app.get(root+'/runs/{ident}/stages/{node_id}/manual-files/{receipt_id}/{file_id}')
    def manual_file(ident: str,node_id: str,receipt_id: str,file_id: str,project_id: str,conversation_id: str):
        return file_attachment(*owner.manual_file(project_id,conversation_id,ident,node_id,receipt_id,file_id))

    @app.get(root+'/runs/{ident}/stages/{node_id}/template-export')
    def template_export(ident: str,node_id: str,project_id: str,conversation_id: str,stage_session_id: str,
                        approval_sha256: str,format: Literal['template','text']='template'):
        from packages.dynamic_workflow.stage_template_export import export_stage_template,original_bytes
        run=owner.get(project_id,conversation_id,ident)
        stage=next((s for s in run['stages'] if s['node_id']==node_id and s['stage_session_id']==stage_session_id),None)
        if stage is None and conversation_id==stage_session_id:
            snapshot=owner.list(project_id,conversation_id)
            if snapshot.get('historical_node_id')==node_id:
                stage=snapshot.get('historical_stage')
        if (not stage or stage['status']!='approved' or stage.get('approval_sha256')!=approval_sha256 or
                stage.get('stage_session_id')!=stage_session_id or not stage.get('template')):
            raise NotFound('找不到所选节点这一版已审核的模板成果')
        template=stage['template'];raw=None
        if template.get('export_extension') not in {None, '.txt'} and format!='text':
            with owner.store.connect() as db:
                raw=original_bytes(db,project_id,run['conversation_id'],template)
        try:
            filename,media,data=export_stage_template(template,stage['artifact'],raw,plain=(format=='text'))
        except (ValueError,TypeError,KeyError) as exc:
            raise InvalidInput(str(exc)) from None
        return Response(data,media_type=media,headers={'Content-Disposition':"attachment; filename*=UTF-8''"+quote(filename,safe='')})

    @app.get(root+'/runs/{ident}/stages/{node_id}/delivery-receipt')
    def delivery_receipt(ident: str,node_id: str,project_id: str,conversation_id: str,
                         approval_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        receipt = owner.delivery_receipt(project_id,conversation_id,ident,node_id,approval_sha256)
        filename = f'cirven-delivery-{approval_sha256[:12]}.json'
        return Response(receipt_bytes(receipt),media_type='application/json',
                        headers={'Content-Disposition':f'attachment; filename="{filename}"',
                                 'Cache-Control':'no-store'})

    @app.get(root+'/runs/{ident}/stages/{node_id}/delivery-bundle')
    def delivery_bundle(ident: str,node_id: str,project_id: str,conversation_id: str,
                        approval_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        stream = _delivery_archive(owner,project_id,conversation_id,ident,node_id,approval_sha256)
        try:
            return DeliveryArchiveResponse(stream,approval_sha256)
        except BaseException:
            stream.close()
            raise

    @app.get(root+'/runs/{ident}/stages/{node_id}/delivery-bundle-reference')
    def delivery_bundle_reference(ident: str,node_id: str,project_id: str,conversation_id: str,
                                  approval_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        with _delivery_archive(owner,project_id,conversation_id,ident,node_id,approval_sha256) as stream:
            size = stream.tell()
            stream.seek(0)
            chunks = [hashlib.sha256(chunk).hexdigest()
                      for chunk in iter(lambda: stream.read(DELIVERY_CHUNK_BYTES),b'')]
        return JSONResponse({'run_id':ident, 'approval_sha256':approval_sha256, 'bytes':size,
                             'chunk_bytes':DELIVERY_CHUNK_BYTES, 'sha256_chunks':chunks},
                            headers={'Cache-Control':'no-store'})

    @app.get(root+'/runs/{ident}/stages/{node_id}/delivery-file')
    def delivery_file(ident: str,node_id: str,project_id: str,conversation_id: str,
                      approval_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
                      item_id: str = Query(min_length=1,max_length=300)):
        return download_record(owner.delivery_file_record(project_id,conversation_id,ident,node_id,approval_sha256,item_id))

    @app.post(root+'/template')
    async def template(project_id: str=Form(),conversation_id: str=Form(),file: UploadFile=File()):
        from packages.dynamic_workflow.stage_templates import parse_stage_template
        from packages.dynamic_workflow.stage_template_export import describe_template
        with owner.store.connect() as db:
            owner._scope(db,project_id,conversation_id,write=True)
        try:
            content = await file.read(MAX_DOCUMENT_BYTES+1)
            parsed = describe_template(parse_stage_template(file.filename or '',content),content)
        except ValueError as exc:
            raise InvalidInput(str(exc)) from None
        finally:
            await file.close()
        return owner.save_template(project_id,conversation_id,parsed,raw=content)
