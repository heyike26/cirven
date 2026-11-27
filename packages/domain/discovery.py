"""Evidence-led professional discovery and requirement confirmation.

OpenCode owns reasoning. This ledger owns revisions and human decisions.
"""
import asyncio
import json
import uuid
from types import SimpleNamespace
from pydantic import ValidationError
from packages.domain.intake import IntakeInput, canonical, digest
from packages.domain.questions import stamp
from packages.domain.store import Conflict, InvalidInput, NotFound
from packages.domain.discovery_schema import DiscoveryCandidate
from packages.domain.planning_schema import ProposalInput


UNKNOWN_OPERATION_MESSAGE = '本次模型处理结果尚未确认，原记录和输入已保留。请核查原操作，不要重复发送。'


METHOD_POLICY = '''专业共创方法：先确认工作情境、实际经历、当前替代办法和证据；用JTBD理解期望结果，
Double Diamond发现/定义问题，OST组织结果/机会/多方案/假设验证，IREB/IIBA引出并确认需求，
系统工程检查约束、接口、可行性及可测验收。按决策风险动态选访谈、资料核查、计算或小试验，
不用固定问卷套项目类型。已有需求或任务文件只补实际缺口，不强制重走市场发现。
只使用收到的资料和用户陈述。专业知识补充须明确依据，缺领域资料提出具体请求。
事实/用户陈述/模型建议/未经验证假设分开。SOURCE_FACT条目必须有真实精确source_anchors，
引用范围和SHA仅从给定chunks复制，不能编造。引用正确不证明语义正确，设计仍需人工审查。
竞品维度由用户目标决定，包含直接/间接/人工/不采用替代。官方声称和用户实测必须引用
已捕获资料；没有材料只标UNKNOWN或INFERENCE，没提不等于不支持，不编造价格、排名或分数。
图来自结构模型，节点边需要claim_ids或来源；SOURCE_FACT图元素可以直接引用来源，
或仅引用全部已有真实来源的SOURCE_FACT条目，由宿主确定性解析并留痕；混合建议不能继承事实锚点。
解析引用不证明图的语义正确。架构/流程可有回路，思维图是单根树。
每步输出具体问题、为什么问/影响什么、所需资料及下一步行动/预期产物。
要求确认与方案确认分开；未答可形成阶段产物及待确认项。绝不自称用户已确认或任务已执行。
详细需求应有对象、行为、工况、可观察验收；没有依据不擅自填性能/设备/预算数值。
''' 


class DiscoveryWorkbench:
    def __init__(self, store, proposals, *, recover_operations=True):
        self.store, self.proposals = store, proposals
        self.transport = proposals.transport
        proposals.discovery_authority = self
        self.template_profiles = None
        self.feedback_authority = None
        self.observation_authority = None
        if not recover_operations:return
        # A fresh controller cannot know whether an interrupted model call
        # completed. Record uncertainty without repeating it or taking its PID.
        with store.connect() as c:
            for row in c.execute("SELECT * FROM discovery_operations WHERE status='PROCESSING'").fetchall():
                result={'status':'UNKNOWN_OUTCOME','operation_id':row['operation_id'],
                        'message':'控制器已重启，原操作结果未知；不会自动再次调用模型。'}
                self._finish(c,row['project_id'],row['operation_id'],result,'UNKNOWN_OUTCOME')

    @staticmethod
    def _checked(row):
        payload=json.loads(row['payload'])
        if digest(payload)!=row['sha256']:raise Conflict('Discovery integrity check failed')
        return payload

    @classmethod
    def _version(cls,c,pid,sid,revision=None):
        head=c.execute('SELECT * FROM discovery_sessions WHERE id=? AND project_id=?',(sid,pid)).fetchone()
        if head is None:raise NotFound('Discovery session not found in this project')
        rev=head['current_revision'] if revision is None else revision
        row=c.execute('SELECT * FROM discovery_versions WHERE session_id=? AND revision=?',(sid,rev)).fetchone()
        if row is None:raise NotFound('Discovery revision does not exist')
        payload=cls._checked(row)
        if (payload['project_id'],payload['id'],payload['revision'])!=(pid,sid,rev):
            raise Conflict('Discovery scope check failed')
        return {'id':sid,'project_id':pid,'revision':rev,'current_revision':head['current_revision'],
                'sha256':row['sha256'],'created_at':row['created_at'],'payload':payload}

    @staticmethod
    def _append(c,pid,sid,payload):
        rev=payload['revision']
        if rev==1:c.execute('INSERT INTO discovery_sessions VALUES (?,?,?)',(sid,pid,rev))
        elif c.execute('UPDATE discovery_sessions SET current_revision=? WHERE id=? AND project_id=? AND current_revision=?',
                       (rev,sid,pid,rev-1)).rowcount!=1:raise Conflict('Discovery changed; reload current revision')
        c.execute('INSERT INTO discovery_versions VALUES (?,?,?,?,?)',(sid,rev,canonical(payload),digest(payload),stamp()))

    @staticmethod
    def _event(c,pid,sid,op,kind,**detail):
        value={'kind':kind,'created_at':stamp(),**detail}
        c.execute('INSERT INTO discovery_events(project_id,session_id,operation_id,payload,sha256) VALUES (?,?,?,?,?)',
                  (pid,sid,op,canonical(value),digest(value)))

    @staticmethod
    def _replay(c,pid,op,sha):
        row=c.execute('SELECT * FROM discovery_operations WHERE project_id=? AND operation_id=?',(pid,op)).fetchone()
        if row is None:return None
        if row['request_sha256']!=sha:raise Conflict('IDEMPOTENCY_CONFLICT: operation binds different input')
        if row['result'] is None:return {'operation_id':op,'session_id':row['session_id'],'status':row['status']}
        value=json.loads(row['result'])
        if digest(value)!=row['result_sha256']:raise Conflict('Operation result integrity check failed')
        return value

    @staticmethod
    def _claim(c,pid,sid,op,kind,sha):
        if c.execute("SELECT 1 FROM discovery_operations WHERE project_id=? AND session_id=? AND status='PROCESSING'",(pid,sid)).fetchone():
            raise Conflict('A discovery operation is already running; inspect or cancel it')
        c.execute('INSERT INTO discovery_operations VALUES (?,?,?,?,?,?,?,?)',(pid,op,sid,kind,sha,'PROCESSING',None,None))

    @staticmethod
    def _finish(c,pid,op,result,status='COMPLETED'):
        if c.execute("UPDATE discovery_operations SET status=?,result=?,result_sha256=? WHERE project_id=? AND operation_id=? AND status='PROCESSING'",
                     (status,canonical(result),digest(result),pid,op)).rowcount!=1:
            raise Conflict('Discovery operation is already terminal')

    @staticmethod
    def _expect(view,revision):
        if view['current_revision']!=revision or view['revision']!=revision:
            raise Conflict('Discovery changed; reload current revision')

    def _current(self,pid,payload):
        try:
            return self.proposals.source_current(pid,payload['source_receipt']) and self.proposals._workspace_current(pid,payload['workspace_ref'])
        except (Conflict,InvalidInput,NotFound,OSError):return False

    def _sources(self,pid,sid,op,history,materials):
        # Consolidate only real user messages; model guesses are never turned
        # into source documents to make an uncited assertion look grounded.
        transcript='\n\n'.join(f"{r['kind']}：{r['text']}" for r in history)
        if len(transcript)>64000:raise InvalidInput('Transcript exceeds 64000 characters; explicitly start a linked new session')
        doc=self.store.save_document(pid,'用户陈述-'+sid+'-'+digest(op)[:12]+'.txt',transcript.encode(),
                                     'discovery-user-statements:'+sid)
        refs=[{'document_id':doc['id'],'revision':doc['revision'],'sha256':doc['sha256'],'kind':'NOTES'},*materials]
        intake=self.proposals.intakes.save(pid,IntakeInput.model_validate({'idempotency_key':'discovery-'+digest([pid,op])[:40],
                                                   'materials':refs,'purpose':'专业需求共创：'+sid}))
        sources=self.proposals._sources(intake)
        return sources,refs

    @staticmethod
    def _validate(candidate,sources):
        items=[*candidate.claims,*candidate.questions]
        for comp in candidate.competitors:items.extend(comp.observations)
        for graph in candidate.graphs:items.extend([*graph.nodes,*graph.edges])
        # Reuse the already tested byte/range authority for every element.
        from packages.domain.planning_proposals import PlanningProposals
        PlanningProposals._validate_sources(SimpleNamespace(nodes=items,questions=[]),sources)

    def _view(self,c,pid,view):
        payload=view['payload'];resolutions={r['question_id']:r for r in payload.get('resolutions',[])}
        answers=[]
        for row in c.execute('SELECT * FROM discovery_answers WHERE session_id=? ORDER BY rowid',(view['id'],)):
            answer=self._checked(row)
            answers.append({**answer,'accepted':resolutions.get(answer['question_id'],{}).get('answer_id')==answer['id']})
        pending=[q for q in payload['candidate']['questions'] if q['id'] not in resolutions]
        current=self._current(pid,payload)
        required=[x for x in payload['candidate']['claims'] if x['category']=='REQUIREMENT']
        readiness={'requirements_confirmed':payload['status']=='CONFIRMED' and current,
                   'can_plan':payload['status']=='CONFIRMED' and current and bool(required),
                   'pending_count':len(pending),'blocking_count':sum(q['blocking'] for q in pending),
                   'review_status':'CURRENT' if current else 'NEEDS_REVIEW',
                   'semantic_verification':'HUMAN_REVIEW_REQUIRED','execution_authorized':False}
        return {**view,'answers':answers,'source_current':current,'readiness':readiness}

    def get(self,pid,sid,revision=None):
        with self.store.connect() as c:return self._view(c,pid,self._version(c,pid,sid,revision))

    def _answers_snapshot(self,c,pid,view,resolutions):
        """Freeze real answer bytes and their exact questions at human review.

        Answers are separate append-only records. Looking them up later from a
        historical view would also expose replies written after confirmation.
        """
        accepted={r['answer_id'] for r in resolutions if r['mode']=='ANSWER_ACCEPTED'}
        current={q['id']:q for q in view['payload']['candidate']['questions']}
        result=[]
        rows=c.execute('SELECT * FROM discovery_answers WHERE session_id=? AND session_revision<=? ORDER BY rowid',
                       (view['id'],view['revision'])).fetchall()
        for row in rows:
            answer=self._checked(row)
            original=self._version(c,pid,view['id'],answer['session_revision'])
            question=next((q for q in original['payload']['candidate']['questions'] if q['id']==answer['question_id']),None)
            if question is None or digest(question)!=answer['question_sha256']:
                raise Conflict('Answer does not match its frozen question')
            applies=answer['question_id'] in current and digest(current[answer['question_id']])==answer['question_sha256']
            result.append({**answer,'answer_sha256':row['sha256'],'question':question,
                'applies_to_confirmed_question':applies,'accepted':answer['id'] in accepted and applies})
        return result

    def list(self,pid):
        self.store.project(pid)
        with self.store.connect() as c:
            return [self._view(c,pid,self._version(c,pid,r['id'])) for r in c.execute('SELECT id FROM discovery_sessions WHERE project_id=? ORDER BY rowid DESC',(pid,)).fetchall()]

    def _write(self,pid,sid,body,kind,mutate):
        sha=digest({'session_id':sid,**body.model_dump(exclude={'operation_id'})})
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            replay=self._replay(c,pid,body.operation_id,sha)
            if replay is not None:return replay
            view=self._version(c,pid,sid);self._expect(view,body.expected_revision)
            payload=mutate(c,view)
            self._claim(c,pid,sid,body.operation_id,kind,sha)
            self._append(c,pid,sid,payload)
            result=self._view(c,pid,self._version(c,pid,sid))
            self._event(c,pid,sid,body.operation_id,kind,revision=result['revision'])
            self._finish(c,pid,body.operation_id,result)
            return result

    def create(self,pid,body):
        self.store.project(pid);sha=digest(body.model_dump(exclude={'operation_id'}));sid=str(uuid.uuid4())
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');replay=self._replay(c,pid,body.operation_id,sha)
            if replay is not None:return replay
            self._claim(c,pid,sid,body.operation_id,'CREATED',sha)
        try:
            history=[{'kind':'USER_STATEMENT','text':body.statement,'created_at':stamp()}]
            sources,refs=self._sources(pid,sid,body.operation_id,history,[m.model_dump() for m in body.materials])
            candidate=DiscoveryCandidate.model_validate({'title':body.title,'phase':'DISCOVER' if body.entry_mode=='PROBLEM' else 'DEFINE',
                'summary':body.statement,'claims':[{'id':'initial-statement','text':body.statement,
                    'category':'PROBLEM' if body.entry_mode=='PROBLEM' else 'NEED','origin':'USER_STATEMENT'}],
                'next_actions':[{'id':'review-context','method':'需求引出/资料分析','action':'核对实际工作情境和资料，明确目标、边界及如何判断成功。',
                    'reason':'原始陈述尚未形成经确认的可测需求。','expected_artifact':'带来源的需求、关键问题与验证办法'}]})
            workspace=body.workspace_ref.model_dump() if body.workspace_ref else None
            if not self.proposals._workspace_current(pid,workspace):raise Conflict('Workspace binding changed')
            payload={'id':sid,'project_id':pid,'revision':1,'entry_mode':body.entry_mode,'status':'DRAFT',
                     'candidate':candidate.model_dump(),'history':history,'materials':[m.model_dump() for m in body.materials],
                     'source_materials':refs,'source_receipt':sources,'workspace_ref':workspace,'accepted_claim_ids':[],
                     'accepted_graph_ids':[],'resolutions':[],'proposal_ref':None,'execution':{'mode':'USER_INPUT','model_calls':0}}
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE');self._append(c,pid,sid,payload)
                result=self._view(c,pid,self._version(c,pid,sid))
                self._event(c,pid,sid,body.operation_id,'CREATED',revision=1)
                self._finish(c,pid,body.operation_id,result)
            return result
        except (Conflict,InvalidInput,NotFound,ValueError) as exc:
            return self._failed(pid,sid,body.operation_id,exc)

    def _failed(self,pid,sid,op,exc,status='FAILED'):
        # A domain Conflict does not prove that the dispatched native request
        # failed. Resolve only this operation's existing execution; no dispatch.
        observed=self.operation(pid,op)
        if observed['status'] in {'UNKNOWN_OUTCOME','CANCELLED'}:
            status=observed['status']
        result={'id':sid,'status':status,'operation_id':op,'reason':type(exc).__name__,
                'message':UNKNOWN_OPERATION_MESSAGE if status=='UNKNOWN_OUTCOME' else
                    '本次操作未完成；请查看资料、模型配置或结构化结果。原记录和失败保留，不自动再次调用。'}
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');self._event(c,pid,sid,op,status,reason=type(exc).__name__)
            self._finish(c,pid,op,result,status)
        return result

    def edit(self,pid,sid,body):
        def mutate(c,view):
            self._validate(body.candidate,view['payload']['source_receipt'])
            return {**view['payload'],'revision':view['revision']+1,'candidate':body.candidate.model_dump(),
                    'status':'DRAFT','accepted_claim_ids':[],'accepted_graph_ids':[],'resolutions':[],
                    'proposal_ref':None,'change_reason':body.change_reason,'evidence_derivation_receipt':None}
        return self._write(pid,sid,body,'EDITED',mutate)

    def answer(self,pid,sid,body):
        sha=digest({'session_id':sid,**body.model_dump(exclude={'operation_id'})})
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');replay=self._replay(c,pid,body.operation_id,sha)
            if replay is not None:return replay
            view=self._version(c,pid,sid);self._expect(view,body.expected_revision)
            if body.question_id not in {q['id'] for q in view['payload']['candidate']['questions']}:
                raise InvalidInput('Question does not exist in this revision')
            self._claim(c,pid,sid,body.operation_id,'ANSWERED',sha)
            question=next(q for q in view['payload']['candidate']['questions'] if q['id']==body.question_id)
            aid=str(uuid.uuid4());result={'id':aid,'session_id':sid,'session_revision':view['revision'],'question_sha256':digest(question),
                'question_id':body.question_id,'text':body.text,'created_at':stamp(),'accepted':False}
            c.execute('INSERT INTO discovery_answers VALUES (?,?,?,?,?,?)',(aid,sid,view['revision'],body.question_id,canonical(result),digest(result)))
            self._event(c,pid,sid,body.operation_id,'ANSWERED',answer_id=aid)
            self._finish(c,pid,body.operation_id,result)
            return result

    def confirm(self,pid,sid,body):
        def mutate(c,view):
            p=view['payload']
            if view['sha256']!=body.session_sha256:raise Conflict('Discovery SHA changed')
            if not self._current(pid,p):raise Conflict('Source review required before confirmation')
            claims={x['id']:x for x in p['candidate']['claims']}
            if not set(body.accepted_claim_ids)<=claims.keys():raise InvalidInput('Unknown accepted requirement')
            required=[claims[i] for i in body.accepted_claim_ids if claims[i]['category']=='REQUIREMENT']
            if not required or any(not r['acceptance'] for r in required):
                raise Conflict('At least one accepted requirement needs observable acceptance criteria')
            if not set(body.accepted_graph_ids)<={g['id'] for g in p['candidate']['graphs']}:
                raise InvalidInput('Unknown accepted diagram')
            questions={q['id']:q for q in p['candidate']['questions']};resolved=set()
            for r in body.resolutions:
                if r.question_id not in questions:raise InvalidInput('Unknown question resolution')
                if r.mode=='ANSWER_ACCEPTED':
                    row=c.execute('SELECT * FROM discovery_answers WHERE id=? AND session_id=? AND session_revision=? AND question_id=?',
                                  (r.answer_id,sid,view['revision'],r.question_id)).fetchone()
                    if row is None:raise Conflict('Resolution needs an exact answer from this question revision')
                    self._checked(row)
                resolved.add(r.question_id)
            if any(q['blocking'] and q['id'] not in resolved for q in questions.values()):
                raise Conflict('Blocking questions require accepted answers or explicit assumptions')
            decisions=[r.model_dump() for r in body.resolutions]
            return {**p,'revision':view['revision']+1,'status':'CONFIRMED','accepted_claim_ids':body.accepted_claim_ids,
                    'accepted_graph_ids':body.accepted_graph_ids,'resolutions':decisions,
                    'answers_snapshot':self._answers_snapshot(c,pid,view,decisions),
                    'confirmation':{'basis_revision':view['revision'],'basis_sha256':view['sha256'],'execution_authorized':False}}
        return self._write(pid,sid,body,'REQUIREMENTS_CONFIRMED',mutate)

    def _start(self,pid,sid,body,kind):
        request=body.model_dump(exclude={'operation_id'})
        if request.get('coverage_contract') is None:request.pop('coverage_contract',None)
        sha=digest({'session_id':sid,**request})
        with self.store.connect() as c:
            c.execute('BEGIN IMMEDIATE');replay=self._replay(c,pid,body.operation_id,sha)
            if replay is not None:return None,replay
            view=self._version(c,pid,sid);self._expect(view,body.expected_revision)
            if not self._current(pid,view['payload']):
                feedback=(getattr(body,'feedback_ref',None) or getattr(body,'feedback_refs',None))
                if feedback:
                    raise Conflict('Feedback source changed; review current materials and explicitly locate a new annotation')
                if not (kind=='ADVANCE' and body.materials is not None):
                    raise Conflict('Selected source changed; review or select current materials')
            self._claim(c,pid,sid,body.operation_id,kind,sha)
            self._event(c,pid,sid,body.operation_id,kind+'_REQUESTED',revision=view['revision'])
        return view,None

    async def advance(self,pid,sid,body):
        view,replay=self._start(pid,sid,body,'ADVANCE')
        if replay is not None:return replay
        try:
            p=view['payload'];history=[*p['history'],{'kind':'USER_MESSAGE','text':body.message,'created_at':stamp()}]
            feedback=[]
            refs=[body.feedback_ref] if body.feedback_ref else body.feedback_refs
            if refs:
                if self.feedback_authority is None:raise Conflict('Workbench feedback service unavailable')
                for ref in refs:
                    item=self.feedback_authority.resolve(pid,ref.model_dump(),view)
                    feedback.append(item)
                    history.append({'kind':'USER_FEEDBACK','text':canonical(item['payload']),'created_at':stamp()})
            with self.store.connect() as c:
                for row in c.execute('SELECT * FROM discovery_answers WHERE session_id=? AND session_revision=?',(sid,view['revision'])):
                    answer=self._checked(row);history.append({'kind':'USER_ANSWER','text':answer['question_id']+'：'+answer['text'],'created_at':answer['created_at']})
            materials=[m.model_dump() for m in body.materials] if body.materials is not None else p['materials']
            sources,refs=self._sources(pid,sid,body.operation_id,history,materials)
            observations=[]
            bitmaps=list({digest(f['payload']['selection']['bitmap_ref']):f['payload']['selection']['bitmap_ref']
                for f in feedback if f['payload']['selection'].get('bitmap_ref')}.values())
            for f in feedback:
                selection=f['payload']['selection']
                if selection.get('ocr_ref'):
                    if self.observation_authority is None:raise Conflict('Offline OCR observation service unavailable')
                    observations.append(self.observation_authority.resolve(pid,selection['ocr_ref'],selection.get('bitmap_ref')))
            if bitmaps:
                profile=self.transport.provider_registry.resolve(body.provider_profile_ref)
                if not profile.supports_images:
                    if self.observation_authority is None:raise Conflict('Configure image input or the offline OCR component')
                    for bitmap in bitmaps:
                        if not any(o['payload']['bitmap_ref']==bitmap for o in observations):
                            observations.append(await asyncio.to_thread(self.observation_authority.recognize,pid,bitmap))
                    bitmaps=[]
            if observations:
                sources['documents'][0]['local_ocr_observations']=observations
                sources['sha256']=digest({k:sources[k] for k in ['documents','coverage']})
            workspace=body.workspace_ref.model_dump() if body.workspace_ref else p['workspace_ref']
            if workspace is None:raise Conflict('Select a local workspace before calling OpenCode')
            if not self.proposals._workspace_current(pid,workspace):raise Conflict('Workspace binding changed')
            from packages.planning.discovery_methods import method_pack
            methods=method_pack(self.proposals.source_root,p['candidate']['phase'])
            prompt=METHOD_POLICY+'\n固定方法与审查Skill：'+canonical(methods)
            prompt+='\n入口：'+p['entry_mode']+'\n已有建议（不是事实）：'+canonical(p['candidate'])
            prompt+='\n用户最新要求：'+body.message+'\n可选目标阶段：'+str(body.target_phase or '按证据选择')
            if feedback:
                prompt+='\n当前精确版本的用户批注：'+canonical([f['payload'] for f in feedback])
                prompt+='\n圈画是视口坐标，仅辅助定位；优先明确node_ids/文字意见。未能定位时提出问题，不伪称看懂。'
            if observations:
                prompt+='\n本地OCR已识别的批注来源（只证明观察内容，不等于已保存文档或工程事实；低置信/不明确部分提问）：'+canonical(observations)
            request={'project_id':pid,'operation_id':'disc-'+digest([pid,body.operation_id])[:40],'prompt':prompt,
                'source_receipt':sources,'workspace_ref':workspace,'provider_profile_ref':body.provider_profile_ref,
                'budget':body.budget.model_dump(),'timeout_seconds':body.budget.timeout_seconds,
                'needs_tools':False,'output_schema':DiscoveryCandidate.model_json_schema()}
            if bitmaps:
                request['artifact_images']=bitmaps
            execution=await self.transport.propose(request)
            if execution.get('status') not in {'RETURNED','COMPLETED','SUCCEEDED','PROPOSED'}:
                raise Conflict('OpenCode did not return complete structured discovery')
            if execution.get('source_receipt_sha256')!=sources['sha256']:raise Conflict('Native source receipt changed')
            from packages.domain.discovery_evidence import resolve_graph_evidence
            resolved, evidence_receipt = resolve_graph_evidence(execution['candidate_json'], sources)
            candidate=DiscoveryCandidate.model_validate(resolved);self._validate(candidate,sources)
            evidence_receipt['validated_candidate_sha256']=digest(candidate.model_dump())
            if not self.proposals.source_current(pid,sources):raise Conflict('Sources changed during reasoning')
            payload={**p,'revision':view['revision']+1,'status':'DRAFT','candidate':candidate.model_dump(),'history':history,
                    'materials':materials,'source_materials':refs,'source_receipt':sources,'workspace_ref':workspace,
                    'accepted_claim_ids':[],'accepted_graph_ids':[],'resolutions':[],'proposal_ref':None,'execution':execution,
                    'evidence_derivation_receipt':evidence_receipt,
                    'method_receipt':[{'id':m['id'],'sha256':m['sha256'],'source_reference':m['source_reference']} for m in methods['methods']]}
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE');self._append(c,pid,sid,payload)
                result=self._view(c,pid,self._version(c,pid,sid));self._event(c,pid,sid,body.operation_id,'ADVANCE_RETURNED',revision=result['revision'])
                self._finish(c,pid,body.operation_id,result)
            return result
        except asyncio.CancelledError as exc:
            self._failed(pid,sid,body.operation_id,exc,'UNKNOWN_OUTCOME');raise
        except (Conflict,InvalidInput,NotFound,ValidationError,ValueError) as exc:
            return self._failed(pid,sid,body.operation_id,exc)

    async def plan(self,pid,sid,body):
        # Gate before reserving/calling a model: existing task files do not
        # bypass explicit requirements confirmation.
        request=body.model_dump(exclude={'operation_id'})
        if request.get('coverage_contract') is None:request.pop('coverage_contract',None)
        sha=digest({'session_id':sid,**request})
        with self.store.connect() as c:
            replay=self._replay(c,pid,body.operation_id,sha)
            if replay is not None:return replay
        old=self.get(pid,sid)
        if not old['readiness']['can_plan']:raise Conflict('Confirm current requirements before task decomposition')
        view,replay=self._start(pid,sid,body,'PLAN')
        if replay is not None:return replay
        try:
            p=view['payload'];basis={'id':sid,'revision':view['revision'],'sha256':view['sha256']}
            accepted=[c for c in p['candidate']['claims'] if c['id'] in p['accepted_claim_ids']]
            prompt='根据已确认要求形成详细任务书和任务依赖网络；不自动执行。\n'+METHOD_POLICY
            baseline={'kind':'USER_CONFIRMED_REQUIREMENTS_NOT_EXECUTION_AUTHORIZATION','discovery_ref':basis,
                'accepted_requirements':accepted,'question_resolutions':p['resolutions'],'real_user_history':p['history'],
                'questions':p['candidate']['questions'],
                'questions_and_answers':p.get('answers_snapshot',old['answers']),
                'unaccepted_answers_are_not_requirements':True,
                'competitor_observations_semantics_not_independently_verified':p['candidate']['competitors'],
                'designs':[g for g in p['candidate']['graphs'] if g['id'] in p['accepted_graph_ids']],
                'original_source_receipt_sha256':p['source_receipt']['sha256'],'model_suggestions_are_not_source_facts':True}
            if body.experience_ref:
                if self.template_profiles is None:raise Conflict('Template experience service unavailable')
                baseline['approved_writing_experience_not_project_facts']=self.template_profiles.instructions(pid,body.experience_ref.model_dump())
            # Keep the public prompt short without dropping the actual baseline.
            # It is an authored snapshot of explicit human acceptance, not a
            # fabricated source. Original selected materials remain alongside.
            raw=('# 明确接受的需求与决议\n\n```json\n'+canonical(baseline)+'\n```\n').encode()
            doc=self.store.save_document(pid,'已确认要求-'+sid+'-'+str(view['revision'])+'.md',raw,
                                          'discovery-confirmed-baseline:'+sid+':'+str(view['revision'])+':'+view['sha256'])
            refs=[{'document_id':doc['id'],'revision':doc['revision'],'sha256':doc['sha256'],'kind':'DESIGN_DOCUMENT'},*p['materials']]
            prompt+='\n以附带的明确接受需求快照为要求基线，原始材料并列核对；不将其中设计/竞品推断变成外部事实。'
            result=await self.proposals.create(pid,ProposalInput.model_validate({'operation_id':'discplan-'+digest([pid,body.operation_id])[:40],
                'prompt':prompt,'materials':refs,'workspace_ref':p['workspace_ref'],
                'provider_profile_ref':body.provider_profile_ref,'budget':body.budget.model_dump(),'discovery_ref':basis,
                'coverage_contract':body.coverage_contract}))
            if result.get('status') in {'FAILED','PROCESSING'}:raise Conflict('Task decomposition did not complete')
            ref={'id':result['id'],'revision':result['revision'],'sha256':result['sha256']}
            payload={**p,'revision':view['revision']+1,'proposal_ref':ref}
            with self.store.connect() as c:
                c.execute('BEGIN IMMEDIATE');self._append(c,pid,sid,payload)
                session=self._view(c,pid,self._version(c,pid,sid));value={'session':session,'proposal_ref':ref,'proposal':result}
                self._event(c,pid,sid,body.operation_id,'PLAN_RETURNED',proposal_ref=ref)
                self._finish(c,pid,body.operation_id,value)
            return value
        except asyncio.CancelledError as exc:
            self._failed(pid,sid,body.operation_id,exc,'UNKNOWN_OUTCOME');raise
        except (Conflict,InvalidInput,NotFound,ValidationError,ValueError) as exc:
            return self._failed(pid,sid,body.operation_id,exc)

    def recoverable_operations(self,pid,sid,before=None,limit=20):
        if type(limit) is not int or not 1<=limit<=20 or before is not None and (type(before) is not int or before<1):
            raise InvalidInput('Invalid operation recovery page')
        with self.store.connect() as c:
            self._version(c,pid,sid)
            rows=c.execute('SELECT rowid AS cursor,operation_id FROM discovery_operations '
                'WHERE project_id=? AND session_id=? AND kind IN (\'ADVANCE\',\'PLAN\') '
                'AND (? IS NULL OR rowid<?) ORDER BY rowid DESC LIMIT ?',
                (pid,sid,before,before,limit+1)).fetchall()
            count=c.execute('SELECT count(*) FROM discovery_operations WHERE project_id=? AND session_id=? '
                'AND kind IN (\'ADVANCE\',\'PLAN\') AND rowid<=?',
                (pid,sid,rows[0]['cursor'])).fetchone()[0] if rows else 0
            headers=[]
            for index,row in enumerate(rows[:limit]):
                value=self.operation(pid,row['operation_id'],include_events=False)
                if value['status'] not in {'PROCESSING','UNKNOWN_OUTCOME'}:continue
                event=c.execute('SELECT payload,sha256 FROM discovery_events WHERE project_id=? AND session_id=? '
                    'AND operation_id=? ORDER BY seq LIMIT 1',(pid,sid,row['operation_id'])).fetchone()
                detail=self._checked(event) if event else {}
                headers.append({k:value[k] for k in ['operation_id','session_id','kind','status','native_execution'] if k in value}|
                    {'created_at':detail.get('created_at'),'basis_revision':detail.get('revision'),'sequence':count-index})
            return {'operations':headers,'next_before':rows[limit-1]['cursor'] if len(rows)>limit else None}

    def operation(self,pid,op,after=0,*,include_events=True):
        self.store.project(pid)
        with self.store.connect() as c:
            row=c.execute('SELECT * FROM discovery_operations WHERE project_id=? AND operation_id=?',(pid,op)).fetchone()
            if row is None:raise NotFound('Discovery operation not found')
            value={k:row[k] for k in ['operation_id','session_id','kind','status']}
            event=c.execute('SELECT payload,sha256 FROM discovery_events WHERE project_id=? AND session_id=? '
                'AND operation_id=? ORDER BY seq LIMIT 1',(pid,row['session_id'],op)).fetchone()
            value['basis_revision']=self._checked(event).get('revision') if event else None
            if row['result'] is not None:
                result=json.loads(row['result'])
                if digest(result)!=row['result_sha256']:raise Conflict('Operation integrity check failed')
                value['result']=result
            native_op=('discplan-' if row['kind']=='PLAN' else 'disc-')+digest([pid,op])[:40]
            native=c.execute('SELECT id FROM agent_executions WHERE project_id=? AND operation_id=?',(pid,native_op)).fetchone()
            if native:
                inspected=self.transport.inspect(pid,native['id'])
                value['native_execution']={k:inspected[k] for k in ['id','execution_status','session_id']}
                value['events']=self.transport.stream_events(pid,native['id'],after) if include_events else []
                if row['status'] in {'PROCESSING','UNKNOWN_OUTCOME','FAILED'}:
                    native_status=inspected['execution_status']
                    if native_status in {'UNKNOWN_OUTCOME','TIMED_OUT','INTERRUPTED'}:
                        value['status']='UNKNOWN_OUTCOME'
                    elif native_status in {'CANCELLED','FAILED'}:
                        value['status']=native_status
            else:value['events']=[]
            if value['status']=='UNKNOWN_OUTCOME':
                # Legacy FAILED/result bytes stay immutable; the read view
                # exposes the more precise native outcome and safe next step.
                value['message']=UNKNOWN_OPERATION_MESSAGE
            elif isinstance(value.get('result'),dict) and isinstance(value['result'].get('message'),str):
                value['message']=value['result']['message']
            return value

    async def cancel(self,pid,op):
        value=self.operation(pid,op)
        if not value.get('native_execution'):raise Conflict('No native owner has been dispatched yet')
        return await self.transport.request_cancel(pid,value['native_execution']['id'])

    def pending(self,pid,sid,revision=None):
        view=self.get(pid,sid,revision);p=view['payload'];resolved={r['question_id'] for r in p['resolutions']}
        questions=[]
        for q in p['candidate']['questions']:
            if q['id'] in resolved:continue
            answers=[a for a in view['answers'] if a['question_id']==q['id'] and a.get('question_sha256')==digest(q)]
            questions.append({**q,'answers':answers,'status':'ANSWERED_PENDING_ACCEPTANCE' if answers else 'AWAITING_ANSWER'})
        return {'session_ref':{'id':sid,'revision':view['revision'],'sha256':view['sha256']},'source_sha256':p['source_receipt']['sha256'],
                'source_current':view['source_current'],'questions':questions,'pending_count':len(questions),'silence_is_approval':False}

    def pending_markdown(self,pid,sid,revision=None):
        value=self.pending(pid,sid,revision);lines=['# 待确认事项','',f"需求版本：{value['session_ref']['revision']}",f"来源SHA：{value['source_sha256']}",'']
        for q in value['questions']:
            lines.extend(['## '+q['text'],'','原因：'+q['reason'],'状态：'+q['status'],'所需资料：'+'；'.join(q['requested_materials']),
                          '影响要求：'+'、'.join(q['affects_claim_ids'])])
            lines.extend('已有答复（未接受）：'+a['text'] for a in q['answers']);lines.append('')
        if not value['questions']:lines.append('本版本没有尚未裁决的问题；不代表工程已验证完成。')
        return '\n'.join(lines)+'\n'
