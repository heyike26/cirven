import {Marked} from 'marked';
import DOMPurify from 'dompurify';

const escapeHTML=(value:string)=>value.replace(/[&<>"']/g,char=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]!));
const parser=new Marked({gfm:true,breaks:true,renderer:{
 heading({tokens,depth}){const level=Math.min(6,Math.max(3,depth+1));return `<h${level}>${this.parser.parseInline(tokens)}</h${level}>\n`;},
 // Summaries never fetch remote media or embed executable document content.
 image({text}){return escapeHTML(text);},
 html({text}){return escapeHTML(text);},
 link({href,title,tokens}){const text=this.parser.parseInline(tokens);return /^(?:https?:|mailto:)/i.test(href)?`<a href="${escapeHTML(href)}"${title?` title="${escapeHTML(title)}"`:''} target="_blank" rel="noopener noreferrer">${text}</a>`:text;},
}});

/** Presentation only. Never rewrites the saved candidate or follows its URLs. */
export function summaryHTML(source:string):string{
 if(typeof DOMPurify.sanitize!=='function')return `<p>${escapeHTML(source).replace(/\n/g,'<br>')}</p>`;
 return DOMPurify.sanitize(parser.parse(source,{async:false}),{
  ALLOWED_TAGS:['p','br','h3','h4','h5','h6','strong','em','del','blockquote','ul','ol','li','code','pre','hr','table','thead','tbody','tr','th','td','a'],
  ALLOWED_ATTR:['href','title','start','target','rel'],ALLOW_DATA_ATTR:false,ALLOW_ARIA_ATTR:false,
  ADD_URI_SAFE_ATTR:['start','target','rel'],
  ALLOWED_URI_REGEXP:/^(?:https?:|mailto:)/i,
 });
}

export function summaryNeedsDisclosure(source:string):boolean{
 return source.length>640||source.split('\n').length>12;
}

/** A rendered quote is usable only when it proves one exact raw-field range.
 * Markdown syntax, repeated quotes and decoded entities must never guess an
 * offset in the immutable candidate basis. Offsets use the existing JS API. */
export function summarySelectionRange(source:string,quote:string):{start:number;end:number}|undefined{
 if(!quote)return;
 const start=source.indexOf(quote);
 if(start<0||source.indexOf(quote,start+1)!==-1)return;
 return {start,end:start+quote.length};
}
