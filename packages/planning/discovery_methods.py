"""Original practice notes with primary references, usable without the web."""
import hashlib
from pathlib import Path


METHODS = [
    ('problem-discovery','问题发现','DISCOVER','https://www.designcouncil.org.uk/resources/the-double-diamond/'),
    ('needs-elicitation','需求引出与确认','DEFINE','https://cpre.ireb.org/en/concept/requirements-elicitation'),
    ('competitive-evidence','竞品与替代方案核对','RESEARCH','https://www.productboard.com/glossary/competitor-analysis/'),
    ('engineering-requirements','工程约束与验收','SPECIFY','https://www.nasa.gov/reference/4-2-technical-requirements-definition/'),
    ('diagram-evidence','结构模型与来源图','SPECIFY','https://js.cytoscape.org/'),
    ('prompt-orchestration','任务提示词编排','PLAN','https://github.com/github/spec-kit'),
]


def method_pack(source_root,phase=None):
    result=[]
    for ident,title,stage,reference in METHODS:
        path=Path(source_root)/'packages/planning/skills'/ident/'SKILL.md'
        raw=path.read_bytes()
        result.append({'id':ident,'title':title,'phase':stage,'sha256':hashlib.sha256(raw).hexdigest(),
                       'source_reference':reference,'instruction':raw.decode(),
                       'authorship':'EEAS_ORIGINAL_PRACTICE_NOTES','qualification':'METHOD_GUIDANCE_NOT_DOMAIN_CERTIFICATION'})
    # All entry points can revisit any step; guidance is not a project-type preset.
    return {'schema_version':'eeas.discovery-methods.v1','methods':result,
            'current_phase':phase,'offline_available':True,'external_calls':0}
