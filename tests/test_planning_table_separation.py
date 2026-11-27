"""Generated table boundaries; actual native pagination remains Root's check."""
from io import BytesIO

from docx import Document
from docx.oxml.ns import qn
from docx.shared import Pt

from packages.domain.planning_docx import _table


def check(value,message):
    if not value:raise AssertionError(message)


def test_adjacent_node_and_relation_tables_have_real_compact_separator_and_own_headers():
    document=Document()
    original=document.add_table(rows=1,cols=1);original.cell(0,0).text='模板原表保留'
    original_xml=original._tbl.xml
    document.add_paragraph('原模板后已有正文')
    _table(document,['节点编号','名称与说明','需求依据'],[['N01','节点正文','R01']],[.15,.65,.2])
    _table(document,['关系编号','起点 → 终点','关系'],[['E05','N01 → N02','关系正文']],[.15,.35,.5])
    output=BytesIO();document.save(output);restored=Document(BytesIO(output.getvalue()))
    check(restored.tables[0]._tbl.xml==original_xml,'Existing template table must remain untouched')
    node,relation=restored.tables[1:]
    separator=node._tbl.getnext()
    check(separator.tag==qn('w:p') and separator.getnext() is relation._tbl,'A paragraph must separate the exact node and relation tables')
    check(not separator.findall('.//'+qn('w:t')),'Separator does not add explanatory text')
    paragraph=next(p for p in restored.paragraphs if p._p is separator)
    check(paragraph.paragraph_format.line_spacing==Pt(1),'Separator line is exactly one point')
    check(paragraph.paragraph_format.space_before==Pt(0) and paragraph.paragraph_format.space_after==Pt(4),'Separator spacing remains compact')
    check(paragraph.paragraph_format.keep_with_next is False,'Separator does not chain tables across pages')
    for table,heading in [(node,'节点编号'),(relation,'关系编号')]:
        check(table.rows[0].cells[0].text==heading,'Each table keeps its own actual heading')
        check(table.rows[0]._tr.trPr.find(qn('w:tblHeader')) is not None,'Each header independently repeats')
        check(all(p.paragraph_format.keep_with_next is True for c in table.rows[0].cells for p in c.paragraphs),'Each header stays with its following body row')
        check(table._tbl.getnext().tag==qn('w:p'),'Every generated table ends with its own separator')
