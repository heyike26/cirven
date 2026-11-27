"""Verify three actual slice artifacts, preserving XML/schema and render receipts."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--evidence',type=Path,required=True)
    parser.add_argument('--office-root',type=Path,required=True)
    parser.add_argument('--schema-validator',type=Path,required=True)
    args=parser.parse_args();root=Path(__file__).resolve().parents[1];ev=args.evidence.resolve()
    validator=args.schema_validator.resolve();skill=validator.parents[2]
    def tools_sha():
        return {str(p.relative_to(skill)):hashlib.sha256(p.read_bytes()).hexdigest()
                for p in (skill/'scripts').rglob('*') if p.is_file() and p.suffix in {'.py','.xsd'}}
    before=tools_sha()
    (ev/'docx-validation-tool-before.json').write_text(json.dumps({'path':str(skill),'sha256':before},indent=2)+'\n')
    cases={'built-in':'test_draft_real_docx_pending_and_frozen_download_after_late_answer',
           'user-plain':'test_user_plain_local_docx_preserves_format_header_footer_and_full_append',
           'user-slots':'test_named_slots_preserve_format_xml_escaping_and_complete_network'}
    env={**os.environ,'PYTHONPATH':str(root/'toolchain/word/validation-deps')}
    receipts=[]
    for label,case in cases.items():
        values=list((ev/'actual-artifacts'/case/'artifacts').glob('*/report.docx'))
        if len(values)!=1:raise RuntimeError('Expected exactly one real positive artifact for '+case)
        raw=values[0];command=[sys.executable,str(validator),str(raw)]
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
        (ev/(label+'-xsd.log')).write_text(result.stdout+result.stderr)
        receipts.append({'kind':label,'input':str(raw),'input_sha256':hashlib.sha256(raw.read_bytes()).hexdigest(),
            'validation_command':command,'validation_exit_code':result.returncode})
        if result.returncode:raise RuntimeError('XSD validation failed: '+label+'; original log retained')
        command=[sys.executable,str(root/'scripts/verify_planning_report.py'),'--input',str(raw),
            '--output',str(ev/('render-'+label)),'--office-root',str(args.office_root.resolve())]
        result=subprocess.run(command,capture_output=True,text=True,timeout=120)
        (ev/(label+'-render.log')).write_text(result.stdout+result.stderr)
        receipts[-1].update(render_command=command,render_exit_code=result.returncode)
        if result.returncode:raise RuntimeError('Actual LibreOffice render failed: '+label+'; original log retained')
    after=tools_sha();(ev/'docx-validation-tool-after.json').write_text(json.dumps({'sha256':after,'stable':before==after},indent=2)+'\n')
    if before!=after:raise RuntimeError('Development schema tool changed during verification')
    (ev/'word-render-validation.json').write_text(json.dumps({'status':'PASS','artifacts':receipts,
        'schema_tool_stable':True,'visual_inspection':'NOT_RUN_ROOT_ONLY','native_word':'NOT_RUN','windows':'NOT_RUN'},indent=2)+'\n')
    print('Three actual OOXML/XSD and LibreOffice/Poppler renders PASS')


if __name__=='__main__':main()
