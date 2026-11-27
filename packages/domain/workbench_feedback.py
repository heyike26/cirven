"""Immutable annotations; recording and native submission are separate actions."""
import json
import uuid
from typing import Literal,Annotated
from pydantic import Field,model_validator
from packages.domain.questions import RequestModel,Id,Hash,stamp,QuestionAnchor
from packages.domain.discovery_schema import DiscoveryCandidate,DiscoveryAdvance,RevisionRef
from packages.domain.intake import canonical,digest
from packages.domain.planning_proposals import raw_hash
from packages.domain.local_workspaces import FileSource
from packages.domain.store import Conflict,InvalidInput,NotFound


class ArtifactRef(RequestModel):
    kind: Literal['DISCOVERY']
    id: Id
    revision: int = Field(ge=1,strict=True)
    sha256: Hash


class Point(RequestModel):
    x: float = Field(ge=0,le=1,allow_inf_nan=False)
    y: float = Field(ge=0,le=1,allow_inf_nan=False)


class Pan(RequestModel):
    x: float = Field(allow_inf_nan=False)
    y: float = Field(allow_inf_nan=False)


class Viewport(RequestModel):
    width: int = Field(ge=1,le=32768,strict=True)
    height: int = Field(ge=1,le=32768,strict=True)
    zoom: float = Field(gt=0,le=100,allow_inf_nan=False)
    pan: Pan


class CandidateTextLocator(RequestModel):
    kind: Literal['CANDIDATE_FIELD']
    field_pointer: str = Field(min_length=1,max_length=500,pattern=r'^/')
    char_start: int = Field(ge=0,strict=True)
    char_end: int = Field(ge=1,strict=True)


class SourceTextLocator(RequestModel):
    kind: Literal['SOURCE_CHUNK']
    source_anchor: QuestionAnchor


class ReportTextLocator(RequestModel):
    kind: Literal['REPORT_PARAGRAPH']
    source_kind: Literal['DISCOVERY','PROPOSAL'] = 'DISCOVERY'
    source_id: Id
    artifact_id: Hash
    docx_sha256: Hash
    paragraph_index: int = Field(ge=0,strict=True)
    char_start: int = Field(ge=0,strict=True)
    char_end: int = Field(ge=1,strict=True)


class OCRTextLocator(RequestModel):
    kind: Literal['OCR_LINE']
    observation_ref: RevisionRef
    line_id: Id
    char_start: int = Field(ge=0,strict=True)
    char_end: int = Field(ge=1,strict=True)


class TextSelection(RequestModel):
    text: str = Field(min_length=1,max_length=32000)
    quote_sha256: Hash
    offset_unit: Literal['UNICODE_CODEPOINT'] = 'UNICODE_CODEPOINT'
    locator: Annotated[CandidateTextLocator | SourceTextLocator | ReportTextLocator | OCRTextLocator,Field(discriminator='kind')]


class NativeBitmap(RequestModel):
    kind: Literal['NATIVE_CAPTURE']
    view_ref: str = Field(pattern=r'^[0-9a-f]{32}$')
    frame_revision: int = Field(ge=1,strict=True)
    sha256: Hash
    binding_sha256: Hash


class LocalBitmap(RequestModel):
    kind: Literal['LOCAL_FILE']
    file_source_ref: FileSource
    sha256: Hash


class Selection(RequestModel):
    graph_id: Id | None = None
    node_ids: list[Id] = Field(default_factory=list,max_length=2000)
    normalized_points: list[Point] = Field(default_factory=list,max_length=2000)
    viewport: Viewport | None = None
    page_number: int | None = Field(default=None,ge=1,le=100000,strict=True)
    text_selection: TextSelection | None = None
    bitmap_ref: Annotated[NativeBitmap | LocalBitmap,Field(discriminator='kind')] | None = None
    ocr_ref: RevisionRef | None = None


class FeedbackInput(RequestModel):
    operation_id: Id
    artifact_ref: ArtifactRef
    tool: Literal['COMMENT','RECTANGLE','FREEHAND','TEXT_SELECTION','TEXT_EDIT']
    comment: str = Field(min_length=1,max_length=8000)
    selection: Selection = Field(default_factory=Selection)
    edited_candidate: DiscoveryCandidate | None = None

    @model_validator(mode='after')
    def intentional(self):
        if (self.tool=='TEXT_EDIT')!=(self.edited_candidate is not None):
            raise ValueError('Text edit needs actual changed candidate; other tools do not accept it')
        if self.tool=='RECTANGLE' and len(self.selection.normalized_points)!=2:
            raise ValueError('Rectangle needs two normalized corners')
        if self.tool=='FREEHAND' and len(self.selection.normalized_points)<2:
            raise ValueError('Freehand needs at least two points')
        if self.tool=='TEXT_SELECTION' and self.selection.text_selection is None:
            raise ValueError('Text annotation needs an exact selected quotation and locator')
        return self


class FeedbackRevise(DiscoveryAdvance):
    message: str = '按已提交批注修订，保留未涉及内容；意图不明确时先具体提问。'


class WorkbenchFeedback:
    def __init__(self,store,discovery):
        self.store,self.discovery=store,discovery
        self.text_authority=None
        self.bitmap_authority=None
        self.observation_authority=None
        discovery.feedback_authority=self

    def _text(self,pid,selection,view):
        selected=selection.text_selection
        if selected is None:return
        locator=selected.locator
        if locator.kind=='CANDIDATE_FIELD':
            value=view['payload']['candidate']
            try:
                for part in locator.field_pointer.split('/')[1:]:
                    part=part.replace('~1','/').replace('~0','~')
                    if isinstance(value,list):
                        if not part.isascii() or not part.isdecimal() or str(int(part))!=part:raise KeyError(part)
                        value=value[int(part)]
                    elif isinstance(value,dict):value=value[part]
                    else:raise KeyError(part)
            except (KeyError,IndexError,ValueError):raise InvalidInput('Selected text field does not exist') from None
            if not isinstance(value,str) or not 0<=locator.char_start<locator.char_end<=len(value):
                raise InvalidInput('Selected text range is outside the exact field')
            quote=value[locator.char_start:locator.char_end]
        elif locator.kind=='REPORT_PARAGRAPH':
            if self.text_authority is None:raise Conflict('Word paragraph authority is unavailable')
            quote=self.text_authority.quotation(pid,locator,view)
        elif locator.kind=='OCR_LINE':
            if self.observation_authority is None:raise Conflict('Offline OCR observation authority is unavailable')
            if selection.bitmap_ref is None or selection.ocr_ref is None:
                raise InvalidInput('OCR text selection requires its exact image and observation references')
            if locator.observation_ref!=selection.ocr_ref:
                raise InvalidInput('OCR text selection and attached observation differ')
            self.observation_authority.resolve(pid,selection.ocr_ref.model_dump(),selection.bitmap_ref.model_dump())
            quote=self.observation_authority.quotation(pid,locator)
        else:
            anchor=locator.source_anchor;material=next((d for d in view['payload']['source_receipt']['documents'] if d['document_id']==anchor.document_id),None)
            if material is None or material['revision']!=anchor.revision or material['raw_sha256']!=anchor.raw_sha256:
                raise InvalidInput('Selected text is outside this artifact source revision')
            chunk=next((c for c in material['chunks'] if c['index']==anchor.chunk_index),None)
            if chunk is None or chunk['sha256']!=anchor.chunk_sha256 or raw_hash(chunk['content'])!=chunk['sha256']:
                raise Conflict('Selected source text integrity failed')
            if not chunk['char_start']<=anchor.char_start<anchor.char_end<=chunk['char_end']:
                raise InvalidInput('Selected text range is outside the exact source chunk')
            quote=chunk['content'][anchor.char_start-chunk['char_start']:anchor.char_end-chunk['char_start']]
            if raw_hash(quote)!=anchor.quote_sha256:raise InvalidInput('Selected source quote hash does not match')
        if quote!=selected.text or raw_hash(quote)!=selected.quote_sha256:
            raise InvalidInput('Selected quotation differs from the versioned artifact')

    def get(self,pid,ident):
        self.store.project(pid)
        with self.store.connect() as c:
            row=c.execute('SELECT * FROM workbench_feedback WHERE id=? AND project_id=?',(ident,pid)).fetchone()
        if row is None:raise NotFound('Feedback not found in this project')
        payload=json.loads(row['payload'])
        if digest(payload)!=row['sha256']:raise Conflict('Feedback integrity check failed')
        return {'feedback_ref':{'id':ident,'revision':1,'sha256':row['sha256']},'payload':payload,'created_at':row['created_at']}

    def list(self,pid,sid):
        self.discovery.get(pid,sid)
        with self.store.connect() as c:
            rows=c.execute('SELECT id FROM workbench_feedback WHERE project_id=? ORDER BY rowid',(pid,)).fetchall()
        return [v for v in [self.get(pid,r['id']) for r in rows] if v['payload']['artifact_ref']['id']==sid]

    def resolve(self,pid,ref,view):
        value=self.get(pid,ref['id'])
        if value['feedback_ref']!=ref:raise Conflict('Feedback reference changed')
        expected={'kind':'DISCOVERY','id':view['id'],'revision':view['revision'],'sha256':view['sha256']}
        if value['payload']['revision_target_ref']!=expected:
            raise Conflict('Feedback applies to another revision; explicitly rebase it')
        return value

    def record(self,pid,body):
        self.store.project(pid);request_sha=digest(body.model_dump(exclude={'operation_id'}))
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            old=c.execute('SELECT * FROM workbench_feedback_operations WHERE project_id=? AND operation_id=?',(pid,body.operation_id)).fetchone()
            if old:
                if old['request_sha256']!=request_sha:raise Conflict('IDEMPOTENCY_CONFLICT: feedback input changed')
                result=json.loads(old['result'])
                if digest(result)!=old['result_sha256']:raise Conflict('Feedback receipt integrity failed')
                return result
            view=self.discovery._version(c,pid,body.artifact_ref.id)
            if (view['revision'],view['sha256'])!=(body.artifact_ref.revision,body.artifact_ref.sha256):
                raise Conflict('Workbench changed; reload before annotation')
            graphs={g['id']:g for g in view['payload']['candidate']['graphs']}
            selection=body.selection
            self._text(pid,selection,view)
            if selection.bitmap_ref:
                if self.bitmap_authority is None:raise Conflict('Bitmap source authority is unavailable')
                self.bitmap_authority(pid,selection.bitmap_ref.model_dump())
                if selection.graph_id or selection.node_ids:raise InvalidInput('A bitmap region is not a structured graph node selection')
            if selection.ocr_ref:
                if self.observation_authority is None:raise Conflict('Offline OCR observation authority is unavailable')
                self.observation_authority.resolve(pid,selection.ocr_ref.model_dump(),selection.bitmap_ref.model_dump() if selection.bitmap_ref else None)
            if selection.graph_id:
                if selection.graph_id not in graphs:raise InvalidInput('Selected graph does not exist')
                if not set(selection.node_ids)<={n['id'] for n in graphs[selection.graph_id]['nodes']}:
                    raise InvalidInput('Selected nodes do not exist in this graph')
            elif selection.node_ids:raise InvalidInput('Node selection needs exact graph identity')
            if body.edited_candidate:
                self.discovery._validate(body.edited_candidate,view['payload']['source_receipt'])
                op='fb-edit-'+digest([pid,body.operation_id])[:40]
                self.discovery._claim(c,pid,view['id'],op,'FEEDBACK_EDIT',request_sha)
                payload={**view['payload'],'revision':view['revision']+1,'candidate':body.edited_candidate.model_dump(),
                         'status':'DRAFT','accepted_claim_ids':[],'accepted_graph_ids':[],'resolutions':[],
                         'proposal_ref':None,'change_reason':body.comment}
                self.discovery._append(c,pid,view['id'],payload)
                updated=self.discovery._view(c,pid,self.discovery._version(c,pid,view['id']))
                self.discovery._finish(c,pid,op,updated)
            else:updated=self.discovery._view(c,pid,view)
            ident=str(uuid.uuid4());target={'kind':'DISCOVERY','id':updated['id'],'revision':updated['revision'],'sha256':updated['sha256']}
            payload={'id':ident,'project_id':pid,'artifact_ref':body.artifact_ref.model_dump(),
                     'revision_target_ref':target,'tool':body.tool,'comment':body.comment,'selection':selection.model_dump(),
                     'change_kind':'USER_EDIT' if body.edited_candidate else 'USER_ANNOTATION',
                     'coordinates_are':'NORMALIZED_VIEWPORT_NOT_INFERRED_MODEL_LOCATIONS','created_at':stamp()}
            value_sha=digest(payload)
            c.execute('INSERT INTO workbench_feedback VALUES (?,?,?,?,?)',(ident,pid,canonical(payload),value_sha,payload['created_at']))
            result={'feedback_ref':{'id':ident,'revision':1,'sha256':value_sha},'artifact_ref':body.artifact_ref.model_dump(),
                    'revision_target_ref':target,'status':'RECORDED','native_requested':False,'session':updated,'payload':payload}
            c.execute('INSERT INTO workbench_feedback_operations VALUES (?,?,?,?,?,?)',
                      (pid,body.operation_id,request_sha,ident,canonical(result),digest(result)))
            self.discovery._event(c,pid,updated['id'],body.operation_id,'WORKBENCH_FEEDBACK_RECORDED',feedback_ref=result['feedback_ref'])
            return result

    async def revise(self,pid,ident,body):
        feedback=self.get(pid,ident);target=feedback['payload']['revision_target_ref']
        if target['revision']!=body.expected_revision:raise Conflict('Revision does not match feedback target')
        value=body.model_dump();value['feedback_ref']=feedback['feedback_ref']
        return await self.discovery.advance(pid,target['id'],DiscoveryAdvance.model_validate(value))
