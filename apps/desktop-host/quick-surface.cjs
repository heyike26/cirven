'use strict';
const path = require('node:path');
const {WINDOW_TITLES,SHORT_NAME}=require('./product-brand.cjs');
const {randomUUID} = require('node:crypto');
const {QuickNotSubmittedError}=require('./quick-not-submitted.cjs');
const {QuickActivation}=require('./quick-activation.cjs');
const {quickNativeReview,quickResetNativeReview,quickReviewProjection,quickDigest,quickAssociationScope}=require('./quick-journal.cjs');
const {submissionObservation}=require('./quick-submission-observation.cjs');
const {validateOriginalWorkChoice}=require('../../plugins/vscode-companion/work-input-wire.cjs');
const {canonicalQuickMetadataStatus}=require('./canonical-quick.cjs');
const {replyLinkTargets}=require('./quick-reply-links.bundle.cjs');

const {t,resolveLocale,htmlCatalog}=require('../../packages/i18n/index.cjs');
const CHANNEL = 'eeas:quick-surface:event';
const STATE_CHANNEL = 'eeas:quick-surface:state';
const TOOLS = Object.freeze(['rect', 'ellipse', 'arrow', 'freehand', 'text']);
const SHA = /^[a-f0-9]{64}$/;
const ID = /^[A-Za-z0-9_-]{1,100}$/;
const PNG = /^data:image\/png;base64,[A-Za-z0-9+/]+=*$/;
const TEXT_LIMIT = 16000;
const ARTIFACT_COPY = Object.freeze({
  PREPARING:'正在生成修订稿…',PROPOSED_NOT_PUBLISHED:'修订提案已保留，尚未生成文件。',
  NEEDS_CLARIFICATION:'这段内容需要进一步确认，尚未生成修订文件。',
  CANCELLED_NOT_PUBLISHED:'已停止生成修订文件；原回复、原文件和提案保留。',
  PUBLICATION_UNKNOWN:'修订文件的生成结果尚未确认；请核查原操作，勿重新生成。',
  UNAVAILABLE:'修订文件暂不可用，原回复与提案保留；请核查原操作，勿重复发送。'
});

function exact(value, keys) {
  return !!value && typeof value === 'object' && !Array.isArray(value) &&
    Object.keys(value).sort().join(',') === [...keys].sort().join(',');
}
function point(value) {
  return exact(value, ['x','y']) && [value.x,value.y].every(n => Number.isFinite(n) && n >= 0 && n <= 1);
}
function normalizedMarks(raw) {
  if (!Array.isArray(raw) || raw.length > 32) throw new Error('Too many annotation marks');
  let points = 0, textCharacters = 0;
  return raw.map(mark => {
    if (!mark || !TOOLS.includes(mark.type)) throw new Error('Unknown annotation tool');
    if(mark.type==='text'){
      if(!exact(mark,['type','x','y','text'])||!point({x:mark.x,y:mark.y})||
          !boundedText(mark.text,2000).trim())throw new Error('Invalid text annotation');
      textCharacters += [...mark.text].length;
      if(textCharacters>16000)throw new Error('Too much annotation text');
      return {type:'text',x:mark.x,y:mark.y,text:mark.text};
    }
    if (['rect','ellipse'].includes(mark.type)) {
      if (!exact(mark,['type','x','y','width','height']) || ![mark.x,mark.y,mark.width,mark.height].every(Number.isFinite) ||
          mark.x < 0 || mark.y < 0 || mark.width <= 0 || mark.height <= 0 || mark.x+mark.width > 1.000001 || mark.y+mark.height > 1.000001) {
        throw new Error('Invalid normalized annotation rectangle');
      }
      return {type:mark.type,x:mark.x,y:mark.y,width:mark.width,height:mark.height};
    }
    if (!exact(mark,['type','points']) || !Array.isArray(mark.points) || mark.points.length < 2 ||
        mark.points.length > (mark.type === 'arrow' ? 2 : 256) || !mark.points.every(point)) throw new Error('Invalid annotation points');
    points += mark.points.length;
    if (points > 1024) throw new Error('Too many annotation points');
    return {type:mark.type,points:mark.points.map(p=>({x:p.x,y:p.y}))};
  });
}
function markRegions(marks) {
  return marks.flatMap(mark => {
    if(mark.type==='text')return [];
    if (['rect','ellipse'].includes(mark.type)) return [{x:mark.x,y:mark.y,width:mark.width,height:mark.height}];
    const xs=mark.points.map(p=>p.x),ys=mark.points.map(p=>p.y),x=Math.min(...xs),y=Math.min(...ys);
    const width=Math.max(...xs)-x,height=Math.max(...ys)-y;
    return width > 0 && height > 0 ? [{x,y,width,height}] : [];
  });
}
function boundedText(value, limit=TEXT_LIMIT) {
  if (typeof value !== 'string' || [...value].length > limit || /[\uD800-\uDBFF](?![\uDC00-\uDFFF])|(?<![\uD800-\uDBFF])[\uDC00-\uDFFF]/u.test(value)) throw new Error('A bounded text value is required');
  return value;
}
function safeText(value, limit=240) { return typeof value === 'string' ? [...value].slice(0,limit).join('') : ''; }
function guidanceScope(draft) {
  if(!draft||draft.readonly||!draft.operationId)return null;
  const result=draft.result;
  const canonical=result?.canonical_input_ref??result?.canonical_message_ref;
  if(result?.canonical_original&&ref(canonical)&&canonical.revision===1)
    return {expected_canonical_message_ref:canonical,can_guide:result.control_capabilities?.can_guide===true,can_status:result.control_capabilities?.can_status===true};
  if(result?.native_message_ref&&!result.review&&ref(result.native_message_ref)&&result.native_message_ref.revision===1&&result.native_message_ref.id===draft.operationId)
    return {expected_native_message_ref:result.native_message_ref,can_guide:result.control_capabilities?.can_guide===true,can_status:result.control_capabilities?.can_status===true};
  if(ref(result?.review?.review_ref))return {expected_review_ref:result.review.review_ref,can_guide:result.review.can_guide===true,can_status:false};
  return null;
}
function ref(value) { return value && typeof value.id === 'string' && Number.isSafeInteger(value.revision) && value.revision > 0 && SHA.test(value.sha256); }
function activityKey(raw){
  if(!raw||typeof raw!=='object'||Array.isArray(raw)||Object.keys(raw).some(key=>!['kind','project_id','object_id','operation_id','generation'].includes(key))||
      typeof raw.kind!=='string'||!/^[A-Za-z][A-Za-z0-9_-]{0,63}$/.test(raw.kind)||typeof raw.object_id!=='string'||!/^[A-Za-z0-9_-]{1,200}$/.test(raw.object_id)||
      !Number.isSafeInteger(raw.generation)||raw.generation<0)throw new Error('Invalid activity task key');
  const result={kind:raw.kind,object_id:raw.object_id,generation:raw.generation};
  for(const name of ['project_id','operation_id'])if(Object.hasOwn(raw,name)){
    if(raw[name]!==null&&(typeof raw[name]!=='string'||!/^[A-Za-z0-9_-]{1,100}$/.test(raw[name])))throw new Error('Invalid activity task scope');
    result[name]=raw[name];
  }
  return result;
}
function activityProjection(snapshot,revision){
  const keys=new Map(),counts={attention:0,running:0,recent:0};
  if(snapshot==null)return {keys,public:{revision,status:'UNAVAILABLE',enabled:null,observer_status:null,items:[],full_count:0,has_more:false,counts}};
  if(!snapshot.settings||typeof snapshot.settings.enabled!=='boolean'||!Array.isArray(snapshot.tasks)||snapshot.tasks.length>10000||
      !['INITIAL','READY','PARTIAL','OBSERVATION_UNAVAILABLE','DISABLED','CLOSED'].includes(snapshot.observer?.status))throw new Error('Invalid activity snapshot');
  const labels={RUNNING:'运行中',WAITING_USER:'等待补充',EXECUTION_RETURNED:'执行返回',EXECUTION_ERROR:'执行报错',TIMED_OUT:'超时，需核查',CANCEL_REQUESTED:'取消中',CANCELLED:'已取消',NEEDS_CHECK:'需核查'};
  const values=snapshot.tasks.map(task=>{
    const key=activityKey(task.key);
    if(typeof task.label!=='string'||!task.label.trim()||[...task.label].length>200||/[\u0000-\u001f\u007f]/.test(task.label)||
        /(?:bearer\s+\S+|sk-[a-z0-9_-]{12,}|(?:api[_-]?key|password|access[_-]?token)\s*[:=])/i.test(task.label)||
        !Object.hasOwn(labels,task.classification)||!['CURRENT','STALE','NEEDS_CHECK_AFTER_RESTART'].includes(task.observation_status)||
        typeof task.source_status!=='string'||!/^[A-Za-z][A-Za-z0-9_.:-]{0,79}$/.test(task.source_status)||
        typeof task.observed_at!=='string'||task.observed_at.length>64||!/(Z|[+-]\d{2}:\d{2})$/.test(task.observed_at)||!Number.isFinite(Date.parse(task.observed_at))||
        task.actual_error_code!=null&&(typeof task.actual_error_code!=='string'||!/^[A-Za-z][A-Za-z0-9_.:-]{0,99}$/.test(task.actual_error_code)))throw new Error('Invalid activity task metadata');
    const group=task.observation_status!=='CURRENT'||['WAITING_USER','EXECUTION_ERROR','TIMED_OUT','NEEDS_CHECK'].includes(task.classification)?'attention':
      ['RUNNING','CANCEL_REQUESTED'].includes(task.classification)?'running':'recent';counts[group]++;
    return {key,group,label:task.label,classification:task.classification,source_status:task.source_status,observation_status:task.observation_status,
      actual_error_code:task.actual_error_code??null,observed_at:task.observed_at,status:labels[task.classification]+
        (task.observation_status==='STALE'?' · 观察过期':task.observation_status==='NEEDS_CHECK_AFTER_RESTART'?' · 重启后待核对':'')};
  }).sort((a,b)=>['attention','running','recent'].indexOf(a.group)-['attention','running','recent'].indexOf(b.group)||Date.parse(b.observed_at)-Date.parse(a.observed_at));
  const items=values.slice(0,20).map(({key,...item})=>{const token='task_'+randomUUID();keys.set(token,key);return {token,...item};});
  return {keys,public:{revision,status:'AVAILABLE',enabled:snapshot.settings.enabled,observer_status:snapshot.observer.status,items,full_count:values.length,has_more:values.length>items.length,counts}};
}
function supervisorSelection(value,snapshot,verifiedByOwner=false){
  const current=snapshot?.operations.find(row=>row.operation_id===value?.operation_id);
  if(!exact(value,['operation_id','request_sha256','question','answer_text','status'])||!ID.test(value.operation_id)||
      !SHA.test(value.request_sha256)||(current?current.request_sha256!==value.request_sha256:!verifiedByOwner)||
      !['RETURNED','UNKNOWN','REJECTED','PROCESSING','REQUESTING','RUNNING','FAILED','CANCELLED'].includes(value.status))throw new Error('Supervisor task selection changed');
  boundedText(value.question,8000);boundedText(value.answer_text,48000);return JSON.parse(JSON.stringify(value));
}
function sourceView(capture) {
  const snapshot=capture?.snapshot,preview=snapshot?.preview;
  return {label:safeText(snapshot?.observation?.window?.title)||'当前页面未取得',
    available:snapshot?.current===true && ref(snapshot?.observation?.context_ref),
    image:preview?.status==='AVAILABLE' && typeof preview.data_url==='string' && preview.data_url.length <= 40*1024*1024 && PNG.test(preview.data_url) ? preview.data_url : null};
}
function fileContextView(capture){
  const scope=capture?.scope,link=scope?.fileLinkRef;
  const available=!!link&&scope.projectId===link.project_id&&SHA.test(link.id)&&ref(link);
  return {available,label:available?safeText(scope.fileLabel,160)||'项目文稿':'',readOnly:true};
}
function workContextView(capture,nativeReview){
 const scope=capture?.scope;if(scope?.workInputUnavailable)return {available:true,unavailable:true,label:'所选原工作的范围需要核对，原稿保留，请返回对话。'};
 if(!scope?.originalWorkChoice)return {available:false,unavailable:false,label:''};
 try{const choice=validateOriginalWorkChoice(scope.originalWorkChoice);if(!scope.conversationRef||!scope.originalSource||choice.owner.project_id!==scope.originalSource.project_id||quickDigest(choice.owner.session_ref)!==quickDigest(scope.originalSource.session_ref)||scope.fileLinkRef||nativeReview)throw Error('Different work scope');return {available:true,unavailable:false,label:choice.label||'所选原工作'};}
 catch{return {available:true,unavailable:true,label:'所选原工作的范围需要核对，原稿保留，请返回对话。'};}
}
function metadataStatusDraft(draft){return !!draft&&canonicalQuickMetadataStatus({scope:draft.capture?.scope,nativeReview:draft.nativeReview},[draft.comment,...(draft.marks??[]).filter(m=>m.type==='text').map(m=>m.text)].filter(Boolean).join('\n\n'));}
function rectangle(value) {
  return !!value && ['x','y','width','height'].every(k=>Number.isFinite(value[k])) && value.width>0 && value.height>0;
}
function contains(area, bounds) {
  return bounds.x>=area.x && bounds.y>=area.y && bounds.x+bounds.width<=area.x+area.width+1 && bounds.y+bounds.height<=area.y+area.height+1;
}
function annotationBounds(capture, screen, ownWindow) {
  const geometry=capture?.geometry;
  if (!sourceView(capture).image || geometry?.verified!==true || !rectangle(geometry.physicalBounds) ||
      !Number.isSafeInteger(geometry.pixelWidth) || !Number.isSafeInteger(geometry.pixelHeight) || geometry.pixelWidth<1 || geometry.pixelHeight<1 ||
      geometry.physicalBounds.width!==geometry.pixelWidth || geometry.physicalBounds.height!==geometry.pixelHeight ||
      typeof screen.screenToDipRect!=='function' || typeof screen.getAllDisplays!=='function') return null;
  let result;
  // Null chooses the display nearest the physical rect. The durable hidden
  // overlay may still be on another monitor and must not supply its old DPI.
  try{result=screen.screenToDipRect(null,geometry.physicalBounds);}catch{return null;}
  if (!rectangle(result)) return null;
  // Keep the owned window inside the converted source rectangle. Independent
  // rounding of width/height can expand its physical right/bottom at fractional
  // scales. The renderer scales the complete PNG and normalized marks into this
  // slightly smaller read-only canvas; no source pixels are cropped.
  const x=Math.ceil(result.x),y=Math.ceil(result.y);
  const bounds={x,y,width:Math.floor(result.x+result.width)-x,height:Math.floor(result.y+result.height)-y};
  if (!rectangle(bounds)) return null;
  // A mixed-DPI spanning image cannot be mapped using a single global scale.
  let displays;
  try{displays=screen.getAllDisplays();}catch{return null;}
  // Check the unrounded source too: inward rounding must not conceal a source
  // spanning two displays, even by a fraction of one DIP.
  const display=displays.find(display=>rectangle(display.bounds)&&result.x>=display.bounds.x&&result.y>=display.bounds.y&&
    result.x+result.width<=display.bounds.x+display.bounds.width&&result.y+result.height<=display.bounds.y+display.bounds.height);
  return display ? {bounds,displayId:String(display.id)} : null;
}
function popupBounds(screen, anchor, size={width:380,height:176},position) {
  const cursor=rectangle(position)?{x:position.x+position.width/2,y:position.y+position.height/2}:
    anchor && Number.isFinite(anchor.x) && Number.isFinite(anchor.y) ? anchor : screen.getCursorScreenPoint();
  const area=screen.getDisplayNearestPoint(cursor).workArea;
  if (!rectangle(area)) throw new Error('A usable display is required');
  const width=Math.min(size.width,Math.floor(area.width)),height=Math.min(size.height,Math.floor(area.height));
  const clamp=(n,min,max)=>Math.min(Math.max(n,min),max);
  return {x:Math.round(clamp(position?.x??cursor.x+16,area.x,area.x+area.width-width)),
    y:Math.round(clamp(position?.y??cursor.y-height-12,area.y,area.y+area.height-height)),width,height};
}
function quickHtml(role,locale='zh-CN') {
  const overlay=role==='overlay';
  return `<!doctype html><html lang="${locale}"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; img-src data:; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-src 'none'"><title>${t('common',overlay?'window.annotation':'window.quick',locale,{short_name:SHORT_NAME})}</title><style>
:root{color-scheme:light;font-family:"Segoe UI","Microsoft YaHei",sans-serif;color:#36393c;background:transparent;--ink:#36393c;--muted:#616973;--line:#dfe1e3;--paper:#f5f6f8;--window-radius:0px}*{box-sizing:border-box}html,body{margin:0;width:100%;height:100%;overflow:hidden;background:transparent}button,textarea{font:inherit;caret-color:var(--ink)}button{cursor:pointer;border:0;min-height:36px;padding:7px 10px;border-radius:9px;color:var(--ink);background:transparent;transition:background 180ms ease,transform 180ms cubic-bezier(.16,1,.3,1);white-space:nowrap}button:hover{background:#e7e9eb}button:active{transform:scale(.97)}button:disabled{opacity:.48;cursor:default;transform:none}button:focus-visible,textarea:focus-visible{outline:2px solid #616973;outline-offset:2px}button.primary{background:var(--ink);color:white}button.primary:hover{background:#4a4e52}button svg{width:18px;height:18px;stroke:currentColor;stroke-width:1.7;stroke-linecap:round;stroke-linejoin:round;fill:none;vertical-align:middle}::selection{background:#dfe1e3;color:#36393c}*{scrollbar-color:#b8bdc2 transparent;scrollbar-width:thin}[hidden]{display:none!important}
.sheet{height:100%;padding:14px 16px 12px;background:var(--paper);border:1px solid var(--line);border-radius:var(--window-radius);display:flex;flex-direction:column;gap:10px;}header{display:flex;align-items:center;gap:6px;flex-shrink:0}h1{font-size:15px;font-weight:600;line-height:24px;margin:0;flex:1}header button{width:34px;min-height:32px;padding:5px}.source{display:flex;align-items:center;gap:8px;min-height:26px;color:var(--muted);font-size:12px}.source span:first-child{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.source button{min-height:28px;padding:3px 6px;font-size:12px}.source-confirm{font-size:12px;line-height:1.6;background:#fff;padding:8px 10px;border:1px solid var(--line);border-radius:9px}.source-confirm>div{display:flex;justify-content:flex-end;gap:4px}.source-confirm button{font-size:12px;min-height:28px;padding:4px 6px}.preview{position:relative;max-height:125px;min-height:0;overflow:hidden;border-radius:8px;background:#e4e6e8;flex-shrink:0}.preview img{display:block;width:100%;max-height:125px;object-fit:contain}.preview svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.editor{display:flex;flex-direction:column;gap:8px;min-height:0;flex:1}.editor textarea{display:block;resize:none;flex:1;min-height:82px;width:100%;border:1px solid var(--line);border-radius:10px;background:#fff;color:var(--ink);padding:11px 12px;font-size:14px;line-height:1.65}.editor textarea::placeholder{color:#616973}.controls{display:flex;align-items:center;justify-content:space-between;gap:6px;font-size:13px}.actions{display:flex;align-items:center;gap:6px;margin-left:auto}.status{margin:0;font-size:12px;line-height:1.55;color:var(--muted);overflow-wrap:anywhere;flex-shrink:0}.status[data-error=true]{color:#8d3f35}.question{margin:0;font-size:12px;line-height:1.6;color:var(--muted);max-height:58px;overflow:auto;flex-shrink:0;white-space:pre-wrap;overflow-wrap:anywhere}.reply{flex:1;white-space:normal;overflow:auto;min-height:0;max-height:none;font-size:14px;line-height:1.7;margin:0;border-top:1px solid var(--line);padding-top:10px;user-select:text;overflow-wrap:anywhere}.files{display:flex;flex-direction:column;gap:4px;max-height:95px;overflow:auto}.files button{flex-shrink:0;text-align:left;white-space:normal;overflow-wrap:anywhere;font-size:13px}.foot{display:flex;align-items:center;justify-content:space-between;gap:6px;border-top:1px solid var(--line);padding-top:5px;flex-shrink:0;color:var(--muted);font-size:12px}.foot button{font-size:12px;min-height:28px;padding:4px 6px}
.ask-panel{display:contents}.activity-panel{display:flex;flex-direction:column;min-height:0;flex:1;gap:9px}.activity-panel header h2{flex:1;font-size:14px;line-height:24px;font-weight:600;margin:0}.activity-status,.activity-error{font-size:11px;line-height:1.6;color:var(--muted);margin:0;overflow-wrap:anywhere}.activity-error{color:#993f3e}.activity-list{min-height:0;flex:1;overflow:auto;scrollbar-width:thin}.activity-list h3{font-size:11px;font-weight:500;color:var(--muted);margin:10px 0 3px}.activity-list h3:first-child{margin-top:0}.activity-task{display:flex;flex-direction:column;gap:3px;width:100%;text-align:left;white-space:normal;padding:7px 5px;border-radius:6px;border-bottom:1px solid var(--line)}.activity-task strong{font-size:12px;font-weight:500;line-height:1.5;overflow-wrap:anywhere}.activity-task small{font-size:10px;line-height:1.5;color:var(--muted);overflow-wrap:anywhere}.activity-empty{font-size:12px;color:var(--muted);margin:20px 0}.activity-panel footer{display:flex;justify-content:space-between;gap:7px;border-top:1px solid var(--line);padding-top:7px}.activity-panel footer button{font-size:12px;min-height:29px;padding:4px 8px}.text-marks{position:absolute;inset:0;pointer-events:none;overflow:hidden}.text-mark{position:absolute;pointer-events:auto;max-width:40%;max-height:35%;overflow:auto;padding:3px 6px;min-height:0;border:1px solid #c34838;background:#fff7f3;color:#9a3428;border-radius:5px;white-space:pre-wrap;overflow-wrap:anywhere;text-align:left;font-size:14px;line-height:1.5;cursor:text}.preview .text-mark{font-size:10px;padding:1px 3px;pointer-events:none}.text-editor{position:absolute;z-index:3;width:min(320px,calc(100% - 16px));padding:9px;background:#f5f6f8;border:1px solid #c34838;border-radius:10px;box-shadow:0 5px 18px #36393c24;cursor:default;user-select:text}.compact{padding:9px 12px 10px;gap:6px}.compact header{min-height:24px;gap:4px}.compact header button{min-height:24px;width:auto;min-width:25px;padding:1px 5px;font-size:13px}.compact header button:last-child{font-size:21px;line-height:20px}.context-indicator{font-size:11px;color:var(--muted);flex:1}.mark-count{font-size:11px;color:#9a3428}.compact .text-editor{position:relative;width:100%;flex-shrink:0}.compact .editor{gap:5px}.compact .editor textarea{min-height:48px;padding:7px 9px;font-size:14px;line-height:1.55}.compact .controls button{font-size:12px;min-height:29px;padding:4px 8px}.compact .status{font-size:11px;line-height:1.5;max-height:48px;overflow:auto}.compact .status[data-submission=true]{max-height:84px}.compact .question{font-size:12px;max-height:44px}.compact .reply{padding-top:7px}.file-context{display:flex;align-items:center;gap:5px;min-height:22px;flex-shrink:0;padding:1px 6px;background:#e9ebed;border-radius:7px;font-size:11px;color:var(--muted)}.file-context span{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.file-context button{min-height:22px;padding:0 4px;font-size:15px}#work-context{font-size:14px;line-height:24px;padding:4px 8px;min-height:32px;max-height:80px;align-items:flex-start}#work-context span{white-space:normal;overflow-wrap:anywhere;max-height:72px;overflow:auto}#work-context[data-unavailable="true"]{font-weight:600}.result-actions{display:flex;justify-content:flex-end;gap:4px;flex-shrink:0}.result-actions:has(button:not([hidden])){padding-top:3px}.result-actions button{font-size:12px;min-height:26px;padding:3px 6px}.text-summary{font-size:12px;line-height:1.6;white-space:pre-wrap;overflow-wrap:anywhere;max-height:54px;overflow:auto;color:#9a3428;background:#fff7f3;border-radius:6px;padding:6px 8px;flex-shrink:0}.text-editor textarea{width:100%;height:86px;resize:none;border:1px solid var(--line);border-radius:6px;padding:7px;background:#fff;color:var(--ink);font:14px/1.5 "Segoe UI","Microsoft YaHei",sans-serif}.text-editor footer{display:flex;align-items:center;justify-content:flex-end;gap:3px;margin-top:5px}.text-editor small{flex:1;color:var(--muted);font-size:11px}.text-editor button{font-size:12px;min-height:30px;padding:4px 7px}.canvas{position:absolute;inset:0;touch-action:none;user-select:none;cursor:crosshair;background:#fff}.canvas>.text-editor{max-height:calc(100% - 16px);overflow-y:auto}.canvas>img{position:absolute;inset:0;display:block;width:100%;height:100%;pointer-events:none}.canvas>svg{position:absolute;inset:0;width:100%;height:100%;pointer-events:none}.toolbar{position:absolute;left:50%;bottom:14px;transform:translateX(-50%);max-width:calc(100% - 16px);max-height:calc(100% - 28px);overflow-y:auto;display:flex;flex-wrap:wrap;justify-content:center;gap:3px;padding:6px;background:#f5f6f8;border:1px solid var(--line);border-radius:12px;box-shadow:0 5px 18px #36393c24;user-select:none}.toolbar button{font-size:12px;min-width:36px;min-height:36px;padding:6px 8px}.toolbar button[aria-pressed=true]{color:#fff;background:#36393c}.overlay-status{position:absolute;top:12px;left:12px;right:12px;width:max-content;max-width:calc(100% - 24px);padding:7px 11px;color:#36393c;background:#f5f6f8;border:1px solid var(--line);border-radius:9px;font-size:12px;pointer-events:none}@keyframes enter{from{opacity:.65;transform:translateY(5px)}to{opacity:1;transform:none}}@media(prefers-reduced-motion:reduce){*{animation:none!important;transition:none!important}}@media(max-height:390px){.preview{max-height:80px}.preview img{max-height:80px}.sheet{gap:6px;padding:10px 12px}.reply{max-height:none}}
.compact header .chat-pin,.activity-panel header .chat-pin{width:28px;min-width:28px;height:28px;min-height:28px;padding:4px;display:grid;place-items:center;flex:none}.chat-pin svg{width:20px;height:20px;fill:none;stroke:currentColor;stroke-width:1.5;stroke-linejoin:round;stroke-linecap:round}.chat-pin[aria-pressed=true]{background:#dfe4e8;color:#303842}.compact header,.activity-panel header{-webkit-app-region:drag}.compact header button,.activity-panel header button{-webkit-app-region:no-drag}#review-choice{font-size:12px}#review-picker{display:grid;gap:8px;margin:4px 0}#review-picker label{display:grid;grid-template-columns:40px minmax(0,1fr);align-items:center;gap:8px}#review-picker select{min-width:0;width:100%;font:inherit;height:32px;border:1px solid var(--line);background:var(--paper);color:var(--ink);border-radius:6px}#review-consent{display:flex;align-items:center;gap:8px;min-height:32px}#review-consent input{width:18px;height:18px;margin:0;flex:none}#review-note{margin:4px 0;overflow-wrap:anywhere}#review-pending{max-height:min(360px,calc(100dvh - 224px));overflow-y:auto;display:grid;gap:8px}#review-pending p{margin:4px 0;overflow-wrap:anywhere}#review-pending label{display:flex;align-items:flex-start;gap:8px}#review-pending input[type=radio],#review-pending input[type=checkbox]{width:18px;height:18px;flex:none}.canonical-stream{display:flex;flex-direction:column;gap:12px;flex:1;min-height:0;overflow:auto;overscroll-behavior:contain;scrollbar-width:thin}.canonical-stream #reply{flex:none;overflow:visible;max-height:none;border:0;padding:0}.canonical-stream #control-history>div{max-height:none;overflow:visible}.canonical-stream #control-history>summary{display:none}.canonical-stream #question,.canonical-stream .canonical-control-input{font-size:14px;line-height:1.65;color:var(--ink);margin:0 0 0 16px;padding:8px 10px;border:1px solid var(--line);border-radius:10px}.canonical-stream .canonical-control-reply{font-size:14px;line-height:1.7}.canonical-chat .editor{flex:none}.canonical-chat .editor textarea{flex:none;height:72px;min-height:72px}
#control-history{font-size:12px}#control-history>div{max-height:128px;overflow:auto}#control-history p{white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0 4px}#control-history small{color:var(--muted)}
.review-permission-targets{display:grid;gap:4px;max-height:80px;overflow:auto;min-width:0;margin-block:8px}.review-permission-targets code{white-space:pre-wrap;overflow-wrap:anywhere;font:inherit}.review-permission-details{min-width:0;margin-block:8px}.review-permission-details pre{max-height:160px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font:12px/1.5 monospace;padding:8px;border:1px solid var(--line);border-radius:8px}
#review-guidance textarea{width:100%;height:56px;font:inherit;resize:none;border:1px solid var(--line);background:var(--paper);color:var(--ink)}
.reply>:first-child{margin-top:0}.reply>:last-child{margin-bottom:0}.reply p{margin:0 0 10px}.reply h3,.reply h4,.reply h5,.reply h6{font-size:14px;line-height:1.6;margin:16px 0 6px;font-weight:650}.reply ul,.reply ol{padding-left:22px;margin:6px 0 12px}.reply li{padding-left:2px;margin:3px 0}.reply li>p{margin:4px 0}.reply blockquote{margin:10px 0;padding-left:10px;border-left:2px solid #b8bdc2;color:var(--muted)}.reply hr{border:0;border-top:1px solid var(--line);margin:14px 0}.reply code{font-family:Consolas,monospace;font-size:12px;background:#e9ebed;padding:1px 4px;border-radius:3px}.reply pre{max-width:100%;overflow-x:auto;white-space:pre;overflow-wrap:normal;padding:10px;background:#e9ebed;border-radius:6px;margin:10px 0;line-height:1.6}.reply pre code{padding:0;background:none;border-radius:0}.reply-table{max-width:100%;overflow-x:auto;margin:10px 0}.reply table{width:100%;border-collapse:collapse;font-size:12px;line-height:1.6}.reply th,.reply td{min-width:76px;padding:7px 8px;vertical-align:top;text-align:left;border:1px solid var(--line)}.reply th{font-weight:600;background:#e9ebed}.reply a{color:#385c7a;text-underline-offset:3px}.reply:focus-visible,.reply-table:focus-visible,.reply pre:focus-visible{outline:2px solid #616973;outline-offset:-2px}.reply[data-format=text]{white-space:pre-wrap}.result-actions{flex-wrap:wrap}.status{max-height:70px;overflow-y:auto}.files{flex-shrink:0}
#review-pending article{min-width:0;display:grid;gap:12px;padding:12px;border:1px solid var(--line);border-radius:12px;font:13px/1.6 "Segoe UI","Microsoft YaHei",sans-serif}#review-pending fieldset{min-width:0;display:grid;gap:8px;border:0;margin:0;padding:0}#review-pending legend{padding:0;margin-bottom:8px;overflow-wrap:anywhere;font:inherit}#review-pending fieldset>input:not([type=checkbox]):not([type=radio]){box-sizing:border-box;display:block;width:100%;min-width:0;min-height:40px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);font:inherit}.review-pending-buttons{display:flex;flex-wrap:wrap;gap:8px}.review-pending-buttons button{min-height:36px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;font:inherit}#review-picker input,#review-picker select{font:inherit}
.review-capability-input{box-sizing:border-box;max-height:144px;overflow:auto;white-space:pre-wrap;overflow-wrap:anywhere;font-size:12px;line-height:1.5;padding:12px;border:1px solid var(--line);border-radius:8px}#review-pending article>small{display:block;margin-top:4px;color:var(--muted)}#capability-picker{grid-column:1/-1;min-width:0}#capability-search{box-sizing:border-box;width:100%;min-height:36px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink)}#capability-tools{max-height:176px;overflow:auto;margin-top:8px;display:grid;gap:8px}#capability-tools fieldset{min-width:0;border:0;margin:0;padding:0;display:grid;gap:4px}#capability-tools legend{font-size:12px;color:var(--muted);padding:4px 0}#capability-tools label{display:flex;align-items:center;gap:8px;min-height:32px;padding:4px 8px;overflow-wrap:anywhere}#capability-tools input{flex:0 0 auto}#review-remote-consent{display:flex;align-items:center;gap:8px;font-size:12px;margin:8px 0}
.activity-panel{gap:8px}.activity-content{min-height:0;flex:1;overflow:auto;overscroll-behavior:contain;scrollbar-width:thin}.activity-content>details>summary{padding:4px 0;font-size:12px;color:var(--muted);cursor:pointer}.activity-content .activity-list{max-height:160px;overflow:auto}.supervisor-history-tools{position:sticky;top:0;z-index:1;background:var(--paper);display:flex;flex-wrap:wrap;gap:4px 8px;padding-block:4px;overflow-anchor:none}.supervisor-history-tools button{font-size:12px;min-height:28px;padding:4px 8px}.supervisor-history-hint{flex-basis:100%;min-height:40px;margin:0;font-size:12px;line-height:20px;color:var(--muted)}.supervisor-history{overflow-anchor:none;display:grid;gap:16px;padding:12px 0 4px}.supervisor-message{min-width:0}.supervisor-message>small{display:block;font-size:11px;line-height:16px;color:var(--muted);margin-bottom:4px}.supervisor-message-body{font-size:13px;line-height:1.7;white-space:pre-wrap;overflow-wrap:anywhere}.supervisor-message-body p{margin:0 0 8px}.supervisor-message-body>*:last-child{margin-bottom:0}.supervisor-message-body pre{white-space:pre-wrap;overflow-wrap:anywhere}.supervisor-message button{font-size:12px;padding:4px 8px;min-height:28px;margin-top:4px}.supervisor-compose{display:grid;gap:8px;flex:none;border-top:1px solid var(--line);padding-top:12px}.supervisor-compose textarea{display:block;box-sizing:border-box;width:100%;min-width:0;min-height:56px;max-height:88px;resize:vertical;border:1px solid var(--line);border-radius:10px;padding:8px 12px;background:#fff;color:var(--ink);font:13px/20px "Segoe UI","Microsoft YaHei",sans-serif}.supervisor-compose textarea:focus{outline:2px solid #899aa9;outline-offset:2px}.supervisor-actions{display:flex;flex-wrap:wrap;align-items:center;gap:8px;justify-content:flex-end}.supervisor-actions button{font-size:12px;min-height:32px;padding:4px 12px}.supervisor-actions>button:first-child{margin-right:auto;color:var(--muted)}.supervisor-conflict{display:grid;gap:8px;font-size:12px;line-height:1.6}.supervisor-conflict>div{display:flex;gap:8px;flex-wrap:wrap}.supervisor-conflict button{font-size:12px;min-height:28px;padding:4px 8px}.supervisor-message-state{color:var(--muted);font-size:11px;line-height:16px;margin:4px 0}#supervisor-error{max-height:52px;overflow:auto}@media(max-height:380px){.activity-content .activity-list{max-height:88px}.supervisor-compose{padding-top:8px}.supervisor-compose textarea{min-height:48px}}
</style></head><body data-role="${role}" data-ui-locale="${locale}" data-i18n="${htmlCatalog('host')}">${overlay ? `<main id="canvas" class="canvas" aria-label="在冻结的当前页面上批注" data-ui-aria-label="在冻结的当前页面上批注"><svg id="marks" viewBox="0 0 1 1" preserveAspectRatio="none"></svg><div id="text-marks" class="text-marks"></div><section id="text-editor" class="text-editor" hidden aria-label="编辑图上文字" data-ui-aria-label="编辑图上文字"><textarea id="text-value" maxlength="64000" aria-label="图上文字标记" placeholder="写明这里的问题或修改意见" data-ui-aria-label="图上文字标记" data-ui-placeholder="写明这里的问题或修改意见"></textarea><footer><small id="text-count">0 / 2000</small><button data-text-command="text-delete" data-ui-key="删除">删除</button><button data-text-command="text-cancel" data-ui-key="取消">取消</button><button data-text-command="text-confirm" class="primary" data-ui-key="确认">确认</button></footer></section></main><p class="overlay-status" data-ui-key="在冻结画面上圈画或写字 · Enter 完成批注">在冻结画面上圈画或写字 · Enter 完成批注</p><nav class="toolbar" aria-label="批注工具" data-ui-aria-label="批注工具"><button data-tool="rect" aria-label="矩形框选" data-ui-key="框选" data-ui-aria-label="矩形框选">框选</button><button data-tool="freehand" aria-label="自由圈画" data-ui-key="圈画" data-ui-aria-label="自由圈画">圈画</button><button data-tool="ellipse" aria-label="椭圆标记" data-ui-key="椭圆" data-ui-aria-label="椭圆标记">椭圆</button><button data-tool="arrow" aria-label="箭头标记" data-ui-key="箭头" data-ui-aria-label="箭头标记">箭头</button><button data-tool="text" aria-label="文字标记" data-ui-key="文字" data-ui-aria-label="文字标记">文字</button><button data-command="undo" data-ui-key="撤销">撤销</button><button data-command="redo" data-ui-key="重做">重做</button><button data-command="clear" data-ui-key="清空">清空</button><button data-command="dismiss" data-ui-key="取消">取消</button><button class="primary" data-command="review" data-ui-key="确认标记">确认标记</button></nav>` : `<main class="sheet compact"><div id="ask-panel" class="ask-panel"><header><h1 id="heading" hidden data-ui-key="问当前页">问当前页</h1><span id="source-label" hidden></span><span id="context-indicator" class="context-indicator" title="后台读取当前页" data-ui-key="当前页" data-ui-title="后台读取当前页">当前页</span><span id="mark-count" class="mark-count" hidden></span><button id="toggle-preview" hidden title="手动查看冻结依据" data-ui-key="依据" data-ui-title="手动查看冻结依据">依据</button><button data-command="pin" class="chat-pin" aria-label="固定轻量协作窗口" title="固定轻量协作窗口" aria-pressed="false"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3h6l-1 5 3 3v1H5v-1l3-3-1-5Z M10 12v5"/></svg></button><button data-command="recapture" title="重新读取当前页" aria-label="重新读取当前页" data-ui-aria-label="重新读取当前页" data-ui-title="重新读取当前页">↻</button><button data-command="dismiss" aria-label="收起快捷问答" title="收起" data-ui-aria-label="收起快捷问答" data-ui-title="收起">×</button></header><div id="work-context" class="file-context" hidden><span id="work-context-label" tabindex="0"></span></div><div id="file-context" class="file-context" hidden><span id="file-context-label"></span><button data-command="file-clear" title="仅这条问题不引用文稿" aria-label="清除此条文稿引用" data-ui-aria-label="清除此条文稿引用" data-ui-title="仅这条问题不引用文稿">×</button></div><div id="retake-confirm" class="source-confirm" hidden><span data-ui-key="保留文字，清除标记并读取当前页？">保留文字，清除标记并读取当前页？</span><div><button data-command="recapture-cancel" data-ui-key="取消">取消</button><button data-command="recapture-confirm" data-ui-key="确认换页">确认换页</button></div></div><div id="preview" class="preview" hidden><svg id="marks" viewBox="0 0 1 1" preserveAspectRatio="none"></svg></div><p id="question" class="question" hidden></p><details id="control-history" hidden><summary data-ui-key="补充记录">补充记录</summary><div></div></details><div id="text-summary" class="text-summary" hidden aria-label="图上文字意见" data-ui-aria-label="图上文字意见"></div><section id="text-editor" class="text-editor" hidden aria-label="编辑图上文字" data-ui-aria-label="编辑图上文字"><textarea id="text-value" maxlength="64000" aria-label="图上文字标记" placeholder="写明这里的问题或修改意见" data-ui-aria-label="图上文字标记" data-ui-placeholder="写明这里的问题或修改意见"></textarea><footer><small id="text-count">0 / 2000</small><button data-text-command="text-delete" data-ui-key="删除">删除</button><button data-text-command="text-cancel" data-ui-key="取消">取消</button><button data-text-command="text-confirm" class="primary" data-ui-key="确认">确认</button></footer></section><section id="review-choice"><button data-command="review-catalog" data-ui-key="技能与工具">技能与工具</button><button data-command="review-clear" hidden data-ui-key="移除选择">移除选择</button><div id="review-picker" hidden><label><span data-ui-key="技能">技能</span><select id="review-skill" aria-label="技能" data-ui-aria-label="技能"></select></label><label id="review-legacy-tool"><span data-ui-key="工具">工具</span><select id="review-tool" aria-label="工具" data-ui-aria-label="工具"></select></label><div id="capability-picker" hidden><input id="capability-search" type="search" aria-label="搜索能力" placeholder="搜索能力" data-ui-aria-label="搜索能力" data-ui-placeholder="搜索能力"/><div id="capability-tools" aria-label="工具" data-ui-aria-label="工具"></div></div></div><label id="review-consent" hidden><input type="checkbox" id="review-process"/><span data-ui-key="允许启动所选本地工具">允许启动所选本地工具</span></label><label id="review-remote-consent" hidden><input type="checkbox" id="review-remote"/><span data-ui-key="允许连接所选远端工具">允许连接所选远端工具</span></label><p id="review-note" hidden></p></section><section id="review-pending" hidden></section><section id="review-guidance" hidden><textarea id="guidance" maxlength="16000" aria-label="补充本页" data-ui-aria-label="补充本页"></textarea><button data-command="review-guidance" data-ui-key="补充本页">补充本页</button></section><section id="editor" class="editor"><textarea id="comment" aria-label="问当前页" placeholder="问当前页" maxlength="16000" data-ui-aria-label="问当前页" data-ui-placeholder="问当前页"></textarea><div class="controls"><button data-command="annotate" title="在冻结画面上批注" data-ui-key="圈选" data-ui-title="在冻结画面上批注">圈选</button><div class="actions"><button data-command="draft" hidden data-ui-key="加入草稿">加入草稿</button><button class="primary" data-command="send" data-ui-key="发送">发送</button></div></div></section><p id="status" class="status" role="status" aria-live="polite" hidden></p><section id="reply" class="reply" tabindex="0" aria-label="对话" data-ui-aria-label="对话" hidden></section><div id="files" class="files" hidden></div><footer class="result-actions"><button data-command="expand" hidden data-ui-key="对话">对话</button><span id="keyboard-hint" hidden></span><button data-command="stop" hidden data-ui-key="停止">停止</button><button data-command="inspect" hidden data-ui-key="查看进展">查看进展</button><button data-command="review-continue" hidden data-ui-key="继续编辑">继续编辑</button><button data-command="capability-continue" hidden data-ui-key="继续任务">继续任务</button><button data-command="new-question" hidden data-ui-key="新问题">新问题</button></footer></div><section id="activity-panel" class="activity-panel" hidden aria-label="任务动态" data-ui-aria-label="任务动态"><header><h2 id="activity-heading" data-ui-key="任务动态">任务动态</h2><button data-command="pin" class="chat-pin" aria-label="固定轻量协作窗口" title="固定轻量协作窗口" aria-pressed="false"><svg viewBox="0 0 20 20" aria-hidden="true"><path d="M7 3h6l-1 5 3 3v1H5v-1l3-3-1-5Z M10 12v5"/></svg></button><button data-command="activity-refresh" aria-label="读取任务动态" title="读取现有状态" data-ui-aria-label="读取任务动态" data-ui-title="读取现有状态">↻</button><button data-command="dismiss" aria-label="收起任务动态" title="收起" data-ui-aria-label="收起任务动态" data-ui-title="收起">×</button></header><p id="activity-status" class="activity-status" role="status"></p><p id="activity-error" class="activity-error" role="alert" hidden></p><div class="activity-content"><details id="supervisor-tasks" open><summary id="supervisor-tasks-label">任务</summary><div id="activity-list" class="activity-list"></div></details><section id="supervisor-history" class="supervisor-history" aria-live="polite" hidden></section></div><form id="supervisor-compose" class="supervisor-compose" hidden><textarea id="supervisor-input" maxlength="16000" rows="2"></textarea><div id="supervisor-conflict" class="supervisor-conflict" hidden><span id="supervisor-conflict-label"></span><div><button id="supervisor-keep" type="button"></button><button id="supervisor-remote" type="button"></button></div></div><p id="supervisor-error" class="activity-error" role="status" hidden></p><div id="supervisor-save-recovery" class="supervisor-actions" hidden><button id="supervisor-check-save" type="button"></button><button id="supervisor-retry-save" type="button"></button></div><div class="supervisor-actions"><button id="supervisor-local" type="button"></button><button id="supervisor-inspect" type="button" hidden></button><button data-command="supervisor-configure" id="supervisor-configure" type="button" hidden></button><button id="supervisor-send" class="primary" type="submit"></button></div></form><footer><button data-command="activity-back" data-ui-key="返回提问">返回提问</button><button data-command="activity-supervisor" data-ui-key="问总管">问总管</button><button data-command="activity-all" data-ui-key="查看全部">查看全部</button></footer></section></main>`}</body></html>`;
}

class QuickSurface {
  constructor({BrowserWindow,screen,ipcMain,locale='zh-CN',onActivationState,onEmptyCapture,onCapture,onDraft,onContinuationDraft,onPrepareSend,onSend,onInspect,onExpand,onOpenArtifact,onOpenExternal,onLoadDraft,onLoadReadonlyResult,onCancel,onClearFileContext,onRetainSubmitted,onReviewCatalog,onReviewControl,
    onError=()=>{},onState=()=>{},isClosing=()=>false,onOpenTask,onExpandActivity,onRefreshActivity,onOpenSupervisor,onGetSupervisorConversation,onSaveSupervisorDraft,onAskSupervisor,onInspectSupervisor,onSaveSupervisorLocalAnswer,onReadSupervisorHistory,onReadSupervisorOperation,pinned=false,onSetPinned}) {
    if (typeof BrowserWindow!=='function'||!screen||!ipcMain||[onCapture,onDraft,onSend,onInspect,onExpand,onOpenArtifact].some(fn=>typeof fn!=='function')) {
      throw new Error('Quick surface requires explicit owned host callbacks');
    }
    Object.assign(this,{screen,ipcMain,onCapture,onDraft,onContinuationDraft,onPrepareSend,onSend,onInspect,onExpand,onOpenArtifact,onOpenExternal,onLoadDraft,onLoadReadonlyResult,onCancel,onClearFileContext,onRetainSubmitted,onReviewCatalog,onReviewControl,onError,onState,isClosing,onOpenTask,onExpandActivity,onRefreshActivity,onOpenSupervisor,onGetSupervisorConversation,onSaveSupervisorDraft,onAskSupervisor,onInspectSupervisor,onSaveSupervisorLocalAnswer,onReadSupervisorHistory,onReadSupervisorOperation});
    this.locale=resolveLocale(locale,locale);
    this.activation=new QuickActivation(onActivationState);this.openSequence=0;
    this.onEmptyCapture=onEmptyCapture;this.discussionKey=null;this.scopeSwitch=null;this.scopeSwitching=false;this.scopeSwitchFailed=false;
    if(typeof pinned!=='boolean')throw new Error('Explicit chat pin preference required');
    this.pinned=pinned;this.onSetPinned=onSetPinned;this.pinBusy=false;
    this.quickUserBounds=null;this.quickAppliedBounds=null;this.revealSequence=0;this.motionPhase='HIDDEN';this.collapse=null;
    this.disposed=false;this.loaded=false;this.epoch=0;this.geometryEpoch=0;this.visible=false;this.overlayVisible=false;this.kind='ask';
    this.roleLoaded={quickPopover:false,annotationOverlay:false};this.roleLoads={};this.roleFailed={};this.loadFault=false;
    this.error='';this.notice='';this.unread=false;this.capturePending=false;this.draft=null;this.anchor=null;this.autosave=null;
    this.overlayPreparing=null;
    this.rendererConnected={};this.editorFlush=null;this.handoffBusy=false;this.suspendedDraft=null;
    this.previewExpanded=false;
    this.supervisorSnapshot=null;this.supervisorProfile=null;this.supervisorSelection=null;this.supervisorSelectionRevision=0;
    this.presentation='ASK';this.activityRevision=0;this.activity=activityProjection(null,0);this.activityBusy=false;this.activityRequest=0;
    this.urls={};this.windows={};
    for (const [name,role] of [['quickPopover','popover'],['annotationOverlay','overlay']]) {
      this.urls[name]='data:text/html;charset=utf-8,'+encodeURIComponent(quickHtml(role,this.locale));
      const bounds=role==='popover'?popupBounds(screen):{x:0,y:0,width:2,height:2};
      const window=new BrowserWindow({...bounds,title:t('common',role==='popover'?'window.quick':'window.annotation',this.locale,{short_name:SHORT_NAME}),show:false,
        frame:false,transparent:true,roundedCorners:false,backgroundColor:'#00000000',hasShadow:role==='popover',resizable:false,
        minimizable:false,maximizable:false,fullscreenable:false,skipTaskbar:true,alwaysOnTop:role==='overlay'||pinned,focusable:true,
        webPreferences:{preload:path.join(__dirname,'quick-surface-preload.bundle.cjs'),nodeIntegration:false,contextIsolation:true,sandbox:true,webviewTag:false}});
      this.windows[name]=window;
      if(role==='popover')window.on('move',()=>{
        if(!this.visible||this.disposed)return;
        const actual=window.getBounds();
        if(rectangle(actual)&&this.quickAppliedBounds&&['x','y'].some(key=>actual[key]!==this.quickAppliedBounds[key]))this.quickUserBounds={...actual};
      });
      window.setContentProtection(false);
      window.webContents.setWindowOpenHandler(()=>({action:'deny'}));
      window.webContents.on('will-navigate',(event,url)=>{if(url!==this.urls[name])event.preventDefault();});
      window.webContents.on('will-redirect',event=>event.preventDefault());
      window.webContents.on('render-process-gone',()=>this._fatal(new Error('快捷窗口暂不可用，请从 Agent 继续。')));
    window.on('close',event=>{if(!this.disposed){event.preventDefault();void this.hide().catch(()=>{});}});
      window.on('closed',()=>{if(!this.disposed)this._fatal(new Error('已绑定的快捷窗口意外关闭。'));});
    }
    this._handler=(event,payload)=>this._receive(event,payload);
    ipcMain.handle(CHANNEL,this._handler);
    this._displayChanged=()=>{
      if(this.disposed)return;
      this.geometryEpoch++;
      const drawing=this.overlayVisible||this.overlayPreparing;this.windows.annotationOverlay.hide();this.overlayVisible=false;this.overlayPreparing=null;
      if(this.draft)this.draft.geometryInvalidated=true;
      if(drawing){this.error='显示器已变化，请重新读取页面后再圈选。';this._showQuick(false);}
      if(this.visible)this._positionQuick();
      this._publish();
    };
    screen.on?.('display-removed',this._displayChanged);screen.on?.('display-metrics-changed',this._displayChanged);
    this.ready=this._loadRole('quickPopover').then(()=>this.get(),error=>{this._fatal(error);return this.get();});
  }
  _loadRole(name){
    if(this.roleLoads[name])return this.roleLoads[name];
    const window=this.windows[name];
    this.roleLoads[name]=Promise.resolve().then(()=>{
      if(!this._alive()||this.loadFault||window.isDestroyed())return false;
      return window.loadURL(this.urls[name]).then(()=>true);
    }).then(loaded=>{
      if(!loaded||!this._alive()||this.loadFault||window.isDestroyed())return false;
      this.roleLoaded[name]=true;if(name==='quickPopover')this.loaded=true;
      this._publish();return true;
    },error=>{this.roleFailed[name]=true;throw error;});
    // Keep a failed attempt: later presentation requests do not reload a
    // renderer implicitly. Two concurrent requests share this one load.
    return this.roleLoads[name];
  }
  setLocale(locale){this.locale=resolveLocale(locale,locale);if(!this.disposed){for(const [name,window]of Object.entries(this.windows))if(!window.isDestroyed())window.setTitle?.(t('common',name==='quickPopover'?'window.quick':'window.annotation',this.locale,{short_name:SHORT_NAME}));this._publish();}return this.get();}
  setPinned(enabled){
    if(typeof enabled!=='boolean')throw new Error('Explicit chat pin preference required');
    if(!this._alive())return this.get();
    this.pinned=enabled;
    const own=this.windows.quickPopover;
    if(!own.isDestroyed()&&(own.isAlwaysOnTop?.()===true)!==enabled)own.setAlwaysOnTop(enabled);
    this._publish();return this.get();
  }
  _tr(text,params={}){return typeof text==='string'?t('host',text,this.locale,params):text;}
  _newDraft() {
    return {id:'quick_'+randomUUID(),revision:0,comment:'',marks:[],capture:null,basisCapture:this.onEmptyCapture?.()??null,restored:false,operationId:null,
      phase:'EDITING',result:null,nativeReview:null,guidanceText:'',continuationRef:null,continuationBasis:null,continuationVersion:0,continuationTail:Promise.resolve(),reviewControl:null,saveTail:Promise.resolve(),savedSignature:null,savePending:0,touched:false,retakePending:false};
  }
  _alive() {return !this.disposed&&!this.isClosing();}
  _current(epoch,draft=this.draft) {return this._alive()&&this.epoch===epoch&&this.draft===draft;}
  _fail(error,message) {
    if(!this._alive())return;
    this.error=message;try{this.onError(error);}catch{}this._publish();
  }
  _fatal(error) {
    if(this.disposed)return;
    this.loaded=false;this.loadFault=true;this.roleLoaded.quickPopover=false;this.roleLoaded.annotationOverlay=false;this.error='快捷窗口暂不可用，请从 Agent 继续。';
    for(const window of Object.values(this.windows))if(!window.isDestroyed())window.hide();
    this.visible=false;this.overlayVisible=false;this.overlayPreparing=null;this.capturePending=false;this.epoch++;
    try{this.onError(error);}catch{}this._publish();
  }
  get() {
    const draft=this.draft,source=sourceView(draft?.capture);
    const guidance=guidanceScope(draft),guidanceEditable=!!draft?.operationId&&!draft.readonly&&!!(guidance&&(guidance.can_guide||guidance.can_status)||draft.guidanceText||draft.reviewControl);
    const reviewBusy=!!this.reviewBusy&&this.reviewBusyDraft===draft&&this.reviewBusyEpoch===this.epoch;
    const hintCatalog=draft?.nativeReview?.version===1&&this.reviewCatalog?.schema==='eeas.native-capability-task-capabilities.v2'?this.reviewCatalog.legacy_review_catalog:this.reviewCatalog;
    const expires=Date.parse(hintCatalog?.permission_hint?.expires_at??''),hintCurrent=!!hintCatalog?.permission_hint&&this.reviewHintCapture===draft?.capture&&this.reviewHintSelection===quickDigest(draft?.nativeReview?.selection??null)&&(!Number.isFinite(expires)||expires>Date.now());
    const required=!draft?.nativeReview?[]:hintCurrent?hintCatalog?.permission_hint?.required_effects??[]:draft?.nativeReview?.version===2?(draft.nativeReview.selection.mcp_tool_selection.length?['PROCESS_START','REMOTE_MCP_CONNECT']:[]):draft?.nativeReview?.selection.tool_selection?['PROCESS_START']:[];
    const processRequired=required.includes('PROCESS_START'),remoteRequired=required.includes('REMOTE_MCP_CONNECT');
    const historical=draft?.restored&&ref(draft.capture?.snapshot?.observation?.context_ref),workContext=workContextView(draft?.capture,draft?.nativeReview),metadataOnly=metadataStatusDraft(draft);
    const canConfirm=!!draft&&draft.phase==='EDITING'&&!this.capturePending&&(!historical||metadataOnly)&&!workContext.unavailable&&!this.scopeSwitching&&!this.scopeSwitchFailed&&
      (metadataOnly||this.kind!=='annotate'||!!source.image&&source.available)&&(metadataOnly||!draft.marks.length||!!source.image&&source.available&&!draft.restored)&&
      (!!draft.comment.trim()||draft.marks.length>0);
    return {locale:this.locale,loaded:this.loaded,roleLoaded:{...this.roleLoaded},disposed:this.disposed,epoch:this.epoch,kind:this.kind,visible:this.visible,
      pinned:this.windows.quickPopover.isAlwaysOnTop?.()===true,pinnedPreference:this.pinned,pinBusy:this.pinBusy,
      pinSupported:typeof this.onSetPinned==='function',pinLabel:this._tr(this.pinned?'取消固定轻量窗口':'固定轻量协作窗口'),
      motion:{sequence:this.revealSequence,phase:this.motionPhase,origin:this._motionOrigin()},
      overlayVisible:this.overlayVisible,overlayPreparing:this.overlayPreparing?.phase==='DECODING',overlayLoading:this.overlayPreparing?.phase==='LOADING',capturePending:this.capturePending,error:this._tr(this.scopeSwitchFailed?'讨论切换尚未完成，原稿仍保留。请完成输入后重试。':this.error),notice:this._tr(this.notice),unread:this.unread,
      previewExpanded:this.previewExpanded,
      presentation:this.presentation,activity:{...this.activity.public,items:this.activity.public.items.map(item=>({...item,status:this._tr(({RUNNING:'运行中',WAITING_USER:'等待补充',EXECUTION_RETURNED:'执行返回',EXECUTION_ERROR:'执行报错',TIMED_OUT:'超时，需核查',CANCEL_REQUESTED:'取消中',CANCELLED:'已取消',NEEDS_CHECK:'需核查'})[item.classification])+this._tr(item.observation_status==='STALE'?' · 观察过期':item.observation_status==='NEEDS_CHECK_AFTER_RESTART'?' · 重启后待核对':'')})),counts:{...this.activity.public.counts},busy:this.activityBusy,
        can_open:typeof this.onOpenTask==='function',can_expand:typeof this.onExpandActivity==='function',can_refresh:typeof this.onRefreshActivity==='function',can_ask:this._supervisorSupported()},
      supervisor:{supported:this._supervisorSupported(),history_supported:typeof this.onReadSupervisorHistory==='function',operation_read_supported:typeof this.onReadSupervisorOperation==='function',snapshot:this.supervisorSnapshot,providerProfileRef:this.supervisorProfile,selection:this.supervisorSelection,selection_revision:this.supervisorSelectionRevision,can_configure:typeof this.onOpenSupervisor==='function'},
      reviewSummary:draft?.nativeReview?[hintCatalog?.skills?.find(row=>quickDigest(row.config_ref??null)===quickDigest(draft.nativeReview.selection.skill_ref))?.title??draft.nativeReview.selection.skill_ref?.id,draft.nativeReview.version===2?(draft.nativeReview.selection.mcp_tool_selection.length?this._tr('已选择 {count} 个工具',{count:draft.nativeReview.selection.mcp_tool_selection.length}):''):((row)=>row?.selection?.kind==='BUILTIN_READONLY'?this._tr(row.title):row?.title)(hintCatalog?.tools?.find(row=>quickDigest(row.selection??null)===quickDigest(draft.nativeReview.selection.tool_selection)))??draft.nativeReview.selection.tool_selection?.tool_ref?.id??draft.nativeReview.selection.tool_selection?.mcp_tool_selection?.items?.[0]?.name].filter(Boolean).join(' · '):'',
      reviewCatalog:this.reviewOpen?this.reviewCatalog??null:null,reviewOpen:!!this.reviewOpen,reviewBusy,reviewProcessRequired:processRequired,reviewRemoteRequired:remoteRequired,
      draft:draft?{readonly:!!draft.readonly,hasSuspendedDraft:!!this.suspendedDraft,nativeReview:draft.nativeReview,guidanceText:draft.guidanceText,reviewControl:draft.reviewControl,controlBusy:!!draft.controlBusy,id:draft.id,revision:draft.revision,comment:draft.comment,marks:normalizedMarks(draft.marks),
        phase:draft.phase,operationId:draft.operationId,savePending:draft.savePending>0,restored:draft.restored,retakePending:draft.retakePending,cancelPending:!!draft.cancelPending,
        textEditor:draft.textEditor?{...draft.textEditor}:null}:null,
      guidanceEditable,canGuide:!!guidance&&(guidance.can_guide||guidance.can_status),
      source:{...source,image:this.presentation==='ACTIVITY'?null:source.image,label:source.available?(safeText(draft?.capture?.snapshot?.observation?.window?.title)||this._tr('当前页面未取得')):this._tr('未引用页面'),available:source.available&&!draft?.restored},canConfirm,
      workContext,fileContext:{...fileContextView(draft?.capture),label:fileContextView(draft?.capture).available?(safeText(draft?.capture?.scope?.fileLabel,160)||this._tr('项目文稿')):'',canClear:typeof this.onClearFileContext==='function'&&draft?.phase==='EDITING'&&!this.capturePending},
      cancelSupported:!draft?.readonly&&typeof this.onCancel==='function',canStop:!draft?.readonly&&typeof this.onCancel==='function'&&(!draft?.result?.canonical_original||draft.result.control_capabilities?.can_cancel===true)&&(draft?.phase==='RUNNING'||!!draft?.result?.canonical_stop_ref||draft?.result?.status==='UNKNOWN_OUTCOME'&&(draft.result.review?.can_cancel===true||draft.result.control_capabilities?.can_cancel===true))&&!draft.cancelPending&&!['REQUESTED','UNKNOWN'].includes(draft.result?.cancellation_status)&&draft.result?.status!=='CANCEL_REQUESTED',
      canDraft:canConfirm,canSend:!this.scopeSwitching&&!this.scopeSwitchFailed&&(guidanceEditable?!!draft.guidanceText.trim()&&!draft.controlBusy&&!!(guidance?.can_guide||guidance?.can_status||draft.reviewControl):canConfirm&&!reviewBusy&&(draft?.nativeReview?.version!==2||source.available)&&(!processRequired||draft.nativeReview.process_start_consent)&&(!remoteRequired||draft.nativeReview.remote_mcp_connect_consent)&&!!(draft?.comment.trim()||draft?.marks.some(mark=>mark.type==='text'&&mark.text.trim()))),
      annotationAvailable:!this.roleFailed.annotationOverlay&&!!annotationBounds(draft?.capture,this.screen,this.windows.annotationOverlay)&&!draft?.restored&&!draft?.geometryInvalidated,
      result:draft?.result ? this._resultView(draft.result) : null};
  }
  _publish() {
    if(this.disposed)return;
    if(this.visible)this._positionQuick();
    const state=this.get();
    for(const [name,window]of Object.entries(this.windows))if(this.roleLoaded[name]&&!window.isDestroyed())window.webContents.send(STATE_CHANNEL,state);
    try{this.onState(state);}catch{}
  }
  _positionQuick(){
    const own=this.windows.quickPopover,next=popupBounds(this.screen,this.anchor,this._quickSize(),this.quickUserBounds);
    const before=own.getBounds();this.quickAppliedBounds=next;
    if(!rectangle(before)||Object.keys(next).some(key=>next[key]!==before[key]))own.setBounds(next,false);
  }
  _motionOrigin(){
    const bounds=this.quickAppliedBounds??this.windows.quickPopover.getBounds(),point=this.anchor;
    return {x:Math.max(0,Math.min(bounds.width,(point?.x??bounds.x)-bounds.x)),
      y:Math.max(0,Math.min(bounds.height,(point?.y??bounds.y+bounds.height)-bounds.y))};
  }
  _cancelCollapse(){
    if(!this.collapse)return;
    const pending=this.collapse;this.collapse=null;clearTimeout(pending.timer);pending.resolve();
    this.motionPhase='OPEN';this.revealSequence++;
  }
  _collapseQuick(){
    if(!this.visible||!this.loaded||!this._alive())return Promise.resolve();
    if(this.collapse)return this.collapse.promise;
    let resolve;const promise=new Promise(done=>{resolve=done;});
    this.collapse={promise,resolve,timer:setTimeout(()=>{this.collapse=null;resolve();},180)};
    this.motionPhase='CLOSE';this.revealSequence++;this._publish();return promise;
  }
  _resultView(result) {
    const labels={RETURNED:'回复已返回',COMPLETED:'处理已结束',SUCCESS:'处理已结束',SUCCEEDED:'处理已结束',
      NOT_CONFIGURED:'请先配置模型连接',INTERRUPTED:'处理已中断',FAILED:'处理未完成',CANCELLED:'处理已取消',CANCELED:'处理已取消',REJECTED:'本次请求未执行',
      RUNNING:'正在处理',PROCESSING:'正在处理',REQUESTING:'正在请求',QUEUED:'正在排队',PENDING:'等待处理',CANCEL_REQUESTED:'正在取消'};
    const artifactStatus=safeText(result.artifact_status,80)||null;
    const artifactError=safeText(result.artifact_error?.message,600);
    // Publication is independent of model completion. Only bounded display
    // metadata crosses into the sandbox; the original operation is untouched.
    const artifactStatusLabel=artifactError?this._tr(artifactError):
      Object.hasOwn(ARTIFACT_COPY,artifactStatus)?this._tr(ARTIFACT_COPY[artifactStatus]):
      artifactStatus&&artifactStatus!=='NEW_ARTIFACT'?this._tr('结果待核实'):'';
    const first=submissionObservation(result.first_submission_observation);
    const firstLabel=first?.kind==='HTTP_RESPONSE'?this._tr('首次发送：HTTP {status}；执行结果待核查，未重发。').replace('{status}',String(first.http_status)):
      first?this._tr('首次发送未收到有效回执；原操作编号和内容已保留，未自动重发。'):'';
    const firstReason=first?.reason_code==='LINKED_DOCUMENT_UNAVAILABLE'?this._tr('读取关联文稿时收到文件占用或位置变化错误。'):'';
    const rawReason=safeText(result.error?.message||result.reason,600);
    const reason=first&&['尚未查到原操作记录，不能据此认定未执行；未自动重发。','发送结果尚未确认，内容已保留；可查看原操作，未自动重发。'].includes(rawReason)?this._tr(rawReason):rawReason;
    const review=quickReviewProjection(result.review);
    const text=typeof result.text==='string'&&result.text?result.text:typeof result.summary==='string'?result.summary:'';
    return {first_submission_observation:first,firstSubmissionLabel:[firstLabel,firstReason].filter(Boolean).join(' '),operation_id:result.operation_id,status:safeText(result.status,80),statusLabel:this._tr(labels[result.status]||'结果待核实'),text,text_sha256:quickDigest(text),
      artifact_status:artifactStatus,artifact_error:artifactError?{message:this._tr(artifactError)}:null,artifactStatusLabel,
      cancellation_status:['REQUESTED','UNKNOWN','SETTLED'].includes(result.cancellation_status)?result.cancellation_status:null,
      local_persistence_status:result.local_persistence_status==='FAILED'?'FAILED':null,
      local_projection:result.local_projection?.complete===false&&result.local_projection.original_operation_id===result.operation_id?
        {complete:false,original_operation_id:result.operation_id}:null,
      canonical_original:result.canonical_original===true,canonical_message_ref:result.canonical_message_ref??null,canonical_controls:result.canonical_controls??[],canonical_stop_ref:result.canonical_stop_ref??null,native_message_ref:ref(result.native_message_ref)?result.native_message_ref:null,native_message_phase:safeText(result.native_message_phase,80),control_capabilities:result.control_capabilities??null,native_message_controls:Array.isArray(result.native_message_controls)?result.native_message_controls.slice(-16):[],review,local_review_controls:result.local_review_controls??[],dispatch_state:result.dispatch_state,execution_started:result.execution_started,owner_stop_confirmed:result.owner_stop_confirmed===true,reason:review?.details_omitted?this._tr('结果待核实'):reason,artifacts:Array.isArray(result.artifacts)?result.artifacts.slice(0,30).map((artifact,index)=>({index,label:safeText(artifact.filename||artifact.name)||this._tr('打开交付文件')})):[]};
  }
  _showQuick(activate) {
    if(!this._alive()||!this.loaded)return;
    this.windows.annotationOverlay.hide();this.overlayVisible=false;this.overlayPreparing=null;
    const window=this.windows.quickPopover;
    if(!this.visible){this.quickUserBounds=null;this.motionPhase='OPEN';this.revealSequence++;}
    this._cancelCollapse();this._positionQuick();
    // Explicit actions may activate after a fresh native input/window guard.
    // Results and unverified late work always use showInactive.
    if(activate)window.show();else window.showInactive();
    this.visible=true;if(this.presentation!=='ACTIVITY')this.unread=false;this._publish();
  }
  async _showQuickForOpen(epoch,draft,lease){
    if(!this._current(epoch,draft)||this.scopeSwitching||this.scopeSwitchFailed)return {status:'STALE'};
    const requestedAt=Date.now();
    const activate=await this.activation.unchanged(lease);
    if(!this._current(epoch,draft)||this.scopeSwitching||this.scopeSwitchFailed)return {status:'STALE'};
    this._showQuick(activate);
    if(activate){
      try{this.windows.quickPopover.webContents.send(STATE_CHANNEL,{kind:'focus-input',epoch,draftId:draft.id,requestedAt});}
      catch(error){try{this.onError(error);}catch{}}
    }
    return this.get();
  }
  _quickSize(){
    if(this.presentation==='ACTIVITY')return {width:380,height:this._supervisorSupported()?520:420};
    const draft=this.draft,guidance=guidanceScope(draft);
    const guidanceVisible=!!draft?.operationId&&!draft.readonly&&!!(guidance&&(guidance.can_guide||guidance.can_status)||draft.guidanceText||draft.reviewControl);
    const feedback=!!(this.error||this.notice||draft?.restored||draft?.retakePending||draft?.phase==='UNKNOWN'&&draft?.result?.first_submission_observation);
    return {width:380,height:Math.min(500,(draft?.textEditor?320:draft?.operationId?(guidanceVisible?420:320):draft?.marks.length?218:176)+
      (this.previewExpanded?125:0)+(feedback?38:0)+(draft?.retakePending?45:0)+(fileContextView(draft?.capture).available?28:0)+(workContextView(draft?.capture,draft?.nativeReview).available?80:0)+(this.reviewOpen?160:0)+(draft?.nativeReview?58:0)+(draft?.result?.review?.pending_permissions?.length||draft?.result?.review?.pending_questions?.length?120:0))};
  }
  async _flushEditor() {
    if(this.presentation==='ACTIVITY')return this._flushSupervisor();
    const draft=this.draft,epoch=this.epoch;
    if(!draft||draft.readonly||draft.phase!=='EDITING'&&!this.get().guidanceEditable||this.presentation==='ACTIVITY')return;
    const role=this.overlayVisible?'annotationOverlay':this.visible?'quickPopover':null;
    if(!role||!this.rendererConnected[role])return;
    if(this.editorFlush)throw new Error('草稿正在保存，请稍后重试。');
    const id=randomUUID();
    try{const completion=new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{if(this.editorFlush?.id===id)this.editorFlush=null;reject(new Error('草稿输入尚未确认，请保持窗口并重试。'));},5000);
      this.editorFlush={id,epoch,draftId:draft.id,role,resolve:()=>{clearTimeout(timer);this.editorFlush=null;resolve();},reject:()=>{clearTimeout(timer);this.editorFlush=null;reject(new Error('请先完成当前输入，再切换。'));}};
      this.windows[role].webContents.send(STATE_CHANNEL,{kind:'flush-request',id,epoch,draftId:draft.id});
    });if(this.editorFlush?.id===id)this.editorFlush.completion=completion;await completion;}catch(error){this._releaseEditor();throw error;}
    if(!this._current(epoch,draft))throw new Error('草稿范围已变化，当前内容保留。');
  }
  _releaseEditor(){
    if(this.scopeSwitching)return;
    for(const [role,window]of Object.entries(this.windows))if(this.rendererConnected[role]&&!window.isDestroyed())window.webContents.send(STATE_CHANNEL,{kind:'flush-release',epoch:this.epoch});
  }
  _supervisorSupported(){return ['onGetSupervisorConversation','onSaveSupervisorDraft','onAskSupervisor','onInspectSupervisor','onSaveSupervisorLocalAnswer'].every(key=>typeof this[key]==='function');}
  updateSupervisorConversation(snapshot,providerProfileRef=null,selectionUpdate){
    if(!this._alive())return {status:'CLOSING'};
    if(providerProfileRef!==null&&(!exact(providerProfileRef,['id','revision','sha256'])||!ID.test(providerProfileRef.id)||!ref(providerProfileRef)))throw new Error('Invalid public supervisor profile');
    if(snapshot!==null&&snapshot!==undefined){
      if(!snapshot||typeof snapshot.conversation_id!=='string'||!ID.test(snapshot.conversation_id)||!Number.isSafeInteger(snapshot.revision)||snapshot.revision<0||!exact(snapshot.draft,['text','revision'])||!Number.isSafeInteger(snapshot.draft.revision)||snapshot.draft.revision<0||!Array.isArray(snapshot.messages)||snapshot.messages.length>256||!Array.isArray(snapshot.operations)||snapshot.operations.length>128)throw new Error('Invalid supervisor snapshot');
      boundedText(snapshot.draft.text,8000);
      const value={revision:snapshot.revision,conversation_id:snapshot.conversation_id,draft:snapshot.draft,messages:snapshot.messages,operations:snapshot.operations};
      for(const key of ['storage_bytes','storage_limit_bytes','history','state_sha256'])if(Object.hasOwn(snapshot,key))value[key]=snapshot[key];
      const wire=JSON.stringify(value);if(Buffer.byteLength(wire)>256*1024)throw new Error('Supervisor snapshot too large');
      if(this.supervisorSnapshot?.conversation_id!==snapshot.conversation_id)this.supervisorSelection=null;
      this.supervisorSnapshot=JSON.parse(wire);
      const known=this.supervisorSelection&&this.supervisorSnapshot.operations.find(row=>row.operation_id===this.supervisorSelection.operation_id);
      if(known&&known.request_sha256!==this.supervisorSelection.request_sha256)this.supervisorSelection=null;
    }
    if(selectionUpdate===null)this.supervisorSelection=null;
    else if(selectionUpdate!==undefined&&this.supervisorSelection?.operation_id===selectionUpdate.operation_id&&this.supervisorSelection.request_sha256===selectionUpdate.request_sha256)
      this.supervisorSelection=supervisorSelection(selectionUpdate,this.supervisorSnapshot,true);
    this.supervisorProfile=providerProfileRef?{...providerProfileRef}:null;this._publish();return this.get().supervisor;
  }
  async _flushSupervisor(){
    if(this.presentation!=='ACTIVITY'||!this._supervisorSupported()||!this.visible||!this.rendererConnected.quickPopover)return;
    if(this.editorFlush)throw new Error('草稿正在保存，请稍后重试。');
    const id=randomUUID(),epoch=this.epoch;
    try{await new Promise((resolve,reject)=>{
      const timer=setTimeout(()=>{if(this.editorFlush?.id===id)this.editorFlush=null;reject(new Error(this.locale==='en'?'The coordinator draft could not be saved. Your input is kept.':'总管草稿尚未保存，当前输入仍保留。'));},5000);
      this.editorFlush={kind:'supervisor',id,epoch,role:'quickPopover',resolve:()=>{clearTimeout(timer);this.editorFlush=null;resolve();},reject:()=>{clearTimeout(timer);this.editorFlush=null;reject(new Error(this.locale==='en'?'The coordinator draft could not be saved. Your input is kept.':'总管草稿尚未保存，当前输入仍保留。'));}};
      this.windows.quickPopover.webContents.send(STATE_CHANNEL,{kind:'supervisor-flush-request',id,epoch});
    });}catch(error){this._releaseEditor();throw error;}
    if(epoch!==this.epoch||!this._alive())throw new Error('Supervisor surface changed');
  }
  async flushSupervisorDraft(){try{await this._flushSupervisor();}finally{this._releaseEditor();}}
  async _supervisorEvent(payload){
    if(!this._supervisorSupported())throw new Error('SUPERVISOR_CLIENT_NOT_READY');
    const kind=payload.kind,hasBody=kind!=='supervisor-get'&&kind!=='supervisor-inspect';
    if(!exact(payload,['kind','epoch',...(hasBody?['body']:kind==='supervisor-inspect'?['operationId']:[])]))throw new Error('Invalid supervisor event');
    const body=payload.body;
    if(kind==='supervisor-draft-save'){
      if(!exact(body,['text','expectedRevision'])||!Number.isSafeInteger(body.expectedRevision)||body.expectedRevision<0)throw new Error('Invalid supervisor draft');boundedText(body.text,8000);
    }else if(kind==='supervisor-history-read'){
      if(typeof this.onReadSupervisorHistory!=='function'||!exact(body,['cursor','limit'])||!(body.cursor===null||typeof body.cursor==='string'&&body.cursor.length<=512&&/^[A-Za-z0-9_-]+$/.test(body.cursor))||!Number.isSafeInteger(body.limit)||body.limit<1||body.limit>24)throw new Error('Invalid supervisor history request');
    }else if(kind==='supervisor-operation-read'){
      if(typeof this.onReadSupervisorOperation!=='function'||!exact(body,['operation_id','request_sha256'])||!ID.test(body.operation_id)||!SHA.test(body.request_sha256))throw new Error('Invalid supervisor operation reference');
    }else if(kind==='supervisor-ask'){
      if(!exact(body,['operationId','text','providerProfileRef',...body&&Object.hasOwn(body,'followupRef')?['followupRef']:[]])||typeof body.operationId!=='string'||!ID.test(body.operationId)||!boundedText(body.text,8000).trim()||!exact(body.providerProfileRef,['id','revision','sha256'])||!ID.test(body.providerProfileRef.id)||!ref(body.providerProfileRef))throw new Error('Invalid supervisor question');
      if(!this.supervisorProfile||quickDigest(body.providerProfileRef)!==quickDigest(this.supervisorProfile))throw new Error('SUPERVISOR_PROFILE_INVALID');
      if(Object.hasOwn(body,'followupRef')){
        const target=body.followupRef;
        if(!exact(target,['operation_id','request_sha256'])||!ID.test(target.operation_id)||!SHA.test(target.request_sha256))throw new Error('Invalid supervisor follow-up reference');
        const current=this.supervisorSnapshot?.operations.find(row=>row.operation_id===target.operation_id);
        if(current){if(current.request_sha256!==target.request_sha256)throw new Error('Invalid supervisor follow-up reference');}
        else{const epoch=this.epoch,observed=typeof this.onReadSupervisorOperation==='function'?await this.onReadSupervisorOperation({...target}):undefined;
          if(epoch!==this.epoch||!this._alive()||observed?.conversation_id!==this.supervisorSnapshot?.conversation_id||observed.operation?.operation_id!==target.operation_id||observed.operation.request_sha256!==target.request_sha256)throw new Error('Invalid supervisor follow-up reference');}
      }
    }else if(kind==='supervisor-inspect'){if(typeof payload.operationId!=='string'||!ID.test(payload.operationId))throw new Error('Invalid supervisor operation');}
    else if(kind==='supervisor-local-save'){
      if(!exact(body,['question','answer'])||!boundedText(body.question,8000).trim()||!(typeof body.answer==='string'||exact(body.answer,['text'])))throw new Error('Invalid local supervisor answer');boundedText(typeof body.answer==='string'?body.answer:body.answer.text,24000);
    }else if(kind!=='supervisor-get')throw new Error('Invalid supervisor event');
    try{
      if(kind==='supervisor-get')return await this.onGetSupervisorConversation();
      if(kind==='supervisor-history-read')return await this.onReadSupervisorHistory({cursor:body.cursor,limit:body.limit});
      if(kind==='supervisor-operation-read')return await this.onReadSupervisorOperation({...body});
      if(kind==='supervisor-draft-save')return await this.onSaveSupervisorDraft({text:body.text,expectedRevision:body.expectedRevision});
      if(kind==='supervisor-ask')return await this.onAskSupervisor({operationId:body.operationId,text:body.text,providerProfileRef:{...body.providerProfileRef},...body.followupRef?{followupRef:{...body.followupRef}}:{}});
      if(kind==='supervisor-inspect')return await this.onInspectSupervisor(payload.operationId);
      return await this.onSaveSupervisorLocalAnswer({question:body.question,answer:typeof body.answer==='string'?body.answer:{text:body.answer.text}});
    }catch(error){
      const allowed=new Set(['SUPERVISOR_DRAFT_CONFLICT','SUPERVISOR_STORAGE_CONFLICT','SUPERVISOR_STORAGE_FULL','SUPERVISOR_CLOSED','SUPERVISOR_OPERATION_PENDING','SUPERVISOR_UNKNOWN_INPUT_REPLAY_BLOCKED','SUPERVISOR_OPERATION_CONFLICT','SUPERVISOR_PROFILE_INVALID','SUPERVISOR_TEXT_INVALID','SUPERVISOR_HISTORY_CHANGED','SUPERVISOR_HISTORY_REQUEST_INVALID','SUPERVISOR_QUESTION_REFERENCE_CHANGED','SUPERVISOR_ARCHIVE_INVALID']);
      const fallback=kind==='supervisor-get'?'SUPERVISOR_CLIENT_READ_UNAVAILABLE':kind==='supervisor-draft-save'?'SUPERVISOR_CLIENT_DRAFT_SAVE_UNKNOWN':kind==='supervisor-ask'?'SUPERVISOR_CLIENT_ASK_UNCONFIRMED':kind==='supervisor-inspect'?'SUPERVISOR_CLIENT_INSPECT_UNCONFIRMED':'SUPERVISOR_CLIENT_LOCAL_SAVE_UNCONFIRMED';
      return {supervisor_error:allowed.has(error?.code)?error.code:fallback};
    }
  }
  _restoreDraft(){
    if(!this.draft?.readonly)return;
    this.draft=this.suspendedDraft;this.suspendedDraft=null;this.reviewOpen=false;this.previewExpanded=false;
  }
  _savedDraft(saved){
    if(!ID.test(saved.id)||!Number.isSafeInteger(saved.revision)||saved.revision<1)throw new Error('Saved draft receipt is incomplete');
    const draft=this._newDraft();draft.id=saved.id;draft.revision=saved.revision;draft.comment=boundedText(saved.comment??'');
    draft.marks=normalizedMarks(saved.marks??[]);draft.capture=saved.capture??null;
    draft.nativeReview=quickResetNativeReview(saved.nativeReview);
    draft.restored=saved.restored===false||saved.capture?.restored===false?false:true;draft.touched=!saved.readonly;
    if(saved.readonly||saved.continuationDraft){
      if(!ID.test(saved.operationId)||saved.message?.operation_id!==saved.operationId)throw new Error('Saved reply identity differs');
      draft.readonly=!!saved.readonly;draft.operationId=saved.operationId;draft.result=saved.message;draft.phase=this._replyPhase(saved.message);
      if(saved.continuationDraft){const item=saved.continuationDraft;draft.guidanceText=boundedText(item.text);draft.continuationRef={id:saved.operationId,revision:item.revision,sha256:item.sha256};draft.continuationBasis=item.basis;draft.continuationVersion=item.version;draft.reviewControl=item.request;}
    }else if(saved.operationId&&ID.test(saved.operationId)){draft.operationId=saved.operationId;draft.phase='UNKNOWN';}
    return draft;
  }
  async _openSaved(invocation){
    if(!this._alive())return {status:'CLOSING'};
    if(this.handoffBusy||this.scopeSwitching||this.scopeSwitchFailed||this.capturePending||['SAVING','SENDING'].includes(this.draft?.phase)||this.draft?.controlBusy)return {status:'BUSY'};
    const epoch=this.epoch,previous=this.draft,isCurrent=()=>this._current(epoch,previous)&&!this.scopeSwitching&&!this.scopeSwitchFailed&&(typeof invocation.isCurrent!=='function'||invocation.isCurrent());
    this.handoffBusy=true;
    try{
      await this.ready;if(!isCurrent())return {status:'STALE'};
      if(!this.loaded)return {status:'UNAVAILABLE'};
      await this._flushEditor();if(!isCurrent())return {status:'STALE'};
      this._commitText();this._cancelAutosave();
      if(previous?.operationId&&!previous.readonly&&(previous.guidanceText||previous.continuationRef))await this._persistContinuation(previous);
      if(previous?.touched&&!previous.operationId)await this._persist('EDITING',previous);
      if(!isCurrent())return {status:'STALE'};
      const saved=await this.onLoadDraft({draftRef:invocation.draftRef});
      if(!isCurrent())return {status:'STALE'};
      if(!saved)throw new Error('Saved reply unavailable');
      const next=this._savedDraft(saved);
      if(next.readonly){if(!previous?.readonly)this.suspendedDraft=previous;}
      else if(previous?.readonly)this.suspendedDraft=null;
      this.epoch++;this.draft=next;this.kind='ask';this.presentation='ASK';this.previewExpanded=false;this.reviewOpen=false;
      this.capturePending=false;this.error='';this.notice='';this.anchor=invocation.anchor??this.screen.getCursorScreenPoint();
      this._cancelCollapse();this.windows.annotationOverlay.hide();this.overlayVisible=false;this._showQuick(false);return this.get();
    }finally{this.handoffBusy=false;this._releaseEditor();}
  }
  async _load(invocation) {
    const explicit=typeof invocation?.draftId==='string';
    if((this.draft&&!explicit)||typeof this.onLoadDraft!=='function')return;
    if(explicit&&!ID.test(invocation.draftId))throw new Error('Exact saved draft required');
    const epoch=this.epoch;
    if(explicit&&this.draft?.id===invocation.draftId&&!invocation.continueEditing)return;
    if(explicit&&this.draft?.touched&&!this.draft.operationId)await this._persist('EDITING');
    if(!this._current(epoch))return;
    const previous=this.draft;
    const saved=await this.onLoadDraft({...(invocation?.draftId?{draftId:invocation.draftId}:{})});
    if(!this._current(epoch,previous)||!saved)return;
    const draft=this._savedDraft(saved);
    this.draft=draft;this.notice=draft.restored&&ref(draft.capture?.snapshot?.observation?.context_ref)?'已恢复未完成内容；重新读取页面后才能发送。':'已恢复未完成内容。';
  }
  syncDiscussion(scope){
    const key=scope===undefined?this.discussionKey:quickDigest({...quickAssociationScope(scope),
      ...(scope.conversationRef?{canonicalId:scope.conversationRef.id}:{}),...(scope.originalWorkChoice?{originalWorkChoice:scope.originalWorkChoice,originalWorkSource:scope.originalSource}:{}),...(scope.workInputUnavailable?{workInputUnavailable:true}:{})});
    if(key===this.discussionKey&&!this.scopeSwitchFailed)return this.scopeSwitch??Promise.resolve(this.get());
    this.discussionKey=key;this.openSequence++;
    // Multiple selections share one save barrier. Host already holds the
    // latest target; a future load reads that target, never an earlier reply.
    if(this.scopeSwitch)return this.scopeSwitch;
    if(!this.draft&&!this.suspendedDraft){this.scopeSwitchFailed=false;return Promise.resolve(this.get());}
    this.scopeSwitching=true;this.scopeSwitchFailed=false;this._publish();
    const work=(async()=>{
      try{
        const pending=this.editorFlush?.completion;
        if(pending)await pending;else await this._flushEditor();
        this._commitText();this._cancelAutosave();
        for(const draft of new Set([this.draft,this.suspendedDraft])){
          if(draft?.operationId&&!draft.readonly&&(draft.guidanceText||draft.continuationRef||draft.reviewControl))await this._flushContinuation(draft);
          const meaningful=draft&&(draft.revision>0||draft.comment.length||draft.marks.length||draft.nativeReview||sourceView(draft.capture).available);
          if(draft?.touched&&!draft.operationId&&meaningful)await this._persist('EDITING',draft);
          if(draft?.savePending)await draft.saveTail;
        }
        if(!this._alive())return {status:'CLOSING'};
        this.epoch++;this.capturePending=false;this.activityRequest++;this.activityBusy=false;
        this._cancelCollapse();
        for(const window of Object.values(this.windows))if(!window.isDestroyed())window.hide();
        this.visible=false;this.overlayVisible=false;this.overlayPreparing=null;this.motionPhase='HIDDEN';
        this.draft=null;this.suspendedDraft=null;this.reviewOpen=false;this.previewExpanded=false;
        this.reviewCatalog=null;this.reviewHintCapture=null;this.reviewHintSelection=null;
        this.error='';this.notice='';this.unread=false;
        return this.get();
      }catch(error){
        this.scopeSwitchFailed=true;
        this._fail(error,'讨论切换尚未完成，原稿仍保留。请完成输入后重试。');
        throw error;
      }finally{
        this.scopeSwitching=false;this.scopeSwitch=null;this._releaseEditor();this._publish();
      }
    })();
    this.scopeSwitch=work;return work;
  }
  async openAsk(invocation={}) {return this._open('ask',invocation);}
  async beginAnnotation(invocation={}) {return this._open('annotate',invocation);}
  updateActivity(snapshot){
    if(!this._alive())return {status:'CLOSING'};
    try{this.activity=activityProjection(snapshot,++this.activityRevision);if(this.presentation==='ACTIVITY'&&this.visible){this.error='';this._publish();}return this.get().activity;}
    catch(error){if(this.presentation==='ACTIVITY'&&this.visible)this._fail(error,'任务状态暂不可读，保留上次列表。');return {status:'UNAVAILABLE'};}
  }
  async openActivity(snapshot,invocation={}){
    if(!this._alive())return {status:'CLOSING'};
    const opening=++this.openSequence,epoch=this.epoch,isCurrent=()=>opening===this.openSequence&&(typeof invocation?.isCurrent!=='function'||invocation.isCurrent());
    await this.ready;if(!this._alive())return {status:'CLOSING'};
    if(epoch!==this.epoch||!isCurrent())return {status:'STALE'};
    if(!this.loaded)return {status:'UNAVAILABLE'};
    const hidden=await this.hide({save:true,isCurrent,invalidateOpen:false});
    if(!hidden||hidden.status)return hidden??{status:'CLOSING'};
    if(!this._alive())return {status:'CLOSING'};
    if(hidden.epoch!==this.epoch||!isCurrent())return {status:'STALE'};
    const anchor=invocation?.anchor,cursor=anchor&&Number.isFinite(anchor.x)&&Number.isFinite(anchor.y)?anchor:this.screen.getCursorScreenPoint();
    this.anchor={x:cursor.x,y:cursor.y};this.presentation='ACTIVITY';this.error='';this.notice='';this.updateActivity(snapshot);
    this._showQuick(false);return this.get();
  }
  async _open(kind,invocation) {
    if(invocation?.draftRef)return this._openSaved(invocation);
    if(!this._alive())return {status:'CLOSING'};
    if(this.handoffBusy)return {status:'BUSY'};
    const pending=this.scopeSwitch??(this.scopeSwitchFailed?this.syncDiscussion():null);
    if(!pending&&this.capturePending&&kind===this.kind){await this.hide({save:false});return {status:'CANCELLED'};}
    const opening=++this.openSequence,initialEpoch=this.epoch;
    const activation=kind==='ask'?await this.activation.begin(invocation?.trigger):null;
    if(pending){try{await pending;}catch{return this.get();}}
    if(!this._alive()||!pending&&this.epoch!==initialEpoch||this.openSequence!==opening)return {status:'STALE'};
    const settledEpoch=this.epoch;
    await this._flushEditor();
    if(!this._alive()||this.epoch!==settledEpoch||this.openSequence!==opening)return {status:'STALE'};
    this._restoreDraft();this._releaseEditor();
    this._commitText();
    this.presentation='ASK';this.activityRequest++;this.activityBusy=false;
    this.epoch++;const epoch=this.epoch;this.kind=kind;this.error='';this.notice='';
    this._cancelCollapse();
    const anchor=invocation?.anchor;
    const cursor=anchor&&Number.isFinite(anchor.x)&&Number.isFinite(anchor.y)?anchor:this.screen.getCursorScreenPoint();
    this.anchor={x:cursor.x,y:cursor.y};
    await this.ready;if(!this._current(epoch))return {status:'STALE'};
    if(!this.loaded)return {status:'UNAVAILABLE'};
    try{await this._load(invocation);}catch(error){if(!this._current(epoch))return {status:'STALE'};if(!this.draft)this.draft=this._newDraft();this._fail(error,'草稿暂未恢复，可稍后重试；不会覆盖旧草稿。');return this._showQuickForOpen(epoch,this.draft,activation);}
    if(!this._current(epoch))return {status:'STALE'};
    if(!this.draft)this.draft=this._newDraft();
    if(this.draft.capture||this.draft.comment||this.draft.operationId){
      await this._showQuickForOpen(epoch,this.draft,activation);
      if(kind==='annotate'&&!this.draft.restored&&!this.draft.operationId)await this._showOverlay(false);
      if(!this._current(epoch))return {status:'STALE'};
      return this.get();
    }
    return this._capture(kind,epoch,activation);
  }
  async _capture(kind,epoch=this.epoch,activation) {
    const draft=this.draft,geometryEpoch=this.geometryEpoch;
    if(!draft||draft.operationId)return this.get();
    // Hiding our windows restores normal Windows z-order without activating a
    // foreign HWND. The host separately excludes the orb while it takes pixels.
    for(const window of Object.values(this.windows))window.hide();
    this.visible=false;this.overlayVisible=false;this.overlayPreparing=null;
    this.capturePending=true;this.error='';this._publish();
    try{
      if(activation===undefined)activation=kind==='ask'?await this.activation.begin():null;
      if(!this._current(epoch,draft)||this.scopeSwitching)return {status:'STALE'};
      const capture=await this.onCapture({intent:kind==='annotate'?'批注当前页面':'提问当前页面',kind});
      if(!this._current(epoch,draft)||this.scopeSwitching)return {status:'STALE'};
      if(!capture||typeof capture!=='object'||!capture.snapshot)throw new Error('Current-page capture is incomplete');
      const geometryChanged=geometryEpoch!==this.geometryEpoch;
      draft.capture=capture;draft.restored=false;draft.geometryInvalidated=geometryChanged;this.capturePending=false;
      if(geometryChanged)this.error='显示器已变化，请重新读取页面后再圈选。';
      await this._showQuickForOpen(epoch,draft,activation);
      if(!this._current(epoch,draft))return {status:'STALE'};
      if(!sourceView(capture).available)this.notice='未引用页面。可直接提问，也可重新读取当前页面。';
      else if(kind==='annotate'&&!geometryChanged)await this._showOverlay(false);
      if(!this._current(epoch,draft))return {status:'STALE'};
      this._publish();return this.get();
    }catch(error){
      if(this._current(epoch,draft)){this.capturePending=false;this._fail(error,'暂时无法读取当前页面。可直接提问，或重新读取；已写的意见仍保留。');await this._showQuickForOpen(epoch,draft,activation);}
      return this._current(epoch,draft)?this.get():{status:'STALE'};
    }
  }
  async _showOverlay(activate) {
    if(!this._alive()||!this.loaded||!this.draft||this.draft.phase!=='EDITING'||this.draft.operationId||this.draft.restored)return;
    if(this.overlayPreparing?.phase==='LOADING'&&this.overlayPreparing.epoch===this.epoch&&this.overlayPreparing.draft===this.draft)return this.get();
    const geometry=annotationBounds(this.draft.capture,this.screen,this.windows.annotationOverlay);
    if(!geometry||this.draft.geometryInvalidated){this.error='此页面暂不能精确原位圈选，请换到单个显示器后重新读取。';this._publish();return;}
    const draft=this.draft,epoch=this.epoch,request={epoch,draft,phase:'LOADING'};
    this.overlayPreparing=request;this._publish();
    try{
      const loaded=await this._loadRole('annotationOverlay');
      if(!this._current(epoch,draft)||this.overlayPreparing!==request||draft.phase!=='EDITING'||draft.operationId){
        if(this.overlayPreparing===request){this.overlayPreparing=null;if(this._alive())this._publish();}
        return {status:'STALE'};
      }
      if(!loaded||!this.roleLoaded.annotationOverlay){this.overlayPreparing=null;return {status:'UNAVAILABLE'};}
    }catch(error){
      if(this._current(epoch,draft)&&this.overlayPreparing===request){
        this._showQuick(false);this._fail(error,'操作未完成，内容已保留。请重试或展开对话继续。');
      }
      return {status:'UNAVAILABLE'};
    }
    const currentGeometry=annotationBounds(draft.capture,this.screen,this.windows.annotationOverlay);
    if(!currentGeometry||draft.geometryInvalidated){this.overlayPreparing=null;this.error='此页面暂不能精确原位圈选，请换到单个显示器后重新读取。';this._publish();return;}
    this.windows.quickPopover.hide();this.visible=false;
    this.windows.annotationOverlay.setBounds(currentGeometry.bounds,false);
    request.phase='DECODING';this._publish();
    // Its renderer acknowledges that the exact local PNG has decoded before we
    // expose the overlay. Delayed decode never receives foreground focus.
  }
  _cancelAutosave(){if(this.autosave){clearTimeout(this.autosave);this.autosave=null;}}
  _commitText(){
    const draft=this.draft,editor=draft?.textEditor;
    if(!editor)return;
    if(editor.index===null&&!editor.text){draft.textEditor=null;return;}
    try{
      const mark={type:'text',x:editor.x,y:editor.y,text:editor.text};
      const marks=[...draft.marks];
      if(editor.index===null)marks.push(mark);else marks[editor.index]=mark;
      draft.marks=normalizedMarks(marks);draft.textEditor=null;draft.touched=true;this.error='';
    }catch(error){
      this._fail(error,'文字未保存：每条须有内容且不超过 2000 字，全部文字不超过 16000 字、标记不超过 32 个。请修改，或明确取消/删除。');
      throw error;
    }
  }
  _scheduleSave() {
    this._cancelAutosave();const draft=this.draft;
    this.autosave=setTimeout(()=>{this.autosave=null;if(this._alive()&&this.draft===draft)void this._persist('EDITING',draft).catch(()=>{});},450);
  }
  _persistContinuation(draft=this.draft) {
    if(!draft?.operationId||draft.readonly)return Promise.resolve(null);
    const value={parentOperationId:draft.operationId,basis:draft.continuationBasis,text:draft.guidanceText,version:draft.continuationVersion,
      request:draft.reviewControl?.kind==='guidance'?{parentOperationId:draft.operationId,kind:'guidance',body:draft.reviewControl.body,draftVersion:draft.reviewControl.draftVersion??draft.continuationVersion}:null};
    const work=(draft.continuationTail??Promise.resolve()).catch(()=>{}).then(async()=>{
      if(typeof this.onContinuationDraft!=='function')throw Error('Continuation draft persistence is unavailable');
      const saved=await this.onContinuationDraft({...value,expectedRef:draft.continuationRef??null});
      if(saved?.parentOperationId!==draft.operationId||!Number.isSafeInteger(saved.revision)||saved.revision<1||!SHA.test(saved.sha256)||saved.text!==value.text||saved.version!==value.version)throw Error('Continuation persistence receipt differs');
      draft.continuationRef={id:draft.operationId,revision:saved.revision,sha256:saved.sha256};
      return saved;
    }).catch(error=>{if(this.draft===draft)this._fail(error,'保存未完成，内容仍在此处。请重试后再收起或发送。');throw error;});
    draft.continuationTail=work;return work;
  }
  async _flushContinuation(draft){
    const deadline=Date.now()+5000;
    do{
      const version=draft.continuationVersion,request=draft.reviewControl;
      await this._persistContinuation(draft);await draft.continuationTail;
      if(version===draft.continuationVersion&&request===draft.reviewControl)return;
    }while(Date.now()<deadline);
    throw Error('补充仍在修改，请完成输入后再退出。');
  }
  _persist(delivery,draft=this.draft) {
    if(!draft)return Promise.resolve(null);
    if(draft===this.draft)this._commitText();
    const content={comment:draft.comment,marks:normalizedMarks(draft.marks),capture:draft.capture??draft.basisCapture,nativeReview:quickNativeReview(draft.nativeReview)};
    const signature=JSON.stringify({comment:content.comment,marks:content.marks,capture:content.capture?.snapshot?.operation_id??null,scope:content.capture?.scope??null,...(content.nativeReview?{nativeReview:content.nativeReview}:{})});
    draft.savePending++;this._publish();
    const work=draft.saveTail.catch(()=>{}).then(async()=>{
      if(!this._alive())throw new Error('Quick surface is closing');
      if(draft.confirmed)return {id:draft.id,revision:draft.revision,status:'DRAFT'};
      if(delivery==='EDITING'&&draft.savedSignature===signature)return {id:draft.id,revision:draft.revision,status:'EDITING'};
      const receipt=await this.onDraft({draftId:draft.id,expectedRevision:draft.revision,capture:content.capture,
        comment:content.comment,regions:markRegions(content.marks),marks:content.marks,delivery,...(content.nativeReview?{nativeReview:content.nativeReview}:{})});
      if(!receipt||receipt.id!==draft.id||!Number.isSafeInteger(receipt.revision)||receipt.revision<=draft.revision||
          !SHA.test(receipt.sha256)||typeof receipt.status!=='string'||!receipt.status||['FAILED','ERROR','CONFLICT'].includes(receipt.status)) {
        throw new Error('Draft persistence receipt is incomplete or conflicts');
      }
      draft.revision=receipt.revision;draft.savedSignature=signature;
      if(delivery==='DRAFT')draft.confirmed=true;
      if(this.draft===draft)this.error='';return receipt;
    }).catch(error=>{if(this.draft===draft)this._fail(error,'保存未完成，内容仍在此处。请重试后再收起或发送。');throw error;})
      .finally(()=>{draft.savePending--;if(this.draft===draft)this._publish();});
    draft.saveTail=work;return work;
  }
  async _confirm(delivery) {
    if(this.get().reviewBusy)throw new Error('Review selection is updating');
    const draft=this.draft,epoch=this.epoch;
    if(!draft||draft.operationId||this.capturePending||draft.phase!=='EDITING')return {status:'BUSY'};
    this._commitText();
    if(!draft.comment.trim()&&!draft.marks.length)throw new Error('Write a question or add a mark first');
    if(!this.get().canConfirm)throw new Error('Read the current page before confirming annotations or historical references');
    if(delivery==='SEND'&&draft.nativeReview?.version===2&&!sourceView(draft.capture).available)throw new Error('未取得当前页，请重新读取');
    if(delivery==='SEND'&&draft.nativeReview?.version===2&&!this.get().canSend)throw new Error('请先确认所选工具的连接许可');
    if(delivery==='SEND'&&!draft.comment.trim()&&!draft.marks.some(mark=>mark.type==='text'&&mark.text.trim())){
      this.error='请补充要解释或修改什么，标记已保留。';this._publish();return {status:'INTENT_REQUIRED'};
    }
    if(draft.nativeReview){const view=this.get();draft.nativeReview={...draft.nativeReview,process_start_consent:view.reviewProcessRequired&&draft.nativeReview.process_start_consent,...(draft.nativeReview.version===2?{remote_mcp_connect_consent:view.reviewRemoteRequired&&draft.nativeReview.remote_mcp_connect_consent}:{})};}
    this._cancelAutosave();draft.phase='SAVING';this.error='';this._publish();
    try{
      await this._persist(delivery==='DRAFT'?'DRAFT':'EDITING',draft);
      if(!this._current(epoch,draft)){draft.phase='EDITING';return {status:'STALE'};}
      if(delivery==='DRAFT'){
        this.draft=null;this.notice='已加入聊天草稿。';await this.hide({save:false});return {status:'DRAFT_SAVED'};
      }
      const operationId='quick_'+randomUUID();
      // The host journals this exact operation before dispatch. No path calls onSend twice.
      const request={operationId,draftId:draft.id,capture:sourceView(draft.capture).available?draft.capture:null,comment:draft.comment,
        regions:markRegions(draft.marks),marks:normalizedMarks(draft.marks),...(draft.nativeReview?{nativeReview:quickNativeReview(draft.nativeReview)}:{})};
      if(this.onPrepareSend){
        try{await this.onPrepareSend(request);}
        catch(error){
          if(!this._current(epoch,draft)){draft.phase='EDITING';return {status:'STALE'};}
          this.error='尚未发送，输入已保留。请检查页面来源、模型或所选工具后重试。';throw error;
        }
        if(!this._current(epoch,draft)){draft.phase='EDITING';return {status:'STALE'};}
      }
      draft.operationId=operationId;draft.phase='SENDING';this._publish();
      await this.hide({save:false,animate:false});
      const dispatchEpoch=this.epoch;
      try{
        const receipt=await this.onSend(request);
        if(this._alive()&&this.draft===draft&&this.publishResult(receipt)?.status==='STALE')throw new Error('Reply receipt does not belong to this operation');
      }catch(error){if(this._alive()&&this.draft===draft&&draft.operationId===operationId){
        const current=this._current(dispatchEpoch,draft);
        if(error instanceof QuickNotSubmittedError&&error.matches(operationId,draft)){
          draft.operationId=null;draft.phase='EDITING';this.unread=false;
          if(current){if(!this.visible)this._showQuick(false);this._fail(error,'尚未发送，输入已保留。请检查页面来源、模型或所选工具后重试。');}
        }else{draft.phase='UNKNOWN';this.unread=true;if(current)this._fail(error,'发送结果暂不确定，请查看原操作进展；不会重复发送。');}
        this._publish();
      }}
      return {status:draft.phase,operationId:draft.operationId};
    }catch(error){if(this.draft===draft&&draft.phase==='SAVING'){draft.phase='EDITING';this._publish();}throw error;}
  }
  _replyPhase(receipt){
    return receipt.dispatch_state==='NOT_DISPATCHED'?'FAILED':['PROCESSING','REQUESTING','RUNNING','QUEUED','PENDING','CANCEL_REQUESTED'].includes(receipt.status)?'RUNNING':
      ['RETURNED','COMPLETED','SUCCESS','SUCCEEDED'].includes(receipt.status)?'COMPLETED':
      ['FAILED','CANCELLED','CANCELED','REJECTED','UNAVAILABLE','NOT_CONFIGURED','INTERRUPTED','TIMED_OUT'].includes(receipt.status)?'FAILED':'UNKNOWN';
  }
  publishResult(receipt,{readonly=false}={}) {
    const draft=this.draft,operation=receipt?.operation_id??receipt?.operationId;
    if(!this._alive()||!draft?.operationId||draft.readonly&&!readonly||operation!==draft.operationId||typeof receipt.status!=='string')return {status:'STALE'};
    draft.result={...receipt,operation_id:operation};if(receipt.dispatch_state==='NOT_DISPATCHED')this.reviewHintSelection=undefined;
    draft.phase=this._replyPhase(receipt);
    if(receipt.canonical_original&&receipt.canonical_controls?.at(-1)?.text&&['补充已送达','任务已返回','任务仍在运行','任务当前空闲'].includes(this.notice))this.notice='';
    this.error='';this.unread=!this.visible||this.presentation==='ACTIVITY';this._publish();return this.get();
  }
  async _readReviewCatalog({open=true}={}){
    const draft=this.draft,epoch=this.epoch,capture=draft?.capture,read=this.reviewReadEpoch=(this.reviewReadEpoch??0)+1;
    if(!draft||draft.phase!=='EDITING'||this.capturePending||typeof this.onReviewCatalog!=='function')throw new Error('技能与工具目录暂不可用');
    this.reviewBusy=true;this.reviewBusyDraft=draft;this.reviewBusyEpoch=epoch;this._publish();
    try{const value=await this.onReviewCatalog({...(draft.capture?.scope?{scope:draft.capture.scope}:{}),selection:draft.nativeReview?.selection??null,kind:'CAPABILITY_TASK'});if(!this._current(epoch,draft)||capture!==draft.capture||read!==this.reviewReadEpoch)return {status:'STALE'};
      if(!['eeas.native-review-capabilities.v1','eeas.native-capability-task-capabilities.v2'].includes(value?.schema)||!Array.isArray(value.skills)||!(value.schema==='eeas.native-capability-task-capabilities.v2'?Array.isArray(value.mcp_catalog?.mcp_servers):Array.isArray(value.tools))||Buffer.byteLength(JSON.stringify(value))>1048576)throw new Error('技能与工具目录暂不可用');
      this.reviewCatalog=value;this.reviewHintCapture=capture;this.reviewHintSelection=quickDigest(draft.nativeReview?.selection??null);if(open)this.reviewOpen=true;return value;
    }catch(error){if(!this._current(epoch,draft)||capture!==draft.capture||read!==this.reviewReadEpoch)return {status:'STALE'};throw error;
    }finally{if(this._current(epoch,draft)&&read===this.reviewReadEpoch){this.reviewBusy=false;this._publish();}}
  }
  async _selectReview(payload){const draft=this.draft,catalog=this.reviewCatalog?.schema==='eeas.native-capability-task-capabilities.v2'?this.reviewCatalog.legacy_review_catalog:this.reviewCatalog;if(!draft||draft.phase!=='EDITING'||this.capturePending||!catalog||typeof payload.skillId!=='string'||typeof payload.toolId!=='string')throw new Error('Review selection unavailable');
    const skill=catalog.skills.find(row=>row.id===payload.skillId),tool=catalog.tools.find(row=>row.id===payload.toolId);
    if(payload.skillId&&!skill?.selectable||payload.toolId&&!tool?.selectable)throw new Error('所选技能或工具已变化，请重新选择');
    draft.nativeReview=skill||tool?quickNativeReview({version:1,selection:{skill_ref:skill?.config_ref??null,tool_selection:tool?.selection??null,capability_binding_sha256:catalog.capability_binding_sha256,basis_ref:catalog.basis_ref??null},process_start_consent:false}):null;
    draft.touched=true;this._scheduleSave();return this._readReviewCatalog();
  }
  async _selectCapability(payload){
    const draft=this.draft,catalog=this.reviewCatalog;
    if(!draft||draft.phase!=='EDITING'||this.capturePending||catalog?.schema!=='eeas.native-capability-task-capabilities.v2'||typeof payload.skillId!=='string'||!Array.isArray(payload.toolIds)||payload.toolIds.length>64||new Set(payload.toolIds).size!==payload.toolIds.length)throw new Error('Review selection unavailable');
    const skill=catalog.skills.find(row=>row.id===payload.skillId);if(payload.skillId&&!skill?.selectable)throw new Error('所选技能或工具已变化，请重新选择');
    const items=payload.toolIds.map(id=>{
      const server=catalog.mcp_catalog.mcp_servers.find(row=>row.enabled&&row.availability==='AVAILABLE'&&row.tools.some(tool=>tool.id===id&&tool.selectable));
      const tool=server?.tools.find(row=>row.id===id);if(!server?.probe_ref||!tool)throw new Error('所选技能或工具已变化，请重新选择');
      return {server_id:server.id,config_ref:server.config_ref,probe_ref:server.probe_ref,tool_id:tool.id,name:tool.name,input_schema_sha256:tool.input_schema_sha256};
    });
    draft.nativeReview=skill||items.length?quickNativeReview({version:2,kind:'CAPABILITY_TASK',selection:{capability_binding_sha256:catalog.capability_binding_sha256,skill_ref:skill?.config_ref??null,mcp_tool_selection:items},process_start_consent:false,remote_mcp_connect_consent:false,task_root_operation_id:null}):null;
    draft.touched=true;this._scheduleSave();return this._readReviewCatalog();
  }
  async _reviewControl(payload){
    const draft=this.draft,epoch=this.epoch,review=draft?.result?.review,scope=guidanceScope(draft)??(draft?.reviewControl?{}:null);
    if(!draft?.operationId||draft.readonly||!scope||typeof this.onReviewControl!=='function'||draft.controlBusy)throw new Error('当前任务暂不能补充，请先核查原操作');
    const matches=request=>payload.action==='guidance'?request.kind==='guidance':request.body.request_ref?.id===payload.requestId;
    if(draft.reviewControl&&!matches(draft.reviewControl))throw new Error('结果尚未确认，请核查原操作');
    let request=draft.reviewControl??draft.result.local_review_controls?.find(matches);
    if(request)request={parentOperationId:request.parentOperationId,kind:request.kind,body:request.body,inspect:request.inspect!==false,draftVersion:request.draftVersion};
    if(!request){
      const item=[...review?.pending_permissions??[],...review?.pending_questions??[]].find(item=>item.request_ref?.id===payload.requestId),action=payload.action;
      if(action==='guidance'){if(!(scope.can_guide||scope.can_status)||!draft.guidanceText.trim())throw new Error('当前任务暂不能补充，请先核查原操作');}
      else if(!review||!item||!['once','reply','reject'].includes(action)||action==='once'&&item.kind!=='PERMISSION')throw new Error('Original pending request differs');
      const basis=scope.expected_canonical_message_ref?{expected_canonical_message_ref:draft.continuationBasis??scope.expected_canonical_message_ref}:scope.expected_native_message_ref?{expected_native_message_ref:scope.expected_native_message_ref,intent:'AUTO'}:{expected_review_ref:scope.expected_review_ref};
      request={parentOperationId:draft.operationId,kind:action==='guidance'?'guidance':item.kind==='PERMISSION'?'permission':action==='reject'?'question-reject':'question',body:{operation_id:'review_'+randomUUID(),...basis,...action==='guidance'?{raw_text:draft.guidanceText}:{request_ref:item.request_ref,...item.kind==='PERMISSION'?{reply:action==='reject'?'reject':'once'}:action==='reject'?{}:{answers:payload.answers}}}};
      request.draftVersion=draft.continuationVersion;draft.reviewControl=request;
    }
    draft.controlBusy=true;this._publish();
    try{
      if(request.kind==='guidance')await this._persistContinuation(draft);
      const result=await this.onReviewControl(request);if(!this._current(epoch,draft))return {status:'STALE'};
      if(['UNKNOWN_OUTCOME','PROCESSING','REQUESTING','DISPATCHING'].includes(result.status))this.error='结果尚未确认，请核查原操作';
      else{
        draft.reviewControl=null;
        if(['ACCEPTED','OBSERVED','NEXT_TURN_ASSOCIATED','COHORT_RETURNED','RETURNED','COMPLETED'].includes(result.status)){
          const retained={text:draft.guidanceText,basis:draft.continuationBasis,version:draft.continuationVersion};
          const clear=draft.continuationVersion===request.draftVersion&&draft.guidanceText===request.body.raw_text;
          if(clear){draft.guidanceText='';draft.continuationBasis=null;}
          try{if(request.kind==='guidance')await this._persistContinuation(draft);}
          catch(error){
            if(clear&&draft.continuationVersion===retained.version&&!draft.guidanceText){draft.guidanceText=retained.text;draft.continuationBasis=retained.basis;}
            draft.reviewControl={...request,inspect:true};throw error;
          }
          this.notice=result.role==='LOCAL_STATUS'?(result.local_status?.observation_is_current===false?'已读取上次保存的进度':result.local_status?.active_operation_id?'任务仍在运行':'任务当前空闲'):result.status==='COHORT_RETURNED'?'任务已返回':'补充已送达';this.error='';
        }
        await this._inspect({controls:false});
        if(['FAILED_NOT_DISPATCHED','FAILED','REJECTED'].includes(result.status))this.error='此条补充未执行，内容已保留';
      }
      return result;
    }finally{draft.controlBusy=false;if(this._current(epoch,draft))this._publish();}
  }
  async _inspect({controls=true}={}) {
    const draft=this.draft,epoch=this.epoch;
    if(!draft?.operationId)throw new Error('No operation to inspect');
    const pending=!draft.readonly&&controls&&(draft.reviewControl??draft.result?.local_review_controls?.[0]);
    if(pending?.kind==='guidance'&&typeof this.onReviewControl==='function')return this._reviewControl({action:'guidance'});
    if(pending&&typeof this.onReviewControl==='function'){
      const result=await this.onReviewControl({parentOperationId:pending.parentOperationId,kind:pending.kind,body:pending.body,inspect:true});
      if(!this._current(epoch,draft))return {status:'STALE'};
      if(!['UNKNOWN_OUTCOME','PROCESSING','REQUESTING'].includes(result.status))draft.reviewControl=null;
    }
    const receipt=await this.onInspect(draft.operationId);
    if(!this._current(epoch,draft))return {status:'STALE'};
    const durable=draft.readonly?await this.onLoadReadonlyResult(draft.operationId,draft.id):receipt;
    if(!this._current(epoch,draft))return {status:'STALE'};
    if(draft.readonly&&receipt.local_persistence_status==='FAILED')this.notice='本次回执的本机保存未成功；请核查原操作，勿重复发送。';
    const result=this.publishResult(durable,{readonly:!!draft.readonly});
    if(result?.status==='STALE')throw new Error('Inspection receipt does not belong to this operation');
    return result;
  }
  async _stop(){
    const draft=this.draft,epoch=this.epoch;
    if(!draft?.operationId||typeof this.onCancel!=='function')throw new Error('原操作尚不能停止。');
    if(draft.cancelPending)return {status:'BUSY'};
    if(!this.get().canStop)return this._inspect();
    draft.cancelPending=true;this._publish();
    try{
      const receipt=await this.onCancel(draft.operationId);
      if(!this._current(epoch,draft))return {status:'STALE'};
      const result=this.publishResult(receipt);if(result?.status==='STALE')throw new Error('停止回执不属于原操作。');return result;
    }catch(error){if(this._current(epoch,draft)){draft.result={...(draft.result??{}),operation_id:draft.operationId,status:draft.result?.status??'UNKNOWN_OUTCOME',cancellation_status:'UNKNOWN'};this._fail(error,'停止请求结果尚未确认；请核查原操作，不会重复请求。');}return this.get();}
    finally{draft.cancelPending=false;if(this._current(epoch,draft))this._publish();}
  }
  async _clearFileContext(){
    const draft=this.draft,epoch=this.epoch;
    if(!draft||draft.phase!=='EDITING'||this.capturePending||!fileContextView(draft.capture).available||typeof this.onClearFileContext!=='function')throw new Error('此条文稿引用当前不能更改。');
    this._commitText();this._cancelAutosave();await this._persist('EDITING',draft);
    if(!this._current(epoch,draft))return {status:'STALE'};
    const previous=draft.capture,cleared=await this.onClearFileContext(previous);
    if(!this._current(epoch,draft))return {status:'STALE'};
    if(!cleared||cleared.snapshot!==previous.snapshot||cleared.scope?.fileLinkRef||cleared.scope?.fileLabel)throw new Error('文稿引用尚未安全清除，原依据保留。');
    const clean=scope=>Object.fromEntries(Object.entries(scope??{}).filter(([key])=>!['fileLinkRef','fileLabel'].includes(key)).sort(([a],[b])=>a.localeCompare(b)));
    if(JSON.stringify(clean(cleared.scope))!==JSON.stringify(clean(previous.scope))||cleared.hostGeneration!==previous.hostGeneration||cleared.restored!==previous.restored)throw new Error('清除文稿不能更换原问题或页面范围。');
    draft.capture=cleared;draft.touched=true;draft.savedSignature=null;await this._persist('EDITING',draft);
    this.notice='这条问题不再引用文稿，原页面与文字已保留。';this._publish();return this.get();
  }
  async hide({save=true,animate=true,isCurrent,invalidateOpen=true}={}) {
    if(this.disposed)return;
    if(invalidateOpen)this.openSequence++;
    if(this.scopeSwitch)await this.scopeSwitch;
    if(this.handoffBusy)throw new Error('草稿正在切换，请稍后重试。');
    const epoch=this.epoch;
    if(isCurrent&&!isCurrent())return {status:'STALE'};
    if(save){await this._flushEditor();for(const item of new Set([this.draft,this.suspendedDraft]))if(item?.operationId&&!item.readonly&&(item.guidanceText||item.continuationRef||item.reviewControl))await this._flushContinuation(item);}
    if(epoch!==this.epoch||this.disposed||isCurrent&&!isCurrent()){this._releaseEditor();return {status:'STALE'};}
    this._commitText();
    this.overlayPreparing=null;
    const draft=this.draft;
    if(save&&this.presentation!=='ACTIVITY'&&draft?.touched&&!draft.operationId){
      if(draft.phase==='SAVING'){this._releaseEditor();return {status:'BUSY'};}
      draft.phase='SAVING';this._cancelAutosave();this._publish();
      try{await this._persist('EDITING',draft);}
      catch(error){
        if(this.draft===draft)draft.phase='EDITING';
        if(this._alive()&&this.draft===draft){if(!this.visible)this._showQuick(false);this._publish();}
        this._releaseEditor();throw error;
      }
      if(this.draft===draft)draft.phase='EDITING';
    }
    if(epoch!==this.epoch||this.disposed||isCurrent&&!isCurrent()){this._releaseEditor();return {status:'STALE'};}
    const hideEpoch=++this.epoch;this.capturePending=false;this.activityRequest++;this.activityBusy=false;this._cancelAutosave();
    if(animate)await this._collapseQuick();
    if(hideEpoch!==this.epoch||this.disposed||isCurrent&&!isCurrent()){
      if(hideEpoch===this.epoch&&this._alive()&&this.visible&&this.motionPhase==='CLOSE'){
        this.motionPhase='OPEN';this.revealSequence++;this._publish();
      }
      return {status:'STALE'};
    }
    for(const window of Object.values(this.windows))if(!window.isDestroyed())window.hide();
    this.motionPhase='HIDDEN';
    this.visible=false;this.overlayVisible=false;this.overlayPreparing=null;this._restoreDraft();this._releaseEditor();this._publish();
    return this.get();
  }
  _trusted(event) {
    return Object.entries(this.windows).find(([name,window])=>!window.isDestroyed()&&event?.sender===window.webContents&&
      event.senderFrame===window.webContents.mainFrame&&event.senderFrame?.url===this.urls[name]&&window.webContents.getURL()===this.urls[name])?.[0];
  }
  async _receive(event,payload) {
    const role=this._trusted(event);
    if(!this._alive()||!role)throw new Error('Untrusted quick surface sender');
    if(exact(payload,['kind'])&&payload.kind==='state'){this.rendererConnected[role]=true;return this.get();}
    if(exact(payload,['kind','id','epoch','status'])&&payload.kind==='supervisor-flush-complete'){
      const pending=this.editorFlush;if(role!=='quickPopover'||pending?.kind!=='supervisor'||payload.id!==pending.id||payload.epoch!==pending.epoch||!['SAVED','FAILED'].includes(payload.status))throw new Error('Stale supervisor flush');
      payload.status==='SAVED'?pending.resolve():pending.reject();return {status:'OBSERVED'};
    }
    if(exact(payload,['kind','id','epoch','draftId','status'])&&payload.kind==='flush-complete'){
      const pending=this.editorFlush;
      if(!pending||role!==pending.role||payload.id!==pending.id||payload.epoch!==pending.epoch||payload.draftId!==pending.draftId||!['SAVED','FAILED'].includes(payload.status))throw new Error('Stale editor flush');
      payload.status==='SAVED'?pending.resolve():pending.reject();return {status:'OBSERVED'};
    }
    if(role==='annotationOverlay'&&exact(payload,['kind','epoch'])&&['preview-ready','preview-failed'].includes(payload.kind)&&
        payload.epoch===this.epoch&&this.roleLoaded.annotationOverlay&&this.overlayPreparing?.phase==='DECODING'&&this.overlayPreparing.epoch===this.epoch){
      this.overlayPreparing=null;
      if(payload.kind==='preview-ready'){this.windows.annotationOverlay.showInactive();this.overlayVisible=true;this._publish();}
      else{this.error='页面画面未能加载，意见已保留，请重新读取。';this._showQuick(false);}
      return this.get();
    }
    if(!payload||typeof payload!=='object'||payload.epoch!==this.epoch||
        !(role==='quickPopover'?this.visible:this.overlayVisible))throw new Error('Stale or hidden quick surface');
    if((this.scopeSwitching||this.scopeSwitchFailed)&&!['edit','text-edit','review-guidance-edit'].includes(payload.kind)&&!(payload.kind==='command'&&payload.command==='dismiss'))
      throw new Error(this._tr('讨论切换尚未完成，原稿仍保留。请完成输入后重试。'));
    try{
      if(this.handoffBusy&&!this.editorFlush)throw new Error('草稿正在切换，请稍后重试。');
      if(typeof payload.kind==='string'&&payload.kind.startsWith('supervisor-')){
        if(role!=='quickPopover'||this.presentation!=='ACTIVITY')throw new Error('Supervisor is not the active surface');
        return await this._supervisorEvent(payload);
      }
      if(this.draft?.readonly&&this.presentation!=='ACTIVITY'&&!(payload.kind==='artifact'||payload.kind==='reply-link'||payload.kind==='command'&&['dismiss','inspect','expand','workbench','new-question','toggle-preview','pin'].includes(payload.command)))throw new Error('Historical reply is read-only');
      if(this.presentation==='ACTIVITY'&&!(payload.kind==='activity-open'||payload.kind==='command'&&
          ['dismiss','activity-back','activity-all','activity-refresh','activity-supervisor','supervisor-configure','pin'].includes(payload.command)))throw new Error('Task list cannot dispatch or edit a question');
      if(payload.kind==='activity-open'&&exact(payload,['kind','epoch','activity_revision','token'])){
        if(role!=='quickPopover'||this.presentation!=='ACTIVITY'||payload.activity_revision!==this.activity.public.revision||
            !this.activity.keys.has(payload.token)||typeof this.onOpenTask!=='function')throw new Error('Activity selection changed');
        if(this.activityBusy)return {status:'BUSY'};
        const epoch=this.epoch,key={...this.activity.keys.get(payload.token)};
        this.activityBusy=true;this._publish();
        try{
          await this.flushSupervisorDraft();if(!this._alive()||this.epoch!==epoch||this.presentation!=='ACTIVITY')return {status:'STALE'};
          const opened=await this.onOpenTask(key);
          if(!this._alive()||this.epoch!==epoch||this.presentation!=='ACTIVITY')return {status:'STALE'};
          if(opened?.status==='UNAVAILABLE')return opened;
          if(opened?.status==='SUPERVISOR'){
            const selected=opened.selection;
            if(!this._supervisorSupported()||key.kind!=='native_message'||key.project_id!=null||key.object_id!==key.operation_id||selected?.operation_id!==key.operation_id)throw new Error('Supervisor task selection changed');
            this.supervisorSelection=supervisorSelection(selected,this.supervisorSnapshot,true);this.supervisorSelectionRevision++;this._publish();return {status:'SUPERVISOR'};
          }
          await this.hide({save:true,animate:false});
        }
        finally{if(epoch===this.epoch){this.activityBusy=false;if(this.presentation==='ACTIVITY'&&this.visible)this._publish();}}
        return {status:'OPENED'};
      }
      if(payload.kind==='text-edit'&&exact(payload,['kind','epoch','editor'])){
        const draft=this.draft,editor=payload.editor;
        if(!draft||draft.phase!=='EDITING'||this.capturePending||!(role==='annotationOverlay'||role==='quickPopover'&&draft.textEditor)||
            !exact(editor,['index','x','y','text'])||!point({x:editor.x,y:editor.y})||
            !(editor.index===null||Number.isSafeInteger(editor.index)&&editor.index>=0&&draft.marks[editor.index]?.type==='text'))throw new Error('Invalid text editor');
        // A temporarily over-limit input stays in the owned editor, allowing
        // explicit correction/cancel. Only normalized <=2000-char marks may
        // reach the durable journal; hide refuses invalid pending input.
        boundedText(editor.text,64000);
        if(draft.textEditor&&draft.textEditor.index!==editor.index)throw new Error('Confirm the existing text first');
        draft.textEditor={...editor};this._cancelAutosave();this.error='';this._publish();return this.get();
      }
      if(payload.kind==='edit'&&exact(payload,['kind','epoch','comment','marks'])){
        const draft=this.draft;
        if(!draft||draft.phase!=='EDITING'||this.capturePending)throw new Error('Quick draft is not editable');
        if(draft.textEditor)throw new Error('Confirm text before replacing marks');
        const comment=boundedText(payload.comment),marks=normalizedMarks(payload.marks);
        draft.comment=comment;draft.marks=marks;draft.touched=true;
        this.error='';this._scheduleSave();this._publish();return this.get();
      }
      if(payload.kind==='artifact'&&role==='quickPopover'&&exact(payload,['kind','epoch','index'])&&Number.isSafeInteger(payload.index)){
        const artifact=this.draft?.result?.artifacts?.[payload.index];
        if(payload.index<0||payload.index>=30||!artifact)throw new Error('Unknown artifact');
        await this.onOpenArtifact(artifact);return {status:'OPENED'};
      }
      if(payload.kind==='reply-link'&&role==='quickPopover'&&exact(payload,['kind','epoch','operationId','text_sha256','url'])){
        if(typeof this.onOpenExternal!=='function'||!this.draft?.result?.text||typeof payload.url!=='string'||payload.url.length>4096||/[\u0000-\u0020\u007f]/.test(payload.url))throw new Error('Invalid reply link');
        if(payload.operationId!==this.draft.operationId||payload.text_sha256!==quickDigest(this.draft.result.text))throw new Error('Reply changed');
        const url=new URL(payload.url);
        if(!['http:','https:','mailto:'].includes(url.protocol)||url.username||url.password)throw new Error('Invalid reply link');
        if(!replyLinkTargets(this.draft.result.text).includes(url.href))throw new Error('Link is not in the current reply');
        await this.onOpenExternal(url.href);return {status:'OPENED'};
      }
      if(payload.kind==='review-selection'&&role==='quickPopover'&&exact(payload,['kind','epoch','skillId','toolId']))return this._selectReview(payload);
      if(payload.kind==='capability-selection'&&role==='quickPopover'&&exact(payload,['kind','epoch','skillId','toolIds']))return this._selectCapability(payload);
      if(payload.kind==='review-remote-consent'&&role==='quickPopover'&&exact(payload,['kind','epoch','enabled'])){if(this.draft?.phase!=='EDITING'||this.draft.nativeReview?.version!==2||this.get().reviewBusy||!this.get().reviewRemoteRequired||typeof payload.enabled!=='boolean')throw new Error('Review draft is not editable');this.draft.nativeReview={...this.draft.nativeReview,remote_mcp_connect_consent:payload.enabled};this.draft.touched=true;this._scheduleSave();this._publish();return this.get();}
      if(payload.kind==='review-consent'&&role==='quickPopover'&&exact(payload,['kind','epoch','enabled'])){if(this.draft?.phase!=='EDITING'||!this.draft.nativeReview||this.get().reviewBusy||!this.get().reviewProcessRequired||typeof payload.enabled!=='boolean')throw new Error('Review draft is not editable');this.draft.nativeReview={...this.draft.nativeReview,process_start_consent:payload.enabled};this.draft.touched=true;this._scheduleSave();this._publish();return this.get();}
      if(payload.kind==='review-guidance-edit'&&role==='quickPopover'&&exact(payload,['kind','epoch','text'])){if(this.draft?.readonly||!this.draft?.operationId||!guidanceScope(this.draft)&&!this.draft.reviewControl)throw new Error('Guidance unavailable');const draft=this.draft,text=boundedText(payload.text);if(text!==draft.guidanceText){draft.guidanceText=text;draft.continuationVersion++;if(!text&&!draft.reviewControl)draft.continuationBasis=null;if(text&&!draft.continuationBasis){const scope=guidanceScope(draft);draft.continuationBasis=scope?.expected_canonical_message_ref??scope?.expected_native_message_ref??scope?.expected_review_ref??null;}}this.error='';this._publish();await this._persistContinuation(draft);return this.get();}
      if(payload.kind==='review-control'&&role==='quickPopover'&&exact(payload,['kind','epoch','action','requestId','answers']))return this._reviewControl(payload);
      if(payload.kind!=='command'||!exact(payload,['kind','epoch','command']))throw new Error('Invalid quick surface event');
      const command=payload.command;
      if(command==='activity-back'&&role==='quickPopover'&&this.presentation==='ACTIVITY')return this.openAsk({anchor:this.anchor});
      if(command==='activity-supervisor'&&role==='quickPopover'&&this.presentation==='ACTIVITY')return {status:this._supervisorSupported()?'SUPERVISOR_INLINE':'UNAVAILABLE'};
      if(command==='supervisor-configure'&&role==='quickPopover'&&this.presentation==='ACTIVITY'){
        if(typeof this.onOpenSupervisor!=='function')throw new Error('Supervisor configuration unavailable');
        await this.flushSupervisorDraft();
        try{const opened=await this.onOpenSupervisor({configure:true});if(opened?.status!=='OPENED')throw new Error('Supervisor configuration did not open');}
        catch(error){this._fail(error,this.locale==='en'?'Connection settings could not be opened. Your input is kept.':'连接设置未能打开，当前输入仍保留。');return {status:'ACTION_FAILED'};}
        await this.hide({save:true});return {status:'CONFIGURATION_OPENED'};
      }
      if(command==='activity-all'&&role==='quickPopover'&&this.presentation==='ACTIVITY'){
        if(this.activityBusy)return {status:'BUSY'};if(typeof this.onExpandActivity!=='function')throw new Error('Activity overview is unavailable');const epoch=this.epoch;
        this.activityBusy=true;this._publish();
        try{await this.flushSupervisorDraft();if(!this._alive()||this.epoch!==epoch||this.presentation!=='ACTIVITY')return {status:'STALE'};await this.onExpandActivity();if(this._alive()&&epoch===this.epoch&&this.presentation==='ACTIVITY')await this.hide({save:true});}
        finally{if(epoch===this.epoch){this.activityBusy=false;if(this.presentation==='ACTIVITY'&&this.visible)this._publish();}}
        return {status:'EXPANDED'};
      }
      if(command==='activity-refresh'&&role==='quickPopover'&&this.presentation==='ACTIVITY'){
        if(this.activityBusy)return {status:'BUSY'};if(typeof this.onRefreshActivity!=='function')throw new Error('Activity refresh is unavailable');
        const request=++this.activityRequest,epoch=this.epoch,revision=this.activity.public.revision;this.activityBusy=true;this._publish();
        try{const snapshot=await this.onRefreshActivity();if(this._alive()&&request===this.activityRequest&&epoch===this.epoch&&this.presentation==='ACTIVITY'&&revision===this.activity.public.revision)this.updateActivity(snapshot);}
        catch(error){if(this._alive()&&request===this.activityRequest&&epoch===this.epoch)this._fail(error,'任务状态暂未刷新，保留上次列表。');}
        finally{if(request===this.activityRequest){this.activityBusy=false;if(this.presentation==='ACTIVITY'&&this.visible)this._publish();}}
        return this.get();
      }
      if(command==='toggle-preview'&&role==='quickPopover'){
        this.previewExpanded=!this.previewExpanded;this._publish();return this.get();
      }
      if(['text-confirm','text-cancel','text-delete'].includes(command)){
        if(!this.draft?.textEditor)throw new Error('No active text annotation');
        if(command==='text-confirm'){this._commitText();this._scheduleSave();}
        else{
          if(command==='text-delete'&&this.draft.textEditor.index!==null){this.draft.marks.splice(this.draft.textEditor.index,1);this.draft.touched=true;this._scheduleSave();}
          this.draft.textEditor=null;this.error='';
        }
        this._publish();return this.get();
      }
      if(command==='pin'&&role==='quickPopover'){
        if(this.pinBusy)return {status:'BUSY'};
        if(typeof this.onSetPinned!=='function')throw new Error('Chat pin control unavailable');
        this.pinBusy=true;this._publish();
        try{await this.onSetPinned(!this.pinned);return this.get();}
        finally{this.pinBusy=false;if(this._alive())this._publish();}
      }
      if(command==='dismiss'){await this.hide();return {status:'HIDDEN'};}
      if(role==='annotationOverlay'){
        if(command!=='review')throw new Error('Unsupported annotation command');
        this._commitText();
        this._showQuick(true);return this.get();
      }
      if(command==='draft'||command==='send'){
        if(command==='send'&&this.draft?.operationId){
          if(!guidanceScope(this.draft)&&!this.draft.reviewControl){this.error='当前任务暂不能补充，请先核查原操作';this._publish();return {status:'BUSY'};}
          return await this._reviewControl({action:'guidance',requestId:null,answers:null});
        }
        return await this._confirm(command==='draft'?'DRAFT':'SEND');
      }
      if(command==='expand'||command==='workbench'){
        const origin=command==='expand'&&this.draft?.operationId?{draftId:this.draft.id,operationId:this.draft.operationId}:undefined;
        if(this.draft?.touched&&!this.draft.operationId)await this._persist('EDITING');
        const result=await this.onExpand(command==='expand'?'companion':'workbench',origin);return result??{status:'EXPANDED'};
      }
      if(command==='review-continue'&&role==='quickPopover'){if(this.draft?.result?.dispatch_state!=='NOT_DISPATCHED'||this.draft.result.execution_started!==false)throw new Error('Original outcome must be confirmed not dispatched');await this._load({draftId:this.draft.id,continueEditing:true});this.reviewOpen=false;this._showQuick(false);return this.get();}
      if(command==='review-catalog'&&role==='quickPopover'){if(this.reviewOpen){this.reviewOpen=false;this._publish();return this.get();}return this._readReviewCatalog();}
      if(command==='review-clear'&&role==='quickPopover'){if(this.draft?.phase!=='EDITING')throw new Error('Review draft is not editable');this.draft.nativeReview=null;this.draft.touched=true;this._scheduleSave();this._publish();return this.get();}
      if(command==='review-guidance'&&role==='quickPopover')return await this._reviewControl({action:'guidance',requestId:null,answers:null});
      if(command==='inspect')return await this._inspect();
      if(command==='stop')return await this._stop();
      if(command==='file-clear')return await this._clearFileContext();
      if(command==='annotate'){const result=await this._showOverlay(true);return result?.status==='STALE'?result:this.get();}
      if(command==='recapture-cancel'){
        if(this.draft)this.draft.retakePending=false;this.notice='已保留原页面和标记。';this._publish();return this.get();
      }
      if(command==='recapture'||command==='recapture-confirm'){
        if(this.draft?.operationId)throw new Error('An already dispatched operation keeps its original page');
        if(!this.draft)throw new Error('No active draft');
        if(command==='recapture-confirm'&&!this.draft.retakePending)throw new Error('Page replacement was not requested');
        if(command==='recapture'&&this.draft.marks.length){
          this.draft.retakePending=true;this.notice='换当前页会保留文字，清除本轮标记；原稿历史仍保留。';this._publish();return {status:'CONFIRM_RECAPTURE'};
        }
        if(this.draft?.touched)await this._persist('EDITING');
        this.windows.quickPopover.hide();this.windows.annotationOverlay.hide();this.visible=false;this.overlayVisible=false;
        this.draft.marks=[];this.draft.capture=null;this.draft.restored=false;this.draft.retakePending=false;this.notice='';this.epoch++;return await this._capture(this.kind);
      }
      if(command==='new-question'||command==='capability-continue'){
        if(command==='new-question'&&this.draft?.phase==='EDITING'&&this.draft.nativeReview?.version===2&&this.draft.nativeReview.task_root_operation_id){this.draft.nativeReview=quickResetNativeReview(this.draft.nativeReview,{newTask:true});this.draft.touched=true;this._scheduleSave();this._publish();return this.get();}
        if(this.draft?.readonly){this._restoreDraft();this.epoch++;this.notice='';this.error='';this.reviewOpen=false;this._publish();return this.openAsk({anchor:this.anchor});}
        const previous=this.draft,epoch=this.epoch,continuing=command==='capability-continue';
        if(continuing&&(previous?.readonly||previous?.result?.status!=='RETURNED'||previous?.result?.review?.version!==2||!ID.test(previous.result.review.task_root_operation_id??'')||previous.nativeReview?.version!==2))throw new Error('当前任务暂不能继续，请核查原操作');
        if(!previous?.operationId||!['COMPLETED','FAILED','UNKNOWN','RUNNING'].includes(previous.phase))throw new Error('An existing submitted question is required');
        if(previous.controlBusy)return {status:'BUSY'};
        if(previous.guidanceText?.trim()){this.error='请先发送或清空当前补充';this._publish();return {status:'ACTION_FAILED'};}
        if(typeof this.onRetainSubmitted!=='function'){this.error='原问题的本机保存尚未核实，已保留当前内容；请先核查原操作。';throw new Error('No durable retention checker');}
        let retained;
        try{retained=await this.onRetainSubmitted({draftId:previous.id,operationId:previous.operationId});}
        catch(error){if(!this._current(epoch,previous))return {status:'STALE'};this.error='原问题的本机保存尚未核实，已保留当前内容；请先核查原操作。';throw error;}
        if(!this._current(epoch,previous))return {status:'STALE'};
        if(previous.controlBusy)return {status:'BUSY'};
        if(previous.guidanceText?.trim()){this.error='请先发送或清空当前补充';this._publish();return {status:'ACTION_FAILED'};}
        if(retained?.status!=='RETAINED'||retained.operationId!==previous.operationId||retained.ref?.id!==previous.id||!Number.isSafeInteger(retained.ref.revision)||retained.ref.revision<1||!SHA.test(retained.ref.sha256)){
          this.error='原问题的本机保存尚未核实，已保留当前内容；请先核查原操作。';throw new Error('Original question retention was not confirmed');
        }
        this._cancelAutosave();this.epoch++;this.draft=this._newDraft();this.draft.nativeReview=quickResetNativeReview(previous.nativeReview,{newTask:!continuing});if(continuing)this.draft.nativeReview.task_root_operation_id=previous.result.review.task_root_operation_id;this.reviewOpen=false;this.kind='ask';this.previewExpanded=false;this.notice='';this.error='';
        const current=this.draft;this.windows.quickPopover.hide();this.visible=false;const captured=await this._capture('ask');
        if(this.draft===current&&captured.status!=='STALE'&&this.draft.nativeReview&&!this.capturePending)await this._readReviewCatalog({open:false});
        return captured;
      }
      throw new Error('Unsupported quick surface command');
    }catch(error){
      if(this._alive())this._fail(error,this.error||(this.presentation==='ACTIVITY'?(payload.kind==='command'&&['dismiss','activity-back','supervisor-configure'].includes(payload.command)?this.locale==='en'?'The coordinator draft could not be saved. Your input is kept.':'总管草稿尚未保存，当前输入仍保留。':'任务操作未完成，请读取最新列表后再选择。'):'操作未完成，内容已保留。请重试或展开对话继续。'));
      return {status:'ACTION_FAILED'};
    }
  }
  dispose() {
    if(this.disposed)return;
    this.editorFlush?.reject();
    this.disposed=true;this.epoch++;this._cancelCollapse();this._cancelAutosave();this.ipcMain.removeHandler(CHANNEL);
    this.screen.removeListener?.('display-removed',this._displayChanged);this.screen.removeListener?.('display-metrics-changed',this._displayChanged);
    for(const window of Object.values(this.windows))if(!window.isDestroyed())window.destroy();
    this.visible=false;this.overlayVisible=false;
  }
}
module.exports={QuickSurface,CHANNEL,STATE_CHANNEL,TOOLS,normalizedMarks,markRegions,annotationBounds,popupBounds,quickHtml};
