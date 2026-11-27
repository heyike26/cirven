"""Exact pinned native path evidence; real Store/service, UNIT SDK/OS boundaries.

Pure Windows mapping is not Windows I/O or native model qualification. Existing
real producer fixtures protect both present-byte and ABSENT write baselines.
"""
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path

import pytest

from packages.adapters.opencode_permission_paths import permission_paths, check_read_target, native_write_policy
from packages.adapters.opencode_change_review import approve_guard
from packages.domain.store import Conflict, InvalidInput
from packages.domain.tasks import digest
from test_agent_sessions_deadline_recovery import Scene, clock, check
from test_session_full_diffs import coding, Producer, native, observed, saved


WIN_ROOT = r'D:\EEAS-qualification-20261015\source39-code-full-diff-native-qa\workspace'


def original(root=WIN_ROOT, *, tool='read', relative_input=False, worktree='/'):
    windows = ':' in root
    target = root + ('\\calc.py' if windows else '/calc.py')
    raw_input = 'calc.py' if relative_input else target
    pattern = target[3:] if windows and worktree == '/' else target[1:] if worktree == '/' else 'calc.py'
    n = {'id':'per_one','sessionID':'ses_one','permission':'read' if tool == 'read' else 'edit',
         'patterns':[pattern],'always':['*'],'tool':{'messageID':'msg_one','callID':'call_one'},'metadata':{}}
    if tool != 'read': n['metadata'] = {'filepath':target,'diff':'a real original diff is checked separately'}
    m = {'info':{'id':'msg_one','role':'assistant','sessionID':'ses_one','path':{'cwd':root,'root':worktree}},
         'parts':[{'id':'prt_one','messageID':'msg_one','sessionID':'ses_one','type':'tool','callID':'call_one',
                   'tool':tool,'state':{'status':'running','input':{'filePath':raw_input}}}]}
    return n, m


@pytest.mark.parametrize('root', [WIN_ROOT, '/own/space workspace'])
@pytest.mark.parametrize('tool', ['read','edit','write'])
@pytest.mark.parametrize('relative_input', [False, True])
def test_worktree_root_literal_maps_exact_original_target_without_rewriting(root, tool, relative_input):
    n,m = original(root, tool=tool, relative_input=relative_input)
    before = deepcopy((n,m))
    check(permission_paths(n,m,root) == ['calc.py'])
    check((n,m) == before, 'Original patterns/input/metadata/IDs must not be rewritten')


@pytest.mark.parametrize('root', [WIN_ROOT, '/own/workspace'])
def test_explicit_worktree_cwd_and_ancestor_worktree_follow_same_native_rule(root):
    n,m=original(root,worktree=root)
    check(permission_paths(n,m,root)==['calc.py'])
    ancestor=root.rsplit('\\',1)[0] if ':'in root else '/own'
    m['info']['path']['root']=ancestor;n['patterns']=['workspace/calc.py']
    check(permission_paths(n,m,root)==['calc.py'])


@pytest.mark.parametrize('root', [WIN_ROOT, '/own/workspace'])
@pytest.mark.parametrize('spelling', ['./calc.py', '.', './'])
def test_exact_cwd_prefix_and_root_directory_read_use_original_resolve_semantics(root, spelling):
    n,m=original(root)
    m['parts'][0]['state']['input']['filePath']=spelling
    if spelling in {'.','./'}:
        n['patterns']=[root[3:] if ':'in root else root[1:]]
    check(permission_paths(n,m,root)==(['calc.py']if 'calc' in spelling else ['']))
    if spelling != './calc.py':
        m['info']['path']['root']=root;n['patterns']=['']
        check(permission_paths(n,m,root)==[''])


@pytest.mark.parametrize('root', [WIN_ROOT, '/own/workspace'])
def test_original_path_api_projects_only_existing_grants_not_suffix_or_global_asks(root):
    workspace={'root':root,'allowed_write_paths':['calc.py','delivery/']}
    policy=native_write_policy(workspace,{'directory':root,'worktree':'/'})
    prefix=root[3:].replace('\\','/')if ':'in root else root[1:]
    check(policy['*']=='deny'and policy[prefix+'/calc.py']=='ask'and policy[prefix+'/delivery/*']=='ask')
    check(policy['*/.git/*']=='deny'and 'calc.py'not in policy)
    for bad in [{'directory':root+'-other','worktree':'/'},{'directory':root,'worktree':root+'-other'}]:
        with pytest.raises((ValueError,InvalidInput)):native_write_policy(workspace,bad)
    original=native_write_policy(workspace,{'directory':root,'worktree':root})
    check(original['*']=='deny'and original['calc.py']=='ask'and original['delivery/*']=='ask')


@pytest.mark.parametrize('mode', ['new-global','new-root','wrong-directory','wrong-worktree','root-replaced','existing-old','existing-exact','wrong-created-policy'])
def test_real_service_native_admission_uses_original_path_and_never_patches_existing_sessions(coding, mode):
    async def run():
        import httpx,json
        scene=coding;owner=scene['value'];root=scene['folder'];m=deepcopy(scene['manifest']);m['request']['title']='Explicit original write session'
        config={'permission':owner._permissions(m['request'],scene['binding'])}
        original_config=deepcopy(config);trace=[]
        location={'directory':str(root),'worktree':str(root)if mode=='new-root'else'/'}
        if mode=='wrong-directory':location['directory']=str(root)+'-other'
        if mode=='wrong-worktree':location['worktree']=str(root)+'-other'
        policy=native_write_policy(scene['binding'],{'directory':str(root),'worktree':'/'})
        expected=[{'permission':'edit','pattern':p,'action':v}for p,v in policy.items()]
        async def boundary(request):
            body=json.loads(request.content)if request.content else None;trace.append((request.method,request.url.path,body))
            check(request.url.params['directory']==str(root))
            if request.method=='GET'and request.url.path=='/path':return httpx.Response(200,json=location)
            if request.method=='GET'and request.url.path=='/session/ses_existing':
                old=[{'permission':'edit','pattern':'a.py','action':'ask'}]if mode=='existing-old'else expected
                return httpx.Response(200,json={'id':'ses_existing','directory':str(root),'permission':old})
            if request.method=='POST'and request.url.path=='/session':
                return httpx.Response(200,json={'id':'ses_root','directory':str(root),'permission':[]if mode=='wrong-created-policy'else body['permission']})
            raise AssertionError('No PATCH/model/tool/other original endpoint is allowed')
        async with httpx.AsyncClient(base_url='http://sdk-unit.invalid',transport=httpx.MockTransport(boundary),trust_env=False)as client:
            context={'generation':owner._state(scene['pid'],scene['ident'])['generation'],
                     'directory':str(root),'session_id':'ses_existing'if mode.startswith('existing-')else None,'change_review':scene['admission'],'client':client}
            if mode=='root-replaced':root.rename(root.with_name('preserved-original'));root.mkdir()
            try:
                if mode in {'wrong-directory','wrong-worktree','root-replaced','existing-old','wrong-created-policy'}:
                    with pytest.raises((Conflict,ValueError,InvalidInput)):await owner._native_session(m,context,config)
                else:
                    result=await owner._native_session(m,context,config)
                    check(result['id']==context['session_id'])
                    receipt=owner._state(scene['pid'],scene['ident'])['native_initialization']
                    check(receipt=={'stage':'NATIVE_SESSION_KNOWN', 'generation':context['generation'],
                                    'manifest_sha256':digest(m), 'original_session_id':context['session_id']})
                    if mode=='existing-exact':check([x[0]for x in trace]==['GET','GET'])
                    else:
                        check([x[:2]for x in trace]==[('GET','/path'),('POST','/session')])
                        sent=trace[-1][2]['permission'];edits=[x for x in sent if x['permission']=='edit']
                        prefix=''if mode=='new-root'else str(root).lstrip('/')+'/'
                        check({'permission':'edit','pattern':prefix+'a.py','action':'ask'}in edits)
                        check({'permission':'edit','pattern':'*','action':'deny'}in edits)
                        check(not any(x['action']=='allow'for x in edits))
                check(not any(x[0]=='PATCH'for x in trace)and config==original_config)
                if mode in {'wrong-directory','wrong-worktree','root-replaced','existing-old'}:check(not any(x[0]=='POST'for x in trace))
            finally:
                if mode=='root-replaced':root.rmdir();root.with_name('preserved-original').rename(root)
    asyncio.run(run())


@pytest.mark.parametrize('failure', ['call','message','session','part-session','duplicate','completed','cwd','worktree',
    'missing-path','pattern','wildcard','traversal','drive-relative','unc','device','ads','outside','other-drive','prefix-collision'])
def test_windows_original_evidence_and_path_confusions_are_refused(failure):
    n,m=original();part=m['parts'][0]
    if failure=='call': n['tool']['callID']='another'
    elif failure=='message': n['tool']['messageID']='msg_another'
    elif failure=='session': n['sessionID']='ses_another'
    elif failure=='part-session': part['sessionID']='ses_other'
    elif failure=='duplicate':m['parts'].append(deepcopy(part))
    elif failure=='completed':part['state']['status']='completed'
    elif failure=='cwd':m['info']['path']['cwd']=WIN_ROOT+'-other'
    elif failure=='worktree':m['info']['path']['root']=r'D:\unrelated'
    elif failure=='missing-path':m['info'].pop('path')
    elif failure=='pattern':n['patterns']=['calc.py']
    elif failure=='wildcard':n['patterns']=['*']
    elif failure=='traversal':part['state']['input']['filePath']=r'nested\..\calc.py'
    elif failure=='drive-relative':part['state']['input']['filePath']='D:calc.py'
    elif failure=='unc':part['state']['input']['filePath']=r'\\server\share\calc.py'
    elif failure=='device':part['state']['input']['filePath']=r'\\?\D:\calc.py'
    elif failure=='ads':part['state']['input']['filePath']=WIN_ROOT+r'\calc.py:secret'
    elif failure=='outside':part['state']['input']['filePath']=r'D:\elsewhere\calc.py'
    elif failure=='other-drive':part['state']['input']['filePath']=WIN_ROOT.replace('D:','E:')+r'\calc.py'
    elif failure=='prefix-collision':part['state']['input']['filePath']=WIN_ROOT+r'-sibling\calc.py'
    with pytest.raises((ValueError,InvalidInput)):
        permission_paths(n,m,WIN_ROOT)


@pytest.mark.parametrize('mode',['absolute','relative','late-input-change','symlink','root-replaced','unknown-ack'])
def test_real_service_first_inspection_and_reply_use_same_fresh_target(tmp_path, clock, mode):
    async def run():
        scene=Scene(tmp_path)
        try:
            accepted=await scene.send()
            m,n=scene.sdk.tool_wait(accepted['native_message_id'])
            m['info']['path']['root']='/'
            m['parts'][0]['state']['input']['filePath']='owned.txt' if mode=='relative' else str(scene.workspace/'owned.txt')
            n['patterns']=[str(scene.workspace/'owned.txt').lstrip('/')]
            original_native=deepcopy(n)
            view=await scene.inspect()
            pending=view['pending_permissions'][0]
            check(pending['allowed_choices']==['once','reject'], 'First snapshot must use its fresh native tool message')
            body=scene.body('allow-once',request_ref=pending['request_ref'],reply='once')
            if mode=='late-input-change':m['parts'][0]['state']['input']['filePath']=str(tmp_path/'outside.txt')
            elif mode=='symlink':
                (scene.workspace/'owned.txt').unlink();(scene.workspace/'owned.txt').symlink_to(tmp_path/'outside.txt')
            elif mode=='root-replaced':
                scene.workspace.rename(tmp_path/'preserved-workspace');scene.workspace.mkdir();(scene.workspace/'owned.txt').write_bytes(scene.original)
            if mode=='unknown-ack':
                original_handle=scene.sdk.handle
                async def handle(request):
                    if request.method=='POST'and request.url.path.endswith('/permission/per_wait/reply'):
                        import httpx
                        scene.sdk.posts.append({'path':request.url.path,'body':{'reply':'once'}})
                        raise httpx.ReadTimeout('UNIT reply ACK lost',request=request)
                    return await original_handle(request)
                import httpx
                await scene.client.aclose();scene.client=httpx.AsyncClient(base_url='http://sdk-unit.invalid',transport=httpx.MockTransport(handle),trust_env=False);scene.context['client']=scene.client
            if mode in {'late-input-change','symlink','root-replaced'}:
                with pytest.raises(Conflict):await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')
                check(scene.sdk.post_count('/reply')==0)
            elif mode=='unknown-ack':
                import httpx
                with pytest.raises(httpx.ReadTimeout):await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')
                op=scene.owner.operation(scene.pid,scene.sid,'allow-once');check(op['status']=='UNKNOWN_OUTCOME')
                check(await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')==op)
                check(scene.sdk.post_count('/reply')==1)
            else:
                result=await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')
                check(result['status']=='RETURNED'and result['original_request_processed']is True)
                check(scene.sdk.post_count('/reply')==1)
                check(await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')==result)
                check(scene.sdk.post_count('/reply')==1)
            check(n==original_native, 'Native permission/ref stays unchanged')
        finally:
            if mode=='symlink':(scene.workspace/'owned.txt').unlink();(scene.workspace/'owned.txt').write_bytes(scene.original)
            await scene.close()
    asyncio.run(run())


@pytest.mark.parametrize('mode', ['glob-directory','glob-file','grep-file','query-mismatch','external','late-path','symlink'])
def test_search_query_is_not_a_path_and_effective_directory_is_rechecked(tmp_path, clock, mode):
    async def run():
        scene=Scene(tmp_path)
        try:
            sent=await scene.send();m,n=scene.sdk.tool_wait(sent['native_message_id'])
            tool='grep'if mode=='grep-file'else'glob';query='old|new'if tool=='grep'else'**/*.py'
            args={'pattern':query}
            if mode in {'grep-file','glob-file'}:args['path']='owned.txt'
            elif mode=='external':args['path']=str(tmp_path)
            elif mode=='symlink':
                (scene.workspace/'linked').symlink_to(tmp_path,target_is_directory=True);args['path']='linked'
            part=m['parts'][0];part.update(tool=tool);part['state']['input']=args
            n.update(permission=tool,patterns=[query],metadata=deepcopy(args))
            if mode=='query-mismatch':n['metadata']['pattern']='different'
            original_native=deepcopy(n)
            view=await scene.inspect();pending=view['pending_permissions'][0]
            allowed=mode in {'glob-directory','grep-file','late-path'}
            check(pending['allowed_choices']==(['once','reject']if allowed else['reject']))
            if allowed:
                if mode=='late-path':args['path']=str(tmp_path)
                body=scene.body('search-once',request_ref=pending['request_ref'],reply='once')
                if mode=='late-path':
                    with pytest.raises(Conflict):await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')
                    check(scene.sdk.post_count('/reply')==0)
                else:
                    answer=await scene.owner.reply(scene.pid,scene.sid,'per_wait',body,kind='PERMISSION')
                    check(answer['status']=='RETURNED'and scene.sdk.post_count('/reply')==1)
            check(n==original_native)
        finally:await scene.close()
    asyncio.run(run())


@pytest.mark.parametrize('tool,target', [('edit','a.py'),('write','new.py')])
def test_real_before_ledger_accepts_worktree_pattern_and_preserves_present_or_absent_guard(coding, tool, target):
    scene=coding;p=Producer(scene,tool=tool,target=target,relative_input=tool=='write')
    try:
        n=native(scene,p);m=p.message('running');m['info']['path']['root']='/'
        n['metadata']['filepath']=str(scene['folder']/target)
        n['patterns']=[str(scene['folder']/target).lstrip('/')]
        original_native=deepcopy(n)
        saved(scene,[m]);scene['value'].active[scene['ident']]={'session_id':'ses_root','generation':1,'closing':False,'change_review':scene['admission']}
        check(scene['value']._permission_choices(n,scene['manifest'])==['once','reject'])
        review=approve_guard(scene['admission'],observed(scene,[m]),n,m,workspaces=scene['value'].workspaces,binding=scene['binding'])
        check(review['files'][0]['path']==target and review['files'][0]['state']==('UTF8'if tool=='edit'else'ABSENT'))
        (scene['folder']/target).write_text('HUMAN_SAVE_MUST_NOT_BE_GRANTED\n')
        with pytest.raises(Conflict):approve_guard(scene['admission'],observed(scene,[m]),n,m,workspaces=scene['value'].workspaces,binding=scene['binding'])
        check((scene['folder']/target).read_text()=='HUMAN_SAVE_MUST_NOT_BE_GRANTED\n'and n==original_native)
    finally:p.finish(False)


def test_actual_patch_before_ledger_preserves_multifile_move_and_absent_guards(coding):
    scene=coding;root=scene['folder'];(root/'nested').mkdir();(root/'nested/old.py').write_text('old\n')
    args={'patchText':'*** Begin Patch\n*** Update File: a.py\n@@\n-old\n+new\n*** Add File: new.py\n+new\n*** Update File: nested/old.py\n*** Move to: nested/moved.py\n@@\n-old\n+new\n*** End Patch'}
    p=Producer(scene,tool='apply_patch',args=args)
    try:
        m=p.message('running');m['info']['path']['root']='/'
        paths=['a.py','new.py','nested/old.py']
        patterns=[str(root/path).lstrip('/')for path in paths]
        files=[]
        for path,kind in zip(paths,['update','add','move']):
            destination='nested/moved.py'if kind=='move'else path
            files.append({'filePath':str(root/path),'relativePath':str(root/destination).lstrip('/'),
                'type':kind,'patch':'--- before\n+++ after\n@@ -1 +1 @@\n-old\n+new\n','additions':1,'deletions':1,
                **({'movePath':str(root/destination)}if kind=='move'else{})})
        n={'id':'per_one','sessionID':'ses_root','permission':'edit','patterns':patterns,'always':['*'],
            'tool':{'messageID':'msg_one','callID':'call_one'},
            'metadata':{'filepath':', '.join(patterns),'diff':'\n'.join(x['patch']for x in files),'files':files}}
        snapshot=deepcopy((n,m));check(permission_paths(n,m,str(root))==['a.py','new.py','nested/old.py','nested/moved.py'])
        result=approve_guard(scene['admission'],observed(scene,[m]),n,m,workspaces=scene['value'].workspaces,binding=scene['binding'])
        check({x['path']:x['state']for x in result['files']}=={'a.py':'UTF8','new.py':'ABSENT','nested/old.py':'UTF8','nested/moved.py':'ABSENT'})
        for bad in ['pattern','move','display','args','grant','human-save']:
            bn,bm=deepcopy(snapshot)
            if bad=='pattern':bn['patterns'][1]='new.py'
            elif bad=='move':bn['metadata']['files'][2]['movePath']=str(root.parent/'external.py')
            elif bad=='display':bn['metadata']['files'][2]['relativePath']=patterns[2]
            elif bad=='args':bm['parts'][0]['state']['input']['patchText']=args['patchText'].replace('+new','+changed')
            elif bad=='grant':bn['metadata']['files'][2]['movePath']=str(root/'ungranted.py')
            elif bad=='human-save':(root/'nested/moved.py').write_text('human\n')
            with pytest.raises(Conflict):approve_guard(scene['admission'],observed(scene,[bm]),bn,bm,workspaces=scene['value'].workspaces,binding=scene['binding'])
        check((root/'nested/moved.py').read_text()=='human\n'and(n,m)==snapshot)
    finally:p.finish(False)


@pytest.mark.parametrize('kind', ['add','move'])
def test_patch_never_approves_a_destination_that_already_existed_at_capture(coding, kind):
    scene=coding;root=scene['folder'];(root/'new.py').write_text('ORIGINAL_HUMAN_DOCUMENT\n')
    args={'patchText':'*** Begin Patch\n'+('*** Add File: new.py\n+new\n'if kind=='add'else'*** Update File: a.py\n*** Move to: new.py\n@@\n-old\n+new\n')+'*** End Patch'}
    p=Producer(scene,tool='apply_patch',args=args)
    try:
        m=p.message('running');m['info']['path']['root']='/'
        source=root/('new.py'if kind=='add'else'a.py');pattern=str(source).lstrip('/')
        item={'filePath':str(source),'relativePath':str(root/'new.py').lstrip('/'),'type':kind,
            'patch':'--- original\n+++ next\n@@ -1 +1 @@\n-old\n+new\n','additions':1,'deletions':1,
            **({'movePath':str(root/'new.py')}if kind=='move'else{})}
        n={'id':'per_one','sessionID':'ses_root','permission':'edit','patterns':[pattern],'always':['*'],
            'tool':{'messageID':'msg_one','callID':'call_one'},'metadata':{'filepath':pattern,'diff':item['patch'],'files':[item]}}
        check(permission_paths(n,m,str(root))==(['new.py']if kind=='add'else['a.py','new.py']))
        with pytest.raises(Conflict):approve_guard(scene['admission'],observed(scene,[m]),n,m,workspaces=scene['value'].workspaces,binding=scene['binding'])
        check((root/'new.py').read_text()=='ORIGINAL_HUMAN_DOCUMENT\n')
    finally:p.finish(False)


def test_metadata_check_uses_existing_native_handle_boundary_without_reading_bytes(tmp_path):
    (tmp_path/'a.py').write_text('Do not read these bytes')
    class API:
        def open_relative(self,parent,name,access,sharing,**kwargs):
            check(parent==1 and name=='a.py'and access==0x80 and kwargs['directory']is False);return 2
        def close(self,handle):check(handle==2)
        def read(self,*args):raise AssertionError('Permission display cannot read file bytes')
    class Native:
        api=API()
        def _check(self,handle,path,*,directory):check(handle==2 and directory is False)
    class Workspaces:
        def _native_handles(self):return Native()
        @contextmanager
        def _parent(self,binding,path):
            from types import SimpleNamespace
            check(path=='a.py');yield SimpleNamespace(native_handle=1,path=str(tmp_path)), 'a.py'
    check_read_target(Workspaces(),{'root':str(tmp_path)},'a.py')
