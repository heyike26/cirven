"""Reusable deterministic planning tools; semantic review stays with the agent."""
from packages.domain.planning_schema import PlanningCandidate
from packages.domain.planning_proposals import PlanningProposals
from packages.planning.spec_kit import load_templates, POLICY


TOOLS=[
    {'id':'review_sources','title':'资料与来源审阅','description':'读取完整选中资料、覆盖状态及原始SHA，不签多模态理解。'},
    {'id':'validate_network','title':'任务依赖检查','description':'检查节点唯一性、父子关系、依赖及疑问作用范围，拒绝环。'},
    {'id':'acceptance_coverage','title':'交付与验收覆盖','description':'找出缺少交付物、验收及来源的节点，不判断实际业务正确性。'},
    {'id':'pending_questions','title':'待确认事项','description':'逐项获取所需资料、影响节点、回答和明确决议。'},
    {'id':'prompt_preview','title':'Harness提示词预览','description':'读当前计划的逐节点提示词，不确认计划或启动任务。'},
    {'id':'planning_templates','title':'规格与澄清模板','description':'读取固定版本GitHub Spec Kit原始模板和EEAS共创规则。'}]


class PlanningToolkit:
    def __init__(self,proposals):self.proposals=proposals

    def validate_network(self,candidate):
        value=PlanningCandidate.model_validate(candidate)
        return {'valid':True,'node_count':len(value.nodes),'question_count':len(value.questions),
                'flow':PlanningProposals.flow(value.model_dump()),'semantic_correctness':'UNASSESSED'}

    def acceptance_coverage(self,candidate):
        value=PlanningCandidate.model_validate(candidate)
        rows=[{'node_id':n.id,'missing':([ 'outputs' ] if not n.outputs else [])+
            (['acceptance'] if not n.acceptance else [])+(['source_basis'] if not n.source_anchors and n.origin!='USER_STATEMENT' else [])}
              for n in value.nodes if n.kind=='TASK']
        return {'nodes':rows,'complete_nodes':sum(not r['missing'] for r in rows),'total_tasks':len(rows),
                'business_criteria_quality':'UNASSESSED','verified_execution':False}

    def review_sources(self,pid,proposal_id,revision=None):
        value=self.proposals.get(pid,proposal_id,revision)
        return {'proposal_ref':{'id':proposal_id,'revision':value['revision'],'sha256':value['sha256']},
            'source_receipt':value['payload']['source_receipt'],'review_status':value['review_status'],
            'instructions':POLICY,'semantic_completeness':'UNASSESSED'}

    def pending_questions(self,pid,proposal_id,revision=None):return self.proposals.pending(pid,proposal_id,revision)

    def prompt_preview(self,pid,proposal_id,revision=None):return self.proposals.harness(pid,proposal_id,revision)

    def planning_templates(self):
        value=load_templates(self.proposals.source_root)
        return {'component':value['manifest'],'manifest_sha256':value['manifest_sha256'],
                'templates':value['contents'],'instructions':POLICY,'runtime_hooks_executed':False}
