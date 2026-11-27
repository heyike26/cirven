"""Typed professional discovery. Suggestions never approve themselves."""
from typing import Literal
from pydantic import Field, model_validator
from packages.domain.intake import IntakeMaterial
from packages.domain.questions import RequestModel, QuestionAnchor, Id, Hash
from packages.domain.planning_schema import WorkspaceRef, PlanningBudget, QuestionResolution

Phase = Literal['DISCOVER', 'DEFINE', 'RESEARCH', 'SPECIFY', 'PLAN']
Origin = Literal['USER_STATEMENT', 'SOURCE_FACT', 'MODEL_SUGGESTION']


class RevisionRef(RequestModel):
    id: Id
    revision: int = Field(ge=1, strict=True)
    sha256: Hash


class DiscoveryClaim(RequestModel):
    id: Id
    text: str = Field(min_length=1, max_length=24000)
    category: Literal['PROBLEM','NEED','REQUIREMENT','CONSTRAINT','RISK','ASSUMPTION','DECISION']
    origin: Origin = 'MODEL_SUGGESTION'
    source_anchors: list[QuestionAnchor] = Field(default_factory=list, max_length=32)
    acceptance: list[str] = Field(default_factory=list, max_length=100)

    @model_validator(mode='after')
    def grounded(self):
        if self.origin == 'SOURCE_FACT' and not self.source_anchors:
            raise ValueError('Source facts require actual source anchors')
        return self


class DiscoveryQuestion(RequestModel):
    id: Id
    text: str = Field(min_length=1,max_length=16000)
    reason: str = Field(min_length=1,max_length=4000)
    category: Literal['MISSING_INPUT','CONFLICT','UNREASONABLE','DECISION']
    blocking: bool = Field(default=True,strict=True)
    affects_claim_ids: list[Id] = Field(default_factory=list,max_length=2000)
    requested_materials: list[str] = Field(default_factory=list,max_length=100)
    source_anchors: list[QuestionAnchor] = Field(default_factory=list,max_length=32)


class CompetitorObservation(RequestModel):
    dimension: str = Field(min_length=1,max_length=300)
    text: str = Field(min_length=1,max_length=8000)
    status: Literal['OFFICIAL_CLAIM','USER_TESTED','INFERENCE','UNKNOWN']
    source_anchors: list[QuestionAnchor] = Field(default_factory=list,max_length=32)

    @model_validator(mode='after')
    def no_uncited_fact(self):
        if self.status in {'OFFICIAL_CLAIM','USER_TESTED'} and not self.source_anchors:
            raise ValueError('Competitor facts need captured source evidence')
        return self


class DiscoveryCompetitor(RequestModel):
    id: Id
    name: str = Field(min_length=1,max_length=300)
    alternative_type: Literal['DIRECT','INDIRECT','MANUAL','NON_CONSUMPTION']
    summary: str = Field(min_length=1,max_length=16000)
    observations: list[CompetitorObservation] = Field(default_factory=list,max_length=100)


class DiagramNode(RequestModel):
    id: Id
    label: str = Field(min_length=1,max_length=300)
    description: str = Field(min_length=1,max_length=24000)
    claim_ids: list[Id] = Field(default_factory=list,max_length=100)
    source_anchors: list[QuestionAnchor] = Field(default_factory=list,max_length=32)
    origin: Origin = 'MODEL_SUGGESTION'


class DiagramEdge(RequestModel):
    id: Id
    source: Id
    target: Id
    relation: str = Field(min_length=1,max_length=300)
    claim_ids: list[Id] = Field(default_factory=list,max_length=100)
    source_anchors: list[QuestionAnchor] = Field(default_factory=list,max_length=32)
    origin: Origin = 'MODEL_SUGGESTION'


class DiscoveryGraph(RequestModel):
    id: Id
    kind: Literal['ARCHITECTURE','FLOW','MINDMAP']
    title: str = Field(min_length=1,max_length=300)
    nodes: list[DiagramNode] = Field(min_length=1,max_length=2000)
    edges: list[DiagramEdge] = Field(default_factory=list,max_length=8000)

    @model_validator(mode='after')
    def structure(self):
        ids={n.id for n in self.nodes}
        if len(ids)!=len(self.nodes) or len({e.id for e in self.edges})!=len(self.edges) or ids & {e.id for e in self.edges}:
            raise ValueError('Diagram IDs must be unique')
        if any(e.source not in ids or e.target not in ids for e in self.edges):
            raise ValueError('Diagram edges need real endpoints')
        if self.kind=='MINDMAP':
            incoming={i:0 for i in ids}; pending={i:set() for i in ids}
            for e in self.edges:
                incoming[e.target]+=1;pending[e.target].add(e.source)
            if any(v>1 for v in incoming.values()) or sum(v==0 for v in incoming.values())!=1:
                raise ValueError('Mindmap must have one root and one parent per child')
            while pending:
                ready={k for k,v in pending.items() if not v}
                if not ready:raise ValueError('Mindmap cannot contain a cycle')
                pending={k:v-ready for k,v in pending.items() if k not in ready}
        for item in [*self.nodes,*self.edges]:
            if item.origin=='SOURCE_FACT' and not item.source_anchors:
                raise ValueError('Fact diagram elements require source evidence')
        return self


class DiscoveryAction(RequestModel):
    id: Id
    method: str = Field(min_length=1,max_length=300)
    action: str = Field(min_length=1,max_length=8000)
    reason: str = Field(min_length=1,max_length=4000)
    expected_artifact: str = Field(min_length=1,max_length=4000)


class DiscoveryCandidate(RequestModel):
    title: str = Field(min_length=1,max_length=200)
    phase: Phase
    summary: str = Field(min_length=1,max_length=24000)
    claims: list[DiscoveryClaim] = Field(default_factory=list,max_length=2000)
    questions: list[DiscoveryQuestion] = Field(default_factory=list,max_length=200)
    competitors: list[DiscoveryCompetitor] = Field(default_factory=list,max_length=100)
    graphs: list[DiscoveryGraph] = Field(default_factory=list,max_length=20)
    next_actions: list[DiscoveryAction] = Field(default_factory=list,max_length=20)

    @model_validator(mode='after')
    def references(self):
        ids={c.id for c in self.claims}
        for rows in [self.claims,self.questions,self.competitors,self.graphs,self.next_actions]:
            if len({x.id for x in rows})!=len(rows):raise ValueError('Item IDs must be unique')
        if ids & {q.id for q in self.questions}:raise ValueError('Claim/question IDs must differ')
        for q in self.questions:
            if not set(q.affects_claim_ids)<=ids:raise ValueError('Questions refer to missing claims')
        for graph in self.graphs:
            for item in [*graph.nodes,*graph.edges]:
                if not set(item.claim_ids)<=ids:raise ValueError('Diagram basis refers to missing claims')
                if not item.claim_ids and not item.source_anchors:
                    raise ValueError('Every diagram element needs a requirement or source basis')
        if len(self.model_dump_json().encode())>2*1024*1024:
            raise ValueError('Discovery candidate exceeds 2 MiB; split explicitly')
        return self


class DiscoveryCreate(RequestModel):
    operation_id: Id
    title: str = Field(min_length=1,max_length=200)
    entry_mode: Literal['PROBLEM','REQUIREMENTS','TASK_FILE']
    statement: str = Field(min_length=1,max_length=16000)
    materials: list[IntakeMaterial] = Field(default_factory=list,max_length=19)
    workspace_ref: WorkspaceRef | None = None

    @model_validator(mode='after')
    def entry(self):
        if self.entry_mode=='TASK_FILE' and not self.materials:
            raise ValueError('Task-file entry needs a real selected document')
        if len({m.document_id for m in self.materials})!=len(self.materials):
            raise ValueError('Materials must be unique')
        return self


class DiscoveryAdvance(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1,strict=True)
    message: str = Field(min_length=1,max_length=16000)
    provider_profile_ref: dict | None = None
    workspace_ref: WorkspaceRef | None = None
    budget: PlanningBudget = Field(default_factory=PlanningBudget)
    materials: list[IntakeMaterial] | None = Field(default=None,max_length=19)
    target_phase: Phase | None = None
    feedback_ref: RevisionRef | None = None
    feedback_refs: list[RevisionRef] = Field(default_factory=list,max_length=20)

    @model_validator(mode='after')
    def feedback_consistency(self):
        if self.feedback_ref and self.feedback_refs:
            raise ValueError('Use either one feedback_ref or the attachment list')
        keys=[(r.id,r.revision,r.sha256) for r in self.feedback_refs]
        if len(set(keys))!=len(keys):raise ValueError('Feedback attachments must be distinct')
        return self


class DiscoveryEdit(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1,strict=True)
    candidate: DiscoveryCandidate
    change_reason: str = Field(min_length=1,max_length=4000)


class DiscoveryAnswer(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1,strict=True)
    question_id: Id
    text: str = Field(min_length=1,max_length=16000)


class DiscoveryConfirm(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1,strict=True)
    session_sha256: Hash
    user_confirmed: bool = Field(strict=True)
    accepted_claim_ids: list[Id] = Field(min_length=1,max_length=2000)
    accepted_graph_ids: list[Id] = Field(default_factory=list,max_length=20)
    resolutions: list[QuestionResolution] = Field(default_factory=list,max_length=200)

    @model_validator(mode='after')
    def explicit(self):
        if self.user_confirmed is not True:raise ValueError('Confirmation must be explicit')
        for values in [self.accepted_claim_ids,self.accepted_graph_ids,[r.question_id for r in self.resolutions]]:
            if len(set(values))!=len(values):raise ValueError('Confirmation references must be unique')
        return self


class DiscoveryPlan(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1,strict=True)
    provider_profile_ref: dict | None = None
    budget: PlanningBudget = Field(default_factory=PlanningBudget)
    experience_ref: RevisionRef | None = None
    coverage_contract: Literal['ACCEPTED_CLAIMS_V1'] | None = None
