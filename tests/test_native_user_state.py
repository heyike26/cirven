"""Actual Windows private-state recovery. No pytest dependency or real model."""
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from scripts import native_user_state as recovery


def pin(path):
    raw=path.read_bytes()
    return {'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}


class NativeCacheExclusionPolicyTests(unittest.TestCase):
    CACHE = ('agent-sessions/11111111-2222-4333-8444-555555555555/'
             '0123456789abcdef0123456789abcdef/runtime/home/'
             'AppData/Local/Microsoft/Windows/INetCache')
    SHADER_CACHE = 'home/AppData/LocalLow/NVIDIA/PerDriverVersion/DXCache'

    def test_shader_cache_is_versioned_and_bounded_to_actual_home_layouts(self):
        agent = self.CACHE.rsplit('/home/',1)[0] + '/' + self.SHADER_CACHE
        for root in (self.SHADER_CACHE, agent):
            for name in (root,root+'/driver.fixture',root.upper()+'/DRIVER.BIN'):
                with self.subTest(name=name):
                    self.assertTrue(recovery._excluded(name))
                    for old in (recovery.LEGACY_CACHE_EXCLUSION_POLICY,recovery.AGENT_HOME_CACHE_EXCLUSION_POLICY):
                        self.assertFalse(recovery._excluded(name,cache_exclusion_policy=old))
        for name in ('prefix/'+self.SHADER_CACHE,self.SHADER_CACHE+'-durable/settings.json',
                     self.SHADER_CACHE.replace('/DXCache','/settings.json'),
                     self.SHADER_CACHE.replace('home/','project/',1),
                     'project/'+self.SHADER_CACHE,agent.replace('11111111-2222-4333-8444-555555555555','bad-project'),
                     agent.replace('0123456789abcdef0123456789abcdef','bad-session'),
                     agent.replace('/runtime/home/','/runtime/home-copy/'),
                     'subscription-account/opaque.bin','agent-sessions/exact/operations/original.json'):
            with self.subTest(name=name):self.assertFalse(recovery._excluded(name))

    def test_shader_policy_preserves_v1_wininet_cache_rule_and_default_scope(self):
        self.assertEqual(recovery.CURRENT_CACHE_EXCLUSION_POLICY,recovery.WORKFLOW_CACHE_EXCLUSION_POLICY)
        self.assertTrue(recovery._excluded(self.CACHE,cache_exclusion_policy=recovery.SHADER_CACHE_EXCLUSION_POLICY))
        with tempfile.TemporaryDirectory(dir=os.environ.get('EEAS_EVIDENCE_DIR')) as directory:
            root=Path(directory);cache=root/self.SHADER_CACHE/'locked.fixture'
            cache.parent.mkdir(parents=True);cache.write_bytes(b'not read under v2')
            durable=root/'manual.txt';durable.write_bytes(b'original manual')
            original=recovery._hash
            def read(path):
                if Path(path)==cache:raise PermissionError(13,'synthetic locked shader fixture',str(path))
                return original(path)
            with patch.object(recovery,'_hash',read):
                rows,_=recovery._inventory(root)
                self.assertEqual(rows['manual.txt']['sha256'],original(durable))
                self.assertFalse(any(name.startswith(self.SHADER_CACHE) for name in rows))
                with self.assertRaises(PermissionError):
                    recovery._inventory(root,cache_exclusion_policy=recovery.AGENT_HOME_CACHE_EXCLUSION_POLICY)
                with self.assertRaises(PermissionError):recovery._inventory(root,exclude=False)
            self.assertEqual(cache.read_bytes(),b'not read under v2')

    def test_shader_scope_does_not_skip_adjacent_durable_read_failures(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('EEAS_EVIDENCE_DIR')) as directory:
            root=Path(directory);p=root/(self.SHADER_CACHE+'-durable')/'settings.json'
            p.parent.mkdir(parents=True);p.write_bytes(b'durable neighboring preferences')
            with patch.object(recovery,'_hash',side_effect=PermissionError(13,'synthetic durable lock',str(p))):
                with self.assertRaises(PermissionError):recovery._inventory(root)

    def test_new_policy_skips_only_canonical_agent_home_cache_scope(self):
        for name in (self.CACHE, self.CACHE+'/Content.IE5', self.CACHE+'/IE/item', self.CACHE.upper()):
            with self.subTest(name=name):self.assertTrue(recovery._excluded(name))
        self.assertFalse(recovery._excluded(self.CACHE,cache_exclusion_policy=recovery.LEGACY_CACHE_EXCLUSION_POLICY))
        for name in ('prefix/'+self.CACHE, self.CACHE.replace('11111111-2222-4333-8444-555555555555','not-a-uuid'),
                     self.CACHE.replace('0123456789abcdef0123456789abcdef','not-a-session'),
                     self.CACHE.replace('/runtime/home/','/runtime/home-copy/'),
                     self.CACHE.replace('/runtime/home/','/runtime/project/'),
                     self.CACHE+'-durable', self.CACHE.replace('/INetCache','/INetCookies'),
                     self.CACHE.replace('/home/','/project/'), self.CACHE.replace('/AppData/','/documents/')):
            with self.subTest(name=name):self.assertFalse(recovery._excluded(name))

    def test_original_global_cache_and_ephemeral_exclusions_hold_for_both_policies(self):
        for policy in recovery.CACHE_EXCLUSION_POLICIES:
            for name in (*recovery.EXCLUDED,'home/AppData/Local/Microsoft/Windows/INetCache/Content.IE5','local-access.token/child'):
                with self.subTest(policy=policy,name=name):
                    self.assertTrue(recovery._excluded(name,cache_exclusion_policy=policy))
            self.assertFalse(recovery._excluded('agent-sessions/ordinary/durable.json',cache_exclusion_policy=policy))

    def test_unknown_policy_is_never_treated_as_a_cache_exception(self):
        for value in ('future-policy','',None,True,[],{}):
            with self.subTest(policy=value),self.assertRaisesRegex(recovery.RecoveryError,'cache exclusion policy'):
                recovery._excluded(self.CACHE,cache_exclusion_policy=value)


@unittest.skipUnless(os.name=='nt','Windows ACL and no-replace filesystem semantics require native Windows')
class NativeUserStateTests(unittest.TestCase):
    def setUp(self):
        # Keep actual failures and restore fixtures; cleanup removes only owned junctions.
        self.base=Path(tempfile.mkdtemp(prefix='native-state-',dir=os.environ['EEAS_EVIDENCE_DIR']))
        self.root=self.base/'Source install 中文';self.root.mkdir()
        payload={'runtime/electron/version':b'42.3.3','runtime/opencode/opencode.exe':b'original synthetic CLI bytes',
            'packages/domain/migrations/001_original.sql':b'original migration fixture'}
        rows=[]
        for name,raw in payload.items():
            p=self.root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw);rows.append({'path':name,**pin(p)})
        (self.root/'PACKAGE_MANIFEST.json').write_text(json.dumps({'schema':'eeas.native-companion-package.v1','files':rows}))
        data=self.root/'user-data';data.mkdir()
        with sqlite3.connect(data/'eeas.sqlite3')as db:
            db.executescript("CREATE TABLE schema_migrations(version TEXT);INSERT INTO schema_migrations VALUES('001_original.sql');CREATE TABLE operations(id TEXT,status TEXT);INSERT INTO operations VALUES('original-unknown','UNKNOWN_OUTCOME');")
        for name,raw in {'agent-sessions/exact/operations/original.json':b'{"status":"UNKNOWN_OUTCOME","generation":7}',
            'appdata/Local Storage/original-draft':'未发送 原文\n😀'.encode(),
            'subscription-account/opaque.bin':b'original synthetic opaque credential fixture',
            'quick-assistant-journal.json':b'{"copied_to":"original","draft":"not submitted"}',
            'local-access.token':b'ephemeral token fixture','controller-port.json':b'{"schema":"eeas.controller-port.v1","port":1234}'}.items():
            p=data/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
        (data/'empty-durable-directory').mkdir()
    def target(self, name='Restored install'):
        root=self.base/name;root.mkdir()
        for name in ['PACKAGE_MANIFEST.json',*recovery.COMPATIBILITY,'packages/domain/migrations/001_original.sql']:
            p=root/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes((self.root/name).read_bytes())
        return root
    def saved(self):
        path=self.base/'backup';return path,recovery.backup(self.root,path)

    def test_renderer_origin_is_pinned_without_reusing_a_live_service_port(self):
        saved,receipt=self.saved()
        value,_=recovery.verify(saved,receipt['manifest_sha256'])
        self.assertEqual(value['renderer_origin'],'http://127.0.0.1:1234')
        target=self.target();recovery.restore(saved,target,receipt['manifest_sha256'])
        proof=json.loads((target/'user-data/restore-provenance.json').read_bytes())
        self.assertEqual(proof['renderer_source_origin'],'http://127.0.0.1:1234')
        self.assertFalse((target/'user-data/controller-port.json').exists())
        self.resign(saved,lambda value:value.update(renderer_origin='http://example.invalid:1234'))
        with self.assertRaises(recovery.RecoveryError):recovery.verify(saved)
    def resign(self,path,mutate):
        p=path/recovery.MANIFEST;data=json.loads(p.read_bytes());mutate(data);p.write_text(json.dumps(data))
        (path/(recovery.MANIFEST+'.sha256')).write_text(hashlib.sha256(p.read_bytes()).hexdigest())
    def junction(self,path,target):
        quote=lambda p:"'"+str(p).replace("'","''")+"'"
        recovery._powershell("$ErrorActionPreference='Stop';New-Item -ItemType Junction -Path "+quote(path)+' -Target '+quote(target)+'|Out-Null')
        self.addCleanup(lambda:path.rmdir()if os.path.lexists(path)and getattr(path.lstat(),'st_file_attributes',0)&0x400 else None)

    @contextmanager
    def exclusively_read_locked(self,path):
        import ctypes
        from ctypes import wintypes
        k=ctypes.WinDLL('kernel32',use_last_error=True)
        k.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,ctypes.c_void_p,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
        k.CreateFileW.restype=wintypes.HANDLE;k.CloseHandle.argtypes=[wintypes.HANDLE];k.CloseHandle.restype=wintypes.BOOL
        handle=k.CreateFileW(str(path),0x80000000,0,None,3,0x00200000,None)
        if handle is None or handle==ctypes.c_void_p(-1).value:raise ctypes.WinError(ctypes.get_last_error())
        try:yield
        finally:
            if not k.CloseHandle(handle):raise ctypes.WinError(ctypes.get_last_error())

    def agent_home(self):
        return self.root/'user-data'/NativeCacheExclusionPolicyTests.CACHE.rsplit('/AppData/',1)[0]

    def test_agent_home_wininet_junction_is_not_followed_and_other_runtime_state_is_retained(self):
        home=self.agent_home();cache=home/'AppData/Local/Microsoft/Windows/INetCache'
        cache.mkdir(parents=True);(cache/'IE').mkdir();(cache/'IE/system-cache.fixture').write_bytes(b'ephemeral cache')
        self.junction(cache/'Content.IE5',cache/'IE')
        durable=home/'AppData/Local/Microsoft/Windows/keep-session.fixture';durable.write_bytes(b'opaque home state')
        runtime=home.parent/'owner-record.fixture';runtime.write_bytes(b'original runtime record')
        saved,result=self.saved();value,digest=recovery.verify(saved,result['manifest_sha256'])
        self.assertEqual(value['cache_exclusion_policy'],recovery.CURRENT_CACHE_EXCLUSION_POLICY)
        cache_name=cache.relative_to(self.root/'user-data').as_posix()
        self.assertFalse(any(n==cache_name or n.startswith(cache_name+'/')for n in value['entries']))
        self.assertEqual((cache/'IE/system-cache.fixture').read_bytes(),b'ephemeral cache')
        target=self.target();recovery.restore(saved,target,digest)
        for original in (durable,runtime):
            name=original.relative_to(self.root/'user-data')
            self.assertEqual((target/'user-data'/name).read_bytes(),original.read_bytes())
        self.assertFalse((target/'user-data'/cache_name).exists())

    def test_adjacent_agent_home_durable_junction_still_refuses_backup(self):
        home=self.agent_home();parent=home/'AppData/Local/Microsoft/Windows';parent.mkdir(parents=True)
        outside=self.base/'outside agent home';outside.mkdir();(outside/'keep').write_bytes(b'never followed')
        self.junction(parent/'INetCache-durable',outside)
        with self.assertRaisesRegex(recovery.RecoveryError,'Linked/reparse'):
            recovery.backup(self.root,self.base/'durable-link-backup')
        self.assertFalse((self.base/'durable-link-backup').exists())
        self.assertEqual((outside/'keep').read_bytes(),b'never followed')

    def test_legacy_no_policy_backup_restores_saved_ordinary_agent_cache_files(self):
        cache=self.agent_home()/'AppData/Local/Microsoft/Windows/INetCache';cache.mkdir(parents=True)
        original=cache/'ordinary-legacy.fixture';original.write_bytes(b'legacy saved cache must not disappear')
        with patch.object(recovery,'CURRENT_CACHE_EXCLUSION_POLICY',recovery.LEGACY_CACHE_EXCLUSION_POLICY):
            saved,result=self.saved()
        value,_=recovery.verify(saved,result['manifest_sha256'])
        name=original.relative_to(self.root/'user-data').as_posix()
        self.assertIn(name,value['entries'])
        self.resign(saved,lambda d:d.pop('cache_exclusion_policy'))  # Original v1 manifests had no policy field.
        value,digest=recovery.verify(saved);self.assertNotIn('cache_exclusion_policy',value)
        self.assertIn(name,value['entries'])
        target=self.target();recovery.restore(saved,target,digest)
        self.assertEqual((target/'user-data'/name).read_bytes(),original.read_bytes())

    def test_old_v1_shader_members_verify_and_restore_without_refiltering(self):
        originals=[]
        for home in (self.root/'user-data/home',self.agent_home()):
            p=home/'AppData/LocalLow/NVIDIA/PerDriverVersion/DXCache/ordinary-old.fixture'
            p.parent.mkdir(parents=True);p.write_bytes(b'old v1 saved ordinary GPU bytes');originals.append(p)
        with patch.object(recovery,'CURRENT_CACHE_EXCLUSION_POLICY',recovery.AGENT_HOME_CACHE_EXCLUSION_POLICY):
            saved,result=self.saved()
        value,digest=recovery.verify(saved,result['manifest_sha256'])
        self.assertEqual(value['cache_exclusion_policy'],recovery.AGENT_HOME_CACHE_EXCLUSION_POLICY)
        target=self.target();recovery.restore(saved,target,digest)
        for original in originals:
            name=original.relative_to(self.root/'user-data')
            self.assertIn(name.as_posix(),value['entries'])
            self.assertEqual((target/'user-data'/name).read_bytes(),original.read_bytes())

    def test_v2_backup_does_not_open_locked_shader_cache_but_durable_lock_refuses(self):
        path=self.root/'user-data'/NativeCacheExclusionPolicyTests.SHADER_CACHE/'locked-cache.fixture'
        path.parent.mkdir(parents=True);path.write_bytes(b'original shader bytes remain on source')
        with self.exclusively_read_locked(path):
            with self.assertRaises(OSError):recovery._hash(path)
            saved,result=self.saved()
        value,_=recovery.verify(saved,result['manifest_sha256'])
        self.assertEqual(value['cache_exclusion_policy'],recovery.SHADER_CACHE_EXCLUSION_POLICY)
        self.assertFalse(any(name.startswith(NativeCacheExclusionPolicyTests.SHADER_CACHE) for name in value['entries']))
        self.assertEqual(path.read_bytes(),b'original shader bytes remain on source')
        durable=path.parent.with_name('DXCache-durable')/'settings.fixture';durable.parent.mkdir();durable.write_bytes(b'durable neighboring state')
        destination=self.base/'locked-durable-backup'
        with self.exclusively_read_locked(durable):
            with self.assertRaises(OSError):recovery.backup(self.root,destination)
        self.assertFalse(destination.exists());self.assertEqual(durable.read_bytes(),b'durable neighboring state')

    def test_v2_snapshot_rejects_injected_shader_cache_member(self):
        saved,_=self.saved();data=saved/'data'
        member=data/NativeCacheExclusionPolicyTests.SHADER_CACHE/'injected.fixture'
        member.parent.mkdir(parents=True);member.write_bytes(b'injected cache bytes')
        rows,total=recovery._inventory(data,exclude=False)
        self.resign(saved,lambda value:value.update(entries=rows,bytes=total))
        with self.assertRaisesRegex(recovery.RecoveryError,'Invalid private backup member'):recovery.verify(saved)

    def test_new_policy_rejects_an_injected_ordinary_agent_cache_member(self):
        saved,_=self.saved();data=saved/'data';member=data/NativeCacheExclusionPolicyTests.CACHE/'unexpected.fixture'
        member.parent.mkdir(parents=True);member.write_bytes(b'not permitted in a new-policy backup')
        rows,total=recovery._inventory(data,exclude=False)
        self.resign(saved,lambda d:d.update(entries=rows,bytes=total))
        with self.assertRaisesRegex(recovery.RecoveryError,'Invalid private backup member'):
            recovery.verify(saved)

    def test_unknown_cache_policy_refuses_verify_and_restore_before_publication(self):
        saved,_=self.saved();self.resign(saved,lambda d:d.update(cache_exclusion_policy='unknown-future-policy'))
        with self.assertRaisesRegex(recovery.RecoveryError,'cache exclusion policy'):recovery.verify(saved)
        target=self.target()
        with self.assertRaisesRegex(recovery.RecoveryError,'cache exclusion policy'):recovery.restore(saved,target)
        self.assertFalse((target/'user-data').exists());self.assertFalse(list(target.glob('user-data.partial-*')))

    def test_roundtrip_preserves_all_durable_opaque_state_unknown_and_empty_dirs(self):
        before=recovery._inventory(self.root/'user-data')[0];saved,result=self.saved()
        value,digest=recovery.verify(saved,result['manifest_sha256']);self.assertEqual(value['entries'],before)
        self.assertNotIn('local-access.token',before);self.assertNotIn('controller-port.json',before)
        destination=self.target();reply=recovery.restore(saved,destination,digest);self.assertFalse(reply['model_or_agent_started'])
        for name,row in before.items():
            p=destination/'user-data'/name
            if row['kind']=='directory':self.assertTrue(p.is_dir())
            else:self.assertEqual(recovery._hash(p),row['sha256'])
        with sqlite3.connect(destination/'user-data/eeas.sqlite3')as db:
            self.assertEqual(db.execute('SELECT * FROM operations').fetchall(),[('original-unknown','UNKNOWN_OUTCOME')])
        provenance=json.loads((destination/'user-data/restore-provenance.json').read_bytes())
        self.assertEqual(provenance['allowed_source_data_dirs'],[str(self.root/'user-data')])
        self.assertFalse((destination/'user-data/local-access.token').exists());self.assertEqual(recovery._inventory(self.root/'user-data')[0],before)

    def test_existing_backup_and_restore_destination_are_never_overwritten(self):
        saved,_=self.saved();old=recovery._hash(saved/recovery.MANIFEST)
        with self.assertRaises(recovery.RecoveryError):recovery.backup(self.root,saved)
        self.assertEqual(recovery._hash(saved/recovery.MANIFEST),old)
        with self.assertRaises(recovery.RecoveryError):recovery.restore(saved,self.root)
        self.assertEqual((self.root/'user-data/local-access.token').read_bytes(),b'ephemeral token fixture')

    def test_invalid_members_rejected_before_any_target_data_is_created(self):
        saved,_=self.saved();destination=self.target();original=(saved/recovery.MANIFEST).read_bytes()
        for bad in ['../outside','/absolute','file:stream','CON.txt','dir/../escape','trailing.','bad\\path']:
            with self.subTest(member=bad):
                (saved/recovery.MANIFEST).write_bytes(original)
                self.resign(saved,lambda d:d['entries'].update({bad:{'kind':'directory'}}))
                with self.assertRaises(recovery.RecoveryError):recovery.restore(saved,destination)
                self.assertFalse((destination/'user-data').exists());self.assertFalse(list(destination.glob('user-data.partial-*')))

    def test_changed_file_and_independent_manifest_hash_are_refused(self):
        saved,_=self.saved()
        with self.assertRaises(recovery.RecoveryError):recovery.verify(saved,'0'*64)
        (saved/'data/quick-assistant-journal.json').write_bytes(b'changed')
        destination=self.target()
        with self.assertRaises(recovery.RecoveryError):recovery.restore(saved,destination)
        self.assertFalse((destination/'user-data').exists())

    def test_wrong_owner_runtime_and_migration_pins_are_refused(self):
        saved,_=self.saved();destination=self.target();original=json.loads((saved/recovery.MANIFEST).read_bytes())
        self.resign(saved,lambda d:d.update(owner='different-user'))
        with self.assertRaisesRegex(recovery.RecoveryError,'OS user'):recovery.restore(saved,destination)
        self.resign(saved,lambda d:d.update(owner=original['owner']))
        for field in ['compatibility','migrations']:
            with self.subTest(field=field):
                self.resign(saved,lambda d:d[field].update({next(iter(d[field])):'0'*64}))
                with self.assertRaises(recovery.RecoveryError):recovery.restore(saved,destination)
                self.resign(saved,lambda d:d.update({field:original[field]}))
        self.assertFalse((destination/'user-data').exists())

    def test_durable_reparse_is_rejected_and_cache_reparse_is_not_followed(self):
        outside=self.base/'outside';outside.mkdir();(outside/'keep').write_bytes(b'original outside')
        link=self.root/'user-data/durable-link';self.junction(link,outside)
        with self.assertRaises(recovery.RecoveryError):recovery.backup(self.root,self.base/'bad')
        link.rmdir();self.junction(self.root/'user-data/cache',outside)
        saved,_=self.saved();self.assertFalse((saved/'data/cache').exists());self.assertEqual((outside/'keep').read_bytes(),b'original outside')

    def test_extra_ephemeral_file_and_case_collision_are_rejected(self):
        saved,_=self.saved();(saved/'data/local-access.token').write_bytes(b'unexpected')
        with self.assertRaises(recovery.RecoveryError):recovery.verify(saved)
        (saved/'data/local-access.token').unlink()
        self.resign(saved,lambda d:d['entries'].update({'AGENT-SESSIONS':{'kind':'directory'}}))
        with self.assertRaisesRegex(recovery.RecoveryError,'Case-colliding'):recovery.verify(saved)

    def test_owner_appearing_during_copy_leaves_no_completed_backup(self):
        calls=0
        def guard(_root):
            nonlocal calls
            calls+=1
            if calls>1:raise recovery.RecoveryError('source now in use')
        with patch.object(recovery,'_stopped',guard):
            with self.assertRaises(recovery.RecoveryError):recovery.backup(self.root,self.base/'backup')
        self.assertFalse((self.base/'backup').exists());self.assertTrue(list(self.base.glob('backup.partial-*')))

    def test_entry_and_space_limits_refuse_before_publication(self):
        with patch.object(recovery,'MAX_FILES',2):
            with self.assertRaisesRegex(recovery.RecoveryError,'entry limit'):recovery.backup(self.root,self.base/'backup')
        self.assertFalse(list(self.base.glob('backup*')))
        with patch.object(recovery,'RESERVE_BYTES',2**63):
            with self.assertRaisesRegex(recovery.RecoveryError,'free space'):recovery.backup(self.root,self.base/'backup')
        self.assertFalse(list(self.base.glob('backup*')))

    def test_atomic_publish_race_never_replaces_an_existing_empty_directory(self):
        stage=self.base/'stage';stage.mkdir();(stage/'original').write_bytes(b'keep')
        existing=self.base/'existing';existing.mkdir()
        with self.assertRaises(OSError):recovery._publish(stage,existing)
        self.assertTrue(existing.is_dir());self.assertFalse(list(existing.iterdir()));self.assertEqual((stage/'original').read_bytes(),b'keep')

    def test_account_binding_stays_immutable_while_each_restore_binds_its_own_directory(self):
        data=self.root/'user-data';native=data/'subscription-account/native-data';native.mkdir()
        (native/'opaque.fixture').write_bytes(b'SYNTHETIC NEVER PARSED AS OAUTH')
        actual=native.stat()
        value={'schema':'eeas.native-subscription-account.v1','id':'fixture-account','revision':1,
            'account_data_home':str(native),'directory_identity':[actual.st_dev,actual.st_ino]}
        digest=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        binding=data/'subscription-account/binding.json'
        binding.write_text(json.dumps({'value':value,'sha256':digest}),encoding='utf8')
        original=recovery._hash(binding);saved,_=self.saved();first=self.target()
        recovery.restore(saved,first)
        target=first/'user-data';proof=json.loads((target/'restore-provenance.json').read_bytes())
        account=proof['directory_bindings']['subscription-account/native-data']
        now=(target/'subscription-account/native-data').stat()
        self.assertEqual(account['restored_directory_identity'],[now.st_dev,now.st_ino])
        self.assertNotEqual(account['restored_directory_identity'],value['directory_identity'])
        self.assertEqual(account['reference'],{'id':value['id'],'revision':1,'sha256':digest})
        self.assertEqual(proof['target_data_dir'],str(target))
        self.assertEqual(recovery._hash(target/'subscription-account/binding.json'),original)
        second_backup=self.base/'second-backup';recovery.backup(first,second_backup)
        second=self.target('Second restored install');recovery.restore(second_backup,second)
        again=second/'user-data';proof2=json.loads((again/'restore-provenance.json').read_bytes())
        now2=(again/'subscription-account/native-data').stat()
        self.assertEqual(proof2['directory_bindings']['subscription-account/native-data']['restored_directory_identity'],[now2.st_dev,now2.st_ino])
        self.assertEqual(recovery._hash(again/'subscription-account/binding.json'),original)
        self.assertEqual(recovery._hash(binding),original)

    def test_foreign_saved_account_directory_is_refused_before_restore_publication(self):
        data=self.root/'user-data';native=data/'subscription-account/native-data';native.mkdir()
        value={'schema':'eeas.native-subscription-account.v1','id':'fixture-account','revision':1,
            'account_data_home':str(self.base/'foreign account'),'directory_identity':[0,0]}
        digest=hashlib.sha256(json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        (data/'subscription-account/binding.json').write_text(json.dumps({'value':value,'sha256':digest}),encoding='utf8')
        saved,_=self.saved();target=self.target()
        with self.assertRaisesRegex(recovery.RecoveryError,'outside'):
            recovery.restore(saved,target)
        self.assertFalse((target/'user-data').exists())
        self.assertEqual(recovery.verify(saved)[0]['entries']['subscription-account/binding.json']['sha256'],
                         recovery._hash(data/'subscription-account/binding.json'))


if __name__=='__main__':unittest.main()
