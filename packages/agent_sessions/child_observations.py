"""Explicit child reads through the original owner or original saved events.

No owner construction, prompt, transcript copy or independent polling loop.
"""
import re

from packages.domain.store import Conflict, NotFound
from packages.domain.tasks import digest, canonical
from packages.domain.context_read_budget import current_context_read_budget
from packages.domain.task_resource_budget import current_task_resource_budget

IDENT = re.compile(r'^[A-Za-z0-9_-]{1,100}$')


def child_reference(state, child):
    root = state.get('original_session_id')
    if (not isinstance(root, str) or not IDENT.fullmatch(root) or not isinstance(child, dict)
            or not isinstance(child.get('id'), str) or not IDENT.fullmatch(child['id'])
            or child.get('parentID') != root or child['id'] == root):
        return None
    # Mutable titles/timestamps must not retire a private draft or retarget a
    # continuation. Parent and exact original directory remain pinned.
    return {'id': child['id'], 'root_native_session_id': root,
        'sha256': digest({key:child.get(key) for key in ('id','parentID','directory')})}


class ChildObservations:
    def __init__(self, owner):
        self.owner = owner

    def _basis(self, pid, sid, reference, generation, child_ref):
        owner = self.owner
        manifest, actual = owner._manifest(pid, sid, reference)
        if manifest.get('native_review_admission') or manifest.get('native_message_admission'):
            raise Conflict('Private native task children use their existing frozen protocol')
        state = owner._state(pid, sid)
        if state.get('generation') != generation or set(child_ref) != {'id', 'root_native_session_id', 'sha256'}:
            raise Conflict('Original child generation or reference differs')
        children = state.get('children', [])
        if not isinstance(children, list) or len(children) > 1000 or any(not isinstance(child, dict) for child in children):
            raise Conflict('Original child directory exceeds its bound')
        matches = [child for child in children if child.get('id') == child_ref['id']]
        if len(matches) != 1 or child_reference(state, matches[0]) != child_ref:
            raise Conflict('Original child lineage or saved metadata changed')
        root = state.get('original_session') or {}
        if root.get('id') not in (None, state['original_session_id']):
            raise Conflict('Original parent session identity differs')
        if root.get('directory') is not None and matches[0].get('directory') != root['directory']:
            raise Conflict('Original child directory differs from its parent')
        return manifest, actual, state, matches[0]

    async def read(self, pid, sid, reference, generation, child_ref):
        owner = self.owner
        manifest, actual, state, child = self._basis(pid, sid, reference, generation, child_ref)
        context = owner.active.get(sid)
        live = bool(context and not context.get('closing') and context['process'].returncode is None)
        if live:
            _, admitted = owner._context(pid, sid, actual, generation, intent='OBSERVE')
            async with admitted['lock']:
                _, current = owner._context(pid, sid, actual, generation, intent='OBSERVE')
                if current is not admitted:
                    raise Conflict('Original child reader owner changed')
                self._basis(pid, sid, actual, generation, child_ref)
                native = await owner._http(current, 'GET', '/session/'+child_ref['id'], timeout=3)
                if (native.get('id') != child_ref['id'] or native.get('parentID') != child_ref['root_native_session_id']
                        or native.get('directory') != current['directory']):
                    raise Conflict('Live child belongs to another parent or workspace')
                messages = await owner._http(current, 'GET', '/session/'+child_ref['id']+'/message',
                    params={'limit':'40'}, timeout=3)
                self._validate_messages(messages, child_ref['id'])
                _, final = owner._context(pid, sid, actual, generation, intent='OBSERVE')
                if final is not current:
                    raise Conflict('Original child owner changed during read')
                self._basis(pid, sid, actual, generation, child_ref)
                messages = owner._clean(current, messages)
                budget = current_context_read_budget()
                if budget and current_task_resource_budget() is None:
                    budget.charge(len(canonical(native).encode()))
                    budget.charge(len(canonical(messages).encode()))
                coverage = {'source':'ORIGINAL_OWNER_CHILD_HTTP_GET', 'messages_limit':40,
                    'partial':len(messages) == 40, 'history_complete':False, 'missing_events':0,
                    'live_child_sha256':digest(native), 'source_generation':generation,
                    'source_generation_means':'OBSERVATION_OWNER_NOT_MESSAGE_BIRTH'}
        else:
            messages, coverage = self._saved(pid, sid, child_ref)
        return {'kind':'ORIGINAL_CHILD_VIEW', 'scope':{'project_id':pid,'session_ref':actual,'generation':generation},
            'child_ref':child_ref, 'title':child.get('title'), 'messages':messages, 'coverage':coverage,
            'observation_is_current':live, 'read_only':True, 'model_calls':0, 'native_posts':0,
            'new_owner_started':False, 'transcript_copied':False,
            'execution_authorized':False, 'child_continuation':'SEPARATE_EXACT_USER_COMMAND_REQUIRED'}

    @staticmethod
    def _validate_messages(messages, native):
        if not isinstance(messages, list) or len(messages)>40:
            raise Conflict('Child message observation exceeds its bound')
        seen = set()
        for message in messages:
            info = message.get('info', {}) if isinstance(message, dict) else {}
            if (not isinstance(info, dict) or not isinstance(info.get('id'), str) or not IDENT.fullmatch(info['id']) or info['id'] in seen
                    or info.get('sessionID') != native or info.get('role') not in {'user','assistant'}
                    or not isinstance(message.get('parts'), list) or len(message['parts'])>256):
                raise Conflict('Child message identity or part budget differs')
            seen.add(info['id'])
            if any(not isinstance(part, dict) or part.get('sessionID') != native or part.get('messageID') != info['id']
                    for part in message['parts']):
                raise Conflict('Child part belongs to another message or session')

    async def diffs(self,pid,sid,reference,generation,child_ref,workspace_ref):
        """Historical child snapshots, never a present disk reconstruction."""
        from packages.agent_sessions.models import WorkspaceRef
        from packages.agent_sessions.diffs import project_diffs
        from packages.adapters.opencode_change_review import historical_admissions,read_history
        manifest,actual,state,_=self._basis(pid,sid,reference,generation,child_ref)
        workspace=WorkspaceRef.model_validate(workspace_ref).model_dump()
        if manifest['request']['workspace_ref']!=workspace:
            raise Conflict('Child diff workspace differs from its original owner')
        binding=self.owner.workspaces.get(pid,workspace['id'],workspace['revision'])
        if binding['binding_sha256']!=workspace['binding_sha256'] or binding['root_identity']!=manifest['directory_identity']:
            raise Conflict('Child diff registered workspace changed')
        view=await self.read(pid,sid,actual,generation,child_ref)
        observed={'project_id':pid,'session_ref':actual,'generation':generation,'workspace_ref':workspace,
            'original_session_id':child_ref['id'],'root_original_session_id':child_ref['root_native_session_id'],
            'messages':view['messages'],'observation_at':state.get('observation_at')}
        full,reasons=[],[]
        if 'WORKSPACE_WRITE' in manifest['request'].get('allowed_effects',[]):
            try:
                for admission in historical_admissions(self.owner._path(pid,sid,'runtime'),observed):
                    source={**observed,'generation':admission['scope']['generation']}
                    entries,why=read_history(admission,source,data_dir=self.owner.data_dir)
                    for entry in entries:
                        entry.update(source_generation=source['generation'],source_scope={key:source[key]
                            for key in ('project_id','session_ref','generation','workspace_ref')})
                    full.extend(entries[:128-len(full)]);reasons.extend(why)
                    if len(full)>=128:
                        reasons.append('FULL_FILE_COUNT_LIMIT');break
            except (Conflict,NotFound,OSError,KeyError,TypeError,ValueError):
                reasons.append('CHILD_FULL_FILE_HISTORY_UNAVAILABLE')
        if view['coverage'].get('partial'):
            reasons.append('CHILD_MESSAGE_HISTORY_PARTIAL')
        result=project_diffs(observed,binding,path_checker=self.owner.workspaces._private,
            full_entries=full,full_reason_codes=reasons)
        directory,content=result
        original=directory['snapshot_ref']
        directory['child_ref']=child_ref
        directory['snapshot_ref']={'id':digest({'scope':directory['scope'],'child_ref':child_ref}),'revision':1,
            'sha256':digest({'original_snapshot_ref':original,'child_ref':child_ref})}
        directory['coverage']['child_observation']=view['coverage']
        self._basis(pid,sid,actual,generation,child_ref)
        return directory,content

    def _saved(self, pid, sid, child_ref):
        # Reuse the existing bounded original-source reader. Original immutable
        # events remain the sole text source, including while disconnected.
        from apps.api.conversation_history import ReadBudget
        budget = ReadBudget()
        with self.owner.store.connect() as db:
            infos = db.execute('SELECT * FROM original_message_locations WHERE project_id=? AND session_id=? '
                'AND native_session_id=? AND slot_id="@info" AND kind="INFO" ORDER BY event_seq DESC LIMIT 41',
                (pid,sid,child_ref['id'])).fetchall()
            gap = db.execute('SELECT reason FROM original_message_location_gaps WHERE project_id=? AND session_id=?',
                (pid,sid)).fetchone()
            grouped = []
            for info in infos[:40]:
                slots = db.execute('SELECT * FROM original_message_locations WHERE project_id=? AND session_id=? '
                    'AND native_session_id=? AND message_id=? AND slot_id!="@info" '
                    'ORDER BY event_seq LIMIT 65', (pid,sid,child_ref['id'],info['message_id'])).fetchall()
                grouped.append((info,slots))
        def original(row, field):
            event = budget.file(self.owner._path(pid,sid,'events',f"{row['event_seq']:010d}.json"))
            if (digest(event) != row['event_sha256'] or event.get('seq') != row['event_seq']
                    or event.get('type') != 'ORIGINAL_EVENT' or event.get('original_native_replay') is not False):
                raise Conflict('Saved child event identity differs')
            native = event['payload']['native_payload']
            expected = 'message.updated' if field == 'info' else 'message.part.updated'
            value = native['properties'][field]
            if native.get('type') != expected or digest(value) != row['value_sha256']:
                raise Conflict('Saved child slot does not match its original event')
            return value
        messages, omitted, generations = [], 0, set()
        for info,slots in reversed(grouped):
            try:
                if len(slots)>64 or any(row['kind']=='DIRTY' for row in slots):
                    raise Conflict('Child message part coverage is incomplete')
                header = original(info,'info')
                parts = [original(row,'part') for row in slots if row['kind'] in {'TEXT','OTHER'}]
                message = {'info':header, 'parts':parts}
                self._validate_messages([message],child_ref['id'])
                if header['id'] != info['message_id'] or any(part.get('id') != row['slot_id']
                        for part,row in zip(parts,[row for row in slots if row['kind'] in {'TEXT','OTHER'}])):
                    raise Conflict('Saved child message or part identity differs')
                messages.append(message)
                generations.update([info['generation'],*[row['generation'] for row in slots]])
            except (Conflict,NotFound,OSError,KeyError,TypeError,ValueError):
                omitted += 1
        return messages, {'source':'ORIGINAL_IMMUTABLE_EVENT_SLOTS', 'messages_limit':40,
            'partial':True, 'history_complete':False, 'missing_events':omitted,
            'more_indexed_messages':len(infos)>40, 'index_gap':gap['reason'] if gap else None,
            'source_generations':sorted(generations), 'source_bytes_read':budget.bytes,
            'source_reads':budget.reads, 'text_coverage':'VERIFIED_SAVED_PARTS_NOT_COMPLETE_TRANSCRIPT'}
