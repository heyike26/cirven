'use strict';
const {ipcRenderer}=require('electron');
let uiLocale=document.body?.dataset?.uiLocale==='en'?'en':'zh-CN',uiMessages=null,staticText=[],staticAttributes=[];
function L(key,params={}){
  if(uiMessages===null){try{const raw=document.body?.dataset?.i18n||'{}';uiMessages=raw.length<=262144?JSON.parse(raw):{};}catch{uiMessages={};}}
  const text=uiMessages[key]?.[uiLocale]??String(key);
  return text.replace(/\{([A-Za-z][A-Za-z0-9_]*)\}/g,(match,name)=>Object.prototype.hasOwnProperty.call(params,name)?String(params[name]):match);
}
function rememberLabels(){
  L('');
  // Only declared product labels are localized. User/model text is never scanned.
  for(const element of document.querySelectorAll('[data-ui-key]')){
    const key=element.getAttribute('data-ui-key');if(uiMessages[key])staticText.push({element,key});
  }
  for(const attr of ['aria-label','aria-description','placeholder'])for(const element of document.querySelectorAll('[data-ui-'+attr+']')){
    const key=element.getAttribute('data-ui-'+attr);if(uiMessages[key])staticAttributes.push({element,attr,key});
  }
}
function localize(locale){
  uiLocale=locale==='en'?'en':'zh-CN';if(document.documentElement)document.documentElement.lang=uiLocale;
  for(const {element,key}of staticText)element.textContent=L(key);
  for(const {element,attr,key}of staticAttributes)element.setAttribute(attr,L(key));
}
const CHANNEL='eeas:assistant-orb:event',STATE_CHANNEL='eeas:assistant-orb:state';
const ACTIONS=['ask','show-agent','workbench','annotate','tasks'];
let stage,orb,notice,noticeText,press=null,hovered=false,ignoreClick=false,lastState;
const send=payload=>ipcRenderer.invoke(CHANNEL,payload).catch(()=>undefined);
function render(state){
  if(!state||!state.layout||!stage)return;
  lastState=state;localize(state.locale??uiLocale);
  if(typeof state.documentTitle==='string'&&document.title!==state.documentTitle)document.title=state.documentTitle;
  // Native shape holes pass pointer events to the page below. An explicit
  // outside/escape collapse must re-arm entry even if no DOM leave arrived.
  if(state.busy||!state.visible||!state.expanded&&!state.expansionPending)hovered=false;
  stage.dataset.expanded=String(state.expanded===true);stage.dataset.busy=String(state.busy===true);stage.dataset.error=String(!!state.error);
  const activity=state.taskActivity??{running:0,attention:0,unread:0,urgency:'none'};
  stage.dataset.unread=String(state.unread===true||activity.unread>0);stage.dataset.processing=String(state.processing===true);
  stage.dataset.taskUrgency=activity.urgency==='error'&&activity.unread>0?'error':activity.urgency==='waiting'&&(activity.attention>0||activity.unread>0)?'waiting':'none';
  stage.dataset.taskRunning=String(activity.running>0);
  const layout=state.layout;
  stage.querySelector('#arc-guide')?.setAttribute('d',layout.guide||'');
  stage.dataset.direction=layout.direction||'';
  orb.style.left=layout.orb.x+'px';orb.style.top=layout.orb.y+'px';
  orb.disabled=state.busy===true;orb.setAttribute('aria-expanded',String(state.expanded===true));
  orb.setAttribute('aria-busy',String(state.busy===true));
  const summary=[activity.running>0?activity.running+L(' 项运行中'):'',activity.attention>0?activity.attention+L(' 项待处理'):'',activity.unread>0?activity.unread+L(' 条未读'):''].filter(Boolean).join(' · ');
  orb.setAttribute('aria-description',state.error||(summary?summary+L(' · 悬停查看操作'):state.unread?L('查看新回复 · 拖动可移动'):state.processing?L('查看当前问题进展 · 拖动可移动'):L('询问当前页面 · 拖动可移动')));
  orb.setAttribute('aria-label',L('询问当前页面')+(summary?'；'+summary:''));
  if(notice&&noticeText){
    const shown=state.notice?.visible===true&&!!layout.notice;
    stage.dataset.notice=String(shown);notice.hidden=!shown;notice.disabled=state.busy===true;
    noticeText.textContent=shown?state.notice.text:'';
    notice.setAttribute('aria-label',shown?state.notice.text+L('，查看任务'):L('查看任务'));
    if(shown)for(const key of ['x','y','width','height'])notice.style[key==='x'?'left':key==='y'?'top':key]=layout.notice[key]+'px';
  }
  for(const item of layout.actions){
    if(!ACTIONS.includes(item.action))continue;
    const button=stage.querySelector('[data-action="'+item.action+'"]');
    button.style.left=(layout.center.x-22)+'px';button.style.top=(layout.center.y-22)+'px';
    button.style.setProperty('--dx',item.dx+'px');button.style.setProperty('--dy',item.dy+'px');
    button.disabled=state.busy===true;
  }
}
function hover(inside){if(press?.dragging||hovered===inside)return;hovered=inside;void send({kind:'hover',inside});}
ipcRenderer.on(STATE_CHANNEL,(_event,state)=>render(state));
window.addEventListener('DOMContentLoaded',()=>{
  rememberLabels();localize(uiLocale);
  stage=document.querySelector('.stage');orb=document.querySelector('#orb');
  notice=document.querySelector('#task-notice');noticeText=document.querySelector('#task-notice-text');
  if(!stage||!orb)return;
  const overNotice=event=>notice&&(event.target===notice||notice.contains?.(event.target));
  stage.addEventListener('pointerenter',()=>{if(!lastState?.notice?.visible)hover(true);});
  // A completed center action may leave the pointer on the same nonactivating
  // window. Re-arm on its next movement without requiring an artificial exit.
  stage.addEventListener('pointermove',event=>{if(!press&&!overNotice(event)&&!(lastState?.notice?.visible&&event.target===stage))hover(true);});
  stage.addEventListener('pointerleave',()=>{if(!press)hover(false);});
  window.addEventListener('keydown',event=>{if(event.key==='Escape'){hovered=false;void send({kind:'dismiss'});}});
  orb.addEventListener('pointerenter',()=>{if(!press)hover(true);});
  if(notice){
    notice.addEventListener('pointerdown',event=>event.preventDefault());
    notice.addEventListener('click',()=>{hovered=false;void send({kind:'action',action:'tasks'});});
  }
  for(const button of stage.querySelectorAll('[data-action]')){
    button.addEventListener('pointerdown',event=>event.preventDefault());
    button.addEventListener('click',()=>{const action=button.dataset.action;if(ACTIONS.includes(action)){hovered=false;void send({kind:'action',action});}});
  }
  orb.addEventListener('pointerdown',event=>{
    if(event.button!==0||lastState?.busy)return;
    event.preventDefault();ignoreClick=false;press={id:event.pointerId,x:event.screenX,y:event.screenY,dragging:false};
    orb.dataset.pressed='true';orb.setPointerCapture(event.pointerId);
  });
  orb.addEventListener('pointermove',event=>{
    if(!press||event.pointerId!==press.id)return;
    if(!press.dragging&&Math.hypot(event.screenX-press.x,event.screenY-press.y)>=6){
      press.dragging=true;hovered=false;void send({kind:'drag',phase:'start'});
    }
    if(press.dragging)void send({kind:'drag',phase:'move'});
  });
  const release=event=>{
    if(!press||event.pointerId!==press.id)return;
    const dragging=press.dragging;press=null;orb.dataset.pressed='false';ignoreClick=true;
    if(orb.hasPointerCapture(event.pointerId))orb.releasePointerCapture(event.pointerId);
    if(dragging)void send({kind:'drag',phase:'end'});
    else if(event.type==='pointerup'){hovered=false;void send({kind:'action',action:'ask'});}
    setTimeout(()=>{ignoreClick=false;},0);
  };
  orb.addEventListener('pointerup',release);orb.addEventListener('pointercancel',release);orb.addEventListener('lostpointercapture',release);
  orb.addEventListener('click',event=>{if(!ignoreClick&&event.detail===0){hovered=false;void send({kind:'action',action:'ask'});}});
  void send({kind:'state'}).then(render);
});
