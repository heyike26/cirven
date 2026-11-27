"""Original stdlib subprocess contracts, tiny ROOTs; never import the real application."""
from __future__ import annotations
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

SCRIPT=Path(__file__).resolve().parents[1]/'scripts/record_slice.py'
BASE_PYTHON=sys.executable
CAP='公开验收：引号 " 与单引号 \'，换行\n保留 --acceptance 字符'
PAYLOAD=['--acceptance','command-owned value','--reference=command-owned','-h','--','a b','中文','"quote"',"'literal'",'line1\nline2']


class RecordSliceCLI(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='eeas-record-cli-')
        self.root=Path(self.temp.name)
        (self.root/'scripts').mkdir();shutil.copyfile(SCRIPT,self.root/'scripts/record_slice.py')
        (self.root/'apps').mkdir();(self.root/'apps/original.py').write_bytes(b'# original public source\n')
        (self.root/'notes').mkdir();self.reference=self.root/'notes/原始 空格.txt'
        self.reference.write_bytes('原始来源\r\n末尾保留。\r\n'.encode())
        self.events=[]
        subprocess.run(['git','init','-q'],cwd=self.root,check=True)
        subprocess.run(['git','add','.'],cwd=self.root,check=True)
        subprocess.run(['git','-c','user.name=Original CLI Fixture','-c','user.email=original@invalid.local',
            'commit','--no-verify','-qm','original tiny source'],cwd=self.root,check=True)

    def tearDown(self):
        evidence=os.environ.get('EEAS_EVIDENCE_DIR')
        if evidence:
            destination=Path(evidence)/'artifacts'/self._testMethodName
            links=[{'path':p.relative_to(self.root).as_posix(),'target':os.readlink(p)}
                   for p in self.root.rglob('*') if p.is_symlink()]
            def excluded(folder,names):
                return [n for n in names if n=='.git' or (Path(folder)/n).is_symlink()]
            shutil.copytree(self.root,destination,ignore=excluded)
            (destination/'symlink-fixture-metadata.json').write_text(json.dumps(links,indent=2)+'\n')
            (destination/'invocations.json').write_text(json.dumps(self.events,ensure_ascii=False,indent=2)+'\n')
        self.temp.cleanup()

    def invoke(self,prefix,*,separator=True,body=None,payload=PAYLOAD):
        script=body or "import json,sys;print(json.dumps({'argv':sys.argv[1:],'isolated':sys.flags.isolated,'no_site':sys.flags.no_site},ensure_ascii=False))"
        command=[BASE_PYTHON,'-I','-S','-c',script,*payload]
        argv=[BASE_PYTHON,'-I','-S',str(self.root/'scripts/record_slice.py'),*prefix,*(['--'] if separator else []),*command]
        run=subprocess.run(argv,cwd=self.root,capture_output=True,text=True)
        self.events.append({'argv':argv,'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr,
                            'script_sha256':hashlib.sha256((self.root/'scripts/record_slice.py').read_bytes()).hexdigest()})
        return run,command

    def receipt(self):
        reports=list((self.root/'reports').glob('*/*/freeze.json'))
        self.assertEqual(len(reports),1)
        path=reports[0].parent
        return path,json.loads((path/'freeze.json').read_text()),json.loads((path/'result.json').read_text())

    def check_success(self,run,command):
        self.assertEqual(run.returncode,0,run.stderr)
        path,freeze,result=self.receipt()
        self.assertEqual(result['status'],'PASS');self.assertEqual(result['exit_code'],0)
        self.assertEqual(freeze['command'],command)
        self.assertEqual(freeze['acceptance_before_run'],CAP)
        output=json.loads((path/'stdout.txt').read_text())
        self.assertEqual(output['argv'],PAYLOAD)
        self.assertEqual((output['isolated'],output['no_site']),(1,1))
        self.assertEqual(freeze['packages'],{})
        return path,freeze

    def test_slice_first_options_after_slice_reaches_actual_command(self):
        run,cmd=self.invoke(['E00-cli','--acceptance',CAP,'--reference','notes/原始 空格.txt'])
        path,freeze=self.check_success(run,cmd)
        self.assertEqual(freeze['code_sha256']['notes/原始 空格.txt'],hashlib.sha256(self.reference.read_bytes()).hexdigest())
        self.assertEqual((path/'snapshot/notes/原始 空格.txt').read_bytes(),self.reference.read_bytes())
        self.assertEqual(set(freeze['code_sha256']),{'apps/original.py','scripts/record_slice.py','notes/原始 空格.txt'})

    def test_original_flags_before_slice_and_explicit_separator_preserve_argv(self):
        run,cmd=self.invoke(['--acceptance',CAP,'--reference','notes/原始 空格.txt','E00-cli'])
        self.check_success(run,cmd)

    def test_original_no_separator_preserves_command_options_and_later_double_dash(self):
        run,cmd=self.invoke(['--acceptance',CAP,'--reference','notes/原始 空格.txt','E00-cli'],separator=False)
        self.check_success(run,cmd)

    def test_legacy_no_separator_leading_reserved_executable_remains_opaque(self):
        # A parser counterexample, without executing an arbitrary PATH binary.
        body="import json,runpy;ns=runpy.run_path('scripts/record_slice.py');print(json.dumps(ns['split_cli'](['--acceptance','public caps','E00-cli','--help'])))"
        run=subprocess.run([BASE_PYTHON,'-I','-S','-c',body],cwd=self.root,capture_output=True,text=True)
        self.events.append({'scope':'stdlib cold helper call only','argv':[BASE_PYTHON,'-I','-S','-c',body],
                            'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout),[['--acceptance','public caps','E00-cli'],['--help']])

    def test_legacy_reserved_executable_later_separator_is_still_command_data(self):
        body="import json,runpy;ns=runpy.run_path('scripts/record_slice.py');print(json.dumps(ns['split_cli'](['--acceptance','public caps','E00-cli','--reference','command arg','--','payload'])))"
        run=subprocess.run([BASE_PYTHON,'-I','-S','-c',body],cwd=self.root,capture_output=True,text=True)
        self.events.append({'scope':'stdlib cold helper only, no arbitrary executable','exit_code':run.returncode,
                            'stdout':run.stdout,'stderr':run.stderr})
        self.assertEqual(run.returncode,0,run.stderr)
        self.assertEqual(json.loads(run.stdout),[['--acceptance','public caps','E00-cli'],['--reference','command arg','--','payload']])

    def test_mixed_prefix_and_equals_options_keep_caps_and_references_exact(self):
        run,cmd=self.invoke(['--reference=notes/原始 空格.txt','E00-cli','--acceptance='+CAP])
        self.check_success(run,cmd)

    def test_original_abbreviated_options_still_work(self):
        run,cmd=self.invoke(['--acce',CAP,'--refe','notes/原始 空格.txt','E00-cli'],separator=False)
        self.check_success(run,cmd)

    def test_nonzero_command_retains_stdout_stderr_and_fail_exit(self):
        body="import sys; print('original stdout'); print('original stderr',file=sys.stderr);sys.exit(7)"
        run,cmd=self.invoke(['E00-cli','--acceptance',CAP],body=body,payload=[])
        self.assertEqual(run.returncode,7,run.stderr)
        path,freeze,result=self.receipt()
        self.assertEqual(freeze['command'],cmd)
        self.assertEqual((result['status'],result['exit_code']),('FAIL',7))
        self.assertEqual((path/'stdout.txt').read_bytes(),b'original stdout\n')
        self.assertEqual((path/'stderr.txt').read_bytes(),b'original stderr\n')

    def test_reference_outside_or_symlink_outside_rejects_before_command(self):
        outside=Path(self.temp.name).parent/(Path(self.temp.name).name+'-outside.txt');outside.write_text('public outside fixture')
        try:
            (self.root/'notes/link.txt').symlink_to(outside)
            for reference in [str(outside),'notes/link.txt','../'+outside.name]:
                run,cmd=self.invoke(['E00-cli','--acceptance',CAP,'--reference',reference],body="raise AssertionError('must not run')",payload=[])
                self.assertEqual(run.returncode,2,run.stderr)
                self.assertIn('reference must be an existing file inside the repository',run.stderr)
                self.assertFalse(list((self.root/'reports').glob('*/*/result.json')))
        finally:outside.unlink()

    def test_missing_command_missing_caps_and_invalid_slice_rejected(self):
        for tail in [['E00-cli','--acceptance',CAP,'--'],['--acceptance',CAP,'E00-cli'],
                     ['E00-cli','--',BASE_PYTHON,'-I','-S','-c','print(1)'],
                     ['../invalid','--acceptance',CAP,'--',BASE_PYTHON,'-I','-S','-c','print(1)']]:
            run=subprocess.run([BASE_PYTHON,'-I','-S',str(self.root/'scripts/record_slice.py'),*tail],cwd=self.root,capture_output=True,text=True)
            self.events.append({'argv':tail,'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
            self.assertEqual(run.returncode,2,run.stderr)
            self.assertIn('error:',run.stderr)
            self.assertFalse(list((self.root/'reports').glob('*/*/result.json')))

    def test_duplicate_reference_and_absolute_internal_reference_are_preserved(self):
        run,cmd=self.invoke(['E00-cli','--acceptance',CAP,'--reference',str(self.reference),
                            '--reference','notes/原始 空格.txt'])
        path,freeze=self.check_success(run,cmd)
        self.assertEqual(list(freeze['code_sha256']).count('notes/原始 空格.txt'),1)

    def test_snapshot_records_original_source_before_command_changes_fixture(self):
        before=self.reference.read_bytes()
        body="from pathlib import Path;Path('notes/原始 空格.txt').write_text('changed original fixture');print('intentional fixture mutation')"
        run,cmd=self.invoke(['E00-cli','--acceptance',CAP,'--reference','notes/原始 空格.txt'],body=body,payload=[])
        self.assertEqual(run.returncode,0,run.stderr)
        path,freeze,result=self.receipt()
        self.assertEqual((path/'snapshot/notes/原始 空格.txt').read_bytes(),before)
        self.assertEqual(freeze['code_sha256']['notes/原始 空格.txt'],hashlib.sha256(before).hexdigest())
        self.assertNotEqual(self.reference.read_bytes(),before)
        self.assertEqual(result['status'],'PASS')  # Child exit only; not a source-after qualification.


if __name__=='__main__':unittest.main(verbosity=2)
