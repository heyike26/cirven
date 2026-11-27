"""Same-origin control of an exact application-owned original conversation."""
from fastapi import Query
from typing import Literal
from packages.domain.questions import Id

from packages.adapters.opencode_provider import OpenCodeProviderRegistry
from packages.agent_sessions import AgentSessions
from packages.agent_sessions.models import (InterruptSession, LoadSession, OpenConversation, OpenSession, PendingControl,
    PermissionReply, QuestionReply, SendMessage, SessionControl)
from packages.agent_sessions.guidance import GuidanceInput
from packages.agent_sessions.test_reports import CommandTestReportInput
from packages.agent_sessions.work_requirements import (WorkRequirementInspect,WorkRequirementLink,
    WorkRequirementOperation,inspect_work_requirements,link_work_requirements,inspect_work_requirement_operation)

from packages.agent_sessions.work_retests import (WorkRetestPrepare,WorkRetestSubmit,WorkRetestInspect,WorkRetestContinue,
    prepare_work_retest,submit_work_retest,inspect_work_retest,continue_work_retest)
from packages.agent_sessions.work_deliveries import (WorkDeliveries,WorkDeliveryPrepare,WorkDeliveryCreate,
    WorkDeliveryLookup,WorkDeliveryRetain,WorkDeliveryList)
from packages.agent_sessions.work_roots import WorkRootsQuery,inspect_work_roots
from packages.agent_sessions.work_maintenance import (WorkMaintenance,WorkMaintenanceBrowse,
    WorkMaintenanceDelivery,WorkMaintenancePrepare,WorkMaintenanceChange,
    WorkMaintenanceInspect,WorkMaintenanceOperation)

from packages.agent_sessions.work_test_cases import (WorkCaseInspect,WorkCaseMap,WorkCaseOperation,
    inspect_work_test_cases,map_work_test_case,inspect_work_case_operation)
from packages.agent_sessions.work_requirement_sources import (WorkRequirementSourceBrowse,
    WorkRequirementSourceReview,WorkRequirementSourceChange,WorkRequirementSourceOperation,
    browse_work_requirement_sources,review_work_requirement_source,change_work_requirement_source,
    inspect_work_requirement_source_operation)

def register_agent_session_routes(app, store, data_dir, *, service=None):
    if getattr(app.state, "agent_sessions", None) is not None:
        return app.state.agent_sessions
    providers = getattr(app.state, "provider_settings", None)
    value = service or AgentSessions(store, data_dir,
        provider_registry=OpenCodeProviderRegistry(providers.configuration, providers.secret),
        workspaces=app.state.local_workspaces, capabilities=app.state.native_capabilities)
    app.state.agent_sessions = value
    native_messages = getattr(app.state, 'native_messages', None)
    if native_messages is not None:
        from packages.agent_sessions.native_review import NativeReviews
        native_messages.reviews = NativeReviews(native_messages, value)
        from packages.agent_sessions.native_message import NativeMessageSessions
        native_messages.ordinary = NativeMessageSessions(native_messages, value)
    root = "/api/projects/{project_id}/agent-sessions"

    @app.get(root + '/{session_id}/model-budget')
    def original_model_budget(project_id:Id,session_id:Id,session_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        from packages.agent_sessions.subscription_http_budget import public
        return public(value,project_id,session_id,session_sha256)

    @app.get(root + '/{session_id}/resource-budget')
    def original_work_resource_budget(project_id:Id,session_id:Id,session_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),operation_id:Id|None=None):
        from packages.agent_sessions.resource_budget_public import public
        return public(value,project_id,session_id,session_sha256,operation_id)

    from packages.agent_sessions.resource_allowance import AllowanceChange

    @app.post(root + '/{session_id}/resource-budget/adjust')
    def change_original_work_allowance(project_id:Id,session_id:Id,body:AllowanceChange):
        from packages.agent_sessions.resource_allowance import change
        return change(value,project_id,session_id,body)

    @app.get(root + '/{session_id}/resource-budget/adjustments')
    def original_work_allowance_packets(project_id:Id,session_id:Id,work_operation_id:Id,session_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        from packages.agent_sessions.resource_allowance_packets import page
        return page(value,project_id,session_id,session_sha256,work_operation_id)

    @app.get(root + '/{session_id}/resource-budget/adjustments/{operation_id}')
    def inspect_original_work_allowance(project_id:Id,session_id:Id,operation_id:Id,work_operation_id:Id,session_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        from packages.agent_sessions.resource_allowance import lookup
        return lookup(value,project_id,session_id,session_sha256,work_operation_id,operation_id)

    from packages.agent_sessions.experience_candidates import (ExperienceCandidates, ExperiencePropose, ExperienceReview)
    experiences = ExperienceCandidates(value)
    app.state.experience_candidates = experiences
    from packages.agent_sessions.experience_source_policy import ExperienceSourcePolicy,ExperienceSourceDecision
    experience_sources=ExperienceSourcePolicy(experiences)
    if value.capabilities is not None:value.capabilities.experience_source_reader=experience_sources.origin_status
    def present_experience(pid,sid,value):return experience_sources.public(pid,sid,value)

    from packages.agent_sessions.experience_requests import ExperienceRequestPrepare,prepare_experience_request

    @app.post(root + '/{session_id}/experience/requests/prepare')
    def prepare_experience_request_packet(project_id:Id,session_id:Id,body:ExperienceRequestPrepare):
        return prepare_experience_request(experiences,project_id,session_id,body)

    from packages.agent_sessions.experience_pending import ExperiencePending,ExperiencePendingControl
    experience_pending=ExperiencePending(experiences)
    app.state.experience_pending=experience_pending

    @app.get(root + '/{session_id}/experience/pending-configuration/{operation_id}')
    def inspect_experience_pending(project_id:Id,session_id:Id,operation_id:Id,
                                   request_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experience_pending.inspect(project_id,session_id,operation_id,request_sha256))

    @app.post(root + '/{session_id}/experience/pending-configuration')
    def control_experience_pending(project_id:Id,session_id:Id,body:ExperiencePendingControl):
        return present_experience(project_id,session_id,experience_pending.change(project_id,session_id,body))

    @app.get(root + '/{session_id}/experience/pending-configuration/{activation_operation_id}/controls/{operation_id}')
    def inspect_experience_pending_control(project_id:Id,session_id:Id,activation_operation_id:Id,operation_id:Id,
            activation_request_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),
            request_sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),
            mode:Literal['PAUSE','COMPLETE_PAUSED_METADATA','RESUME_PAUSED']=Query()):
        return present_experience(project_id,session_id,experience_pending.inspect_control(project_id,session_id,
            activation_operation_id,activation_request_sha256,operation_id,request_sha256,mode))

    @app.get(root + '/{session_id}/experience/source-policy/{candidate_id}')
    def experience_source_policy(project_id: Id, session_id: Id, candidate_id: Id,
                                 sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return experience_sources.status(project_id,session_id,{'id':candidate_id,'revision':1,'sha256':sha256})

    @app.post(root + '/{session_id}/experience/source-policy')
    def decide_experience_source(project_id: Id, session_id: Id, body: ExperienceSourceDecision):
        return experience_sources.decision(project_id,session_id,body)

    @app.get(root + '/{session_id}/experience/source-policy/{candidate_id}/operations/{operation_id}')
    def inspect_experience_source_decision(project_id:Id,session_id:Id,candidate_id:Id,operation_id:Id,
            sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),request_sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        return experience_sources.inspect_decision(project_id,session_id,
            {'id':candidate_id,'revision':1,'sha256':sha256},operation_id,request_sha256)


    @app.get(root + '/{session_id}/experience/candidates')
    def experience_candidates(project_id: Id, session_id: Id, offset: int = Query(default=0, ge=0, le=32)):
        return present_experience(project_id,session_id,experiences.listing(project_id, session_id, offset))

    @app.post(root + '/{session_id}/experience/candidates')
    def propose_experience_candidate(project_id: Id, session_id: Id, body: ExperiencePropose):
        return present_experience(project_id,session_id,experiences.propose(project_id, session_id, body))

    @app.get(root + '/{session_id}/experience/candidates/{candidate_id}')
    def experience_candidate(project_id: Id, session_id: Id, candidate_id: Id,
                             sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experiences.candidate(project_id, session_id, {'id':candidate_id,'revision':1,'sha256':sha256}))

    @app.get(root + '/{session_id}/experience/candidates/{candidate_id}/source-work')
    def experience_source_work(project_id:Id,session_id:Id,candidate_id:Id,sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        return experiences.source_work(project_id,session_id,{'id':candidate_id,'revision':1,'sha256':sha256})

    @app.get(root + '/{session_id}/experience/candidates/{candidate_id}/history')
    def experience_candidate_history(project_id:Id,session_id:Id,candidate_id:Id,sha256:str=Query(pattern=r'^[a-f0-9]{64}$'),offset:int=Query(default=0,ge=0,le=256),expected_index_sha256:str|None=Query(default=None,pattern=r'^[a-f0-9]{64}$')):
        return experiences.history(project_id,session_id,{'id':candidate_id,'revision':1,'sha256':sha256},offset,expected_index_sha256)

    @app.post(root + '/{session_id}/experience/reviews')
    def review_experience_candidate(project_id: Id, session_id: Id, body: ExperienceReview):
        return present_experience(project_id,session_id,experiences.review(project_id, session_id, body))

    @app.get(root + '/{session_id}/experience/reviews/{review_id}')
    def experience_saved_review(project_id:Id,session_id:Id,review_id:Id,sha256:str=Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experiences.saved_record(project_id,session_id,{'id':review_id,'revision':1,'sha256':sha256},'REVIEW'))

    from packages.agent_sessions.experience_tool_validation import ExperienceToolValidation, ExperienceToolValidate
    experience_tool_validation = ExperienceToolValidation(experiences)
    app.state.experience_tool_validation = experience_tool_validation

    from packages.agent_sessions.experience_skill_validation import ExperienceSkillValidation, ExperienceSkillValidate
    experience_skill_validation=ExperienceSkillValidation(experiences)
    value.experience_validation=experience_skill_validation
    app.state.experience_skill_validation=experience_skill_validation

    @app.post(root + '/{session_id}/experience/skill-validations')
    async def validate_experience_skill(project_id: Id, session_id: Id, body: ExperienceSkillValidate):
        return present_experience(project_id,session_id,await experience_skill_validation.validate(project_id,session_id,body))

    @app.get(root + '/{session_id}/experience/skill-validations/{operation_id}')
    def inspect_experience_skill_validation(project_id: Id, session_id: Id, operation_id: Id,
                                           request_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experience_skill_validation.inspect(project_id,session_id,operation_id,request_sha256))

    @app.post(root + '/{session_id}/experience/tool-validations')
    async def validate_experience_tool(project_id: Id, session_id: Id, body: ExperienceToolValidate):
        return present_experience(project_id,session_id,await experience_tool_validation.validate(project_id, session_id, body))

    @app.get(root + '/{session_id}/experience/tool-validations/{operation_id}')
    def inspect_experience_tool_validation(project_id: Id, session_id: Id, operation_id: Id,
                                          request_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experience_tool_validation.inspect(project_id, session_id, operation_id, request_sha256))

    from packages.agent_sessions.experience_activation import (ExperienceActivation,ExperiencePromotePrepare,
        ExperiencePromote,ExperienceRollbackPrepare,ExperienceRollback,ExperienceActivationRecover)
    experience_activation=ExperienceActivation(experiences)
    app.state.experience_activation=experience_activation

    @app.post(root + '/{session_id}/experience/promotion/prepare')
    def prepare_experience_promotion(project_id: Id, session_id: Id, body: ExperiencePromotePrepare):
        return present_experience(project_id,session_id,experience_activation.prepare(project_id,session_id,body))

    @app.post(root + '/{session_id}/experience/promotion')
    def promote_experience(project_id: Id, session_id: Id, body: ExperiencePromote):
        return present_experience(project_id,session_id,experience_activation.promote(project_id,session_id,body))

    @app.post(root + '/{session_id}/experience/rollback/prepare')
    def prepare_experience_rollback(project_id: Id, session_id: Id, body: ExperienceRollbackPrepare):
        return present_experience(project_id,session_id,experience_activation.prepare_rollback(project_id,session_id,body))

    @app.post(root + '/{session_id}/experience/rollback')
    def rollback_experience(project_id: Id, session_id: Id, body: ExperienceRollback):
        return present_experience(project_id,session_id,experience_activation.rollback(project_id,session_id,body))

    @app.get(root + '/{session_id}/experience/configuration-operations/{operation_id}')
    def inspect_experience_configuration(project_id: Id, session_id: Id, operation_id: Id,
                                         request_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experience_activation.inspect(project_id,session_id,operation_id,request_sha256))

    @app.post(root + '/{session_id}/experience/configuration-operations/recover')
    def recover_experience_configuration(project_id: Id, session_id: Id, body: ExperienceActivationRecover):
        return present_experience(project_id,session_id,experience_activation.recover(project_id,session_id,body))

    @app.get(root + '/{session_id}/experience/operations/{operation_id}')
    def experience_operation(project_id: Id, session_id: Id, operation_id: Id,
                             request_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return present_experience(project_id,session_id,experiences.operation(project_id, session_id, operation_id, request_sha256))

    @app.get("/api/agent-sessions/configuration")
    def configuration():
        return value.configuration()

    @app.post("/api/agent-sessions/open")
    async def open_conversation(body: OpenConversation):
        if body.conversation_ref is not None:
            return await app.state.canonical_conversations.open(body)
        project_id = body.project_id or value.internal_project()
        return await value.open(project_id, body.model_dump(exclude={"project_id"}))

    @app.get('/api/agent-sessions/open-operations/{operation_id}')
    def open_operation(operation_id: str, project_id: str | None = Query(default=None,
                       min_length=1, max_length=100, pattern=r'^[A-Za-z0-9_-]+$')):
        canonical = getattr(app.state, 'canonical_conversations', None)
        if canonical is not None and canonical.has_open_operation(operation_id, project_id):
            return canonical.open_operation(operation_id, project_id)
        return value.open_operation(operation_id, project_id)

    @app.get(root)
    def sessions(project_id: str):
        return {"sessions": value.list(project_id)}

    @app.post(root + "/open")
    async def open_session(project_id: str, body: OpenSession):
        return await value.open(project_id, body)

    @app.get(root + "/{session_id}")
    async def read_session(project_id: str, session_id: str):
        return await value.inspect(project_id, session_id)

    @app.get(root + "/{session_id}/snapshot")
    def saved_session(project_id: str, session_id: str):
        # A canonical navigation read never connects or refreshes the SDK.
        return value.get(project_id,session_id)

    @app.post(root + "/{session_id}/load")
    async def load(project_id: str, session_id: str, body: LoadSession):
        return await value.load(project_id, session_id, body)

    @app.post(root + "/{session_id}/messages")
    async def send(project_id: str, session_id: str, body: SendMessage):
        return await value.send(project_id, session_id, body)

    @app.post(root + '/{session_id}/guidance')
    async def guidance(project_id: str, session_id: str, body: GuidanceInput):
        return await value.guidance.submit(project_id, session_id, body)

    @app.get(root + "/{session_id}/operations/{operation_id}")
    def operation(project_id: str, session_id: str, operation_id: str):
        return value.operation(project_id, session_id, operation_id)

    @app.get(root + "/{session_id}/events")
    def events(project_id: str, session_id: str, after: int = Query(default=0, ge=0), limit: int = Query(default=100, ge=1, le=200)):
        return value.events(project_id, session_id, after, limit)

    @app.get(root + '/{session_id}/diffs')
    def diffs(project_id: str, session_id: str,
              session_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
              generation: int = Query(ge=0),
              workspace_id: str = Query(pattern=r'^[a-f0-9]{64}$'),
              binding_revision: int = Query(ge=1),
              binding_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        return value.diffs(project_id, session_id, session_sha256=session_sha256, generation=generation,
            workspace_ref={'id': workspace_id, 'revision': binding_revision, 'binding_sha256': binding_sha256})

    @app.post(root + '/{session_id}/command-test-reports/inspect')
    def command_test_report(project_id: str,session_id: str,body:CommandTestReportInput):
        # Read-only inspection of one immutable after snapshot. No command or
        # model dispatch, report import, acceptance decision or workspace read.
        return value.command_test_report(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirements/inspect')
    def work_requirements(project_id:str,session_id:str,body:WorkRequirementInspect):
        return inspect_work_requirements(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirements/links')
    def work_requirement_link(project_id:str,session_id:str,body:WorkRequirementLink):
        return link_work_requirements(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirements/operations/inspect')
    def work_requirement_operation(project_id:str,session_id:str,body:WorkRequirementOperation):
        return inspect_work_requirement_operation(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirement-sources/catalog')
    def work_requirement_source_catalog(project_id:str,session_id:str,body:WorkRequirementSourceBrowse):
        return browse_work_requirement_sources(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirement-sources/review')
    def work_requirement_source_review(project_id:str,session_id:str,body:WorkRequirementSourceReview):
        return review_work_requirement_source(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirement-sources/decisions')
    def work_requirement_source_decision(project_id:str,session_id:str,body:WorkRequirementSourceChange):
        return change_work_requirement_source(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-requirement-sources/operations/inspect')
    def work_requirement_source_operation(project_id:str,session_id:str,body:WorkRequirementSourceOperation):
        return inspect_work_requirement_source_operation(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-test-cases/inspect')
    def work_test_cases(project_id:str,session_id:str,body:WorkCaseInspect):
        return inspect_work_test_cases(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-test-cases/mappings')
    def work_test_case_mapping(project_id:str,session_id:str,body:WorkCaseMap):
        return map_work_test_case(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-test-cases/operations/inspect')
    def work_test_case_operation(project_id:str,session_id:str,body:WorkCaseOperation):
        return inspect_work_case_operation(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-retests/prepare')
    def work_retest_prepare(project_id:str,session_id:str,body:WorkRetestPrepare):
        return prepare_work_retest(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-retests/commands')
    async def work_retest_submit(project_id:str,session_id:str,body:WorkRetestSubmit):
        return await submit_work_retest(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-retests/operations/inspect')
    def work_retest_inspect(project_id:str,session_id:str,body:WorkRetestInspect):
        return inspect_work_retest(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-retests/continue')
    async def work_retest_continue(project_id:str,session_id:str,body:WorkRetestContinue):
        return await continue_work_retest(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-roots/inspect')
    def work_roots_inspect(project_id:str,session_id:str,body:WorkRootsQuery):
        return inspect_work_roots(value,project_id,session_id,body)

    @app.post(root + '/{session_id}/work-maintenance/deliveries')
    def work_maintenance_deliveries(project_id:Id,session_id:Id,body:WorkMaintenanceBrowse):
        return WorkMaintenance(value).browse(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-maintenance/delivery')
    def work_maintenance_delivery(project_id:Id,session_id:Id,body:WorkMaintenanceDelivery):
        return WorkMaintenance(value).delivery(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-maintenance/prepare')
    def work_maintenance_prepare(project_id:Id,session_id:Id,body:WorkMaintenancePrepare):
        return WorkMaintenance(value).prepare(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-maintenance/links')
    def work_maintenance_change(project_id:Id,session_id:Id,body:WorkMaintenanceChange):
        return WorkMaintenance(value).change(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-maintenance/inspect')
    def work_maintenance_inspect(project_id:Id,session_id:Id,body:WorkMaintenanceInspect):
        return WorkMaintenance(value).inspect(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-maintenance/operations/inspect')
    def work_maintenance_operation(project_id:Id,session_id:Id,body:WorkMaintenanceOperation):
        return WorkMaintenance(value).operation(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/prepare')
    def work_delivery_prepare(project_id:str,session_id:str,body:WorkDeliveryPrepare):
        return WorkDeliveries(value).prepare(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/packages',status_code=201)
    def work_delivery_create(project_id:str,session_id:str,body:WorkDeliveryCreate):
        return WorkDeliveries(value).create(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/operations/inspect')
    def work_delivery_operation(project_id:str,session_id:str,body:WorkDeliveryLookup):
        return WorkDeliveries(value).operation(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/operations/recovery')
    def work_delivery_recovery(project_id:str,session_id:str,body:WorkDeliveryLookup):
        return WorkDeliveries(value).recovery(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/continue')
    def work_delivery_continue(project_id:str,session_id:str,body:WorkDeliveryLookup):
        return WorkDeliveries(value).continue_original(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/retain')
    def work_delivery_retain(project_id:str,session_id:str,body:WorkDeliveryRetain):
        return WorkDeliveries(value).retain(project_id,session_id,body)

    @app.post(root + '/{session_id}/work-deliveries/list')
    def work_delivery_list(project_id:str,session_id:str,body:WorkDeliveryList):
        return WorkDeliveries(value).listing(project_id,session_id,body)

    @app.get(root + '/{session_id}/work-deliveries/{operation_id}/archive')
    def work_delivery_download(project_id:str,session_id:str,operation_id:str,root_operation_id:str,request_sha256:str):
        from fastapi.responses import Response
        raw,receipt=WorkDeliveries(value).download(project_id,session_id,root_operation_id,operation_id,request_sha256)
        return Response(raw,media_type='application/zip',headers={
            'content-disposition':'attachment; filename="'+receipt['artifact']['filename']+'"',
            'x-cirven-artifact-sha256':receipt['artifact']['sha256'],'cache-control':'no-store'})

    @app.get(root + '/{session_id}/command-observations')
    def command_observations(project_id: str, session_id: str,
              session_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
              generation: int = Query(ge=0),
              workspace_id: str = Query(pattern=r'^[a-f0-9]{64}$'),
              binding_revision: int = Query(ge=1),
              binding_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
              call_id: str | None = Query(default=None, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]{0,191}$')):
        return value.command_observations(project_id, session_id, session_sha256=session_sha256,
            generation=generation, workspace_ref={'id': workspace_id, 'revision': binding_revision,
                'binding_sha256': binding_sha256}, **({'call_id': call_id} if call_id is not None else {}))

    @app.get(root + '/{session_id}/diffs/{diff_id}')
    def diff_file(project_id: str, session_id: str, diff_id: str,
                  session_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
                  generation: int = Query(ge=0),
                  workspace_id: str = Query(pattern=r'^[a-f0-9]{64}$'),
                  binding_revision: int = Query(ge=1),
                  binding_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
                  snapshot_sha256: str = Query(pattern=r'^[a-f0-9]{64}$'),
                  diff_sha256: str = Query(pattern=r'^[a-f0-9]{64}$')):
        from packages.domain.tasks import digest
        workspace = {'id': workspace_id, 'revision': binding_revision, 'binding_sha256': binding_sha256}
        scope = {'project_id': project_id,
                 'session_ref': {'id': session_id, 'revision': 1, 'sha256': session_sha256},
                 'generation': generation, 'workspace_ref': workspace}
        return value.diff_file(project_id, session_id, session_sha256=session_sha256, generation=generation,
            workspace_ref=workspace, snapshot_ref={'id': digest(scope), 'revision': 1, 'sha256': snapshot_sha256},
            diff_ref={'id': diff_id, 'revision': 1, 'sha256': diff_sha256})

    @app.post(root + "/{session_id}/interrupt")
    async def interrupt(project_id: str, session_id: str, body: InterruptSession):
        return await value.interrupt(project_id, session_id, body)

    @app.post(root + "/{session_id}/questions/{request_id}/reply")
    async def answer(project_id: str, session_id: str, request_id: str, body: QuestionReply):
        return await value.reply(project_id, session_id, request_id, body, kind="QUESTION")

    @app.post(root + "/{session_id}/questions/{request_id}/reject")
    async def reject(project_id: str, session_id: str, request_id: str, body: PendingControl):
        return await value.reply(project_id, session_id, request_id, body, kind="QUESTION", reject=True)

    @app.post(root + "/{session_id}/permissions/{request_id}/reply")
    async def permission(project_id: str, session_id: str, request_id: str, body: PermissionReply):
        return await value.reply(project_id, session_id, request_id, body, kind="PERMISSION")

    @app.post(root + "/{session_id}/disconnect")
    async def disconnect(project_id: str, session_id: str, body: SessionControl):
        return await value.controlled_disconnect(project_id, session_id, body)

    return value
