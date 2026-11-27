import test from 'node:test';
import assert from 'node:assert/strict';
import {summaryHTML,summaryNeedsDisclosure,summarySelectionRange} from '../src/summary-content.ts';
import {candidateTextSelection} from '../src/text-feedback.ts';

test('disclosure is explicit for long documents while short explanations stay visible',()=>{
 assert.equal(summaryNeedsDisclosure('简短说明。\n保留完整文字。'),false);
 assert.equal(summaryNeedsDisclosure('文'.repeat(641)),true);
 assert.equal(summaryNeedsDisclosure(Array(14).fill('short').join('\n')),true);
});
test('rendered unique quotation maps to the exact immutable Markdown field, including codepoints',async()=>{
 const raw='## 说明\n中文🙂 **保留人工修改**，不能覆盖。',before=raw;
 const range=summarySelectionRange(raw,'保留人工修改');assert.ok(range);
 const actual=await candidateTextSelection(raw,range.start,range.end,'/summary');
 assert.equal(actual.text,'保留人工修改');assert.equal(actual.locator.char_start,Array.from(raw.slice(0,range.start)).length);
 assert.equal(Array.from(raw).slice(actual.locator.char_start,actual.locator.char_end).join(''),actual.text);
 assert.equal(raw,before);assert.equal(actual.offset_unit,'UNICODE_CODEPOINT');
});
test('repeated, overlapping, transformed or cross-format quotations never guess a raw offset',()=>{
 for(const [raw,quote]of [['重复。重复。','重复'],['aaa','aa'],['**原文**\n下一段','原文\n下一段'],['A &amp; B','A & B'],['one','']])assert.equal(summarySelectionRange(raw,quote),undefined);
});
test('DOM-less rendering keeps raw text escaped and never executes HTML',()=>{
 const raw='<img src=x onerror="globalThis.compromised=true">\n<script>alert(1)</script>';
 const html=summaryHTML(raw);assert.ok(html.includes('&lt;img'));assert.ok(!html.includes('<script>'));assert.equal(globalThis.compromised,undefined);
});
