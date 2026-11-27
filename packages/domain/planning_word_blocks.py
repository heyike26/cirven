"""Bounded Markdown presentation for Word; never changes the source candidate.

The parser is loaded only when a document is rendered. No HTML conversion,
network resources, Jinja evaluation or additional model execution occurs here.
"""
from copy import deepcopy
import hashlib
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Emu, Pt

from packages.domain.store import InvalidInput


def _digest(value):
    return hashlib.sha256(value.encode('utf-8')).hexdigest()


def _token(node):
    return node.token if node.token is not None else node.nester_tokens.opening


def _numbering(document, ordered, start):
    root = document.part.numbering_part.element
    abstract_id = max([int(e.get(qn('w:abstractNumId'))) for e in root.findall(qn('w:abstractNum'))] or [-1]) + 1
    num_id = max([int(e.get(qn('w:numId'))) for e in root.findall(qn('w:num'))] or [0]) + 1
    abstract = OxmlElement('w:abstractNum'); abstract.set(qn('w:abstractNumId'), str(abstract_id))
    for depth in range(9):
        level = OxmlElement('w:lvl'); level.set(qn('w:ilvl'), str(depth))
        for name, value in [('start', str(start)), ('numFmt', 'decimal' if ordered else 'bullet'),
                            ('lvlText', f'%{depth + 1}.' if ordered else '\u2022'), ('lvlJc', 'left')]:
            element = OxmlElement('w:' + name); element.set(qn('w:val'), value); level.append(element)
        ppr = OxmlElement('w:pPr'); indent = OxmlElement('w:ind')
        indent.set(qn('w:left'), str(360 * (depth + 1))); indent.set(qn('w:hanging'), '180')
        ppr.append(indent); level.append(ppr); abstract.append(level)
    first_num = root.find(qn('w:num'))
    if first_num is None: root.append(abstract)
    else: root.insert(root.index(first_num), abstract)
    num = OxmlElement('w:num'); num.set(qn('w:numId'), str(num_id))
    ref = OxmlElement('w:abstractNumId'); ref.set(qn('w:val'), str(abstract_id)); num.append(ref); root.append(num)
    return num_id


class _Writer:
    def __init__(self, document, seed, heading_level, width):
        self.document, self.seed, self.heading_level, self.width = document, seed, heading_level, width
        self.emitted = []
        self.headings = set()
        self.seed_rpr = None
        if seed is not None:
            for run in seed.findall(qn('w:r')):
                if run.find(qn('w:rPr')) is not None:
                    self.seed_rpr = deepcopy(run.find(qn('w:rPr'))); break

    def paragraph(self, style=None, *, target=None, depth=0):
        p = (target or self.document).add_paragraph()
        if style is None and self.seed is not None and self.seed.find(qn('w:pPr')) is not None:
            p._p.insert(0, deepcopy(self.seed.find(qn('w:pPr'))))
        if style is not None:
            p.style = style
            self.headings.add(p._p)
            p.paragraph_format.keep_with_next = True
        else:
            p.paragraph_format.keep_with_next = False
        p.paragraph_format.widow_control = True
        if depth: p.paragraph_format.left_indent = Pt(18 * depth)
        self.emitted.append(p)
        return p

    def text(self, paragraph, text, styles=None):
        run = paragraph.add_run(text)
        if self.seed_rpr is not None and paragraph._p not in self.headings:
            run._r.insert(0, deepcopy(self.seed_rpr))
        for name, value in (styles or {}).items():
            if name == 'strike': run.font.strike = value
            else: setattr(run, name, value)
        return run

    def inline(self, paragraph, nodes, styles=None):
        for node in nodes:
            kind = node.type; token = _token(node)
            if kind == 'inline':
                self.inline(paragraph, node.children, styles)
            elif kind in {'text', 'text_special', 'code_inline', 'html_inline'}:
                self.text(paragraph, token.content, styles)
            elif kind in {'softbreak', 'hardbreak'}:
                self.text(paragraph, '\n', styles)
            elif kind in {'strong', 'em', 's'}:
                self.inline(paragraph, node.children, {**(styles or {}), {'strong':'bold','em':'italic','s':'strike'}[kind]:True})
            elif kind == 'link':
                self.inline(paragraph, node.children, styles)
                # Inert visible URI, never an external package relationship.
                self.text(paragraph, ' (' + str(token.attrGet('href') or '') + ')', styles)
                if token.attrGet('title'):self.text(paragraph,' ('+str(token.attrGet('title'))+')',styles)
            elif kind == 'image':
                self.text(paragraph, token.content + ' (' + str(token.attrGet('src') or '') + ')', styles)
                if token.attrGet('title'):self.text(paragraph,' ('+str(token.attrGet('title'))+')',styles)
            else:
                raise InvalidInput('Unsupported Word Markdown inline: ' + kind)

    def block(self, node, *, list_info=None, quote_depth=0):
        kind = node.type; token = _token(node)
        if kind in {'paragraph', 'heading'}:
            style = None
            if kind == 'heading':
                level = min(6, self.heading_level + int(token.tag[1:]) - 1)
                style = 'Heading ' + str(level)
                if style not in self.document.styles:
                    raise InvalidInput('Word template is missing heading style: ' + style)
            p = self.paragraph(style, depth=quote_depth)
            if list_info is not None:
                num_id, depth, marker = list_info
                p.paragraph_format.left_indent = Pt(18 * (depth + 1))
                p.paragraph_format.first_line_indent = Pt(-9 if marker else 0)
                if marker:
                    pr = p._p.get_or_add_pPr().get_or_add_numPr()
                    pr.get_or_add_ilvl().val = depth; pr.get_or_add_numId().val = num_id
            self.inline(p, node.children)
        elif kind in {'bullet_list', 'ordered_list'}:
            depth = 0 if list_info is None else list_info[1] + 1
            if depth > 8: raise InvalidInput('Word Markdown list nesting exceeds 9 levels')
            start = token.attrGet('start')
            num_id = _numbering(self.document, kind == 'ordered_list', 1 if start is None else int(start))
            for item in node.children:
                if item.type != 'list_item': raise InvalidInput('Invalid Word Markdown list item')
                first = True
                for child in item.children:
                    self.block(child, list_info=(num_id, depth, first), quote_depth=quote_depth)
                    if child.type in {'paragraph', 'heading'}: first = False
        elif kind == 'blockquote':
            for child in node.children: self.block(child, list_info=list_info, quote_depth=quote_depth + 1)
        elif kind in {'fence', 'code_block', 'html_block'}:
            # Literal code is editable text; no evaluator, syntax highlighter or fetch.
            lines = token.content.split('\n')
            if len(lines) > 1 and lines[-1] == '': lines.pop()
            for line in lines:
                self.text(self.paragraph(depth=quote_depth), line)
        elif kind == 'hr':
            p = self.paragraph(); border = OxmlElement('w:pBdr'); bottom = OxmlElement('w:bottom')
            for key, value in [('val','single'),('sz','4'),('space','4'),('color','808080')]: bottom.set(qn('w:' + key), value)
            border.append(bottom); p._p.get_or_add_pPr().append(border)
        elif kind == 'table':
            if self.seed is not None and self.seed.getparent().tag != qn('w:body'):
                raise InvalidInput('Markdown tables inside a Word summary table cell are unsupported')
            rows = [row for group in node.children for row in group.children]
            if not rows or len(rows) > 512: raise InvalidInput('Word Markdown table row limit exceeded')
            columns = len(rows[0].children)
            if not 0 < columns <= 32 or any(len(row.children) != columns for row in rows):
                raise InvalidInput('Word Markdown table has unsupported columns')
            table = self.document.add_table(rows=0, cols=columns); table.autofit = False
            if 'Table Grid' in self.document.styles: table.style = 'Table Grid'
            width = self.width or (self.document.sections[-1].page_width - self.document.sections[-1].left_margin - self.document.sections[-1].right_margin)
            table._tbl.tblPr.find(qn('w:tblW')).set(qn('w:type'),'dxa')
            table._tbl.tblPr.find(qn('w:tblW')).set(qn('w:w'),str(round(width/635)))
            for column in table.columns: column.width = Emu(int(width / columns))
            for row in rows:
                cells = table.add_row().cells
                for cell, child in zip(cells, row.children):
                    cell.width = Emu(int(width / columns)); p = cell.paragraphs[0]
                    self.emitted.append(p); self.inline(p, child.children, {'bold':True} if child.type == 'th' else None)
            header = OxmlElement('w:tblHeader'); table.rows[0]._tr.get_or_add_trPr().append(header)
        else:
            raise InvalidInput('Unsupported Word Markdown block: ' + kind)


def write_markdown(document, source, *, seed=None, heading_level=2, width=None, source_key='summary'):
    """Append native Word blocks and a source-line coverage receipt.

    Blank separators and Markdown syntax stay in the immutable source snapshot;
    every nonblank line is mapped to a rendered block or inert literal fallback.
    """
    if not isinstance(source, str) or len(source) > 1_000_000:
        raise InvalidInput('Word Markdown input exceeds the bounded text limit')
    if any((ord(c) < 32 and c not in '\t\r\n') or 0xD800 <= ord(c) <= 0xDFFF or ord(c) in {0xFFFE,0xFFFF} for c in source):
        raise InvalidInput('Word Markdown contains a character unavailable in XML documents')
    try:
        from markdown_it import MarkdownIt
        from markdown_it.tree import SyntaxTreeNode
    except ImportError as exc:
        raise InvalidInput('Word Markdown parser is unavailable; install the pinned offline Word capability') from exc
    parser = MarkdownIt('commonmark', {'html':False, 'linkify':False, 'typographer':False, 'maxNesting':20}).enable(['table','strikethrough'])
    tokens = parser.parse(source)
    if len(tokens) > 40000: raise InvalidInput('Word Markdown token limit exceeded')
    tree = SyntaxTreeNode(tokens); lines = re.findall(r'[^\r\n]*(?:\r\n|\r|\n|$)', source)
    if lines and lines[-1] == '': lines.pop()
    starts = [0]
    for line in lines: starts.append(starts[-1] + len(line))
    writer = _Writer(document, seed, heading_level, width); ranges = []; cursor = 0

    def record(start, end, kind, before):
        ranges.append({'kind':kind, 'char_start':starts[start], 'char_end':starts[end],
                       'paragraphs':len(writer.emitted) - before})

    def literal_until(end):
        nonlocal cursor
        while cursor < end:
            if not lines[cursor].strip():
                start=cursor
                while cursor<end and not lines[cursor].strip():cursor+=1
                record(start,cursor,'BLANK_SEPARATOR',len(writer.emitted));continue
            before = len(writer.emitted)
            writer.text(writer.paragraph(), lines[cursor].rstrip('\r\n'))
            record(cursor, cursor + 1, 'LITERAL_UNPARSED', before)
            cursor += 1

    for node in tree.children:
        bounds = _token(node).map
        if bounds is None or bounds[0] < cursor or bounds[1] > len(lines):
            raise InvalidInput('Word Markdown source mapping is inconsistent')
        literal_until(bounds[0]); before = len(writer.emitted)
        writer.block(node); record(bounds[0], bounds[1], node.type, before); cursor = bounds[1]
    literal_until(len(lines))
    return {'schema':'eeas.word-block-coverage.v1', 'source_key':source_key,
            'source_sha256':_digest(source), 'source_characters':len(source),
            'coverage':'ALL_SOURCE_LINES_ACCOUNTED', 'blocks':ranges,
            'rendered_text_sha256':_digest('\n'.join(p.text for p in writer.emitted)),
            'external_resources_loaded':0,'media_policy':'ALT_AND_URI_ONLY','links_policy':'TEXT_AND_URI_ONLY'}
