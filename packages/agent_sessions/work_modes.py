"""Goal-oriented primary mode on the original OpenCode loop, not a scheduler.

The exact loaded native definition is checked before admitting this mode. It
inherits the existing Build permission map and the same provider/budget owner.
SDK todo observations remain model progress, never independent validation.
"""
from packages.domain.tasks import digest

WORKFLOW_AGENT = 'workflow'
WORKFLOW_VERSION = 'eeas.original-workflow-mode.v1'
WORKFLOW_PROMPT = '''You are the goal-oriented Workflow primary agent in this existing conversation.
Carry the user's goal through investigation, execution and checking using this original OpenCode loop.
For a simple task, act directly. For substantial work, use todowrite to maintain a concise plan,
adapt it to evidence, and preserve completed work and the user's corrections. Do not require the
user to construct a task graph or repeat their goal in another page.
Use only already admitted tools, models, directories and permissions. A plan, mode selection,
todo item or reference is not authorization. Ask a focused question only when a missing answer
actually prevents safe progress; continue independent useful work. Existing question and
permission tools provide the interaction. Do not invent delegation or a dependency graph.
Check results with available evidence. Distinguish your todo/claims from independent acceptance,
and returned tool results from verified outcomes. Keep exact sources, versions and manual edits.
On failure preserve the input, original operation and partial results. Never resend an unknown
external action, silently retry a non-idempotent operation, reconnect, or expand a budget.
Stay within this owner's shared call/token/deadline limits. Report blockers and remaining work
honestly. A later mode preference applies to later messages, not to the current frozen task.
'''
WORKFLOW_POLICY_SHA256 = digest({'version': WORKFLOW_VERSION, 'prompt': WORKFLOW_PROMPT})


def workflow_mode_admitted(context):
    definition = context.get('workflow_mode') if context else None
    return bool(isinstance(definition, dict) and definition.get('version') == WORKFLOW_VERSION
                and definition.get('policy_sha256') == WORKFLOW_POLICY_SHA256
                and definition.get('execution_loop') == 'ORIGINAL_OPENCODE'
                and isinstance(definition.get('native_definition_sha256'), str)
                and len(definition['native_definition_sha256']) == 64)


def native_workflow_definition(model, permission, steps):
    # Keep the same admitted permission map as Build, including optional plugin
    # additions made before the final immutable native configuration is written.
    return {'mode': 'primary', 'description': 'Pursue a goal with adaptive todos, existing tools and checks',
            'prompt': WORKFLOW_PROMPT, 'model': model, 'steps': steps, 'permission': permission}


def observed_workflow_definition(expected, effective, agents):
    """Only actual native config + registry observations establish availability."""
    if not expected:
        return None
    configured = effective.get('agent', {}).get(WORKFLOW_AGENT, {})
    if any(configured.get(key) != expected[key] for key in ('mode', 'prompt', 'model', 'permission', 'steps')):
        return None
    rows = [row for row in agents if row.get('name') == WORKFLOW_AGENT]
    if len(rows) != 1 or rows[0].get('mode') != 'primary' or rows[0].get('prompt') != WORKFLOW_PROMPT:
        return None
    return {'version': WORKFLOW_VERSION, 'policy_sha256': WORKFLOW_POLICY_SHA256,
            'native_definition_sha256': digest(expected), 'execution_loop': 'ORIGINAL_OPENCODE'}
