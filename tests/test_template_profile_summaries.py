"""Real ASGI summaries retain the existing full profile/pending authority."""
from pathlib import Path
import hashlib

import pytest

from test_template_profile_imports import scene, api, import_body, importing, review_import, ref, _pending_scene


def test_summaries_preserve_legacy_and_exact_draft_reviewed_pending(scene, monkeypatch):
    e = scene; service, pending_root, operation, usable, failed = _pending_scene(e, monkeypatch)
    helper = Path(__file__).with_name('test_template_profile_imports.py')
    # The R32 helper (53c5f9c99dc86416c61615887483a105cf8448223762e6bce6d3b010efb8dc6b)
    # used D: for temporary evidence. This revision moves only those output paths.
    assert hashlib.sha256(helper.read_bytes()).hexdigest() == '26dce6994696154553c92cf7aa2668db48e5f5ec7f0f134b47c82a9379ab260b'
    reviewed_body = import_body(e, 'summary-reviewed'); reviewed_body['name'] = '当前已审经验'
    original = importing(e, reviewed_body)
    reviewed = review_import(e, original, 'summary-review')
    draft_body = import_body(e, 'summary-draft'); draft_body['name'] = '当前待审经验'
    draft = importing(e, draft_body)
    full = api(e, 'GET', e['broot'])
    assert api(e, 'GET', e['broot'] + '?summaries=false') == full
    assert full == service.listing(e['b'])
    assert {p['id'] for p in full['profiles']} == {usable['id'], reviewed['id'], draft['id']}
    assert {p['status'] for p in full['profiles']} == {'DRAFT', 'REVIEWED'}
    assert all('payload' in p and 'draft_rules' in p and 'approved_rules' in p for p in full['profiles'])
    summary = api(e, 'GET', e['broot'] + '?summaries=true')
    assert summary == {'profiles': [{'id':p['id'], 'name':p['payload']['name'], 'status':p['status'],
        'profile_ref':p['profile_ref'], 'current_revision':p['current_revision']} for p in full['profiles']],
        'pending_imports':full['pending_imports']}
    assert all(set(p) == {'id', 'name', 'status', 'profile_ref', 'current_revision'} for p in summary['profiles'])
    assert len(summary['pending_imports']) == 1 and summary['pending_imports'][0]['profile_id'] == pending_root.name
    assert not (operation / 'result.json').exists() and not (pending_root / 'head.json').exists()
    old = api(e, 'GET', e['broot'] + '/' + reviewed['id'] + '?revision=1')
    assert old['profile_ref'] == ref(original) and old['status'] == 'DRAFT' and old['current_revision'] == reviewed['revision']
    current = api(e, 'GET', e['broot'] + '/' + reviewed['id'] + '?revision=' + str(reviewed['revision']))
    selected = next(p for p in summary['profiles'] if p['id'] == reviewed['id'])
    assert current['profile_ref'] == selected['profile_ref'] == ref(reviewed)
    assert current['payload']['name'] == selected['name'] and current['approved_rules']


@pytest.mark.parametrize('fault', ['head', 'version', 'pending_claim'])
def test_summaries_cannot_skip_existing_record_or_pending_validation(scene, monkeypatch, fault):
    e = scene; service, pending_root, operation, usable, failed = _pending_scene(e, monkeypatch)
    before = failed.read_bytes()
    if fault == 'head': service._path(e['b'], usable['id'], 'head.json').write_bytes(b'{bad existing head')
    elif fault == 'version': service._path(e['b'], usable['id'], 'versions', '1.json').write_bytes(b'{bad existing version')
    else:
        claim = next((operation / 'claims').iterdir())
        claim.rename(claim.with_name('0' * 64))
    api(e, 'GET', e['broot'], expected=409)
    api(e, 'GET', e['broot'] + '?summaries=false', expected=409)
    api(e, 'GET', e['broot'] + '?summaries=true', expected=409)
    assert failed.read_bytes() == before and not (pending_root / 'head.json').exists()
