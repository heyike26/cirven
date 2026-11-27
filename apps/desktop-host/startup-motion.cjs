'use strict';

// Presentation only: Main supplies its already verified local brand SVG. This
// module never reads assets, schedules startup, opens windows or takes focus.
const STARTUP_MOTION_DURATION_MS=680;
const PARTS=['outer-C','inner-fold','lower-sweep'];
const escapeAttribute=value=>value.replace(/[&<>"']/g,character=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[character]));

function checkedSVG(value){
  if(typeof value!=='string'||!value.trim()||Buffer.byteLength(value,'utf8')>32768)throw TypeError('STARTUP_MOTION_LOCAL_SVG_REQUIRED');
  const svg=value.trim();
  if(!/^<svg\b[^>]*>[\s\S]*<\/svg>$/i.test(svg)||(svg.match(/<svg\b/gi)||[]).length!==1||(svg.match(/<\/svg\s*>/gi)||[]).length!==1
    ||/<(?:script|foreignObject|image|text|style|animate(?:Motion|Transform)?|set|iframe|object|embed)\b|<!ENTITY|<!DOCTYPE|<\?/i.test(svg)
    ||/\bon[a-z]+\s*=|\b(?:href|src)\s*=/i.test(svg))throw TypeError('STARTUP_MOTION_LOCAL_SVG_REQUIRED');
  for(const match of svg.matchAll(/url\s*\(([^)]*)\)/gi)){
    if(!/^(?:#[A-Za-z_][\w:.-]*|'#[A-Za-z_][\w:.-]*'|"#[A-Za-z_][\w:.-]*")$/.test(match[1].trim()))throw TypeError('STARTUP_MOTION_LOCAL_SVG_REQUIRED');
  }
  return svg;
}

// Animate wrappers in the SVG's normalized coordinate space. Original groups
// can carry essential transforms; CSS must never replace those transforms.
function motionLayers(svg){
  const tokens=[...svg.matchAll(/<g\b(?:[^"'<>]|"[^"]*"|'[^']*')*>|<\/g\s*>/gi)],stack=[],edits=[],parts=[];
  for(const token of tokens){
    if(/^<\/g/i.test(token[0])){
      const entry=stack.pop();if(!entry)return {svg,structured:false};
      if(entry.part)edits.push({at:token.index+token[0].length,text:'</g>'});
    }else{
      const match=token[0].match(/\bdata-motion-part\s*=\s*(['"])([^'"]+)\1/),part=match?.[2];
      if(part){parts.push(part);edits.push({at:token.index,text:`<g data-eeas-motion-part="${PARTS.includes(part)?part:'unknown'}">`});}
      if(!/\/\s*>$/.test(token[0]))stack.push({part});
      else if(part)edits.push({at:token.index+token[0].length,text:'</g>'});
    }
  }
  if(stack.length||parts.length!==3||!PARTS.every(part=>parts.filter(value=>value===part).length===1))return {svg,structured:false};
  // At adjacent sibling boundaries insert the next opening layer first, so the
  // preceding closing layer ends up before it after reverse-position edits.
  let wrapped=svg;for(const edit of edits.sort((a,b)=>b.at-a.at||(a.text==='</g>'?1:-1)))wrapped=wrapped.slice(0,edit.at)+edit.text+wrapped.slice(edit.at);
  return {svg:wrapped,structured:true};
}

const css=`
.eeas-startup-motion{display:block;inline-size:clamp(64px,18vw,96px);aspect-ratio:1;margin:0 0 24px;opacity:1;pointer-events:none;user-select:none;transition:opacity 180ms cubic-bezier(.2,.7,.2,1),transform 180ms cubic-bezier(.2,.7,.2,1)}
.eeas-startup-motion>svg{display:block;inline-size:100%;block-size:100%;overflow:visible}
.eeas-startup-motion[data-opening="true"]{animation:eeasStartupMotionFrame 300ms cubic-bezier(.2,.7,.2,1) 1 backwards}
.eeas-startup-motion[data-opening="true"][data-structure="three-part"] g[data-eeas-motion-part]{transform-box:view-box;transform-origin:50% 50%}
.eeas-startup-motion[data-opening="true"][data-structure="three-part"] g[data-eeas-motion-part="outer-C"]{animation:eeasStartupMotionOuter 680ms cubic-bezier(.22,.68,.17,1) 1 backwards}
.eeas-startup-motion[data-opening="true"][data-structure="three-part"] g[data-eeas-motion-part="inner-fold"]{animation:eeasStartupMotionFold 680ms cubic-bezier(.22,.68,.17,1) 1 backwards}
.eeas-startup-motion[data-opening="true"][data-structure="three-part"] g[data-eeas-motion-part="lower-sweep"]{animation:eeasStartupMotionSweep 680ms cubic-bezier(.22,.68,.17,1) 1 backwards}
.eeas-startup-motion[data-opening="true"][data-structure="whole-mark"]>svg{transform-origin:50% 50%;animation:eeasStartupMotionFallback 420ms cubic-bezier(.2,.7,.2,1) 1 backwards}
.eeas-startup-motion[data-opening="false"]{transition:none}
@keyframes eeasStartupMotionFrame{from{opacity:.84;transform:translateY(3px) scale(.985)}to{opacity:1;transform:none}}
@keyframes eeasStartupMotionOuter{0%{opacity:.6;transform:translate(3.2px,-1.2px) scale(.94)}24%{opacity:.92;transform:translate(1.7px,-.5px) scale(.98)}74%{opacity:1;transform:translate(-.25px,.1px) scale(1.002)}100%{opacity:1;transform:none}}
@keyframes eeasStartupMotionFold{0%{opacity:.32;transform:translate(-3px,3.2px) scale(.91)}18%{opacity:.5;transform:translate(-2.2px,2.1px) scale(.95)}66%{opacity:1;transform:translate(.2px,-.15px) scale(1)}100%{opacity:1;transform:none}}
@keyframes eeasStartupMotionSweep{0%{opacity:.36;transform:translate(-1px,-4.2px) scale(.95)}23%{opacity:.58;transform:translate(-.7px,-2.6px) scale(.98)}78%{opacity:1;transform:translate(.1px,.2px) scale(1)}100%{opacity:1;transform:none}}
@keyframes eeasStartupMotionFallback{from{opacity:.5;transform:translateY(2px) scale(.98)}to{opacity:1;transform:none}}
@media(prefers-reduced-motion:reduce){.eeas-startup-motion,.eeas-startup-motion[data-opening="true"],.eeas-startup-motion[data-opening="true"][data-structure="whole-mark"]>svg,.eeas-startup-motion[data-opening="true"][data-structure="three-part"] g[data-eeas-motion-part]{animation:none;transition:none}}
`;

/** No delay promise is exposed: native startup continues independently. */
function startupMotion({svg,brand,opening=true}={}){
  if(typeof brand!=='string'||!brand.trim()||Array.from(brand).length>160||/[\u0000-\u001f\u007f]/u.test(brand)||typeof opening!=='boolean')throw TypeError('STARTUP_MOTION_INPUT_INVALID');
  const image=checkedSVG(svg),layers=motionLayers(image);
  return {markup:`<div class="eeas-startup-motion" data-opening="${opening}" data-structure="${layers.structured?'three-part':'whole-mark'}" role="img" aria-label="${escapeAttribute(brand)}">${layers.svg}</div>`,css};
}

module.exports={startupMotion,STARTUP_MOTION_DURATION_MS};
