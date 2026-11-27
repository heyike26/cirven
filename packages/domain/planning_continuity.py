"""Conservative structural decision continuity; never a semantic impact claim."""
from collections import deque

SCHEMA = 'cirven.plan-decision-continuity.v1'
GLOBAL_FIELDS = ('title', 'summary', 'goals', 'constraints', 'assumptions', 'risks')


def _indexes(candidate):
    return ({n['id']: n for n in candidate['nodes']},
            {q['id']: q for q in candidate['questions']})


def _successors(*candidates):
    result = {}
    for candidate in candidates:
        for node in candidate['nodes']:
            for predecessor in [*node['depends_on'], *([node['parent_id']] if node['parent_id'] else [])]:
                result.setdefault(predecessor, set()).add(node['id'])
    return result


def _closure(seeds, successors):
    result = set(seeds)
    pending = deque(result)
    while pending:
        for ident in successors.get(pending.popleft(), ()):
            if ident not in result:
                result.add(ident)
                pending.append(ident)
    return result


def structural_impact(before, after):
    """Use both graphs: removing an edge must not hide its old downstream scope."""
    old_nodes, old_questions = _indexes(before)
    new_nodes, new_questions = _indexes(after)
    changed_nodes = {ident for ident in old_nodes.keys() | new_nodes.keys()
                     if old_nodes.get(ident) != new_nodes.get(ident)}
    changed_questions = {ident for ident in old_questions.keys() | new_questions.keys()
                         if old_questions.get(ident) != new_questions.get(ident)}
    global_fields = [name for name in GLOBAL_FIELDS if before.get(name) != after.get(name)]
    seeds = set(changed_nodes)
    for ident in changed_questions:
        for question in (old_questions.get(ident), new_questions.get(ident)):
            if question is None:
                continue
            if not question['affects_node_ids']:
                if 'global_question_scope' not in global_fields:
                    global_fields.append('global_question_scope')
            seeds.update(question['affects_node_ids'])
    affected = _closure(seeds, _successors(before, after))
    if global_fields:
        affected.update(old_nodes.keys() | new_nodes.keys())
    return {'changed_question_ids': sorted(changed_questions),
            'changed_node_ids': sorted(changed_nodes),
            'affected_node_ids': sorted(affected),
            'global_changed_fields': global_fields,
            'semantic_coverage': 'STRUCTURAL_ONLY_NOT_EXHAUSTIVE'}


def question_unchanged(before, after, ident, impact=None):
    old_nodes, old_questions = _indexes(before)
    new_nodes, new_questions = _indexes(after)
    old, new = old_questions.get(ident), new_questions.get(ident)
    if old is None or old != new:
        return False
    impact = structural_impact(before, after) if impact is None else impact
    if impact['global_changed_fields']:
        return False
    # An empty scope is global, not an independent question with no dependencies.
    if not old['affects_node_ids']:
        return not (impact['changed_node_ids'] or impact['changed_question_ids'])
    scope = _closure(old['affects_node_ids'], _successors(before, after))
    return (scope <= old_nodes.keys() and scope <= new_nodes.keys()
            and not scope.intersection(impact['affected_node_ids']))


def include_decision_impact(impact, candidate, changed_questions):
    """Confirmation display scope; this does not invalidate more retained choices."""
    nodes, questions = _indexes(candidate)
    seeds = set()
    for ident in changed_questions:
        seeds.update(questions[ident]['affects_node_ids'] or nodes.keys())
    return {**impact,
            'changed_question_ids': sorted(set(impact['changed_question_ids']) | set(changed_questions)),
            'affected_node_ids': sorted(set(impact['affected_node_ids']) | _closure(seeds, _successors(candidate)))}
