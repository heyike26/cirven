"""Real DOCX/SQLite/frozen continuity; owner admission alone is a unit fixture.

No model, Windows or Microsoft Word rendering qualification. Explicit checks
remain effective under python -O.
"""
from copy import deepcopy
from io import BytesIO
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import zipfile

from docx import Document
from docx.shared import RGBColor
from lxml import etree
import pytest

from packages.domain.docx_revisions import DocxRevisions, Clarification, q
from packages.domain.local_workspaces import digest, sha
from packages.domain.store import Conflict, InvalidInput, NotFound
from test_file_continuity import scene, register


OWNER = {'operation_id': 'question-one', 'generation': 'host-one', 'conversation_id': 'conversation-one'}


def check(value, message='DOCX revision contract failed'):
    if not value:
        raise AssertionError(message)


def package(raw, transform):
    out = BytesIO()
    with zipfile.ZipFile(BytesIO(raw)) as source, zipfile.ZipFile(out, 'w', compression=zipfile.ZIP_DEFLATED) as target:
        values = {item.filename: source.read(item.filename) for item in source.infolist()}
        transform(values)
        for name, value in values.items(): target.writestr(name, value)
    return out.getvalue()


def original_docx():
    document = Document()
    document.add_heading('Original generated report', 1)
    p = document.add_paragraph()
    p.add_run('Keep bold 😀 ').bold = True
    p.add_run('old claim').italic = True
    p.add_run(' and untouched colour.').font.color.rgb = RGBColor(25, 90, 130)
    document.add_comment(p.runs[1], text='Human comment: revise this claim, retain my evidence.', author='Human')
    document.add_paragraph('Human manually saved evidence remains unchanged.')
    document.add_table(rows=1, cols=1).cell(0, 0).text = 'Unchanged table cell'
    document.sections[0].header.paragraphs[0].text = 'Original header'
    stream = BytesIO(); document.save(stream)
    return package(stream.getvalue(), lambda values: values.update({'customXml/retained.xml': b'<retained original="yes">opaque original member</retained>'}))


@pytest.fixture
def revision_scene(scene):
    continuity, link, source, raw = register(scene, raw=original_docx())
    store, data, folder, pid, *_ = scene
    observation = continuity.observe(link)
    service = DocxRevisions(store, data, snapshot_reader=continuity.read_snapshot_bytes,
        owner_checker=lambda project, owner: project == pid and owner == OWNER)
    context = service.context(pid, link, observation['snapshot_ref'])
    paragraph = next(value for value in context['paragraphs'] if 'old claim' in value['text'])
    return {'service': service, 'continuity': continuity, 'pid': pid, 'data': data,
        'folder': folder, 'raw': raw, 'source': source, 'link': link,
        'snapshot': observation['snapshot_ref'], 'paragraph': paragraph, 'store': store}


def request(s, operation='proposal-one', *, replacement=None, patches=None):
    anchor = s['paragraph']['anchor']
    return {'operation_id': operation, 'owner': OWNER, 'file_link_ref': s['link'], 'snapshot_ref': s['snapshot'],
        'patches': patches if patches is not None else [{**anchor,
            'replacement': replacement or anchor['quote'].replace('old claim', 'corrected claim'),
            'reason': 'Only correct the specified claim; preserve human evidence.'}]}


def publish(s, proposal=None, operation='publication-one'):
    prepared = proposal or s['service'].prepare(s['pid'], request(s))
    return s['service'].publish(s['pid'], prepared['proposal_ref'], operation_id=operation, owner=OWNER)


def paragraph_view(element, *, accept):
    def read(node):
        if node.tag == q('del') and accept or node.tag == q('ins') and not accept: return ''
        if node.tag in {q('t'), q('delText')}: return node.text or ''
        return ''.join(read(child) for child in node)
    return read(element)


def test_comment_mixed_runs_real_redline_preserves_source_parts_and_human_content(revision_scene):
    s = revision_scene; service, pid = s['service'], s['pid']
    prepared = service.prepare(pid, request(s))
    check(prepared['status'] == 'PROPOSED' and prepared['artifact_created'] is False)
    check(not (s['data'] / 'docx-revisions' / pid / 'artifacts').exists(), 'prepare must not create a DOCX')
    value = publish(s, prepared); artifact = value['artifact']
    check(value['status'] == 'NEW_ARTIFACT' and value['changes_applied'] is False)
    check(value['owner'] == OWNER and value['proposal_ref'] == prepared['proposal_ref'])
    raw, manifest = service.download(pid, artifact['id']); check(manifest == artifact)
    check((s['folder'] / '论文.docx').read_bytes() == s['raw'])
    with zipfile.ZipFile(BytesIO(s['raw'])) as before, zipfile.ZipFile(BytesIO(raw)) as after:
        check(before.namelist() == after.namelist())
        for name in before.namelist():
            if name != 'word/document.xml': check(before.read(name) == after.read(name), name)
        original = etree.fromstring(before.read('word/document.xml'))
        result = etree.fromstring(after.read('word/document.xml'))
    before_paragraph = list(original.iter(q('p')))[1]; after_paragraph = list(result.iter(q('p')))[1]
    check(paragraph_view(after_paragraph, accept=False) == s['paragraph']['text'])
    check(paragraph_view(after_paragraph, accept=True) == s['paragraph']['text'].replace('old claim', 'corrected claim'))
    check(after_paragraph.find('.//' + q('b')) is not None)
    coloured = [r for r in after_paragraph.findall(q('r')) if r.find('.//' + q('color')) is not None]
    check(any('untouched colour' in paragraph_view(r, accept=True) for r in coloured))
    for name in ('commentRangeStart', 'commentRangeEnd', 'commentReference'):
        check([e.attrib for e in before_paragraph.iter(q(name))] == [e.attrib for e in after_paragraph.iter(q(name))])
    for a, b in zip(list(original.iter(q('p')))[2:], list(result.iter(q('p')))[2:]):
        check(etree.tostring(a, method='c14n') == etree.tostring(b, method='c14n'))
    changes = artifact['review']['changes']
    check(all('Keep bold' not in change['quote'] and 'untouched' not in change['quote'] for change in changes))
    check(artifact['execution_effects'] == 'CREATE_NEW_DERIVED_DOCX' and artifact['source_files_modified'] is False)
    check(artifact['review']['native_word_rendering'] == 'NOT_RUN')


def test_multiple_patches_exact_unicode_locate_and_pagination(revision_scene):
    s = revision_scene; service, pid = s['service'], s['pid']; path = s['paragraph']['paragraph_path']
    a = service.locate(pid, s['link'], s['snapshot'], paragraph_path=path, quote='old claim')
    b = service.locate(pid, s['link'], s['snapshot'], paragraph_path=path, quote='untouched colour')
    check(a['char_start'] == len('Keep bold 😀 '))
    prepared = service.prepare(pid, request(s, patches=[{**a, 'replacement': 'new claim'}, {**b, 'replacement': 'retained colour'}]))
    raw, _ = service.download(pid, publish(s, prepared)['artifact']['id'])
    root = etree.fromstring(zipfile.ZipFile(BytesIO(raw)).read('word/document.xml'))
    check(paragraph_view(list(root.iter(q('p')))[1], accept=True) == s['paragraph']['text'].replace('old claim', 'new claim').replace('untouched colour', 'retained colour'))
    page = service.context(pid, s['link'], s['snapshot'], limit=1)
    check(len(page['paragraphs']) == 1 and page['next_offset'] == 1)
    check(page['paragraphs'][0]['text'] == 'Original generated report', 'paragraphs must remain in document order')


def test_snapshot_not_later_human_save_and_restart_exact_get_no_republication(revision_scene):
    s = revision_scene; prepared = s['service'].prepare(s['pid'], request(s))
    later = original_docx() + b'USER_LATER_SAVED_TRAILER'
    (s['folder'] / '论文.docx').write_bytes(later)
    value = publish(s, prepared)
    restarted = DocxRevisions(s['store'], s['data'], snapshot_reader=s['continuity'].read_snapshot_bytes)
    check(restarted.operation(s['pid'], 'publication-one') == value)
    check(restarted.get(s['pid'], value['artifact']['id']) == value['artifact'])
    check(value['artifact']['source_sha256'] == sha(s['raw']))
    check(value['artifact']['later_source_edits_included'] is False)
    check((s['folder'] / '论文.docx').read_bytes() == later)
    check(restarted.operation(s['pid'], 'never-admitted')['status'] == 'NOT_ADMITTED')
    with pytest.raises(Conflict): publish({**s, 'service': restarted}, prepared)


def test_duplicate_inputs_single_artifact_foreign_owner_and_generation_rejected(revision_scene):
    s = revision_scene; body = request(s); prepared = s['service'].prepare(s['pid'], body)
    check(s['service'].prepare(s['pid'], body) == prepared)
    check(publish(s, prepared) == publish(s, prepared))
    altered = request(s, replacement='A different user instruction')
    with pytest.raises(Conflict): s['service'].prepare(s['pid'], altered)
    for owner in ({**OWNER, 'generation': 'later-host'}, {**OWNER, 'operation_id': 'another-question'}, {**OWNER, 'conversation_id': 'other-topic'}):
        with pytest.raises(Conflict): s['service'].publish(s['pid'], prepared['proposal_ref'], operation_id='foreign-publish', owner=owner)
    foreign = s['store'].create_project('Other project', '')['id']
    with pytest.raises((Conflict, InvalidInput, NotFound)): s['service'].prepare(foreign, body)
    with pytest.raises(NotFound): s['service'].get(foreign, publish(s, prepared)['artifact']['id'])


def test_overlap_bad_range_and_ambiguous_quote_never_create_artifact(revision_scene):
    s = revision_scene; anchor = s['paragraph']['anchor']; service = s['service']
    prepared = service.prepare(s['pid'], request(s, patches=[{**anchor, 'replacement': 'one'}, {**anchor, 'replacement': 'two'}]))
    check(prepared['status'] == 'NEEDS_CLARIFICATION' and prepared['code'] == 'OVERLAPPING_RANGES')
    with pytest.raises(Conflict): publish(s, prepared)
    bad = {**anchor, 'char_start': 1, 'char_end': len(anchor['quote']) + 1, 'replacement': 'changed'}
    value = service.prepare(s['pid'], request(s, 'wrong-range', patches=[bad]))
    check(value['status'] == 'NEEDS_CLARIFICATION' and value['code'] == 'QUOTE_RANGE_MISMATCH')
    with pytest.raises(Clarification): service.locate(s['pid'], s['link'], s['snapshot'], paragraph_path=anchor['paragraph_path'], quote='not present')
    with pytest.raises(ValueError): service.prepare(s['pid'], request(s, 'wrong-hash', patches=[{**anchor, 'quote_sha256': '0' * 64, 'replacement': 'changed'}]))


@pytest.mark.parametrize('tag', ['ins', 'fldSimple', 'hyperlink', 'sdt', 'oMath'])
def test_complex_or_preexisting_revisions_refuse_target_but_keep_package(scene, tag):
    def transform(values):
        root = etree.fromstring(values['word/document.xml']); p = list(root.iter(q('p')))[1]
        wrapped = etree.Element(q(tag)); run = etree.SubElement(wrapped, q('r')); etree.SubElement(run, q('t')).text = 'complex'
        p.append(wrapped); values['word/document.xml'] = etree.tostring(root)
    raw = package(original_docx(), transform)
    continuity, link, *_ = register(scene, raw=raw)
    store, data, folder, pid, *_ = scene; observation = continuity.observe(link)
    service = DocxRevisions(store, data, snapshot_reader=continuity.read_snapshot_bytes, owner_checker=lambda p, o: o == OWNER)
    context = service.context(pid, link, observation['snapshot_ref']); paragraph = next(p for p in context['paragraphs'] if 'old claim' in p['text'])
    check(not paragraph['editable'] and paragraph['reason'])
    patch = {'part': 'word/document.xml', 'paragraph_path': paragraph['paragraph_path'], 'char_start': 0, 'char_end': 1, 'quote': paragraph['text'][0], 'quote_sha256': sha(paragraph['text'][0].encode()), 'replacement': 'X'}
    result = service.prepare(pid, {'operation_id': 'unsupported', 'owner': OWNER, 'file_link_ref': link, 'snapshot_ref': observation['snapshot_ref'], 'patches': [patch]})
    check(result['status'] == 'NEEDS_CLARIFICATION')
    check((folder / '论文.docx').read_bytes() == raw)


def test_unobserved_publish_does_not_retry_or_repair_after_partial_write(revision_scene):
    s = revision_scene; service = s['service']; prepared = service.prepare(s['pid'], request(s))
    original_write = service._write
    def fail(path, value):
        if path.name == 'manifest.json': raise OSError('owned fixture: disk full after derived bytes')
        return original_write(path, value)
    service._write = fail
    with pytest.raises(OSError): publish(s, prepared)
    service._write = original_write
    before = {str(p): p.read_bytes() for p in (s['data'] / 'docx-revisions').rglob('*') if p.is_file()}
    check(service.operation(s['pid'], 'publication-one')['status'] == 'UNKNOWN_OUTCOME')
    check(publish(s, prepared)['status'] == 'UNKNOWN_OUTCOME')
    after = {str(p): p.read_bytes() for p in (s['data'] / 'docx-revisions').rglob('*') if p.is_file()}
    check(before == after and (s['folder'] / '论文.docx').read_bytes() == s['raw'])


def test_capacity_and_missing_authority_fail_closed(revision_scene):
    s = revision_scene
    service = DocxRevisions(s['store'], s['data'], snapshot_reader=s['continuity'].read_snapshot_bytes, owner_checker=lambda p, o: True, max_storage_bytes=1)
    with pytest.raises(Conflict): service.prepare(s['pid'], request(s))
    service = DocxRevisions(s['store'], s['data'], snapshot_reader=s['continuity'].read_snapshot_bytes)
    with pytest.raises(Conflict): service.prepare(s['pid'], request(s))


def test_real_concurrent_prepare_and_publish_have_single_immutable_results(revision_scene):
    s = revision_scene; service, pid = s['service'], s['pid']
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.prepare, pid, request(s)) for _ in range(2)]
        proposals = [f.result() for f in futures]
    check(proposals[0] == proposals[1])
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(service.publish, pid, proposals[0]['proposal_ref'], operation_id='concurrent-publish', owner=OWNER) for _ in range(2)]
        outcomes = [f.result() for f in futures]
    check(outcomes[0] == outcomes[1])
    check(len(list((s['data'] / 'docx-revisions' / pid / 'artifacts').iterdir())) == 1)


def test_owner_revoked_during_local_preparation_cannot_publish(revision_scene):
    s = revision_scene; service = s['service']; prepared = service.prepare(s['pid'], request(s))
    seen = []
    def current(_pid, owner):
        seen.append(owner)
        return len(seen) == 1
    service.owner_checker = current
    with pytest.raises(Conflict): publish(s, prepared)
    check(len(seen) == 2)
    check(not (s['data'] / 'docx-revisions' / s['pid'] / 'artifacts').exists())
    check(service.operation(s['pid'], 'publication-one')['status'] == 'UNKNOWN_OUTCOME')


def test_insert_delete_and_adjacent_supported_ranges_round_trip(revision_scene):
    s = revision_scene; service, pid = s['service'], s['pid']; path = s['paragraph']['paragraph_path']
    first = service.locate(pid, s['link'], s['snapshot'], paragraph_path=path, quote='old claim')
    insertion = {**first, 'char_start': 0, 'char_end': 0, 'quote': '', 'quote_sha256': sha(b''), 'replacement': 'Reviewed: '}
    prepared = service.prepare(pid, request(s, patches=[{**first, 'replacement': ''}, insertion]))
    value = publish(s, prepared); raw, _ = service.download(pid, value['artifact']['id'])
    root = etree.fromstring(zipfile.ZipFile(BytesIO(raw)).read('word/document.xml'))
    p = list(root.iter(q('p')))[1]
    check(paragraph_view(p, accept=True) == 'Reviewed: ' + s['paragraph']['text'].replace('old claim', ''))
    check(paragraph_view(p, accept=False) == s['paragraph']['text'])
    from packages.domain.docx_import import parse_docx
    parsed = parse_docx('revised.docx', raw)
    check('Human comment: revise this claim' in parsed.content)
    check('[tracked-ins]' in parsed.content and '[tracked-del]' in parsed.content)
