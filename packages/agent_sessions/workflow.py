"""Admission and live policy bridge for the separately packaged workflow plugin.

This module configures an original owner. It never runs a model or dispatches a
task. All credentials remain in the existing exact-profile provider relays.
"""
import asyncio
from datetime import datetime, timezone
import hashlib
import hmac
import json
import re
import secrets
import threading
import time
from urllib.parse import urlsplit

from packages.adapters.model_transport import private_model_redactions
from packages.adapters.opencode_workflow_plugin import prepare_workflow_plugin, write_workflow_policy
from packages.adapters.subscription_runtime import is_subscription
from packages.agent_sessions.wire import ProviderWire
from packages.domain.store import Conflict
from packages.domain.tasks import digest


def workflow_scope(owner, pid, request, main_profile, *, initial=False, validate_routes=True):
    reference = request.get('workflow_ref')
    extras = request.get('workflow_provider_refs', [])
    if reference is None:
        if extras:
            raise Conflict('Extra workflow models require an explicitly selected workflow')
        return None
    core = getattr(owner, 'dynamic_workflows', None)
    if core is None:
        raise Conflict('Dynamic Workflow is not installed')
    view = core.get(pid, reference['id'])
    if initial and view['workflow_ref'] != reference:
        raise Conflict('Selected workflow version changed; read it before opening a session')
    if len(view['plan']['nodes']) > 64:
        raise Conflict('This plugin registers at most 64 nodes; the full plan is retained')
    profiles = {main_profile.profile_id: main_profile}
    for ref in extras:
        profile = owner.providers.resolve(ref)
        if profile.profile_id in profiles:
            raise Conflict('Workflow model profiles must be unique')
        if (profile.executable != main_profile.executable or
                profile.executable_sha256 != main_profile.executable_sha256):
            raise Conflict('Workflow model profiles require the same qualified original CLI')
        if is_subscription(profile) or is_subscription(main_profile):
            raise Conflict('Mixing subscription accounts/API in one workflow is not qualified; no fallback was used')
        profiles[profile.profile_id] = profile
    if len({profile.provider_id for profile in profiles.values()}) != len(profiles):
        raise Conflict('Workflow profiles have conflicting provider namespaces')
    if request['network_mode'] == 'STRICT_OFFLINE' and any(
            urlsplit(profile.provider_url).hostname not in {'127.0.0.1', 'localhost', '::1'} for profile in profiles.values()):
        raise Conflict('Strict offline workflow requires explicitly local model endpoints')
    catalog = [{'id': profile.profile_id, 'provider_id': profile.provider_id, 'model_id': profile.model_id,
                'variants': [None, *getattr(profile, 'reasoning_efforts', ())], 'read_only': True} for profile in profiles.values()]
    nodes = [{'id': node['id'], 'title': node['title'], 'agent_policy': node['agent_policy']}
             for node in view['plan']['nodes']]
    unavailable_routes = []
    for node in nodes:
        if node['agent_policy']['model']['mode'] == 'MANUAL' and node['agent_policy']['model']['value'] not in profiles:
            unavailable_routes.append(node['id'])
            if validate_routes:
                raise Conflict('A pinned workflow model was not explicitly admitted to this session')
        if node['agent_policy']['depth']['mode'] == 'MANUAL':
            candidates = [route for route in catalog if node['agent_policy']['model']['mode'] == 'AUTO'
                          or route['id'] == node['agent_policy']['model']['value']]
            if not any(node['agent_policy']['depth']['value'] in route['variants'] for route in candidates):
                unavailable_routes.append(node['id'])
                if validate_routes:
                    raise Conflict('This configured model has no admitted reasoning-depth route')
    return {'view': view, 'profiles': profiles, 'catalog': catalog, 'nodes': nodes,
            'unavailable_routes': sorted(set(unavailable_routes)),
            'dependencies': [{'source': dep, 'target': node['id']} for node in view['plan']['nodes'] for dep in node['depends_on']]}


async def prepare_workflow(owner, manifest, context, config, binding):
    scope = await asyncio.to_thread(workflow_scope, owner, manifest['project_id'], manifest['request'], context['profile'])
    if scope is None:
        return
    pid, ident = manifest['project_id'], manifest['id']
    fingerprints = {key: profile.fingerprint for key, profile in scope['profiles'].items()}
    if fingerprints != manifest.get('workflow_provider_fingerprints'):
        raise Conflict('An admitted workflow model configuration or credential changed')
    context['workflow_wires'] = []
    for profile in scope['profiles'].values():
        if profile.profile_id == context['profile'].profile_id:
            continue
        wire = ProviderWire(profile, admit=lambda sha,input_bytes=0: owner._reserve_rpc(pid, ident, sha,expected_context=context,input_bytes=input_bytes),
            complete=lambda value: owner._rpc_complete(pid, ident, value),
            max_output_tokens=manifest['request']['max_output_tokens'],include_input_bytes=True).start()
        context['workflow_wires'].append(wire)
        context['secrets'].extend([wire.key, *private_model_redactions(profile)])
        config['provider'].update(owner._config(manifest, profile, binding, wire)['provider'])
        config['enabled_providers'].append(profile.provider_id)
    token = secrets.token_hex(48)
    context['workflow_event_token'] = token
    context['workflow_plan_token'] = secrets.token_hex(48)
    context['secrets'].extend([token, context['workflow_plan_token']])
    _, session_ref = owner._manifest(pid, ident)
    owner_ref = {'project_id': pid, 'session_ref': session_ref, 'generation': context['generation']}
    origin = getattr(owner, 'workflow_event_origin', None)
    prepared = await asyncio.to_thread(prepare_workflow_plugin,
        owner._path(pid, ident, 'runtime', 'workflow', 'generation-' + str(context['generation'])),
        owner_ref=owner_ref, catalog=scope['catalog'], nodes=scope['nodes'],
        plan_ref=scope['view']['workflow_ref'], dependencies=scope['dependencies'],
        sink_url=origin + '/api/dynamic-workflow/plugin-events' if origin else None,
        model_replan=bool(origin))
    context['workflow'] = prepared
    context['workflow_policy_lock'] = threading.RLock()
    context['workflow_id'] = scope['view']['id']
    context['workflow_plan_ref'] = scope['view']['workflow_ref']
    context['workflow_activation_status'] = 'NEXT_DELEGATION'
    policy_ref = prepared['admission']['policy_ref']
    context['workflow_policy_plans'] = {(policy_ref['revision'], policy_ref['sha256']): scope['view']['workflow_ref']}
    config['plugin'].extend(prepared['config_fragment']['plugin'])
    config['permission']['eeas_workflow_graph'] = 'allow'
    if origin:
        config['permission']['eeas_workflow_replan'] = 'allow'
        config['permission']['eeas_workflow_plan_operation'] = 'allow'
    for alias in prepared['aliases']:
        config['permission']['task'][alias['alias']] = 'ask'


def admitted_workflow_owner(owner, token, *, plan=False, read_only=False):
    if not isinstance(token, str) or not token.startswith('Bearer '):
        return None
    secret = token[7:]
    if not re.fullmatch(r'[a-f0-9]{96}', secret):
        return None
    for ident, context in list(owner.active.items()):
        expected = context.get('workflow_plan_token' if plan else 'workflow_event_token')
        if (expected and not context.get('closing') and context.get('process') is not None
                and context['process'].returncode is None and hmac.compare_digest(expected, secret)):
            try:
                claimed = context['workflow']['admission']['owner_ref']
                manifest, actual_ref = owner._manifest(claimed['project_id'], ident)
                state = owner._state(claimed['project_id'], ident)
                if (actual_ref != claimed['session_ref'] or state['generation'] != claimed['generation']
                        or context['generation'] != claimed['generation']):
                    continue
                if plan and not read_only and (owner.closed or state['owner_status'] != 'READY'
                        or state.get('observation_error') or not state.get('active_operation_id')
                        or datetime.fromisoformat(context['expires_at']) <= datetime.now(timezone.utc)
                        or not owner._configuration_current(manifest)):
                    continue
                return ident, context
            except Exception:
                continue
    return None


def admitted_event_owner(owner, token):
    return admitted_workflow_owner(owner, token)


def sync_workflow_policies(owner, pid, wid):
    """A committed user plan is independent of whether runtime sync succeeds."""
    results = []
    for ident, context in list(owner.active.items()):
        prepared = context.get('workflow')
        if context.get('workflow_id') != wid or not prepared or context.get('closing'):
            continue
        with context.setdefault('workflow_policy_lock', threading.RLock()):
            if context.get('closing') or owner.active.get(ident) is not context:
                continue
            try:
                # Read the current head while owning the entire file-CAS and
                # context update. A slower earlier sync must not restore v2
                # after a concurrent newer v3 has already become effective.
                manifest, _ = owner._manifest(pid, ident)
                scope = workflow_scope(owner, pid, manifest['request'], context['profile'], validate_routes=False)
                previous, current = context.get('workflow_plan_ref'), scope['view']['workflow_ref']
                if previous and (current['revision'] < previous['revision'] or
                        current['revision'] == previous['revision'] and current != previous):
                    raise Conflict('A runtime workflow policy cannot regress its plan basis')
                if previous != current or context.get('workflow_policy_sync_error'):
                    policy_ref = write_workflow_policy(prepared['admission'], nodes=scope['nodes'],
                        plan_ref=current, dependencies=scope['dependencies'],
                        expected_ref=prepared['admission']['policy_ref'])
                    prepared['admission']['policy_ref'] = policy_ref
                context['workflow_plan_ref'] = current
                policy_ref = prepared['admission']['policy_ref']
                context.setdefault('workflow_policy_plans', {})[(policy_ref['revision'], policy_ref['sha256'])] = current
                context.pop('workflow_policy_sync_error', None)
                registered = {item['node_id'] for item in prepared['aliases']}
                # Persist new human pins even when their account was not
                # admitted at startup. The original task hook refuses that
                # next delegation; already-running child model calls retain
                # their admitted provider instead of being globally blocked.
                status = ('RECONNECT_REQUIRED' if scope['unavailable_routes'] or
                          not {node['id'] for node in scope['nodes']} <= registered else 'NEXT_DELEGATION')
            except Exception:
                context['workflow_policy_sync_error'] = 'POLICY_SYNC_UNCONFIRMED'
                status = 'POLICY_SYNC_UNCONFIRMED'
            context['workflow_activation_status'] = status
        results.append({'session_id': ident, 'generation': context['generation'], 'status': status})
    return results


async def reconcile_workflow(owner, pid, ident, context, messages):
    """Associate exact task calls with their plan basis and native child facts.

    The plugin's selection log establishes node intent. Original task metadata
    and child assistant messages establish actual execution/model observations.
    A completed task remains RETURNED, never independently validated here.
    """
    if not context.get('workflow'):
        return
    try:
        projection = read_workflow_projection(owner, pid, ident)['projection']
        dispatches = {(item['session_id'], item['call_id']): item for item in projection['actual']['dispatches']}
        state = owner._state(pid, ident)
        current_operation = state.get('active_operation_id')
        context['workflow_evidence_pending'] = False
        seen = context.setdefault('workflow_observations_seen', set())
        if len(seen) >= 2048:
            owner._update(pid, ident, workflow_observation_error='CAPACITY_REACHED')
            return
        operations = context.setdefault('workflow_call_operations', {})
        for message in messages:
            for part in message.get('parts', []):
                if part.get('type') != 'tool' or part.get('tool') != 'task':
                    continue
                selected = dispatches.get((part.get('sessionID'), part.get('callID')))
                if selected is None:
                    continue
                tool_state = part.get('state', {})
                metadata = tool_state.get('metadata', {})
                child = metadata.get('sessionId')
                if child not in context['known_sessions'] or metadata.get('parentSessionId') != part.get('sessionID'):
                    continue
                call_id = part['callID']
                if current_operation:
                    operations.setdefault(call_id, current_operation)
                original_operation = operations.get(call_id)
                policy = selected['policy_ref']
                plan_ref = context.get('workflow_policy_plans', {}).get((policy['revision'], policy['sha256']))
                if not original_operation or plan_ref is None:
                    continue
                basis = digest([call_id, tool_state.get('status'), metadata, selected['id']])
                final_calls = context.setdefault('workflow_final_calls', {})
                if final_calls.get(call_id) == basis:
                    continue
                checks = context.setdefault('workflow_child_checks', {})
                previous_check = checks.get(call_id)
                if previous_check and previous_check[0] == basis and time.monotonic() - previous_check[1] < 1:
                    context['workflow_evidence_pending'] = context.get('workflow_evidence_pending') or previous_check[2]
                    continue
                child_messages = await owner._http(context, 'GET', '/session/' + child + '/message', params={'limit': '100'})
                if not isinstance(child_messages, list) or any(item.get('info', {}).get('sessionID') != child for item in child_messages):
                    raise Conflict('Workflow child evidence belongs to another original session')
                assistants = [item['info'] for item in child_messages if item.get('info', {}).get('role') == 'assistant']
                info = assistants[-1] if assistants else None
                terminal = tool_state.get('status') in {'completed', 'error'}
                deadline = context.setdefault('workflow_evidence_deadlines', {}).setdefault(call_id, time.monotonic() + 10) if terminal else 0
                pending_evidence = terminal and not (info and info.get('time', {}).get('completed')) and time.monotonic() < deadline
                context['workflow_evidence_pending'] = context.get('workflow_evidence_pending') or pending_evidence
                checks[call_id] = (basis, time.monotonic(), pending_evidence)
                fingerprint = digest([basis, info])
                if fingerprint in seen:
                    if terminal and info and info.get('time', {}).get('completed'):
                        final_calls[call_id] = basis
                    continue
                evidence = {'owner_operation': owner.operation(pid, ident, original_operation)['operation_ref'],
                            'native_task': owner._clean(context, part), 'child_assistant_info': owner._clean(context, info)}
                source_ref = {'id': 'wfobs_' + digest(evidence)[:48], 'revision': 1, 'sha256': digest(evidence)}
                evidence_path = owner._path(pid, ident, 'workflow-observations', source_ref['id'] + '.json')
                if not evidence_path.exists():
                    owner._write(evidence_path, evidence)
                elif owner._read(evidence_path) != evidence:
                    raise Conflict('Workflow observation identity changed')
                routing = {field: {'status': 'UNOBSERVED', 'effective': None, 'evidence_level': 'NONE', 'evidence_refs': []}
                           for field in ('model', 'depth')}
                if info:
                    model = selected['selected']
                    if info.get('providerID') == model['provider_id'] and info.get('modelID') == model['model_id']:
                        alias = next(row for row in context['workflow']['aliases'] if row['alias'] == selected['selected_alias'])
                        routing['model'] = {'status': 'OBSERVED', 'effective': alias['route_id'],
                                            'evidence_level': 'ASSISTANT_MESSAGE', 'evidence_refs': [source_ref]}
                    if isinstance(info.get('variant'), str) and info['variant']:
                        routing['depth'] = {'status': 'OBSERVED', 'effective': info['variant'],
                                            'evidence_level': 'ASSISTANT_MESSAGE', 'evidence_refs': [source_ref]}
                status = {'pending': 'RUNNING', 'running': 'RUNNING', 'completed': 'RETURNED', 'error': 'FAILED'}.get(tool_state.get('status'), 'UNKNOWN')
                from packages.domain.tasks import now
                observation = {'owner': {'project_id': pid, 'session_id': ident, 'operation_id': original_operation,
                                        'generation': context['generation']},
                    'status': status, 'observed_at': now(), 'source_ref': source_ref, 'coverage': 'PARTIAL', 'routing': routing}
                await asyncio.to_thread(owner.dynamic_workflows.attach_execution, pid, context['workflow_id'], selected['node_id'],
                    observation, plan_ref=plan_ref, operation_id='observe_' + digest([ident, context['generation'], fingerprint])[:48])
                seen.add(fingerprint)
                if terminal and info and info.get('time', {}).get('completed'):
                    final_calls[call_id] = basis
        if owner._state(pid, ident).get('workflow_observation_error'):
            owner._update(pid, ident, workflow_observation_error=None)
    except Exception as error:
        owner._update(pid, ident, workflow_observation_error=type(error).__name__)


def read_workflow_projection(owner, pid, ident):
    manifest, _ = owner._manifest(pid, ident)
    context = owner.active.get(ident)
    if context is None or not context.get('workflow'):
        return {'status': 'NOT_ACTIVE', 'workflow_ref': manifest['request'].get('workflow_ref'),
                'coverage': 'NO_ACTIVE_PLUGIN_PROJECTION', 'model_calls': 0}
    prepared = context['workflow']
    path = owner._path(pid, ident, 'runtime', 'workflow', 'generation-' + str(context['generation']), 'projection.json')
    if path.is_symlink() or not path.is_file() or path.stat().st_size > 1024 * 1024:
        raise Conflict('Workflow plugin projection is not yet readable')
    raw = path.read_bytes()
    projection = json.loads(raw)
    if (projection.get('owner_ref') != prepared['admission']['owner_ref']
            or projection.get('config_sha256') != prepared['admission']['config_sha256']):
        raise Conflict('Workflow plugin projection belongs to another owner')
    return {'status': 'PARTIAL_EVENT_OBSERVATION', 'projection': projection,
            'source_sha256': hashlib.sha256(raw).hexdigest(), 'model_calls': 0,
            'policy_sync_error': context.get('workflow_policy_sync_error'),
            'runtime_version': '1.19.0', 'standalone_plugin_id': 'eeas.dynamic-workflow'}
