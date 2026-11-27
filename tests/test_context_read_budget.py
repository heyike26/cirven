"""Shared source byte accounting; these cases have not yet been executed."""
from types import SimpleNamespace

import pytest

from apps.api.context_graph import ApplicationContextSources
from packages.domain import context_read_budget as budget_module
from packages.domain.context_graph import ContextGraph, AssociationChange, ScopeChange, SelectionInput, SourceRef
from packages.domain.store import Store, Conflict


def test_nested_source_read_scope_shares_allowance_and_resets_after_error(tmp_path):
    small = budget_module.ContextReadBudget(byte_limit=7, read_limit=3)
    path = tmp_path / 'private-record'
    path.write_bytes(b'12345')
    token = budget_module._CURRENT.set(small)
    try:
        with budget_module.context_read_scope() as outer:
            assert budget_module.source_bytes(path) == b'12345'
            with budget_module.context_read_scope() as inner:
                assert inner is outer is small
                with pytest.raises(budget_module.ContextReadLimit):
                    budget_module.source_bytes(path)
            assert outer.charged_bytes == 5 and outer.reads == 1
    finally:
        budget_module._CURRENT.reset(token)
    assert budget_module.current_context_read_budget() is None
    assert path.read_bytes() == b'12345'


def test_document_ranges_reuse_one_payload_but_exhausted_shared_budget_omits_them(tmp_path):
    store = Store(tmp_path / 'context.sqlite')
    pid = store.create_project('Budget scope', '')['id']
    adapter = ApplicationContextSources(SimpleNamespace(state=SimpleNamespace(store=store)))
    graph = ContextGraph(store, adapter.read, lambda _: {'project_id': pid})
    graph.save_scope('project', ScopeChange(operation_id='scope', expected_revision=0,
        project_id=pid, kind='PROJECT', label='Scope'))
    document = store.save_document(pid, 'one.md', b'First part, second part. ' * 1000, 'UPLOAD')
    ref = SourceRef(kind='DOCUMENT', project_id=pid, object_id=document['id'],
        revision=1, sha256=document['sha256'], length=10)
    for index, offset in enumerate((0, 12)):
        graph.associate(AssociationChange(operation_id='part_' + str(index), expected_revision=0,
            scope_id='project', source=ref.model_copy(update={'offset': offset})))
    normal = graph.select(SelectionInput(operation_id='normal', scope_id='project'))
    used = normal['source_read_budget']['charged_bytes']
    # Exactly the recorded allowance can handle both ranges because the
    # original document payload is charged once in this selection action.
    token = budget_module._CURRENT.set(budget_module.ContextReadBudget(byte_limit=used))
    try:
        selected = graph.select(SelectionInput(operation_id='reuse', scope_id='project'))
        assert len(selected['selected']) == 2
        assert selected['source_read_budget']['charged_bytes'] == used
    finally:
        budget_module._CURRENT.reset(token)
    token = budget_module._CURRENT.set(budget_module.ContextReadBudget(byte_limit=1))
    try:
        limited = graph.select(SelectionInput(operation_id='limited', scope_id='project'))
        assert not limited['selected']
        assert {item['reason'] for item in limited['omitted']} == {'SOURCE_BYTE_BUDGET'}
    finally:
        budget_module._CURRENT.reset(token)
    assert store.document(pid, document['id'])['sha256'] == document['sha256']


def test_failed_native_shared_reader_reserves_allowance_and_does_not_reopen_with_a_larger_limit():
    budget = budget_module.ContextReadBudget(byte_limit=20, read_limit=4)
    calls = []
    def existing_reader(limit):
        calls.append(limit)
        raise Conflict('Original shared-document read failed')
    with pytest.raises(Conflict):
        budget.guarded(existing_reader, 100)
    assert calls == [19] and budget.charged_bytes == 20
    with pytest.raises(budget_module.ContextReadLimit):
        budget.guarded(existing_reader, 100)
    assert calls == [19]
