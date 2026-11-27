"""Editable task networks. Planning approval does not authorize execution."""
from typing import Literal

from pydantic import Field, model_validator

from packages.domain.intake import IntakeMaterial
from packages.domain.questions import Hash, Id, QuestionAnchor, RequestModel
from packages.domain.planning_coverage import CoverageEdit, ModelClaimLink


class PlanningNode(RequestModel):
    id: Id
    title: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=24000)
    kind: Literal['TASK', 'MILESTONE', 'DELIVERABLE', 'DECISION'] = 'TASK'
    parent_id: Id | None = None
    depends_on: list[Id] = Field(default_factory=list, max_length=2000)
    inputs: list[str] = Field(default_factory=list, max_length=100)
    outputs: list[str] = Field(default_factory=list, max_length=100)
    acceptance: list[str] = Field(default_factory=list, max_length=100)
    capabilities: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    source_anchors: list[QuestionAnchor] = Field(default_factory=list, max_length=32)
    origin: Literal['SOURCE_OUTLINE', 'MODEL_SUGGESTION', 'USER_STATEMENT'] = 'MODEL_SUGGESTION'


class PlanningQuestion(RequestModel):
    id: Id
    text: str = Field(min_length=1, max_length=16000)
    reason: str = Field(min_length=1, max_length=4000)
    category: Literal['MISSING_INPUT', 'CONFLICT', 'UNREASONABLE', 'DECISION']
    blocking: bool = Field(default=True, strict=True)
    affects_node_ids: list[Id] = Field(default_factory=list, max_length=2000)
    requested_materials: list[str] = Field(default_factory=list, max_length=100)
    source_anchors: list[QuestionAnchor] = Field(default_factory=list, max_length=32)


class PlanningCandidate(RequestModel):
    title: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=24000)
    goals: list[str] = Field(default_factory=list, max_length=100)
    constraints: list[str] = Field(default_factory=list, max_length=100)
    risks: list[str] = Field(default_factory=list, max_length=100)
    assumptions: list[str] = Field(default_factory=list, max_length=100)
    nodes: list[PlanningNode] = Field(min_length=1, max_length=2000)
    questions: list[PlanningQuestion] = Field(default_factory=list, max_length=200)

    @model_validator(mode='after')
    def graph_integrity(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError('Node IDs must be unique')
        if len({q.id for q in self.questions}) != len(self.questions):
            raise ValueError('Question IDs must be unique')
        if any(q.id in ids for q in self.questions):
            raise ValueError('Question and node identities must be distinct')
        dependencies, parents = {}, {}
        for node in self.nodes:
            if len(set(node.depends_on)) != len(node.depends_on):
                raise ValueError('Dependencies must be unique')
            if node.id in node.depends_on or not set(node.depends_on) <= ids:
                raise ValueError('Dependencies must name other existing nodes')
            if node.parent_id == node.id or (node.parent_id is not None and node.parent_id not in ids):
                raise ValueError('Parent must name another existing node')
            dependencies[node.id] = node.depends_on
            parents[node.id] = [node.parent_id] if node.parent_id else []
        for question in self.questions:
            if not set(question.affects_node_ids) <= ids:
                raise ValueError('Question scope names missing nodes')
        for edges in (dependencies, parents):
            pending = {k: set(v) for k, v in edges.items()}
            while pending:
                ready = {k for k, v in pending.items() if not v}
                if not ready:
                    raise ValueError('Dependency/parent graph must be acyclic')
                pending = {k: v-ready for k, v in pending.items() if k not in ready}
        # Refuse oversized candidates, never silently truncate a complex plan.
        if len(self.model_dump_json().encode('utf-8')) > 2 * 1024 * 1024:
            raise ValueError('Candidate exceeds 2 MiB; divide the plan explicitly')
        return self


class CoveredModelProposal(RequestModel):
    candidate: PlanningCandidate
    claim_links: list[ModelClaimLink] = Field(max_length=2000)

    @model_validator(mode='after')
    def bounded_output(self):
        if sum(len(row.targets) for row in self.claim_links) > 8000 or len(self.model_dump_json().encode()) > 2 * 1024 * 1024:
            raise ValueError('Structured proposal and coverage exceed budget; split the plan')
        return self


class WorkspaceRef(RequestModel):
    id: Hash
    revision: int = Field(ge=1, strict=True)
    binding_sha256: Hash


class PlanningBudget(RequestModel):
    max_calls: int = Field(default=1, ge=1, le=10, strict=True)
    timeout_seconds: int = Field(default=120, ge=1, le=600, strict=True)
    max_output_tokens: int = Field(default=16000, ge=256, le=32768, strict=True)


class PlanningContextRef(RequestModel):
    id: Id
    revision: int = Field(ge=1, strict=True)
    sha256: Hash


class ProposalInput(RequestModel):
    operation_id: Id
    prompt: str = Field(min_length=1, max_length=16000)
    materials: list[IntakeMaterial] = Field(min_length=1, max_length=20)
    workspace_ref: WorkspaceRef | None = None
    provider_profile_ref: dict | None = None
    budget: PlanningBudget = Field(default_factory=PlanningBudget)
    mode: Literal['opencode', 'manual', 'source_outline'] = 'opencode'
    candidate: PlanningCandidate | None = None
    discovery_ref: PlanningContextRef | None = None
    coverage_contract: Literal['ACCEPTED_CLAIMS_V1'] | None = None

    @model_validator(mode='after')
    def explicit_mode(self):
        if (self.mode == 'manual') != (self.candidate is not None):
            raise ValueError('Manual mode needs a candidate; model and outline modes do not accept one')
        if self.mode == 'opencode' and self.workspace_ref is None:
            raise ValueError('OpenCode planning needs the selected local workspace')
        if len({m.document_id for m in self.materials}) != len(self.materials):
            raise ValueError('Material IDs must be unique')
        if self.coverage_contract is not None and self.discovery_ref is None:
            raise ValueError('Coverage needs exact confirmed discovery items')
        return self


class ManualCaptureRef(RequestModel):
    id: Hash
    revision: int = Field(ge=1, le=1, strict=True)
    sha256: Hash


class ManualExportRef(RequestModel):
    id: Hash
    request_sha256: Hash
    result_sha256: Hash


class ManualFileRef(RequestModel):
    export_ref: ManualExportRef
    path: str = Field(min_length=1, max_length=1024)
    basis_sha256: Hash
    edited_sha256: Hash

    @model_validator(mode='after')
    def registered_path(self):
        from packages.domain.local_workspaces import relative
        relative(self.path)
        return self


class ManualCaptureInput(RequestModel):
    operation_id: Id
    target_proposal_ref: PlanningContextRef
    workspace_ref: WorkspaceRef
    files: list[ManualFileRef] = Field(min_length=1, max_length=32)
    predecessor_export_ref: ManualExportRef | None = None

    @model_validator(mode='after')
    def distinct_files(self):
        if len({f.path for f in self.files}) != len(self.files):
            raise ValueError('Choose one exact snapshot per registered harness path')
        return self


class ManualTarget(RequestModel):
    node_id: Id
    field: Literal['description', 'inputs', 'outputs', 'acceptance', 'risks']
    edit_action: Literal['ADD', 'REPLACE', 'REMOVE']
    item_index: int | None = Field(default=None, ge=0, strict=True)
    before_value_sha256: Hash


class ManualDecision(RequestModel):
    hunk_id: Hash
    hunk_sha256: Hash
    mode: Literal['KEEP', 'REPLACE', 'DISCARD']
    target: ManualTarget | None = None
    text: str | None = Field(default=None, min_length=1, max_length=24000)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def explicit_effect(self):
        if self.mode == 'DISCARD':
            if self.target is not None or self.text is not None:
                raise ValueError('Discard carries a reason, not a field mutation')
        elif self.target is None:
            raise ValueError('Adoption must name an exact current task field')
        elif self.target.edit_action != 'REMOVE' and self.text is None:
            raise ValueError('Adoption must include the exact accepted wording')
        elif self.target.edit_action == 'REMOVE' and self.text is not None:
            raise ValueError('Removal does not append or replace text')
        return self


class ManualReview(RequestModel):
    change_ref: ManualCaptureRef
    decisions: list[ManualDecision] = Field(min_length=1, max_length=200)

    @model_validator(mode='after')
    def distinct_hunks(self):
        if len({d.hunk_id for d in self.decisions}) != len(self.decisions):
            raise ValueError('Each captured hunk needs one explicit decision')
        return self


class ProposalEdit(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1, strict=True)
    candidate: PlanningCandidate
    change_reason: str = Field(min_length=1, max_length=4000)
    manual_review: ManualReview | None = None
    requirement_coverage: CoverageEdit | None = None


class ProposalAnswer(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1, strict=True)
    question_id: Id
    text: str = Field(min_length=1, max_length=16000)


class QuestionResolution(RequestModel):
    question_id: Id
    mode: Literal['ANSWER_ACCEPTED', 'ASSUMPTION_ACCEPTED']
    answer_id: Id | None = None
    assumption: str | None = Field(default=None, min_length=1, max_length=16000)
    reason: str = Field(min_length=1, max_length=4000)

    @model_validator(mode='after')
    def explicit_basis(self):
        if self.mode == 'ANSWER_ACCEPTED' and (not self.answer_id or self.assumption is not None):
            raise ValueError('Answer resolution needs one exact answer')
        if self.mode == 'ASSUMPTION_ACCEPTED' and (not self.assumption or self.answer_id is not None):
            raise ValueError('Assumption resolution needs explicit text')
        return self


class ProposalConfirm(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1, strict=True)
    proposal_sha256: Hash
    user_confirmed: bool = Field(strict=True)
    resolutions: list[QuestionResolution] = Field(default_factory=list, max_length=200)
    coverage_sha256: Hash | None = None
    confirmed_exclusion_ids: list[Id] | None = Field(default=None, max_length=2000)

    @model_validator(mode='after')
    def explicit_confirmation(self):
        if self.user_confirmed is not True:
            raise ValueError('User confirmation must be explicit')
        if len({r.question_id for r in self.resolutions}) != len(self.resolutions):
            raise ValueError('Question resolutions must be unique')
        if self.confirmed_exclusion_ids is not None and len(set(self.confirmed_exclusion_ids)) != len(self.confirmed_exclusion_ids):
            raise ValueError('Exclusion confirmations must be unique')
        return self


class HarnessExportInput(RequestModel):
    operation_id: Id
    expected_revision: int = Field(ge=1, strict=True)
    proposal_sha256: Hash
    workspace_ref: WorkspaceRef
    directory: str = Field(default='eeas-harness',min_length=1,max_length=300)

    @model_validator(mode='after')
    def normalized_directory(self):
        from packages.domain.local_workspaces import relative
        relative(self.directory)
        return self
