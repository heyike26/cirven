"""Actual python-docx/docxtpl OOXML and original-template preservation; no font/Word claim."""
from copy import deepcopy
from io import BytesIO
import hashlib
from zipfile import ZipFile

from docx import Document
from docx.enum.style import WD_STYLE_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt, RGBColor
from lxml import etree
import pytest

from packages.domain.planning_docx import render_docx
from packages.domain.chinese_word_typography import HAN, MARKER_NS
from packages.domain.planning_schema import PlanningCandidate


def check(value, message='Chinese typography acceptance failed'):
    if not value:
        raise AssertionError(message)


def snapshot():
    candidate = PlanningCandidate.model_validate({'title':'中文任务书 QA42',
        'summary':'## 中文小节\n\n保留原字符𠮷：中文、English 42 与标点。\n\n- 检查中文项目',
        'goals':['保留原字符𠮷：中文、English 42 与标点。'],'constraints':['不改源模板'],'risks':[],'assumptions':[],
        'nodes':[{'id':'T01','title':'检查任务','description':'原文必须逐字保留。',
                  'inputs':['原稿'],'outputs':['新诊断稿'],'acceptance':['核对格式']}], 'questions':[]}).model_dump()
    ref={'id':'typography-qa','revision':1,'sha256':'b'*64}
    payload={'candidate':candidate,'status':'DRAFT','source_receipt':{'documents':[],'coverage':[],'sha256':'a'*64}}
    return {'source_kind':'PROPOSAL','proposal':{**ref,'payload':payload,'review_status':'CURRENT',
        'flow':{'nodes':[{'id':'T01','planning_status':'DRAFT'}]},'answers':[]},
        'project_id':'qa','pending':{'proposal_ref':ref,'questions':[],'pending_count':0,'sha256':'c'*64},
        'pending_markdown':'没有登记问题。','template_ref':None,'experience':None,'discovery':None,'diagrams':[]}


def raw(document):
    out=BytesIO();document.save(out);return out.getvalue()


def document_text(document):
    return [''.join(node.text or '' for node in paragraph.iter(qn('w:t')))
            for paragraph in document.element.iter(qn('w:p'))]


def chinese_runs(document):
    return [run for run in document.element.iter(qn('w:r'))
            if HAN.search(''.join(t.text or '' for t in run.iter(qn('w:t'))))]


def font(run):
    run=getattr(run,'_r',run)
    properties=run.find(qn('w:rPr'))
    return properties.find(qn('w:rFonts')).get(qn('w:eastAsia'))


def assert_language(run):
    language=run.find(qn('w:rPr')).find(qn('w:lang'))
    check(language is not None and language.get(qn('w:val'))=='zh-CN')
    check(language.get(qn('w:eastAsia'))=='zh-CN')


def test_builtin_chinese_body_heading_latin_and_text_are_explicit_without_rewriting_input():
    source=snapshot()
    candidate=source['proposal']['payload']['candidate']
    specimen=candidate['goals'][0]
    before=deepcopy(source);output,audit=render_docx(source)
    document=Document(BytesIO(output));check(source==before)
    for run in chinese_runs(document):assert_language(run)
    body=next(p for p in document.paragraphs if p.text==specimen)
    check(font(body.runs[0])=='宋体')
    titles=[p for p in document.paragraphs if p.text=='项目任务书']
    check(len(titles)==1,'The current report has one neutral main title')
    title=titles[0];check(title.style.style_id=='Title')
    check(font(title.runs[0])=='黑体')
    original_text=candidate['title']
    originals=[p for p in document.paragraphs if p.text=='主题（题名原文）：'+original_text]
    check(len(originals)==1,'The generated-time title is preserved verbatim once')
    original=originals[0];check(original.style.style_id=='Normal')
    check(font(original.runs[0])=='宋体')
    paragraphs=document.paragraphs
    check(paragraphs[1]._p is original._p,'The subject follows the main report title')
    record=next(r for r in audit['historical_records'] if r['source_key']=='proposal.candidate.summary')
    check(record['preserved_in']=='source_snapshot' and
          record['sha256']==hashlib.sha256(candidate['summary'].encode('utf-8')).hexdigest())
    for name in ['Normal','Title','Heading 1','Heading 2','Heading 3']:
        fonts=document.styles[name]._element.find(qn('w:rPr')).find(qn('w:rFonts'))
        check(fonts.get(qn('w:ascii'))=='Times New Roman')
        check(fonts.get(qn('w:hAnsi'))=='Times New Roman')
        check(fonts.get(qn('w:eastAsiaTheme')) is None)
    check(audit['chinese_typography']['font_installation_and_native_word']=='NOT_RUN')
    check(audit['chinese_typography']['source_characters_modified'] is False)


@pytest.mark.parametrize('selection',['DIRECT','CHARACTER','PARAGRAPH','BASE_STYLE','DOCUMENT_DEFAULT'])
def test_explicit_template_cjk_wins_and_original_run_and_styles_are_not_changed(selection):
    document=Document();normal=document.styles['Normal'];normal.font.name='Cambria'
    properties=normal._element.get_or_add_rPr()
    # Isolate explicit cascade cases from the default Word theme.
    default=document.styles.element.find(qn('w:docDefaults')).find(qn('w:rPrDefault')).find(qn('w:rPr'))
    default.find(qn('w:rFonts')).attrib.pop(qn('w:eastAsiaTheme'),None)
    original=document.add_paragraph('固定原文、不可重新格式化');original.runs[0].font.color.rgb=RGBColor.from_string('A12233')
    slot=document.add_paragraph('{{ title }}');r=slot.runs[0];r.bold=True;r.font.size=Pt(19)
    if selection=='DIRECT':target=r._r.get_or_add_rPr()
    elif selection=='CHARACTER':
        style=document.styles.add_style('QA Character',WD_STYLE_TYPE.CHARACTER);r.style=style;target=style._element.get_or_add_rPr()
    elif selection=='PARAGRAPH':
        style=document.styles.add_style('QA Body',WD_STYLE_TYPE.PARAGRAPH);slot.style=style;target=style._element.get_or_add_rPr()
    elif selection=='BASE_STYLE':
        base=document.styles.add_style('QA Base',WD_STYLE_TYPE.PARAGRAPH)
        style=document.styles.add_style('QA Child',WD_STYLE_TYPE.PARAGRAPH);style.base_style=base;slot.style=style;target=base._element.get_or_add_rPr()
    else:target=default
    fonts=target.find(qn('w:rFonts'))
    if fonts is None:fonts=OxmlElement('w:rFonts');target.insert(0,fonts)
    fonts.set(qn('w:eastAsia'),'仿宋')
    original_run=etree.tostring(original.runs[0]._r);template=raw(document);template_sha=hashlib.sha256(template).hexdigest()
    output,audit=render_docx(snapshot(),template);result=Document(BytesIO(output))
    original_after=next(p for p in result.paragraphs if p.text==original.text)
    check(etree.tostring(original_after.runs[0]._r)==original_run)
    generated=next(p for p in result.paragraphs if p.text=='中文任务书 QA42')
    check(font(generated.runs[0])=='仿宋',selection);assert_language(generated.runs[0]._r)
    check(generated.runs[0].bold and generated.runs[0].font.size==Pt(19))
    check(hashlib.sha256(template).hexdigest()==template_sha)
    with ZipFile(BytesIO(template))as a,ZipFile(BytesIO(output))as b:
        check(a.read('word/styles.xml')==b.read('word/styles.xml'))
        check(a.read('word/theme/theme1.xml')==b.read('word/theme/theme1.xml'))
    check(audit['chinese_typography']['mode']=='TEMPLATE_FORMAT_INHERITANCE')


def test_conflicting_direct_theme_selects_hans_and_only_generated_run_is_materialized():
    document=Document();p=document.add_paragraph('{{ title }}');run=p.runs[0]
    fonts=run._r.get_or_add_rPr().get_or_add_rFonts();fonts.set(qn('w:eastAsia'),'错误的备用字体');fonts.set(qn('w:eastAsiaTheme'),'minorEastAsia')
    template=raw(document);output,audit=render_docx(snapshot(),template);result=Document(BytesIO(output))
    title=next(p for p in result.paragraphs if p.text=='中文任务书 QA42');check(font(title.runs[0]._r)=='宋体')
    check(title.runs[0]._r.rPr.rFonts.get(qn('w:eastAsiaTheme')) is None)
    assert_language(title.runs[0]._r)
    check(audit['chinese_typography']['font_resolution']['DIRECT_THEME']>=1)


def test_slot_copy_inherits_template_not_other_region_and_headers_comments_are_preserved():
    document=Document();document.styles['Normal'].font.name='Calibri'
    document.sections[0].header.paragraphs[0].text='固定页眉'
    document.sections[0].footer.paragraphs[0].text='固定页脚'
    fixed=document.add_paragraph('固定正文，不改语言和字体。');document.add_comment(fixed.runs[0],text='原批注保留',author='QA')
    slot=document.add_paragraph();slot.add_run('{{ ti');slot.add_run('tle }}')
    document.add_paragraph('{{ summary }}');document.add_paragraph('{{ report_sections }}')
    template=raw(document);output,audit=render_docx(snapshot(),template);result=Document(BytesIO(output))
    check(sum(p.text=='中文任务书 QA42'for p in result.paragraphs)==1)
    title=next(p for p in result.paragraphs if p.text=='中文任务书 QA42')
    for run in title.runs:
        if HAN.search(run.text):check(font(run._r)=='宋体');assert_language(run._r)
    check('保留原字符𠮷：中文、English 42 与标点。'in '\n'.join(document_text(result)))
    with ZipFile(BytesIO(template))as a,ZipFile(BytesIO(output))as b:
        for part in ['word/styles.xml','word/theme/theme1.xml','word/header1.xml','word/footer1.xml','word/comments.xml']:
            check(a.read(part)==b.read(part),part)
        check(MARKER_NS.encode()not in b.read('word/document.xml'))
    check(audit['chinese_typography']['generated_chinese_runs']>10)


def test_unresolved_template_theme_is_preserved_and_flagged_not_silently_replaced():
    document=Document();slot=document.add_paragraph('{{ title }}')
    fonts=slot.runs[0]._r.get_or_add_rPr().get_or_add_rFonts();fonts.set(qn('w:eastAsiaTheme'),'minorHAnsi')
    output,audit=render_docx(snapshot(),raw(document));result=Document(BytesIO(output))
    title=next(p for p in result.paragraphs if p.text=='中文任务书 QA42')
    check(title.runs[0]._r.rPr.rFonts.get(qn('w:eastAsiaTheme'))=='minorHAnsi')
    check(title.runs[0]._r.rPr.rFonts.get(qn('w:eastAsia')) is None)
    assert_language(title.runs[0]._r)
    check(audit['chinese_typography']['font_resolution']['NEEDS_FONT_REVIEW']==1)


def test_missing_cjk_uses_role_default_and_conditional_template_font_is_not_guessed():
    document=Document()
    for element in document.styles.element.iter(qn('w:rFonts')):
        element.attrib.pop(qn('w:eastAsia'),None);element.attrib.pop(qn('w:eastAsiaTheme'),None)
    document.styles['Normal'].font.name='Cambria';document.add_paragraph('固定原文')
    original=raw(document);output,audit=render_docx(snapshot(),original);result=Document(BytesIO(output))
    paragraph=next(p for p in result.paragraphs if '保留原字符𠮷' in p.text)
    check(font(paragraph.runs[0])=='宋体')
    heading=next(p for p in result.paragraphs if p.text=='1  目标与工作边界')
    check(font(heading.runs[0])=='黑体')
    check(result.styles['Normal'].font.name=='Cambria')
    check(audit['chinese_typography']['font_resolution']['FALLBACK']>1)
    condition=OxmlElement('w:tblStylePr');condition.set(qn('w:type'),'firstRow')
    rpr=OxmlElement('w:rPr');fonts=OxmlElement('w:rFonts');fonts.set(qn('w:eastAsia'),'微软雅黑');rpr.append(fonts);condition.append(rpr)
    document.styles['Table Grid']._element.append(condition)
    source=raw(document);output,audit=render_docx(snapshot(),source);result=Document(BytesIO(output))
    check(audit['chinese_typography']['font_resolution']['NEEDS_FONT_REVIEW']>0)
    with ZipFile(BytesIO(source))as a,ZipFile(BytesIO(output))as b:check(a.read('word/styles.xml')==b.read('word/styles.xml'))
    check(any(r.find(qn('w:rPr')).find(qn('w:rFonts')) is None for t in result.tables for r in t._tbl.iter(qn('w:r')) if HAN.search(''.join(n.text or ''for n in r.iter(qn('w:t'))))))


def test_existing_custom_marker_is_not_mistaken_for_generated_original_text():
    from packages.domain.chinese_word_typography import SLOT_MARKER
    from packages.domain.store import InvalidInput
    document=Document();fixed=document.add_paragraph('固定原文不可因自定义属性被重新格式化')
    fixed.runs[0]._r.set(SLOT_MARKER,'original-custom-value');before=etree.tostring(fixed.runs[0]._r)
    output,_=render_docx(snapshot(),raw(document));result=Document(BytesIO(output))
    check(etree.tostring(next(p for p in result.paragraphs if p.text==fixed.text).runs[0]._r)==before)
    document.add_paragraph('{{ title }}');source=raw(document);sha=hashlib.sha256(source).hexdigest()
    with pytest.raises(InvalidInput,match='reserved generated-text marker'):render_docx(snapshot(),source)
    check(hashlib.sha256(source).hexdigest()==sha)
