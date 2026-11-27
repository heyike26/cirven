"""Typed input/hash compatibility only, not native execution qualification.

Authored for concentrated debug; no test invocation has occurred this batch.
"""
import pytest
from pydantic import ValidationError

from apps.api.native_messages import NativeQuestionInput


def test_absent_selection_preserves_legacy_native_body_and_explicit_selection_is_exact():
    plain = NativeQuestionInput(operation_id='original', raw_text='Keep the actual input')
    assert 'context_selection_id' not in plain.request_dict()
    selected = NativeQuestionInput(operation_id='original', raw_text='Keep the actual input',
        context_selection_id='a' * 64)
    body = selected.request_dict()
    assert body.pop('context_selection_id') == 'a' * 64
    assert body == plain.request_dict()


@pytest.mark.parametrize('invalid', ['short', 'A' * 64, '/user/path', 123, True])
def test_native_context_selection_cannot_be_an_unbounded_path_or_coerced_reference(invalid):
    with pytest.raises(ValidationError):
        NativeQuestionInput(operation_id='original', raw_text='Keep the actual input',
                            context_selection_id=invalid)
