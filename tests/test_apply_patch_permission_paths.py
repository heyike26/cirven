"""Literal native patch targets, real hook baselines; no original CLI/model.

Windows cases are pure path mapping. POSIX hook/Store fixtures use real files;
they are not Windows runtime qualification or an atomic write guarantee.
"""
import asyncio
from copy import deepcopy
import json
import ntpath
from pathlib import Path
import posixpath

import pytest

from packages.adapters.opencode_permission_paths import permission_paths
from packages.adapters.opencode_change_review import approve_guard
from packages.domain.store import Conflict, InvalidInput
from test_session_full_diffs import coding, Producer, observed, check


FIXTURE = Path(__file__).parent/'fixtures/source42-absolute-patch-pending.json'
WINDOWS_ROOT = json.loads(FIXTURE.read_text())['workspace_root']


def pending(root, operation='update', spelling='absolute'):
    windows = ':' in root
    module = ntpath if windows else posixpath
    source = 'new.py' if operation == 'add' else 'calc.py'
    absolute = module.join(root, source)
    destination = module.join(root, 'moved.py')
    def header(member):
        return module.join(root, member) if spelling == 'absolute' else ('./' if spelling == 'dot' else '')+member
    patch = '*** Begin Patch\n*** '+('Update' if operation == 'move' else operation.title())+' File: '+header(source)+'\n'
    if operation == 'move':
        patch += '*** Move to: '+header('moved.py')+'\n'
    if operation == 'add':
        patch += '+new\n'
    elif operation != 'delete':
        patch += '@@\n-old\n+new\n'
    patch += '*** End Patch'
    pattern = absolute[3:].replace('\\', '/') if windows else absolute[1:]
    display = destination if operation == 'move' else absolute
    file = {'filePath':absolute, 'relativePath':display[3:].replace('\\', '/') if windows else display[1:],
        'type':operation, 'patch':'--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new\n', 'additions':1, 'deletions':1}
    if operation == 'move':
        file['movePath'] = destination
    native = {'id':'per_unit','sessionID':'ses_unit','permission':'edit','patterns':[pattern],'always':['*'],
        'tool':{'messageID':'msg_unit','callID':'call_unit'},
        'metadata':{'filepath':pattern,'diff':file['patch'],'files':[file]}}
    message = {'info':{'id':'msg_unit','role':'assistant','sessionID':'ses_unit','path':{'cwd':root,'root':'/'}},
        'parts':[{'type':'tool','tool':'apply_patch','sessionID':'ses_unit','messageID':'msg_unit','callID':'call_unit',
            'state':{'status':'running','input':{'patchText':patch}}}]}
    return native, message


@pytest.mark.parametrize('root', [WINDOWS_ROOT, '/own/space workspace'])
@pytest.mark.parametrize('operation', ['add','update','delete','move'])
@pytest.mark.parametrize('spelling', ['absolute','relative','dot'])
def test_all_native_patch_target_kinds_use_one_bound_cwd(root, operation, spelling):
    n,m = pending(root, operation, spelling)
    original = deepcopy((n,m))
    expected = ['new.py'] if operation == 'add' else ['calc.py','moved.py'] if operation == 'move' else ['calc.py']
    check(permission_paths(n,m,root) == expected)
    check((n,m) == original, 'Raw patch, args, metadata and request identities must stay unchanged')


def test_exact_archived_windows_pending_update_now_maps_without_rewriting():
    value = json.loads(FIXTURE.read_text())
    n,m = value['native'], value['message']
    before = deepcopy((n,m))
    check(permission_paths(n,m,value['workspace_root']) == ['calc.py'])
    check((n,m) == before)


@pytest.mark.parametrize('fault', ['outside','move-outside','other-drive','drive-relative','unc','device','traversal',
    'absolute-dot','repeat-dot','wildcard','nul','duplicate-alias','metadata-source','metadata-move','pattern','call'])
def test_windows_literal_aliases_and_mismatched_native_evidence_still_refuse(fault):
    n,m = pending(WINDOWS_ROOT, 'move' if fault in {'move-outside','metadata-move'} else 'update')
    part = m['parts'][0]
    patch = part['state']['input']['patchText']
    target = WINDOWS_ROOT+'\\calc.py'
    bad = {'outside':r'D:\outside\calc.py','other-drive':target.replace('D:','E:'),'drive-relative':'D:calc.py',
        'unc':r'\\server\share\calc.py','device':r'\\?\D:\calc.py','traversal':r'nested\..\calc.py',
        'absolute-dot':target[:3]+'.\\'+target[3:],'repeat-dot':'././calc.py','wildcard':WINDOWS_ROOT+r'\*.py',
        'nul':target+'\0'}
    if fault in bad:
        part['state']['input']['patchText'] = patch.replace(target,bad[fault])
    elif fault == 'move-outside':
        part['state']['input']['patchText'] = patch.replace(WINDOWS_ROOT+'\\moved.py',r'D:\outside\moved.py')
    elif fault == 'duplicate-alias':
        extra='*** Update File: ./calc.py\n@@\n-old\n+new\n'
        part['state']['input']['patchText']=patch.replace('*** End Patch',extra+'*** End Patch')
        n['metadata']['files'].append(deepcopy(n['metadata']['files'][0]));n['patterns']*=2
        n['metadata']['filepath']=', '.join(n['patterns'])
    elif fault == 'metadata-source':n['metadata']['files'][0]['filePath']=WINDOWS_ROOT+'\\different.py'
    elif fault == 'metadata-move':n['metadata']['files'][0]['movePath']=WINDOWS_ROOT+'\\different.py'
    elif fault == 'pattern':n['patterns']=['calc.py']
    elif fault == 'call':n['tool']['callID']='another_call'
    with pytest.raises((ValueError,InvalidInput)):
        permission_paths(n,m,WINDOWS_ROOT)


def test_real_before_ledger_accepts_mixed_absolute_dot_relative_multifile_targets(coding):
    s=coding;root=s['folder'];(root/'nested').mkdir()
    for name in ['delete.py','move.py']:(root/'nested'/name).write_text('old\n')
    args={'patchText':f'*** Begin Patch\n*** Update File: {root}/a.py\n@@\n-old\n+new\n*** Add File: ./new.py\n+new\n*** Delete File: {root}/nested/delete.py\n*** Update File: nested/move.py\n*** Move to: {root}/nested/moved.py\n@@\n-old\n+new\n*** End Patch'}
    p=Producer(s,tool='apply_patch',args=args)
    try:
        m=p.message('running');m['info']['path']['root']='/'
        source=['a.py','new.py','nested/delete.py','nested/move.py'];types=['update','add','delete','move'];files=[]
        for name,kind in zip(source,types):
            display='nested/moved.py'if kind=='move'else name
            files.append({'filePath':str(root/name),'relativePath':str(root/display).lstrip('/'),'type':kind,
                'patch':'--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new\n','additions':1,'deletions':1,
                **({'movePath':str(root/display)}if kind=='move'else{})})
        patterns=[str(root/name).lstrip('/')for name in source]
        n={'id':'per_one','sessionID':'ses_root','permission':'edit','patterns':patterns,'always':['*'],
            'tool':{'messageID':'msg_one','callID':'call_one'},'metadata':{'filepath':', '.join(patterns),
            'diff':'\n'.join(row['patch']for row in files),'files':files}}
        reviewed=approve_guard(s['admission'],observed(s,[m]),n,m,workspaces=s['value'].workspaces,binding=s['binding'])
        check({r['path']:r['state']for r in reviewed['files']}=={'a.py':'UTF8','new.py':'ABSENT','nested/delete.py':'UTF8','nested/move.py':'UTF8','nested/moved.py':'ABSENT'})
        (root/'nested/moved.py').write_text('new human file\n')
        with pytest.raises(Conflict):approve_guard(s['admission'],observed(s,[m]),n,m,workspaces=s['value'].workspaces,binding=s['binding'])
        check((root/'a.py').read_text()=='keep\nold\ntail\n'and(root/'nested/moved.py').read_text()=='new human file\n')
    finally:p.finish(False)
