"""Actual parser/OOXML tests. No API, model, Office or GUI qualification."""
from io import BytesIO
import hashlib
import subprocess
import sys
from zipfile import ZipFile

import pytest
from docx import Document
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Pt

from packages.domain.planning_docx import bounded_docx
from packages.domain.planning_word_blocks import write_markdown
from packages.domain.store import InvalidInput


def check(value, message='Word block acceptance failed'):
    if not value: raise AssertionError(message)


def package(document):
    out=BytesIO();document.save(out);raw=out.getvalue();bounded_docx(raw)
    return raw,Document(BytesIO(raw))


def test_parser_is_not_loaded_by_regular_docx_module_import():
    p=subprocess.run([sys.executable,'-B','-c',
        "import sys;import packages.domain.planning_docx;raise SystemExit(any(n=='markdown_it' or n.startswith('markdown_it.') for n in sys.modules))"],
        capture_output=True,text=True,timeout=15)
    check(p.returncode==0,p.stdout+p.stderr)


def test_real_tokens_create_editable_headings_lists_tables_and_complete_coverage():
    source='## 第一节\r\n\r\n正文 **粗体**、*斜体*、~~删除~~ &amp; 保留。\r\n下一行。\r\n\r\n3. 第三项\r\n4. 第四项\r\n   - 内层\r\n\r\n| 列一 | 列二 |\r\n| --- | --- |\r\n| 单元甲 | 单元乙 |\r\n\r\n```python\r\n## 代码井号不可变标题\r\nprint("原样")\r\n```\r\n'
    before=source;document=Document();result=write_markdown(document,source)
    raw,reopened=package(document)
    check(source==before)
    check(reopened.paragraphs[0].style.name.startswith('Heading '))
    check(reopened.paragraphs[0].text=='第一节')
    check(any(r.text=='粗体' and r.bold for p in reopened.paragraphs for r in p.runs))
    check(any(r.text=='斜体' and r.italic for p in reopened.paragraphs for r in p.runs))
    check(any(r.text=='删除' and r.font.strike for p in reopened.paragraphs for r in p.runs))
    check([[c.text for c in r.cells]for r in reopened.tables[0].rows]==[['列一','列二'],['单元甲','单元乙']])
    numbered=[p for p in reopened.paragraphs if p._p.find('w:pPr/w:numPr',{'w':qn('w:p').split('}')[0][1:]})is not None]
    check([p.text for p in numbered]==['第三项','第四项','内层'])
    check(any(p.text=='## 代码井号不可变标题' and p.style.name=='Normal' for p in reopened.paragraphs))
    check(result['source_sha256']==hashlib.sha256(source.encode()).hexdigest())
    check(result['coverage']=='ALL_SOURCE_LINES_ACCOUNTED')
    cursor=0
    recovered=[]
    for block in result['blocks']:
        check(block['char_start']==cursor);cursor=block['char_end'];recovered.append(source[block['char_start']:cursor])
    check(cursor==len(source));check(''.join(recovered)==source);check(len(raw)>0)


def test_html_links_images_and_unused_definitions_remain_inert_visible_content():
    source='\\# 字面井号\n\n<script>not executed</script>\n\n[链接](https://example.invalid/a?x=1&y=2) ![图注](https://example.invalid/p.png)\n\n[未使用]: https://example.invalid/source\n'
    document=Document();write_markdown(document,source);raw,reopened=package(document)
    text='\n'.join(p.text for p in reopened.paragraphs)
    for value in ['# 字面井号','<script>not executed</script>','https://example.invalid/a?x=1&y=2','图注','https://example.invalid/p.png','[未使用]: https://example.invalid/source']:
        check(value in text,value)
    with ZipFile(BytesIO(raw))as z:
        check(not any(n.startswith('word/media/')for n in z.namelist()))
        for name in z.namelist():
            if name.endswith('.rels'):check(b'TargetMode="External"'not in z.read(name))


def test_template_paragraph_baseline_is_preserved_but_heading_uses_heading_style():
    document=Document();seed=document.add_paragraph();seed.paragraph_format.left_indent=Pt(10)
    seed.paragraph_format.space_after=Pt(7);run=seed.add_run('{{ summary }}');run.font.name='Cambria';run.font.size=Pt(12)
    document.styles['Heading 3'].font.size=Pt(16)
    write_markdown(document,'## 正式小节\n\n正文。',seed=seed._p)
    _,reopened=package(document);heading=reopened.paragraphs[1];body=reopened.paragraphs[2]
    check(heading.style.name=='Heading 3');check(heading.runs[0].font.size is None)
    check(heading.style.font.size==Pt(16));check(body.runs[0].font.name=='Cambria')
    check(body.runs[0].font.size==Pt(12));check(body.paragraph_format.space_after==Pt(7))
    check(body.paragraph_format.left_indent==Pt(10));check(body.paragraph_format.widow_control)


def test_ordered_lists_restart_at_declared_value_and_nested_levels_are_real_numbering():
    document=Document();write_markdown(document,'3. 三\n4. 四\n   1. 子项\n\n间隔\n\n7. 七\n')
    _,reopened=package(document);root=reopened.part.numbering_part.element
    nums=[p._p.get_or_add_pPr().numPr for p in reopened.paragraphs if p.text in {'三','四','子项','七'}]
    check(nums[0].numId.val==nums[1].numId.val)
    check(nums[0].numId.val!=nums[-1].numId.val)
    check(nums[2].ilvl.val==1)
    check(any(e.get(qn('w:val'))=='3'for e in root.iter(qn('w:start'))))
    check(any(e.get(qn('w:val'))=='7'for e in root.iter(qn('w:start'))))


def test_nested_markdown_table_in_template_cell_is_explicitly_unsupported():
    document=Document();cell=document.add_table(rows=1,cols=1).cell(0,0);seed=cell.paragraphs[0]._p
    with pytest.raises(InvalidInput,match='inside a Word summary table cell'):
        write_markdown(document,'| 一 | 二 |\n| - | - |\n| 甲 | 乙 |',seed=seed)


def test_whitespace_and_unparsed_reference_lines_are_covered_without_rewriting_source():
    source='\n\n[保留定义]: /relative-path\n\n';document=Document();result=write_markdown(document,source)
    check([p.text for p in document.paragraphs]==['[保留定义]: /relative-path'])
    check(result['blocks'][-1]['char_end']==len(source))
    check(any(b['kind']=='LITERAL_UNPARSED'for b in result['blocks']))


def test_bounds_reject_instead_of_silently_truncating():
    with pytest.raises(InvalidInput,match='bounded text limit'):
        write_markdown(Document(),'字'*1_000_001)


def test_unicode_separators_titles_zero_numbering_and_blank_coverage_are_not_lost():
    source='中文😀\u2028仍在同一原始行。\n\n[名称](https://example.invalid "链接说明")\n\n0. 零起点\n'
    document=Document();result=write_markdown(document,source);_,reopened=package(document)
    text='\n'.join(p.text for p in reopened.paragraphs)
    check('中文😀\u2028仍在同一原始行。'in text);check('链接说明'in text)
    check(any(e.get(qn('w:val'))=='0'for e in reopened.part.numbering_part.element.iter(qn('w:start'))))
    check(result['blocks'][-1]['char_end']==len(source))
    blank=write_markdown(Document(),'\n'*24000)
    check(len(blank['blocks'])==1);check(blank['blocks'][0]['char_end']==24000)
    with pytest.raises(InvalidInput,match='unavailable in XML'):write_markdown(Document(),'原文\x00原文')
