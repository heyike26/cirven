"""Real OOXML/SQLite/host receipts: ordinary cohort publication boundaries.

No model/CLI/Windows execution claim. Only the ordinary dispatch/cohort inputs
are fixtures; document proposal, redline and saved-file continuity are real.
"""
from copy import deepcopy
from io import BytesIO
import os
from types import SimpleNamespace
import zipfile

import psutil
import pytest
from lxml import etree

from apps.api.native_messages import NativeMessages
from packages.domain.local_workspaces import digest
from packages.domain.native_collaboration import NativeCollaborationAuthority
from packages.domain.store import Conflict
from test_file_continuity import scene
from test_docx_revisions import OWNER, package, paragraph_view, revision_scene


def check(value, message='ordinary publication boundary failed'):
    if not value:
        raise AssertionError(message)


class PosixLifetime:
    def process_identity(self, pid):
        return {'pid': pid, 'process_creation': int(psutil.Process(pid).create_time()*1_000_000_000),
            'os_session': os.getsid(pid)}


@pytest.fixture
def ordinary_document(revision_scene):
    s = revision_scene
    native = NativeMessages.__new__(NativeMessages)
    native.ws = NativeCollaborationAuthority(s['data'], token='a'*64, owner_pid=os.getpid(),
        native=PosixLifetime(), platform='posix')
    native.generation, native.closed = OWNER['generation'], False
    native.docx_revisions = s['service']
    native.app = SimpleNamespace(state=SimpleNamespace(file_continuity=s['continuity']))
    native.docx_revisions.owner_checker = native.docx_owner_current
    observed = s['continuity'].observe(s['link'])
    body = {'operation_id': OWNER['operation_id'], 'conversation_id': OWNER['conversation_id'],
        'project_id': s['pid'], 'raw_text': 'Correct the claim; preserve my evidence.',
        'file_link_ref': s['link'], 'context_ref': None, 'provider_profile_ref': None, 'file_source_ref': None}
    record = {'request': body, 'request_sha256': digest(body), 'generation': native.generation,
        'status': 'PROCESSING', 'execution': None, 'file_observation': observed,
        'native_message_ref': {'id': body['operation_id'], 'revision': 1, 'sha256': 'a'*64}}
    native.ws._write(native.path(body['operation_id']), record)
    return s, native


def saved(native):
    return native.ws._read(native.path(OWNER['operation_id']), authority=True)


def cohort(record, sequence):
    value = {'native_message_ref': deepcopy(record['native_message_ref']),
        'parent_operation_id': record['request']['operation_id'],
        'owner_ref': {'id': '1'*32, 'revision': 1, 'sha256': 'b'*64}, 'generation': 1,
        'native_operation_id': 'native-turn-'+str(sequence),
        'native_user_message_id': 'msg_000000000001' + str(sequence)*14,
        'native_session_id': 'ses_test_exact_ordinary', 'sequence': sequence}
    value['cohort_sha256'] = digest(value)
    return value


def begin(native, sequence):
    record = saved(native); value = cohort(record, sequence)
    with native.ws.lock:
        native.ordinary_cohort_changed(record, value)
    return value


def propose(s, native, value, replacement):
    binding = {'request': {'operation_id': OWNER['operation_id'], 'project_id': s['pid'],
        'prompt': saved(native)['request']['raw_text']}, 'native_cohort': value}
    patches = [{**s['paragraph']['anchor'], 'replacement': replacement,
        'reason': 'Only this native turn may publish this replacement.'}]
    return native.propose_docx_revision({'patches': patches}, binding)


def no_publication(s, operation):
    result = s['service'].operation(s['pid'], operation)
    check(result['status'] == 'NOT_ADMITTED', 'No original publication operation may have been admitted')
    check(result['automatic_retry'] is False)


def test_new_guidance_retires_old_proposal_and_only_latest_redline_is_published(ordinary_document):
    s, native = ordinary_document
    first = begin(native, 1)
    old_proposal = propose(s, native, first, 'Old cohort draft')
    old_entry = deepcopy(saved(native)['docx_revision'])
    latest = begin(native, 2)
    current = saved(native)
    check('docx_revision' not in current)
    check(current['docx_revision_history'] == [old_entry])
    with native.ws.lock, pytest.raises(Conflict):
        native.ordinary_terminal(current, first, 'RETURNED')
    no_publication(s, old_entry['operation_id'])
    # Rejected stale callbacks must not even create another proposal operation.
    with pytest.raises(Conflict):
        propose(s, native, first, 'A stale callback')
    fresh_proposal = propose(s, native, latest, 'Latest cohort correction')
    check(fresh_proposal['proposal_ref'] != old_proposal['proposal_ref'])
    new_entry = deepcopy(saved(native)['docx_revision'])
    with native.ws.lock:
        native.ordinary_terminal(saved(native), latest, 'RETURNED')
    published = s['service'].operation(s['pid'], new_entry['operation_id'])
    check(published['status'] == 'NEW_ARTIFACT')
    raw, _ = s['service'].download(s['pid'], published['artifact']['id'])
    with zipfile.ZipFile(BytesIO(raw)) as archive:
        xml = archive.read('word/document.xml')
        accepted = paragraph_view(etree.fromstring(xml), accept=True)
        check('Latest cohort correction' in accepted and 'Old cohort draft' not in accepted)
    check((s['folder']/'论文.docx').read_bytes() == s['raw'])
    no_publication(s, old_entry['operation_id'])
    check(s['service'].proposal(s['pid'], old_proposal['proposal_ref'])['status'] == 'PROPOSED')


def test_cancel_intent_committed_after_terminal_snapshot_prevents_late_publication(ordinary_document):
    s, native = ordinary_document
    value = begin(native, 1); propose(s, native, value, 'Must not publish')
    stale = saved(native); entry = deepcopy(stale['docx_revision'])
    current = saved(native); current['docx_publication_cancel_requested'] = True
    native.ws._write(native.path(OWNER['operation_id']), current)
    with native.ws.lock:
        native.ordinary_terminal(stale, value, 'RETURNED')
    check(saved(native)['docx_publication_cancel_requested'] is True)
    no_publication(s, entry['operation_id'])
    check((s['folder']/'论文.docx').read_bytes() == s['raw'])


def test_human_saved_edit_during_task_holds_publication_and_retains_both_sources(ordinary_document):
    s, native = ordinary_document
    value = begin(native, 1); proposal = propose(s, native, value, 'Model proposal before human save')
    entry = deepcopy(saved(native)['docx_revision'])
    def edit(values):
        values['word/document.xml'] = values['word/document.xml'].replace(b'old claim', b'Human new claim')
    human = package(s['raw'], edit)
    (s['folder']/'论文.docx').write_bytes(human)
    with native.ws.lock:
        native.ordinary_terminal(saved(native), value, 'RETURNED')
    check(saved(native).get('artifact_error', {}).get('code') == 'SOURCE_CHANGED_DURING_TASK')
    no_publication(s, entry['operation_id'])
    check((s['folder']/'论文.docx').read_bytes() == human)
    check(s['service'].proposal(s['pid'], proposal['proposal_ref'])['status'] == 'PROPOSED')


@pytest.mark.parametrize('field', ['native_message_ref', 'parent_operation_id', 'cohort_sha256'])
def test_foreign_or_tampered_cohort_cannot_advance_document_publication(ordinary_document, field):
    s, native = ordinary_document
    value = begin(native, 1); propose(s, native, value, 'Original admitted proposal')
    before = native.path(OWNER['operation_id']).read_bytes()
    foreign = deepcopy(value)
    foreign[field] = ({**foreign[field], 'id': 'foreign-parent'} if field == 'native_message_ref'
        else 'f'*64 if field == 'cohort_sha256' else 'foreign-parent')
    if field != 'cohort_sha256':
        foreign['cohort_sha256'] = digest({k:v for k,v in foreign.items() if k != 'cohort_sha256'})
    with native.ws.lock, pytest.raises(Conflict):
        native.ordinary_terminal(saved(native), foreign, 'RETURNED')
    check(native.path(OWNER['operation_id']).read_bytes() == before)
    check((s['folder']/'论文.docx').read_bytes() == s['raw'])
