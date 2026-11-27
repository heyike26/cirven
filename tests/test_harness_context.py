import hashlib
import json
from packages.planning.harness_context import context_files
from packages.domain.local_workspaces import FileEditInput


def test_large_chinese_emoji_context_reconstructs_exact_source_without_exceeding_workspace_budget():
    text=json.dumps({'original':'资料😀\n'*180000},ensure_ascii=False)+'\n'
    files=context_files('discovery-context.json',text)
    index=json.loads(files[0]['content']);parts={p['filename']:p['content'].encode() for p in files[1:]}
    reconstructed=b''.join(parts[p['filename']] for p in index['parts'])
    assert reconstructed==text.encode() and hashlib.sha256(reconstructed).hexdigest()==index['whole_sha256']
    assert json.loads(reconstructed)['original']=='资料😀\n'*180000
    for p in files:FileEditInput(operation_id='part',path=p['filename'],expected_revision=0,content=p['content'])


def test_small_context_keeps_complete_regular_file():
    text='# 需求\n\n原文😀\n';files=context_files('discovery-context.md',text)
    assert len(files)==1 and files[0]['content']==text
