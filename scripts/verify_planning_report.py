"""Render an actual frozen Word artifact with an isolated Linux office profile."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from xml.sax.saxutils import escape

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from packages.domain.planning_docx import bounded_docx


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--office-root',type=Path,required=True)
    args=parser.parse_args();input_path=args.input.resolve();out=args.output.resolve();root=args.office_root.resolve()
    raw=input_path.read_bytes();bounded_docx(raw);out.mkdir(parents=True,exist_ok=True)
    if any(out.glob('*.pdf')):raise ValueError('Use a fresh report output directory; retain old renders')
    profile=out/'office-profile';profile.mkdir()
    cache=out/'font-cache';cache.mkdir()
    config=out/'fontconfig.xml'
    config.write_text('<?xml version="1.0"?><!DOCTYPE fontconfig SYSTEM "fonts.dtd"><fontconfig>'
        '<include ignore_missing="yes">/etc/fonts/fonts.conf</include><dir>'+
        escape(str(root/'usr/share/fonts/opentype/noto'))+'</dir><cachedir>'+escape(str(cache))+'</cachedir></fontconfig>')
    program=root/'usr/lib/libreoffice/program'
    env={**os.environ,'LD_LIBRARY_PATH':str(program)+':'+str(root/'usr/lib/x86_64-linux-gnu'),
         'SAL_USE_VCLPLUGIN':'svp','FONTCONFIG_FILE':str(config),'XDG_CACHE_HOME':str(out/'cache')}
    commands=[[str(program/'soffice'),'-env:UserInstallation='+profile.as_uri(),
               '--headless','--convert-to','pdf','--outdir',str(out),str(input_path)]]
    results=[]
    for command in commands:
        result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
        results.append({'command':command,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
        if result.returncode:raise RuntimeError(json.dumps(results,ensure_ascii=False))
    pdf=out/(input_path.stem+'.pdf')
    if not pdf.is_file() or not pdf.read_bytes().startswith(b'%PDF-'):raise RuntimeError('Office returned without an actual PDF')
    command=[str(root/'usr/bin/pdftoppm'),'-png','-r','100',str(pdf),str(out/'page')]
    result=subprocess.run(command,env=env,capture_output=True,text=True,timeout=60)
    results.append({'command':command,'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr})
    if result.returncode:raise RuntimeError(json.dumps(results,ensure_ascii=False))
    from pypdf import PdfReader
    pages=PdfReader(str(pdf)).pages
    pngs=sorted(out.glob('page-*.png'))
    if len(pngs)!=len(pages):raise RuntimeError('Every PDF page must have a rendered image')
    texts=[page.extract_text() for page in pages]
    if not any('DRAFT' in value or 'CONFIRMED' in value for value in texts):raise RuntimeError('Report status missing from rendered PDF')
    (out/'pdf-extracted-text.txt').write_text('\n\n'.join(texts))
    receipt={'input':str(input_path),'input_sha256':hashlib.sha256(raw).hexdigest(),
             'pdf_sha256':hashlib.sha256(pdf.read_bytes()).hexdigest(),'page_count':len(pages),
             'rendered_pages':[{'path':str(p),'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in pngs],
             'commands':results,'status':'PASS','visual_inspection':'NOT_RUN_ROOT_ONLY',
             'qualification':'Linux LibreOffice PDF render, not native Microsoft Word/Windows'}
    (out/'render-receipt.json').write_text(json.dumps(receipt,ensure_ascii=False,indent=2)+'\n')
    print(out/'render-receipt.json')


if __name__=='__main__':main()
