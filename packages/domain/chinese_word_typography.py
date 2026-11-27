"""Chinese formatting for newly generated Word runs, never template-wide edits.

Font names are requests to Word, not bundled fonts or proof of installed glyphs.
The template's explicit East Asian font/theme wins; defaults apply only when
the applicable formatting hierarchy has no such choice.
"""
from collections import Counter
import re

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from lxml import etree
from packages.domain.store import InvalidInput

MARKER_NS = 'urn:cirven:generated-word-text:v1'
SLOT_MARKER = '{' + MARKER_NS + '}slot'
HAN = re.compile('[\u3400-\u9fff\uf900-\ufaff\U00020000-\U000323af]')
A = '{http://schemas.openxmlformats.org/drawingml/2006/main}'


def _stories(document):
    yield document.element
    for relation in document.part.rels.values():
        if not relation.is_external and relation.reltype.rsplit('/', 1)[-1] in {'header', 'footer'}:
            yield relation.target_part.element


def mark_template_slots(document):
    """Tag only runs intersecting declared text slots, in the in-memory copy.

    docxtpl reparses XML, so object identity cannot track those substitutions.
    The private attribute is removed before saving, including non-Chinese slots.
    """
    for root in _stories(document):
        if any(SLOT_MARKER in run.attrib for run in root.iter(qn('w:r'))):
            raise InvalidInput('Template uses a reserved generated-text marker; original bytes were not changed')
        for paragraph in root.iter(qn('w:p')):
            texts = list(paragraph.iter(qn('w:t')))
            spans = list(re.finditer(r'\{\{.*?\}\}', ''.join(t.text or '' for t in texts), re.S))
            if not spans:
                continue
            offset = 0
            for text in texts:
                end = offset + len(text.text or '')
                if any(match.start() < end and match.end() > offset for match in spans):
                    run = text.getparent()
                    if run.tag == qn('w:r'):
                        run.set(SLOT_MARKER, '1')
                offset = end


def builtin_chinese_fonts(document):
    """Only the newly created, built-in report; never a supplied template."""
    for name in ['Normal', 'Title', *('Heading ' + str(i) for i in range(1, 10))]:
        if name not in document.styles:
            continue
        fonts = document.styles[name]._element.get_or_add_rPr().get_or_add_rFonts()
        for attr, value in [('ascii', 'Times New Roman'), ('hAnsi', 'Times New Roman'),
                            ('eastAsia', '宋体' if name == 'Normal' else '黑体')]:
            fonts.set(qn('w:' + attr), value)
        for attr in ('asciiTheme', 'hAnsiTheme', 'eastAsiaTheme'):
            fonts.attrib.pop(qn('w:' + attr), None)


class GeneratedChineseTypography:
    def __init__(self, document, *, template=False, slot_markers=False):
        self.document = document
        self.template = template
        self.slot_markers = slot_markers
        self.original = {run for root in _stories(document) for run in root.iter(qn('w:r'))}
        self.styles = {style.style_id: style._element for style in document.styles}
        self.default = document.styles.element.find(qn('w:docDefaults'))
        self.theme = None
        for relation in document.part.rels.values():
            if not relation.is_external and relation.reltype.rsplit('/', 1)[-1] == 'theme':
                # The package was already admitted by bounded_docx.
                self.theme = etree.fromstring(relation.target_part.blob,
                    parser=etree.XMLParser(resolve_entities=False, no_network=True))
                break

    def _style_properties(self, style_id):
        visited = set()
        while style_id and style_id not in visited and len(visited) < 64:
            visited.add(style_id)
            style = self.styles.get(style_id)
            if style is None:
                break
            yield style.find(qn('w:rPr'))
            parent = style.find(qn('w:basedOn'))
            style_id = parent.get(qn('w:val')) if parent is not None else None

    def _properties(self, run):
        rpr = run.find(qn('w:rPr'))
        yield 'DIRECT', rpr
        character = rpr.find(qn('w:rStyle')) if rpr is not None else None
        if character is not None:
            for value in self._style_properties(character.get(qn('w:val'))):
                yield 'CHARACTER_STYLE', value
        paragraph = next((p for p in run.iterancestors() if p.tag == qn('w:p')), None)
        ppr = paragraph.find(qn('w:pPr')) if paragraph is not None else None
        style = ppr.find(qn('w:pStyle')) if ppr is not None else None
        # Unstyled paragraphs use the document's real default paragraph style.
        default = self.document.styles.default(1)
        style_id = style.get(qn('w:val')) if style is not None else (default.style_id if default is not None else 'Normal')
        for value in self._style_properties(style_id):
            yield 'PARAGRAPH_STYLE', value
        table = next((p for p in run.iterancestors() if p.tag == qn('w:tbl')), None)
        if table is not None:
            properties = table.find(qn('w:tblPr'))
            style = properties.find(qn('w:tblStyle')) if properties is not None else None
            if style is not None:
                # Conditional table formatting depends on Word's tblLook and
                # row/column band cascade. Do not overwrite a template's
                # explicit conditional CJK choice with a guessed body font.
                table_id = style.get(qn('w:val'))
                visited = set()
                while table_id and table_id not in visited and len(visited) < 64:
                    visited.add(table_id)
                    table_style = self.styles.get(table_id)
                    if table_style is None:
                        break
                    for condition in table_style.findall(qn('w:tblStylePr')):
                        properties = condition.find(qn('w:rPr'))
                        if properties is not None:
                            yield 'CONDITIONAL_TABLE_STYLE', properties
                    parent = table_style.find(qn('w:basedOn'))
                    table_id = parent.get(qn('w:val')) if parent is not None else None
                for value in self._style_properties(style.get(qn('w:val'))):
                    yield 'TABLE_STYLE', value
        if self.default is not None:
            holder = self.default.find(qn('w:rPrDefault'))
            yield 'DOCUMENT_DEFAULT', holder.find(qn('w:rPr')) if holder is not None else None

    def _theme_font(self, token):
        if self.theme is None or token not in {'majorEastAsia', 'minorEastAsia'}:
            return None
        kind = 'majorFont' if token.startswith('major') else 'minorFont'
        scheme = self.theme.find('.//' + A + 'fontScheme/' + A + kind)
        if scheme is None:
            return None
        # New Chinese text is explicitly zh-CN: do not select Jpan/Hant via
        # an inherited en-US/Japanese locale or a generic Latin theme font.
        specific = next((font.get('typeface') for font in scheme.findall(A + 'font')
                         if font.get('script') == 'Hans' and font.get('typeface')), None)
        east = scheme.find(A + 'ea')
        return specific or (east.get('typeface') if east is not None else None)

    def _font(self, run):
        for origin, properties in self._properties(run):
            fonts = properties.find(qn('w:rFonts')) if properties is not None else None
            if fonts is None:
                continue
            theme = fonts.get(qn('w:eastAsiaTheme'))
            explicit = fonts.get(qn('w:eastAsia'))
            if origin == 'CONDITIONAL_TABLE_STYLE' and (theme or explicit):
                return None, 'UNRESOLVED_TABLE_CONDITION'
            if theme:
                value = self._theme_font(theme)
                # Preserve an unresolved explicit theme rather than silently
                # claiming a default font over a user's unknown choice.
                return value, origin + '_THEME' if value else 'UNRESOLVED_THEME'
            if explicit:
                return explicit, origin
        paragraph = next((p for p in run.iterancestors() if p.tag == qn('w:p')), None)
        ppr = paragraph.find(qn('w:pPr')) if paragraph is not None else None
        style = ppr.find(qn('w:pStyle')) if ppr is not None else None
        style_id = style.get(qn('w:val'), '') if style is not None else ''
        return ('黑体' if style_id == 'Title' or style_id.startswith('Heading') else '宋体'), 'FALLBACK'

    def apply(self):
        sources, fonts = Counter(), Counter()
        count = 0
        for root in _stories(self.document):
            for run in root.iter(qn('w:r')):
                marked = self.slot_markers and run.attrib.pop(SLOT_MARKER, None) is not None
                generated = marked or run not in self.original
                if generated and HAN.search(''.join(node.text or '' for node in run.iter(qn('w:t')))):
                    value, source = self._font(run)
                    properties = run.get_or_add_rPr()
                    if value and len(value) <= 256:
                        rfonts = properties.get_or_add_rFonts()
                        rfonts.set(qn('w:eastAsia'), value)
                        rfonts.attrib.pop(qn('w:eastAsiaTheme'), None)
                        if len(fonts) < 64 or value in fonts:
                            fonts[value] += 1
                    else:
                        source = 'NEEDS_FONT_REVIEW'
                    language = properties.find(qn('w:lang'))
                    if language is None:
                        language = OxmlElement('w:lang')
                        properties.insert_element_before(language, 'w:eastAsianLayout', 'w:specVanish', 'w:oMath')
                    language.set(qn('w:val'), 'zh-CN')
                    language.set(qn('w:eastAsia'), 'zh-CN')
                    sources[source] += 1
                    count += 1
                if generated and MARKER_NS in run.nsmap.values():
                    etree.cleanup_namespaces(run, keep_ns_prefixes=[prefix for prefix, uri in run.nsmap.items()
                        if prefix is not None and uri != MARKER_NS])
        return {'policy': 'GENERATED_CHINESE_RUNS_V1',
                'mode': 'TEMPLATE_FORMAT_INHERITANCE' if self.template else 'BUILT_IN_CHINESE_REPORT',
                'generated_chinese_runs': count, 'east_asian_fonts': dict(fonts),
                'font_resolution': dict(sources), 'language': 'zh-CN',
                'font_installation_and_native_word': 'NOT_RUN', 'source_characters_modified': False}
