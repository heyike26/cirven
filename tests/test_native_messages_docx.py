"""Real App/SQLite/OOXML/original CLI/ordinary tool bridge; only the model is loopback.

The host seam observes the actual POSIX lifetime. Native Word and personal
subscription qualification are separate Windows steps.
"""
from io import BytesIO
from pathlib import Path
import time
import zipfile

from lxml import etree
import pytest

from packages.domain.local_workspaces import canonical, sha
from packages.domain.store import Conflict
from test_file_continuity import docx
from test_native_file_continuity_api import scene, opened_report, send, app_client, MESSAGES
from test_docx_revisions import paragraph_view
from native_docx_wire_fixture import RevisionWire, configure, QUOTE, REPLACEMENT


W = 'http://schemas.openxmlformats.org/wordprocessingml/2006/main'


def check(value, message='native DOCX continuation requirement failed'):
    if not value:
        raise AssertionError(message)


def saved_docx(body='用户保留的阈值为 42。' + QUOTE):
    source = docx(body, '仅修改自动通过的说法，保留人工确认的阈值。')
    output = BytesIO()
    with zipfile.ZipFile(BytesIO(source)) as original, zipfile.ZipFile(output, 'w') as target:
        for item in original.infolist():
            raw = original.read(item.filename)
            if item.filename == 'word/document.xml':
                tree = etree.fromstring(raw)
                for node in tree.xpath('//w:ins|//w:del', namespaces={'w': W}):
                    node.getparent().remove(node)
                raw = etree.tostring(tree, xml_declaration=True, encoding='UTF-8')
            target.writestr(item, raw)
    return output.getvalue()


def returned(client, operation):
    deadline = time.monotonic() + 100
    while time.monotonic() < deadline:
        response = client.get(MESSAGES + '/' + operation)
        check(response.status_code == 200, response.text)
        result = response.json()
        if result['status'] != 'PROCESSING':
            return result
        time.sleep(.1)
    raise AssertionError('Original DOCX question did not reach its actual terminal state')


@pytest.mark.parametrize('newer_save', [False, True], ids=['unchanged-publishes-copy', 'newer-save-holds-proposal'])
def test_original_cli_proposal_respects_source_at_publication(scene, newer_save):
    client, pid, _folder, _base, _source, settings = scene
    original_artifact, immutable, path, link = opened_report(scene)
    frozen = saved_docx()
    path.write_bytes(frozen)
    wire = RevisionWire(gate_model=True)
    try:
        profile = configure(client, settings, wire)
        response = send(client, pid, link, operation='revise-original', provider_profile_ref=profile)
        check(response.status_code == 200, response.text)
        check(wire.entered.wait(90), 'Original CLI did not prepare the bounded proposal')
        pending = client.get(MESSAGES + '/revise-original').json()
        check(pending['artifact_status'] == 'PREPARING' and pending['artifacts'] == [])
        native = client.app.state.native_messages
        admitted = native.ordinary.validate_admission(pending['native_message_ref'])
        binding = {'request': admitted['request'], 'native_cohort': pending['native_cohort']}
        with pytest.raises(Conflict):
            native.read_docx_revision_context({}, {**binding, 'native_cohort': None})
        with pytest.raises(Conflict):
            native.read_docx_revision_context({}, {**binding, 'native_cohort': {**binding['native_cohort'], 'sequence': 0}})
        check(path.read_bytes() == frozen)
        later = saved_docx('用户后来又将阈值改为 99。' + QUOTE)
        if newer_save:
            path.write_bytes(later)
        wire.release.set()
        result = returned(client, 'revise-original')
        check(result['status'] == 'RETURNED', canonical(result))
        check(result['answer_source'] == 'ORIGINAL_NATIVE_TEXT_JSON_VALIDATED')
        check(client.get(original_artifact['download_url']).content == immutable)
        check(len(wire.calls) == 4 and len(wire.results) == 3)
        if newer_save:
            check(result['artifact_status'] == 'PROPOSED_NOT_PUBLISHED', canonical(result))
            check(result['artifact_error']['code'] == 'SOURCE_CHANGED_DURING_TASK')
            check(result['artifacts'] == [] and result['changes_applied'] is False)
            check(bool(result['docx_revision_proposal_ref']) and path.read_bytes() == later)
            with app_client(settings) as restarted:
                historical = restarted.get(MESSAGES + '/revise-original').json()
                check(historical['record_origin'] == 'HISTORICAL_READONLY')
                check(historical['artifact_status'] == result['artifact_status'] and historical['artifacts'] == [])
            check(len(wire.calls) == 4 and path.read_bytes() == later)
            return
        check(result['artifact_status'] == 'NEW_ARTIFACT', canonical(result))
        check(result['execution_effects'] == 'CREATE_NEW_DERIVED_DOCX')
        check(result['changes_applied'] is False and result['source_files_modified'] is False)
        check(path.read_bytes() == frozen)
        artifact = result['artifacts'][0]
        check(artifact['source_sha256'] == sha(frozen) and not artifact['later_source_edits_included'])
        downloaded = client.get(artifact['download_url'])
        check(downloaded.status_code == 200 and sha(downloaded.content) == artifact['sha256'])
        with zipfile.ZipFile(BytesIO(frozen)) as before, zipfile.ZipFile(BytesIO(downloaded.content)) as after:
            for name in before.namelist():
                if name != 'word/document.xml':
                    check(before.read(name) == after.read(name), 'Unchanged OOXML member differs: ' + name)
            tree = etree.fromstring(after.read('word/document.xml'))
            check(bool(tree.xpath('//w:ins', namespaces={'w': W})))
            check(bool(tree.xpath('//w:del', namespaces={'w': W})))
            check(REPLACEMENT in paragraph_view(tree, accept=True))
            check(QUOTE not in paragraph_view(tree, accept=True))
            check(QUOTE in paragraph_view(tree, accept=False))
            check(REPLACEMENT not in paragraph_view(tree, accept=False))
            check('阈值为 42' in ''.join(tree.xpath('//w:t/text()', namespaces={'w': W})))
            check('阈值改为 99' not in ''.join(tree.xpath('//w:t/text()', namespaces={'w': W})))
        opened = client.post('/api/desktop-host/native-open/report', json={
            'project_id': pid, 'artifact_id': artifact['id'], 'sha256': artifact['sha256'],
            'download_url': artifact['download_url']})
        check(opened.status_code == 200, opened.text)
        working = Path(opened.json()['native_path'])
        check(working.read_bytes() == downloaded.content)
        working.write_bytes(saved_docx('用户审阅修订副本后保留的新段落。'))
        repeated = client.post('/api/desktop-host/native-open/report', json={
            'project_id': pid, 'artifact_id': artifact['id'], 'sha256': artifact['sha256'],
            'download_url': artifact['download_url']})
        check(repeated.status_code == 200 and Path(repeated.json()['native_path']) == working)
        check('新段落' in client.app.state.file_continuity.observe(opened.json()['file_link_ref'])['parsed']['text_content'])
        with app_client(settings) as restarted:
            recovered = restarted.get(MESSAGES + '/revise-original')
            check(recovered.status_code == 200, recovered.text)
            check(recovered.json()['artifacts'] == result['artifacts'])
            check(recovered.json()['record_origin'] == 'HISTORICAL_READONLY')
            check(len(wire.calls) == 4 and path.read_bytes() == frozen)
            native = restarted.app.state.native_messages
            with pytest.raises(Conflict):
                native.read_docx_revision_context({}, binding)
        (settings.data_dir.parent / 'docx-original-loop-receipt.json').write_text(canonical({
            'scope': 'REAL_APP_ORIGINAL_CLI_ORDINARY_BRIDGE_EXTERNAL_MODEL_FIXTURE_NOT_WINDOWS',
            'result': result, 'model_requests': len(wire.calls), 'source_retained': True}) + '\n')
    finally:
        wire.close()
