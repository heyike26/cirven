import {Marked} from 'marked';

export function replyLinkTarget(value){
  if(typeof value!=='string'||value.length>4096||/[\u0000-\u0020\u007f]/.test(value))return null;
  try{const url=new URL(value);return ['http:','https:','mailto:'].includes(url.protocol)&&!url.username&&!url.password?url.href:null;}catch{return null;}
}

// Ask the same mature Markdown parser which links actually render. Raw HTML,
// images and code never grant an external-open target. This performs no IO.
export function replyLinkTargets(source){
  const targets=new Set();
  const parser=new Marked({gfm:true,breaks:true,renderer:{
    html(){return '';},image(){return '';},
    link({href,tokens}){const target=replyLinkTarget(href);if(target)targets.add(target);return this.parser.parseInline(tokens);},
  }});
  parser.parse(source,{async:false});return [...targets];
}
