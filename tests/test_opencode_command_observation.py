"""Real Node hooks + filesystem + shell child; no model or Windows claim."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from packages.adapters.opencode_command_observation import (CommandObservationCapacity, ENV_KEY, bind, declared_paths,
    historical_admissions, prepare, read_history, ready, record_admission, verify_bundle)
from packages.domain.local_workspaces import LocalWorkspaces, WorkspaceInput
from packages.domain.store import Store, Conflict, InvalidInput

ROOT = Path(__file__).resolve().parents[1]
NODE = ROOT / '.node-tools/node_modules/node/bin/node'
PLUGIN = ROOT / 'toolchain/opencode-plugins/command-observation'


@pytest.fixture
def command_scene(tmp_path, request):
    data = tmp_path / 'data'
    data.mkdir()
    store = Store(data / 'eeas.sqlite3')
    pid = store.create_project('命令证据原创场景', 'Real local commands without a model')['id']
    folder = tmp_path / getattr(request, 'param', 'code workspace')
    folder.mkdir()
    (folder / 'a.py').write_text('USER_MANUAL\noriginal\n')
    ws = LocalWorkspaces(store, data)
    workspace = ws.bind(pid, WorkspaceInput(idempotency_key='command-source', root=str(folder), allowed_write_paths=['a.py']))
    runtime = data / 'runtime'
    runtime.mkdir()
    ref = {'id': 'a' * 32, 'revision': 1, 'sha256': 'b' * 64}
    admission = bind(prepare(runtime, project_id=pid, session_ref=ref, generation=1,
                             workspace=workspace, source_paths=['a.py']), 'ses_command')
    record_admission(runtime, admission)
    return {'tmp': tmp_path, 'data': data, 'folder': folder, 'runtime': runtime, 'workspace': workspace,
            'pid': pid, 'ref': ref, 'admission': admission}


def exercise(scene, **options):
    script = ("import {exercise} from " + json.dumps((PLUGIN / 'tests/fixture.mjs').as_uri()) + ";"
              "let body='';for await (const c of process.stdin)body+=c;const v=JSON.parse(body);"
              "process.stdout.write(JSON.stringify(await exercise(v.config,v.options)));" )
    run = subprocess.run([str(NODE), '--input-type=module', '-e', script], input=json.dumps({
        'config': scene['admission']['config_path'], 'options': options}), encoding='utf-8',
        capture_output=True, timeout=30, env={**os.environ, 'TMPDIR': str(scene['tmp'])})
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def observation(scene, packet, *, generation=1, status='completed', message='msg_assistant', parent='msg_user'):
    part = {'type': 'tool', 'tool': 'bash', 'id': 'prt_' + packet['input']['callID'],
            'callID': packet['input']['callID'], 'sessionID': 'ses_command', 'messageID': message,
            'state': {'status': status, 'input': packet['args'], 'output': packet['result']['output'],
                      'metadata': packet['result']['metadata'], 'time': {'start': 1, 'end': 2}}}
    return {'project_id': scene['pid'], 'session_ref': scene['ref'], 'generation': generation,
            'workspace_ref': {k: scene['workspace'][k] for k in ('id', 'revision', 'binding_sha256')},
            'original_session_id': 'ses_command',
            'messages': [{'info': {'id': message, 'parentID': parent, 'role': 'assistant', 'sessionID': 'ses_command'},
                          'parts': [part]}]}


def history(scene, observed):
    return read_history(scene['admission'], observed, data_dir=scene['data'])


def test_real_plugin_fail_repair_retest_and_saved_sources_survive_later_manual_edits(command_scene):
    s = command_scene
    failed = exercise(s, callID='failed', exit=7)
    old = observation(s, failed)
    first, why = history(s, old)
    assert not why and first[0]['exit_code'] == 7 and first[0]['source_status'] == 'UNCHANGED'
    (s['folder'] / 'a.py').write_text('USER_MANUAL\nrepaired\n')
    new = exercise(s, callID='retest', exit=0)
    combined = observation(s, new, message='msg_second', parent='msg_user_second')
    combined['messages'] = old['messages'] + combined['messages']
    rows, why = history(s, combined)
    assert not why and [r['exit_code'] for r in rows] == [7, 0]
    assert rows[0]['sources'][0]['before']['sha256'] != rows[1]['sources'][0]['before']['sha256']
    (s['folder'] / 'a.py').write_text('LATER_USER_EDIT_MUST_SURVIVE\n')
    assert history(s, combined) == (rows, why)
    assert (s['folder'] / 'a.py').read_text() == 'LATER_USER_EDIT_MUST_SURVIVE\n'
    assert ready(s['admission'])


def test_permission_wait_source_edit_and_run_mutation_are_separate_observations(command_scene):
    s = command_scene
    p = exercise(s, changeBefore='MANUAL_DURING_PERMISSION\n', changeDuring='MANUAL\nCOMMAND_UPDATE\n')
    rows, why = history(s, observation(s, p))
    assert not why
    assert rows[0]['source_status'] == 'CHANGED'
    source = rows[0]['sources'][0]
    assert source['before']['sha256'] == hashlib.sha256(b'MANUAL_DURING_PERMISSION\n').hexdigest()
    assert source['after']['sha256'] == hashlib.sha256(b'MANUAL\nCOMMAND_UPDATE\n').hexdigest()


@pytest.mark.parametrize('options,status', [({'nullExit': True}, 'completed'), ({'after': False}, 'completed'),
                                          ({'after': False, 'close': True}, 'error')])
def test_null_missing_after_or_closed_owner_never_claim_integer_terminal(command_scene, options, status):
    s = command_scene
    p = exercise(s, **options)
    rows, why = history(s, observation(s, p, status=status))
    assert not why and rows[0]['status'] == 'UNKNOWN_OUTCOME' and rows[0]['exit_code'] is None
    assert rows[0]['record_ref']['sha256']


def test_truncated_merged_output_references_exact_original_preview(command_scene):
    s = command_scene
    p = exercise(s, large=True)
    rows, why = history(s, observation(s, p))
    assert not why and rows[0]['output']['truncated'] is True
    assert rows[0]['output']['bytes'] <= 65536
    assert '\ufffd' not in rows[0]['output']['text']
    assert not any(k in rows[0] for k in ('argv', 'stdout', 'stderr', 'passed'))


@pytest.mark.parametrize('change', ['input', 'output', 'exit', 'duplicate', 'parent', 'scope', 'config', 'blob'])
def test_tampered_native_or_private_evidence_never_becomes_a_current_result(command_scene, change):
    s = command_scene
    p = exercise(s)
    view = observation(s, p)
    part = view['messages'][0]['parts'][0]
    if change == 'input': part['state']['input']['command'] = 'false'
    elif change == 'output': part['state']['output'] += 'invented output'
    elif change == 'exit': part['state']['metadata']['exit'] = 7
    elif change == 'duplicate': view['messages'][0]['parts'].append(deepcopy(part))
    elif change == 'parent': view['messages'][0]['info']['parentID'] = None
    elif change == 'scope': view['project_id'] = 'another_project'
    elif change == 'config': Path(s['admission']['config_path']).write_text('{}')
    elif change == 'blob':
        source = p['pre']['sources'][0]['snapshot']
        (Path(s['admission']['scope']['ledger_root']) / source['blob']).write_bytes(b'changed')
    rows, reasons = history(s, view)
    assert not rows and reasons


def test_historical_admission_is_idempotent_bounded_and_readable_after_restart(command_scene):
    s = command_scene
    p = exercise(s)
    observed = observation(s, p)
    index = s['runtime'] / 'command-observation/admissions.json'
    before = index.read_bytes()
    record_admission(s['runtime'], s['admission'])
    assert index.read_bytes() == before
    assert list(historical_admissions(s['runtime'], observed)) == [s['admission']]
    # Reader relies only on persisted records, with no owner/configuration lookup.
    reloaded = json.loads(json.dumps(s['admission']))
    assert read_history(reloaded, observed, data_dir=s['data']) == history(s, observed)
    mismatch = deepcopy(observed)
    mismatch['session_ref']['sha256'] = 'f' * 64
    with pytest.raises(Conflict): list(historical_admissions(s['runtime'], mismatch))


@pytest.mark.parametrize('paths', [['a.py', 'a.py'], ['src/'], ['a?.py'], ['../outside'], ['.Git/config'], ['*']])
def test_declared_inputs_cannot_expand_to_globs_directories_private_or_traversal(paths):
    with pytest.raises((InvalidInput, ValueError)): declared_paths(paths)


def test_missing_observer_is_coverage_only_and_bundle_is_fixed():
    assert ENV_KEY == 'EEAS_COMMAND_OBSERVATION_CONFIG'
    assert verify_bundle()['entry'] == str(PLUGIN / 'index.mjs')
    assert read_history(None, {}, data_dir=ROOT) == ([], ['NO_ADMITTED_COMMAND_OBSERVER'])


@pytest.mark.parametrize('side', ['input', 'output', 'bool_exit', 'running'])
def test_redacted_or_nonterminal_original_observation_never_exposes_raw_ledger(command_scene, side):
    s = command_scene
    packet = exercise(s)
    view = observation(s, packet)
    state = view['messages'][0]['parts'][0]['state']
    if side == 'input': state['input']['command'] = '[REDACTED_RUNTIME_CREDENTIAL]'
    elif side == 'output': state['output'] = '[REDACTED_RUNTIME_CREDENTIAL]'
    elif side == 'bool_exit': state['metadata']['exit'] = False
    else: state['status'] = 'running'
    rows, reasons = history(s, view)
    assert not rows and reasons


@pytest.mark.parametrize('omit', [True, False])
@pytest.mark.parametrize('command_scene', ['KNOWN_SECRET_UNSHARED_CWD'], indirect=True)
def test_resolved_default_workdir_is_not_extra_public_context_but_exit_and_output_survive(command_scene, omit):
    s = command_scene
    packet = exercise(s, omitWorkdir=omit)
    assert packet['process_exit'] == 0
    assert ('workdir' not in packet['args']) is omit
    view = observation(s, packet)
    rows, reasons = history(s, view)
    assert not reasons and len(rows) == 1
    row = rows[0]
    assert row['status'] == 'EXITED' and row['exit_code'] == 0
    assert row['output']['text'] == packet['result']['output']
    if omit:
        assert row['workdir'] is None
        assert 'COMMAND_WORKDIR_NOT_IN_OBSERVED_INPUT' in row['reason_codes']
        # The absolute private root was added only by shell.env. It must not
        # appear elsewhere in this published command entry either.
        assert str(s['folder']) not in json.dumps(row, ensure_ascii=False)
        assert 'KNOWN_SECRET_UNSHARED_CWD' not in json.dumps(row, ensure_ascii=False)
    else:
        assert row['workdir'] == str(s['folder'])
        assert 'COMMAND_WORKDIR_NOT_IN_OBSERVED_INPUT' not in row['reason_codes']


@pytest.mark.parametrize('full', ['generations', 'metadata'])
def test_only_explicit_history_capacity_has_typed_optional_admission_failure(command_scene, full):
    s = command_scene
    root = s['runtime'] / 'command-observation'
    index = root / 'admissions.json'
    retained = index.read_bytes()
    if full == 'generations':
        for number in range(2, 17): (root / ('generation-' + str(number))).mkdir()
    else:
        for number in range(20): (root / ('metadata-' + str(number))).mkdir()
    with pytest.raises(CommandObservationCapacity):
        prepare(s['runtime'], project_id=s['pid'], session_ref=s['ref'], generation=17,
                workspace=s['workspace'], source_paths=['a.py'])
    assert index.read_bytes() == retained
    assert not (root / 'generation-17').exists()


def test_bundle_integrity_failure_is_not_optional_capacity(command_scene):
    s = command_scene
    bad = s['tmp'] / 'changed-bundle'
    bad.mkdir()
    (bad / 'manifest.json').write_text('{}')
    with pytest.raises(Conflict) as error:
        prepare(s['runtime'], project_id=s['pid'], session_ref=s['ref'], generation=2,
                workspace=s['workspace'], plugin_root=bad)
    assert not isinstance(error.value, CommandObservationCapacity)
    assert not (s['runtime'] / 'command-observation/generation-2').exists()


def test_new_generation_call_after_32_old_native_commands_is_still_readable(command_scene):
    s = command_scene
    s['admission'] = bind(prepare(s['runtime'], project_id=s['pid'], session_ref=s['ref'], generation=2,
                                  workspace=s['workspace'], source_paths=['a.py']), 'ses_command')
    record_admission(s['runtime'], s['admission'])
    packet = exercise(s, callID='new_generation_call')
    assert packet['process_exit'] == 0
    view = observation(s, packet, generation=2)
    # These are prior saved native observations, not commands re-executed by
    # this test. The new generation's call above uses the real hook + child.
    old = []
    for number in range(32):
        message = deepcopy(view['messages'][0])
        ident = 'msg_old_' + str(number)
        message['info']['id'] = ident
        part = message['parts'][0]
        part.update(id='prt_old_' + str(number), messageID=ident, callID='old_call_' + str(number))
        old.append(message)
    view['messages'] = old + view['messages']
    rows, reasons = history(s, view)
    assert len(rows) == 1, {'rows': rows, 'reasons': reasons}
    assert rows[0]['call_id'] == 'new_generation_call' and rows[0]['source_generation'] == 2
    assert rows[0]['exit_code'] == 0
