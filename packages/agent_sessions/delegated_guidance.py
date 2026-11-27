"""Steer an exact foreground task on the current original root operation.

Only the original owner's monitor observes these receipts. No new runtime,
model selection, child polling loop or copied child transcript is introduced.
"""
import time
from datetime import datetime, timedelta, timezone

from packages.domain.store import Conflict
from packages.domain.tasks import canonical, digest, now
from packages.domain.context_read_budget import current_context_read_budget
from packages.domain.task_resource_budget import current_task_resource_budget
from .child_observations import ChildObservations, child_reference, IDENT


class DelegatedGuidance:
    def __init__(self, owner):
        self.owner = owner

    def _root_cohort(self, pid, sid, context, parent):
        owner = self.owner
        state = owner._state(pid, sid)
        _, actual = owner._manifest(pid, sid)
        if (parent.get('kind') != 'MESSAGE' or parent.get('status') != 'ACCEPTED'
                or parent.get('generation') != context['generation'] or parent.get('session_ref') != actual
                or parent.get('original_session_id') != context['session_id']
                or parent.get('native_dispatch_acknowledged') is not True
                or state.get('active_operation_id') != parent['id']):
            raise Conflict('Child guidance requires the exact acknowledged active root operation')
        records = owner._cohort_records(pid, sid, context, state, parent)
        if not records:
            raise Conflict('Original root input cohort is unavailable')
        return records

    def basis(self, pid, sid, context, parent, reference, messages, agent=None, *, _validated_root_records=None):
        records = self._root_cohort(pid, sid, context, parent) if _validated_root_records is None else _validated_root_records
        if _validated_root_records is None:self.validate_messages(messages, context['session_id'])
        parents = {record['native_message_id'] for record in records}
        matches = []
        for message in messages:
            info = message['info']
            if info['role'] != 'assistant' or info.get('parentID') not in parents:
                continue
            for part in message['parts']:
                status = part.get('state') or {}
                metadata = status.get('metadata') or {}
                arguments = status.get('input') or {}
                if (part.get('type') != 'tool' or part.get('tool') != 'task' or status.get('status') != 'running'
                        or metadata.get('sessionId') != reference['id']):
                    continue
                admitted = {'plan', 'build', 'explore', *[row['alias'] for row in context.get('workflow', {}).get('aliases', [])]}
                if (metadata.get('background') is True or arguments.get('background') is True
                        or metadata.get('parentSessionId', context['session_id']) != context['session_id']
                        or arguments.get('subagent_type') not in admitted
                        or agent is not None and arguments.get('subagent_type') != agent
                        or not IDENT.fullmatch(part.get('id', '')) or not IDENT.fullmatch(part.get('callID', ''))):
                    raise Conflict('Exact admitted foreground task configuration is required for child guidance')
                matches.append({'parent_operation_id': parent['id'], 'root_native_session_id': context['session_id'],
                    'root_user_message_id': info['parentID'], 'assistant_message_id': info['id'],
                    'part_id': part['id'], 'call_id': part['callID'], 'part_sha256': digest(part),
                    'child_ref': reference, 'agent': arguments['subagent_type'], 'foreground': True})
        if len(matches) != 1:
            raise Conflict('The selected child is not the exact running foreground task of this root input')
        return matches[0]

    @staticmethod
    def validate_messages(messages, native):
        if not isinstance(messages, list) or len(messages) > 200 or len(canonical(messages).encode()) > 4*1024*1024:
            raise Conflict('Original delegated message observation exceeds its bound')
        if len({row.get('info', {}).get('id') for row in messages}) != len(messages):
            raise Conflict('Original delegated message identity is duplicated')
        for start in range(0, len(messages), 40):
            ChildObservations._validate_messages(messages[start:start+40], native)

    def targets(self, pid, sid, context, messages):
        state = self.owner._state(pid, sid)
        key = state.get('active_operation_id')
        if not key or state.get('active_child_operation'):
            return []
        parent = self.owner.operation(pid, sid, key)
        if parent.get('kind') != 'MESSAGE' or parent.get('status') != 'ACCEPTED':
            return []
        try:
            records = self._root_cohort(pid, sid, context, parent)
        except Conflict:
            return []
        self.validate_messages(messages, context['session_id'])
        candidates = {part['state'].get('metadata', {}).get('sessionId') for message in messages for part in message['parts']
            if part.get('type') == 'tool' and part.get('tool') == 'task' and part.get('state', {}).get('status') == 'running'}
        if len(candidates) > 32:return []
        children = state.get('children', [])
        if not isinstance(children, list) or len(children) > 1000:
            raise Conflict('Original child directory exceeds its bound')
        output = []
        for child in children:
            if child.get('id') not in candidates:continue
            reference = child_reference(state, child)
            if reference is None:
                continue
            try:
                proof = self.basis(pid, sid, context, parent, reference, messages, _validated_root_records=records)
            except Conflict:
                continue
            output.append({'parent_operation_id': key, 'generation': context['generation'],
                'child_ref': reference, 'status': 'RUNNING_FOREGROUND', 'agent': proof['agent']})
        return output if len(output) <= 32 else []

    def records(self, pid, sid, context, parent):
        state = self.owner._state(pid, sid)
        ids = state.get('delegated_guidance_ids', []) if state.get('delegated_guidance_parent') == parent['id'] else []
        if not isinstance(ids, list) or len(ids) > 32 or len(set(ids)) != len(ids):
            raise Conflict('Original delegated guidance receipt bound or identities differ')
        records = [self.owner.operation(pid, sid, key) for key in ids]
        for record in records:
            request, proof = record.get('request') or {}, record.get('delegation_basis') or {}
            if (record.get('kind') != 'CHILD_GUIDANCE' or record.get('delegated_parent') is not True
                    or record.get('session_ref') != parent.get('session_ref') or record.get('generation') != context['generation']
                    or digest(request) != record.get('request_sha256') or request.get('child_ref') != record.get('child_ref')
                    or request.get('expected_active_operation_id') != parent['id']
                    or record.get('original_active_operation_id') != parent['id']
                    or record.get('root_original_session_id') != context['session_id']
                    or proof.get('parent_operation_id') != parent['id'] or proof.get('child_ref') != record.get('child_ref')
                    or proof.get('foreground') is not True or record.get('original_session_id') != record.get('child_ref', {}).get('id')):
                raise Conflict('Original delegated guidance receipt no longer identifies this root operation')
        return records

    async def messages(self, pid, sid, context, statuses, pending=()):
        state = self.owner._state(pid, sid)
        key = state.get('active_operation_id')
        if not key:
            return {}
        parent = self.owner.operation(pid, sid, key)
        records = self.records(pid, sid, context, parent)
        targets = sorted({record['original_session_id'] for record in records
            if record.get('native_post_attempted') is True and record.get('status') in {'ACCEPTED', 'UNKNOWN_OUTCOME'}})
        if len(targets) > 8:
            raise Conflict('Original delegated child observation allowance exceeded')
        deadline, result = time.monotonic()+6., {}
        for target in targets:
            if statuses.get(target, {'type': 'idle'}).get('type') != 'idle' and not any(row.get('sessionID') == target for row in pending):
                continue
            remaining = deadline-time.monotonic()
            if remaining <= 0:
                raise Conflict('Original delegated child observation deadline exceeded')
            messages = await self.owner._http(context, 'GET', '/session/'+target+'/message',
                params={'limit': '200'}, timeout=min(3., remaining))
            self.validate_messages(messages, target)
            budget = current_context_read_budget()
            if budget and current_task_resource_budget() is None: budget.charge(len(canonical(messages).encode()))
            result[target] = messages
        return result

    def human_wait(self, pid, sid, context, root_messages, observed, statuses, *, quiescent):
        """Pause only when current original tool calls prove all work is waiting.

        The owner's absolute TTL is never extended. Cached pending lists,
        another child's question and a busy parallel root tool prove no pause.
        """
        owner = self.owner
        state = owner._state(pid, sid)
        key = state.get('active_operation_id')
        if not key or not quiescent or context.get('interrupt_barrier'):
            return
        parent = owner.operation(pid, sid, key)
        if parent.get('status') != 'ACCEPTED':return
        records = self.records(pid, sid, context, parent)
        if not records:return
        requests = [*state.get('pending_questions', []), *state.get('pending_permissions', [])]
        targets = {record['original_session_id'] for record in records}
        grouped = {target: [] for target in targets}
        valid = bool(requests) and len(requests) <= 64
        for request in requests:
            native = request.get('native_request', {})
            target, tool = native.get('sessionID'), native.get('tool') or {}
            if target not in targets or not tool.get('messageID') or not tool.get('callID'):
                valid = False;break
            grouped[target].append((tool, request['request_ref']))
        blocked = set()
        if valid:
            for target, pending in grouped.items():
                if not pending:continue
                cohort = [record for record in records if record['original_session_id'] == target]
                messages = observed.get(target)
                if messages is None or any(record.get('native_dispatch_acknowledged') is not True for record in cohort):
                    valid = False;break
                parents = {record['native_message_id'] for record in cohort}|{record['delegation_basis']['child_user_message_id'] for record in cohort}
                expected = {(tool['messageID'], tool['callID']) for tool, _ in pending}
                active = set()
                for message in messages:
                    info = message['info']
                    if info['role'] != 'assistant' or info.get('time', {}).get('completed'):continue
                    if info.get('parentID') not in parents:valid = False;break
                    for part in message['parts']:
                        if part.get('type') == 'tool' and part.get('state', {}).get('status') in {'running', 'pending'}:
                            pair = (info['id'], part.get('callID'))
                            if part['state']['status'] != 'running' or pair not in expected or pair in active:valid = False;break
                            active.add(pair)
                        elif part.get('type') in {'text', 'reasoning'} and part.get('time', {}).get('start') and not part['time'].get('end'):
                            valid = False;break
                if not valid or not active or active != expected or len(expected) != len(pending):valid = False;break
                try:
                    proof = self.basis(pid, sid, context, parent, cohort[-1]['child_ref'], root_messages, cohort[-1]['agent'])
                except Conflict:
                    valid = False;break
                if proof['call_id'] != cohort[-1]['delegation_basis']['call_id']:valid = False;break
                blocked.add(target)
            if any(statuses.get(native, {'type': 'idle'}).get('type') != 'idle'
                    for native in context['known_sessions'] if native not in blocked|{context['session_id']}):valid = False
            # Root itself may only be awaiting the proven foreground task(s).
            running = []
            for message in root_messages:
                if message['info']['role'] != 'assistant' or message['info'].get('time', {}).get('completed'):continue
                for part in message['parts']:
                    if part.get('type') == 'tool' and part.get('state', {}).get('status') in {'running', 'pending'}:
                        running.append(part)
                    elif part.get('type') in {'text', 'reasoning'} and part.get('time', {}).get('start') and not part['time'].get('end'):
                        valid = False
            if not running or any(part.get('tool') != 'task' or part['state']['status'] != 'running'
                    or part['state'].get('metadata', {}).get('sessionId') not in blocked for part in running):valid = False
        previous, stamp = parent.get('human_wait', {}), datetime.now(timezone.utc)
        deadline = datetime.fromisoformat(parent['deadline'])
        if valid and blocked and (deadline > stamp or previous.get('status') == 'WAITING_FOR_USER'):
            refs = [request['request_ref'] for request in requests]
            if previous.get('status') == 'WAITING_FOR_USER' and previous.get('requests') == refs:return True
            owner._finish(pid, sid, key, parent['status'], human_wait={'status': 'WAITING_FOR_USER',
                'since': previous.get('since', stamp.isoformat()), 'requests': refs,
                'coverage': 'EXACT_DELEGATED_FOREGROUND_CHILD_REQUESTS', 'deadline_before_wait': parent['deadline'], 'owner_ttl_extended': False})
            return True
        elif previous.get('status') == 'WAITING_FOR_USER' and previous.get('coverage') == 'EXACT_DELEGATED_FOREGROUND_CHILD_REQUESTS':
            elapsed = max(0., (stamp-datetime.fromisoformat(previous['since'])).total_seconds())
            extension = elapsed if previous.get('deadline_before_wait') == parent['deadline'] else 0.
            resumed = min(datetime.fromisoformat(context['expires_at']), deadline+timedelta(seconds=extension))
            owner._finish(pid, sid, key, parent['status'], deadline=resumed.isoformat(), human_wait={
                'status': 'RUNNING', 'completed_wait_seconds': elapsed, 'resumed_at': stamp.isoformat(), 'owner_ttl_extended': False})
            return True
        return False

    def observe(self, pid, sid, context, observed, statuses, *, quiescent, barrier, can_release_interrupt):
        """True holds root settlement. Child result is never a root answer."""
        owner = self.owner
        state = owner._state(pid, sid)
        key = state.get('active_operation_id')
        if not key:
            return False
        parent = owner.operation(pid, sid, key)
        records = self.records(pid, sid, context, parent)
        if not records:
            return False
        if not quiescent:
            return True
        if any(statuses.get(native, {'type': 'idle'}).get('type') != 'idle' for native in context['known_sessions']):
            return True
        if state.get('pending_questions') or state.get('pending_permissions'):
            return True
        stopped = False
        if (barrier and can_release_interrupt and context.get('interrupt_barrier') is barrier
                and barrier.get('target_operation_id') == key and barrier.get('generation') == context['generation']):
            stop = owner.operation(pid, sid, barrier['operation_id'])
            stopped = bool(stop.get('status') == 'RETURNED' and stop.get('original_abort_acknowledged') is True
                and stop.get('target_native_session_id') == context['session_id'] and stop.get('generation') == context['generation'])
        active = [record for record in records if record.get('native_post_attempted') is True
            and record.get('status') in {'ACCEPTED', 'UNKNOWN_OUTCOME'}]
        results = []
        for target in sorted({record['original_session_id'] for record in active}):
            cohort = [record for record in active if record['original_session_id'] == target]
            messages = observed.get(target)
            if messages is None:
                return True
            self.validate_messages(messages, target)
            assistants = [row for row in messages if row['info']['role'] == 'assistant']
            if any(not row['info'].get('time', {}).get('completed') or any(part.get('type') == 'tool'
                    and part.get('state', {}).get('status') in {'pending', 'running'} for part in row['parts']) for row in assistants):
                return True
            if not stopped:
                if any(record.get('status') == 'UNKNOWN_OUTCOME' or record.get('native_dispatch_acknowledged') is not True for record in cohort):
                    return True
                for record in cohort:
                    users = [row for row in messages if row['info']['role'] == 'user' and row['info']['id'] == record.get('native_message_id')]
                    if (len(users) != 1 or ''.join(part.get('text', '') for part in users[0]['parts']
                            if part.get('type') == 'text' and not part.get('synthetic')) != record['request']['raw_text']):
                        return True
                users = [row for row in messages if row['info']['role'] == 'user']
                if not users or users[-1]['info']['id'] != cohort[-1]['native_message_id']:
                    return True
            finals = [row for row in assistants if row['info'].get('parentID') == cohort[-1].get('native_message_id')
                and (row['info'].get('finish') == 'stop' or row['info'].get('error'))]
            if not finals and not stopped:
                return True
            errors = [row['info']['error'] for row in finals if row['info'].get('error')]
            status = 'CANCELLED' if stopped else 'FAILED' if errors else 'RETURNED'
            results.append({'original_session_id': target, 'latest_operation_id': cohort[-1]['id'],
                'operation_ids': [row['id'] for row in cohort], 'status': status, 'messages_sha256': digest(messages),
                'native_assistant_ids': [row['info']['id'] for row in finals]})
        # Publish only after every child has evidence; a failed publication never
        # permits a second POST. UNKNOWN remains UNKNOWN even after an exact stop.
        for result in results:
            cohort = [record for record in active if record['original_session_id'] == result['original_session_id']]
            for record in cohort:
                if record['status'] == 'ACCEPTED':
                    owner._finish(pid, sid, record['id'], result['status'] if record is cohort[-1] else 'COHORT_RETURNED',
                        linked_latest_operation_id=result['latest_operation_id'], messages_sha256=result['messages_sha256'],
                        native_assistant_ids=result['native_assistant_ids'], semantic_acceptance=False, independent_verification='NOT_RUN')
        if results:
            owner._finish(pid, sid, key, parent['status'], delegated_child_guidance_ids=[record['id'] for record in records],
                delegated_child_results=results, semantic_acceptance=False, independent_verification='NOT_RUN')
            owner._update(pid, sid, delegated_child_observation={'parent_operation_id': key, 'observed_at': now(),
                'status': 'STOP_OBSERVED_UNKNOWN_PRESERVED' if stopped else 'EXACT_CHILD_RESULTS_OBSERVED',
                'operation_ids': [record['id'] for record in records], 'automatic_replay': False})
        return False
