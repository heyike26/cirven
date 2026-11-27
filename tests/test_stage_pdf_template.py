import hashlib
from io import BytesIO
from pathlib import Path
import subprocess
import sys

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.generic import (ArrayObject, DecodedStreamObject, DictionaryObject, NameObject,
                           NumberObject, RectangleObject, TextStringObject)

from packages.dynamic_workflow.stage_template_export import describe_template, export_stage_template
from packages.dynamic_workflow.stage_templates import parse_stage_template, template_issues

N = NameObject
T = TextStringObject
D = DictionaryObject


def pdf(*, fields=True, static=True, restricted=False):
    writer = PdfWriter()
    pages = [writer.add_blank_page(width=w,height=h) for w,h in [(510,700),(640,480)]]
    writer.add_metadata({'/Title':'Reviewed form'})
    font = writer._add_object(D({N('/Type'):N('/Font'), N('/Subtype'):N('/Type1'),
                                 N('/BaseFont'):N('/Helvetica'), N('/Encoding'):N('/WinAnsiEncoding')}))
    resources = D({N('/Font'):D({N('/Helv'):font})})
    for index,page in enumerate(pages,1):
        page[N('/Resources')] = resources
        page[N('/Annots')] = ArrayObject()
        if static:
            stream = DecodedStreamObject()
            stream.set_data(f'0.5 w 20 24 m 160 24 l S BT /Helv 10 Tf 22 420 Td (Page {index} heading) Tj ET'.encode())
            page[N('/Contents')] = writer._add_object(stream)
    if fields:
        form = D({N('/DA'):T('/Helv 11 Tf 0 g'),N('/DR'):resources,N('/Fields'):ArrayObject()})
        writer.root_object[N('/AcroForm')] = writer._add_object(form)
        def field(name, flag, x, y, page_numbers, parent=None):
            owner = D({N('/FT'):N('/Tx'),N('/T'):T(name),N('/TU'):T(name+' label'),
                       N('/Ff'):NumberObject(flag),N('/V'):T('source'),N('/Kids'):ArrayObject(),N('/MaxLen'):NumberObject(40)})
            owner_ref = writer._add_object(owner)
            if parent:
                owner[N('/Parent')]=parent
                parent.get_object()['/Kids'].append(owner_ref)
            else:
                form['/Fields'].append(owner_ref)
            for number in page_numbers:
                widget = D({N('/Type'):N('/Annot'),N('/Subtype'):N('/Widget'),N('/F'):NumberObject(4),
                            N('/Rect'):RectangleObject((x,y,x+140,y+34)),N('/Parent'):owner_ref})
                ref=writer._add_object(widget)
                owner['/Kids'].append(ref)
                pages[number-1]['/Annots'].append(ref)
        field('title',2,32,240,[1,2])
        parent=writer._add_object(D({N('/T'):T('group'),N('/Kids'):ArrayObject()}))
        form['/Fields'].append(parent)
        field('title',0,32,160,[1],parent)
        field('notes',1<<12,32,110,[2])
        field('locked',1,32,70,[1])
        field('secret',1<<13,32,30,[1])
    if restricted:
        writer.root_object[N('/Perms')]=D()
    buf=BytesIO()
    writer.write(buf)
    return buf.getvalue()


def select(raw):
    return describe_template(parse_stage_template('Spec.pdf',raw),raw)


def artifact(edits):
    return {'template_output':'Approved content', 'template_edits':[
        {'target':target,'values':[value]} for target,value in edits]}


def test_isolated_interpreter_resolves_both_pdf_workers_from_application(tmp_path):
    script = """
import sys
sys.path.insert(0, sys.argv[1])
from packages.dynamic_workflow.stage_template_export import describe_template, export_stage_template
from packages.dynamic_workflow.stage_templates import parse_stage_template
raw = sys.stdin.buffer.read()
parsed = parse_stage_template('form.pdf', raw)
assert '第 1 页' in parsed['text']
spec = describe_template(parsed, raw)
required = next(slot for slot in spec['edit_targets'] if slot['required'])
artifact = {'template_output':'Reviewed', 'template_edits':[{'target':required['target'], 'values':['Reviewed']}]}
assert export_stage_template(spec, artifact, raw)[2].startswith(raw)
"""
    subprocess.run([sys.executable, "-I", "-B", "-c", script, str(Path(__file__).resolve().parents[1])],
                   input=pdf(), cwd=tmp_path, check=True, timeout=60)


def test_incremental_form_preserves_original_bytes_and_page_layout():
    raw=pdf()
    template=select(raw)
    assert template['export_extension']=='.pdf'
    targets={t['field_name']:t for t in template['edit_targets']}
    assert list(targets)==['title','group.title','notes']
    assert targets['title']['pages']==[1,2] and targets['title']['required']
    assert targets['notes']['multiline'] and targets['title']['max_length']==40
    reviewed=artifact([(targets['title']['target'],'A (reviewed)'),(targets['notes']['target'],'Line A\nLine B')])
    name,mime,data=export_stage_template(template,reviewed,raw)
    assert (name,mime)==('Spec-已审核.pdf','application/pdf')
    assert data.startswith(raw) and data!=raw and hashlib.sha256(raw).hexdigest()==template['sha256']
    before,after=[PdfReader(BytesIO(s),strict=True) for s in (raw,data)]
    assert len(after.pages)==len(before.pages)==2 and after.metadata.title==before.metadata.title
    assert after.get_form_text_fields(full_qualified_name=True)=={
        'title':'A (reviewed)','group.title':'source','notes':'Line A\nLine B','locked':'source','secret':'source'}
    for page,original in zip(after.pages,before.pages):
        assert page.mediabox == original.mediabox and page.get_contents().get_data()==original.get_contents().get_data()
        assert [a.get_object()['/Rect'] for a in page['/Annots']]==[a.get_object()['/Rect'] for a in original['/Annots']]
        assert page['/Annots'][0].get_object()['/AP']['/N'].get_data()
    assert after.root_object['/AcroForm']['/NeedAppearances'].value is False
    assert before.get_form_text_fields()['title']=='source'


def test_fields_without_static_page_text_can_be_read_and_filled():
    raw=pdf(static=False)
    template=select(raw)
    assert template['export_extension']=='.pdf' and 'title label' in template['text']
    form=artifact([(template['edit_targets'][0]['target'],'Filled')])
    assert export_stage_template(template,form,raw)[2].startswith(raw)


@pytest.mark.parametrize('fields,restricted',[(False,False),(True,True)])
def test_static_or_restricted_documents_explicitly_export_text(fields,restricted):
    raw=pdf(fields=fields,restricted=restricted)
    template=select(raw)
    assert template['export_extension']=='.txt' and template['warnings'] and '纯文本' in template['warnings'][-1]
    assert export_stage_template(template,artifact([]),raw)==('Spec-已审核.txt','text/plain',b'Approved content')


def test_review_rejects_missing_blank_oversized_or_multiline_required_field():
    template=select(pdf())
    title,other=template['edit_targets'][:2]
    for edits in ([(other['target'],'x')],[(title['target'],'  ')],[(title['target'],'x'*41)],[(title['target'],'x\ny')]):
        review=artifact(edits)
        assert template_issues(template,review['template_output'],review['template_edits'])


def test_mismatched_source_mappings_and_invisible_characters_fail_closed():
    raw=pdf()
    template=select(raw)
    slot=template['edit_targets'][0]['target']
    with pytest.raises(ValueError,match='校验'):
        export_stage_template(template,artifact([(slot,'OK')]),raw+b'other')
    changed=dict(template,edit_targets=[dict(t,field_name='different') for t in template['edit_targets']])
    with pytest.raises(ValueError,match='原件'):
        export_stage_template(changed,artifact([(slot,'OK')]),raw)
    with pytest.raises(ValueError,match='字体'):
        export_stage_template(template,artifact([(slot,'原字体缺少中文')]),raw)


def test_acroform_field_and_annotation_arrays_can_be_indirect():
    original=PdfReader(BytesIO(pdf()))
    writer=PdfWriter(clone_from=original)
    form=writer.root_object['/AcroForm']
    form[N('/Fields')]=writer._add_object(form['/Fields'])
    page=writer.pages[0]
    page[N('/Annots')]=writer._add_object(page['/Annots'])
    parent=form['/Fields'][0].get_object()
    parent[N('/Kids')]=writer._add_object(parent['/Kids'])
    out=BytesIO()
    writer.write(out)
    raw=out.getvalue()
    template=select(raw)
    _,_,filled=export_stage_template(template,artifact([('pdf:field:1','Indirect')]),raw)
    assert PdfReader(BytesIO(filled)).get_form_text_fields()['title']=='Indirect'
