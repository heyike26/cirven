'use strict';
const {ipcRenderer}=require('electron');
const {renderQuickReply,replyLinkTarget}=require('./quick-reply.mjs');
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
  for(const attr of ['aria-label','title','placeholder'])for(const element of document.querySelectorAll('[data-ui-'+attr+']')){
    const key=element.getAttribute('data-ui-'+attr);if(uiMessages[key])staticAttributes.push({element,attr,key});
  }
}
function localize(locale){
  uiLocale=locale==='en'?'en':'zh-CN';if(document.documentElement)document.documentElement.lang=uiLocale;
  for(const {element,key}of staticText)element.textContent=L(key);
  for(const {element,attr,key}of staticAttributes)element.setAttribute(attr,L(key));
}
const CHANNEL='eeas:quick-surface:event',STATE_CHANNEL='eeas:quick-surface:state';
const SVG='http://www.w3.org/2000/svg';
let state,role,comment,canvas,image,marksSvg,localMarks=[],history=[],future=[],tool='rect',drawing=null;
let composing=false,pendingEdits=0,editTail=Promise.resolve(),lastDraftId=null,lastImage=null,paint=null,showResultPreview=false;
let textMarks,textEditor,textInput,editor=null;
let quickMotion,motionSequence=-1;
let pendingInputFocus=null,lastLocalInputAt=0;
function focusRequestedInput(){
  const pending=pendingInputFocus;
  if(!pending)return;
  const age=Date.now()-pending.requestedAt;
  if(age<0||age>500||role!=='popover'||!state?.visible||state.presentation!=='ASK'||
      state.epoch!==pending.epoch||state.draft?.id!==pending.draftId||lastLocalInputAt>=pending.requestedAt){pendingInputFocus=null;return;}
  if(typeof document.hasFocus!=='function'||!document.hasFocus())return;
  pendingInputFocus=null;
  if(comment&&!comment.disabled&&!composing&&!handoffLock&&document.activeElement!==comment)comment.focus({preventScroll:true});
}
for(const name of ['pointerdown','keydown','input','paste','compositionstart'])window.addEventListener(name,()=>{
  lastLocalInputAt=Date.now();pendingInputFocus=null;
},true);
window.addEventListener('focus',focusRequestedInput);
window.addEventListener('blur',()=>{pendingInputFocus=null;});
let handoffLock=null,savedEditorView=null;
let canonicalStream=null,canonicalAnchors=[],canonicalReading=null,canonicalFrame=0,canonicalInteraction=0;
function syncCanonicalStream(enabled){
 const sheet=document.querySelector('.sheet');sheet?.classList?.toggle('canonical-chat',enabled);
 if(!enabled){
  if(canonicalStream){for(const {node,anchor}of canonicalAnchors){anchor.parentNode.insertBefore(node,anchor);anchor.remove();}canonicalStream.remove();canonicalStream=null;canonicalAnchors=[];canonicalInteraction++;}
  canonicalReading=null;return;
 }
 let created=false;
 if(!canonicalStream){
  canonicalStream=document.createElement('section');canonicalStream.className='canonical-stream';canonicalStream.tabIndex=0;canonicalStream.setAttribute('aria-label',L('对话'));
  const editor=document.querySelector('#editor');editor.before(canonicalStream);
  for(const selector of ['#question','#reply','#control-history']){const node=document.querySelector(selector),anchor=document.createComment('conversation-anchor');node.before(anchor);canonicalAnchors.push({node,anchor});canonicalStream.append(node);}
  for(const event of ['wheel','touchstart','pointerdown','keydown'])canonicalStream.addEventListener(event,()=>canonicalInteraction++,{passive:true});created=true;
 }
 const selection=getSelection(),reading=selection?.type==='Range'&&canonicalStream.contains(selection.anchorNode);
 canonicalReading={node:canonicalStream,sticky:!reading&&(created||canonicalStream.scrollHeight-canonicalStream.scrollTop-canonicalStream.clientHeight<32),serial:canonicalInteraction,epoch:state.epoch,id:state.draft?.id};
}
function restoreCanonicalReading(){
 const saved=canonicalReading;
 if(canonicalFrame){cancelAnimationFrame(canonicalFrame);canonicalFrame=0;}
 if(!saved?.sticky||typeof requestAnimationFrame!=='function')return;
 canonicalFrame=requestAnimationFrame(()=>{canonicalFrame=0;if(saved.node===canonicalStream&&saved.node.isConnected&&saved.serial===canonicalInteraction&&saved.epoch===state.epoch&&saved.id===state.draft?.id)saved.node.scrollTop=saved.node.scrollHeight;});
}
let controlRows=new Map(),controlOwner=null;
function renderControlHistory(element,controls,draft){
 if(!element)return;
 element.hidden=!controls.length;
 if(state.result?.canonical_original){const ids=JSON.stringify(controls.map(row=>[row.operation_id,!!row.text]));if(element.dataset.canonicalIds!==ids){element.dataset.canonicalIds=ids;element.open=!!controls.length;}}
 const list=element.querySelector('div'),owner=draft?.operationId??null;
 if(controlOwner!==owner){controlRows.clear();list.replaceChildren();controlOwner=owner;}
 const retained=new Set();let cursor=list.firstChild;
 for(const [index,control]of controls.entries()){
  const key=control.operation_id??control.id??String(index);retained.add(key);let row=controlRows.get(key);
  if(!row){const article=document.createElement('article'),text=document.createElement('p'),label=document.createElement('small'),reply=document.createElement('p');article.dataset.controlId=key;text.className='canonical-control-input';reply.className='canonical-control-reply';article.append(text,label,reply);row={article,text,label,reply};controlRows.set(key,row);}
  const label=L(control.role==='LOCAL_STATUS'?(control.local_status?.active_operation_id?'任务仍在运行':'任务当前空闲'):['ACCEPTED','OBSERVED','NEXT_TURN_ASSOCIATED'].includes(control.status)?'补充已送达':control.status==='COHORT_RETURNED'?'任务已返回':control.status==='RETURNED'?'补充已有回复':'核查原操作');
  if(row.text.textContent!==control.raw_text)row.text.textContent=control.raw_text;
  if(row.label.textContent!==label)row.label.textContent=label;
  if(row.reply.textContent!==(control.text??''))row.reply.textContent=control.text??'';
  row.reply.hidden=!control.text;
  if(row.article!==cursor)list.insertBefore(row.article,cursor);cursor=row.article.nextSibling;
 }
 for(const [key,row]of controlRows)if(!retained.has(key)){row.article.remove();controlRows.delete(key);}
}
function rememberEditorView(){
  if(handoffLock&&savedEditorView?.id===state?.draft?.id)return;
  if(comment&&(state?.draft?.phase==='EDITING'||state?.guidanceEditable))savedEditorView={id:state.draft.id,start:comment.selectionStart,end:comment.selectionEnd,direction:comment.selectionDirection,scroll:comment.scrollTop,focused:document.activeElement===comment};
}
function revealMotion(motion){
  const sheet=document.querySelector('.sheet');
  if(role!=='popover'||!sheet?.animate||!motion||!Number.isSafeInteger(motion.sequence)||motion.sequence===motionSequence)return;
  motionSequence=motion.sequence;
  const current=quickMotion?getComputedStyle(sheet):null;
  const before=current?{opacity:current.opacity,transform:current.transform}:null;
  quickMotion?.cancel();quickMotion=null;
  if(!['OPEN','CLOSE'].includes(motion.phase))return;
  const x=motion.origin?.x,y=motion.origin?.y;
  if(!Number.isFinite(x)||!Number.isFinite(y))return;
  sheet.style.transformOrigin=`${x}px ${y}px`;
  const closing=motion.phase==='CLOSE';
  const frames=closing?[before??{opacity:1,transform:'scale(1)'},{opacity:0,transform:'scale(.55)'}]:
    [before??{opacity:0,transform:'scale(.55)'},{opacity:1,transform:'scale(1)'}];
  const animation=sheet.animate(frames,{duration:closing?180:250,easing:'cubic-bezier(.16,1,.3,1)',fill:'both'});
  quickMotion=animation;
  animation.finished.then(()=>{if(quickMotion===animation&&!closing){quickMotion=null;animation.cancel();}}).catch(()=>{});
}
const clamp=n=>Math.max(0,Math.min(1,n));
const send=payload=>ipcRenderer.invoke(CHANNEL,payload);
function status(message){const element=state?.presentation==='ACTIVITY'?document.querySelector('#activity-error'):document.querySelector('#status')||document.querySelector('.overlay-status');if(element){element.textContent=message;element.hidden=false;}}
function node(tag,attributes){const result=document.createElementNS(SVG,tag);for(const [name,value] of Object.entries(attributes))result.setAttribute(name,String(value));return result;}
function paintMarks(){
  paint=null;if(!marksSvg)return;marksSvg.replaceChildren();
  textMarks?.replaceChildren();
  const marks=[...localMarks,...drawing?.mark?[drawing.mark]:[]];
  for(const mark of marks){
    if(mark.type==='text'){
      if(textMarks){
        const label=document.createElement('button');label.className='text-mark';label.textContent=mark.text;
        label.style.left=(mark.x*100)+'%';label.style.top=(mark.y*100)+'%';label.title=L('编辑或删除这条文字标记');
        label.style.transform=`translate(${mark.x>.6?'-100%':'0'},${mark.y>.7?'-100%':'0'})`;
        const index=localMarks.indexOf(mark);
        label.addEventListener('pointerdown',event=>event.stopPropagation());
        label.addEventListener('click',()=>{if(role==='overlay')void startText({x:mark.x,y:mark.y},index);});
        textMarks.append(label);
      }
      continue;
    }
    const style={fill:'none',stroke:'#c34838','stroke-width':2,'vector-effect':'non-scaling-stroke','stroke-linecap':'round','stroke-linejoin':'round'};
    if(mark.type==='rect')marksSvg.append(node('rect',{...style,x:mark.x,y:mark.y,width:mark.width,height:mark.height,rx:.002}));
    else if(mark.type==='ellipse')marksSvg.append(node('ellipse',{...style,cx:mark.x+mark.width/2,cy:mark.y+mark.height/2,rx:mark.width/2,ry:mark.height/2}));
    else if(mark.type==='freehand')marksSvg.append(node('polyline',{...style,points:mark.points.map(p=>p.x+','+p.y).join(' ')}));
    else if(mark.type==='arrow'){
      const [a,b]=mark.points,angle=Math.atan2(b.y-a.y,b.x-a.x),size=.018;
      marksSvg.append(node('polyline',{...style,points:`${a.x},${a.y} ${b.x},${b.y}`}),node('polyline',{...style,points:`${b.x-size*Math.cos(angle-.55)},${b.y-size*Math.sin(angle-.55)} ${b.x},${b.y} ${b.x-size*Math.cos(angle+.55)},${b.y-size*Math.sin(angle+.55)}`}));
    }
  }
}
function repaint(){if(paint===null)paint=window.requestAnimationFrame(paintMarks);}
function edit(){
  if(!state?.draft||state.draft.phase!=='EDITING'&&!state.guidanceEditable)return editTail;
  const payload=state.guidanceEditable?{kind:'review-guidance-edit',epoch:state.epoch,text:comment?.value??state.draft.guidanceText}:{kind:'edit',epoch:state.epoch,comment:comment?.value??state.draft.comment,marks:localMarks};
  pendingEdits++;
  editTail=editTail.catch(()=>{}).then(()=>send(payload)).then(result=>{
    if(result?.status==='ACTION_FAILED')status(L('修改暂未保存，内容仍在此处。'));
    return result;
  }).catch(()=>{status(L('修改暂未保存，内容仍在此处。'));return {status:'ACTION_FAILED'};}).finally(()=>{pendingEdits--;});
  return editTail;
}
function saveHistory(){history.push(localMarks);if(history.length>40)history.shift();future=[];}
function showTextEditor(){
  if(!textEditor||!textInput)return;
  textEditor.hidden=!editor;
  if(!editor)return;
  if(textInput.value!==editor.text)textInput.value=editor.text;
  if(role==='overlay'&&canvas){
    const bounds=canvas.getBoundingClientRect();
    textEditor.style.left=Math.max(8,Math.min(editor.x*bounds.width,bounds.width-328))+'px';
    textEditor.style.top=Math.max(8,Math.min(editor.y*bounds.height,bounds.height-156))+'px';
  }
  const count=document.querySelector('#text-count');if(count)count.textContent=[...editor.text].length+' / 2000';
  const remove=document.querySelector('[data-text-command="text-delete"]');if(remove)remove.hidden=editor.index===null;
}
function editText(){
  if(!editor||!state?.draft)return editTail;
  const payload={kind:'text-edit',epoch:state.epoch,editor:{...editor}};
  pendingEdits++;
  editTail=editTail.catch(()=>{}).then(()=>send(payload)).then(result=>{
    if(result?.status==='ACTION_FAILED')status(L('文字输入尚未确认，内容仍在编辑框中；请修改或取消后再收起。'));
    return result;
  }).catch(()=>{status(L('文字尚未交给本机保存，请保持编辑框并重试。'));return {status:'ACTION_FAILED'};})
    .finally(()=>{pendingEdits--;});
  return editTail;
}
async function textCommand(value){
  if(!editor)return true;
  if(composing){status(L('请先完成中文输入，再确认或切换。'));return false;}
  const prior=await editTail;if(prior?.status==='ACTION_FAILED'&&value==='text-confirm')return false;
  const result=await send({kind:'command',epoch:state.epoch,command:value});
  if(result?.status==='ACTION_FAILED')return false;
  // Host state is authoritative; the immediate result also covers a renderer
  // whose state event arrives after invoke resolves.
  if(result?.draft)render(result);
  return !editor;
}
async function startText(point,index=null){
  if(role!=='overlay'||!state?.draft||state.draft.phase!=='EDITING')return;
  if(editor&&!await textCommand('text-confirm'))return;
  const mark=index===null?null:localMarks[index];
  if(index!==null&&mark?.type!=='text')return;
  editor={index,x:mark?.x??point.x,y:mark?.y??point.y,text:mark?.text??''};
  showTextEditor();await editText();showTextEditor();textInput?.focus();
  if(mark)textInput?.select();
}
async function command(value){
  if(!state)return;
  if(handoffLock)return;
  if(composing){status(L('请先完成中文输入，再确认或切换。'));return;}
  if(state.presentation==='ACTIVITY'&&value==='activity-supervisor'){ensureSupervisor();document.querySelector('#supervisor-input')?.focus();return;}
  if(value==='pin'){
    try{await send({kind:'command',epoch:state.epoch,command:value});}
    catch{status(L('操作暂未完成，内容仍在此处。'));}
    return;
  }
  try{
    if(value.startsWith('text-')){await textCommand(value);return;}
    if(editor&&!await textCommand('text-confirm'))return;
  }catch{status(L('文字仍在此处，请确认或取消后再继续。'));return;}
  if(value==='undo'){
    if(history.length){future.push(localMarks);localMarks=history.pop();repaint();void edit();}return;
  }
  if(value==='redo'){
    if(future.length){history.push(localMarks);localMarks=future.pop();repaint();void edit();}return;
  }
  if(value==='clear'){if(localMarks.length){saveHistory();localMarks=[];repaint();void edit();}return;}
  const epoch=state.epoch;
  try{
    await editTail;
    if(state.epoch!==epoch)return;
    await send({kind:'command',epoch,command:value});
  }catch{status(L('操作暂未完成，内容仍在此处。'));}
}
function previewLayout(){
  if(role!=='popover'||!image?.naturalWidth||!image.naturalHeight)return;
  const preview=document.querySelector('#preview'),width=Math.min(preview.parentElement.clientWidth-2,125*image.naturalWidth/image.naturalHeight);
  preview.style.width=width+'px';preview.style.height=(width*image.naturalHeight/image.naturalWidth)+'px';
  preview.style.alignSelf='center';image.style.height='100%';
}
function renderActivity(){
  if(role!=='popover')return false;
  const panel=document.querySelector('#activity-panel'),ask=document.querySelector('#ask-panel'),active=state.presentation==='ACTIVITY';
  if(ask)ask.hidden=active;if(panel)panel.hidden=!active;
  if(!active)return false;
  const activity=state.activity,list=document.querySelector('#activity-list'),statusNode=document.querySelector('#activity-status'),error=document.querySelector('#activity-error');
  if(statusNode){statusNode.textContent=activity?.status!=='AVAILABLE'?L('尚未取得任务状态'):activity.observer_status==='PARTIAL'?L('部分任务状态待核查'):
    activity.observer_status==='OBSERVATION_UNAVAILABLE'?L('最近一次观察不可用'):activity.observer_status==='CLOSED'?L('监督已关闭'):activity.enabled===false?L('监督已关闭，保留最后观察'):
    L('{count} 项任务',{count:activity.full_count})+(activity.has_more?L(' · 先列出 20 项'):'');}
  if(error){error.textContent=state.error||'';error.hidden=!!state.supervisor?.supported||!error.textContent;}
  if(list){
    list.replaceChildren();
    for(const [group,title]of [['attention',L('待处理')],['running',L('运行')],['recent',L('最近结束')]]){
      const items=(activity?.items??[]).filter(item=>item.group===group);if(!items.length)continue;
      const heading=document.createElement('h3');heading.textContent=title+' · '+activity.counts[group];list.append(heading);
      for(const item of items){
        const button=document.createElement('button'),label=document.createElement('strong'),detail=document.createElement('small');
        button.className='activity-task';button.type='button';label.textContent=item.label;
        const observed=new Intl.DateTimeFormat(uiLocale,{hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false}).format(new Date(item.observed_at));
        detail.textContent=[item.status,observed,item.actual_error_code].filter(Boolean).join(' · ');button.append(label,detail);
        button.disabled=!activity.can_open||activity.busy;
        const epoch=state.epoch,revision=activity.revision,token=item.token;
        button.addEventListener('click',()=>void send({kind:'activity-open',epoch,activity_revision:revision,token}).catch(()=>status(L('任务暂未打开，请重新读取列表。'))));
        list.append(button);
      }
    }
    if(!(activity?.items?.length)){
      const empty=document.createElement('p');empty.className='activity-empty';empty.textContent=activity?.status==='AVAILABLE'?L('还没有可观察的任务'):L('可稍后读取现有状态');list.append(empty);
    }
  }
  for(const button of document.querySelectorAll('[data-command]')){
    if(button.dataset.command==='activity-refresh')button.disabled=!activity?.can_refresh||activity.busy;
    if(button.dataset.command==='activity-all')button.disabled=!activity?.can_expand||activity.busy;
    if(button.dataset.command==='activity-supervisor')button.disabled=!activity?.can_ask||activity.busy;
  }
  if(state.supervisor?.supported){ensureSupervisor();renderSupervisor();}
  return true;
}
let supervisorClient=null,supervisorView=null,supervisorComposing=false,supervisorSending=false,supervisorNotice='',supervisorInitial=false,supervisorHistorySignature='',supervisorFollow=true;
let supervisorSelectionRevision=-1,supervisorSelectionView=null;
let supervisorArchiveRows=null,supervisorArchiveRevision=0;
const supervisorListeners=new Set(),supervisorRows=new Map();
let centerText,answerSupervisorStatusQuery;
function supervisorText(key){if(!centerText)({centerText}=require('../agent-shell/src/supervisor-command-center-copy.ts'));return centerText(uiLocale,key);}
function supervisorProjection(){
  const activity=state?.activity,items=(activity?.items??[]).map(item=>({id:item.token,label:item.label,status:item.status,tone:item.group==='running'?'running':item.group==='attention'?'attention':'quiet',priority:0,task:{...item}}));
  const cached=activity?.status!=='AVAILABLE'||activity.enabled!==true||!['READY','PARTIAL'].includes(activity.observer_status);
  return {tasks:items,items:[],counts:activity?.counts??{running:0,attention:0,recent:0},observedAt:null,cached};
}
function supervisorLocalAnswer(question){
  if(!answerSupervisorStatusQuery)({answerSupervisorStatusQuery}=require('../agent-shell/src/supervisor-command-center.ts'));
  const answer=answerSupervisorStatusQuery(question,supervisorProjection(),uiLocale);if(!answer)return;
  if(state?.activity?.status!=='AVAILABLE')answer.text=supervisorText('unavailable');
  else if(state.activity.has_more)answer.text+='\n'+L(' · 先列出 20 项').trim();
  return answer;
}
async function supervisorInvoke(kind,body){
  if(role!=='popover'||state?.presentation!=='ACTIVITY'||!state.visible)throw Object.assign(new Error('SUPERVISOR_CLIENT_NOT_READY'),{code:'SUPERVISOR_CLIENT_NOT_READY'});
  const payload={kind,epoch:state.epoch,...kind==='supervisor-inspect'?{operationId:body}:body!==undefined?{body}:{}};
  const result=await send(payload);
  if(result&&Object.keys(result).length===1&&typeof result.supervisor_error==='string'&&/^SUPERVISOR_[A-Z_]+$/.test(result.supervisor_error))throw Object.assign(new Error(result.supervisor_error),{code:result.supervisor_error});
  return result;
}
function ensureSupervisor(){
  if(role!=='popover'||!state?.supervisor?.supported)return null;
  if(!supervisorClient){
    const {SupervisorConversationClient}=require('../agent-shell/src/supervisor-conversation-client.ts');
    supervisorClient=new SupervisorConversationClient({
      getSnapshot:()=>state?.presentation!=='ACTIVITY'||!state?.visible?Promise.resolve(state?.supervisor?.snapshot):supervisorInvoke('supervisor-get'),
      saveDraft:body=>supervisorInvoke('supervisor-draft-save',body),ask:body=>supervisorInvoke('supervisor-ask',body),inspect:id=>supervisorInvoke('supervisor-inspect',id),persistLocal:body=>supervisorInvoke('supervisor-local-save',body),
      ...(state.supervisor.history_supported?{readHistoryPage:body=>supervisorInvoke('supervisor-history-read',body)}:{}),
      ...(state.supervisor.operation_read_supported?{readOperation:body=>supervisorInvoke('supervisor-operation-read',body)}:{}),
      onChange:listener=>{supervisorListeners.add(listener);return()=>supervisorListeners.delete(listener);}
    },view=>{supervisorView=view;renderSupervisor();});
    void supervisorClient.start().catch(()=>{supervisorNotice=supervisorText('readUnavailable');renderSupervisor();});
  }
  return supervisorClient;
}
function supervisorAdmission(question){return supervisorClient&&state?.supervisor?.providerProfileRef?supervisorClient.askAdmission(question,state.supervisor.providerProfileRef):{allowed:true};}
function supervisorFollowupRef(){
  const selected=state?.supervisor?.selection;
  return selected&&supervisorSelectionView?.key===selected.operation_id+':'+selected.request_sha256?{operation_id:selected.operation_id,request_sha256:selected.request_sha256}:undefined;
}
function supervisorDisplayMessages(snapshot){
  const live=snapshot?.messages??[],history=typeof supervisorView!=='undefined'?supervisorView?.history:undefined,messages=[...new Map([...(history?.messages??[]),...(history?.windowed?[]:live)].map(row=>[row.id,row])).values()],selected=state?.supervisor?.selection,revision=state?.supervisor?.selection_revision;
  const known=selected&&snapshot?.operations.find(row=>row.operation_id===selected.operation_id),valid=selected&&(!known||known.request_sha256===selected.request_sha256);
  if(!valid)supervisorSelectionView=null;
  else if(Number.isSafeInteger(revision)&&revision!==supervisorSelectionRevision){
    supervisorSelectionRevision=revision;const content=document.querySelector('.activity-content');
    supervisorSelectionView={key:selected.operation_id+':'+selected.request_sha256,baseline:new Set(messages.map(message=>message.id)),operations:new Set(snapshot.operations.map(operation=>operation.operation_id)),openedAt:new Date().toISOString(),scroll:content?.scrollTop??0,follow:supervisorFollow};
    supervisorFollow=true;const tasks=document.querySelector('#supervisor-tasks');if(tasks)tasks.open=false;
  }
  let bar=document.querySelector('#supervisor-selection');
  if(!bar){
    bar=document.createElement('div');bar.id='supervisor-selection';bar.className='supervisor-actions';
    const label=document.createElement('small'),recent=document.createElement('button');label.style.cssText='margin-right:auto;font-size:11px;line-height:16px;color:var(--muted)';recent.id='supervisor-recent';recent.type='button';
    recent.addEventListener('click',()=>{const previous=supervisorSelectionView;if(!previous)return;supervisorSelectionView=null;supervisorFollow=false;renderSupervisor();const content=document.querySelector('.activity-content');if(content)content.scrollTop=previous.scroll;supervisorFollow=previous.follow;});
    bar.append(label,recent);document.querySelector('.activity-content')?.before(bar);
  }
  const active=valid&&supervisorSelectionView?.key===selected.operation_id+':'+selected.request_sha256;
  bar.hidden=!active;bar.firstElementChild.textContent=supervisorText('originalQuestion');bar.lastElementChild.textContent=supervisorText('recentDiscussion');
  if(!active)return {messages,retained:messages};
  const operation=snapshot.operations.find(row=>row.operation_id===selected.operation_id),answer=messages.findLast(message=>message.role==='assistant'&&message.operation_id===selected.operation_id);
  const prefix='selected:'+supervisorSelectionView.key;
  const original=[{id:prefix+':question',role:'user',text:selected.question,operation_id:selected.operation_id},{id:prefix+':answer',role:'assistant',text:answer?.text??selected.answer_text,operation_id:selected.operation_id,status:answer?.status??operation?.status??selected.status,selected:true}];
  const tail=messages.filter(message=>!supervisorSelectionView.baseline.has(message.id)&&(message.operation_id?!supervisorSelectionView.operations.has(message.operation_id):message.created_at>=supervisorSelectionView.openedAt));
  return {messages:[...original,...tail],retained:[...messages,...original]};
}
function supervisorDraftError(view){
  if(!view?.error||view.error.operation_id)return '';
  const {supervisorDraftFailureKind}=require('../agent-shell/src/supervisor-conversation-client.ts');
  const kind=supervisorDraftFailureKind(view.error.code);
  return supervisorText(view.draftConflict?'draftConflict':view.error.code==='SUPERVISOR_STORAGE_FULL'?'storageFull':kind==='UNKNOWN'?'draftSaveUnknown':kind==='FAILED'||view.draftBlocked?'draftUnavailable':'readUnavailable');
}
async function loadSupervisorEarlier(){
  if(supervisorView?.history?.loading||!supervisorClient)return;
  supervisorFollow=false;const content=document.querySelector('.activity-content'),epoch=state.epoch,before=content?.scrollTop??0,bounds=content?.getBoundingClientRect();
  const anchor=bounds?[...document.querySelectorAll('#supervisor-history article[data-message-id]')].find(row=>row.getBoundingClientRect().bottom>bounds.top+32):undefined,id=anchor?.dataset.messageId,top=anchor?.getBoundingClientRect().top;
  await supervisorClient.readEarlier();
  if(!content||state.epoch!==epoch||state.presentation!=='ACTIVITY'||!state.visible||content.scrollTop!==before||!bounds||content.getBoundingClientRect().width!==bounds.width)return;
  const current=id?[...document.querySelectorAll('#supervisor-history article[data-message-id]')].find(row=>row.dataset.messageId===id):undefined;
  if(current&&top!==undefined)content.scrollTop+=current.getBoundingClientRect().top-top;
}
function renderSupervisorHistoryTools(history){
  let tools=document.querySelector('#supervisor-history-tools');const value=supervisorView?.history;
  if(!tools&&state.supervisor.history_supported){
    tools=document.createElement('div');tools.id='supervisor-history-tools';tools.className='supervisor-history-tools';
    const earlier=document.createElement('button'),recent=document.createElement('button'),hint=document.createElement('p');earlier.type=recent.type='button';earlier.id='supervisor-earlier';recent.id='supervisor-history-recent';hint.id='supervisor-history-hint';hint.className='supervisor-history-hint';hint.setAttribute('role','status');
    earlier.addEventListener('click',()=>void loadSupervisorEarlier());recent.addEventListener('click',()=>{supervisorFollow=true;supervisorClient?.resetHistory();renderSupervisor();});tools.append(earlier,recent,hint);history.before(tools);
  }
  if(!tools)return;
  tools.hidden=!state.supervisor.history_supported||!!supervisorSelectionView||!(value?.hasMore||value?.loading||value?.changed||value?.error||value?.messages.length);
  const earlier=document.querySelector('#supervisor-earlier'),recent=document.querySelector('#supervisor-history-recent'),hint=document.querySelector('#supervisor-history-hint');
  earlier.hidden=!(value?.hasMore||value?.loading||value?.changed||value?.error);earlier.disabled=!!value?.loading;earlier.textContent=supervisorText(value?.loading?'historyLoading':value?.changed?'historyReadAgain':value?.error?'historyRetry':'historyEarlier');
  recent.hidden=!value?.messages.length;recent.textContent=supervisorText('recentDiscussion');hint.hidden=!(value?.messages.length||value?.error);hint.textContent=value?.error?supervisorText(value.error==='CHANGED'?'historyChanged':'historyUnavailable'):value?.changed?supervisorText('historyChanged'):value?.windowed?supervisorText('historyWindow'):' ';
}
function renderSupervisor(){
  const supported=!!state?.supervisor?.supported,form=document.querySelector('#supervisor-compose'),input=document.querySelector('#supervisor-input');
  if(!form||!input)return;
  form.hidden=!supported;
  const heading=document.querySelector('#activity-heading');if(heading&&supported)heading.textContent=supervisorText('label');
  const focusButton=document.querySelector('[data-command="activity-supervisor"]');if(focusButton)focusButton.hidden=supported;
  if(!supported)return;
  input.setAttribute('aria-label',supervisorText('ask'));input.placeholder=supervisorText('ask');
  const view=supervisorView;
  const local=supervisorLocalAnswer(view?.draft??''),admission=local?{allowed:true}:supervisorAdmission(view?.draft??'');
  if(view&&input.value!==view.draft&&!supervisorComposing)input.value=view.draft;
  input.disabled=!view?.snapshot||!!handoffLock;
  const conflict=document.querySelector('#supervisor-conflict');conflict.hidden=!view?.draftConflict;
  document.querySelector('#supervisor-conflict-label').textContent=supervisorText('draftConflict');
  const recovery=supervisorClient?.draftSaveRecovery(),recoveryPanel=document.querySelector('#supervisor-save-recovery');recoveryPanel.hidden=!recovery;
  for(const [id,key,kind]of [['supervisor-check-save','checkDraftSave','CHECK_REQUIRED'],['supervisor-retry-save','retryDraftSave','RETRY_ALLOWED']]){const button=document.querySelector('#'+id);button.textContent=supervisorText(key);button.hidden=recovery!==kind;button.disabled=!!handoffLock||!!view?.saving;}
  for(const [id,key]of [['supervisor-keep','keepDraft'],['supervisor-remote','readDraft'],['supervisor-local','local'],['supervisor-configure','configure'],['supervisor-send',supervisorSending&&!local?'pending':'send']]){const element=document.querySelector('#'+id);element.textContent=supervisorText(key);element.disabled=!!handoffLock||!!view?.saving&&['supervisor-keep','supervisor-remote'].includes(id);}
  document.querySelector('#supervisor-local').disabled=!view?.snapshot||!!handoffLock||!!view?.draftBlocked;
  document.querySelector('#supervisor-send').disabled=!view?.snapshot||!view.draft.trim()||!!view.draftBlocked||!!handoffLock||!local&&(supervisorSending||!admission.allowed);
  const inspect=document.querySelector('#supervisor-inspect'),inspectId=!admission.allowed?admission.operation_id:view?.error?.code==='SUPERVISOR_CLIENT_ASK_NOT_DISPATCHED'?undefined:view?.error?.operation_id;inspect.dataset.operationId=inspectId??'';inspect.hidden=!inspectId;inspect.textContent=supervisorText('inspect');inspect.disabled=!!handoffLock;
  document.querySelector('#supervisor-configure').hidden=!!state.supervisor.providerProfileRef||!state.supervisor.can_configure;
  const message=supervisorNotice||(!admission.allowed?supervisorText(admission.code==='SUPERVISOR_OPERATION_PENDING'?'pending':'unknown'):view?.error?(view.error.code==='SUPERVISOR_CLIENT_ASK_NOT_DISPATCHED'?supervisorText('askStorageFull'):view.error.operation_id?supervisorText('unknown'):supervisorDraftError(view)):state.error||'');
  const error=document.querySelector('#supervisor-error');error.textContent=message;error.hidden=!message;
  if(supervisorArchiveRows!==view?.history?.messages){supervisorArchiveRows=view?.history?.messages;supervisorArchiveRevision++;}
  const history=document.querySelector('#supervisor-history'),display=supervisorDisplayMessages(view?.snapshot),messages=display.messages,signature=JSON.stringify([uiLocale,supervisorArchiveRevision,view?.snapshot?.messages,state.supervisor.selection,inspectId]);history.hidden=!messages.length;renderSupervisorHistoryTools(history);
  if(!supervisorInitial&&view?.snapshot){supervisorInitial=true;const tasks=document.querySelector('#supervisor-tasks');if(tasks)tasks.open=!messages.length;}
  document.querySelector('#supervisor-tasks-label').textContent=supervisorText('tasks');
  if(signature!==supervisorHistorySignature){
    supervisorHistorySignature=signature;const retained=new Set(display.retained.map(message=>message.id)),visible=new Set(messages.map(message=>message.id));
    for(const [id,row]of supervisorRows)row.element.hidden=!visible.has(id);
    for(const message of messages){
      let row=supervisorRows.get(message.id);
      if(!row){const element=document.createElement('article'),label=document.createElement('small'),body=document.createElement('div'),statusNode=document.createElement('p'),inspect=document.createElement('button');element.className='supervisor-message';element.dataset.messageId=message.id;body.className='supervisor-message-body';statusNode.className='supervisor-message-state';inspect.type='button';inspect.addEventListener('click',()=>{supervisorFollow=true;const id=row.operationId;if(id)void supervisorClient.inspect(id).catch(()=>{supervisorNotice=supervisorText('unknown');renderSupervisor();});});element.append(label,body,statusNode,inspect);row={element,label,body,statusNode,inspect,text:null,operationId:null};supervisorRows.set(message.id,row);}
      row.label.textContent=message.role==='assistant'?'Cirven':uiLocale==='en'?'You':'你';
      if(row.text!==message.text){row.text=message.text;if(message.role==='assistant')renderQuickReply(row.body,{text:message.text,identity:message.id,markdown:true,locale:uiLocale});else row.body.textContent=message.text;}
      row.element.hidden=false;row.operationId=message.operation_id;const unresolved=message.status==='UNKNOWN'||['PROCESSING','REQUESTING','RUNNING'].includes(message.status);row.statusNode.hidden=!unresolved&&!message.selected;row.statusNode.textContent=supervisorText(message.status==='UNKNOWN'?'unknown':unresolved?'pending':message.status==='FAILED'?'EXECUTION_ERROR':message.status==='CANCELLED'?'CANCELLED':message.status==='REJECTED'?'rejected':'returned');row.inspect.hidden=message.role!=='assistant'||!unresolved||!message.operation_id||message.operation_id===inspectId;row.inspect.textContent=supervisorText('inspect');history.append(row.element);
    }
    for(const [id,row]of supervisorRows)if(!retained.has(id)){row.element.remove();supervisorRows.delete(id);}
    if(supervisorFollow&&state.presentation==='ACTIVITY'&&state.visible)requestAnimationFrame(()=>{if(supervisorFollow&&state.presentation==='ACTIVITY'&&state.visible){const content=document.querySelector('.activity-content');if(content)content.scrollTop=content.scrollHeight;}});
  }
}
async function askSupervisor(localOnly=false){
  if(supervisorComposing||composing||handoffLock)return;
  const client=ensureSupervisor();if(!client)return;
  if(!supervisorView?.snapshot){await client.start();if(!supervisorView?.snapshot)return;}
  const question=localOnly?(uiLocale==='en'?'Task status':'任务状态'):supervisorView.draft;
  if(supervisorView.draftBlocked||!question.trim())return;
  const local=supervisorLocalAnswer(question),admission=local?{allowed:true}:supervisorAdmission(question);
  if(!admission.allowed){supervisorNotice=supervisorText(admission.code==='SUPERVISOR_OPERATION_PENDING'?'pending':'unknown');renderSupervisor();return;}
  if(supervisorSending&&!local)return;
  client.resetHistory();
  supervisorNotice='';supervisorFollow=true;const tasks=document.querySelector('#supervisor-tasks');if(tasks)tasks.open=false;
  try{
    if(local){await client.persistLocal({question,answer:{text:local.text}});}
    else if(!state.supervisor.providerProfileRef){supervisorNotice=supervisorText('retained');}
    else{const followupRef=supervisorFollowupRef();supervisorSending=true;renderSupervisor();const result=await client.ask({operationId:crypto.randomUUID(),text:question,providerProfileRef:{...state.supervisor.providerProfileRef},...followupRef?{followupRef}:{}});if(result.status==='REJECTED'&&result.error==='SUPERVISOR_STORAGE_FULL')supervisorNotice=supervisorText('askStorageFull');}
  }catch{supervisorNotice=supervisorDraftError(supervisorView)||supervisorText('unknown');}
  finally{if(!local)supervisorSending=false;renderSupervisor();}
}
let reviewOptionsKey='',reviewPendingScope='',reviewSelectionPending=null,reviewSelectionSequence=0;const reviewAnswers=new Map(),reviewCustom=new Map(),reviewPendingRows=new Map();
function filterCapabilities(){const query=document.querySelector('#capability-search')?.value.trim().toLocaleLowerCase()??'';for(const row of document.querySelectorAll('#capability-tools label[data-search]'))row.hidden=!!query&&!row.dataset.search.includes(query);for(const group of document.querySelectorAll('#capability-tools fieldset'))group.hidden=![...group.querySelectorAll('label')].some(row=>!row.hidden);}
function selectReview(input){
 const pending={epoch:state.epoch,sequence:++reviewSelectionSequence},payload={...input,epoch:state.epoch};reviewSelectionPending=pending;
 const submit=document.querySelector('[data-command="send"]');if(submit)submit.disabled=true;
 void send(payload).catch(()=>{if(reviewSelectionPending===pending&&state.epoch===pending.epoch)status(L('所选技能或工具已变化，请重新选择'));}).finally(()=>{if(reviewSelectionPending!==pending||state.epoch!==pending.epoch)return;reviewSelectionPending=null;render(state);});
}
function renderReview(){
 const draft=state.draft,choice=document.querySelector('#review-choice');if(!choice)return;
 const editing=draft?.phase==='EDITING',catalog=state.reviewCatalog,selection=draft?.nativeReview?.selection,review=state.result?.review;
 choice.hidden=!editing;document.querySelector('#review-picker').hidden=!state.reviewOpen;
 const capability=catalog?.schema==='eeas.native-capability-task-capabilities.v2',usingLegacy=!!selection&&!Object.hasOwn(selection,'mcp_tool_selection'),legacy=capability?catalog.legacy_review_catalog:catalog;
 const skills=usingLegacy?legacy?.skills??[]:catalog?.skills??[],tools=legacy?.tools??[];
 const pickerKey=JSON.stringify([state.epoch,skills,tools,catalog?.mcp_catalog,uiLocale]);
 if(catalog&&reviewOptionsKey!==pickerKey){reviewOptionsKey=pickerKey;
  for(const [id,rows]of [['review-skill',skills],['review-tool',tools]]){const select=document.querySelector('#'+id);select.replaceChildren();const none=document.createElement('option');none.value='';none.textContent=L('不选择');select.append(none);for(const row of rows){const option=document.createElement('option');option.value=row.id;option.disabled=!row.selectable;option.textContent=(row.selection?.kind==='BUILTIN_READONLY'?L(row.title):row.title)+(row.selectable?'':' · '+L('暂不可用'));select.append(option);}}
  const list=document.querySelector('#capability-tools');list?.replaceChildren();
  if(capability&&list){
   for(const tool of tools.filter(row=>row.selection?.kind==='BUILTIN_READONLY')){const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type='checkbox';input.dataset.builtinId=tool.id;input.disabled=!tool.selectable;text.textContent=L(tool.title);label.dataset.search=text.textContent.toLocaleLowerCase();input.addEventListener('change',()=>selectReview({kind:'review-selection',skillId:state.draft?.nativeReview?.version===1?document.querySelector('#review-skill').value:'',toolId:input.checked?tool.id:''}));label.append(input,text);list.append(label);}
   for(const server of catalog.mcp_catalog.mcp_servers.filter(row=>row.enabled&&row.availability==='AVAILABLE')){
    const fieldset=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=server.id;fieldset.append(legend);
    for(const tool of server.tools){const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type='checkbox';input.dataset.capabilityTool=tool.id;input.disabled=!tool.selectable;text.textContent=tool.name;label.dataset.search=(server.id+' '+tool.name).toLocaleLowerCase();input.addEventListener('change',()=>{const selected=state.draft?.nativeReview?.selection,ids=Array.isArray(selected?.mcp_tool_selection)?selected.mcp_tool_selection.map(item=>item.tool_id):[];selectReview({kind:'capability-selection',skillId:Array.isArray(selected?.mcp_tool_selection)?document.querySelector('#review-skill').value:'',toolIds:input.checked?[...ids.filter(id=>id!==tool.id),tool.id]:ids.filter(id=>id!==tool.id)});});label.append(input,text);fieldset.append(label);}
    list.append(fieldset);
   }
  }
 }
 if(catalog){document.querySelector('#review-skill').value=skills.find(row=>JSON.stringify(row.config_ref)===JSON.stringify(selection?.skill_ref))?.id??'';document.querySelector('#review-tool').value=tools.find(row=>JSON.stringify(row.selection)===JSON.stringify(selection?.tool_selection))?.id??'';}
 document.querySelector('#review-legacy-tool').hidden=!!capability;document.querySelector('#capability-picker').hidden=!capability;
 const clear=document.querySelector('[data-command="review-clear"]');clear.hidden=!selection;
 document.querySelector('#review-consent').hidden=!state.reviewProcessRequired;document.querySelector('#review-remote-consent').hidden=!state.reviewRemoteRequired;
 const busy=state.reviewBusy||reviewSelectionPending?.epoch===state.epoch;
 for(const id of ['review-skill','review-tool'])document.querySelector('#'+id).disabled=!editing||busy;
 for(const [id,field]of [['review-process','process_start_consent'],['review-remote','remote_mcp_connect_consent']]){const check=document.querySelector('#'+id);check.checked=!!draft?.nativeReview?.[field];check.disabled=!editing||busy;}
 const ids=selection?.mcp_tool_selection?.map(item=>item.tool_id)??[];
 for(const input of document.querySelectorAll('[data-capability-tool]')){input.checked=ids.includes(input.dataset.capabilityTool);input.disabled=!editing||busy||!input.checked&&ids.length>=64;}
 for(const input of document.querySelectorAll('[data-builtin-id]')){input.checked=tools.find(row=>row.id===input.dataset.builtinId)?.selection?.tool_ref?.id===selection?.tool_selection?.tool_ref?.id;input.disabled=!editing||busy||!tools.find(row=>row.id===input.dataset.builtinId)?.selectable;}
 filterCapabilities();
 const note=document.querySelector('#review-note');note.textContent=tools.some(row=>!row.selectable&&row.reason==='REVIEW_PLAN_BASIS_REQUIRED')?L('请先引用要检查的任务计划'):selection&&!state.reviewOpen?state.reviewSummary||L('已选择'):'';note.hidden=!note.textContent;
 const guide=document.querySelector('#review-guidance');guide.hidden=true;const field=document.querySelector('#guidance');if(document.activeElement!==field)field.value=draft?.guidanceText??'';
 const pending=document.querySelector('#review-pending'),items=draft?.readonly?[]:[...review?.pending_permissions??[],...review?.pending_questions??[]];pending.hidden=!items.length;
 const scope=JSON.stringify([draft?.id,draft?.operationId,review?.review_ref??null,!!draft?.readonly]);
 if(scope!==reviewPendingScope){reviewPendingScope=scope;pending.replaceChildren();reviewPendingRows.clear();reviewAnswers.clear();reviewCustom.clear();}
 const retained=new Set();let cursor=pending.firstChild;
 for(const item of items){
  const requestId=item.request_ref.id,answerKey=JSON.stringify([draft?.operationId,item.kind,requestId,item.request_ref.generation,item.request_ref.sha256]),signature=JSON.stringify([item.native_request,item.capability_call,item.allowed_choices]);retained.add(answerKey);
  let row=reviewPendingRows.get(answerKey);
  if(!row||row.signature!==signature){
   const article=document.createElement('article'),heading=document.createElement('strong');article.dataset.nativeRequestKey=answerKey;article.append(heading);
   const answers=reviewAnswers.get(answerKey)??[];reviewAnswers.set(answerKey,answers);const customs=[];
   if(item.kind==='PERMISSION'){
    const text=document.createElement('p');text.textContent=item.capability_call?.tool_id??String(item.native_request?.permission??'');article.append(text);
    if(item.capability_call){if(item.capability_call.coverage==='UNAVAILABLE'){const unavailable=document.createElement('p');customs.push({element:unavailable,key:'未取得本次调用参数'});article.append(unavailable);}else{const pre=document.createElement('pre');pre.className='review-capability-input';pre.textContent=item.capability_call.input_json??'';article.append(pre);for(const [flag,key]of [['input_truncated','参数已截断'],['input_redacted','部分值已隐藏']])if(item.capability_call[flag]){const note=document.createElement('small');customs.push({element:note,key});article.append(note);}}}
    const targets=document.createElement('div');targets.className='review-permission-targets';for(const pattern of Array.isArray(item.native_request?.patterns)?item.native_request.patterns:[]){const value=document.createElement('code');value.textContent=String(pattern);targets.append(value);}article.append(targets);
    const details=document.createElement('details'),summary=document.createElement('summary'),pre=document.createElement('pre');details.className='review-permission-details';pre.textContent=JSON.stringify(item.native_request,null,2);details.append(summary,pre);article.append(details);customs.push({element:summary,key:'原请求字段与范围'});
   }
   if(item.kind==='QUESTION')for(const [index,q]of (item.native_request?.questions??[]).entries()){
    const fieldset=document.createElement('fieldset'),legend=document.createElement('legend');legend.textContent=q.question;fieldset.append(legend);
    for(const option of q.options??[]){const label=document.createElement('label'),input=document.createElement('input'),text=document.createElement('span');input.type=q.multiple?'checkbox':'radio';input.name=answerKey+':'+index;input.checked=answers[index]?.includes(option.label)??false;text.textContent=option.label;input.addEventListener('change',()=>{const old=answers[index]??[];answers[index]=q.multiple?(input.checked?[...old,option.label]:old.filter(v=>v!==option.label)):[option.label];});label.append(input,text);fieldset.append(label);}
    if(q.custom!==false){const input=document.createElement('input');input.maxLength=2000;input.value=reviewCustom.get(answerKey)?.[index]??'';input.addEventListener('input',()=>{const rows=reviewCustom.get(answerKey)??[];rows[index]=input.value;reviewCustom.set(answerKey,rows);});customs.push({element:input,key:'补充回答',attribute:'aria-label'});fieldset.append(input);}article.append(fieldset);
   }
   const buttons=[],buttonGroup=document.createElement('div');buttonGroup.className='review-pending-buttons';article.append(buttonGroup);
   for(const action of [item.kind==='PERMISSION'?'once':'reply','reject']){const button=document.createElement('button');button.addEventListener('click',()=>void send({kind:'review-control',epoch:state.epoch,action,requestId,answers:action==='reply'?(item.native_request?.questions??[]).map((q,index)=>[...answers[index]??[],...(q.custom!==false&&reviewCustom.get(answerKey)?.[index]?.trim()?[reviewCustom.get(answerKey)[index].trim()]:[])]):null}).catch(()=>status(L('结果尚未确认，请核查原操作'))));buttons.push({element:button,action,key:action==='once'?'允许一次':action==='reply'?'保存答复':'拒绝'});buttonGroup.append(button);}
   if(row){if(cursor===row.article)cursor=row.article.nextSibling;row.article.remove();}row={article,heading,buttons,customs,signature};reviewPendingRows.set(answerKey,row);
  }
  row.heading.textContent=L(item.kind==='PERMISSION'?'工具调用请求':'待回答');
  for(const {element,key,attribute}of row.customs){if(attribute)element.setAttribute(attribute,L(key));else element.textContent=L(key);}
  for(const {element,key,action}of row.buttons){element.textContent=L(key);element.disabled=!!draft?.controlBusy||action==='once'&&(!(item.allowed_choices?.includes('once')??review?.version!==2)||item.capability_call?.coverage==='UNAVAILABLE');}
  if(row.article!==cursor)pending.insertBefore(row.article,cursor);cursor=row.article.nextSibling;
 }
 for(const [key,row]of reviewPendingRows)if(!retained.has(key)){row.article.remove();reviewPendingRows.delete(key);reviewAnswers.delete(key);reviewCustom.delete(key);}

}
function render(next){
  if(!next||typeof next.epoch!=='number'||!document.body)return;
  if(state&&next.epoch<state.epoch)return;
  if(pendingInputFocus&&(!next.visible||next.epoch!==pendingInputFocus.epoch||next.draft?.id!==pendingInputFocus.draftId))pendingInputFocus=null;
  const previous=state?.draft;
  if(previous?.id!==next.draft?.id)rememberEditorView();
  if(handoffLock&&next.epoch!==handoffLock.epoch)handoffLock=null;
  state=next;if(next?.supervisor?.snapshot)for(const listener of supervisorListeners)listener(next.supervisor.snapshot);localize(state.locale??uiLocale);if(image)image.alt=role==='overlay'?L('本次批注的冻结页面'):L('已冻结的页面依据');const draft=state.draft,changed=draft?.id!==lastDraftId;
  revealMotion(state.motion);
  for(const button of document.querySelectorAll('[data-command="pin"]')){
    button.setAttribute('aria-pressed',String(state.pinnedPreference===true));
    button.setAttribute('aria-label',state.pinLabel);button.title=state.pinLabel;
    button.disabled=!state.pinSupported||state.pinBusy===true;
  }
  if(!changed&&previous?.textEditor&&!draft?.textEditor&&JSON.stringify(localMarks)!==JSON.stringify(draft?.marks??[]))saveHistory();
  if(changed){lastDraftId=draft?.id;history=[];future=[];drawing=null;localMarks=draft?.marks??[];showResultPreview=false;editor=null;}
  else if(!pendingEdits&&!drawing)localMarks=draft?.marks??[];
  if(!pendingEdits)editor=draft?.textEditor?{...draft.textEditor}:null;
  showTextEditor();
  if(textInput)textInput.disabled=!!handoffLock;
  if(image&&image.src!==state.source.image){
    if(lastImage!==state.source.image){lastImage=state.source.image;history=[];future=[];drawing=null;}
    image.hidden=true;if(state.source.image)image.src=state.source.image;else image.removeAttribute('src');
  }
  if(renderActivity())return;
  repaint();
  if(role==='overlay'){
    const label=document.querySelector('.overlay-status');if(label)label.textContent=state.error||(editor?L('在图上输入文字 · Ctrl+Enter 确认 · Esc 取消本次编辑'):L('在冻结画面上圈画或写字 · Enter 完成批注'));
    if(state.overlayPreparing&&image?.complete&&image.naturalWidth>0)void send({kind:'preview-ready',epoch:state.epoch}).catch(()=>{});
    return;
  }
  renderReview();
  syncCanonicalStream(state.result?.canonical_original===true);
  const editing=(draft?.phase==='EDITING'||state.guidanceEditable)&&!state.capturePending&&!handoffLock;
  const inputText=state.guidanceEditable?draft?.guidanceText??'':draft?.comment??'';
  const clearedGuidance=state.guidanceEditable&&!composing&&!pendingEdits&&inputText===''&&comment?.value===previous?.guidanceText;
  if(comment){if((changed||clearedGuidance||(!composing&&!pendingEdits&&document.activeElement!==comment))&&comment.value!==inputText)comment.value=inputText;comment.disabled=!editing;
    if(savedEditorView?.id===draft?.id&&editing&&state.visible){const view=savedEditorView,epoch=state.epoch,id=draft.id;savedEditorView=null;if(Number.isInteger(view.start))comment.setSelectionRange?.(view.start,view.end,view.direction);comment.scrollTop=view.scroll;if(view.focused)void Promise.resolve().then(()=>{if(state?.epoch===epoch&&state?.draft?.id===id&&state.visible&&(state.draft.phase==='EDITING'||state.guidanceEditable)&&!handoffLock&&typeof document.hasFocus==='function'&&document.hasFocus())comment.focus?.({preventScroll:true});});}}
  document.querySelector('#source-label').textContent=state.source.label;
  document.querySelector('#source-label').title=state.source.label;
  const workContext=document.querySelector('#work-context'),workLabel=document.querySelector('#work-context-label');
  if(workContext){workContext.hidden=!state.workContext?.available;workContext.dataset.unavailable=String(!!state.workContext?.unavailable);workContext.setAttribute('role',state.workContext?.unavailable?'alert':'note');}
  if(workLabel){workLabel.textContent=state.workContext?.available?(state.workContext.unavailable?L(state.workContext.label):L('原工作 · ')+state.workContext.label):'';workLabel.title=L('沿用桌面对话中的明确工作选择，不扩大执行权限。');}
  const fileContext=document.querySelector('#file-context'),fileLabel=document.querySelector('#file-context-label');
  if(fileContext)fileContext.hidden=!state.fileContext?.available;
  if(fileLabel){fileLabel.textContent=state.fileContext?.available?L('文稿 · ')+state.fileContext.label:'';fileLabel.title=L('明确关联的项目文稿，只读依据；不表示已识别当前窗口');}
  document.querySelector('#preview').hidden=!state.source.image||!state.previewExpanded;
  document.querySelector('#toggle-preview').hidden=!state.source.image;
  document.querySelector('#toggle-preview').textContent=state.previewExpanded?L('收起依据'):L('依据');
  const indicator=document.querySelector('#context-indicator'),markCount=document.querySelector('#mark-count');
  if(indicator){indicator.textContent=state.capturePending?L('读取中'):state.guidanceEditable?L(state.result?.canonical_original?'对话':'补充当前任务'):state.source.available?L('当前页'):L('未引用页面');indicator.title=state.source.label;}
  if(markCount){markCount.textContent=(draft?.marks.length??0)+L(' 处批注');markCount.hidden=!draft?.marks.length;}
  document.querySelector('#editor').hidden=!!draft?.operationId&&!state.guidanceEditable||!!draft?.textEditor;
  if(comment){comment.placeholder=L(state.guidanceEditable?'补充要求，或询问进度…':'问当前页');comment.setAttribute('aria-label',L(state.guidanceEditable?(state.result?.canonical_original?'对话':'补充当前任务'):'问当前页'));}
  document.querySelector('#question').hidden=!draft?.operationId;
  document.querySelector('#question').textContent=draft?.comment||L('已发送标记意见');
  const controlHistory=document.querySelector('#control-history'),controls=[...state.result?.native_message_controls??[],...state.result?.canonical_controls??[]];
  renderControlHistory(controlHistory,controls,draft);
  const summary=document.querySelector('#text-summary');
  if(summary){summary.textContent=(draft?.marks??[]).filter(mark=>mark.type==='text').map(mark=>mark.text).join('\n\n');summary.hidden=!summary.textContent;}
  document.querySelector('#keyboard-hint').hidden=true;
  document.querySelector('#retake-confirm').hidden=!draft?.retakePending;
  document.querySelector('#heading').textContent=draft?.operationId?L('当前问题'):state.kind==='annotate'?L('批注当前页面'):L('问当前页面');
  const messages={SAVING:L('正在保存…'),SENDING:L('正在发送…'),RUNNING:L('处理中…'),COMPLETED:'',FAILED:L('未完成，可查看回复。'),UNKNOWN:L('结果尚不确定，查看原操作进展；不会重复发送。')};
  const intentHint=state.canDraft&&!state.canSend?L('补充要解释或修改什么。'):'';
  const cancelNotice=draft?.cancelPending?L('正在请求停止…'):draft?.phase==='RUNNING'&&state.result?.cancellation_status==='UNKNOWN'?L('停止请求结果待核查；原任务状态见进展，不会重复请求。'):draft?.phase==='RUNNING'&&(state.result?.cancellation_status==='REQUESTED'||state.result?.status==='CANCEL_REQUESTED')?L('正在停止，等待原操作回执…'):'';
  const message=state.error||cancelNotice||intentHint||state.notice||(draft?.phase==='UNKNOWN'&&state.result?.firstSubmissionLabel)||(state.capturePending?L('读取当前页…'):draft?.restored?L('旧依据；重新引用后可发送。'):
    draft?.phase==='FAILED'?state.result?.statusLabel||messages.FAILED:messages[draft?.phase]??(draft?.savePending?L('正在保存…'):''));
  const persistenceFailed=state.result?.local_persistence_status==='FAILED',projectionIncomplete=state.result?.local_projection?.complete===false;
  const returned=['RETURNED','COMPLETED','SUCCESS','SUCCEEDED'].includes(state.result?.status);
  const persistenceNotice=persistenceFailed?(returned?L('回复已收到，本机保存未成功；请核查原操作，勿重复发送。'):L('本次回执的本机保存未成功；请核查原操作，勿重复发送。')):'';
  const projectionNotice=projectionIncomplete?L('本机仅保留部分回执，可核查原操作查看完整内容。'):'';
  const artifactNotice=state.result?.artifact_error?.message||state.result?.artifactStatusLabel||'';
  const submissionNotice=!returned?state.result?.firstSubmissionLabel||'':'';
  const element=document.querySelector('#status');
  element.textContent=[persistenceFailed&&returned&&!state.error?persistenceNotice:message,...persistenceFailed&&(!returned||state.error)?[persistenceNotice]:[],projectionNotice,artifactNotice,...state.result?.owner_stop_confirmed?[L('处理已中断')]:[],submissionNotice!==message?submissionNotice:''].filter(Boolean).join(' ');
  element.hidden=!element.textContent;
  element.dataset.submission=String(!!submissionNotice);
  element.dataset.error=String(!!state.error);element.dataset.warning=String(persistenceFailed||projectionIncomplete||!!artifactNotice||!!submissionNotice);
  for(const button of document.querySelectorAll('[data-command]')){
    const value=button.dataset.command;
    if(value==='send')button.disabled=!editing||!state.canSend||reviewSelectionPending?.epoch===state.epoch;
    if(value==='file-clear'){button.hidden=!state.fileContext?.available;button.disabled=!state.fileContext?.canClear;}
    if(value==='stop'){button.hidden=!!draft?.readonly||!state.cancelSupported||!draft?.operationId||(!state.result?.canonical_stop_ref&&!['RUNNING','SENDING'].includes(draft.phase)&&!(state.result?.status==='UNKNOWN_OUTCOME'&&(state.result.review?.can_cancel===true||state.result.control_capabilities?.can_cancel===true)&&!state.result.review?.stop_control&&!['REQUESTED','UNKNOWN'].includes(state.result.cancellation_status)));button.disabled=!state.canStop;button.textContent=draft?.cancelPending||state.result?.cancellation_status==='REQUESTED'||state.result?.status==='CANCEL_REQUESTED'?L('停止中'):state.result?.cancellation_status==='UNKNOWN'?L('停止待核查'):L('停止');}
    if(value==='draft'){button.disabled=!editing||!state.canDraft;button.hidden=!draft?.marks.length;}
    if(value==='annotate'){button.hidden=!!draft?.operationId;button.disabled=draft?.phase!=='EDITING'||!state.annotationAvailable;button.title=state.annotationAvailable?L('在当前页面圈选或圈画'):L('需要已验证的单屏页面画面，才能原位圈选');}
    if(value==='recapture')button.disabled=!!draft?.operationId||state.capturePending||draft?.phase==='SAVING';
    if(value==='inspect'){
      const pendingControl=!!draft?.reviewControl||!!state.result?.local_review_controls?.length;
      button.hidden=!draft?.operationId||(!['SENDING','RUNNING','UNKNOWN'].includes(draft?.phase)&&!persistenceFailed&&!projectionIncomplete&&!artifactNotice&&!pendingControl);
      button.textContent=persistenceFailed||projectionIncomplete||artifactNotice||pendingControl||state.result?.cancellation_status==='UNKNOWN'?L('核查原操作'):L('查看进展');
    }
    if(value==='capability-continue')button.hidden=!!draft?.readonly||state.result?.status!=='RETURNED'||state.result?.review?.version!==2||!state.result?.review?.task_root_operation_id;
    if(value==='review-continue')button.hidden=!!draft?.readonly||state.result?.dispatch_state!=='NOT_DISPATCHED'||state.result?.execution_started!==false;
    if(value==='new-question'){button.hidden=!['COMPLETED','FAILED','UNKNOWN','RUNNING'].includes(draft?.phase)&&!(draft?.phase==='EDITING'&&draft.nativeReview?.version===2&&draft.nativeReview.task_root_operation_id);button.textContent=L(draft?.readonly&&draft.hasSuspendedDraft?'返回草稿':'新问题');}
    if(value==='expand')button.hidden=!draft?.operationId;
  }
  renderQuickReply(document.querySelector('#reply'),{text:state.result?.text||state.result?.reason||'',
    identity:draft?.operationId||draft?.id||'',markdown:!!state.result?.text,locale:uiLocale});
  restoreCanonicalReading();
  const files=document.querySelector('#files');files.replaceChildren();
  for(const artifact of state.result?.artifacts??[]){
    const button=document.createElement('button');button.textContent=artifact.label;button.title=L('在本机软件中打开');
    button.addEventListener('click',()=>void send({kind:'artifact',epoch:state.epoch,index:artifact.index}).catch(()=>status(L('文件暂未打开，可展开对话继续。'))));files.append(button);
  }
  files.hidden=!files.children.length;previewLayout();
}
function position(event){const bounds=canvas.getBoundingClientRect();return {x:clamp((event.clientX-bounds.left)/bounds.width),y:clamp((event.clientY-bounds.top)/bounds.height)};}
function updateDraw(event){
  if(!drawing||drawing.id!==event.pointerId)return;
  const end=position(event),start=drawing.start;
  if(tool==='freehand'){
    const points=drawing.mark?.points??[start];
    if(points.length<256)points.push(end);else points[points.length-1]=end;
    drawing.mark={type:tool,points};
  }else if(tool==='arrow')drawing.mark={type:tool,points:[start,end]};
  else drawing.mark={type:tool,x:Math.min(start.x,end.x),y:Math.min(start.y,end.y),width:Math.abs(end.x-start.x),height:Math.abs(end.y-start.y)};
  repaint();
}
function finishDraw(event,commit){
  if(!drawing||drawing.id!==event.pointerId)return;
  updateDraw(event);const mark=drawing.mark;drawing=null;
  if(canvas.hasPointerCapture(event.pointerId))canvas.releasePointerCapture(event.pointerId);
  if(commit&&mark){
    const bounds=canvas.getBoundingClientRect();
    const valid=['rect','ellipse'].includes(mark.type)?mark.width*bounds.width>=3&&mark.height*bounds.height>=3:
      mark.points.length>=2&&mark.points.some(p=>Math.hypot((p.x-mark.points[0].x)*bounds.width,(p.y-mark.points[0].y)*bounds.height)>=3);
    const points=localMarks.reduce((sum,item)=>sum+(item.points?.length??0),0)+(mark.points?.length??0);
    if(valid&&localMarks.length<32&&points<=1024){saveHistory();localMarks=[...localMarks,mark];void edit();}
    else if(localMarks.length>=32||points>1024)status(L('标记已较多，请先确认本轮意见。'));
  }
  repaint();
}
ipcRenderer.on(STATE_CHANNEL,(_event,next)=>{
  if(next?.kind==='focus-input'){
    if(Number.isSafeInteger(next.epoch)&&typeof next.draftId==='string'&&Number.isSafeInteger(next.requestedAt)){
      pendingInputFocus={epoch:next.epoch,draftId:next.draftId,requestedAt:next.requestedAt};focusRequestedInput();
    }
    return;
  }
  if(next?.kind==='flush-release'){handoffLock=null;if(state)render(state);return;}
  if(next?.kind==='supervisor-flush-request'){
    const request=next;void(async()=>{let ok=role==='popover'&&state?.presentation==='ACTIVITY'&&request.epoch===state.epoch&&!handoffLock&&!supervisorComposing&&!composing;
      if(ok){handoffLock=request;renderSupervisor();try{await supervisorClient?.flush();ok=request.epoch===state.epoch&&!supervisorComposing&&!composing;}catch{ok=false;}}
      try{await send({kind:'supervisor-flush-complete',id:request.id,epoch:request.epoch,status:ok?'SAVED':'FAILED'});}catch{handoffLock=null;renderSupervisor();}
    })();return;
  }
  if(next?.kind!=='flush-request'){render(next);return;}
  const request=next;
  void (async()=>{
    let ok=!!state&&request.epoch===state.epoch&&request.draftId===state.draft?.id&&!handoffLock&&!composing&&!drawing;
    if(ok){rememberEditorView();handoffLock=request;if(comment)comment.disabled=true;if(textInput)textInput.disabled=true;
      const result=editor?await editText():role==='popover'?await edit():await editTail;ok=result?.status!=='ACTION_FAILED'&&request.epoch===state.epoch&&request.draftId===state.draft?.id&&!composing&&!drawing;
    }
    try{await send({kind:'flush-complete',id:request.id,epoch:request.epoch,draftId:request.draftId,status:ok?'SAVED':'FAILED'});}
    catch{handoffLock=null;if(state)render(state);}
  })();
});
window.addEventListener('DOMContentLoaded',()=>{
  rememberLabels();localize(uiLocale);
  role=document.body.dataset.role;comment=document.querySelector('#comment');canvas=document.querySelector('#canvas');
  image=document.querySelector('#source-image');marksSvg=document.querySelector('#marks');
  textMarks=document.querySelector('#text-marks');textEditor=document.querySelector('#text-editor');textInput=document.querySelector('#text-value');
  if(!textMarks&&typeof document.createElement==='function'){
    textMarks=document.createElement('div');textMarks.className='text-marks';(canvas||document.querySelector('#preview'))?.append(textMarks);
  }
  if(!image){
    image=document.createElement('img');image.id='source-image';image.alt=role==='overlay'?L('本次批注的冻结页面'):L('已冻结的页面依据');image.draggable=false;image.hidden=true;
    (canvas||document.querySelector('#preview'))?.prepend(image);
  }
  image?.addEventListener('load',()=>{
    image.hidden=false;previewLayout();
    if(role==='overlay'&&state?.overlayPreparing)void send({kind:'preview-ready',epoch:state.epoch}).catch(()=>{});
  });
  image?.addEventListener('error',()=>{if(role==='overlay'&&state?.overlayPreparing)void send({kind:'preview-failed',epoch:state.epoch}).catch(()=>{});});
  window.addEventListener('resize',previewLayout);
  document.querySelector('#toggle-preview')?.addEventListener('click',()=>void command('toggle-preview'));
  for(const button of document.querySelectorAll('[data-command]'))button.addEventListener('click',()=>void command(button.dataset.command));
  for(const button of document.querySelectorAll('[data-tool]')){
    button.setAttribute('aria-pressed',String(button.dataset.tool===tool));
    button.addEventListener('click',async()=>{
      if(editor&&!await textCommand('text-confirm'))return;
      tool=button.dataset.tool;for(const item of document.querySelectorAll('[data-tool]'))item.setAttribute('aria-pressed',String(item===button));
    });
  }
  for(const button of document.querySelectorAll('[data-text-command]'))button.addEventListener('click',()=>void command(button.dataset.textCommand));
  textInput?.addEventListener('compositionstart',()=>{composing=true;});
  textInput?.addEventListener('compositionend',()=>{composing=false;if(editor){editor.text=textInput.value;showTextEditor();void editText();}});
  textInput?.addEventListener('input',()=>{if(editor){editor.text=textInput.value;showTextEditor();void editText();}});
  comment?.addEventListener('compositionstart',()=>{composing=true;});comment?.addEventListener('compositionend',()=>{composing=false;void edit();});
  comment?.addEventListener('input',()=>{void edit();});
  for(const id of ['review-skill','review-tool'])document.querySelector('#'+id)?.addEventListener('change',()=>{
    const selected=state.draft?.nativeReview?.selection,capability=state.reviewCatalog?.schema==='eeas.native-capability-task-capabilities.v2';
    selectReview(capability&&(!selected||Array.isArray(selected.mcp_tool_selection))?{kind:'capability-selection',skillId:document.querySelector('#review-skill').value,toolIds:selected?.mcp_tool_selection?.map(item=>item.tool_id)??[]}:{kind:'review-selection',skillId:document.querySelector('#review-skill').value,toolId:document.querySelector('#review-tool').value});
  });
  document.querySelector('#capability-search')?.addEventListener('input',filterCapabilities);
  for(const [id,kind]of [['review-process','review-consent'],['review-remote','review-remote-consent']])document.querySelector('#'+id)?.addEventListener('change',event=>{
    if(state.reviewBusy||reviewSelectionPending?.epoch===state.epoch){renderReview();return;}
    void send({kind,epoch:state.epoch,enabled:event.currentTarget.checked}).catch(()=>status(L('修改暂未保存，内容仍在此处。')));
  });
  document.querySelector('#reply')?.addEventListener('click',event=>{
    const anchor=event.target?.closest?.('a[href]');if(!anchor)return;event.preventDefault();
    const url=replyLinkTarget(anchor.getAttribute('href'));if(!event.isTrusted||!url)return;
    void send({kind:'reply-link',epoch:state.epoch,operationId:state.draft?.operationId,text_sha256:state.result?.text_sha256,url}).catch(()=>status(L('操作未完成，内容已保留。请重试或展开对话继续。')));
  });
  document.querySelector('#guidance')?.addEventListener('input',event=>void send({kind:'review-guidance-edit',epoch:state.epoch,text:event.currentTarget.value}).catch(()=>{}));
  const activityContent=document.querySelector('.activity-content'),readFollow=()=>requestAnimationFrame(()=>{if(activityContent)supervisorFollow=activityContent.scrollHeight-activityContent.scrollTop-activityContent.clientHeight<48;});
  for(const name of ['wheel','touchmove'])activityContent?.addEventListener(name,readFollow,{passive:true});
  activityContent?.addEventListener('pointerup',event=>{if(!event.target?.closest?.('button,a,summary'))readFollow();});
  activityContent?.addEventListener('keydown',event=>{if(['PageUp','PageDown','Home','End','ArrowUp','ArrowDown'].includes(event.key))readFollow();});
  document.querySelector('#supervisor-tasks')?.addEventListener('toggle',event=>{if(event.currentTarget.open)supervisorFollow=false;});
  const supervisorInput=document.querySelector('#supervisor-input');
  supervisorInput?.addEventListener('compositionstart',()=>{supervisorComposing=true;});
  supervisorInput?.addEventListener('compositionend',()=>{supervisorComposing=false;try{ensureSupervisor()?.edit(supervisorInput.value);}catch{supervisorNotice=supervisorText('draftUnavailable');renderSupervisor();}});
  supervisorInput?.addEventListener('input',()=>{if(supervisorComposing)return;try{supervisorNotice='';ensureSupervisor()?.edit(supervisorInput.value);}catch{supervisorNotice=supervisorText('draftUnavailable');renderSupervisor();}});
  document.querySelector('#supervisor-compose')?.addEventListener('submit',event=>{event.preventDefault();void askSupervisor();});
  document.querySelector('#supervisor-inspect')?.addEventListener('click',()=>{const id=document.querySelector('#supervisor-inspect')?.dataset.operationId;if(id)void supervisorClient.inspect(id).catch(()=>renderSupervisor());});
  document.querySelector('#supervisor-local')?.addEventListener('click',()=>void askSupervisor(true));
  for(const [id,method]of [['supervisor-check-save','inspectDraftSave'],['supervisor-retry-save','retryDraftSave']])document.querySelector('#'+id)?.addEventListener('click',()=>{if(supervisorComposing||handoffLock)return;supervisorNotice='';void supervisorClient?.[method]().catch(()=>renderSupervisor());});
  for(const [id,choice]of [['supervisor-keep','keep-local'],['supervisor-remote','use-remote']])document.querySelector('#'+id)?.addEventListener('click',()=>{if(supervisorComposing||handoffLock)return;try{supervisorNotice='';supervisorClient?.resolveDraftConflict(choice);if(choice==='keep-local')void supervisorClient.flush().catch(()=>renderSupervisor());}catch{supervisorNotice=supervisorText('draftUnavailable');renderSupervisor();}});
  window.addEventListener('keydown',event=>{
    if(event.isComposing||composing||event.keyCode===229)return;
    if(state?.presentation==='ACTIVITY'){
      if(supervisorComposing)return;
      if(state.supervisor?.supported&&event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();void askSupervisor();return;}
      if(event.key==='Escape'){event.preventDefault();void command('dismiss');}
      return;
    }
    if(editor){
      if(event.key==='Escape'){event.preventDefault();void command('text-cancel');}
      else if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)){event.preventDefault();void command('text-confirm');}
      return;
    }
    if(event.key==='Escape'){event.preventDefault();if(drawing){drawing=null;repaint();}void command('dismiss');}
    else if(event.key==='Enter'&&(event.ctrlKey||event.metaKey)&&role==='popover'){event.preventDefault();void command('send');}
    else if(role==='overlay'&&(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='z'){event.preventDefault();void command(event.shiftKey?'redo':'undo');}
    else if(role==='overlay'&&event.key==='Enter'){event.preventDefault();void command('review');}
  });
  if(canvas){
    canvas.addEventListener('pointerdown',event=>{
      if(event.button!==0||drawing||state?.draft?.phase!=='EDITING')return;
      if(event.target?.closest?.('.text-editor,.text-mark,.toolbar'))return;
      if(tool==='text'){event.preventDefault();void startText(position(event));return;}
      if(editor){void textCommand('text-confirm');return;}
      event.preventDefault();drawing={id:event.pointerId,start:position(event),mark:null};canvas.setPointerCapture(event.pointerId);
    });
    canvas.addEventListener('pointermove',updateDraw);canvas.addEventListener('pointerup',event=>finishDraw(event,true));
    canvas.addEventListener('pointercancel',event=>finishDraw(event,false));
    canvas.addEventListener('lostpointercapture',event=>{if(drawing?.id===event.pointerId){drawing=null;repaint();}});
  }
  void send({kind:'state'}).then(render).catch(()=>status(L('快捷窗口暂未连接，请稍后重试。')));
});
